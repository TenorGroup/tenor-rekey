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
from chameleon.chameleon_enum import (Status, SlotNumber, TagSpecificType, TagSenseType, MfcKeyType,
                                      MifareClassicPrngType, MifareClassicDarksideStatus)
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
                 reader_mode=False, prng=None):
        self.model, self.app, self.git, self.chip = model, app, git, chip
        self.uid, self.atqa, self.sak, self.ats = uid, atqa, sak, ats
        self.present = present
        # prng None = no reader-mode attack surface (mf1_detect_prng raises, so the
        # daemon's attack stage skips cleanly, as on a card with no detectable
        # vulnerability). Set 0/1/2 (STATIC/WEAK/HARD) to enable the acquire methods.
        self.prng = prng
        self.acquired = []                # names of acquire/detect calls, in order
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
        # ---- slot config / emulator / magic surface (P3) ----
        self.type_sets = []               # (slot0, TagSpecificType) from set_slot_tag_type
        self.default_sets = []            # (slot0, TagSpecificType) from set_slot_data_default
        self.enable_sets = []             # (slot0, TagSenseType, bool) from set_slot_enable
        self.nick_store = {}              # (slot0, TagSenseType) -> nick
        self.saved = 0                    # slot_data_config_save call count
        self.emu = {}                     # block -> 16 bytes (HF emulator memory)
        self.emu_writes = []              # (start_block, block_count) in order
        self.written = {}                 # block -> 16 bytes (physical card write target)
        self.writes = []                  # (block, keytype, keyhex) in order
        self.magic = {"gen1a": None, "gen2": None, "block_anti_coll": None}

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

    # ---- attack surface (mirrors the firmware acquire methods' parsed shapes) ----
    def _uid_int(self):
        return int.from_bytes(self.uid[-4:], "big")

    def mf1_detect_prng(self):
        self._require_reader()
        if self.prng is None:                 # no detectable vulnerability
            raise UnexpectedResponseError("prng detect: no vuln")
        return int(self.prng)

    def mf1_detect_nt_dist(self, block_known, type_known, key_known):
        self._require_reader()
        self.acquired.append("detect_nt_dist")
        return {"uid": self._uid_int(), "dist": 100}

    def mf1_nested_acquire(self, block_known, type_known, key_known, block_target, type_target):
        self._require_reader()
        self.acquired.append("nested_acquire")
        # Opaque placeholder nonces - the injected fake cracker is what turns these
        # into a key here; the real cracker is proven separately in chameleon_crack.
        return [{"nt": 1, "nt_enc": 2, "par": 0} for _ in range(4)]

    def mf1_static_nested_acquire(self, block_known, type_known, key_known, block_target, type_target):
        self._require_reader()
        self.acquired.append("static_nested_acquire")
        return {"uid": self._uid_int(), "nts": [{"nt": 0x01200145, "nt_enc": 2}]}

    def mf1_darkside_acquire(self, block_target, type_target, first_recover, sync_max):
        self._require_reader()
        self.acquired.append("darkside_acquire")
        return (MifareClassicDarksideStatus.OK,
                {"uid": self._uid_int(), "nt1": 1, "par": 0, "ks1": 0, "nr": 2, "ar": 3})

    def mf1_auth_one_key_block(self, block, type_value, key):
        self._require_reader()
        kt = "A" if int(type_value) == 0x60 else "B"
        want = self.keymap.get((self._sector_of(block), kt))
        return want is not None and bytes.fromhex(want) == key

    # ---- slot config (the daemon passes SlotNumber / TagSenseType values) ----
    @staticmethod
    def _slot0(slot_number):
        return int(slot_number) - 1               # SlotNumber (1..8) -> 0-based

    def set_slot_tag_type(self, slot_number, tag_type):
        self.type_sets.append((self._slot0(slot_number), tag_type))

    def set_slot_data_default(self, slot_number, tag_type):
        self.default_sets.append((self._slot0(slot_number), tag_type))

    def set_slot_enable(self, slot_number, sense_type, enabled):
        self.enable_sets.append((self._slot0(slot_number), sense_type, bool(enabled)))

    def set_slot_tag_nick(self, slot_number, sense_type, name):
        self.nick_store[(self._slot0(slot_number), sense_type)] = name

    def get_slot_tag_nick(self, slot_number, sense_type):
        key = (self._slot0(slot_number), sense_type)
        if key not in self.nick_store:            # firmware errors on an unset nick
            raise UnexpectedResponseError("no nick")
        return self.nick_store[key]

    def slot_data_config_save(self):
        self.saved += 1

    # ---- emulator memory ----
    def mf1_write_emu_block_data(self, block_start, block_data):
        n = len(block_data) // 16
        self.emu_writes.append((block_start, n))
        for i in range(n):
            self.emu[block_start + i] = bytes(block_data[i * 16:(i + 1) * 16])

    def mf1_read_emu_block_data(self, block_start, block_count):
        out = bytearray()
        for i in range(block_count):
            out += self.emu.get(block_start + i, bytes(16))
        return bytes(out)

    def mf1_set_block_anti_coll_mode(self, enabled):
        self.magic["block_anti_coll"] = bool(enabled)

    def mf1_set_gen1a_mode(self, enabled):
        self.magic["gen1a"] = bool(enabled)

    def mf1_set_gen2_mode(self, enabled):
        self.magic["gen2"] = bool(enabled)

    # ---- physical-card block write (magic clone target) ----
    def mf1_write_one_block(self, block, type_value, key, block_data):
        self._require_reader()
        kt = "A" if int(type_value) == 0x60 else "B"
        self.writes.append((block, kt, key.hex()))
        want = self.keymap.get((self._sector_of(block), kt))
        if want is None or bytes.fromhex(want) != key:
            raise UnexpectedResponseError("HF tag auth fail")
        self.written[block] = bytes(block_data)
        return True


class FakeCrack:
    """Stand-in for chameleon_crack: returns a canned candidate key so the decode
    chaining (acquire -> crack -> verify -> feed back) is testable without the built
    C binaries. The real crackers are proven by chameleon_crack's forward-sim."""
    def __init__(self, key):
        self.key = key
        self.calls = []

    def nested(self, uid, dist, samples):
        self.calls.append("nested")
        return [self.key]

    def staticnested(self, uid, type_target, pairs):
        self.calls.append("staticnested")
        return [self.key]

    def darkside(self, uid, items):
        self.calls.append("darkside")
        return [self.key], True


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


class MidWriteSwapFake(FakeChameleon):
    """A magic-clone target that is swapped AFTER the first physical block write: its uid
    flips once a block lands, so the per-write uid pin must catch it before the next block
    and abort. Scans/reads (preflight) do not flip it, so preflight completes normally."""
    def mf1_write_one_block(self, block, type_value, key, block_data):
        ok = super().mf1_write_one_block(block, type_value, key, block_data)
        self.uid = b"\x09\x09\x09\x09"
        return ok


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
          caps.get("attacks") == ["dict", "nested", "staticNested", "darkside"]
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


# --------------------------------------------------------------------------
# 13. decode nested chain: 1 dict key + weak PRNG -> nested opens the rest.
# --------------------------------------------------------------------------
def test_cham_decode_nested_chain(check):
    K = "0f1e2d3c4b5a"                    # not in the shipped dictionary
    km = {(s, kt): K for s in range(16) for kt in ("A", "B")}
    km[(0, "A")] = FF                     # sector 0 opens on the dict; 1..15 need nested
    km[(0, "B")] = FF
    learned, path = _fresh_learned()
    fake = FakeChameleon(keymap=km, prng=MifareClassicPrngType.WEAK)
    d = cham_daemon(fake, learned=learned)
    d.crack = FakeCrack(K)
    d.emit = lambda o: None
    r = d.decode({})
    check("nested-chain: one dict key + weak PRNG recovers all 16 sectors",
          r["recovered"] == 16, str(r["recovered"]))
    check("nested-chain: routes through the nested acquire path (detect_nt_dist + nested_acquire), not darkside",
          "detect_nt_dist" in fake.acquired and "nested_acquire" in fake.acquired
          and "darkside_acquire" not in fake.acquired, str(fake.acquired))
    check("nested-chain: the host cracker was invoked for nested",
          "nested" in d.crack.calls, str(d.crack.calls))
    check("nested-chain: one recovered key is fed back and opens the rest in a single acquire",
          fake.acquired.count("nested_acquire") == 1 and r["keys"]["9"] is not None,
          str(fake.acquired))
    os.remove(path)


# --------------------------------------------------------------------------
# 14. decode darkside chain: a zero-key card routes to darkside for a foothold.
# --------------------------------------------------------------------------
def test_cham_decode_darkside_chain(check):
    K = "0f1e2d3c4b5a"
    km = {(s, kt): K for s in range(16) for kt in ("A", "B")}   # nothing in the dict
    learned, path = _fresh_learned()
    fake = FakeChameleon(keymap=km, prng=MifareClassicPrngType.WEAK)
    d = cham_daemon(fake, learned=learned)
    d.crack = FakeCrack(K)
    d.emit = lambda o: None
    r = d.decode({})
    check("darkside-chain: a zero-key card routes to darkside_acquire for a foothold",
          "darkside_acquire" in fake.acquired, str(fake.acquired))
    check("darkside-chain: the darkside cracker was invoked", "darkside" in d.crack.calls,
          str(d.crack.calls))
    check("darkside-chain: the foothold key is fed back and opens all 16 sectors",
          r["recovered"] == 16, str(r["recovered"]))
    os.remove(path)


# --------------------------------------------------------------------------
# 15. decode static chain: a static-PRNG card routes to static-nested.
# --------------------------------------------------------------------------
def test_cham_decode_static_chain(check):
    K = "0f1e2d3c4b5a"
    km = {(s, kt): K for s in range(16) for kt in ("A", "B")}
    km[(0, "A")] = FF
    km[(0, "B")] = FF
    learned, path = _fresh_learned()
    fake = FakeChameleon(keymap=km, prng=MifareClassicPrngType.STATIC)
    d = cham_daemon(fake, learned=learned)
    d.crack = FakeCrack(K)
    d.emit = lambda o: None
    r = d.decode({})
    check("static-chain: a static-PRNG card routes to static_nested_acquire (not weak nested)",
          "static_nested_acquire" in fake.acquired and "nested_acquire" not in fake.acquired,
          str(fake.acquired))
    check("static-chain: static-nested recovers the remaining sectors", r["recovered"] == 16,
          str(r["recovered"]))
    os.remove(path)


# --------------------------------------------------------------------------
# 16. decode hard PRNG: hardnested is not wired in P1 -> hard sectors stay unopened.
# --------------------------------------------------------------------------
def test_cham_decode_hard_prng(check):
    K = "0f1e2d3c4b5a"
    km = {(s, kt): K for s in range(16) for kt in ("A", "B")}
    km[(0, "A")] = FF
    km[(0, "B")] = FF
    learned, path = _fresh_learned()
    fake = FakeChameleon(keymap=km, prng=MifareClassicPrngType.HARD)
    d = cham_daemon(fake, learned=learned)
    d.crack = FakeCrack(K)
    events = []
    d.emit = lambda o: events.append(o)
    r = d.decode({})
    check("hard-PRNG: hardnested is out of scope for P1, so hard sectors stay unrecovered",
          r["recovered"] == 1 and r["exhausted"] is True, str(r["recovered"]))
    check("hard-PRNG: decode reports the hardnested stage as unsupported",
          any(e.get("stage") == "hardnested" and e.get("supported") is False for e in events),
          "no unsupported-hardnested progress event")
    os.remove(path)


# --------------------------------------------------------------------------
# 17. decode cancel: the cooperative abort stops the attack stage, returns partial.
# --------------------------------------------------------------------------
def test_cham_decode_cancel(check):
    K = "0f1e2d3c4b5a"
    km = {(s, kt): K for s in range(16) for kt in ("A", "B")}   # zero dict keys
    learned, path = _fresh_learned()
    fake = FakeChameleon(keymap=km, prng=MifareClassicPrngType.WEAK)
    d = cham_daemon(fake, learned=learned)
    d.crack = FakeCrack(K)
    d.emit = lambda o: None
    check("cancel() trips the cooperative abort flag",
          d.cancel({}) == {"cancelled": True} and d._cancel.is_set())
    r = d.decode({})
    check("a pre-cancelled decode aborts the attack stage (no acquire) and reports cancelled",
          r["cancelled"] is True and r["recovered"] == 0 and fake.acquired == [],
          str((r["cancelled"], r["recovered"], fake.acquired)))
    os.remove(path)


# --------------------------------------------------------------------------
# 18. attack budget guard: an already-expired wall-clock budget stops the stage.
# --------------------------------------------------------------------------
def test_cham_attack_budget_guard(check):
    K = "0f1e2d3c4b5a"
    km = {(s, kt): K for s in range(16) for kt in ("A", "B")}
    # reader_mode=True: decode() sets this via _ensure_reader before the attack stage;
    # this unit test drives _recover_attacks directly, so set it up front.
    fake = FakeChameleon(keymap=km, prng=MifareClassicPrngType.WEAK, reader_mode=True)
    d = cham_daemon(fake)
    d.crack = FakeCrack(K)
    d.emit = lambda o: None
    sk, pool = {}, set()
    attempts, cancelled = d._recover_attacks(
        fake, 16, sk, pool, [], time.monotonic() - 1, 0)
    check("an already-expired budget stops the attack stage before any acquire",
          cancelled is True and fake.acquired == [] and sk == {},
          str((cancelled, fake.acquired)))


def _dump_1k():
    """A deterministic full 1K image: {block-index: 32-hex}, block b = b || 01..0f."""
    return {str(b): (bytes([b]) + bytes(range(1, 16))).hex() for b in range(64)}


# --------------------------------------------------------------------------
# 19. slot config: set type (+ default), enable/disable, nick set/get, save.
# --------------------------------------------------------------------------
def test_cham_slot_config(check):
    fake = FakeChameleon()
    d = cham_daemon(fake)
    r = d.slot_set_type({"slot": 2, "type": "MIFARE_1024"})
    check("slot_set_type sets tag type AND seeds default data on the 0-based slot",
          r["type"] == "MIFARE_1024"
          and fake.type_sets == [(2, TagSpecificType.MIFARE_1024)]
          and fake.default_sets == [(2, TagSpecificType.MIFARE_1024)], str(fake.type_sets))
    # type may also be given as the raw enum value
    d.slot_set_type({"slot": 0, "type": int(TagSpecificType.EM410X)})
    check("slot_set_type accepts a numeric tag type",
          fake.type_sets[-1] == (0, TagSpecificType.EM410X), str(fake.type_sets[-1]))
    re = d.slot_enable({"slot": 3, "sense": "lf", "enabled": True})
    check("slot_enable toggles the requested field (sense-typed)",
          re["enabled"] is True
          and fake.enable_sets == [(3, TagSenseType.LF, True)], str(fake.enable_sets))
    rn = d.slot_nick({"slot": 1, "sense": "hf", "name": "front door"})
    check("slot_nick set writes the nickname", rn["nick"] == "front door")
    rg = d.slot_nick({"slot": 1, "sense": "hf"})
    check("slot_nick get reads it back", rg["nick"] == "front door", str(rg))
    rge = d.slot_nick({"slot": 5, "sense": "hf"})
    check("slot_nick get on an unset slot -> '' (not an error)", rge["nick"] == "", str(rge))
    rs = d.slot_save({})
    check("slot_save persists config to flash", rs["saved"] is True and fake.saved == 1)


# --------------------------------------------------------------------------
# 20. emulate_mode: reader<->tag toggle, and the cached reader flag.
# --------------------------------------------------------------------------
def test_cham_emulate_mode(check):
    fake = FakeChameleon(reader_mode=True)
    d = cham_daemon(fake)
    r = d.emulate_mode({"reader": False})
    check("emulate_mode false switches the device into tag/emulate mode",
          r["reader"] is False and fake.reader_mode is False
          and fake.mode_sets[-1] is False, str(fake.mode_sets))
    check("emulate_mode false clears the cached reader flag so a later reader op re-arms",
          d._reader_mode is False)
    r2 = d.emulate_mode({"reader": True})
    check("emulate_mode true returns to reader mode",
          r2["reader"] is True and fake.reader_mode is True and d._reader_mode is True)


# --------------------------------------------------------------------------
# 21. emulate_load: chunked emu write over contiguous runs + block-0 anti-coll.
# --------------------------------------------------------------------------
def test_cham_emulate_load(check):
    fake = FakeChameleon()
    d = cham_daemon(fake)
    r = d.emulate_load({"blocks": _dump_1k()})
    check("emulate_load reports the full 1K written (64 blocks) with block 0 present",
          r["blocks"] == 64 and r["block0"] is True, str(r))
    check("emulate_load turns on block-0 anti-collision (emulated card presents dump uid)",
          fake.magic["block_anti_coll"] is True)
    check("emulate_load chunks a contiguous run into <=8-block writes (64 -> 8 chunks)",
          len(fake.emu_writes) == 8 and all(n <= 8 for _, n in fake.emu_writes),
          str(fake.emu_writes))
    check("emulate_load block 0 in the emulator matches the dump block 0",
          fake.emu[0] == bytes([0]) + bytes(range(1, 16)), fake.emu.get(0, b"").hex())
    # a SPARSE dump (a hole at sector 7) loads as separate runs, never a fake zero block
    sparse = {k: v for k, v in _dump_1k().items() if int(k) not in (28, 29, 30, 31)}
    f2 = FakeChameleon()
    d2 = cham_daemon(f2)
    r2 = d2.emulate_load({"blocks": sparse})
    check("emulate_load skips a hole (sparse dump) - block 28 never written",
          r2["blocks"] == 60 and 28 not in f2.emu, str(r2))
    # block 0 ABSENT: the emulator must NOT derive its identity from a missing block 0
    noB0 = {k: v for k, v in _dump_1k().items() if int(k) != 0}
    f3 = FakeChameleon()
    d3 = cham_daemon(f3)
    r3 = d3.emulate_load({"blocks": noB0})
    check("emulate_load reports block 0 absent and does not fabricate it",
          r3["block0"] is False and r3["blocks"] == 63 and 0 not in f3.emu, str(r3))
    check("emulate_load leaves block-0 anti-collision OFF when block 0 is absent",
          f3.magic["block_anti_coll"] is None, str(f3.magic["block_anti_coll"]))


# --------------------------------------------------------------------------
# 22. emu_read: read the active slot's emulator memory back as a block map.
# --------------------------------------------------------------------------
def test_cham_emu_read(check):
    fake = FakeChameleon()
    d = cham_daemon(fake)
    d.emulate_load({"blocks": _dump_1k()})
    r = d.emu_read({"count": 64})
    check("emu_read returns one hex entry per block for the requested count",
          r["count"] == 64 and len(r["blocks"]) == 64, str(r["count"]))
    check("emu_read round-trips the loaded dump byte-identical (block 5)",
          r["blocks"]["5"].replace(" ", "") == (bytes([5]) + bytes(range(1, 16))).hex(),
          r["blocks"].get("5"))


# --------------------------------------------------------------------------
# 23. magic_write: clone a dump onto a blank magic card; trailer + block-0 gating.
# --------------------------------------------------------------------------
def test_cham_magic_write(check):
    # default fake = a magic card with FF keys everywhere -> the FF builtin authenticates
    fake = FakeChameleon(uid=b"\xaa\xbb\xcc\xdd", reader_mode=False)
    d = cham_daemon(fake)
    emitted = []
    d.emit = lambda o: emitted.append(o)
    r = d.magic_write({"blocks": _dump_1k(), "trailers": True, "uid": True,
                       "target_uid": "aa bb cc dd"})
    check("magic_write ensures reader mode before cloning", fake.reader_mode is True)
    check("magic_write reports the card present and writes every block of a 1K (uid+trailers on)",
          r["present"] is True and r["wrote"] == 64 and r["failed"] == [], str(r))
    check("magic_write writes block 0 (uid) when uid is enabled", 0 in fake.written)
    check("magic_write resets a trailer's access bytes to ff0780 (keeps the tag writable)",
          fake.written[3][6:9] == bytes.fromhex("ff0780"), fake.written.get(3, b"").hex())
    prog = [e for e in emitted if e.get("method") == "magic_write"]
    check("magic_write streams one progress event per written block",
          len(prog) == 64 and all(e["ok"] for e in prog), "%d events" % len(prog))

    # uid OFF: block 0 is left alone; trailers OFF: the trailer is not written
    f2 = FakeChameleon(uid=b"\xaa\xbb\xcc\xdd")
    d2 = cham_daemon(f2)
    d2.emit = lambda o: None
    r2 = d2.magic_write({"blocks": _dump_1k(), "trailers": False, "uid": False,
                         "target_uid": "aa bb cc dd"})
    # 64 blocks - 16 trailers - block 0 = 47 data blocks written
    check("magic_write skips block 0 when uid is off, and trailers when trailers is off",
          0 not in f2.written and 3 not in f2.written and r2["wrote"] == 47, str(r2["wrote"]))

    # target_uid is REQUIRED: omitting it is a contract violation, not a silent write
    f3 = FakeChameleon(uid=b"\xaa\xbb\xcc\xdd")
    d3 = cham_daemon(f3)
    d3.emit = lambda o: None
    err = d3.handle({"id": 1, "method": "magic_write", "params": {"blocks": _dump_1k()}})
    check("magic_write requires target_uid (error envelope, nothing written)",
          "error" in err and "target_uid" in err["error"] and f3.written == {}, str(err))


# --------------------------------------------------------------------------
# 24. magic_write guards: wrong target card, and a sector with no usable key.
# --------------------------------------------------------------------------
def test_cham_magic_write_guards(check):
    # card-pin: the card on the reader differs from the authorised uid -> refuse
    fake = FakeChameleon(uid=b"\x11\x22\x33\x44")
    d = cham_daemon(fake)
    d.emit = lambda o: None
    r = d.magic_write({"blocks": _dump_1k(), "target_uid": "aa bb cc dd"})
    check("magic_write refuses when the card on the reader is not the authorised one",
          r["present"] is True and r["wrote"] == 0 and "card changed" in (r["error"] or ""),
          str(r))
    check("magic_write wrote nothing to the wrong card", fake.written == {}, str(fake.written))

    # a sector whose key is not FF / builtin: the all-sector auth preflight must abort
    # BEFORE any write (never a half-written, mixed-key card), and write NOTHING.
    km = {(s, kt): FF for s in range(16) for kt in ("A", "B")}
    km[(5, "A")] = "0f1e2d3c4b5a"          # sector 5 not openable by the defaults
    km[(5, "B")] = "0f1e2d3c4b5a"
    f2 = FakeChameleon(uid=b"\xaa\xbb\xcc\xdd", keymap=km)
    d2 = cham_daemon(f2)
    events = []
    d2.emit = lambda o: events.append(o)
    r2 = d2.magic_write({"blocks": _dump_1k(), "trailers": True, "uid": True,
                         "target_uid": "aa bb cc dd"})
    check("magic_write preflight aborts a no-key sector: nothing written, reason names the sector",
          r2["wrote"] == 0 and "no key for sector 5" in (r2["error"] or "")
          and f2.written == {}, str(r2))
    check("magic_write emits no per-block progress when the preflight aborts",
          not any(e.get("method") == "magic_write" for e in events), "wrote before preflight abort")


# --------------------------------------------------------------------------
# 25. magic_write mid-write swap: the per-write uid pin aborts (CRITICAL regression).
# --------------------------------------------------------------------------
def test_cham_magic_write_midswap(check):
    fake = MidWriteSwapFake(uid=b"\xaa\xbb\xcc\xdd")
    d = cham_daemon(fake)
    events = []
    d.emit = lambda o: events.append(o)
    r = d.magic_write({"blocks": _dump_1k(), "trailers": True, "uid": True,
                       "target_uid": "aa bb cc dd"})
    check("magic_write aborts when the card is swapped mid-write (re-pins uid per block)",
          "card changed" in (r["error"] or ""), str(r))
    check("magic_write stops on the swap - only the single pre-swap block was written",
          len(fake.written) == 1 and r["wrote"] == 1, "wrote %d blocks" % len(fake.written))
    ok_events = [e for e in events if e.get("ok")]
    check("magic_write streamed exactly one successful write before aborting",
          len(ok_events) == 1, "%d ok events" % len(ok_events))


# --------------------------------------------------------------------------
# 26. magic_write trailer key substitution: a zeroed key slot is never written as 000000.
# --------------------------------------------------------------------------
def test_cham_magic_write_trailer_keys(check):
    K = "a0b1c2d3e4f5"                     # the dump's recovered sector-0 key
    dump = _dump_1k()
    # sector-0 trailer read back with BOTH key slots zeroed (firmware masks Key A, and a
    # KeyB-unreadable sector masks Key B too), access ff0780, GPB 69
    dump["3"] = "000000000000" + "ff078069" + "000000000000"
    fake = FakeChameleon(uid=b"\xaa\xbb\xcc\xdd")
    d = cham_daemon(fake)
    d.emit = lambda o: None
    d.magic_write({"blocks": dump, "keys": {"0": ["A", K]}, "trailers": True,
                   "uid": True, "target_uid": "aa bb cc dd"})
    tr = fake.written.get(3)
    check("magic_write substitutes a zeroed trailer KeyA with the dump's recovered key",
          tr is not None and tr[0:6] == bytes.fromhex(K), tr.hex() if tr else "not written")
    check("magic_write substitutes a zeroed trailer KeyB too (never writes 000000)",
          tr is not None and tr[10:16] == bytes.fromhex(K), tr.hex() if tr else "not written")
    check("magic_write keeps the substituted trailer access bytes writable (ff0780)",
          tr is not None and tr[6:9] == bytes.fromhex("ff0780"), tr.hex() if tr else "not written")


TESTS = [test_cham_info, test_cham_slots, test_cham_slot_select, test_cham_poll,
         test_cham_read_block, test_cham_decode, test_cham_decode_partial,
         test_cham_decode_nocard, test_cham_decode_swap, test_cham_decode_user_key,
         test_cham_transport_wedge, test_cham_dispatch,
         test_cham_decode_nested_chain, test_cham_decode_darkside_chain,
         test_cham_decode_static_chain, test_cham_decode_hard_prng,
         test_cham_decode_cancel, test_cham_attack_budget_guard,
         test_cham_slot_config, test_cham_emulate_mode, test_cham_emulate_load,
         test_cham_emu_read, test_cham_magic_write, test_cham_magic_write_guards,
         test_cham_magic_write_midswap, test_cham_magic_write_trailer_keys]


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
