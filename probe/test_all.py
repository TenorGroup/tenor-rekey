#!/usr/bin/env python3
"""Hardware-free regression suite for the X7 engine + daemon contract.

Runs with NO reader attached: a FakeCard stands in for the hardware so the REAL
x7lib logic (find_key sweep, dump key-reuse + exhaustive walk, the x7d.py daemon
dispatch + write/format gating) is exercised end to end. Crypto + the _pt anchor
have their own self-tests, invoked here too.

    python3 test_all.py            # fast suite
    SLOW_TESTS=1 python3 test_all.py   # also the ~30s lfsr_recovery32 crypto test

Exit code 0 = all passed.
"""
import json
import os
import subprocess
import sys
import tempfile

import x7d
import x7lib
import learned_keys

# Keep every test's default learned-key cache off the real App Support store.
os.environ.setdefault("X7_LEARNED_PATH",
                      os.path.join(tempfile.gettempdir(), "rekey-test-learned.json"))
from x7lib import (DEFAULT_KEYS, trailer_block, first_block, sector_count,
                   blocks_in_sector)
from learned_keys import LearnedKeyCache

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("[ok] " if cond else "[XX] ") + name + (("  -> " + detail) if detail and not cond else ""))


# --------------------------------------------------------------------------
# FakeCard: implements the X7Card surface the daemon/dump touch, in memory.
# `keymap`: sector -> (keytype, keyhex) the card accepts (None = unknown).
# Records every write so the write/format contract can be asserted.
# --------------------------------------------------------------------------
class FakeCard(x7lib.X7Card):
    def __init__(self, sak=0x08, keymap=None, uid=b"\x01\x02\x03\x04",
                 data=None, ntag=None):
        self.sak = sak
        self.uid = uid
        self.keymap = keymap if keymap is not None else {}
        self.data = data or {}            # block -> 16 bytes (for reads)
        self.writes = []                  # (block, bytes) in order
        self.ntag = ntag or {}
        self.full_sweeps = []             # sectors that got a full-dict find_key
        self.closed = False

    # ---- poll / select ----
    def _info(self):
        return {"uid": self.uid, "atqa": b"\x00\x04", "sak": self.sak}

    def poll(self):
        return self._info()

    def wait_for_card(self, tries=25):
        return self._info()

    def _select(self):
        pass

    # ---- auth / read / write ----
    def auth(self, block, key, keytype="A", to=700):
        s = block // 4 if block < 128 else 32 + (block - 128) // 16
        want = self.keymap.get(s)
        kh = key if isinstance(key, str) else key.hex()
        if want and want[0] == keytype and want[1] == kh:
            return True
        # factory FF auth also opens a "blank" sector with no assigned key
        return want is None and kh == "ffffffffffff"

    def find_key(self, block, keys=DEFAULT_KEYS, budget=None, on_progress=None):
        s = block // 4 if block < 128 else 32 + (block - 128) // 16
        if len(keys) > len(DEFAULT_KEYS) + 2:
            self.full_sweeps.append(s)          # a real dictionary walk (vs cheap reuse)
        found = self.keymap.get(s)
        return found if found and found[1] in keys else None

    def read_block(self, block):
        return self.data.get(block, bytes(16))

    def write_block(self, block, data16):
        if isinstance(data16, str):
            data16 = bytes.fromhex(data16)
        self.writes.append((block, bytes(data16)))
        return True

    def read_ntag(self, pages=45):
        return dict(self.ntag)

    def apdu(self, data):
        return b"\x90\x00"

    def close(self):
        self.closed = True


def daemon_with(card, learned=None):
    # learned=None -> Daemon builds its default cache at X7_LEARNED_PATH (a temp
    # file, set at import), so daemon tests never touch the real store.
    d = x7d.Daemon(learned=learned)
    d.card = card
    d._open = lambda: card
    return d


# --------------------------------------------------------------------------
# 1. find_key: ONE auth per key, full KeyA sweep before KeyB, A preferred.
# --------------------------------------------------------------------------
def _counter_card(accept):
    """A hardware-free card that records auth order; `accept(kt, keyhex)->bool`."""
    class Counter(x7lib.X7Card):
        def __init__(self):
            self.uid = b"\x01\x02\x03\x04"; self.order = []
        def poll(self): return {"uid": self.uid, "atqa": b"\x00\x04", "sak": 0x08}
        def wait_for_card(self, tries=25): return self.poll()
        def _select(self): pass
        def auth(self, block, key, keytype="A", to=700):
            kh = key if isinstance(key, str) else key.hex()
            self.order.append((keytype, kh))
            return accept(keytype, kh)
    return Counter()


def test_find_key_sweep():
    GARBAGE = ["%012x" % (0xc0de0000 + i) for i in range(70)]   # > FAST_HEAD, none match

    # 1. KeyB preferred-key in the COMMON HEAD is caught fast (the regression the
    #    hybrid head fixes): KeyA secret, KeyB=FF (FF is head index 0).
    c = _counter_card(lambda kt, kh: kt == "B" and kh == "ffffffffffff")
    got = c.find_key(3, ["ffffffffffff"] + GARBAGE)
    check("find_key catches a common KeyB (head A+B) fast",
          got == ("B", "ffffffffffff") and len(c.order) <= 2, "%r in %d auths" % (got, len(c.order)))

    # 2. A KeyB-only key in the TAIL (index > FAST_HEAD) is recovered after the
    #    tail KeyA sweep.
    keyb = GARBAGE[66]
    c2 = _counter_card(lambda kt, kh: kt == "B" and kh == keyb)
    check("find_key recovers a KeyB-only key in the tail",
          c2.find_key(3, GARBAGE) == ("B", keyb))

    # 3. Tail is swept entirely as KeyA before KeyB: a KeyA key LATER in the tail
    #    and a KeyB key EARLIER (both past the head) -> the A sweep finds KeyA first.
    keya = GARBAGE[68]; keyb2 = GARBAGE[64]
    c3 = _counter_card(lambda kt, kh: (kt == "A" and kh == keya) or (kt == "B" and kh == keyb2))
    check("find_key sweeps the tail as KeyA before KeyB",
          c3.find_key(3, GARBAGE) == ("A", keya))

    # 4. KeyA preferred when a head key works as both.
    c4 = _counter_card(lambda kt, kh: kh == "a0b1c2d3e4f5")
    check("find_key prefers KeyA when both work",
          c4.find_key(3, ["a0b1c2d3e4f5"] + GARBAGE) == ("A", "a0b1c2d3e4f5"))


# --------------------------------------------------------------------------
# 2. dump: reuse-first, exhaustive (no attempt cap) walk.
# --------------------------------------------------------------------------
def test_dump_reuse_and_budget():
    BIG = ["%012x" % i for i in range(17000)]
    deep = "%012x" % 9000                       # in BIG, well past the defaults

    # uniform deep key: only sector 0 walks the dictionary, the rest reuse it.
    c = FakeCard(keymap={s: ("A", deep) for s in range(16)})
    d = c.dump(keys=BIG)
    check("uniform card walks the dictionary once, the rest reuse", c.full_sweeps == [0],
          str(c.full_sweeps))
    check("uniform card recovers all 16 sectors", d["recovered"] == 16, str(d["recovered"]))
    check("dump reports recovered / attempts / exhausted",
          all(k in d for k in ("recovered", "attempts", "exhausted")))

    # all-unknown -> recovers nothing (every sector gets a full walk, none hit).
    f = FakeCard(keymap={s: None for s in range(16)})
    d2 = f.dump(keys=BIG)
    check("unknown card recovers nothing", d2["recovered"] == 0)

    # reuse ALWAYS runs (never watchdog-gated): a sector sharing a proven key is
    # recovered even when it sits after an unknown sector.
    km = {s: ("A", deep) for s in range(16)}; km[7] = None
    d3 = FakeCard(keymap=km).dump(keys=BIG)
    check("reuse recovers shared-key sectors past an unknown one (15/16)",
          d3["recovered"] == 15, str(d3["recovered"]))


def test_dump_exhausts_deep_key():
    # The walk now runs to dictionary EXHAUSTION per unresolved sector (no attempt
    # cap). A uniform card whose key sits DEEP - index ~4000 of a ~4500-entry list,
    # far past the old 3500 cap - must STILL be recovered. With the REAL find_key
    # (which walks + ticks) only sector 0 pays the deep walk; sectors 1-15 reuse it.
    BIG = ["%012x" % i for i in range(4500)]
    deep = BIG[4000]                            # past the old fixed cap

    class Walk(FakeCard):
        def find_key(self, block, keys=DEFAULT_KEYS, budget=None, on_progress=None):
            return x7lib.X7Card.find_key(self, block, keys, budget, on_progress)
        def auth(self, block, key, keytype="A", to=700):
            return FakeCard.auth(self, block, key, keytype, to)

    c = Walk(keymap={s: ("A", deep) for s in range(16)})
    d = c.dump(keys=BIG, max_seconds=999)
    check("a deep in-dict key past the old cap is still found (exhaustive walk)",
          d["recovered"] == 16 and not d["exhausted"],
          "recovered=%d exhausted=%s attempts=%d" % (d["recovered"], d["exhausted"], d["attempts"]))
    check("the exhaustive walk really went past the old fixed cap",
          d["attempts"] > 3500, "attempts=%d" % d["attempts"])


def test_dump_hard_sector_no_starve():
    # sector 0 is proprietary (not in the dictionary) and walks the dictionary to
    # exhaustion, but sectors 1-15 use a common key that sits in the hot prefix. The
    # cheap per-sector hot-set probe (pass A) must still recover 1-15 - one hard
    # sector may not starve the rest.
    BIG = ["%012x" % i for i in range(17000)]
    common = "%012x" % 3                        # inside HOT_KEYS_N (the hot prefix)

    class Walk(FakeCard):
        def find_key(self, block, keys=DEFAULT_KEYS, budget=None, on_progress=None):
            return x7lib.X7Card.find_key(self, block, keys, budget, on_progress)
        def auth(self, block, key, keytype="A", to=700):
            return FakeCard.auth(self, block, key, keytype, to)

    km = {s: ("A", common) for s in range(16)}; km[0] = None
    d = Walk(keymap=km).dump(keys=BIG, max_seconds=999)
    check("a hard sector does not starve common-key sectors (15/16)",
          d["recovered"] == 15, "recovered=%d" % d["recovered"])


# --------------------------------------------------------------------------
# 3. daemon: keys surface (no real-key leak), counts.
# --------------------------------------------------------------------------
def test_daemon_keys():
    d = daemon_with(FakeCard())
    kd = d.keys_default({})["keys"]
    # keys_default must be EXACTLY the curated in-tree list; that is also the
    # no-leak guarantee - a real deployment key (entered only via user_keys on the
    # founder's machine) can never appear here, so it is never shipped to the UI.
    check("keys_default mirrors x7lib DEFAULT_KEYS", kd == list(DEFAULT_KEYS))
    check("keys_default is all well-formed 12-hex keys (no leaked/garbage entry)",
          all(len(k) == 12 and all(c in "0123456789abcdef" for c in k) for k in kd))
    check("keys_builtin_count matches the loaded dictionary",
          d.keys_builtin_count({})["count"] == len(x7lib.BUILTIN_KEYS))


# --------------------------------------------------------------------------
# 4. daemon decode: events + user-keys-first merge.
# --------------------------------------------------------------------------
def test_daemon_decode():
    emitted = []
    card = FakeCard(keymap={s: ("A", "ffffffffffff") for s in range(16)})
    d = daemon_with(card)
    d.emit = lambda o: emitted.append(o)
    r = d.decode({})
    check("decode reports 16/16", r["recovered"] == 16 and r["sectors"] == 16)
    check("decode emits a progress event per sector",
          sum(1 for e in emitted if e.get("method") == "decode" and "keytype" in e) == 16)
    # user key is tried first (the daemon merges user_keys + builtin, user-first)
    card2 = FakeCard(keymap={s: ("A", "aa11bb22cc33") for s in range(16)})
    d2 = daemon_with(card2); d2.emit = lambda o: None
    r2 = d2.decode({"user_keys": ["aa11bb22cc33"]})
    check("decode honours a user key not in the builtin dict", r2["recovered"] == 16)


# --------------------------------------------------------------------------
# 5. daemon write_mfd: gates (uid/trailer skipped unless opted in) + FF fallback.
# --------------------------------------------------------------------------
FACTORY_TRAILER_HEX = "ffffffffffff" + "ff078069" + "ffffffffffff"


def test_daemon_write():
    # sector 0: data blocks 0..2 + a VALID factory trailer at block 3
    blocks = {"0": "00" * 16, "1": "00" * 16, "2": "00" * 16, "3": FACTORY_TRAILER_HEX}
    keys = {"0": ["A", "a0b1c2d3e4f5"]}

    # trailers/uid OFF -> only data blocks 1,2 (skip uid block 0 + trailer block 3)
    c = FakeCard(keymap={0: ("A", "a0b1c2d3e4f5")})
    d = daemon_with(c); d.emit = lambda o: None
    res = d.write_mfd({"blocks": blocks, "keys": keys, "trailers": False, "uid": False})
    written = sorted(b for b, _ in c.writes)
    check("write_mfd OFF/OFF writes only data blocks", written == [1, 2], str(written))
    check("write_mfd OFF/OFF reports wrote==2", res["wrote"] == 2 and res["failed"] == [])

    # both ON -> all 4 blocks (incl uid 0 + valid trailer 3)
    c2 = FakeCard(keymap={0: ("A", "a0b1c2d3e4f5")})
    d2 = daemon_with(c2); d2.emit = lambda o: None
    check("write_mfd ON/ON writes all 4 blocks",
          d2.write_mfd({"blocks": blocks, "keys": keys, "trailers": True, "uid": True})["wrote"] == 4)

    # FF fallback: a blank magic sector (no assigned key) still takes the write
    c3 = FakeCard(keymap={0: None})    # FakeCard.auth returns True for FF when key is None
    d3 = daemon_with(c3); d3.emit = lambda o: None
    res3 = d3.write_mfd({"blocks": blocks, "keys": keys, "trailers": True, "uid": True})
    check("write_mfd falls back to factory FF on a blank sector", res3["wrote"] == 4, str(res3))

    # target_uid pins the write to the card the UI showed: a mismatch refuses to
    # write, so a card swapped in before the daemon acquired one is never cloned onto.
    ct = FakeCard(keymap={0: ("A", "a0b1c2d3e4f5")})    # uid 01 02 03 04
    dt = daemon_with(ct); dt.emit = lambda o: None
    rt = dt.write_mfd({"blocks": blocks, "keys": keys, "trailers": True, "uid": True,
                       "target_uid": "09 09 09 09"})
    check("write_mfd refuses a target uid that is not the card on the reader",
          rt.get("error") == "the card on the reader is not the target shown" and ct.writes == [],
          str(rt))
    # the matching uid writes normally
    cm = FakeCard(keymap={0: ("A", "a0b1c2d3e4f5")})
    dm = daemon_with(cm); dm.emit = lambda o: None
    rm = dm.write_mfd({"blocks": blocks, "keys": keys, "trailers": True, "uid": True,
                       "target_uid": "01 02 03 04"})
    check("write_mfd writes when the target uid matches", rm["wrote"] == 4 and not rm.get("error"), str(rm))

    # no card -> guarded
    cg = FakeCard(); cg.wait_for_card = lambda tries=25: None
    dg = daemon_with(cg); dg.emit = lambda o: None
    check("write_mfd guards no-card", dg.write_mfd({"blocks": blocks, "keys": keys})["present"] is False)


def test_write_safety():
    keys = {"0": ["A", "a0b1c2d3e4f5"]}

    # a trailer with CORRUPT access bytes is refused (would brick the sector)
    bad_trailer = "ffffffffffff" + "000000" + "00" + "ffffffffffff"   # 000000 access = invalid
    c = FakeCard(keymap={0: ("A", "a0b1c2d3e4f5")})
    d = daemon_with(c); d.emit = lambda o: None
    res = d.write_mfd({"blocks": {"3": bad_trailer}, "keys": keys, "trailers": True, "uid": False})
    check("write_mfd refuses a trailer with corrupt access bytes",
          3 in res["failed"] and 3 not in [b for b, _ in c.writes], str(res))

    # a trailer that LOCKS its own keys (valid integrity, group3=111) is refused -
    # writing it would permanently brick the sector
    locked_trailer = "ffffffffffff" + "00f0ff" + "ffffffffffff"
    cl = FakeCard(keymap={0: ("A", "a0b1c2d3e4f5")})
    dl = daemon_with(cl); dl.emit = lambda o: None
    rl = dl.write_mfd({"blocks": {"3": locked_trailer}, "keys": keys, "trailers": True, "uid": False})
    check("write_mfd refuses a self-locking trailer",
          3 in rl["failed"] and 3 not in [b for b, _ in cl.writes], str(rl))

    # a trailer with a 000000 key slot has the recovered key substituted in (never
    # writes 000000, which could brick), access bytes preserved
    zero_b_trailer = "a0b1c2d3e4f5" + "ff078069" + "000000000000"
    c2 = FakeCard(keymap={0: ("A", "a0b1c2d3e4f5")})
    d2 = daemon_with(c2); d2.emit = lambda o: None
    d2.write_mfd({"blocks": {"3": zero_b_trailer}, "keys": keys, "trailers": True, "uid": False})
    wrote3 = [data for b, data in c2.writes if b == 3]
    check("write_mfd substitutes a 000000 key slot with the recovered key",
          wrote3 and wrote3[0][10:16] == bytes.fromhex("a0b1c2d3e4f5")
          and wrote3[0][6:10] == bytes.fromhex("ff078069"), wrote3[0].hex() if wrote3 else "none")

    # a malformed (wrong-length) block is recorded failed, never sent to write_block
    c3 = FakeCard(keymap={0: ("A", "a0b1c2d3e4f5")})
    d3 = daemon_with(c3); d3.emit = lambda o: None
    res3 = d3.write_mfd({"blocks": {"1": "00" * 15}, "keys": keys, "trailers": False, "uid": False})
    check("write_mfd rejects a wrong-length block (no half-write)",
          res3["failed"] == [1] and c3.writes == [], str(res3))

    # card swap mid-write aborts instead of writing to the wrong card
    class Swapper(FakeCard):
        def __init__(self, **kw):
            super().__init__(**kw); self.n = 0
        def poll(self):
            self.n += 1
            if self.n > 1:                 # a different card drifts in after the first poll
                self.uid = b"\x09\x09\x09\x09"
            return {"uid": self.uid, "atqa": b"\x00\x04", "sak": 0x08}
    cs = Swapper(keymap={s: ("A", "a0b1c2d3e4f5") for s in range(16)})
    ds = daemon_with(cs); ds.emit = lambda o: None
    blocks = {str(b): "00" * 16 for b in range(1, 3)}
    rs = ds.write_mfd({"blocks": blocks, "keys": keys, "trailers": False, "uid": False})
    check("write_mfd aborts on a mid-write card swap", rs.get("error") == "card changed during write", str(rs))


def test_dump_swap_safety():
    # A different card arrives after sector 0 was read. The next sector boundary
    # poll must abort the whole dump instead of returning a mixed-card image.
    class Swapper(FakeCard):
        def __init__(self, **kw):
            super().__init__(**kw)
            self.swap_ready = False
        def poll(self):
            if self.swap_ready:
                self.uid = b"\x09\x09\x09\x09"
            return self._info()
        def read_block(self, block):
            data = super().read_block(block)
            if block == trailer_block(0):
                self.swap_ready = True
            return data

    c = Swapper(keymap={s: ("A", DEFAULT_KEYS[0]) for s in range(16)})
    message = None
    try:
        c.dump(keys=list(DEFAULT_KEYS))
    except RuntimeError as error:
        message = str(error)
    check("dump aborts on a mid-decode card swap",
          message == "card changed during decode", repr(message))


def test_dump_keyb_read_fallback():
    # A card whose DATA block reads only with KeyB (KeyA==KeyB value). dump must
    # retry the read with the other key type instead of losing the block.
    class KeyBReadCard(FakeCard):
        def read_block(self, block):
            # block 1 readable only after a KeyB auth; emulate by tracking last auth
            if block == 1 and self._last_kt != "B":
                return None
            return bytes(16)
        def auth(self, block, key, keytype="A", to=700):
            self._last_kt = keytype
            kh = key if isinstance(key, str) else key.hex()
            return kh == "a0b1c2d3e4f5"      # KeyA==KeyB: the key auths as either type
    c = KeyBReadCard(keymap={s: ("A", "a0b1c2d3e4f5") for s in range(16)})
    c._last_kt = None
    d = c.dump(keys=["a0b1c2d3e4f5"])
    check("dump retries a KeyB-only-readable data block with the other key",
          d["blocks"].get(1) is not None, repr(d["blocks"].get(1)))


def test_dump_trailer_mirror():
    # When KeyB reads back as zero (unrecovered), dump mirrors the recovered key
    # into the KeyB slot so a trailer clone never writes 000000.
    c = FakeCard(keymap={0: ("A", "aabbccddeeff")})
    d = daemon_with(c); d.emit = lambda o: None
    r = d.decode({})
    tb = r["blocks"]["3"].replace(" ", "")
    check("dump mirrors the recovered key into a zero KeyB slot",
          tb[0:12] == "aabbccddeeff" and tb[20:32] == "aabbccddeeff", tb)


def test_access_bits_valid():
    factory = bytes.fromhex("ffffffffffff" + "ff078069" + "ffffffffffff")
    corrupt = bytes.fromhex("ffffffffffff" + "000000" + "00" + "ffffffffffff")
    locked = bytes.fromhex("ffffffffffff" + "00f0ff" + "ffffffffffff")   # valid integrity, group3=111
    check("access_bits_valid accepts the factory trailer", x7lib.access_bits_valid(factory))
    check("access_bits_valid rejects an all-zero access triple", not x7lib.access_bits_valid(corrupt))
    check("trailer_locks_keys: factory trailer is writable", not x7lib.trailer_locks_keys(factory))
    check("trailer_locks_keys: a fully-locked trailer is flagged",
          x7lib.access_bits_valid(locked) and x7lib.trailer_locks_keys(locked))


def test_card_kind():
    # NTAG/Ultralight is the ONLY SAK 0x00 + ATQA 0x0044 card; a magic/blank
    # Classic reports SAK 0x00 too but ATQA 0x0004, so SAK alone mis-detects it.
    check("card_kind: genuine NTAG (sak 0x00, atqa 0x0044)",
          x7lib.card_kind(0x00, b"\x00\x44") == "ntag")
    check("card_kind: magic/blank Classic (sak 0x00, atqa 0x0004)",
          x7lib.card_kind(0x00, b"\x00\x04") == "classic")
    check("card_kind: normal Classic 1K (sak 0x08)",
          x7lib.card_kind(0x08, b"\x00\x04") == "classic")
    check("card_kind accepts an int atqa", x7lib.card_kind(0x00, 0x0044) == "ntag")
    # the daemon poll must surface the kind so the UI never re-derives it
    d = daemon_with(FakeCard(sak=0x00))
    check("daemon poll surfaces card_kind", d.poll({}).get("kind") == "classic")


def test_read_ntag_wrap():
    # A 6-page tag whose READ rolls over to page 0 past the end. read_ntag must
    # stop at the wrap, not store wrapped pages under high indices.
    pageset = {p: bytes([p, p, p, p]) for p in range(6)}
    class NtagCard(FakeCard):
        def read_ntag(self, max_pages=240):                # use the real wrap logic
            return x7lib.X7Card.read_ntag(self, max_pages=max_pages)
        def _pt(self, cmd, reads=8, to=700):
            p = cmd[4]
            src = pageset.get(p % 6)   # rolls over past page 5
            return bytes([0xD5, 0x41, 0x00]) + src + bytes(8)   # _pt result starts at 0xD5
    c = NtagCard(sak=0x00)
    pages = c.read_ntag(max_pages=240)
    check("read_ntag stops at end-of-memory (no wrapped pages)",
          len(pages) == 6 and pages[0] == bytes([0, 0, 0, 0]), "got %d pages" % len(pages))


# --------------------------------------------------------------------------
# 6. daemon format: trailer written LAST per sector, block 0 left alone.
# --------------------------------------------------------------------------
def test_daemon_format():
    c = FakeCard(keymap={s: ("A", "ffffffffffff") for s in range(16)})
    d = daemon_with(c); d.emit = lambda o: None
    res = d.format({"keys": {str(s): ["A", "ffffffffffff"] for s in range(16)}})
    blks = [b for b, _ in c.writes]
    check("format never writes block 0 (uid)", 0 not in blks)
    # within sector 0, trailer block 3 is written AFTER data blocks 1,2
    s0 = [b for b in blks if b in (1, 2, 3)]
    check("format writes a sector's trailer last", s0 and s0[-1] == 3, str(s0))
    # trailer bytes are the factory transport config
    tw = [data for b, data in c.writes if b == 3]
    check("format writes the factory trailer", tw and tw[-1] == x7d.FACTORY_TRAILER, tw[-1].hex() if tw else "none")
    check("format reports no failures", res["failed"] == [])


# --------------------------------------------------------------------------
# 7. daemon read_ntag + apdu + no-card guards.
# --------------------------------------------------------------------------
def test_daemon_ntag_apdu():
    c = FakeCard(sak=0x00, ntag={0: b"\x04\x11\x22\x33", 1: b"\xaa\xbb\xcc\xdd"})
    d = daemon_with(c); d.emit = lambda o: None
    r = d.read_ntag({})
    check("read_ntag returns pages", r["present"] and r["pages"]["0"] == "04 11 22 33")

    c2 = FakeCard(); d2 = daemon_with(c2); d2.emit = lambda o: None
    ra = d2.apdu({"hex": "00a4040000"})
    check("apdu returns a response", ra["present"] and ra["resp"] == "90 00")

    cg = FakeCard(); cg.wait_for_card = lambda tries=25: None
    dg = daemon_with(cg); dg.emit = lambda o: None
    check("apdu guards no-card", dg.apdu({"hex": "00"})["present"] is False)
    check("read_ntag guards no-card", dg.read_ntag({})["present"] is False)


# --------------------------------------------------------------------------
# 8. daemon dispatch robustness (no hardware path).
# --------------------------------------------------------------------------
def test_daemon_dispatch():
    d = daemon_with(FakeCard())
    check("unknown method -> error envelope",
          "error" in d.handle({"id": 1, "method": "frobnicate"}))
    r = d.handle({"id": 2, "method": "keys_builtin_count"})
    check("known method -> result envelope", r.get("result", {}).get("count") == len(x7lib.BUILTIN_KEYS))
    # missing required param surfaces as an error, not a crash
    bad = daemon_with(FakeCard())
    check("missing param -> error envelope, no crash", "error" in bad.handle({"id": 3, "method": "apdu", "params": {}}))


# --------------------------------------------------------------------------
# 9. apdu envelope parse: card data only, no transport trailer/padding.
# --------------------------------------------------------------------------
def test_apdu_parse():
    # Build a realistic decoded envelope payload: [00, d5, 41, status, <data>]
    # and confirm apdu() returns just <data> even though _pt's raw would carry the
    # checksum + 0xFD + zero padding after it.
    class Stub(x7lib.X7Card):
        def __init__(self): self.uid = b"\x01\x02\x03\x04"
    s = Stub()
    s.x = type("X", (), {})()
    payload = bytes([0x00, 0xD5, 0x41, 0x00, 0x90, 0x00])   # data = 90 00
    s.x.cmd = lambda *a, **k: ({"payload": payload}, [])
    check("apdu parses card data without transport bytes", s.apdu("3000") == b"\x90\x00")
    s.x.cmd = lambda *a, **k: (None, [])
    check("apdu returns None on no response", s.apdu("3000") is None)


# --------------------------------------------------------------------------
# 10. subprocess self-tests (crypto + _pt anchor).
# --------------------------------------------------------------------------
def test_subprocess_selftests():
    here = os.path.dirname(os.path.abspath(__file__))
    env = dict(os.environ)
    for mod in ("crapto1.py", "test_pt_anchor.py", "x7crypto.py"):
        r = subprocess.run([sys.executable, mod], cwd=here, env=env,
                           capture_output=True, text=True, timeout=300)
        ok = r.returncode == 0
        check("self-test: %s" % mod, ok, (r.stdout + r.stderr)[-200:])


# --------------------------------------------------------------------------
# 11. learned-key cache: the persistent reranker over the static dictionary.
# --------------------------------------------------------------------------
def test_learned_keys():
    paths = []

    def newpath():
        fd, p = tempfile.mkstemp(prefix="lk-test-", suffix=".json")
        os.close(fd)
        paths.append(p)
        return p

    def make(now=None):
        return LearnedKeyCache(path=newpath(), now=now)

    # a deterministic clock so recency ordering is reproducible
    tick = [1000.0]
    def now():
        tick[0] += 1.0
        return tick[0]

    try:
        # record then read back
        c = make(now)
        n = c.record(["aaaaaaaaaaaa", "bbbbbbbbbbbb"], uid="0a1b2c3d")
        tk = set(c.top_keys())
        check("learned: record returns count of keys stored", n == 2, str(n))
        check("learned: top_keys returns the recorded keys",
              tk == {"aaaaaaaaaaaa", "bbbbbbbbbbbb"}, str(tk))

        # a key recorded twice (different uids) -> hits=2, outranks a hits=1 key
        c2 = make(now)
        c2.record(["cccccccccccc"], uid="1111")
        c2.record(["dddddddddddd"], uid="2222")
        c2.record(["cccccccccccc"], uid="3333")          # cccc now hits=2
        e = c2._find("cccccccccccc")
        check("learned: a key seen twice reaches hits=2", e["hits"] == 2, str(e["hits"]))
        check("learned: the higher-hit key ranks first",
              c2.top_keys()[0] == "cccccccccccc", str(c2.top_keys()))

        # uid grouping beats hit count: a uid match ranks ahead of a busier key
        c3 = make(now)
        c3.record(["111111111111"], uid="cafe")           # hits=1, uid cafe
        c3.record(["222222222222"])                        # hits=1
        c3.record(["222222222222"])                        # hits=2
        c3.record(["222222222222"])                        # hits=3, no uid
        check("learned: without a uid the busiest key ranks first",
              c3.top_keys()[0] == "222222222222", str(c3.top_keys()))
        check("learned: top_keys(uid) floats uid-matching entries to the front",
              c3.top_keys(uid="cafe")[0] == "111111111111", str(c3.top_keys(uid="cafe")))

        # limit is honoured
        check("learned: top_keys respects the limit argument",
              len(c3.top_keys(limit=1)) == 1, str(c3.top_keys(limit=1)))

        # quota: > MAX_ENTRIES distinct keys caps the store and evicts lowest-hit
        c4 = make(now)
        survivors = ["%012x" % i for i in range(5)]
        for _ in range(10):
            c4.record(survivors)                           # survivors reach hits=10
        flood = ["%012x" % i for i in range(1000, 1000 + 600)]
        c4.record(flood)                                   # hits=1 each
        keys_now = {e["key"] for e in c4.entries}
        check("learned: quota caps the store at MAX_ENTRIES",
              len(c4.entries) == LearnedKeyCache.MAX_ENTRIES, str(len(c4.entries)))
        check("learned: eviction keeps the high-hit survivors",
              all(s in keys_now for s in survivors), "missing survivors")

        # persistence: a fresh cache on the same path reloads identical entries
        p = newpath()
        c5 = LearnedKeyCache(path=p, now=now)
        c5.record(["abcabcabcabc", "abcabcabcabd"], uid="0a1b2c3d", site="hcmc")
        c6 = LearnedKeyCache(path=p, now=now)
        check("learned: entries survive save/load unchanged",
              c6.entries == c5.entries, str(c6.entries))

        # robustness: a file of garbage bytes loads as an empty store, no raise
        pg = newpath()
        with open(pg, "wb") as f:
            f.write(b"\x00\x01\xff not json at all {{{")
        cg = LearnedKeyCache(path=pg, now=now)
        check("learned: corrupt file loads as empty (no exception)", cg.entries == [], str(cg.entries))

        # invalid keys (wrong length / uppercase / non-hex) are skipped
        c7 = make(now)
        n7 = c7.record(["gggggggggggg", "AAAAAAAAAAAA", "abc",
                        "abcabcabcabcab", "aabbccddeeff"])
        check("learned: record skips invalid keys, counts only valid ones", n7 == 1, str(n7))
        check("learned: only the valid key is stored",
              len(c7.entries) == 1 and c7.entries[0]["key"] == "aabbccddeeff", str(c7.entries))

        # limit <= 0 returns nothing (contract: at most `limit`, not one)
        c8 = make(now)
        c8.record(["112233445566", "223344556677"])
        check("learned: limit<=0 returns no keys",
              c8.top_keys(limit=0) == [] and c8.top_keys(limit=-3) == [],
              str(c8.top_keys(limit=0)))

        # a hand-edited file with duplicate keys is coalesced on load
        pdup = newpath()
        with open(pdup, "w") as f:
            json.dump([{"key": "eeeeeeeeeeee", "hits": 3, "last_used": 5.0,
                        "first_seen": 4.0, "uids": ["aa"], "site": None},
                       {"key": "eeeeeeeeeeee", "hits": 2, "last_used": 9.0,
                        "first_seen": 1.0, "uids": ["bb"], "site": "x"}], f)
        cdup = LearnedKeyCache(path=pdup, now=now)
        ed = cdup._find("eeeeeeeeeeee")
        check("learned: duplicate keys coalesce to one entry on load",
              len(cdup.entries) == 1, str(cdup.entries))
        check("learned: coalesced entry sums hits and merges metadata",
              ed and ed["hits"] == 5 and ed["first_seen"] == 1.0
              and set(ed["uids"]) == {"aa", "bb"} and ed["site"] == "x", str(ed))
    finally:
        for p in paths:
            try:
                os.remove(p)
            except OSError:
                pass


def test_daemon_learned_cache():
    # End-to-end through the daemon: a learned key is tried ahead of the built-in
    # dictionary, and a decode records the keys it verified on the card.
    K = "0f1e2d3c4b5a"   # a key deliberately outside the shipped dictionary
    check("learned-daemon: test key is not a built-in (test premise)",
          K not in set(x7lib.BUILTIN_KEYS), "pick another test key")
    fd, cpath = tempfile.mkstemp(prefix="lk-daemon-", suffix=".json")
    os.close(fd)
    try:
        # cold: the key is not learned, so its sector cannot be reached at all.
        cold = learned_keys.LearnedKeyCache(path=cpath)
        d0 = daemon_with(FakeCard(keymap={0: ("A", K)}, uid=b"\x0a\x0b\x0c\x0d"),
                         learned=cold)
        r0 = d0.decode({"user_keys": [], "max_seconds": 5})
        check("learned-daemon: an unlearned key leaves its sector unresolved",
              r0["keys"].get("0") is None, str(r0["keys"].get("0")))

        # warm: learn the key, and the same card now resolves the sector.
        warm = learned_keys.LearnedKeyCache(path=cpath)
        warm.record([K], uid="0a0b0c0d")
        d1 = daemon_with(FakeCard(keymap={0: ("A", K)}, uid=b"\x0a\x0b\x0c\x0d"),
                         learned=warm)
        r1 = d1.decode({"user_keys": [], "max_seconds": 5})
        check("learned-daemon: a learned key resolves its sector",
              r1["keys"].get("0") == ["A", K], str(r1["keys"].get("0")))
        e = warm._find(K)
        check("learned-daemon: decode records verified keys back into the cache",
              e is not None and e["hits"] == 2, str(e))
    finally:
        try:
            os.remove(cpath)
        except OSError:
            pass


if __name__ == "__main__":
    test_find_key_sweep()
    test_dump_reuse_and_budget()
    test_dump_exhausts_deep_key()
    test_dump_hard_sector_no_starve()
    test_daemon_keys()
    test_daemon_decode()
    test_daemon_write()
    test_write_safety()
    test_dump_swap_safety()
    test_dump_keyb_read_fallback()
    test_dump_trailer_mirror()
    test_access_bits_valid()
    test_card_kind()
    test_daemon_format()
    test_daemon_ntag_apdu()
    test_read_ntag_wrap()
    test_daemon_dispatch()
    test_apdu_parse()
    test_learned_keys()
    test_daemon_learned_cache()
    test_subprocess_selftests()
    print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
    if FAIL:
        print("FAILED:", ", ".join(FAIL))
        sys.exit(1)
    print("ALL TESTS PASSED")
