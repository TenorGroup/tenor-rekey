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
import time
from x7 import X7, hx
from x7_init import INIT_SEQ

# The dictionary walk is bounded by a wall-clock deadline so an UNKNOWN card fails
# fast instead of grinding the whole ~4.5k-key dictionary for minutes. Two things
# keep real cards fast well inside that budget: key-reuse (a key proven on one sector
# is tried first on every other) and the learned-key cache (a key found once is
# front-loaded on later cards). A brand-new card whose only key sits very deep may
# reach the deadline on its FIRST decode; adding that brand's key in Settings, or a
# later decode seeding the cache, then resolves it. If the deadline fires, dump
# returns whatever was recovered so far. The user cancel path (daemon kill) is separate.
DEFAULT_SCAN_SECONDS = 90

# How many of the dictionary's front keys (common defaults + hotel/vendor brands,
# since the dict is hotel-first) are tried on EVERY sector in the cheap first pass.
# This guarantees each sector a common-key probe so a single hard sector can never
# starve the others, at a bounded cost (~HOT_KEYS_N*2 auths/sector).
HOT_KEYS_N = 24


class _Budget:
    """Wall-clock deadline + auth counter + progress throttle for the dictionary
    walk. `timed_out()` fires once the walk passes max_seconds, bounding an unknown
    card's decode (see DEFAULT_SCAN_SECONDS) so it fails fast rather than exhausting
    the whole dictionary; dump then returns whatever was recovered so far. An optional
    `should_cancel` callable lets the shell abort a long op cooperatively; `expired()`
    (the walk's stop condition) is true on EITHER the deadline or a cancel."""
    def __init__(self, max_seconds=DEFAULT_SCAN_SECONDS, should_cancel=None):
        self.attempts = 0
        self._start = time.monotonic()
        self._deadline = self._start + max_seconds
        self._last_emit = 0.0
        self._should_cancel = should_cancel

    def tick(self):
        self.attempts += 1

    def timed_out(self):
        return time.monotonic() >= self._deadline

    def cancelled(self):
        return self._should_cancel is not None and bool(self._should_cancel())

    def expired(self):
        # The walk stops on the wall-clock watchdog OR a cooperative shell cancel.
        return self.timed_out() or self.cancelled()

    def elapsed(self):
        return time.monotonic() - self._start

    def should_emit(self):
        now = time.monotonic()
        if now - self._last_emit >= 0.25:      # throttle progress to ~4x/second
            self._last_emit = now
            return True
        return False

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
    """The bundled curated dictionary (dict/mfc_keys.dic, ~17.5k keys), or the
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

# How many of the most common keys (the ordered head of the dictionary) find_key
# tries as BOTH KeyA and KeyB before the one-auth-per-key A/B sweeps. This catches
# a card that is only readable with a common KeyB (e.g. KeyB=FF, secret KeyA)
# immediately, at a cost of at most ~2*FAST_HEAD auths on an unknown card.
FAST_HEAD = 64


def sector_count(sak):
    """MIFARE (Classic + Plus SL2/SL3) sector count from SAK, per NXP AN10833:
    Mini (0x09)=5, 1K (0x08/0x88/0x28)=16, 2K Classic (0x19)=32, Plus 2K SL2 (0x10)=32,
    Plus 4K SL2 (0x11)=40, 4K (0x18/0x38)=40. Unknown SAKs default to 1K (16), the
    safest common geometry. The 4K big-sector layout is handled by blocks_in_sector /
    first_block / trailer_block, so only the count needs the richer mapping."""
    return {0x09: 5, 0x08: 16, 0x88: 16, 0x28: 16,
            0x19: 32, 0x10: 32, 0x11: 40, 0x18: 40, 0x38: 40}.get(sak, 16)


def card_kind(sak, atqa):
    """MIFARE family of a polled card. NTAG/Ultralight report SAK 0x00 AND
    ATQA 0x0044; a magic/blank Classic also reports SAK 0x00 but ATQA 0x0004,
    so SAK alone mis-detects it. Everything that is not a genuine NTAG is
    treated as Classic (the decode path auths and reads it). atqa: 2 bytes or int."""
    a = int.from_bytes(atqa, "big") if isinstance(atqa, (bytes, bytearray)) else int(atqa)
    return "ntag" if (sak == 0x00 and a == 0x0044) else "classic"


def blocks_in_sector(s):
    return 4 if s < 32 else 16                   # 4K big sectors


def first_block(s):
    return s * 4 if s < 32 else 128 + (s - 32) * 16


def trailer_block(s):
    return first_block(s) + blocks_in_sector(s) - 1


def access_bits_valid(trailer):
    """True if a MIFARE Classic trailer's access bytes (6, 7, 8) pass the standard
    inverted-complement integrity check. A trailer whose access bytes are corrupt
    must NEVER be written: the wrong condition bits can lock a sector permanently
    (no key can rewrite the trailer again). Mirrors the app's AccessConditions.decode
    integrity flag. Layout: byte6 low nibble = ~C1, high = ~C2; byte7 low = ~C3,
    high = C1; byte8 low = C2, high = C3."""
    if len(trailer) < 9:
        return False
    b6, b7, b8 = trailer[6], trailer[7], trailer[8]
    bit = lambda v, n: (v >> n) & 1
    for i in range(4):
        c1, c2, c3 = bit(b7, 4 + i), bit(b8, i), bit(b8, 4 + i)
        if bit(b6, i) == c1 or bit(b6, 4 + i) == c2 or bit(b7, i) == c3:
            return False
    return True


def trailer_locks_keys(trailer):
    """True if a trailer's access bits leave the sector in a state where NEITHER
    key can ever rewrite the trailer again (keys permanently frozen). Writing such
    a trailer onto a normal card bricks the sector, so the write path refuses it.
    The trailer group (group 3) C1C2C3 in {010, 110, 101, 111} = "keys locked" /
    "fully locked" (matches the app's trailerAccessSummary). The factory trailer
    (ff 07 80) is group-3 001 = "a writes keys+access", so it is not affected."""
    if len(trailer) < 9:
        return True                              # can't tell -> treat as unsafe
    b7, b8 = trailer[7], trailer[8]
    bit = lambda v, n: (v >> n) & 1
    c = (bit(b7, 7), bit(b8, 3), bit(b8, 7))     # group 3 = bits index 4+3, 3, 4+3
    return c in {(0, 1, 0), (1, 1, 0), (1, 0, 1), (1, 1, 1)}


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
        # Did the reader answer InListPassiveTarget at all? A healthy reader with NO
        # card still replies (d5 4b 00); total silence here means a wedged / half-
        # dropped handle (empty reads, no OSError). The daemon reads this flag to tell
        # "no card" apart from "reader unresponsive" and recover the dead handle.
        self.reader_answered = len(r) >= 2 and r[1] == 0x4B
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
        answered = False
        for _ in range(tries):
            i = self.poll()                     # sets self.reader_answered for THIS sub-poll
            answered = answered or getattr(self, "reader_answered", False)
            if i:
                return i
        # Latch the aggregate: the reader is alive if ANY sub-poll got a valid d5 4b,
        # even if the last one happened to time out - so a flaky-but-alive reader is
        # not mistaken for a wedge on the last-poll value alone.
        self.reader_answered = answered
        return None

    def auth(self, block, key, keytype="A", to=700):
        if isinstance(key, str):
            key = bytes.fromhex(key)
        kt = 0x60 if keytype == "A" else 0x61
        # MIFARE crypto1 auth uses the 4-byte (last cascade level) UID; a 7-byte
        # card (Classic EV1) must send its LAST 4 bytes, not all 7, or auth fails.
        r = self._pt([0xD4, 0x40, 0x01, kt, block] + list(key) + list(self.uid[-4:]), reads=4, to=to)
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

    def find_key(self, block, keys=DEFAULT_KEYS, budget=None, on_progress=None):
        """Return (keytype, keyhex) that authenticates `block`, or None.

        Mirrors nfcPro's captured fast cycle so a dictionary walk runs at its speed
        (~26 ms/auth, measured): each attempt is _select() (d4 4e) + auth() (d4 40)
        on a short timeout; a failed auth halts the card, so a re-poll (d4 4a)
        re-selects it. The common head (FAST_HEAD keys) is tried as BOTH KeyA and
        KeyB so a KeyB-only card is caught early; the tail is swept as KeyA then KeyB,
        KeyA preferred throughout.

        `budget` (a _Budget) is the wall-clock watchdog: find_key ticks it per auth
        and stops only if it expires (a runaway guard), so a walk normally runs to
        dictionary exhaustion and a deep in-dict key is still found. Pass budget=None
        for the cheap reuse of keys already proven on the card (always worth trying).
        `on_progress(attempts, phase)` streams live progress (throttled) so the UI
        never looks frozen."""
        if not self.poll() and not self.wait_for_card():
            return None
        n = len(keys)
        head = min(n, FAST_HEAD)

        def attempt(k, kt, phase):
            # returns True (found), False (miss), or "stop" (budget spent)
            if budget is not None:
                if budget.expired():
                    return "stop"
                budget.tick()
                if on_progress is not None and budget.should_emit():
                    on_progress(budget.attempts, phase)
            self._select()
            if self.auth(block, k, kt, to=FAST_TO):
                return True
            self.poll()                                # re-select after the failed auth
            return False

        for i in range(head):                          # common head, A and B
            for kt in ("A", "B"):
                r = attempt(keys[i], kt, "hot")
                if r == "stop":
                    return None
                if r:
                    return (kt, keys[i])
        tail = keys[head:]
        for kt in ("A", "B"):                          # tail as A, then B
            for k in tail:
                r = attempt(k, kt, "dict")
                if r == "stop":
                    return None
                if r:
                    return (kt, k)
        return None

    def dump(self, keys=None, progress=None, on_try=None,
             max_seconds=DEFAULT_SCAN_SECONDS, should_cancel=None):
        """Dump the whole card. Returns blocks/keys/sak/uid + recovered count.

        `keys` is the ranked dictionary (the daemon puts the user's keys first, then
        the hotel-first built-in list). Two passes with key REUSE. Pass A (cheap): on
        every sector retry the keys already proven on this card, then the small hot
        set. Pass B (deep): for each sector pass A left open, retry reuse, then walk
        the REST of the dictionary to EXHAUSTION - every unresolved sector gets its
        full walk, so a deep in-dict key is found even if an earlier sector was a
        genuine miss. The moment a key is proven it is tried FIRST on every later
        sector, so a uniform card resolves in a handful of auths.

        The only non-exhaustion stop is the wall-clock watchdog (max_seconds): a
        runaway guard for a stuck reader, not a "key absent" signal. If it fires,
        dump returns whatever was recovered so far. Reuse is never watchdog-gated.
        `on_try(sector, attempts, walk_total, phase)` streams live progress, where
        walk_total is the ADAPTIVE remaining-work estimate (unresolved sectors *
        candidate count) that shrinks as sectors resolve."""
        if keys is None:
            keys = BUILTIN_KEYS
        info = self.wait_for_card()
        if not info:
            raise RuntimeError("no card on reader")
        target = info["uid"]
        nsec = sector_count(info["sak"])
        blocks, skeys = {}, {}
        assumed = {}                           # sector -> keytype whose slot was guessed
        found_keys, found_set = [], set()      # proven on THIS card, tried first
        budget = _Budget(max_seconds, should_cancel=should_cancel)

        def read_sector(s, found):
            tb = trailer_block(s)
            kt, k = found
            if k in found_set:
                found_keys.remove(k)
            else:
                found_set.add(k)
            found_keys.insert(0, k)             # promote for the remaining sectors
            other = "B" if kt == "A" else "A"
            for b in range(first_block(s), tb + 1):
                if budget.cancelled():
                    break
                data = None
                # Some access-bit configs grant a block's READ to only one key
                # type, so if the found key type cannot read it, retry with the
                # other type (same key value covers the common KeyA==KeyB card).
                for try_kt in (kt, other):
                    for _ in range(6):              # re-auth per block for reliability
                        if not self.poll():
                            continue
                        if self.uid != target:
                            raise RuntimeError("card changed during decode")
                        if not self.auth(tb, k, try_kt):
                            break                  # this key type can't auth; try the other
                        data = self.read_block(b)
                        if data is not None:
                            break
                    if data is not None:
                        break
                blocks[b] = data
            # Patch the trailer: a READ returns the AUTH key slot as zero, so fill it
            # with the recovered key (genuine recovery). The OTHER slot is mirrored
            # only when it too read back all-zero - never leave a 000000 key slot,
            # which a trailer clone would write and could brick a sector - but that
            # mirrored value is a GUESS, so flag the sector in `assumed` so a clone /
            # emulate can surface KeyB (or KeyA) as assumed rather than as fact. A slot
            # that read back non-zero (a genuinely readable key) is kept and not flagged.
            if blocks.get(tb) is not None:
                t = bytearray(blocks[tb])
                kb = bytes.fromhex(k)
                if kt == "A":
                    t[0:6] = kb
                    if t[10:16] == bytes(6):
                        t[10:16] = kb
                        assumed[s] = "B"
                else:
                    t[10:16] = kb
                    if t[0:6] == bytes(6):
                        t[0:6] = kb
                        assumed[s] = "A"
                blocks[tb] = bytes(t)

        def resolve(s, found):
            skeys[s] = found
            if found:
                read_sector(s, found)

        # PASS A - cheap, NEVER budget-capped: on every sector try the keys already
        # proven on this card, then the small hot set (the dictionary's front: common
        # defaults + hotel/vendor brands). This guarantees each sector a common-key
        # probe, so one hard sector can never starve the others of an easy key, and a
        # uniform card resolves entirely here (sector 0 finds the key, the rest reuse).
        hot = keys[:HOT_KEYS_N]
        for s in range(nsec):
            if budget.cancelled():
                break
            self.poll()
            if self.uid != target:
                raise RuntimeError("card changed during decode")
            found = None
            if found_keys:                                     # reuse: ALWAYS worth trying,
                found = self.find_key(trailer_block(s),        # so never budget-gated
                                      list(found_keys), budget=None)
            if found is None:
                # Budget-gate the hot-set sweep so an unknown card cannot overrun
                # max_seconds inside PASS A (this used to run budget=None on every
                # sector, uncounted and uncancellable); the deadline + cancel + attempt
                # count now cover PASS A too.
                found = self.find_key(trailer_block(s), hot, budget=budget)
            if found:                                          # only report resolved here;
                resolve(s, found)                              # the rest are handled in pass B
                if progress:
                    progress(s, nsec, found)

        # PASS B - the deep dictionary walk, only on sectors pass A left open. Reuse
        # is retried first (a walk hit on one sector can open a later one); the deep
        # walk skips the hot prefix (already tried) and runs to EXHAUSTION for every
        # unresolved sector - no early-exit on a full miss, so the Nth sector's deep
        # key is found even when an earlier sector had none. Only the wall-clock
        # watchdog (a stuck reader) stops the loop early, returning partial.
        deep = keys[HOT_KEYS_N:]
        for s in range(nsec):
            if s in skeys:
                continue
            if budget.cancelled():
                break
            self.poll()
            if self.uid != target:
                raise RuntimeError("card changed during decode")
            found = None
            if found_keys:
                found = self.find_key(trailer_block(s), list(found_keys), budget=None)
            if found is None and not budget.expired():
                trial = [k for k in deep if k not in found_set]
                # Adaptive remaining-work total: unresolved sectors (this one included,
                # it is not in skeys yet) times the candidate count. It SHRINKS as
                # sectors resolve, so the UI shows an honest "attempts / walk_total".
                remaining = sum(1 for i in range(nsec) if i not in skeys)
                walk_total = remaining * len(trial)
                found = self.find_key(
                    trailer_block(s), trial, budget=budget,
                    on_progress=(lambda attempts, phase, s=s, wt=walk_total:
                                 on_try(s, attempts, wt, phase)) if on_try else None)
            resolve(s, found)
            if progress:
                progress(s, nsec, found)
        recovered = sum(1 for v in skeys.values() if v)
        return {"uid": info["uid"], "sak": info["sak"], "atqa": info["atqa"],
                "blocks": blocks, "keys": skeys, "sectors": nsec,
                "recovered": recovered, "attempts": budget.attempts,
                "exhausted": budget.timed_out(), "cancelled": budget.cancelled(),
                "assumed_keys": assumed}

    def read_ntag(self, max_pages=240):
        """Dump an NTAG21x / Ultralight (SAK 0x00). Returns dict page->4 bytes.

        READ always returns 4 pages and, past the last page, rolls over to page 0,
        so a fixed 4-page-step loop both mislabels the wrapped tail (storing page 0
        under a high index) and under-dumps large tags. We read page by page and
        keep only the first page of each READ, stopping as soon as a READ rolls
        over (its data equals page 0) or the card NAKs past the end. max_pages caps
        the largest type (NTAG216 = 231 pages) so an oddly-behaving tag can't spin."""
        out = {}
        if not self.poll():
            raise RuntimeError("no card")
        page0 = None
        for p in range(max_pages):
            r = self._pt([0xD4, 0x40, 0x01, 0x30, p])
            if not (len(r) >= 7 and r[1] == 0x41 and r[2] == 0x00):
                if not self.poll():       # NAK past end of memory: stop
                    break
                continue
            first = bytes(r[3:7])
            if p == 0:
                page0 = first
            elif first == page0:          # rolled over to page 0: past end of memory
                break
            out[p] = first
        return out

    def apdu(self, data):
        """Send a raw APDU to a selected ISO14443-4 / CPU card via InDataExchange.

        Returns the card's response bytes only. We read the decoded envelope
        payload (which the transport bounds with `total`) rather than _pt's raw
        slice, so the HID envelope checksum + 0xFD trailer + zero padding never
        leak into the result - the apdu console showed that trailing junk before."""
        if isinstance(data, str):
            data = bytes.fromhex(data)
        cmd = [0xD4, 0x40, 0x01] + list(data)
        dec, _ = self.x.cmd([0xFF, 0x00, 0x00, 0x00, len(cmd)] + cmd, reads=8, timeout=700)
        pl = dec.get("payload") if dec else None
        if not pl:
            return None
        j = pl.find(b"\xd5")             # InDataExchange response: d5 41 <status> <data>
        if j < 0 or len(pl) < j + 3 or pl[j + 1] != 0x41:
            return None
        return bytes(pl[j + 3:])         # card response, transport envelope excluded

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

    # CIU (PN53x/RC522) register addresses. CONFIRMED live against an nfcPro darkside
    # crack USB capture (2026-07-14, card a7 04 e5 04) cross-checked with libnfc
    # pn53x-internal.h. The earlier CIU_MFRX=0x6312 was WRONG (0x6312 is CRCResultLSB);
    # parity control is ManualRCV 0x630D bit4.
    CIU_MANUALRCV = 0x630D   # bit4 (0x10) = ParityDisable; nfcPro writes 0x10 to turn parity OFF
    CIU_BITFRAMING = 0x633D  # TxLastBits[2:0]
    CIU_CONTROL = 0x633C     # Control: RxLastBits[2:0] (== 4 for the darkside 4-bit NACK)
    CIU_COLL = 0x633E        # CollReg
    CIU_FIFOLEVEL = 0x633A   # FIFOLevel
    CIU_ERROR = 0x6336       # ErrorReg: ParityErr bit
    CIU_TXMODE = 0x6302      # TxMode: bit7 = TxCRCEn (clear for raw frames)
    CIU_RXMODE = 0x6303      # RxMode: bit7 = RxCRCEn (clear for raw frames)

    def _set_parity_raw(self, raw):
        """Disable (raw=True) or enable the controller's automatic parity, so software
        can supply/observe the parity bits a darkside attack needs. PN53x ManualRCV
        0x630D bit4 (0x10): 1 = parity OFF. CONFIRMED live in the nfcPro darkside
        capture (writes 0x10 to disable, 0x00 to re-enable). Returns True on success."""
        v = self.reg_read(self.CIU_MANUALRCV)
        if v is None:
            return False
        v = (v | 0x10) if raw else (v & ~0x10)
        return self.reg_write(self.CIU_MANUALRCV, v)

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
            is set (CIU_MANUALRCV 0x630D bit4) and you read it from the FIFO/CollReg. Confirm the
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
                     + list(kk) + list(self.uid[-4:]))
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
