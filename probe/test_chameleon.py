#!/usr/bin/env python3
"""Hardware-free tests for chameleon_d - the Chameleon Ultra daemon contract.

A FakeChameleon stands in for the ChameleonCMD command layer (injected so a real
serial port is never opened), exercising the REAL chameleon_d dispatch: info +
capability manifest, slot parsing, poll present/absent, single-block read, and the
dict-only decode (check-keys on-device + per-sector block reads + progress events).

The fake models real firmware quirks so the tests actually exercise the daemon's
fixes rather than pre-normalized data: it starts in TAG mode and rejects card ops
with DEVICE_MODE_ERROR until reader mode is set, returns ATQA in WIRE (LSB-first)
order, and returns trailer reads with zeroed key slots. The vendored transport's
close-safe wait is tested directly against ChameleonCom.

Registered by test_all.py (its TESTS list is run there); also runnable standalone:

    python3 test_chameleon.py
"""
import os
import sys
import tempfile
import threading
import time

# Keep the learned-key cache off the real App Support store (mirrors test_all).
os.environ.setdefault("X7_LEARNED_PATH",
                      os.path.join(tempfile.gettempdir(), "rekey-test-learned.json"))

import chameleon_d
from chameleon.chameleon_com import ChameleonCom
from chameleon.chameleon_enum import Status, SlotNumber, TagSpecificType
from chameleon.chameleon_utils import UnexpectedResponseError
from learned_keys import LearnedKeyCache

FF = "ffffffffffff"
ACCESS = "ff078069"                       # factory access bytes + GPB


# --------------------------------------------------------------------------
# FakeChameleon: the ChameleonCMD surface the daemon touches, in memory.
# Methods return the SAME parsed values the real decorated methods return
# (or raise UnexpectedResponseError, as the real @expect_response does).
# `keymap`: (sector, "A"|"B") -> 12-hex key the fake card accepts.
# ATQA is WIRE order (LSB-first), e.g. b"\x04\x00" for semantic 00 04.
# --------------------------------------------------------------------------
class FakeChameleon:
    def __init__(self, model=0, app=(1, 0), git="0abcdef", chip="aabbccddeeff0011",
                 uid=b"\xaa\xbb\xcc\xdd", atqa=b"\x04\x00", sak=b"\x08", ats=b"",
                 present=True, keymap=None, blockdata=None,
                 slot_info=None, enabled=None, nicks=None, active=0,
                 reader_mode=False):
        self.model, self.app, self.git, self.chip = model, app, git, chip
        self.uid, self.atqa, self.sak, self.ats = uid, atqa, sak, ats
        self.present = present
        # default: an all-FF MIFARE Classic 1K (both keys FF on every sector)
        self.keymap = keymap if keymap is not None else {
            (s, kt): FF for s in range(16) for kt in ("A", "B")}
        self.blockdata = blockdata or {}
        self.slot_info = slot_info or [{"hf": 0, "lf": 0} for _ in range(8)]
        self.enabled = enabled or [{"hf": 0, "lf": 0} for _ in range(8)]
        self.nicks = nicks or [{"hf": "", "lf": ""} for _ in range(8)]
        self.active = active
        self.reader_mode = reader_mode    # starts in TAG mode (the risky default)
        self.mode_sets = []               # every set_device_reader_mode arg
        self.reads = []                   # (block, keytype, keyhex) in order

    # firmware rejects a card op in tag/emulator mode (Status.DEVICE_MODE_ERROR)
    def _require_reader(self):
        if not self.reader_mode:
            raise UnexpectedResponseError("API request fail, device mode error")

    # ---- info ----
    def get_device_model(self): return self.model
    def get_app_version(self): return self.app
    def get_git_version(self): return self.git
    def get_device_chip_id(self): return self.chip

    # ---- reader / tag mode ----
    def get_device_mode(self): return self.reader_mode
    def is_device_reader_mode(self): return self.reader_mode

    def set_device_reader_mode(self, reader_mode=True):
        self.mode_sets.append(reader_mode)
        if self.model != 0:               # Lite has no reader front-end
            raise UnexpectedResponseError("Some api not implemented")
        self.reader_mode = reader_mode

    # ---- poll / scan ----
    def hf14a_scan(self):
        self._require_reader()
        if not self.present:
            raise UnexpectedResponseError("HF tag no found or lost")
        return [{"uid": self.uid, "atqa": self.atqa, "sak": self.sak, "ats": self.ats}]

    # ---- slots ----
    def get_slot_info(self): return self.slot_info
    def get_enabled_slots(self): return self.enabled
    def get_all_slot_nicks(self): return self.nicks
    def get_active_slot(self): return self.active

    def set_active_slot(self, slot_number):
        self.active = SlotNumber(slot_number).value - 1

    # ---- read / check-keys ----
    def _sector_of(self, block):
        return block // 4 if block < 128 else 32 + (block - 128) // 16

    def _is_trailer(self, block):
        return (block % 4 == 3) if block < 128 else ((block - 128) % 16 == 15)

    def _block_bytes(self, block):
        if block in self.blockdata:
            return self.blockdata[block]
        if self._is_trailer(block):
            # a real trailer READ returns BOTH key slots as zero (Key A is never
            # exposed), access bytes intact - so the daemon's patch has work to do
            return bytes(6) + bytes.fromhex(ACCESS) + bytes(6)
        return bytes([block & 0xFF]) + b"\x00" * 15

    def mf1_read_one_block(self, block, type_value, key):
        self._require_reader()
        kt = "A" if int(type_value) == 0x60 else "B"
        self.reads.append((block, kt, key.hex()))
        want = self.keymap.get((self._sector_of(block), kt))
        if want is None or bytes.fromhex(want) != key:
            raise UnexpectedResponseError("HF tag auth fail")
        return self._block_bytes(block)

    def mf1_check_keys_of_sectors(self, mask, keys):
        self._require_reader()
        assert len(mask) == 10, "mask must be 10 bytes"
        assert 1 <= len(keys) <= 83, "keys must be 1..83"
        keyset = set(keys)
        bits = 80                          # sector-keys still to check (0 = all masked)
        for b in mask:
            while b > 0:
                bits -= (b & 1)
                b >>= 1
        if bits < 1:
            return {"status": Status.HF_TAG_OK}
        found = bytearray(10)
        sector_keys = {}
        for k in range(80):                # index k: sector k//2, KeyA if k even
            byte_i, bit = k // 8, 7 - (k % 8)
            if (mask[byte_i] >> bit) & 1:
                continue                   # masked
            want = self.keymap.get((k // 2, "A" if k % 2 == 0 else "B"))
            if want is not None and bytes.fromhex(want) in keyset:
                found[byte_i] |= (1 << bit)
                sector_keys[k] = bytes.fromhex(want)
        return {"status": Status.HF_TAG_OK, "found": bytes(found),
                "sectorKeys": sector_keys}


class SwapFake(FakeChameleon):
    """A card that is swapped mid-decode: after `swap_after` scans it reports a
    different uid, so the daemon's card-identity guard must abort."""
    def __init__(self, swap_after=2, **kw):
        super().__init__(**kw)
        self.scan_count = 0
        self.swap_after = swap_after

    def hf14a_scan(self):
        self._require_reader()
        self.scan_count += 1
        if self.scan_count > self.swap_after:
            self.uid = b"\x09\x09\x09\x09"
        if not self.present:
            raise UnexpectedResponseError("HF tag no found or lost")
        return [{"uid": self.uid, "atqa": self.atqa, "sak": self.sak, "ats": self.ats}]


def cham_daemon(fake, learned=None):
    d = chameleon_d.Daemon(learned=learned)
    d.cmd = fake
    d.com = object()
    d._connect = lambda port=None: fake
    return d


def _fresh_learned():
    fd, p = tempfile.mkstemp(prefix="cham-lk-", suffix=".json")
    os.close(fd)
    return LearnedKeyCache(path=p), p


# --------------------------------------------------------------------------
# 1. info: the capability manifest shape (SPEC 2.3), model-driven.
# --------------------------------------------------------------------------
def test_cham_info(check):
    d = cham_daemon(FakeChameleon(model=0, chip="aabbccddeeff0011", app=(1, 2)))
    r = d.info({})
    caps = r.get("capabilities", {})
    check("info reports the chameleon-ultra family",
          r["family"] == "chameleon-ultra" and r["model"] == "Chameleon Ultra", str(r))
    check("info surfaces the chip id as serial (no real card uid)",
          r["serial"] == "aabbccddeeff0011")
    check("info hw carries app version + git", "app 1.2" in r["hw"] and "0abcdef" in r["hw"], r["hw"])
    check("capabilities manifest has the SPEC 2.3 shape",
          caps.get("slots") == 8 and caps.get("emulate") is True and caps.get("lf") is True
          and caps.get("dfu") is True and caps.get("sniff") is True, str(caps))
    check("capabilities lists the attack + writeMode surface",
          caps.get("attacks") == ["dict", "nested", "staticNested", "darkside", "hardnested"]
          and caps.get("writeModes") == ["normal", "denied", "deceive", "shadow", "shadowReq"],
          str(caps))
    # Lite: same 8 slots, but no reader-mode attacks / sniff (data-driven off model)
    dl = cham_daemon(FakeChameleon(model=1))
    rl = dl.info({})
    check("Lite reports chameleon-lite with 8 slots but no reader attacks",
          rl["family"] == "chameleon-lite" and rl["capabilities"]["slots"] == 8
          and rl["capabilities"]["attacks"] == [] and rl["capabilities"]["sniff"] is False,
          str(rl["capabilities"]))


# --------------------------------------------------------------------------
# 2. slots_list: parse 8 slots (type / enabled / nick / active).
# --------------------------------------------------------------------------
def test_cham_slots(check):
    slot_info = [{"hf": 0, "lf": 0} for _ in range(8)]
    slot_info[0] = {"hf": int(TagSpecificType.MIFARE_1024), "lf": 0}       # 1001
    slot_info[3] = {"hf": 0, "lf": int(TagSpecificType.EM410X)}            # 100
    enabled = [{"hf": 0, "lf": 0} for _ in range(8)]
    enabled[0] = {"hf": 1, "lf": 0}
    enabled[3] = {"hf": 0, "lf": 1}
    nicks = [{"hf": "", "lf": ""} for _ in range(8)]
    nicks[0] = {"hf": "front door", "lf": ""}
    fake = FakeChameleon(slot_info=slot_info, enabled=enabled, nicks=nicks, active=2)
    d = cham_daemon(fake)
    slots = d.slots_list({})["slots"]
    check("slots_list returns exactly 8 slots", len(slots) == 8, str(len(slots)))
    check("slot indexes are 0..7 in order", [s["index"] for s in slots] == list(range(8)))
    check("the active slot is flagged (and only it)",
          slots[2]["active"] is True and sum(1 for s in slots if s["active"]) == 1)
    check("slot HF type/enabled/nick parsed",
          slots[0]["hf"]["type"] == "MIFARE_1024" and slots[0]["hf"]["enabled"] is True
          and slots[0]["hf"]["nick"] == "front door", str(slots[0]["hf"]))
    check("slot LF type/enabled parsed",
          slots[3]["lf"]["type"] == "EM410X" and slots[3]["lf"]["enabled"] is True,
          str(slots[3]["lf"]))
    check("an empty slot reads UNDEFINED / disabled",
          slots[5]["hf"]["type"] == "UNDEFINED" and slots[5]["hf"]["enabled"] is False)


# --------------------------------------------------------------------------
# 3. slot_select: 0-based boundary maps to SlotNumber (from_fw).
# --------------------------------------------------------------------------
def test_cham_slot_select(check):
    fake = FakeChameleon(active=0)
    d = cham_daemon(fake)
    r = d.slot_select({"slot": 4})
    check("slot_select activates the requested 0-based slot",
          r["slot"] == 4 and fake.active == 4, "active=%d" % fake.active)


# --------------------------------------------------------------------------
# 4. poll: ensures reader mode; semantic ATQA; present/absent; Lite; NTAG.
# --------------------------------------------------------------------------
def test_cham_poll(check):
    # present: fake starts in TAG mode -> poll must switch it to reader mode first,
    # then scan. ATQA b"\x04\x00" (wire) must be emitted as semantic "00 04".
    fake = FakeChameleon(present=True, uid=b"\xaa\xbb\xcc\xdd",
                         atqa=b"\x04\x00", sak=b"\x08", reader_mode=False)
    d = cham_daemon(fake)
    r = d.poll({})
    check("poll switches a tag-mode device into reader mode before scanning",
          fake.reader_mode is True and fake.mode_sets == [True], str(fake.mode_sets))
    check("poll present returns uid + SEMANTIC atqa + sak + kind",
          r["present"] is True and r["reader"] is True and r["uid"] == "aa bb cc dd"
          and r["atqa"] == "00 04" and r["sak"] == 0x08 and r["kind"] == "classic", str(r))

    # NTAG: scanned ATQA b"\x44\x00" (wire) -> semantic 00 44 -> classified ntag.
    dn = cham_daemon(FakeChameleon(present=True, uid=b"\x04\x11\x22\x33\x44\x55\x66",
                                   atqa=b"\x44\x00", sak=b"\x00"))
    rn = dn.poll({})
    check("poll classifies a semantic-0044 ATQA as ntag (not classic)",
          rn["atqa"] == "00 44" and rn["kind"] == "ntag", str(rn))

    # absent: reader up, no card
    ra = cham_daemon(FakeChameleon(present=False)).poll({})
    check("poll absent: reader up, no card",
          ra["present"] is False and ra["reader"] is True, str(ra))

    # Lite: no reader mode -> reader:false, distinguished from a phantom no-card
    rl = cham_daemon(FakeChameleon(model=1, present=True)).poll({})
    check("poll on a Lite (no reader mode) reports reader:false, not a false no-card",
          rl["present"] is False and rl["reader"] is False, str(rl))


# --------------------------------------------------------------------------
# 5. mf_read_block: a single authed block read (ensures reader mode).
# --------------------------------------------------------------------------
def test_cham_read_block(check):
    payload = "00112233445566778899aabbccddeeff"
    fake = FakeChameleon(blockdata={4: bytes.fromhex(payload)}, reader_mode=False)
    d = cham_daemon(fake)
    r = d.mf_read_block({"block": 4, "keytype": "A", "key": FF})
    check("mf_read_block ensures reader mode then returns block data as spaced hex",
          fake.reader_mode is True and r["block"] == 4
          and r["data"].replace(" ", "") == payload, str(r))
    # a wrong key surfaces as an error envelope, not a crash
    err = d.handle({"id": 9, "method": "mf_read_block",
                    "params": {"block": 4, "keytype": "A", "key": "000000000000"}})
    check("mf_read_block on a wrong key -> error envelope", "error" in err, str(err))


# --------------------------------------------------------------------------
# 6. decode: all-FF 1K -> 16/16, full blocks + keys, trailer patched, progress.
# --------------------------------------------------------------------------
def test_cham_decode(check):
    learned, path = _fresh_learned()
    fake = FakeChameleon(uid=b"\xaa\xbb\xcc\xdd", atqa=b"\x04\x00", reader_mode=False)
    d = cham_daemon(fake, learned=learned)
    emitted = []
    d.emit = lambda o: emitted.append(o)
    r = d.decode({})
    check("decode ensures reader mode before scanning", fake.reader_mode is True)
    check("decode reports 16 sectors and recovers all 16",
          r["sectors"] == 16 and r["recovered"] == 16, str((r["sectors"], r["recovered"])))
    check("decode returns a key for every sector (KeyA preferred)",
          len(r["keys"]) == 16 and r["keys"]["0"] == ["A", FF], str(r["keys"].get("0")))
    check("decode reads every block of a 1K (16 sectors x 4 = 64 blocks)",
          len(r["blocks"]) == 64 and all(v is not None for v in r["blocks"].values()),
          "%d blocks" % len(r["blocks"]))
    check("decode emits the x7d shape (uid + SEMANTIC atqa + int sak + attempts/exhausted)",
          r["uid"] == "aa bb cc dd" and r["atqa"] == "00 04" and r["sak"] == 0x08
          and isinstance(r["attempts"], int) and r["exhausted"] is False, str(r.get("atqa")))
    # trailer patch: the read returned 000000 key slots; decode fills the recovered
    # keys back (Key A in 0-5, Key B in 10-15), access bytes preserved.
    tr = r["blocks"]["3"].replace(" ", "")
    check("decode patches the recovered keys into the trailer (no false 000000)",
          tr == FF + ACCESS + FF, tr)
    prog = [e for e in emitted if e.get("method") == "decode" and "sector" in e]
    check("decode emits one progress event per sector (method 'decode')",
          len(prog) == 16 and prog[0]["total"] == 16, "%d events" % len(prog))
    check("decode progress carries the recovered key per sector",
          prog[0]["keytype"] == "A" and prog[0]["key"] == FF, str(prog[0]))
    os.remove(path)


# --------------------------------------------------------------------------
# 7. decode partial: an unrecovered sector is null in keys + null blocks.
# --------------------------------------------------------------------------
def test_cham_decode_partial(check):
    learned, path = _fresh_learned()
    km = {(s, kt): FF for s in range(16) for kt in ("A", "B")}
    del km[(7, "A")]
    del km[(7, "B")]                      # sector 7 unknown to the dictionary
    d = cham_daemon(FakeChameleon(keymap=km), learned=learned)
    d.emit = lambda o: None
    r = d.decode({})
    check("decode recovers 15/16 when one sector is unknown", r["recovered"] == 15, str(r["recovered"]))
    check("an unrecovered sector is null in keys (x7d shape), and marked exhausted",
          r["keys"]["7"] is None and r["exhausted"] is True, str((r["keys"].get("7"), r["exhausted"])))
    check("an unrecovered sector's blocks are null",
          all(r["blocks"][str(b)] is None for b in (28, 29, 30, 31)),
          str([r["blocks"].get(str(b)) for b in (28, 29, 30, 31)]))
    os.remove(path)


# --------------------------------------------------------------------------
# 8. decode no-card: raises (a clean error envelope), matching x7d/x7lib.
# --------------------------------------------------------------------------
def test_cham_decode_nocard(check):
    learned, path = _fresh_learned()
    d = cham_daemon(FakeChameleon(present=False), learned=learned)
    d.emit = lambda o: None
    r = d.handle({"id": 5, "method": "decode", "params": {}})
    check("decode with no card -> error envelope, not a DecodeResult",
          "error" in r and "no card" in r["error"] and "result" not in r, str(r))
    os.remove(path)


# --------------------------------------------------------------------------
# 9. decode card-swap guard: a mid-decode uid change aborts the read.
# --------------------------------------------------------------------------
def test_cham_decode_swap(check):
    learned, path = _fresh_learned()
    d = cham_daemon(SwapFake(swap_after=2, uid=b"\xaa\xbb\xcc\xdd"), learned=learned)
    d.emit = lambda o: None
    r = d.handle({"id": 6, "method": "decode", "params": {}})
    check("decode aborts on a mid-decode card swap (never mixes cards under one uid)",
          "error" in r and "card changed during decode" in r["error"], str(r))
    os.remove(path)


# --------------------------------------------------------------------------
# 10. decode honours a user key outside the builtin dictionary (and learns it).
# --------------------------------------------------------------------------
def test_cham_decode_user_key(check):
    K = "0f1e2d3c4b5a"                    # not in the shipped dictionary
    check("decode-user: test key is not a builtin (test premise)",
          K not in set(chameleon_d.BUILTIN_KEYS), "pick another test key")
    km = {(s, kt): K for s in range(16) for kt in ("A", "B")}

    cold, cpath = _fresh_learned()
    d0 = cham_daemon(FakeChameleon(keymap=km), learned=cold)
    d0.emit = lambda o: None
    r0 = d0.decode({})
    check("decode-user: an unknown key recovers nothing without user_keys",
          r0["recovered"] == 0, str(r0["recovered"]))

    warm, wpath = _fresh_learned()
    d1 = cham_daemon(FakeChameleon(keymap=km, uid=b"\x0a\x0b\x0c\x0d"), learned=warm)
    d1.emit = lambda o: None
    r1 = d1.decode({"user_keys": [K]})
    check("decode-user: a user key not in the dictionary recovers all sectors",
          r1["recovered"] == 16, str(r1["recovered"]))
    check("decode-user: verified keys are recorded into the shared learned cache",
          warm._find(K) is not None, "key not learned")
    os.remove(cpath)
    os.remove(wpath)


# --------------------------------------------------------------------------
# 11. transport (vendored ChameleonCom): send_cmd_sync is bounded + close-safe.
# --------------------------------------------------------------------------
def test_cham_transport_wedge(check):
    # (a) start-wait is bounded: send registers no wait entry -> must not hang.
    c = ChameleonCom()
    c.commands = []
    c.send_cmd_auto = lambda *a, **k: None
    t0 = time.time()
    raised = None
    try:
        c.send_cmd_sync(0x03e8, timeout=0)
    except TimeoutError as e:
        raised = e
    check("send_cmd_sync bounds the start-wait (no infinite spin)",
          raised is not None and (time.time() - t0) < 4, "elapsed=%.2f" % (time.time() - t0))

    # (b) response-wait is close-safe: a transport thread clears the wait map
    #     mid-wait (close()) -> must raise, not KeyError or hang.
    c2 = ChameleonCom()
    c2.commands = []
    c2.send_cmd_auto = lambda *a, **k: c2.wait_response_map.__setitem__(0x03e8, {"response": None})

    def _clear():
        time.sleep(0.1)
        c2.wait_response_map.clear()

    threading.Thread(target=_clear).start()
    raised2 = None
    try:
        c2.send_cmd_sync(0x03e8, timeout=5)
    except TimeoutError as e:
        raised2 = e
    check("send_cmd_sync raises (not KeyError/hang) when close() clears the map mid-wait",
          raised2 is not None, repr(raised2))


# --------------------------------------------------------------------------
# 12. dispatch robustness (unknown method, missing param, no hardware).
# --------------------------------------------------------------------------
def test_cham_dispatch(check):
    d = cham_daemon(FakeChameleon())
    check("unknown method -> error envelope",
          "error" in d.handle({"id": 1, "method": "frobnicate"}))
    r = d.handle({"id": 2, "method": "slot_select", "params": {}})
    check("missing param -> error envelope, no crash", "error" in r, str(r))
    # no-hardware daemon: info is dispatchable and errors cleanly (never crashes)
    bare = chameleon_d.Daemon(learned=None)
    rb = bare.handle({"id": 3, "method": "info"})
    check("no-hardware info -> clean error envelope",
          "error" in rb and "Chameleon" in rb["error"], str(rb))


TESTS = [test_cham_info, test_cham_slots, test_cham_slot_select, test_cham_poll,
         test_cham_read_block, test_cham_decode, test_cham_decode_partial,
         test_cham_decode_nocard, test_cham_decode_swap, test_cham_decode_user_key,
         test_cham_transport_wedge, test_cham_dispatch]


if __name__ == "__main__":
    PASS, FAIL = [], []

    def check(name, cond, detail=""):
        (PASS if cond else FAIL).append(name)
        print(("[ok] " if cond else "[XX] ") + name + (("  -> " + detail) if detail and not cond else ""))

    for t in TESTS:
        t(check)
    print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
    if FAIL:
        print("FAILED:", ", ".join(FAIL))
        sys.exit(1)
    print("ALL CHAMELEON TESTS PASSED")
