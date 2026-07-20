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
import io
import sys
import json
import zipfile
import hashlib
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


# --------------------------------------------------------------------------
# Firmware DFU (chameleon_d dfu_check / dfu_flash) - hardware-free.
# Every hardware / network / subprocess seam is a small overridable method, so the
# REAL validation + enter-DFU + argv + progress-parse logic runs against fakes.
# --------------------------------------------------------------------------

def _pb_varint(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _pb_lenf(field, data):
    return _pb_varint(field << 3 | 2) + _pb_varint(len(data)) + data


def _pb_varf(field, n):
    return _pb_varint(field << 3 | 0) + _pb_varint(n)


def _init_packet(binf, hash_type=3, signed=True, good_hash=True,
                 fw_type=0, dup_type=None, sd_size=None, bl_size=None):
    """Build a Nordic dfu-cc init packet (application.dat) for `binf`, mirroring the real
    structure the daemon walks: Packet.signed_command -> command -> InitCommand -> hash.
    Defaults to a valid APPLICATION packet (type=0). When `good_hash` the stored
    (byte-reversed) hash matches `binf`; `signed=False` omits the signed_command wrapper.
    `fw_type=None` omits the type field (a MISSING type, which must be refused). `dup_type`
    appends a SECOND type field (a duplicate security-relevant field, which must be refused).
    `fw_type` != 0 + `sd_size` / `bl_size` build a RENAMED FULL packet: a signed image that
    DECLARES bootloader/softdevice even though it is named application.dat."""
    algo = {2: "sha1", 3: "sha256", 4: "sha512"}[hash_type]
    digest = hashlib.new(algo, binf).digest()
    stored = bytes(reversed(digest)) if good_hash \
        else bytes(reversed(hashlib.new(algo, binf + b"tamper").digest()))
    hashmsg = _pb_varf(1, hash_type) + _pb_lenf(2, stored)
    init = b""
    if fw_type is not None:
        init += _pb_varf(4, fw_type)
    if dup_type is not None:
        init += _pb_varf(4, dup_type)
    if sd_size is not None:
        init += _pb_varf(5, sd_size)
    if bl_size is not None:
        init += _pb_varf(6, bl_size)
    init += _pb_lenf(8, hashmsg)
    command = _pb_varf(1, 1) + _pb_lenf(2, init)
    if signed:
        signed_cmd = _pb_lenf(1, command) + _pb_varf(2, 0) + _pb_lenf(3, b"\x00" * 64)
        return _pb_lenf(2, signed_cmd)
    return _pb_lenf(1, command)                    # unsigned: Packet.command directly


def _make_dfu_zip(dat, binf, full=False):
    """Write a DFU zip to a temp file and return its path. `full=True` makes it a FULL
    package (adds sd_bl.* + a manifest declaring softdevice_bootloader) that must be refused."""
    fd, path = tempfile.mkstemp(prefix="cham-dfu-test-", suffix=".zip")
    os.close(fd)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("application.dat", dat)
        z.writestr("application.bin", binf)
        mani = {"manifest": {"application": {"bin_file": "application.bin",
                                             "dat_file": "application.dat"}}}
        if full:
            mani["manifest"]["softdevice_bootloader"] = {"bin_file": "sd_bl.bin",
                                                         "dat_file": "sd_bl.dat"}
            z.writestr("sd_bl.bin", b"\x00" * 64)
            z.writestr("sd_bl.dat", b"\x00" * 64)
        z.writestr("manifest.json", json.dumps(mani))
    return path


class FakeSerial:
    """Captures the raw bytes written for the enter-DFU frame (stands in for pyserial)."""
    def __init__(self, port):
        self.port = port
        self.writes = []
        self.dtr = None
        self.timeout = None
        self.closed = False

    def write(self, data):
        self.writes.append(bytes(data))
        return len(data)

    def close(self):
        self.closed = True


class FakePort:
    """A pyserial ListPortInfo stand-in (device / vid / pid) for port-discovery tests."""
    def __init__(self, device, vid, pid):
        self.device, self.vid, self.pid = device, vid, pid


class FakePopen:
    """Stands in for the adafruit-nrfutil subprocess: yields canned output lines then a
    chosen exit code, so the daemon's progress parse + non-zero handling are exercised.
    `poll()` mirrors a real Popen (None while running, the exit code once waited), so the
    daemon's wait-for-exit-before-releasing-the-guard path is exercised too."""
    def __init__(self, lines, code=0):
        self.stdout = iter(lines)
        self._code = code
        self._done = False

    def wait(self):
        self._done = True
        return self._code

    def poll(self):
        return self._code if self._done else None


class SlowExitPopen:
    """A flasher that is still RUNNING (poll() -> None) until wait() has been called enough
    times; the first `raise_waits` wait() calls raise (a transient EINTR-style failure that
    must be retried). Proves _run_flash never returns while the child is alive and never
    treats a raising wait() as 'exited'."""
    def __init__(self, lines, code=0, raise_waits=0):
        self.stdout = iter(lines)
        self._code = code
        self._alive = True
        self._waits = 0
        self._raise_waits = raise_waits

    def wait(self):
        self._waits += 1
        if self._waits <= self._raise_waits:
            raise OSError("interrupted wait")
        self._alive = False
        return self._code

    def poll(self):
        return None if self._alive else self._code


class _FakeClock:
    """A deterministic monotonic()/sleep() stand-in: sleep() advances a virtual clock instead
    of blocking, so time-dependent polling (the DFU settle window) is tested exactly and fast.
    Installed by rebinding chameleon_d.time for the duration of a call."""
    def __init__(self):
        self.t = 0.0

    def monotonic(self):
        return self.t

    def sleep(self, dt):
        self.t += dt


class _GatedStream:
    """A stdin stand-in that yields `lines`, then BLOCKS at EOF until `gate` is set - so a
    test can hold the stream open (delaying run()'s EOF teardown, which also sets the cancel
    flag) until an in-flight op has reached a chosen point."""
    def __init__(self, lines, gate):
        self._it = iter(lines)
        self._gate = gate

    def __iter__(self):
        return self

    def __next__(self):
        try:
            return next(self._it)
        except StopIteration:
            self._gate.wait(5)
            raise


def _dfu_daemon(fake):
    """A daemon wired to a FakeChameleon, with the network seam stubbed off by default so
    a stray call cannot reach GitHub. DFU tests override the specific seams they exercise. The
    flasher head is pinned to a deterministic value so _run_flash works regardless of whether
    adafruit-nrfutil is installed on the test machine; the REAL PATH-independent resolver is
    exercised by test_cham_dfu_flasher_resolve."""
    d = cham_daemon(fake)
    d._latest_release = lambda model: {"tag": "v2.0.0", "url": "https://example/%s" % model}
    d._flasher_head = lambda: ["adafruit-nrfutil"]
    return d


def _expect_reject(check, label, fn):
    try:
        fn()
        check("dfu validate rejects %s" % label, False, "ACCEPTED an unsafe package")
    except RuntimeError as e:
        check("dfu validate rejects %s" % label, True, str(e))


def _good_pkg_bytes(binf=None):
    """The bytes of a well-formed app-only DFU zip (what an official download would yield)."""
    binf = binf if binf is not None else bytes(range(256)) * 4
    p = _make_dfu_zip(_init_packet(binf), binf)
    data = open(p, "rb").read()
    os.remove(p)
    return data


def _mock_download(data):
    """A `_download_asset(rel, dest)` stand-in that writes `data` to dest (v1 is download-only,
    so the real network + validation seams are the download itself; tests inject the bytes)."""
    def _dl(rel, dest):
        with open(dest, "wb") as f:
            f.write(data)
        return len(data)
    return _dl


# --------------------------------------------------------------------------
# 27. dfu asset selection: model 0 -> ultra, else lite.
# --------------------------------------------------------------------------
def test_cham_dfu_asset(check):
    d = _dfu_daemon(FakeChameleon())
    check("dfu asset: model 0 (Ultra) -> ultra-dfu-app.zip",
          d._dfu_asset_name(0) == "ultra-dfu-app.zip", d._dfu_asset_name(0))
    check("dfu asset: model 1 (Lite) -> lite-dfu-app.zip",
          d._dfu_asset_name(1) == "lite-dfu-app.zip", d._dfu_asset_name(1))


# --------------------------------------------------------------------------
# 27a. model normalisation (finding 6): only exact 0/1 or 'ultra'/'lite' is accepted;
#      every other value (2, -1, 0.5, bool, other strings) is refused, never coerced.
# --------------------------------------------------------------------------
def test_cham_dfu_norm_model(check):
    d = _dfu_daemon(FakeChameleon())
    check("norm_model accepts the exact valid choices",
          [d._norm_model(m) for m in (0, 1, "ultra", "lite", "0", "1", None)]
          == [0, 1, 0, 1, 0, 1, None], "valid mapping wrong")
    for bad in (2, -1, 0.5, True, False, "banana", "2", "ULTRAX"):
        ok = False
        try:
            d._norm_model(bad)
        except RuntimeError:
            ok = True
        check("norm_model refuses %r (no numeric/other guess)" % (bad,), ok, "was coerced")

    # finding 2: _dfu_asset_name is fail-closed - 0/1 only, anything else raises (never maps a
    # non-0 value to Lite).
    check("dfu_asset_name maps 0/1 to the model assets",
          d._dfu_asset_name(0) == "ultra-dfu-app.zip" and d._dfu_asset_name(1) == "lite-dfu-app.zip")
    for bad in (2, -1, "ultra", None):
        ok = False
        try:
            d._dfu_asset_name(bad)
        except RuntimeError:
            ok = True
        check("dfu_asset_name refuses %r (no Lite fallback)" % (bad,), ok, "coerced to an asset")


# --------------------------------------------------------------------------
# 27b. port discovery: CDC (0x6868) vs DFU (0x1915/0x521f) filtering, and the REAL
#      _wait_new_dfu_ports over _list_ports (a NEW port relative to a pre-reboot snapshot).
# --------------------------------------------------------------------------
def test_cham_dfu_port_discovery(check):
    d = _dfu_daemon(FakeChameleon())
    d._list_ports = lambda: [
        FakePort("/dev/cu.usbmodem6868", 0x6868, 0x8686),
        FakePort("/dev/cu.usbserial", 0x0403, 0x6001),   # an unrelated FTDI adapter
        FakePort("/dev/cu.usbmodemDFU", 0x1915, 0x521f),
    ]
    check("_find_cdc_ports returns only the Chameleon CDC (VID 0x6868) ports",
          d._find_cdc_ports() == ["/dev/cu.usbmodem6868"], str(d._find_cdc_ports()))
    check("_find_dfu_ports returns only the bootloader (0x1915/0x521f) ports",
          d._find_dfu_ports() == ["/dev/cu.usbmodemDFU"], str(d._find_dfu_ports()))
    check("_wait_new_dfu_ports (real) returns a DFU port not in the snapshot",
          d._wait_new_dfu_ports([], timeout=1, settle=0.1) == ["/dev/cu.usbmodemDFU"],
          "did not find the new DFU port")
    check("_wait_new_dfu_ports ignores a DFU port already in the snapshot (times out to [])",
          d._wait_new_dfu_ports(["/dev/cu.usbmodemDFU"], timeout=0, settle=0.1) == [],
          "returned a pre-existing DFU port")


# --------------------------------------------------------------------------
# 28. enter-bootloader writes the EXACT 10-byte frame, DTR high, then closes.
# --------------------------------------------------------------------------
def test_cham_dfu_enter_bootloader(check):
    d = _dfu_daemon(FakeChameleon())
    fs = FakeSerial("/dev/cu.usbmodem6868")
    d._serial = lambda port: fs
    d._send_enter_dfu("/dev/cu.usbmodem6868")
    check("enter-DFU writes exactly the 10-byte ENTER_BOOTLOADER frame",
          fs.writes == [b"\x11\xef\x03\xf2\x00\x00\x00\x00\x0b\x00"], str([w.hex() for w in fs.writes]))
    check("enter-DFU raises DTR before writing and closes the port after",
          fs.dtr == 1 and fs.closed is True, "dtr=%s closed=%s" % (fs.dtr, fs.closed))


# --------------------------------------------------------------------------
# 29. package validation: accept a well-formed app-only zip; reject full / mismatch /
#     unsigned / missing-file zips - the brick-safety gate.
# --------------------------------------------------------------------------
def test_cham_dfu_validate(check):
    binf = bytes(range(256)) * 4
    d = _dfu_daemon(FakeChameleon())

    # NOTE: this is an INTEGRITY check, not a signature check. The host confirms the
    # package shape (signed_command present), the DECLARED app-only type, and that the
    # image hash matches the init packet; the nRF bootloader is the real signature
    # authority (it verifies the ECDSA signature at flash time). The 64 zero "signature"
    # bytes below satisfy the shape check, not a cryptographic one.
    good = _make_dfu_zip(_init_packet(binf, good_hash=True), binf)
    info = d._validate_dfu_zip(good)
    check("dfu validate ACCEPTS a well-formed app-only package (integrity-only, device signs)",
          info.get("hash_type") == "sha256" and info.get("fw_type") == 0, str(info))

    full = _make_dfu_zip(_init_packet(binf, good_hash=True), binf, full=True)
    _expect_reject(check, "a full-dfu package (bootloader + softdevice, by filename + manifest)",
                   lambda: d._validate_dfu_zip(full))

    # CRITICAL (finding 1): a RENAMED full package - a signed image DECLARING
    # softdevice+bootloader (type=3, nonzero sd_size/bl_size) but named application.dat/.bin
    # with an app-only manifest - must be refused on its DECLARED type, not just its name.
    renamed = _make_dfu_zip(
        _init_packet(binf, good_hash=True, fw_type=3, sd_size=4096, bl_size=8192), binf)
    _expect_reject(check, "a RENAMED full package (declared type != application)",
                   lambda: d._validate_dfu_zip(renamed))

    # CRITICAL (finding 1): a MISSING type field is refused (fail closed - not the proto
    # default). The old code accepted a no-type packet; this now rejects it.
    notype = _make_dfu_zip(_init_packet(binf, fw_type=None), binf)
    _expect_reject(check, "a packet with NO declared type (fail-closed)",
                   lambda: d._validate_dfu_zip(notype))

    # a DUPLICATE type field is refused (a repeat could make host and bootloader read
    # different values).
    duptype = _make_dfu_zip(_init_packet(binf, fw_type=0, dup_type=3), binf)
    _expect_reject(check, "a packet with a DUPLICATE type field",
                   lambda: d._validate_dfu_zip(duptype))

    # finding 5: a valid application pair PLUS a manifest.softdevice_bootloader entry whose
    # files DODGE the name markers - adafruit-nrfutil would follow the manifest and write the
    # full payload, so the manifest is inspected and any non-application image class refused.
    fdm, manifull = tempfile.mkstemp(prefix="cham-dfu-test-", suffix=".zip")
    os.close(fdm)
    with zipfile.ZipFile(manifull, "w") as z:
        z.writestr("application.dat", _init_packet(binf))
        z.writestr("application.bin", binf)
        z.writestr("core.dat", b"\x00" * 8)                  # dodges bootloader/softdevice markers
        z.writestr("core.bin", b"\x00" * 8)
        z.writestr("manifest.json", json.dumps({"manifest": {
            "application": {"dat_file": "application.dat", "bin_file": "application.bin"},
            "softdevice_bootloader": {"dat_file": "core.dat", "bin_file": "core.bin"}}}))
    _expect_reject(check, "a manifest declaring softdevice_bootloader (files dodge the markers)",
                   lambda: d._validate_dfu_zip(manifull))

    # finding 5: a manifest whose application entry points at NON-standard files (indirection)
    # is refused - the flasher must read exactly the application.dat/.bin we validated.
    fdi, indirect = tempfile.mkstemp(prefix="cham-dfu-test-", suffix=".zip")
    os.close(fdi)
    with zipfile.ZipFile(indirect, "w") as z:
        z.writestr("application.dat", _init_packet(binf))
        z.writestr("application.bin", binf)
        z.writestr("manifest.json", json.dumps({"manifest": {
            "application": {"dat_file": "other.dat", "bin_file": "other.bin"}}}))
    _expect_reject(check, "a manifest whose application entry names non-standard files",
                   lambda: d._validate_dfu_zip(indirect))

    # a package with NO manifest.json is refused (cannot confirm app-only)
    fdn, nomani = tempfile.mkstemp(prefix="cham-dfu-test-", suffix=".zip")
    os.close(fdn)
    with zipfile.ZipFile(nomani, "w") as z:
        z.writestr("application.dat", _init_packet(binf))
        z.writestr("application.bin", binf)
    _expect_reject(check, "a package with no manifest.json",
                   lambda: d._validate_dfu_zip(nomani))

    mism = _make_dfu_zip(_init_packet(binf, good_hash=False), binf)
    _expect_reject(check, "a hash-mismatch package",
                   lambda: d._validate_dfu_zip(mism))

    uns = _make_dfu_zip(_init_packet(binf, signed=False), binf)
    _expect_reject(check, "an unsigned package",
                   lambda: d._validate_dfu_zip(uns))

    # missing application.bin entirely
    fd, miss = tempfile.mkstemp(prefix="cham-dfu-test-", suffix=".zip")
    os.close(fd)
    with zipfile.ZipFile(miss, "w") as z:
        z.writestr("application.dat", _init_packet(binf))
    _expect_reject(check, "a package missing application.bin",
                   lambda: d._validate_dfu_zip(miss))

    for p in (good, full, renamed, notype, duptype, manifull, indirect, nomani, mism, uns, miss):
        os.remove(p)


# --------------------------------------------------------------------------
# 30. dfu_check: reports the current firmware (app + git) and the latest release tag.
# --------------------------------------------------------------------------
def test_cham_dfu_check(check):
    fake = FakeChameleon(model=0, app=(2, 0), git="v1.9.0-4-gabcdef")
    d = _dfu_daemon(fake)                          # _latest_release stubbed -> v2.0.0
    r = d.dfu_check({})
    check("dfu_check reports the current firmware version + git",
          r["current"] == "2.0" and r["git"] == "v1.9.0-4-gabcdef", str(r))
    check("dfu_check reports the latest release tag + the model asset",
          r["latest"] == "v2.0.0" and r["asset"] == "ultra-dfu-app.zip", str(r))
    check("dfu_check flags an update when the latest tag is newer than the running git",
          r["updateAvailable"] is True, str(r.get("updateAvailable")))

    # up to date: the running git already embeds the latest tag -> no update offered
    fake2 = FakeChameleon(model=0, app=(2, 0), git="v2.0.0-0-gdeadbee")
    r2 = _dfu_daemon(fake2).dfu_check({})
    check("dfu_check reports no update when the running firmware already is the latest",
          r2["updateAvailable"] is False, str(r2.get("updateAvailable")))

    # offline: a failed release fetch is not fatal - latest is null, note carries why
    fake3 = FakeChameleon(model=0, app=(2, 0), git="v2.0.0")
    d3 = _dfu_daemon(fake3)
    d3._latest_release = lambda model: (_ for _ in ()).throw(RuntimeError("network down"))
    r3 = d3.dfu_check({})
    check("dfu_check survives an offline release fetch (latest null, note set)",
          r3["latest"] is None and r3["updateAvailable"] is False and "network down" in (r3["note"] or ""),
          str(r3))


# --------------------------------------------------------------------------
# 31. flash runner: correct adafruit-nrfutil argv + percent progress + error on non-zero.
# --------------------------------------------------------------------------
DFU_ARGV_TAIL = ["dfu", "serial", "-pkg", "/tmp/fw.zip", "-p", "/dev/cu.dfu", "-b", "115200"]


def test_cham_dfu_flash_runner(check):
    d = _dfu_daemon(FakeChameleon())
    argv = d._nrfutil_argv("/tmp/fw.zip", "/dev/cu.dfu")
    # the argv is the resolved flasher HEAD (tested for real in test_cham_dfu_flasher_resolve)
    # followed by the dfu serial arguments - assert the tail + that a non-empty head precedes it.
    check("dfu argv appends the dfu serial arguments to the resolved flasher head",
          argv[-len(DFU_ARGV_TAIL):] == DFU_ARGV_TAIL and len(argv) > len(DFU_ARGV_TAIL), str(argv))

    captured = {}

    def fake_popen(argv):
        captured["argv"] = argv
        return FakePopen(["Upgrading target...\n", "10%\n", "55%\n", "100%\n",
                          "Device programmed.\n"], code=0)
    d._popen = fake_popen
    emitted = []
    d.emit = lambda o: emitted.append(o)
    d._run_flash("/tmp/fw.zip", "/dev/cu.dfu")
    check("dfu flash runner spawns the argv (dfu serial tail present)",
          captured["argv"][-len(DFU_ARGV_TAIL):] == DFU_ARGV_TAIL, str(captured))
    pcts = [e["percent"] for e in emitted if e.get("method") == "dfu_flash" and "percent" in e]
    check("dfu flash runner streams the parsed percents then a final 100 done",
          10 in pcts and 55 in pcts and pcts[-1] == 100, str(pcts))
    check("dfu flash runner emits a terminal 'done' stage",
          any(e.get("stage") == "done" for e in emitted), str([e.get("stage") for e in emitted]))

    # a non-zero exit surfaces as a RuntimeError carrying the tool output tail
    d2 = _dfu_daemon(FakeChameleon())
    d2._popen = lambda argv: FakePopen(["Timed out waiting for ACK\n"], code=1)
    d2.emit = lambda o: None
    _expect_reject(check, "a non-zero flasher exit (surfaced as an error)",
                   lambda: d2._run_flash("/tmp/fw.zip", "/dev/cu.dfu"))

    # finding 5: a wait() that RAISES (still-running child) must NOT be treated as done; the
    # flasher is waited out until poll() reports exit, and the real exit code is then used.
    d3 = _dfu_daemon(FakeChameleon())
    proc3 = SlowExitPopen(["10%\n", "100%\n"], code=0, raise_waits=1)
    d3._popen = lambda argv: proc3
    ev3 = []
    d3.emit = lambda o: ev3.append(o)
    d3._run_flash("/tmp/fw.zip", "/dev/cu.dfu")    # must not raise despite the transient wait error
    check("run_flash waits out a still-running flasher (a transient wait error is not fatal)",
          proc3._alive is False and any(e.get("stage") == "done" for e in ev3),
          "flasher was abandoned or wrongly failed")
    # a non-zero exit is still surfaced even after a retried wait()
    d4 = _dfu_daemon(FakeChameleon())
    d4._popen = lambda argv: SlowExitPopen(["boom\n"], code=3, raise_waits=1)
    d4.emit = lambda o: None
    _expect_reject(check, "a non-zero exit after a retried wait (still surfaced)",
                   lambda: d4._run_flash("/tmp/fw.zip", "/dev/cu.dfu"))


# --------------------------------------------------------------------------
# 31b. flasher resolution: the REAL _flasher_head resolves adafruit-nrfutil WITHOUT PATH (the
#      packaged app spawns the daemon with the launchd PATH that excludes the bundled bin dir).
#      Covers the bundle (interpreter-relative script), module (nordicsemi -m), dev (PATH),
#      and none (clear error) cases; and that _nrfutil_argv = head + the dfu serial tail.
# --------------------------------------------------------------------------
def test_cham_dfu_flasher_resolve(check):
    d = chameleon_d.Daemon(learned=None)               # real resolver (no _dfu_daemon override)
    real_exec = chameleon_d.sys.executable
    real_find = chameleon_d.importlib.util.find_spec
    real_which = chameleon_d.shutil.which
    tail = ["dfu", "serial", "-pkg", "/tmp/f.zip", "-p", "/dev/cu.dfu", "-b", "115200"]
    tmp = tempfile.mkdtemp()
    empty = tempfile.mkdtemp()
    try:
        # (1) BUNDLE: an executable adafruit-nrfutil next to the interpreter is picked, no PATH.
        scr = os.path.join(tmp, "adafruit-nrfutil")
        with open(scr, "w") as f:
            f.write("#!/bin/sh\n")
        os.chmod(scr, 0o755)
        chameleon_d.sys.executable = os.path.join(tmp, "python3")
        head1 = d._flasher_head()
        check("flasher resolve (bundle): the interpreter-relative script is picked",
              head1 == [scr], str(head1))
        argv = d._nrfutil_argv("/tmp/f.zip", "/dev/cu.dfu")
        check("nrfutil argv = resolved head + the dfu serial tail",
              argv == [scr] + tail, str(argv))

        # (2) MODULE: no interpreter-relative script; nordicsemi importable -> [python, -m, nordicsemi].
        chameleon_d.sys.executable = os.path.join(empty, "python3")
        chameleon_d.importlib.util.find_spec = lambda name: object() if name == "nordicsemi" else None
        head2 = d._flasher_head()
        check("flasher resolve (module): [interpreter, -m, nordicsemi] when the module is importable",
              head2 == [os.path.join(empty, "python3"), "-m", "nordicsemi"], str(head2))

        # (3) DEV/PATH: nothing interpreter-relative or importable; shutil.which finds it on PATH.
        chameleon_d.importlib.util.find_spec = lambda name: None
        chameleon_d.shutil.which = lambda name: ("/usr/local/bin/adafruit-nrfutil"
                                                 if name == "adafruit-nrfutil" else None)
        head3 = d._flasher_head()
        check("flasher resolve (dev): shutil.which(PATH) is used when nothing else resolves",
              head3 == ["/usr/local/bin/adafruit-nrfutil"], str(head3))

        # each resolved head is one of the three valid forms
        forms_ok = ((len(head1) == 1 and os.path.isabs(head1[0]))
                    and (head2[1:] == ["-m", "nordicsemi"])
                    and (len(head3) == 1))
        check("every resolved head is one of the three valid forms (path / -m nordicsemi / which)",
              forms_ok, str((head1, head2, head3)))

        # (4) NONE resolvable -> a clear error (not a bare bad argv that FileNotFoundErrors later).
        chameleon_d.shutil.which = lambda name: None
        ok = False
        try:
            d._flasher_head()
        except RuntimeError as e:
            ok = "flasher not found" in str(e)
        check("flasher resolve: raises a clear 'flasher not found' when nothing resolves", ok)
    finally:
        chameleon_d.sys.executable = real_exec
        chameleon_d.importlib.util.find_spec = real_find
        chameleon_d.shutil.which = real_which
        __import__("shutil").rmtree(tmp, ignore_errors=True)
        __import__("shutil").rmtree(empty, ignore_errors=True)


# --------------------------------------------------------------------------
# 32. dfu_flash end-to-end (mocked): validate -> enter-DFU -> wait -> flash.
# --------------------------------------------------------------------------
def test_cham_dfu_flash_e2e(check):
    binf = bytes(range(256)) * 4
    data = _good_pkg_bytes(binf)                        # what the official download yields
    fake = FakeChameleon(model=0)
    d = _dfu_daemon(fake)
    fs = FakeSerial("/dev/cu.usbmodem6868")
    d._serial = lambda port: fs
    d._find_dfu_ports = lambda: []                     # snapshot empty; a NEW port appears after reboot
    d._find_cdc_ports = lambda: ["/dev/cu.usbmodem6868"]  # exactly one CDC device
    d._wait_new_dfu_ports = lambda before, timeout=20: ["/dev/cu.usbmodemDFU"]
    d._download_asset = _mock_download(data)           # download-only: inject the official bytes
    flashed = {}

    def fake_run(zp, port):
        flashed["zip"], flashed["port"] = zp, port
        d.emit({"event": "progress", "method": "dfu_flash", "stage": "flash", "percent": 50})
    d._run_flash = fake_run
    emitted = []
    d.emit = lambda o: emitted.append(o)

    r = d.dfu_flash({})                                # NO zip_path: model read from the device
    check("dfu_flash download-only path succeeds end to end",
          r["flashed"] is True and r["hash"] == "sha256" and r["tag"] == "v2.0.0", str(r))
    check("dfu_flash wrote the enter-DFU frame before flashing",
          fs.writes == [b"\x11\xef\x03\xf2\x00\x00\x00\x00\x0b\x00"], str([w.hex() for w in fs.writes]))
    check("dfu_flash flashes the NEW bootloader port, not the stale CDC one",
          flashed.get("port") == "/dev/cu.usbmodemDFU", str(flashed))
    check("dfu_flash flashes the downloaded model-specific asset from a private temp dir",
          flashed.get("zip") is not None and os.path.basename(flashed["zip"]) == "ultra-dfu-app.zip",
          str(flashed.get("zip")))
    stages = [e.get("stage") for e in emitted if e.get("method") == "dfu_flash"]
    check("dfu_flash streams prepare -> download -> validated -> enter -> wait -> flash",
          all(s in stages for s in ("prepare", "download", "validated", "enter", "wait", "flash")),
          str(stages))

    # a device already in DFU (no CDC to reboot) with an EXPLICIT model flashes straight away
    d2 = _dfu_daemon(FakeChameleon(model=0))
    d2._find_dfu_ports = lambda: ["/dev/cu.usbmodemDFU"]
    d2._find_cdc_ports = lambda: []                    # pure recovery: nothing to reboot
    d2._download_asset = _mock_download(_good_pkg_bytes(binf))
    already = {}
    d2._send_enter_dfu = lambda port: already.setdefault("entered", True)
    d2._run_flash = lambda zp, port: already.update(port=port, zip=zp)
    d2.emit = lambda o: None
    r2 = d2.dfu_flash({"model": "lite"})               # explicit model choice for recovery
    check("dfu_flash recovers an already-in-DFU device with an explicit model (no enter-DFU)",
          r2["flashed"] is True and "entered" not in already
          and already.get("port") == "/dev/cu.usbmodemDFU", str((r2, already)))
    check("dfu_flash recovery flashes the CHOSEN model's asset (lite-dfu-app.zip)",
          os.path.basename(already.get("zip", "")) == "lite-dfu-app.zip", str(already.get("zip")))

    # already in DFU with NO explicit model: refuse (never guess Ultra vs Lite)
    d3 = _dfu_daemon(FakeChameleon(model=0))
    d3._find_dfu_ports = lambda: ["/dev/cu.usbmodemDFU"]
    d3._find_cdc_ports = lambda: []
    d3._run_flash = lambda zp, port: None
    d3.emit = lambda o: None
    err3 = d3.handle({"id": 1, "method": "dfu_flash", "params": {}})
    check("dfu_flash refuses an already-in-DFU device of unknown model (requires an explicit choice)",
          "error" in err3 and "choose Ultra or Lite" in err3["error"], str(err3))

    # finding 1: a caller-supplied model on a LIVE (CDC) device is REJECTED - the model must
    # come from hardware, so a live Ultra can never be handed Lite firmware. This exercises the
    # real path (no _download_asset mock is even reached: it fails before touching hardware).
    d4 = _dfu_daemon(FakeChameleon(model=0))            # a live Ultra
    d4._find_dfu_ports = lambda: []
    d4._find_cdc_ports = lambda: ["/dev/cu.usbmodem6868"]
    hit4 = {}
    d4._send_enter_dfu = lambda port: hit4.setdefault("entered", True)
    d4._run_flash = lambda zp, port: hit4.setdefault("flashed", True)
    d4.emit = lambda o: None
    err4 = d4.handle({"id": 2, "method": "dfu_flash", "params": {"model": "lite"}})
    check("dfu_flash rejects a caller-supplied model on a live device (reads model from hardware)",
          "error" in err4 and "cannot be overridden" in err4["error"]
          and "entered" not in hit4 and "flashed" not in hit4, str(err4))

    # finding 2: a live device whose HARDWARE model is an unexpected value (2) is REFUSED, not
    # coerced to Lite firmware.
    d5 = _dfu_daemon(FakeChameleon(model=2))            # an unexpected/future model
    d5._find_dfu_ports = lambda: []
    d5._find_cdc_ports = lambda: ["/dev/cu.usbmodem6868"]
    hit5 = {}
    d5._send_enter_dfu = lambda port: hit5.setdefault("entered", True)
    d5._run_flash = lambda zp, port: hit5.setdefault("flashed", True)
    d5.emit = lambda o: None
    err5 = d5.handle({"id": 3, "method": "dfu_flash", "params": {}})
    check("dfu_flash refuses a live device of unexpected hardware model (never picks Lite)",
          "error" in err5 and "unknown model" in err5["error"]
          and "entered" not in hit5 and "flashed" not in hit5, str(err5))


# --------------------------------------------------------------------------
# 33. dfu_flash brick-safety: a full package is refused before any hardware touch;
#     a pre-cancel stops before the flash; an unreachable device gives the manual fallback.
# --------------------------------------------------------------------------
def test_cham_dfu_flash_safety(check):
    binf = bytes(range(256)) * 4
    full_bytes = open(_make_dfu_zip(_init_packet(binf), binf, full=True), "rb").read()
    good_bytes = _good_pkg_bytes(binf)

    # a FULL package (downloaded) is refused by the app-only sanity check, before any hardware
    fake = FakeChameleon(model=0)
    d = _dfu_daemon(fake)
    d._find_dfu_ports = lambda: []
    d._find_cdc_ports = lambda: ["/dev/cu.usbmodem6868"]
    d._download_asset = _mock_download(full_bytes)
    entered = {"n": 0}
    d._send_enter_dfu = lambda port: entered.__setitem__("n", entered["n"] + 1)
    d._run_flash = lambda zp, port: entered.__setitem__("flashed", True)
    d.emit = lambda o: None
    err = d.handle({"id": 1, "method": "dfu_flash", "params": {}})
    check("dfu_flash refuses a FULL asset (sanity check) BEFORE touching hardware (no enter, no flash)",
          "error" in err and entered["n"] == 0 and "flashed" not in entered, str(err))

    # pre-cancel: the flag is set before the op -> it stops after validate, never flashes
    d2 = _dfu_daemon(FakeChameleon(model=0))
    d2._find_dfu_ports = lambda: []
    d2._find_cdc_ports = lambda: ["/dev/cu.usbmodem6868"]
    d2._download_asset = _mock_download(good_bytes)
    d2._send_enter_dfu = lambda port: None
    ran = {}
    d2._run_flash = lambda zp, port: ran.setdefault("flashed", True)
    d2.emit = lambda o: None
    d2._cancel.set()
    r = d2.dfu_flash({})
    check("a pre-cancelled dfu_flash stops before the flash write (never bricks a cancel)",
          r.get("cancelled") is True and "flashed" not in ran, str((r, ran)))

    # unreachable device (no CDC port, not already in DFU) -> the manual B-button fallback
    d3 = _dfu_daemon(FakeChameleon(model=0))
    d3._find_dfu_ports = lambda: []
    d3._find_cdc_ports = lambda: []
    d3.emit = lambda o: None
    err3 = d3.handle({"id": 2, "method": "dfu_flash", "params": {}})
    check("dfu_flash on an unreachable device returns the manual B-button fallback",
          "error" in err3 and "button B" in err3["error"], str(err3))

    # more than one device in DFU (and no CDC to reboot) -> refuse
    d4 = _dfu_daemon(FakeChameleon(model=0))
    d4._find_dfu_ports = lambda: ["/dev/cu.dfu1", "/dev/cu.dfu2"]
    d4._find_cdc_ports = lambda: []
    flashed4 = {}
    d4._run_flash = lambda zp, port: flashed4.setdefault("flashed", True)
    d4.emit = lambda o: None
    err4 = d4.handle({"id": 3, "method": "dfu_flash", "params": {"model": "ultra"}})
    check("dfu_flash refuses when MULTIPLE devices are in DFU (never flashes the wrong one)",
          "error" in err4 and "more than one" in err4["error"] and "flashed" not in flashed4, str(err4))

    # more than one Chameleon connected (needing reboot) -> refuse, never reboot an arbitrary one
    d5 = _dfu_daemon(FakeChameleon(model=0))
    d5._find_dfu_ports = lambda: []
    d5._find_cdc_ports = lambda: ["/dev/cu.a", "/dev/cu.b"]
    entered5 = {}
    d5._send_enter_dfu = lambda port: entered5.setdefault("entered", True)
    d5.emit = lambda o: None
    err5 = d5.handle({"id": 4, "method": "dfu_flash", "params": {}})
    check("dfu_flash refuses to reboot when MULTIPLE Chameleons are connected (no arbitrary enter)",
          "error" in err5 and "more than one" in err5["error"] and "entered" not in entered5, str(err5))


# --------------------------------------------------------------------------
# 33b. download integrity (finding 4): a complete download passes; a truncated / oversized /
#      digest-mismatch / non-https download is refused.
# --------------------------------------------------------------------------
def test_cham_dfu_download_checks(check):
    data = _good_pkg_bytes()
    sha = hashlib.sha256(data).hexdigest()
    d = _dfu_daemon(FakeChameleon())
    fd, dest = tempfile.mkstemp(suffix=".zip")
    os.close(fd)

    # (finding 3) the download must go through the pinned asset-ID endpoint with an
    # octet-stream Accept, so a delete+replace of the asset cannot swap the bytes.
    captured = {}

    def pin_http(url, dest, max_bytes=chameleon_d.MAX_FIRMWARE_BYTES, timeout=180, accept=None):
        captured["url"], captured["accept"] = url, accept
        with open(dest, "wb") as f:
            f.write(data)
        return len(data)
    d._http_download = pin_http
    w = d._download_asset({"asset_id": 4242, "size": len(data), "digest": "sha256:" + sha}, dest)
    check("download pins the asset by ID (releases/assets/<id>) with octet-stream Accept",
          w == len(data) and captured["url"].endswith("/releases/assets/4242")
          and captured["accept"] == "application/octet-stream", str(captured))

    # (finding 3/4) no asset id -> cannot pin -> refused
    _expect_reject(check, "an asset with no id (cannot pin the exact asset)",
                   lambda: d._download_asset({"size": len(data)}, dest))
    # (finding 4) a missing / non-integer / out-of-range size is refused (not silently skipped)
    _expect_reject(check, "an asset with no declared size",
                   lambda: d._download_asset({"asset_id": 1}, dest))
    _expect_reject(check, "an asset whose size exceeds the cap",
                   lambda: d._download_asset({"asset_id": 1, "size": chameleon_d.MAX_FIRMWARE_BYTES + 1}, dest))
    for bad_size in (True, 12.0, 0, -5):
        _expect_reject(check, "an asset with a %r declared size" % (bad_size,),
                       lambda bs=bad_size: d._download_asset({"asset_id": 1, "size": bs}, dest))
    # truncated: bytes written != declared size -> refused
    _expect_reject(check, "a truncated download (size mismatch)",
                   lambda: d._download_asset({"asset_id": 1, "size": len(data) + 10}, dest))
    # (finding 4) a present-but-unsupported / malformed digest is refused (not skipped)
    _expect_reject(check, "a present digest with an unsupported algorithm",
                   lambda: d._download_asset({"asset_id": 1, "size": len(data), "digest": "md5:abcd"}, dest))
    _expect_reject(check, "a present digest that is malformed (bad hex)",
                   lambda: d._download_asset({"asset_id": 1, "size": len(data), "digest": "sha256:nothex"}, dest))
    # digest mismatch -> refused
    _expect_reject(check, "a download whose sha256 digest does not match",
                   lambda: d._download_asset({"asset_id": 1, "size": len(data),
                                              "digest": "sha256:" + "0" * 64}, dest))
    # an ENTIRELY ABSENT digest is allowed (size + asset-ID pin are the completeness guards)
    ok = True
    try:
        d._download_asset({"asset_id": 1, "size": len(data)}, dest)
    except Exception:
        ok = False
    check("an absent digest is allowed (size + asset-ID pin guard completeness)", ok)

    # (finding 4) non-https initial url, and a redirect that DOWNGRADES to http, are refused
    # (real _http_download over a fake urlopen).
    d2 = chameleon_d.Daemon(learned=None)
    _expect_reject(check, "a non-https firmware url",
                   lambda: d2._http_download("http://x/y", dest))
    import urllib.request as _u
    _orig = _u.urlopen

    class _Resp:
        def __init__(s, b, final): s._b = io.BytesIO(b); s._f = final
        def geturl(s): return s._f
        def read(s, n): return s._b.read(n)
        def __enter__(s): return s
        def __exit__(s, *a): pass
    try:
        _u.urlopen = lambda req, timeout=0: _Resp(b"x" * 10, "http://evil/blob")
        _expect_reject(check, "a redirect that downgrades to a non-https url",
                       lambda: d2._http_download("https://x/y", dest))
        _u.urlopen = lambda req, timeout=0: _Resp(b"x" * 5000, "https://ok/blob")
        _expect_reject(check, "an oversized download (exceeds the byte cap)",
                       lambda: d2._http_download("https://x/y", dest, max_bytes=100))
    finally:
        _u.urlopen = _orig
    os.remove(dest)


# --------------------------------------------------------------------------
# 33c. device identity binding (finding 6): snapshot the DFU ports BEFORE the reboot, then
#      accept exactly ONE NEW attributable port; a second new DFU device -> refuse. A device
#      already stuck in DFU before the reboot is never mistaken for the rebooted one.
# --------------------------------------------------------------------------
def test_cham_dfu_identity_binding(check):
    binf = bytes(range(256)) * 4
    data = _good_pkg_bytes(binf)

    # (a) DELAYED second enumeration exercised through the REAL _wait_new_dfu_ports: the target
    #     appears, then a SECOND new DFU device enumerates a poll later (inside the settle
    #     window). The helper must observe the whole window, accumulate both, and the caller
    #     refuses - never flashing on the first-seen. (This is the bug a mock that returns both
    #     at once would hide.)
    d = _dfu_daemon(FakeChameleon(model=0))
    d._find_cdc_ports = lambda: ["/dev/cu.usbmodem6868"]
    d._serial = lambda port: FakeSerial(port)
    d._download_asset = _mock_download(data)
    seq = [[], ["/dev/cu.a"], ["/dev/cu.a", "/dev/cu.b"]]  # b appears one poll after a
    step = {"i": 0}

    def find_dfu():
        i = min(step["i"], len(seq) - 1)
        step["i"] += 1
        return seq[i]
    d._find_dfu_ports = find_dfu
    # keep the settle short so the test is fast, but real (not a helper mock)
    real_wait = d._wait_new_dfu_ports
    d._wait_new_dfu_ports = lambda before, timeout=2, settle=0.4: real_wait(before, timeout, settle)
    flashed = {}
    d._run_flash = lambda zp, port: flashed.setdefault("flashed", True)
    d.emit = lambda o: None
    err = d.handle({"id": 1, "method": "dfu_flash", "params": {}})
    check("dfu_flash refuses when a SECOND new DFU device enumerates in the settle window (real wait)",
          "error" in err and "more than one new" in err["error"] and "flashed" not in flashed, str(err))

    # (b) a device already stuck in DFU before the reboot is EXCLUDED by the snapshot; only the
    #     new attributable port is flashed. Real _wait_new_dfu_ports over a stateful port list.
    d2 = _dfu_daemon(FakeChameleon(model=0))
    d2._find_cdc_ports = lambda: ["/dev/cu.usbmodem6868"]
    d2._serial = lambda port: FakeSerial(port)
    d2._download_asset = _mock_download(data)
    # the stuck device is present the whole time; the fresh one appears after the reboot
    seq2 = [["/dev/cu.stuckDFU"], ["/dev/cu.stuckDFU", "/dev/cu.freshDFU"]]
    step2 = {"i": 0}

    def find_dfu2():
        i = min(step2["i"], len(seq2) - 1)
        step2["i"] += 1
        return seq2[i]
    d2._find_dfu_ports = find_dfu2
    real_wait2 = d2._wait_new_dfu_ports
    d2._wait_new_dfu_ports = lambda before, timeout=2, settle=0.4: real_wait2(before, timeout, settle)
    flashed2 = {}
    d2._run_flash = lambda zp, port: flashed2.update(port=port)
    d2.emit = lambda o: None
    r2 = d2.dfu_flash({})
    check("dfu_flash flashes ONLY the new attributable port, not the pre-existing stuck one",
          r2["flashed"] is True and flashed2.get("port") == "/dev/cu.freshDFU", str(flashed2))


# --------------------------------------------------------------------------
# 33d. LATE-FIRST settle boundary (fix 1): the first new DFU port appears NEAR the discovery
#      deadline; the settle window must still be observed IN FULL past the deadline so a second
#      device enumerating shortly after is caught. Deterministic via a fake clock.
# --------------------------------------------------------------------------
def test_cham_dfu_settle_past_deadline(check):
    d = _dfu_daemon(FakeChameleon())
    fc = _FakeClock()
    real = chameleon_d.time

    def find_dfu():
        el = fc.t
        if el < 1.9:                       # first appears at ~1.9s, just under the 2.0s timeout
            return []
        if el < 2.2:                       # second appears ~0.3s later at ~2.2s, PAST the deadline
            return ["/dev/cu.a"]
        return ["/dev/cu.a", "/dev/cu.b"]
    d._find_dfu_ports = find_dfu
    chameleon_d.time = fc
    try:
        r = d._wait_new_dfu_ports([], timeout=2.0, settle=1.0)
    finally:
        chameleon_d.time = real
    check("a late-first port still gets the FULL settle window, so the delayed second is caught",
          set(r) == {"/dev/cu.a", "/dev/cu.b"}, str(r))

    # and the happy case: a lone late-first port (no second) returns exactly it.
    d2 = _dfu_daemon(FakeChameleon())
    fc2 = _FakeClock()

    def find_one():
        return [] if fc2.t < 1.9 else ["/dev/cu.only"]
    d2._find_dfu_ports = find_one
    chameleon_d.time = fc2
    try:
        r2 = d2._wait_new_dfu_ports([], timeout=2.0, settle=1.0)
    finally:
        chameleon_d.time = real
    check("a lone late-first port returns exactly one after its full settle window",
          r2 == ["/dev/cu.only"], str(r2))


# --------------------------------------------------------------------------
# 37. EOF never abandons a flash (finding 2): a flash that is WRITING when stdin EOFs
#     forces run() to join UNBOUNDED (not the bounded window), so the flasher is never
#     torn down mid-write. EOF_JOIN_TIMEOUT is shrunk so bounded != unbounded is fast to see.
# --------------------------------------------------------------------------
def test_cham_dfu_eof_waits_for_flash(check):
    d = _dfu_daemon(FakeChameleon(model=0))
    d.EOF_JOIN_TIMEOUT = 0.2                        # a wrong (bounded) join would return in 0.2s
    d._find_cdc_ports = lambda: ["/dev/cu.usbmodem6868"]
    d._find_dfu_ports = lambda: []
    d._serial = lambda port: FakeSerial(port)
    d._download_asset = _mock_download(_good_pkg_bytes())
    d._wait_new_dfu_ports = lambda before, timeout=20: ["/dev/cu.dfu"]
    started = threading.Event()                     # the flash write has begun (_flashing set)
    release = threading.Event()                     # let the flash finish

    def slow_flash(zp, port):
        started.set()
        release.wait(5)                            # hold the committed write open
    d._run_flash = slow_flash
    d.emit = lambda o: None
    # Hold the stream open until the write has begun, so EOF (and its cancel) fires only AFTER
    # the flash is committed - the mid-write kill the guard must prevent.
    stream = _GatedStream(['{"id": 1, "method": "dfu_flash", "params": {}}\n'], started)
    runner = threading.Thread(target=lambda: d.run(stream))
    runner.start()
    ok_started = started.wait(3)                    # worker committed to the (blocked) write
    runner.join(timeout=0.6)                        # 0.6s >> the 0.2s bounded window
    check("EOF UNBOUNDED-joins a committed flash (does not abandon it after the bounded window)",
          ok_started and runner.is_alive() and d._flashing.is_set(),
          "run() returned while the flash was still writing")
    release.set()
    runner.join(timeout=3)
    check("run() tears down only once the committed flash completes", not runner.is_alive())


# --------------------------------------------------------------------------
# 38. EOF-during-DISPATCH race (finding 2c): EOF lands while dfu_flash is in its PRE-flash
#     phase - _flashing is not set yet, but _flash_pending (armed at DISPATCH) must still
#     force the unbounded join, so the imminent flash is not abandoned.
# --------------------------------------------------------------------------
def test_cham_dfu_eof_dispatch_race(check):
    d = _dfu_daemon(FakeChameleon(model=0))
    d.EOF_JOIN_TIMEOUT = 0.2
    at_wait = threading.Event()                     # worker reached the PRE-flash wait step
    release = threading.Event()
    d._find_cdc_ports = lambda: ["/dev/cu.usbmodem6868"]
    d._find_dfu_ports = lambda: []
    d._serial = lambda port: FakeSerial(port)
    d._download_asset = _mock_download(_good_pkg_bytes())

    def blocking_wait(before, timeout=20):
        at_wait.set()                              # pre-flash phase: _flashing NOT set yet
        release.wait(5)
        return ["/dev/cu.dfu"]
    d._wait_new_dfu_ports = blocking_wait
    d._run_flash = lambda zp, port: None
    d.emit = lambda o: None
    # Hold EOF until the worker is in the pre-flash wait, so EOF fires while _flash_pending
    # (dispatch-armed) is set but _flashing is not - the race the dispatch guard closes.
    stream = _GatedStream(['{"id": 1, "method": "dfu_flash", "params": {}}\n'], at_wait)
    runner = threading.Thread(target=lambda: d.run(stream))
    runner.start()
    ok_wait = at_wait.wait(3)
    check("pre-flash phase: _flashing not set yet but _flash_pending is (dispatch-armed guard)",
          ok_wait and not d._flashing.is_set() and d._flash_pending.is_set(),
          "dispatch guard not armed during the pre-flash phase")
    runner.join(timeout=0.6)                        # 0.6s >> the 0.2s bounded window
    check("EOF unbounded-joins a DISPATCHED-but-not-yet-writing flash (via _flash_pending)",
          runner.is_alive(), "run() abandoned a dispatched flash during the pre-flash phase")
    release.set()
    runner.join(timeout=3)
    # The EOF cancel safely aborts a not-yet-writing flash; the point proven is that run()
    # WAITED for the worker (unbounded) rather than exiting mid-approach to the write.
    check("run() waits out the dispatched flash then tears down (never abandons it)",
          not runner.is_alive(), "run() did not complete after the dispatched flash resolved")


# --------------------------------------------------------------------------
# 35. cancel THROUGH run(): a cancel line delivered while a dfu_flash op is queued must
#     stop it before the flash write - exercising the real dispatch, not a pre-set flag.
# --------------------------------------------------------------------------
def test_cham_dfu_cancel_through_run(check):
    d = _dfu_daemon(FakeChameleon(model=0))
    d._find_cdc_ports = lambda: ["/dev/cu.usbmodem6868"]
    d._find_dfu_ports = lambda: []
    d._serial = lambda port: FakeSerial(port)
    d._download_asset = _mock_download(_good_pkg_bytes())
    # Block the op at the wait step until the cancel lands, so the cancel is guaranteed to
    # arrive before the (never-reached) flash write; then the post-wait cancel check fires.
    d._send_enter_dfu = lambda port: None

    def wait_new(before, timeout=20):
        d._cancel.wait(2)                          # hold until the cancel line is handled
        return ["/dev/cu.dfu"]
    d._wait_new_dfu_ports = wait_new
    ran = {}
    d._run_flash = lambda zp, port: ran.setdefault("flashed", True)
    out = []
    d.emit = lambda o: out.append(o)
    d.run(io.StringIO('{"id": 1, "method": "dfu_flash", "params": {}}\n'
                      '{"id": 2, "method": "cancel"}\n'))
    ans = next((o for o in out if o.get("id") == 1), None)
    check("a cancel routed THROUGH run() stops a queued dfu_flash before the flash write",
          ans is not None and ans["result"].get("cancelled") is True and "flashed" not in ran,
          str(ans))


TESTS = [test_cham_info, test_cham_slots, test_cham_slot_select, test_cham_poll,
         test_cham_read_block, test_cham_decode, test_cham_decode_partial,
         test_cham_decode_nocard, test_cham_decode_swap, test_cham_decode_user_key,
         test_cham_transport_wedge, test_cham_dispatch,
         test_cham_decode_nested_chain, test_cham_decode_darkside_chain,
         test_cham_decode_static_chain, test_cham_decode_hard_prng,
         test_cham_decode_cancel, test_cham_attack_budget_guard,
         test_cham_slot_config, test_cham_emulate_mode, test_cham_emulate_load,
         test_cham_emu_read, test_cham_magic_write, test_cham_magic_write_guards,
         test_cham_magic_write_midswap, test_cham_magic_write_trailer_keys,
         test_cham_dfu_asset, test_cham_dfu_norm_model, test_cham_dfu_port_discovery,
         test_cham_dfu_enter_bootloader, test_cham_dfu_validate,
         test_cham_dfu_check, test_cham_dfu_flash_runner, test_cham_dfu_flasher_resolve,
         test_cham_dfu_flash_e2e,
         test_cham_dfu_flash_safety, test_cham_dfu_download_checks,
         test_cham_dfu_identity_binding, test_cham_dfu_settle_past_deadline,
         test_cham_dfu_cancel_through_run,
         test_cham_dfu_eof_waits_for_flash, test_cham_dfu_eof_dispatch_race]


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
