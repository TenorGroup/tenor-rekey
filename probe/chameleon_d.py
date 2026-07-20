#!/usr/bin/env python3
"""chameleon_d - JSON-over-stdio daemon wrapping the Chameleon Ultra CLI engine.

Sibling of x7d.py. The native macOS (SwiftUI) front-end speaks the SAME contract
for every device it drives, so the shell never learns a new protocol: it reads the
`capabilities` manifest returned by `info` and lights up the panels the device
supports. This daemon holds one vendored ChameleonCom (transport) + ChameleonCMD
(command layer) and serialises every device op (one device, one command stream).

Wire format - newline-delimited JSON on stdin/stdout (identical to x7d.py):
  request : {"id": <n>, "method": "<name>", "params": {...}}
  response: {"id": <n>, "result": {...}}  |  {"id": <n>, "error": "<msg>"}
  event   : {"event": "progress", "method": "<name>", ...}   (id-less, unsolicited)

Methods (P0 surface): info, poll, slots_list, slot_select, mf_read_block, decode.
Hex at the JSON boundary is lowercase space-separated ("01 02 03 04"); keys are
12-char hex. SAK is an int (matching x7d, so the shell's parser is unchanged).
"""
import sys
import json

# Vendored upstream engine (GPLv3, RfidResearchGroup/ChameleonUltra). Imports on a
# bare interpreter - serial/colorama/prompt_toolkit are optional in the package.
from chameleon.chameleon_com import ChameleonCom, NotOpenException, OpenFailException
from chameleon.chameleon_cmd import ChameleonCMD
from chameleon.chameleon_enum import SlotNumber, TagSpecificType, MfcKeyType, Status
from chameleon.chameleon_utils import UnexpectedResponseError

# The learned-key reranker + the curated dictionary are SHARED with x7d (one cache
# and one dictionary across both readers). Both are optional so the daemon still
# runs if they are unavailable.
try:
    from learned_keys import LearnedKeyCache
except Exception:                        # pragma: no cover - import guard
    LearnedKeyCache = None
try:
    from x7lib import BUILTIN_KEYS as _X7_BUILTIN
except Exception:                        # pragma: no cover - import guard
    _X7_BUILTIN = []

# USB CDC vendor id of the Chameleon Ultra / Lite (VID 0x6868, PID 0x8686).
CHAMELEON_VID = 0x6868

# How many learned keys to try (after user keys, before the dictionary). Mirrors
# x7d so the two readers rerank identically.
LEARNED_TOP_N = 64

# Public, well-known MIFARE Classic default keys (documented defaults, never card
# secrets - safe in a public repo). The named set comes first so a factory card
# resolves in the first check-keys chunk; probe's curated dictionary (loaded by
# x7lib from probe/dict) is appended when present.
_DEFAULT_KEYS = [
    "ffffffffffff", "000000000000", "a0a1a2a3a4a5", "d3f7d3f7d3f7",
    "a0b0c0d0e0f0", "b0b1b2b3b4b5", "4d3a99c351dd", "1a982c7e459a",
    "aabbccddeeff", "714c5c886e97", "587ee5f9350f", "a0478cc39091",
    "533cb6c723f6", "8fd0a4f256e9",
]
BUILTIN_KEYS = list(_DEFAULT_KEYS)
_seen = set(_DEFAULT_KEYS)
for _k in _X7_BUILTIN:
    if _k not in _seen:
        BUILTIN_KEYS.append(_k)
        _seen.add(_k)


def hx(b):
    """Lowercase space-separated hex, identical to x7d/x7 hx()."""
    return " ".join("%02x" % x for x in b)


def _valid_key_hex(k):
    """A key must be exactly 12 hex chars, else it cannot be used for auth."""
    return isinstance(k, str) and len(k) == 12 and all(c in "0123456789abcdefABCDEF" for c in k)


# ---- MIFARE Classic geometry (kept local so this daemon does not pull in the X7
# hidapi stack just for these pure helpers). Mirrors x7lib's definitions. --------

def sector_count(sak):
    return 40 if sak == 0x18 else 16            # 4K vs 1K


def blocks_in_sector(s):
    return 4 if s < 32 else 16                   # 4K big sectors


def first_block(s):
    return s * 4 if s < 32 else 128 + (s - 32) * 16


def trailer_block(s):
    return first_block(s) + blocks_in_sector(s) - 1


def card_kind(sak, atqa):
    """NTAG/Ultralight report SAK 0x00 AND ATQA 0x0044; a magic/blank Classic also
    reports SAK 0x00 but ATQA 0x0004. atqa: 2 bytes or int."""
    a = int.from_bytes(atqa, "big") if isinstance(atqa, (bytes, bytearray)) else int(atqa)
    return "ntag" if (sak == 0x00 and a == 0x0044) else "classic"


def _type_name(t):
    """TagSpecificType value -> stable enum name (the shell localises it)."""
    try:
        return TagSpecificType(t).name
    except ValueError:
        return "UNKNOWN_%d" % t


def _capabilities(model):
    """The capability manifest the shell reads to gate panels (SPEC 2.3). Built
    from the device model, not hardcoded: model 0 = Ultra, 1 = Lite. The Lite has
    the same 8 slots + emulation + LF + DFU but no HF/LF reader, so reader-mode
    attacks and sniffing do not apply (P0 default; confirm the Lite on hardware)."""
    caps = {
        "slots": 8, "emulate": True, "lf": True, "dfu": True, "sniff": True,
        "attacks": ["dict", "nested", "staticNested", "darkside", "hardnested"],
        "writeModes": ["normal", "denied", "deceive", "shadow", "shadowReq"],
    }
    if model != 0:                       # Lite: no reader front-end
        caps["sniff"] = False
        caps["attacks"] = []
    return caps


# Transport-dead exceptions: like x7d dropping the handle on OSError, drop the
# Chameleon handle when the port is gone so the next command reconnects cleanly.
# (TimeoutError from send_cmd_sync is an OSError subclass.)
_DEAD = (OSError, NotOpenException, OpenFailException)


class Daemon:
    METHODS = ("info", "poll", "slots_list", "slot_select", "mf_read_block", "decode")

    def __init__(self, learned=None, port=None):
        self.com = None
        self.cmd = None                  # the ChameleonCMD command layer (or a fake)
        self._port = port
        self._reader_mode = None         # cached: True once the device is in reader mode
        # The protocol channel, captured at construction. __main__ redirects
        # sys.stdout to stderr so vendored-library print() cannot corrupt it.
        self._out = sys.stdout
        # Shared verified-key reranker (injectable for tests). None if unavailable.
        if learned is not None:
            self.learned = learned
        elif LearnedKeyCache is not None:
            self.learned = LearnedKeyCache()
        else:
            self.learned = None

    # ---- connection --------------------------------------------------------

    def _find_port(self):
        """First serial port whose USB vendor id is the Chameleon's, or None."""
        try:
            from serial.tools import list_ports
        except ImportError:
            return None
        for p in list_ports.comports():
            if getattr(p, "vid", None) == CHAMELEON_VID:
                return p.device
        return None

    def _connect(self, port=None):
        """Open the device once and reuse it. ChameleonCom.open sets 115200 + DTR
        high. Raises when no device is found so `info` returns a clean error
        envelope instead of crashing on a hardware-free machine."""
        if self.cmd is None:
            dev = port or self._port or self._find_port()
            if not dev:
                raise RuntimeError("no Chameleon device found")
            com = ChameleonCom()
            com.open(dev)
            self.com = com
            self.cmd = ChameleonCMD(com)
        return self.cmd

    def _drop(self):
        """Forget the device handle so the next _connect() re-opens it (called when
        a hardware op fails: the cached handle is dead)."""
        if self.com is not None:
            try:
                self.com.close()
            except Exception:
                pass
        self.com = None
        self.cmd = None
        self._reader_mode = None

    def _ensure_reader(self, c):
        """Reader ops (poll/decode/mf_read_block) need the device in reader mode; a
        card op issued in tag/emulator mode returns DEVICE_MODE_ERROR (mis-read as
        "no card"). Switch once per connection and cache it. The Lite has no reader
        front-end (set reader mode -> NOT_IMPLEMENTED), surfaced as a clean
        RuntimeError so callers report a reader fault, never a phantom no-card."""
        if self._reader_mode:
            return
        try:
            if not c.is_device_reader_mode():
                c.set_device_reader_mode(True)
        except UnexpectedResponseError as e:
            raise RuntimeError("device has no reader mode (Lite?): %s" % e)
        self._reader_mode = True

    def emit(self, obj):
        self._out.write(json.dumps(obj) + "\n")
        self._out.flush()

    # ---- methods -----------------------------------------------------------

    def info(self, p):
        c = self._connect(p.get("port"))
        model = c.get_device_model()             # 0 = Ultra, 1 = Lite
        major, minor = c.get_app_version()
        git = c.get_git_version()
        chip = c.get_device_chip_id()
        family = "chameleon-ultra" if model == 0 else "chameleon-lite"
        name = "Chameleon Ultra" if model == 0 else "Chameleon Lite"
        return {"family": family, "model": name, "serial": chip,
                "hw": "app %d.%d (%s)" % (major, minor, git),
                "capabilities": _capabilities(model)}

    def poll(self, p):
        # `reader` reports whether the Chameleon is connected (vs `present`, a card
        # in the field). No tag -> field is up, so reader:true. A dead port or a
        # device with no reader mode (Lite) -> reader:false. `_ensure_reader` runs
        # first so hf14a_scan cannot fail with DEVICE_MODE_ERROR and be mistaken
        # for a genuine no-card. Errors here are normal, so we answer, not raise.
        try:
            c = self._connect(p.get("port"))
            self._ensure_reader(c)
            tags = c.hf14a_scan()
        except UnexpectedResponseError:
            return {"present": False, "reader": True}     # field up, no tag
        except (RuntimeError, ImportError):
            return {"present": False, "reader": False}    # no device / no reader mode
        except _DEAD:
            self._drop()
            return {"present": False, "reader": False}    # device gone
        if not tags:
            return {"present": False, "reader": True}
        t = tags[0]
        sak = t["sak"]
        atqa = t["atqa"][::-1]                             # wire (LSB-first) -> semantic
        return {"present": True, "reader": True,
                "uid": hx(t["uid"]), "atqa": hx(atqa),
                "sak": sak[0], "ats": (hx(t["ats"]) if t["ats"] else ""),
                "kind": card_kind(sak[0], atqa)}

    def slots_list(self, p):
        c = self._connect(p.get("port"))
        info = c.get_slot_info()             # 8 x {'hf': type, 'lf': type}
        enabled = c.get_enabled_slots()      # 8 x {'hf': bool, 'lf': bool}
        nicks = c.get_all_slot_nicks()       # 8 x {'hf': str,  'lf': str}
        active = c.get_active_slot()         # fw index 0..7
        slots = []
        for i in range(8):
            slots.append({
                "index": i,
                "active": i == active,
                "hf": {"type": _type_name(info[i]["hf"]),
                       "enabled": bool(enabled[i]["hf"]),
                       "nick": nicks[i]["hf"]},
                "lf": {"type": _type_name(info[i]["lf"]),
                       "enabled": bool(enabled[i]["lf"]),
                       "nick": nicks[i]["lf"]},
            })
        return {"slots": slots}

    def slot_select(self, p):
        c = self._connect(p.get("port"))
        slot = int(p["slot"])                # 0-based, matches slots_list index
        c.set_active_slot(SlotNumber.from_fw(slot))
        return {"slot": slot}

    def mf_read_block(self, p):
        c = self._connect(p.get("port"))
        self._ensure_reader(c)
        block = int(p["block"])
        kt = (p.get("keytype") or "A").upper()
        key = bytes.fromhex(p["key"].replace(" ", ""))
        mkt = MfcKeyType.A if kt == "A" else MfcKeyType.B
        data = c.mf1_read_one_block(block, mkt, key)
        return {"block": block, "data": hx(data)}

    # ---- decode: dict-only key recovery + full read (nested/darkside is P1) --

    def _check_keys(self, c, n_sectors, key_bytes):
        """On-device dictionary check across n_sectors, mirroring the CLI. The
        80-bit mask (10 bytes, MSB-first) masks a sector-key when its bit is set:
        bit 2*s = KeyA of sector s, bit 2*s+1 = KeyB. Sectors past the card are
        pre-masked; found bits are accumulated so later chunks skip them. Returns
        ({index: 6-byte key} keyed 0..79, keys_tried) - index k -> sector k//2,
        KeyA if k even."""
        mask = bytearray(10)
        for s in range(n_sectors, 40):
            mask[s // 4] |= 3 << (6 - (s % 4) * 2)
        sector_keys, tried = {}, 0
        CHUNK = 20                        # firmware accepts 1..83; the CLI uses 20
        for i in range(0, len(key_bytes), CHUNK):
            chunk = key_bytes[i:i + CHUNK]
            if not chunk:
                continue
            tried += len(chunk)
            resp = c.mf1_check_keys_of_sectors(bytes(mask), chunk)
            if resp.get("status") != Status.HF_TAG_OK:
                break
            if "sectorKeys" not in resp:
                break                     # all sector-keys found or masked
            found = resp["found"]
            for j in range(10):
                mask[j] |= found[j]
            sector_keys.update(resp["sectorKeys"])
            if all(b == 0xFF for b in mask):
                break                     # nothing left to check
        return sector_keys, tried

    def _read_block(self, c, block, candidates):
        """Read one block trying each recovered key for the sector (KeyA first,
        KeyB fallback), so a block that is only KeyB-readable is not lost."""
        for kt, keyhex in candidates:
            try:
                mkt = MfcKeyType.A if kt == "A" else MfcKeyType.B
                return c.mf1_read_one_block(block, mkt, bytes.fromhex(keyhex))
            except UnexpectedResponseError:
                continue
        return None

    def _rescan_uid(self, c):
        """Current card uid via a fresh scan, or None if the field is empty."""
        try:
            tags = c.hf14a_scan()
        except UnexpectedResponseError:
            return None
        return tags[0]["uid"] if tags else None

    def decode(self, p):
        """params: user_keys [hex]. DICT-ONLY recovery for P0 (nested/darkside is
        P1): check the dictionary on-device, then read every block of each recovered
        sector. Emits a progress event per sector. Returns the x7d decode shape. No
        card -> raises (a clean error envelope), matching x7d / x7lib."""
        c = self._connect(p.get("port"))
        self._ensure_reader(c)
        try:
            tags = c.hf14a_scan()
        except UnexpectedResponseError:
            raise RuntimeError("no card on reader")
        if not tags:
            raise RuntimeError("no card on reader")
        t = tags[0]
        target = t["uid"]                                 # the card we committed to
        atqa = t["atqa"][::-1]                            # wire (LSB-first) -> semantic
        sak = t["sak"]
        n = sector_count(sak[0])

        # Key order: user keys first, then learned (ranked), then the dictionary.
        keys, seen = [], set()

        def add(k):
            k = (k or "").lower()
            if _valid_key_hex(k) and k not in seen:
                keys.append(k)
                seen.add(k)

        for k in (p.get("user_keys") or p.get("keys") or []):
            add(k)
        if self.learned is not None:
            try:
                for k in self.learned.top_keys(limit=LEARNED_TOP_N, uid=target.hex()):
                    add(k)
            except Exception:
                pass
        for k in BUILTIN_KEYS:
            add(k)

        key_bytes = [bytes.fromhex(k) for k in keys]
        sector_keys, attempts = self._check_keys(c, n, key_bytes)

        # index -> {"A": hex, "B": hex} per sector
        sk = {}
        for idx, kb in sector_keys.items():
            s = idx // 2
            kt = "A" if idx % 2 == 0 else "B"
            sk.setdefault(s, {})[kt] = kb.hex()

        blocks, keys_out, recovered = {}, {}, 0
        for s in range(n):
            found = sk.get(s)
            pref = None
            if found:
                pref = ("A", found["A"]) if "A" in found else ("B", found["B"])
            self.emit({"event": "progress", "method": "decode", "sector": s,
                       "total": n, "keytype": (pref[0] if pref else None),
                       "key": (pref[1] if pref else None)})
            if not found:                        # match x7d: unrecovered -> null
                keys_out[str(s)] = None
                for b in range(first_block(s), trailer_block(s) + 1):
                    blocks[str(b)] = None
                continue
            recovered += 1
            keys_out[str(s)] = [pref[0], pref[1]]
            # Card-identity guard: re-verify the card is still the one we scanned
            # before reading its blocks, so a mid-decode swap never returns another
            # card's blocks under the first uid (mirrors x7lib's per-sector check).
            if self._rescan_uid(c) != target:
                raise RuntimeError("card changed during decode")
            cand = [(kt, found[kt]) for kt in ("A", "B") if kt in found]
            raw = {b: self._read_block(c, b, cand)
                   for b in range(first_block(s), trailer_block(s) + 1)}
            # Trailer patch: a READ returns Key A as 000000 (firmware never exposes
            # it) and may return Key B as 000000 too. Write the recovered keys back
            # so a clone never stamps a 000000 key slot; preserve the access bytes
            # (6-9) as read. Mirrors x7lib read_sector.
            tb = trailer_block(s)
            if raw.get(tb) is not None:
                tr = bytearray(raw[tb])
                ka, kb = found.get("A"), found.get("B")
                if ka:
                    tr[0:6] = bytes.fromhex(ka)
                if kb:
                    tr[10:16] = bytes.fromhex(kb)
                if ka and not kb and tr[10:16] == bytes(6):
                    tr[10:16] = bytes.fromhex(ka)
                if kb and not ka and tr[0:6] == bytes(6):
                    tr[0:6] = bytes.fromhex(kb)
                raw[tb] = bytes(tr)
            for b in range(first_block(s), trailer_block(s) + 1):
                blocks[str(b)] = (hx(raw[b]) if raw[b] else None)

        # Learn the keys that authed on this card so later decodes try them first.
        if self.learned is not None:
            rec = sorted({v for f in sk.values() for v in f.values()})
            if rec:
                try:
                    self.learned.record(rec, uid=target.hex())
                except Exception:
                    pass

        return {"uid": hx(target), "atqa": hx(atqa), "sak": sak[0],
                "sectors": n, "recovered": recovered,
                "attempts": attempts, "exhausted": recovered < n,
                "blocks": blocks, "keys": keys_out}

    # ---- dispatch ----------------------------------------------------------

    def handle(self, req):
        rid = req.get("id")
        method = req.get("method")
        if method not in self.METHODS:
            return {"id": rid, "error": "unknown method: %r" % method}
        try:
            return {"id": rid, "result": getattr(self, method)(req.get("params") or {})}
        except _DEAD as e:
            # The port died mid-op: drop the dead handle so the NEXT command
            # re-opens a fresh one (mirrors x7d dropping on OSError).
            self._drop()
            return {"id": rid, "error": "%s: %s" % (type(e).__name__, e)}
        except Exception as e:
            return {"id": rid, "error": "%s: %s" % (type(e).__name__, e)}

    def run(self):
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
            except ValueError as e:
                self.emit({"error": "bad json: %s" % e})
                continue
            self.emit(self.handle(req))
        self._drop()


if __name__ == "__main__":
    # Protect the protocol channel: the daemon captured the real stdout at
    # construction; redirect sys.stdout to stderr so any stray library print()
    # (the vendored ChameleonCom prints on frame errors + verbose logging) goes
    # to stderr and never interleaves with the newline-JSON on stdout.
    _daemon = Daemon()
    sys.stdout = sys.stderr
    _daemon.run()
