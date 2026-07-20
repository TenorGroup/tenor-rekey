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
import time
import queue
import threading

# Vendored upstream engine (GPLv3, RfidResearchGroup/ChameleonUltra). Imports on a
# bare interpreter - serial/colorama/prompt_toolkit are optional in the package.
from chameleon.chameleon_com import ChameleonCom, NotOpenException, OpenFailException
from chameleon.chameleon_cmd import ChameleonCMD
from chameleon.chameleon_enum import (SlotNumber, TagSpecificType, TagSenseType, MfcKeyType,
                                      Status, MifareClassicPrngType, MifareClassicDarksideStatus)
from chameleon.chameleon_utils import UnexpectedResponseError

# Host-side crackers over the vendored C tools (firmware acquires nonces, host
# cracks them). Optional: if the binaries are not built, decode degrades to the
# on-device dictionary check and never crashes.
try:
    import chameleon_crack as crack
except Exception:                        # pragma: no cover - import guard
    crack = None

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

# Attack-stage budget. The on-device dictionary check is fast; the nonce-cracking
# attacks (nested/darkside) are the slow part, so the wall-clock budget guards them
# (overridable per-call via params.max_seconds). Mirrors x7d's runaway watchdog.
DEFAULT_ATTACK_SECONDS = 120
# Darkside collects one acquisition per round and retries until the crack yields a
# key or the parity-zero intersection converges; bounded by rounds AND the budget.
DARKSIDE_MAX_ROUNDS = 24
DARKSIDE_SYNC_MAX = 30                    # firmware sync attempts per round (CLI default)
DARKSIDE_TARGET_BLOCK = 3                # sector 0 trailer, KeyA - the autopwn foothold

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


def _resolve_type(t):
    """A tag type given as an enum NAME ('MIFARE_1024') or an int -> TagSpecificType.
    Raises (KeyError / ValueError, surfaced as a clean error envelope) on an unknown
    type rather than guessing one."""
    if isinstance(t, str):
        return TagSpecificType[t]
    return TagSpecificType(int(t))


def _sense(s):
    """The slot field a method targets: 'lf' -> TagSenseType.LF, anything else -> HF
    (the common case; slot_* callers pass an explicit 'hf'/'lf')."""
    return TagSenseType.LF if str(s).lower() == "lf" else TagSenseType.HF


def _capabilities(model):
    """The capability manifest the shell reads to gate panels (SPEC 2.3). Built
    from the device model, not hardcoded: model 0 = Ultra, 1 = Lite. The Lite has
    the same 8 slots + emulation + LF + DFU but no HF/LF reader, so reader-mode
    attacks and sniffing do not apply (P0 default; confirm the Lite on hardware)."""
    caps = {
        "slots": 8, "emulate": True, "lf": True, "dfu": True, "sniff": True,
        # hardnested is intentionally omitted: the device supports it but the host
        # cracker is not built yet, so the tool cannot deliver it. Advertise only
        # what decode() can actually run; add "hardnested" back when it is built.
        "attacks": ["dict", "nested", "staticNested", "darkside"],
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
    METHODS = ("info", "poll", "slots_list", "slot_select", "mf_read_block",
               "decode", "cancel",
               "slot_set_type", "slot_enable", "slot_nick", "slot_save",
               "emulate_mode", "emulate_load", "emu_read", "magic_write")

    # Long ops whose cancel window is armed (the flag cleared) at DISPATCH, so a
    # cancel that lands before the worker starts the op still targets it and a stale
    # cancel from a prior op cannot leak in (mirrors x7d).
    CANCELLABLE = ("decode",)

    def __init__(self, learned=None, port=None, cracker=crack):
        self.com = None
        self.cmd = None                  # the ChameleonCMD command layer (or a fake)
        self._port = port
        self._reader_mode = None         # cached: True once the device is in reader mode
        self.crack = cracker             # host-side crackers (injectable for tests)
        self._cancel = threading.Event()  # cooperative abort for the long decode
        self._emit_lock = threading.Lock()  # serialize stdout across worker + reader
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
        with self._emit_lock:
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

    # ---- slot library (Chameleon-only; the shell gates on capabilities.slots) --

    def slot_set_type(self, p):
        """Set a slot's emulated tag type: set_slot_tag_type (RAM) + set_slot_data_default
        (default data, persisted on the next save). `slot` is 0-based (matches
        slots_list); `type` is a TagSpecificType name or value."""
        c = self._connect(p.get("port"))
        slot = int(p["slot"])
        tt = _resolve_type(p["type"])
        sn = SlotNumber.from_fw(slot)
        c.set_slot_tag_type(sn, tt)
        c.set_slot_data_default(sn, tt)
        return {"slot": slot, "type": tt.name}

    def slot_enable(self, p):
        """Enable / disable a slot's HF or LF field (set_slot_enable). `slot` 0-based,
        `sense` 'hf'/'lf'."""
        c = self._connect(p.get("port"))
        slot = int(p["slot"])
        sense = str(p.get("sense", "hf")).lower()
        enabled = bool(p["enabled"])
        c.set_slot_enable(SlotNumber.from_fw(slot), _sense(sense), enabled)
        return {"slot": slot, "sense": sense, "enabled": enabled}

    def slot_nick(self, p):
        """Get or set a slot's nickname. With `name` -> set_slot_tag_nick; without ->
        get_slot_tag_nick (an unset nick is '' rather than an error)."""
        c = self._connect(p.get("port"))
        slot = int(p["slot"])
        sense = str(p.get("sense", "hf")).lower()
        st = _sense(sense)
        sn = SlotNumber.from_fw(slot)
        name = p.get("name")
        if name is not None:
            c.set_slot_tag_nick(sn, st, str(name))
            nick = str(name)
        else:
            try:
                nick = c.get_slot_tag_nick(sn, st)
            except UnexpectedResponseError:
                nick = ""                # no nick stored on this slot/field
        return {"slot": slot, "sense": sense, "nick": nick}

    def slot_save(self, p):
        """Persist the current slot configuration + data to flash (slot_data_config_save)."""
        c = self._connect(p.get("port"))
        c.slot_data_config_save()
        return {"saved": True}

    # ---- emulate: reader<->tag toggle, load a dump into a slot, read it back ----

    def emulate_mode(self, p):
        """Switch the device between reader mode (`reader:true`, scans cards) and
        tag/emulate mode (`reader:false`, presents the active slot). Keeps the cached
        reader flag honest so the next reader op (poll/decode) re-arms it correctly."""
        c = self._connect(p.get("port"))
        reader = bool(p.get("reader", False))
        c.set_device_reader_mode(reader)
        self._reader_mode = reader
        return {"reader": reader}

    def emulate_load(self, p):
        """Load a full dump into the ACTIVE slot's HF (MIFARE Classic) emulator. When
        block 0 IS in the dump, turn on block-0 anti-collision so the emulated card
        presents the dump's own UID/SAK/ATQA (which live in its block 0); when block 0 is
        ABSENT we leave the mode off so the emulator keeps its own identity rather than
        derive it from a zero/stale block 0 (surfaced as `block0: false`). Mirrors the GUI
        slot-load: chunked mf1_write_emu_block_data over contiguous runs. A sparse dump
        (unread sectors omitted) loads in runs, so a hole is never written as a fabricated
        zero block. `blocks`: {block-index: hex}."""
        c = self._connect(p.get("port"))
        blocks = p.get("blocks") or {}
        items = sorted((int(k), bytes.fromhex(v.replace(" ", "")))
                       for k, v in blocks.items() if v)
        has_block0 = any(blk == 0 for blk, _ in items)
        written, run_start, run_buf, prev = 0, None, bytearray(), None
        for blk, data in items:
            if prev is None or blk != prev + 1:
                if run_start is not None:
                    self._flush_emu(c, run_start, run_buf)
                run_start, run_buf = blk, bytearray()
            run_buf += data
            written += 1
            prev = blk
        if run_start is not None:
            self._flush_emu(c, run_start, run_buf)
        # Only derive the emulated identity from block 0 when it was actually loaded.
        if has_block0:
            c.mf1_set_block_anti_coll_mode(True)
        return {"blocks": written, "loaded": True, "block0": has_block0}

    @staticmethod
    def _flush_emu(c, start, buf):
        """Write one emulator run in <=8-block (128-byte) chunks (the GUI flush size),
        auto-incrementing from block `start`."""
        CHUNK = 128
        for off in range(0, len(buf), CHUNK):
            c.mf1_write_emu_block_data(start + off // 16, bytes(buf[off:off + CHUNK]))

    def emu_read(self, p):
        """Read the active slot's HF emulator memory back as a block-index -> hex map
        (the shell renders it in the existing sector grid). Read in 32-block chunks (the
        GUI esave size). `count` blocks, default 64 (a 1K image)."""
        c = self._connect(p.get("port"))
        count = int(p.get("count") or 64)
        CHUNK = 32
        blocks, b = {}, 0
        while b < count:
            n = min(CHUNK, count - b)
            data = c.mf1_read_emu_block_data(b, n)
            for i in range(n):
                blocks[str(b + i)] = hx(data[i * 16:(i + 1) * 16])
            b += n
        return {"blocks": blocks, "count": count}

    # ---- magic clone: write a dump onto a magic card on the reader --------------

    def magic_write(self, p):
        """Clone a dump onto a magic (CUID / gen2 / gen1a) card on the reader, mirroring
        the CLI `hf mf clone` flow. Data blocks always; the trailer only when `trailers`
        is set (its access bytes reset to the generic ff0780 so the tag stays writable,
        unless `clone_access`); block 0 only when `uid` is set. `gen` ('gen1a'/'gen2')
        optionally arms the device's matching magic mode, per SPEC 3.2.

        Safety (mirrors x7d write_mfd / format):
        - `target_uid` is REQUIRED and the card is re-pinned to it immediately before
          EVERY block write, so a card swapped in mid-clone (even one with compatible
          keys) never receives the remaining blocks.
        - Every touched sector is auth-preflighted BEFORE the first write; if any cannot
          auth we abort and write nothing, so a mixed-key card is never half-written.
        - A zeroed trailer key slot is substituted with the dump's recovered key (or
          factory FF), never written as 000000.

        Per-block outcome streams as progress; the tally returns in the x7d write shape
        ({present, wrote, failed, error})."""
        c = self._connect(p.get("port"))
        self._ensure_reader(c)
        blocks = p.get("blocks") or {}
        dump_keys = p.get("keys") or {}
        extra_keys = [k.lower() for k in (p.get("extra_keys") or []) if _valid_key_hex(k)]
        write_trailers = bool(p.get("trailers", False))
        write_uid = bool(p.get("uid", False))
        clone_access = bool(p.get("clone_access", False))
        gen = p.get("gen")

        # target_uid is REQUIRED: the clone is pinned to the exact card the user
        # authorised, for the WHOLE write - not merely checked once up front.
        target = (p.get("target_uid") or "").replace(" ", "").lower()
        if not target:
            raise RuntimeError("magic_write requires target_uid")

        try:
            tags = c.hf14a_scan()
        except UnexpectedResponseError:
            return {"present": False}
        if not tags:
            return {"present": False}
        if hx(tags[0]["uid"]).replace(" ", "") != target:
            return {"present": True, "wrote": 0, "failed": [],
                    "error": "card changed, not written"}
        n = sector_count(tags[0]["sak"][0])

        # The blocks each sector will actually write (data always; block 0 only with uid;
        # the trailer only with trailers). A sector with nothing to write needs no key.
        todo = {}
        for s in range(n):
            tb = trailer_block(s)
            bl = [b for b in range(first_block(s), tb + 1)
                  if not (b == 0 and not write_uid)
                  and not (b == tb and not write_trailers)
                  and blocks.get(str(b))]
            if bl:
                todo[s] = bl

        # Preflight: find an auth key for EVERY sector that will be touched, re-pinning
        # the target between sectors, BEFORE the first write. If any cannot auth we abort
        # and write nothing (never a half-written, mixed-key card). Mirrors x7d format.
        sector_keys = {}
        for s in todo:
            if self._current_uid_norm(c) != target:
                return {"present": True, "wrote": 0, "failed": [],
                        "error": "card changed, not written"}
            found = self._magic_sector_keys(c, s, dump_keys, extra_keys)
            if not found:
                return {"present": True, "wrote": 0, "failed": [],
                        "error": "no key for sector %d - nothing written" % s}
            sector_keys[s] = found

        # SPEC 3.2 mapping. The reliable clone is the block-write flow below; these are
        # the device's emulator magic-mode flags, so they are armed only on explicit
        # request (the default clone leaves them untouched).
        if gen == "gen1a":
            c.mf1_set_gen1a_mode(True)
        elif gen == "gen2":
            c.mf1_set_gen2_mode(True)

        wrote, failed = 0, []
        for s in sorted(todo):
            found = sector_keys[s]
            tb = trailer_block(s)
            dk = dump_keys.get(str(s))
            sub = (bytes.fromhex(dk[1]) if (isinstance(dk, list) and len(dk) == 2
                   and _valid_key_hex(dk[1])) else bytes.fromhex("ffffffffffff"))
            for b in todo[s]:
                # Re-pin the target immediately before each write: a card swapped in mid-
                # clone (even one with compatible keys) must never receive the rest.
                if self._current_uid_norm(c) != target:
                    return {"present": True, "wrote": wrote, "failed": failed,
                            "error": "card changed, not written"}
                raw = bytes.fromhex(blocks[str(b)].replace(" ", ""))
                if len(raw) != 16:
                    failed.append(b)
                    continue
                if b == tb:
                    d = bytearray(raw)
                    if not clone_access:
                        d[6:9] = bytes.fromhex("ff0780")   # keep the tag writable
                    # Never write a 000000 key slot: substitute the dump's recovered key
                    # (or factory FF). Mirrors x7lib/x7d trailer handling.
                    if d[0:6] == bytes(6):
                        d[0:6] = sub
                    if d[10:16] == bytes(6):
                        d[10:16] = sub
                    raw = bytes(d)
                ok = self._magic_write_block(c, b, found, raw)
                self.emit({"event": "progress", "method": "magic_write",
                           "block": b, "ok": ok, "unsafe": None if ok else "write-refused"})
                if ok:
                    wrote += 1
                else:
                    failed.append(b)
        return {"present": True, "wrote": wrote, "failed": failed, "error": None}

    def _current_uid_norm(self, c):
        """Normalised (space-free, lowercase) uid of the card in the field now, or None
        when the field is empty. Used to re-pin the target before each clone write."""
        uid = self._rescan_uid(c)
        return hx(uid).replace(" ", "") if uid is not None else None

    def _magic_sector_keys(self, c, s, dump_keys, extra):
        """Find a KeyA and/or KeyB that authenticate the TARGET card's sector s, drawn
        from the dump's own sector key, the caller's extra keys, then the built-in
        defaults (FF first, so a blank magic card resolves immediately)."""
        cands, seen = [], set()

        def add(k):
            k = (k or "").lower()
            if _valid_key_hex(k) and k not in seen:
                cands.append(k)
                seen.add(k)

        dk = dump_keys.get(str(s))
        if isinstance(dk, list) and len(dk) == 2:
            add(dk[1])
        for k in extra:
            add(k)
        for k in BUILTIN_KEYS:
            add(k)
        fb = first_block(s)
        found = {}
        for keyhex in cands:
            for kt in ("A", "B"):
                if kt in found:
                    continue
                mkt = MfcKeyType.A if kt == "A" else MfcKeyType.B
                try:
                    c.mf1_read_one_block(fb, mkt, bytes.fromhex(keyhex))
                    found[kt] = keyhex
                except UnexpectedResponseError:
                    continue
            if "A" in found and "B" in found:
                break
        return found

    def _magic_write_block(self, c, block, found, data):
        """Write one block, KeyB first then KeyA (mirrors `hf mf clone`). False when no
        key authenticates the write."""
        for kt in ("B", "A"):
            if kt not in found:
                continue
            mkt = MfcKeyType.A if kt == "A" else MfcKeyType.B
            try:
                if c.mf1_write_one_block(block, mkt, bytes.fromhex(found[kt]), data):
                    return True
            except UnexpectedResponseError:
                continue
        return False

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
        """params: user_keys [hex], max_seconds. Full key recovery, mirroring the
        CLI autopwn chain: (1) on-device dictionary check, then for sectors still
        unresolved (2) nested when at least one key is known and the PRNG is weak,
        (3) darkside when NO key is known, (4) static-nested for static-PRNG cards.
        Every newly recovered key is fed back into the check-keys pool so a shared
        key unlocks the other sectors. Then every block of each recovered sector is
        read. Emits progress per stage/sector; respects a wall-clock budget and the
        cooperative cancel flag. Returns the x7d decode shape. No card -> raises (a
        clean error envelope), matching x7d / x7lib."""
        c = self._connect(p.get("port"))
        deadline = time.monotonic() + int(p.get("max_seconds") or DEFAULT_ATTACK_SECONDS)
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

        # STAGE 2: nonce-cracking attacks for sectors the dictionary could not open.
        # The pool is every hex key proven so far; recovered keys are fed back so a
        # shared key unlocks other sectors. Skipped cleanly when no cracker is bundled
        # or the device has no reader-mode attacks (Lite).
        pool = {v for f in sk.values() for v in f.values()}
        cancelled = False
        if self.crack is not None and any(s not in sk for s in range(n)):
            attempts, cancelled = self._recover_attacks(
                c, n, sk, pool, key_bytes, deadline, attempts)

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
                "cancelled": cancelled,
                "blocks": blocks, "keys": keys_out}

    # ---- attack stage: nested / darkside / static-nested -------------------

    def _pick_known(self, sk):
        """A (block, keytype, keyhex) already proven on this card, for use as the
        nested/static-nested KNOWN sector. None if no key is known yet."""
        for s in sorted(sk):
            for kt in ("A", "B"):
                if kt in sk[s]:
                    return trailer_block(s), kt, sk[s][kt]
        return None

    def _verify_candidates(self, c, block, cands, keytypes=("A", "B")):
        """Return the first candidate that authenticates on `block` (crackers emit
        candidates, not proven keys), with the keytype it worked as. (None, None) if
        none auth. A dead port propagates; a plain auth-fail is just a miss."""
        for keyhex in cands:
            kb = bytes.fromhex(keyhex)
            for kt in keytypes:
                mkt = MfcKeyType.A if kt == "A" else MfcKeyType.B
                try:
                    if c.mf1_auth_one_key_block(block, mkt, kb):
                        return keyhex, kt
                except UnexpectedResponseError:
                    continue
        return None, None

    def _absorb_key(self, c, n, sk, pool, key_bytes, keyhex):
        """Fold a freshly proven key into the pool and re-check it against ALL
        sectors on-device, so a shared key (common on hotel cards) unlocks every
        sector it opens. Returns keys tried in the feedback check."""
        if keyhex not in pool:
            pool.add(keyhex)
            key_bytes.append(bytes.fromhex(keyhex))
        new_sk, tried = self._check_keys(c, n, [bytes.fromhex(keyhex)])
        for idx, kb in new_sk.items():
            s = idx // 2
            kt = "A" if idx % 2 == 0 else "B"
            sk.setdefault(s, {})[kt] = kb.hex()
        return tried

    def _nested_recover(self, c, known, target_blk, ttype, deadline):
        """Weak-PRNG nested: prime the nt distance, acquire encrypted nonces, crack
        them host-side. Returns candidate keys (verified by the caller)."""
        blk, kt, keyhex = known
        mkt_known = MfcKeyType.A if kt == "A" else MfcKeyType.B
        mkt_target = MfcKeyType.A if ttype == "A" else MfcKeyType.B
        kb = bytes.fromhex(keyhex)
        dist = c.mf1_detect_nt_dist(blk, mkt_known, kb)          # {'uid','dist'}
        samples = c.mf1_nested_acquire(blk, mkt_known, kb, target_blk, mkt_target)
        if not samples:
            return []
        return self.crack.nested(dist["uid"], dist["dist"], samples)

    def _staticnested_recover(self, c, known, target_blk, ttype):
        """Static-PRNG nested: acquire the static nonce pairs, crack host-side."""
        blk, kt, keyhex = known
        mkt_known = MfcKeyType.A if kt == "A" else MfcKeyType.B
        mkt_target = MfcKeyType.A if ttype == "A" else MfcKeyType.B
        sn = c.mf1_static_nested_acquire(blk, mkt_known, bytes.fromhex(keyhex),
                                         target_blk, mkt_target)
        if not sn or not sn.get("nts"):
            return []
        code = 0x60 if ttype == "A" else 0x61
        return self.crack.staticnested(sn["uid"], code, sn["nts"])

    def _darkside_recover(self, c, deadline):
        """Zero-known-key foothold: acquire darkside leaks on the sector-0 KeyA and
        crack until a candidate authenticates or the budget/rounds run out. Returns
        (keyhex, 'A') on success, (None, None) otherwise. Mirrors the CLI's retry +
        NXP parity-zero reset."""
        items, first = [], True
        for _ in range(DARKSIDE_MAX_ROUNDS):
            if self._cancel.is_set() or time.monotonic() > deadline:
                break
            resp = c.mf1_darkside_acquire(DARKSIDE_TARGET_BLOCK, MfcKeyType.A,
                                          first, DARKSIDE_SYNC_MAX)
            first = False
            if not resp or resp[0] != MifareClassicDarksideStatus.OK:
                break
            obj = resp[1]
            if obj["par"] != 0:                 # NXP workaround: reset accumulation
                items = []
            items.append({"nt1": obj["nt1"], "ks1": obj["ks1"], "par": obj["par"],
                          "nr": obj["nr"], "ar": obj["ar"]})
            cands, _ = self.crack.darkside(obj["uid"], items)
            key, kt = self._verify_candidates(c, DARKSIDE_TARGET_BLOCK, cands, ("A",))
            if key:
                return key, kt
        return None, None

    def _recover_attacks(self, c, n, sk, pool, key_bytes, deadline, attempts):
        """Drive the attack chain over the sectors the dictionary left unopened.
        Returns (attempts, cancelled). A sector is 'open' once we hold any key for
        it (A or B), which is all decode needs to read + dump it. A mid-attack card
        swap is caught by the per-sector uid guard in the block-read stage (which
        aborts before any key is learned), so this stage needs no separate guard."""
        cancelled = False
        # PRNG class decides which nested variant applies; if the device has no
        # reader-mode attacks (Lite) this raises and we skip the whole stage.
        try:
            prng = int(c.mf1_detect_prng())
        except (UnexpectedResponseError,) + _DEAD:
            return attempts, cancelled

        def unresolved():
            return [s for s in range(n) if s not in sk]

        # No key at all: darkside a foothold before any nested attack can run (the
        # autopwn order). Its key is fed back, which may open shared sectors.
        if not pool and unresolved():
            self.emit({"event": "progress", "method": "decode", "stage": "darkside",
                       "sector": DARKSIDE_TARGET_BLOCK // 4})
            key, _kt = self._darkside_recover(c, deadline)
            if key:
                attempts += self._absorb_key(c, n, sk, pool, key_bytes, key)

        for s in list(unresolved()):
            if self._cancel.is_set() or time.monotonic() > deadline:
                cancelled = True
                break
            if s in sk:                          # opened by an earlier feedback merge
                continue
            known = self._pick_known(sk)
            target_blk = trailer_block(s)
            if prng == MifareClassicPrngType.HARD:
                # Hard-PRNG needs the hardnested cracker (not built in P1); report it
                # and stop - no light attack can open these sectors.
                self.emit({"event": "progress", "method": "decode",
                           "stage": "hardnested", "sector": s, "supported": False})
                break
            if known is None:
                break                            # darkside failed; nothing to nest from
            stage = "nested" if prng == MifareClassicPrngType.WEAK else "staticNested"
            self.emit({"event": "progress", "method": "decode", "stage": stage,
                       "sector": s, "known_block": known[0]})
            try:
                if prng == MifareClassicPrngType.WEAK:
                    cands = self._nested_recover(c, known, target_blk, "A", deadline)
                else:
                    cands = self._staticnested_recover(c, known, target_blk, "A")
            except UnexpectedResponseError:
                cands = []                       # acquisition faulted: treat as a miss
            key, _kt = self._verify_candidates(c, target_blk, cands, ("A", "B"))
            if key:
                attempts += self._absorb_key(c, n, sk, pool, key_bytes, key)
        return attempts, cancelled

    def cancel(self, p):
        """Cooperative abort: trip the flag the decode attack loop watches so the
        shell can stop a long recovery WITHOUT killing the daemon; decode then
        returns whatever it recovered so far. Handled inline by run() (off the
        worker) so it lands while decode is still running."""
        self._cancel.set()
        return {"cancelled": True}

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

    def run(self, stream=None):
        # A worker thread runs requests one at a time (the device is a single command
        # stream, so ops never overlap) while THIS thread keeps reading stdin. A
        # `cancel` arriving mid-decode is handled inline - off the worker - so it
        # trips the flag the attack loop watches and the shell can abort a long
        # recovery without killing the daemon; every other request is serialized.
        if stream is None:
            stream = sys.stdin
        q = queue.Queue()

        def worker():
            while True:
                req = q.get()
                if req is None:
                    return
                self.emit(self.handle(req))

        t = threading.Thread(target=worker, daemon=True)
        t.start()
        for line in stream:
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
            except ValueError as e:
                self.emit({"error": "bad json: %s" % e})
                continue
            method = req.get("method")
            if method == "cancel":
                self.emit(self.handle(req))
            else:
                if method in self.CANCELLABLE:
                    # Arm a fresh cancel window for THIS op at dispatch, before any
                    # later cancel line is read - so a cancel that lands before the
                    # worker starts the op still cancels it, and a stale cancel from a
                    # prior op cannot leak in. The op body never clears the flag.
                    self._cancel.clear()
                q.put(req)
        # EOF: abort any in-flight op and join the worker (bounded). Only drop the
        # handle if the worker actually stopped, so close() cannot race a serial
        # read still in flight on the worker thread (matches x7d's EOF guard). If
        # the worker is still blocked, leave the handle for process-exit cleanup.
        self._cancel.set()
        q.put(None)
        t.join(timeout=5)
        if not t.is_alive():
            self._drop()


if __name__ == "__main__":
    # Protect the protocol channel: the daemon captured the real stdout at
    # construction; redirect sys.stdout to stderr so any stray library print()
    # (the vendored ChameleonCom prints on frame errors + verbose logging) goes
    # to stderr and never interleaves with the newline-JSON on stdout.
    _daemon = Daemon()
    sys.stdout = sys.stderr
    _daemon.run()
