"""Native macOS driver for the XIXEI X7 NFC reader - MIFARE Classic.

Restores nfcPro's core: read (UID), decode (full card dump with keys), write/clone.
Works by driving the reader's internal PN532 via the vendor HID passthrough opcode
(FF 00 00 00 <L> D4 ...), using the RF-init sequence captured from nfcPro.

MIFARE ops use standard PN532 InDataExchange (verified working on this firmware):
  auth  : D4 40 01 60/61 <block> <key6> <uid4>
  read  : D4 40 01 30 <block>            -> 16 bytes
  write : D4 40 01 A0 <block> <data16>
"""
import os
from x7 import X7, hx
from x7_init import INIT_SEQ

# Well-known MIFARE Classic keys (proxmark/mfoc dictionary) + ones recovered here.
# Ordered so the most common (FF, then this deployment's key) hit first. This is
# the in-binary fast-path fallback; the full curated dictionary is BUILTIN_KEYS.
DEFAULT_KEYS = [
    "ffffffffffff", "a0b1c2d3e4f5", "000000000000", "a0a1a2a3a4a5",
    "d3f7d3f7d3f7", "a0b0c0d0e0f0", "b0b1b2b3b4b5", "aabbccddeeff",
    "4d3a99c351dd", "1a982c7e459a", "714c5c886e97", "587ee5f9350f",
    "a64598a77478", "8fd0a4f256e9", "fc00018778f7", "0297927c0f77",
    "ee0042f88840", "722bfcc5375f", "f1d83f964314", "54726176656c",
    "b5ff67cba951", "7b5b66dddd71", "2a2c13cc242a", "fd8705e721b0",
    "75ccb59c9bed", "4b791bea7bcc", "5c8ff9990da2", "d01afeeb890a",
    "fdcd24e17d12", "f0a8c4137f51", "5a7a52d5e20d", "abcdef123456",
    "44ab09010845", "a31667a8cec1", "563de26d8e3f", "11ee2a23f8fb",
    "010203040506", "111111111111", "222222222222", "333333333333",
    "444444444444", "555555555555", "666666666666", "777777777777",
    "888888888888", "999999999999", "aaaaaaaaaaaa", "bbbbbbbbbbbb",
    "cccccccccccc", "dddddddddddd", "eeeeeeeeeeee", "123456789abc",
]


def _load_builtin_keys():
    """The bundled curated dictionary (dict/mfc_keys.dic, ~4.5k keys), or the
    in-binary DEFAULT_KEYS if the file is missing. Loaded once at import."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dict", "mfc_keys.dic")
    try:
        keys, seen = [], set()
        with open(path) as f:
            for line in f:
                s = line.strip().lower()
                if len(s) == 12 and all(c in "0123456789abcdef" for c in s) and s not in seen:
                    seen.add(s)
                    keys.append(s)
        return keys or list(DEFAULT_KEYS)
    except OSError:
        return list(DEFAULT_KEYS)


# Full curated dictionary used for decode (DEFAULT_KEYS is the fallback subset).
BUILTIN_KEYS = _load_builtin_keys()

# Read timeout (ms) for the dictionary-walk auth cycle. An auth answers in a few
# ms (success or the 0x14 error), so a short bound keeps a failed key fast; this
# is the main lever that brings the walk to nfcPro's ~26 ms/key.
FAST_TO = 150


def sector_count(sak):
    return 40 if sak == 0x18 else 16            # 4K vs 1K


def blocks_in_sector(s):
    return 4 if s < 32 else 16                   # 4K big sectors


def first_block(s):
    return s * 4 if s < 32 else 128 + (s - 32) * 16


def trailer_block(s):
    return first_block(s) + blocks_in_sector(s) - 1


class X7Card:
    def __init__(self):
        self.x = X7()
        self.uid = None

    def _pt(self, cmd, reads=8, to=700):
        """Send a PN532 command via passthrough, return PN532 response (from 0xD5)."""
        p = [0xFF, 0x00, 0x00, 0x00, len(cmd)] + list(cmd)
        d, r = self.x.cmd(p, reads=reads, timeout=to)
        raw = b"".join(r)
        # Anchor on the 0xD5 PN532 direction byte, but search only PAST the 4-byte
        # envelope header [marker, total, seq_lo, seq_hi]. The 16-bit seq is
        # host-chosen (x7.py increments by 2; device echoes seq|1), so a seq byte
        # is 0xD5 for ~1.17% of commands. A find() from offset 0 would mis-anchor
        # on that seq byte and drop the command (a missed key in find_key, a lost
        # nonce in collect_nested_nonce). raw[4] is the 0x00 vendor status; the
        # real 0xD5 is at offset 5.
        i = raw.find(b"\xd5", 4)
        return raw[i:] if i >= 0 else b""

    def init_rf(self):
        for h in INIT_SEQ:
            self.x.cmd(list(bytes.fromhex(h)), reads=3, timeout=300)

    def poll(self):
        """InListPassiveTarget -> dict(uid, atqa, sak) or None."""
        r = self._pt([0xD4, 0x4A, 0x01, 0x00])
        if len(r) < 8 or r[1] != 0x4B or r[2] != 1:
            return None
        atqa, sak, uidlen = r[4:6], r[6], r[7]
        uid = bytes(r[8:8 + uidlen])
        if len(uid) != uidlen:
            return None
        self.uid = uid
        return {"uid": uid, "atqa": bytes(atqa), "sak": sak}

    def wait_for_card(self, tries=25):
        """Poll until a card couples (coupling can be intermittent on first contact)."""
        for _ in range(tries):
            i = self.poll()
            if i:
                return i
        return None

    def auth(self, block, key, keytype="A", to=700):
        if isinstance(key, str):
            key = bytes.fromhex(key)
        kt = 0x60 if keytype == "A" else 0x61
        r = self._pt([0xD4, 0x40, 0x01, kt, block] + list(key) + list(self.uid), reads=4, to=to)
        return len(r) >= 3 and r[1] == 0x41 and r[2] == 0x00

    def _select(self):
        """The pre-auth command nfcPro sends before every auth (captured as
        d4 4e 01 00 00 -> d5 4f 00). It re-activates the listed target so the next
        InDataExchange auth is immediate; without it our auths needed slow retries."""
        self._pt([0xD4, 0x4E, 0x01, 0x00, 0x00], reads=2, to=FAST_TO)

    def read_block(self, block):
        r = self._pt([0xD4, 0x40, 0x01, 0x30, block])
        if len(r) >= 19 and r[1] == 0x41 and r[2] == 0x00:
            return bytes(r[3:19])
        return None

    def write_block(self, block, data16):
        if isinstance(data16, str):
            data16 = bytes.fromhex(data16)
        assert len(data16) == 16, "block data must be 16 bytes"
        r = self._pt([0xD4, 0x40, 0x01, 0xA0, block] + list(data16))
        return len(r) >= 3 and r[1] == 0x41 and r[2] == 0x00

    def find_key(self, block, keys=DEFAULT_KEYS, on_try=None):
        """Return (keytype, keyhex) that authenticates `block`, or None.

        Mirrors nfcPro's captured fast cycle so a dictionary walk runs at its
        speed (~26 ms/key, measured): select the card once, then each attempt is
        _select() (d4 4e) + auth() (d4 40) on a short timeout; a failed auth halts
        the card, so a re-poll (d4 4a) re-selects it for the next attempt. A is
        preferred (a KeyA==KeyB card never flips A<->B). `on_try` (throttled)
        drives the caller's progress bar."""
        if not self.poll() and not self.wait_for_card():
            return None
        n = len(keys)
        for i, k in enumerate(keys):
            if on_try is not None and i and i % 256 == 0:
                on_try(i, n)
            for kt in ("A", "B"):
                self._select()
                if self.auth(block, k, kt, to=FAST_TO):
                    if kt == "B":                      # prefer A when it also works
                        self.poll(); self._select()
                        if self.auth(block, k, "A", to=FAST_TO):
                            return ("A", k)
                    return (kt, k)
                self.poll()                            # re-select after the failed auth
        return None

    def dump(self, keys=None, progress=None, on_try=None):
        """Dump the whole card. Returns dict: blocks{n:16B}, keys{sector:(kt,key)}, sak, uid.

        Key REUSE: a key proven on one sector is tried first on the rest (MIFARE
        deployments reuse keys), so a normal card resolves in a handful of auths
        regardless of dictionary size; the full dict is only walked on a sector
        whose key is genuinely unknown."""
        if keys is None:
            keys = BUILTIN_KEYS
        info = self.wait_for_card()
        if not info:
            raise RuntimeError("no card on reader")
        nsec = sector_count(info["sak"])
        blocks, skeys = {}, {}
        found_keys, found_set = [], set()      # proven on THIS card, tried first
        for s in range(nsec):
            tb = trailer_block(s)
            trial = found_keys + [k for k in keys if k not in found_set]
            found = self.find_key(tb, trial, on_try=(lambda i, n, s=s: on_try(s, i, n)) if on_try else None)
            skeys[s] = found
            if progress:
                progress(s, nsec, found)
            if not found:
                continue
            kt, k = found
            if k not in found_set:             # promote for the remaining sectors
                found_set.add(k)
                found_keys.insert(0, k)
            for b in range(first_block(s), tb + 1):
                data = None
                for _ in range(6):                  # re-auth per block for reliability
                    if not self.poll():
                        continue
                    if not self.auth(tb, k, kt):
                        continue
                    data = self.read_block(b)
                    if data is not None:
                        break
                blocks[b] = data
            # patch trailer: the key we used is never returned by READ (reads as 0)
            if blocks.get(tb) is not None:
                t = bytearray(blocks[tb])
                kb = bytes.fromhex(k)
                if kt == "A":
                    t[0:6] = kb
                else:
                    t[10:16] = kb
                blocks[tb] = bytes(t)
        return {"uid": info["uid"], "sak": info["sak"], "atqa": info["atqa"],
                "blocks": blocks, "keys": skeys, "sectors": nsec}

    def read_ntag(self, pages=45):
        """Dump an NTAG21x / Ultralight (SAK 0x00). READ returns 4 pages (16B)/call.
        No auth for the data area. Returns dict page->4 bytes."""
        out = {}
        if not self.poll():
            raise RuntimeError("no card")
        for p in range(0, pages, 4):
            r = self._pt([0xD4, 0x40, 0x01, 0x30, p])
            if len(r) >= 19 and r[1] == 0x41 and r[2] == 0x00:
                blk = r[3:19]
                for j in range(4):
                    out[p + j] = bytes(blk[j * 4:j * 4 + 4])
            else:
                if not self.poll():       # rewrap on read past end
                    break
        return out

    def apdu(self, data):
        """Send a raw APDU to a selected ISO14443-4 / CPU card via InDataExchange."""
        if isinstance(data, str):
            data = bytes.fromhex(data)
        r = self._pt([0xD4, 0x40, 0x01] + list(data))
        if len(r) >= 3 and r[1] == 0x41:
            return bytes(r[3:])          # response APDU (status 0x00 prefix stripped by caller)
        return None

    # -----------------------------------------------------------------------
    # Low-level CIU register + raw-transceive primitives (for nested cracking).
    #
    # Wire format verified in the nfcPro USB capture (re/x7_traffic.txt):
    #   ReadRegister  : OUT  FF 00 00 00 04 D4 06 <hi> <lo>
    #                   IN   D5 07 <val> 90 00            -> _pt gives r[1]=07,r[2]=val
    #   WriteRegister : OUT  FF 00 00 00 05 D4 08 <hi> <lo> <val>
    #                   IN   D5 09 90 00                  -> _pt gives r[1]=09
    #   InCommThru    : OUT  FF 00 00 00 <L> D4 42 <raw bus bytes...>
    #                   IN   D5 43 <status> <data...> 90 00 -> r[1]=43,r[2]=status,
    #                                                          r[3:] = data + 90 00
    # Registers proven reachable in the capture: 6302 Command, 6303 CommIEn,
    # 6305 TxMode, 630d/633d BitFraming(TxLastBits), 633c FIFOLevel, 633e CollReg.
    # -----------------------------------------------------------------------

    def reg_read(self, addr):
        """Read one CIU register. Returns the byte value, or None."""
        r = self._pt([0xD4, 0x06, addr >> 8, addr & 0xFF])
        return r[2] if len(r) >= 3 and r[1] == 0x07 else None

    def reg_write(self, addr, val):
        """Write one CIU register. Returns True on the D5 09 ack."""
        r = self._pt([0xD4, 0x08, addr >> 8, addr & 0xFF, val & 0xFF])
        return len(r) >= 2 and r[1] == 0x09

    def comm_thru(self, data):
        """Send a raw (possibly already-enciphered) bus frame via InCommunicateThru.
        Returns (status, payload_bytes). payload excludes the vendor 90 00 trailer."""
        r = self._pt([0xD4, 0x42] + list(data))
        if len(r) >= 2 and r[1] == 0x43:
            status = r[2] if len(r) >= 3 else 0xFF
            body = bytes(r[3:])
            if body.endswith(b"\x90\x00"):       # strip vendor envelope trailer
                body = body[:-2]
            return status, body
        return None, b""

    # CIU register addresses for the parity/bit-framing control the nested attack
    # needs. The base addresses (0x63xx) are confirmed by the capture; the SPECIFIC
    # parity-disable bit location is the one thing NOT exercised by the dictionary
    # capture and MUST be confirmed live (see PARITY note below).
    CIU_MFRX = 0x6312        # MfRxReg on PN53x: bit0 ParityDisable (LIVE-PROBE)
    CIU_BITFRAMING = 0x633D  # TxLastBits[2:0], RxAlign[6:4] (confirmed in capture)
    CIU_COLL = 0x633E        # CollReg (confirmed in capture)
    CIU_FIFOLEVEL = 0x633C   # FIFOLevel (confirmed in capture)
    CIU_ERROR = 0x6306       # ErrorReg: ParityErr bit (read after transceive)

    def _set_parity_raw(self, raw):
        """Disable (raw=True) or enable controller auto-parity, so software can
        supply/observe parity bits. PN53x: MfRxReg bit0 = ParityDisable.
        Returns True if the register write succeeded. LIVE-PROBE: confirm CIU_MFRX
        is the right register on this emulated firmware before trusting nonces."""
        v = self.reg_read(self.CIU_MFRX)
        if v is None:
            return False
        v = (v | 0x01) if raw else (v & ~0x01)
        return self.reg_write(self.CIU_MFRX, v)

    def collect_nested_nonce(self, known_blk, known_key, known_kt,
                             target_blk, target_kt="A"):
        """Capture one ENCRYPTED nested nonce for a MIFARE Classic nested attack.

        Steps (mfoc model):
          1. InDataExchange auth to a KNOWN-key sector -> CIU crypto1 = ENCRYPTED.
          2. Disable controller auto-parity (MfRxReg ParityDisable) so the host
             can read the tag's transmitted parity bits.
          3. InCommunicateThru a RAW 4-byte auth frame (60/61 + target_blk + 2-byte
             CRC) to the TARGET sector. Because the session is already enciphered,
             the tag answers with its nonce ENCRYPTED (nt_enc, 4 bytes) plus the
             4 parity bits.
          4. Read nt_enc from the InCommThru payload; read the parity bits.

        Returns (nt_enc:int32, parity:list[4 bits]) or (None, None).

        *** TWO LIVE-PROBE RISKS on this emulated-PN532 firmware ***
        (a) ENCRYPTED-STATE PERSISTENCE: does the CIU keep crypto1 in the encrypted
            state across an InCommunicateThru issued after an InDataExchange auth?
            The vendor MCU might reset the cipher between PN532 sub-commands. The
            USB capture does NOT prove this (it was a dictionary read). If state
            does NOT persist, nt_enc comes back as a PLAINTEXT nonce and the crack
            fails; fall back to doing the FIRST auth ALSO via raw InCommThru frames
            (full software crypto1 handshake).
        (b) PARITY RETRIEVAL: PN53x returns received parity only when ParityDisable
            is set (CIU_MFRX) and you read it from the FIFO/CollReg. Confirm the
            register address and that parity bytes actually appear.
        If parity cannot be read, the crack still works with more nonces (the
        keystream/cross-sample constraints carry it), so par may be returned None.
        """
        # VERIFIED LIVE on the X7 (2026-06-19): one fresh known-sector auth per
        # nonce; the X7 CIU enciphers the nested auth in HARDWARE, so no software
        # crypto1 is needed on the wire. Reverse-engineered from nfcPro fcn.140033920.
        if isinstance(known_key, str):
            kk = bytes.fromhex(known_key)
        else:
            kk = bytes(known_key)
        known_kt_b = 0x60 if known_kt == "A" else 0x61
        target_kt_b = 0x60 if target_kt == "A" else 0x61

        # 1. reselect + auth the known sector (a fresh auth is required for each
        #    nonce; the encrypted state is consumed by one InCommThru).
        if not self.poll():
            return None, None
        r = self._pt([0xD4, 0x40, 0x01, known_kt_b, known_blk]
                     + list(kk) + list(self.uid))
        if not (len(r) >= 3 and r[1] == 0x41 and r[2] == 0x00):
            return None, None

        # 2. poke the CIU out of idle (clear Command/CommIEn bit7) so the next raw
        #    InCommThru runs a fresh Transceive instead of aborting after 2 bytes.
        self._ciu_rmw(0x6302, 0x00, 0x80)
        self._ciu_rmw(0x6303, 0x00, 0x80)

        # 3. raw nested auth [60/61, target_blk, CRC_A]; the still-encrypted CIU
        #    enciphers it and the tag answers with its 4-byte encrypted nonce.
        frame = bytes([target_kt_b, target_blk])
        frame += _crc_a(frame)
        status, body = self.comm_thru(frame)
        if status == 0x00 and len(body) >= 4:
            return int.from_bytes(body[:4], "big"), None
        return None, None

    def _ciu_rmw(self, addr, val, mask):
        """CIU register read-modify-write: newval = (val & mask) | (cur & ~mask)."""
        if mask == 0xFF:
            return self.reg_write(addr, val & 0xFF)
        cur = self.reg_read(addr)
        if cur is None:
            return False
        return self.reg_write(addr, ((val & mask) | (cur & (~mask & 0xFF))) & 0xFF)

    def _read_parity_bits(self):
        """Read the 4 received parity bits the tag sent with the last nonce.
        On a genuine PN53x these come back interleaved in the FIFO when
        ParityDisable is set, or are derivable from CollReg/ErrorReg. The exact
        retrieval on THIS firmware is UNVERIFIED -> returns None until a live
        probe confirms it. The crack pipeline treats parity=None gracefully."""
        return None

    def crack_key(self, known_blk, known_key, target_blk,
                  known_kt="A", target_kt="A", **kw):
        """Recover the key of `target_blk` via the nested attack, using a working
        key for `known_blk`. Returns the recovered key hex, or None.

        Thin wrapper over x7crypto.nested_recover_key (the crypto + orchestration).
        Example (test card): card.crack_key(7, "ffffffffffff", 3) -> "a0b1c2d3e4f5".
        """
        import x7crypto
        if not self.uid:
            self.wait_for_card()
        return x7crypto.nested_recover_key(
            self, known_blk, known_key, target_blk,
            known_kt=known_kt, target_kt=target_kt, **kw)

    def close(self):
        self.x.close()


def _crc_a(data):
    """ISO14443-A CRC (CRC_A), little-endian 2 bytes. Standard MIFARE CRC."""
    crc = 0x6363
    for b in data:
        b ^= crc & 0xFF
        b = (b ^ (b << 4)) & 0xFF
        crc = ((crc >> 8) ^ (b << 8) ^ (b << 3) ^ (b >> 4)) & 0xFFFF
    return bytes([crc & 0xFF, (crc >> 8) & 0xFF])
