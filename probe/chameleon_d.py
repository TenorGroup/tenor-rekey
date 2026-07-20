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
import os
import re
import json
import time
import queue
import struct
import shutil
import hashlib
import zipfile
import tempfile
import threading
import subprocess
import collections
import importlib.util

# Vendored upstream engine (GPLv3, RfidResearchGroup/ChameleonUltra). Imports on a
# bare interpreter - serial/colorama/prompt_toolkit are optional in the package.
from chameleon.chameleon_com import ChameleonCom, NotOpenException, OpenFailException
from chameleon.chameleon_cmd import ChameleonCMD
from chameleon.chameleon_enum import (SlotNumber, TagSpecificType, TagSenseType, MfcKeyType,
                                      Status, MifareClassicPrngType, MifareClassicDarksideStatus,
                                      HIDFormat)
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

# Firmware update (Nordic Secure DFU). In normal run the device is CDC VID 0x6868;
# once rebooted into the bootloader it re-enumerates as VID 0x1915 / PID 0x521f. The
# exact 10-byte ENTER_BOOTLOADER frame (cmd 0x03f2 = 1010) is taken verbatim from the
# upstream resource/tools/enter_dfu.py: SOF 0x11, header-LRC 0xef, cmd 0x03 0xf2,
# status 0x00 0x00, length 0x00 0x00, header-LRC 0x0b, data-LRC 0x00.
DFU_VID = 0x1915
DFU_PID = 0x521f
DFU_ENTER_FRAME = b"\x11\xef\x03\xf2\x00\x00\x00\x00\x0b\x00"
# Seconds to wait for the bootloader port to re-appear after the enter-DFU write.
DFU_WAIT_SECONDS = 20
# After the first new DFU port appears, keep watching this long for a SECOND one to enumerate
# (USB re-enum is sub-second, so a second appearing within this settle window means another
# device is in DFU - the target is then ambiguous and the flash is refused).
DFU_SETTLE_SECONDS = 2.0
# Firmware source (v1 is DOWNLOAD-ONLY): the model-specific application-only asset from the
# official RfidResearchGroup/ChameleonUltra RELEASES. No local files, no nightlies, no CI
# workflow artifacts, no arbitrary URLs - only the official release asset for the model.
GITHUB_RELEASES = "https://api.github.com/repos/RfidResearchGroup/ChameleonUltra/releases"
# Per-asset API endpoint. Downloading through the pinned asset ID (with Accept:
# application/octet-stream) fetches the EXACT asset resolved during this op, so deleting +
# replacing an asset under the same tag/filename between resolve and download cannot swap
# the bytes (browser_download_url would follow the new asset).
GITHUB_ASSET = "https://api.github.com/repos/RfidResearchGroup/ChameleonUltra/releases/assets/%s"
# Hard cap on the firmware download (the app image is well under 1 MB; this bounds a
# truncated/oversized/hostile response). A download that exceeds it is refused.
MAX_FIRMWARE_BYTES = 16 * 1024 * 1024
# Nordic dfu-cc Hash.hash_type enum -> hashlib algorithm (mirrors the GUI validateFiles:
# SHA128 -> sha1, SHA256 -> sha256, SHA512 -> sha512). CRC / NO_HASH are not accepted.
_HASH_ALGO = {2: "sha1", 3: "sha256", 4: "sha512"}
# Nordic dfu-cc FwType.APPLICATION - the only image type this tool will flash (a
# SOFTDEVICE / BOOTLOADER / SOFTDEVICE_BOOTLOADER declared type is a full package).
_FWTYPE_APPLICATION = 0
# Filenames that mark a FULL package (bootloader + softdevice). An app-only package
# ships exactly the application pair (+ manifest); anything naming a bootloader or
# softdevice image is a full-dfu zip, which can BRICK on a mid-flash failure.
_FULL_MARKERS = ("bootloader", "softdevice", "sd_bl")
# Shown when the running firmware cannot be reached to trigger the reboot-into-DFU.
_MANUAL_FALLBACK = ("power the device off, hold button B while plugging in USB "
                    "(LEDs 4 and 5 blink = bootloader), then run the update again.")

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

# Hardnested (hard-PRNG / MFC Ev1) collects encrypted nonces on-device until the full
# 256-value nt_enc first-byte (MSB) distribution is seen - the coverage the host cracker
# needs. Each acquire is slow, so the loop is bounded by this run cap AND the wall-clock
# budget + cooperative cancel (mirrors the CLI's max_runs, but the budget is the real
# guard). The crack subprocess is itself capped by the remaining budget.
HARDNESTED_MAX_RUNS = 200                 # CLI default max_runs
HARDNESTED_MSB_TARGET = 256               # unique nt_enc MSBs = a complete distribution

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


def _is_hex(s, n):
    """True when `s` is exactly `n` lowercase/uppercase hex chars (used to validate a
    sha256 digest string from the releases API before trusting it)."""
    return isinstance(s, str) and len(s) == n and all(c in "0123456789abcdefABCDEF" for c in s)


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


# ---- Ultralight / NTAG geometry (kept local, mirrors the reference GUI's
# mifare_ultralight/general.dart) so read_ntag can size a dump per chip type and the
# slot-emulate path can refuse a non-UL/NTAG type. -----------------------------------

# The UL/NTAG HF TagSpecificType set (>= 1100). A slot may only be UL/NTAG-emulated
# with one of these; any other HF type is refused by emulate_load_ntag.
_NTAG_UL_TYPES = frozenset({
    TagSpecificType.NTAG_210, TagSpecificType.NTAG_212, TagSpecificType.NTAG_213,
    TagSpecificType.NTAG_215, TagSpecificType.NTAG_216, TagSpecificType.MF0ICU1,
    TagSpecificType.MF0ICU2, TagSpecificType.MF0UL11, TagSpecificType.MF0UL21,
})

# Page count per chip type (4-byte pages). Values from the NXP datasheets, matching
# the GUI's mfUltralightGetPagesCount.
_UL_PAGE_COUNT = {
    TagSpecificType.MF0ICU1: 16,             # Mifare Ultralight
    TagSpecificType.MF0ICU2: 48,             # Mifare Ultralight C
    TagSpecificType.MF0UL11: 20,             # Ultralight EV1 (640 bit)
    TagSpecificType.MF0UL21: 41,             # Ultralight EV1 (1312 bit)
    TagSpecificType.NTAG_210: 20,
    TagSpecificType.NTAG_212: 41,
    TagSpecificType.NTAG_213: 45,
    TagSpecificType.NTAG_215: 135,
    TagSpecificType.NTAG_216: 231,
}


def _ultralight_type(version):
    """UL/NTAG TagSpecificType inferred from the 8-byte GET_VERSION (byte 6 = storage size),
    mirroring the GUI's mfUltralightGetType. A short/empty version (a plain UL that NAKs
    GET_VERSION) -> UNDEFINED so the caller reads via the bounded fallback. An UNRECOGNIZED
    storage byte ALSO -> UNDEFINED (bounded fallback) rather than being forced to a 16-page
    plain Ultralight: a real 135-page tag with an unexpected storage byte must dump fully,
    not truncate to 16 pages."""
    if len(version) < 7:
        return TagSpecificType.UNDEFINED
    b6 = version[6]
    if b6 in (0x0B, 0x00):
        return TagSpecificType.MF0UL11
    if b6 == 0x0E:
        return TagSpecificType.MF0UL21
    if b6 == 0x0F:
        return TagSpecificType.NTAG_213
    if b6 == 0x11:
        return TagSpecificType.NTAG_215
    if b6 == 0x13:
        return TagSpecificType.NTAG_216
    return TagSpecificType.UNDEFINED          # unrecognized storage byte: bounded fallback, never truncate


def _ultralight_pages(tt):
    """Readable-page count for a UL/NTAG type, or 0 when the type is unknown (the caller
    then reads until the tag rolls over / NAKs)."""
    return _UL_PAGE_COUNT.get(tt, 0)


def _ultralight_counters(tt):
    """Number of one-way NFC counters the type exposes (UL EV1 = 3, NTAG21x = 1, else 0)."""
    if tt in (TagSpecificType.MF0UL11, TagSpecificType.MF0UL21):
        return 3
    if tt in (TagSpecificType.NTAG_210, TagSpecificType.NTAG_212, TagSpecificType.NTAG_213,
              TagSpecificType.NTAG_215, TagSpecificType.NTAG_216):
        return 1
    return 0


# LF (125 kHz) slot types with an emulation path in v1: em410x_set_emu_id supports
# EXACTLY these two (5-byte EM410x, 13-byte Electra). Any OTHER LF-sense type (Viking /
# PAC / ioProx / Idteck / Jablotron / HID Prox / the EM410x ASK sub-variants with no
# emu-id support) is out of the v1 LF scope AND has no emulation path, so slot_set_type
# refuses to configure it - it would strand a slot the shell can never fill.
_LF_EMU_TYPES = frozenset({TagSpecificType.EM410X, TagSpecificType.EM410X_ELECTRA})

# The human status string the CLI's @expect_response raises for an empty LF field
# (LF_TAG_NO_FOUND). lf_scan treats ONLY this as "no tag"; any other status (PAR_ERR /
# device-mode / NOT_IMPLEMENTED / INVALID_CMD) surfaces as a real error, not a phantom
# absent. Computed from the enum so it stays in sync with the vendored status strings.
_LF_NO_TAG_MSG = str(Status.LF_TAG_NO_FOUND)


def _is_lf_type(tt):
    """True when a TagSpecificType is an LF-sense type (0 < value < TAG_TYPES_LF_END),
    so HF types (>= 1000) are left unrestricted by the LF-scope gate."""
    return TagSpecificType.UNDEFINED < tt < TagSpecificType.TAG_TYPES_LF_END


def _is_lf_no_tag(exc):
    """True only when an UnexpectedResponseError is the LF_TAG_NO_FOUND status. The
    vendored @expect_response collapses the status to its human string, so we compare
    against that string (there is no status attribute on the exception)."""
    return str(exc) == _LF_NO_TAG_MSG


def _capabilities(model, cracker=None):
    """The capability manifest the shell reads to gate panels (SPEC 2.3). Built
    from the device model, not hardcoded: model 0 = Ultra, 1 = Lite. The Lite has
    the same 8 slots + emulation + DFU but no HF reader, so reader-mode attacks do
    not apply (P0 default; confirm the Lite on hardware)."""
    # hardnested (hard-PRNG / MFC Ev1) IS wired into decode() now, but it can only run
    # when its host cracker binary is actually built. Advertise it DYNAMICALLY - appended
    # to the base attacks only when the cracker reports it present - so the shell never
    # lights up an attack the daemon cannot deliver (mirrors the graceful degrade of the
    # other crackers). `lf` is now TRUE: this daemon exposes lf_scan / lf_write / lf_emu,
    # but ONLY for the protocols listed in lfProtocols (EM410x + HID Prox read, T5577
    # write, EM410x emulate) - the daemon can drive more LF protocols but only these are
    # surfaced, so the shell never lights up an LF protocol without live acceptance. `sniff`
    # stays FALSE until this daemon exposes a sniffer.
    attacks = ["dict", "nested", "staticNested", "darkside"]
    if cracker is not None:
        try:
            if cracker.available("hardnested"):
                attacks.append("hardnested")
        except Exception:                # a cracker with no available() probe: omit it
            pass
    caps = {
        "slots": 8, "emulate": True, "lf": True, "sniff": False, "dfu": True,
        "lfProtocols": ["em410x", "hidprox"],
        "attacks": attacks,
        "writeModes": ["normal", "denied", "deceive", "shadow", "shadowReq"],
    }
    if model != 0:
        # Lite: no reader front-end. It still configures + emulates an EM410x slot (lf stays
        # true so the slot LF controls stay reachable), but it CANNOT read or clone an LF
        # tag, so it advertises NO lf read protocols - the shell hides the read/write panel
        # on an empty lfProtocols. Reader-mode attacks are cleared for the same reason.
        caps["attacks"] = []
        caps["lfProtocols"] = []
    return caps


# ---- firmware DFU package validation (brick-safety) ------------------------
# The signed init packet (application.dat) is a Nordic dfu-cc protobuf. We do not
# pull in a protobuf runtime for one nested field walk; a tiny wire-format reader
# extracts exactly the path the GUI's validateFiles reads:
#   Packet.signed_command(2) -> SignedCommand.command(1) -> Command.init(2)
#     -> InitCommand.hash(8) -> {Hash.hash_type(1), Hash.hash(2)}
# then confirms reversed(stored hash) == the hash of application.bin. This proves the
# package is (a) signed and (b) matches its own image, before anything is flashed.

def _pb_varint(data, i):
    """Read a base-128 varint at offset i; returns (value, next_offset)."""
    shift, result = 0, 0
    while True:
        b = data[i]
        i += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, i
        shift += 7


def _pb_iter(data):
    """Yield (field_number, wire_type, value) for a protobuf message. `value` is the
    raw bytes for length-delimited fields (wire 2) and the int for varints (wire 0);
    fixed 32/64-bit fields are skipped. A malformed buffer raises (surfaced as a clean
    'not a valid init packet' error), never a silent partial parse."""
    i, n = 0, len(data)
    while i < n:
        tag, i = _pb_varint(data, i)
        field, wire = tag >> 3, tag & 7
        if wire == 0:
            val, i = _pb_varint(data, i)
            yield field, wire, val
        elif wire == 2:
            ln, i = _pb_varint(data, i)
            yield field, wire, data[i:i + ln]
            i += ln
        elif wire == 5:
            i += 4
        elif wire == 1:
            i += 8
        else:
            raise ValueError("bad protobuf wire type %d" % wire)


def _pb_single(data, field):
    """The (wire_type, value) for a SINGULAR `field`, or (None, None) if absent. Raises
    ValueError on a DUPLICATE occurrence: for security-relevant singular fields (type,
    sizes, hash), a repeated field could make the host validator and the bootloader read
    different values, so a duplicate is refused rather than silently taking the first."""
    found = None
    for f, w, v in _pb_iter(data):
        if f == field:
            if found is not None:
                raise ValueError("duplicate protobuf field %d" % field)
            found = (w, v)
    return found if found is not None else (None, None)


def _validate_init_packet(dat, bin_data):
    """Confirm the init packet DECLARES an app-only image whose hash matches its image.
    Checks, in order (all fail closed; security-relevant singular fields are read with
    _pb_single so a DUPLICATE field is refused, not silently first-wins):
      - the package carries a signed_command wrapper (the shape of an official release;
        the nRF bootloader is the real signature authority, we do not verify ECDSA here);
      - InitCommand.type is PRESENT and == APPLICATION, and neither sd_size nor bl_size is
        set, so a renamed FULL package (a signed bootloader+softdevice image dropped in as
        an application pair) is refused on its DECLARED type - a MISSING type is refused too;
      - reversed(Hash.hash) equals the hash of `bin_data` (image integrity).
    Raises RuntimeError with a clear reason on anything else; returns {'hash_type', 'fw_type',
    'sd_size', 'bl_size'} on success. NEVER accepts a full / mismatched / untyped image."""
    try:
        _, signed = _pb_single(dat, 2)             # Packet.signed_command
        if signed is None:
            raise RuntimeError("firmware package is not an official signed release "
                               "(no signed init packet)")
        _, command = _pb_single(signed, 1)         # SignedCommand.command
        if command is None:
            raise RuntimeError("firmware init packet has no command")
        _, init = _pb_single(command, 2)           # Command.init
        if init is None:
            raise RuntimeError("firmware package command has no init")
        _, ftype = _pb_single(init, 4)             # InitCommand.type (FwType)
        _, sd_size = _pb_single(init, 5)           # InitCommand.sd_size
        _, bl_size = _pb_single(init, 6)           # InitCommand.bl_size
        _, hashmsg = _pb_single(init, 8)           # InitCommand.hash
        if hashmsg is None:
            raise RuntimeError("firmware init packet carries no image hash")
        _, htype = _pb_single(hashmsg, 1)          # Hash.hash_type
        _, hbytes = _pb_single(hashmsg, 2)         # Hash.hash
    except ValueError as e:                        # duplicate field / truncated / non-protobuf
        raise RuntimeError("firmware init packet is not a valid dfu-cc packet: %s" % e)
    # DECLARED type gate (brick-safety): the type MUST be present AND APPLICATION. A missing
    # type is refused (fail closed - we do not lean on the proto default), as is any nonzero
    # softdevice / bootloader size, so a full package cannot pass by omitting its type.
    if ftype is None:
        raise RuntimeError("firmware init packet declares no image type - refused (brick-safety)")
    if ftype != _FWTYPE_APPLICATION:
        raise RuntimeError("firmware init packet declares a non-application image "
                           "(fw type %r) - full DFU refused (brick-safety)" % ftype)
    if (sd_size or 0) != 0 or (bl_size or 0) != 0:
        raise RuntimeError("firmware init packet declares a softdevice/bootloader image "
                           "(sd_size=%r bl_size=%r) - full DFU refused (brick-safety)"
                           % (sd_size, bl_size))
    algo = _HASH_ALGO.get(htype)
    if algo is None or not hbytes:
        raise RuntimeError("firmware init packet uses an unsupported hash type %r" % htype)
    expected = bytes(reversed(hbytes))             # stored hash is byte-reversed
    actual = hashlib.new(algo, bin_data).digest()
    if expected != actual:
        raise RuntimeError("firmware image hash does not match the init packet")
    return {"hash_type": algo, "fw_type": ftype,
            "sd_size": sd_size or 0, "bl_size": bl_size or 0}


# Transport-dead exceptions: like x7d dropping the handle on OSError, drop the
# Chameleon handle when the port is gone so the next command reconnects cleanly.
# (TimeoutError from send_cmd_sync is an OSError subclass.)
_DEAD = (OSError, NotOpenException, OpenFailException)


class Daemon:
    METHODS = ("info", "poll", "slots_list", "slot_select", "mf_read_block",
               "decode", "read_ntag", "cancel",
               "slot_set_type", "slot_enable", "slot_nick", "slot_save",
               "emulate_mode", "emulate_load", "emulate_load_ntag", "emu_read",
               "magic_write",
               "lf_scan", "lf_write", "lf_emu",
               "dfu_check", "dfu_flash")

    # Long ops whose cancel window is armed (the flag cleared) at DISPATCH, so a
    # cancel that lands before the worker starts the op still targets it and a stale
    # cancel from a prior op cannot leak in (mirrors x7d). dfu_flash honors the flag
    # only BEFORE the flash write begins (never mid-write - that can brick).
    CANCELLABLE = ("decode", "dfu_flash")

    # Bounded join for a NON-flash in-flight op on stdin EOF (a flash in flight is joined
    # unbounded instead). A class attribute so tests can shrink it to prove the flash path
    # takes the unbounded branch rather than this one.
    EOF_JOIN_TIMEOUT = 5.0

    # Settle delay before the LF write read-back verify: a freshly written T5577 needs a
    # moment to re-power and answer, so an immediate re-scan can miss it and report a false
    # unverified. Matches the reference GUI's 500 ms. A class attribute so tests set it to 0.
    LF_SETTLE_SECONDS = 0.5

    def __init__(self, learned=None, port=None, cracker=crack):
        self.com = None
        self.cmd = None                  # the ChameleonCMD command layer (or a fake)
        self._port = port
        self._reader_mode = None         # cached: True once the device is in reader mode
        self.crack = cracker             # host-side crackers (injectable for tests)
        self._cancel = threading.Event()  # cooperative abort for the long decode
        # Two-stage firmware-flash guard, both preventing a mid-flash teardown (brick):
        # `_flash_pending` is armed at DISPATCH of dfu_flash (before any EOF/timeout could
        # pick the bounded join, closing the dispatch race), and `_flashing` marks the
        # committed, uninterruptible write. Either set => EOF/shutdown join UNBOUNDED and
        # SIGTERM/SIGINT are ignored, so the flasher subprocess is never abandoned.
        self._flash_pending = threading.Event()
        self._flashing = threading.Event()
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
                "capabilities": _capabilities(model, self.crack)}

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

    # ---- NTAG / Ultralight read (reader-mode 14a raw transceive) ----------------

    # Consecutive NAKs on an UNKNOWN-size tag that mean the READ has walked off the end of
    # memory (not merely a locked page): the fallback stops and drops this terminating run.
    NTAG_OFF_END_MARGIN = 4
    # Hard cap on any page dump so an oddly-behaving tag cannot spin (NTAG216 = 231 pages).
    NTAG_PAGE_CAP = 256

    @staticmethod
    def _14a_raw(c, cmd, check_crc=True):
        """One reader-mode ISO14443A transceive (auto-select + append CRC), returning the
        tag's response bytes (empty on a NAK / no answer / transient fault). Mirrors the
        reference GUI's send14ARaw defaults; the emulator-independent page read the Ultralight
        scan uses. The undecorated hf14a_raw can raise UnexpectedResponseError (an unsupported
        command / device-mode status), TimeoutError (a slow / mid-read stall), or ValueError
        (a malformed frame) - all are collapsed to empty data so a single unreadable page is
        a null page, not a failed daemon request. A truly dead port raises OSError from the
        transport, which handle() catches to drop + reconnect."""
        opts = {"activate_rf_field": 1, "wait_response": 1, "append_crc": 1,
                "auto_select": 1, "keep_rf_field": 0,
                "check_response_crc": 1 if check_crc else 0}
        try:
            return bytes(c.hf14a_raw(options=opts, resp_timeout_ms=100, data=list(cmd)))
        except (UnexpectedResponseError, TimeoutError, ValueError):
            return b""

    def _dump_pages(self, c, count):
        """Read a UL/NTAG tag's pages via the emulator-independent READ (0x30), keeping the
        first 4 bytes of each 16-byte response. With a KNOWN `count` (from GET_VERSION) read
        exactly that many, marking any unreadable (password-locked) page null but NEVER
        stopping early. With an UNKNOWN count (0), read with a bounded fallback: a NAK marks
        the page null and the dump CONTINUES; the walk stops when a READ rolls over to page 0
        (past end of memory) or a run of consecutive NAKs exceeds NTAG_OFF_END_MARGIN (walked
        off the end) - the terminating off-end null run is then dropped so no phantom pages
        past the real end are reported. Capped at NTAG_PAGE_CAP either way."""
        pages, page0, nak_run, pg = {}, None, 0, 0
        limit = count if count else self.NTAG_PAGE_CAP
        while pg < limit:
            data = self._14a_raw(c, [0x30, pg])           # READ returns 16 bytes (4 pages)
            if len(data) < 4:                             # NAK: password-locked OR past end
                pages[str(pg)] = None
                pg += 1
                if not count:
                    nak_run += 1
                    if nak_run > self.NTAG_OFF_END_MARGIN:
                        for k in range(pg - nak_run, pg):  # drop the off-the-end null run
                            pages.pop(str(k), None)
                        break
                continue
            nak_run = 0
            first = data[:4]
            if pg == 0:
                page0 = first
            elif not count and first == page0:            # rolled over to page 0: past end
                break
            pages[str(pg)] = hx(first)
            pg += 1
        return pages

    def read_ntag(self, p):
        """Dump an NTAG21x / Ultralight (SAK 0x00) as 4-byte pages via reader-mode 14a raw
        transceive, returning the SAME shape as x7d.read_ntag ({present, uid, sak, pages})
        plus the detected `type` and version/signature/counters where the chip exposes them
        (UL EV1 / NTAG21x), so the emulate path can reproduce the exact tag that was read.

        GET_VERSION (0x60) identifies the chip type + page count; an unresolved type reads via
        the bounded fallback (see _dump_pages) rather than truncating. A page that needs
        PWD_AUTH NAKs and is marked null while the dump continues. Reader-mode gated like the
        HF read, so a card op is never issued in tag/emulator mode."""
        c = self._connect(p.get("port"))
        self._ensure_reader(c)
        try:
            tags = c.hf14a_scan()
        except UnexpectedResponseError:
            return {"present": False}
        if not tags:
            return {"present": False}
        t = tags[0]
        version = self._14a_raw(c, [0x60])                # GET_VERSION: 8 bytes on EV1/NTAG21x
        ntype = _ultralight_type(version)
        pages = self._dump_pages(c, _ultralight_pages(ntype))   # 0 count -> bounded fallback
        out = {"present": True, "uid": hx(t["uid"]), "sak": t["sak"][0], "pages": pages}
        if ntype != TagSpecificType.UNDEFINED:
            out["type"] = ntype.name                      # the detected type (carried into emulate)
        if len(version) == 8:
            out["version"] = hx(version)
            sig = self._14a_raw(c, [0x3C, 0x00])          # READ_SIG: EXACTLY 32 bytes when present
            if len(sig) == 32 and any(sig):               # a short NAK (e.g. one byte) is NOT a signature
                out["signature"] = hx(sig)
        # Counters keyed by INDEX so a failed counter is absent-at-its-index, never dropped-
        # and-shifted (a failed counter 0 must not move counter 1's value into slot 0).
        counters = {}
        for i in range(_ultralight_counters(ntype)):
            r = self._14a_raw(c, [0x39, i])               # READ_CNT: 3-byte LE counter value
            if len(r) >= 3:
                counters[str(i)] = r[0] | (r[1] << 8) | (r[2] << 16)
        if counters:
            out["counters"] = counters
        return out

    # ---- slot library (Chameleon-only; the shell gates on capabilities.slots) --

    def slot_set_type(self, p):
        """Set a slot's emulated tag type: set_slot_tag_type (RAM) + set_slot_data_default
        (default data, persisted on the next save). `slot` is 0-based (matches
        slots_list); `type` is a TagSpecificType name or value.

        LF-scope gate: an LF-sense type may ONLY be EM410x / EM410x Electra (the two with an
        emulation path in v1); any other LF type (Viking / PAC / ioProx / Idteck / Jablotron
        / HID Prox, by name OR number) is refused, so a caller cannot configure an
        out-of-scope LF slot the shell can never emulate. HF types are unrestricted."""
        c = self._connect(p.get("port"))
        slot = int(p["slot"])
        tt = _resolve_type(p["type"])
        if _is_lf_type(tt) and tt not in _LF_EMU_TYPES:
            raise RuntimeError("LF slot type %s is out of scope; only EM410X / "
                               "EM410X_ELECTRA can be set on a slot's LF field" % tt.name)
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

    def emulate_load_ntag(self, p):
        """Load a UL/NTAG page dump into the ACTIVE slot's HF emulator as an Ultralight /
        NTAG tag. Mirrors the reference GUI's Ultralight slot-load: set the slot tag type
        (+ default data) to the UL/NTAG type, set the anti-collision (UID/ATQA/SAK), write
        the 4-byte emulator pages over contiguous runs, then set the version / signature /
        counters where present. `type` MUST be a UL/NTAG type (NTAG_213/215/216, MF0UL11/21,
        ...) - any other type is refused, so this path never mis-configures a Classic slot.
        `pages`: {page-index: 4-byte hex}. `uid` (optional, hex): the emulated 7-byte UID;
        when absent it is derived from pages 0-1 if those were loaded.

        This is the UL/NTAG sibling of emulate_load (which handles MIFARE Classic blocks);
        both operate on the active slot the caller selected."""
        c = self._connect(p.get("port"))
        tt = _resolve_type(p["type"])
        if tt not in _NTAG_UL_TYPES:
            raise RuntimeError("emulate_load_ntag needs a UL/NTAG tag type, got %s" % tt.name)
        sn = SlotNumber.from_fw(int(c.get_active_slot()))
        c.set_slot_tag_type(sn, tt)
        c.set_slot_data_default(sn, tt)
        pages = p.get("pages") or {}
        items = sorted((int(k), bytes.fromhex(v.replace(" ", "")))
                       for k, v in pages.items() if v)
        by_index = dict(items)
        # The emulated identity: an explicit `uid` wins; else derive the 7-byte UID from
        # pages 0-1 (page0 = uid[0:3] + BCC0, page1 = uid[3:7]) when both were loaded. NTAG /
        # UL are ATQA 0x0044 / SAK 0x00. Skipped when neither a uid nor page 0/1 is available.
        uid = bytes.fromhex((p.get("uid") or "").replace(" ", "")) if p.get("uid") else b""
        if not uid and 0 in by_index and 1 in by_index:
            uid = by_index[0][:3] + by_index[1][:4]
        uid_set = False
        if uid:
            c.hf14a_set_anti_coll_data(uid, b"\x44\x00", b"\x00", b"")
            uid_set = True
        # Write pages 4 at a time via contiguous runs (mfu_write_emu_page_data takes a
        # multiple of 4 bytes and writes from page_start). A sparse dump (a locked page
        # omitted) loads as separate runs, so a hole is never written as a fabricated page.
        written, run_start, run_buf, prev = 0, None, bytearray(), None
        for pg, data in items:
            if prev is None or pg != prev + 1:
                if run_start is not None:
                    c.mfu_write_emu_page_data(run_start, bytes(run_buf))
                run_start, run_buf = pg, bytearray()
            run_buf += data
            written += 1
            prev = pg
        if run_start is not None:
            c.mfu_write_emu_page_data(run_start, bytes(run_buf))
        # Version / signature / counters (UL EV1 / NTAG21x), best-effort like the GUI, so the
        # emulated tag reproduces the metadata that was read - not the emulator defaults.
        version = (p.get("version") or "").replace(" ", "")
        if version:
            c.mf0_ntag_set_version_data(bytes.fromhex(version))
        signature = (p.get("signature") or "").replace(" ", "")
        if signature:
            c.mf0_ntag_set_signature_data(bytes.fromhex(signature))
        # Counters arrive keyed by index ({index: value}) so each is written to its own
        # emulator counter; a legacy list is still accepted (index = position).
        counters = p.get("counters") or {}
        pairs = (sorted(counters.items(), key=lambda kv: int(kv[0]))
                 if isinstance(counters, dict) else list(enumerate(counters)))
        for idx, val in pairs:
            c.mfu_write_emu_counter_data(int(idx), int(val), True)
        if pairs:
            c.mfu_reset_auth_cnt()
        return {"pages": written, "loaded": True, "type": tt.name, "uid": uid_set}

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

    # ---- LF 125 kHz: EM410x + HID Prox read, T5577 write, EM410x emulate --------
    # NARROWED surface (v1): only the three protocols the owner has hardware-accepted.
    # The vendored engine can drive more LF protocols; those are deliberately NOT
    # exposed here (each exposed protocol needs its own live acceptance).

    @staticmethod
    def _hid_id_bytes(fmt, fc, cn1, cn2, il, oem):
        """Re-pack the 6 parsed HID Prox fields into the 13-byte id the firmware read /
        write / emulate all speak (`>BIBIBH`, matching the upstream CLI)."""
        return struct.pack(">BIBIBH", fmt, fc, cn1, cn2, il, oem)

    @staticmethod
    def _hid_result(kind, parsed):
        """Shape a HID Prox scan/read tuple `(format, fc, cn1, cn2, il, oem)` into the
        wire result: the 13-byte id (hex) the shell echoes back to lf_write / lf_emu, plus
        the human fields (card number is the two halves recombined)."""
        fmt, fc, cn1, cn2, il, oem = parsed
        id_bytes = Daemon._hid_id_bytes(fmt, fc, cn1, cn2, il, oem)
        try:
            fmt_name = HIDFormat(fmt).name
        except ValueError:
            fmt_name = "H%d" % fmt
        return {"present": True, "kind": kind, "id": hx(id_bytes),
                "format": fmt, "formatName": fmt_name,
                "fc": fc, "cn": (cn1 << 32) + cn2, "il": il, "oem": oem}

    def lf_scan(self, p):
        """Read the LF (125 kHz) tag on the reader. Tries EM410x first, then HID Prox
        (the two READ protocols in scope), mirroring the GUI's read order. Reader-mode
        gated like the HF read. Returns {present, kind:'em410x'/'hidprox', id (hex),
        ...fields}; an EMPTY field (LF_TAG_NO_FOUND) -> {present:false}. A genuine fault
        (PAR_ERR / device-mode / NOT_IMPLEMENTED / INVALID_CMD) is surfaced as an error,
        never mistaken for absent. Viking / PAC / ioProx / Idteck are NOT probed here."""
        c = self._connect(p.get("port"))
        self._ensure_reader(c)
        try:
            tag_type, uid = c.em410x_scan()
            return {"present": True, "kind": "em410x",
                    "id": hx(uid), "tagType": _type_name(tag_type)}
        except UnexpectedResponseError as e:
            if not _is_lf_no_tag(e):
                raise                            # a real fault: surface it, do not fall through
        try:
            parsed = c.hidprox_scan(0)           # format 0 = no hint, firmware auto-detects
            return self._hid_result("hidprox", parsed)
        except UnexpectedResponseError as e:
            if not _is_lf_no_tag(e):
                raise                            # a real fault: surface it, not a phantom absent
            return {"present": False}

    def _lf_verify(self, read):
        """Run the read-back verify after an LF write, after a settle delay so a freshly
        written T5577 has re-powered and can answer. `read` returns (ok, note): whether the
        read-back matched, and a note on a mismatch / read fault (None on a clean match).
        A read fault is kept as the note rather than collapsed silently to unverified."""
        if self.LF_SETTLE_SECONDS:
            time.sleep(self.LF_SETTLE_SECONDS)
        try:
            return read()
        except UnexpectedResponseError as e:
            return False, str(e)                 # e.g. LF_TAG_NO_FOUND: the write did not take

    def lf_write(self, p):
        """Write an LF id onto a blank T5577 tag on the reader (the LF clone). `kind` is a
        REQUIRED explicit protocol ('em410x' / 'hidprox') - a destructive endpoint never
        defaults a protocol; `id` is the hex id (an EM410x 5-byte id or 13-byte Electra; a
        HID Prox 13-byte id, as returned by lf_scan). Reader-mode gated. After a settle
        delay the tag is read back and byte-compared, so `verified` reports whether the
        clone took, with `note` carrying the reason on a mismatch / read fault (best-effort,
        mirroring the GUI's write-then-read-back). No card-uid pin: a blank T5577 has no
        stable identity before it is written."""
        c = self._connect(p.get("port"))
        self._ensure_reader(c)
        kind = p.get("kind")
        if kind is None:
            raise RuntimeError("lf_write requires an explicit kind (em410x / hidprox)")
        kind = str(kind).lower()
        id_bytes = bytes.fromhex((p.get("id") or "").replace(" ", ""))
        if kind == "em410x":
            if len(id_bytes) not in (5, 13):
                raise RuntimeError("EM410x id must be 5 bytes (10 hex) or 13 bytes (Electra)")
            c.em410x_write_to_t55xx(id_bytes)

            def read():
                _tt, uid = c.em410x_scan()
                ok = bytes(uid) == id_bytes
                return ok, (None if ok else "read-back mismatch")
            verified, note = self._lf_verify(read)
            return {"wrote": True, "kind": kind, "id": hx(id_bytes),
                    "verified": verified, "note": note}
        if kind == "hidprox":
            if len(id_bytes) != 13:
                raise RuntimeError("HID Prox id must be 13 bytes (26 hex)")
            c.hidprox_write_to_t55xx(id_bytes)

            def read():
                ok = self._hid_id_bytes(*c.hidprox_scan(0)) == id_bytes
                return ok, (None if ok else "read-back mismatch")
            verified, note = self._lf_verify(read)
            return {"wrote": True, "kind": kind, "id": hx(id_bytes),
                    "verified": verified, "note": note}
        raise RuntimeError("unsupported LF kind %r (em410x / hidprox only)" % kind)

    def lf_emu(self, p):
        """Set the ACTIVE slot's EM410x emulation id (`em410x_set_emu_id`), for LF emulate.
        EM410x-only in v1: the firmware requires the active slot's LF field to already be
        EM410x (or Electra) - if it is not, or the id length is wrong, it raises, surfaced
        as a clean error envelope. `id` is the 5-byte EM410x (or 13-byte Electra) hex. Not
        reader-mode gated (a slot config op, not a reader op), mirroring slot_set_type."""
        c = self._connect(p.get("port"))
        id_bytes = bytes.fromhex((p.get("id") or "").replace(" ", ""))
        c.em410x_set_emu_id(id_bytes)
        return {"loaded": True, "id": hx(id_bytes)}

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

    # ---- firmware update (Nordic Secure DFU, app-only, brick-safe) ----------
    # The port / serial / subprocess / network seams are small overridable methods so
    # the whole flow is exercised hardware-free in the tests (FakeSerial + a fake
    # Popen + a stubbed release fetch); the real flash is hardware-gated by the owner.

    @staticmethod
    def _dfu_asset_name(model):
        """The app-only DFU asset for the device model. FAIL-CLOSED: 0 -> Ultra, 1 -> Lite,
        anything else raises - an unexpected / future / wrong-compatible model must never be
        coerced to a firmware (e.g. mapping 'not 0' to Lite would flash Lite onto it)."""
        if model == 0:
            return "ultra-dfu-app.zip"
        if model == 1:
            return "lite-dfu-app.zip"
        raise RuntimeError("unsupported device model %r - refusing to pick firmware" % model)

    @staticmethod
    def _norm_model(m):
        """Normalise a model choice to EXACTLY 0 (Ultra) / 1 (Lite), or None if unspecified.
        Accepts only the exact int 0/1 or the string 'ultra'/'lite' (or '0'/'1'). Anything
        else - 2, -1, 0.5, True/False, other strings - RAISES rather than being coerced, so a
        stray value can never silently pick a firmware (we never guess Ultra vs Lite)."""
        if m is None:
            return None
        if isinstance(m, bool):                  # bool is an int subclass - reject explicitly
            raise RuntimeError("unknown model %r (expected 'ultra' or 'lite')" % m)
        if isinstance(m, str):
            s = m.strip().lower()
            if s in ("ultra", "0"):
                return 0
            if s in ("lite", "1"):
                return 1
            raise RuntimeError("unknown model %r (expected 'ultra' or 'lite')" % m)
        if isinstance(m, int) and m in (0, 1):
            return m
        raise RuntimeError("unknown model %r (expected 'ultra' or 'lite')" % m)

    def _list_ports(self):
        """All serial ports (pyserial is imported lazily so the daemon still loads on a
        bare interpreter). Overridden in tests."""
        try:
            from serial.tools import list_ports
        except ImportError:
            return []
        return list(list_ports.comports())

    def _find_dfu_ports(self):
        """Every serial port of a device in the bootloader (VID 0x1915 / PID 0x521f)."""
        return [p.device for p in self._list_ports()
                if getattr(p, "vid", None) == DFU_VID and getattr(p, "pid", None) == DFU_PID]

    def _find_cdc_ports(self):
        """Every serial port of a Chameleon in normal (CDC) mode (VID 0x6868)."""
        return [p.device for p in self._list_ports()
                if getattr(p, "vid", None) == CHAMELEON_VID]

    def _serial(self, port):
        """Open a raw pyserial port at 115200 (used only to write the enter-DFU frame).
        Lazy import + overridable in tests."""
        import serial
        return serial.Serial(port=port, baudrate=115200)

    def _send_enter_dfu(self, port):
        """Reboot the running firmware into the Nordic bootloader: open the normal CDC
        port, raise DTR, write the exact 10-byte ENTER_BOOTLOADER frame, close (mirrors
        resource/tools/enter_dfu.py). No response is expected - the device re-enumerates."""
        s = self._serial(port)
        try:
            s.dtr = 1
            s.timeout = 0
            s.write(DFU_ENTER_FRAME)
        finally:
            s.close()

    def _wait_new_dfu_ports(self, before, timeout=DFU_WAIT_SECONDS, settle=DFU_SETTLE_SECONDS):
        """Return the set of NEW bootloader ports that appear after the enter-DFU write,
        relative to the snapshot `before` taken BEFORE the reboot (macOS renames the /dev node
        on re-enumeration, so identity is 'a port that was not present before'). We do NOT
        return on the first new port: once one appears at time t, we observe the FULL settle
        window (until t + `settle`) EVEN IF that runs past the original discovery `timeout`, so
        a first port that shows up near the deadline still gets the whole attribution window and
        a SECOND device enumerating milliseconds later is still caught. We accumulate the UNION
        of every new port seen; the caller requires exactly one, so a second new port anywhere in
        the window makes the target ambiguous and the flash is refused. Returns [] if none shows
        within `timeout`."""
        before = set(before)
        discovery_deadline = time.monotonic() + timeout
        seen = set()
        first_at = None
        while True:
            now = time.monotonic()
            # Until the first new port appears, bound by the discovery timeout; after it appears
            # at first_at, bound by the FULL settle window (first_at + settle), regardless of when
            # first_at fell relative to the discovery deadline.
            deadline = discovery_deadline if first_at is None else (first_at + settle)
            if now >= deadline:
                break
            new = set(self._find_dfu_ports()) - before
            seen |= new
            if len(seen) > 1:
                return sorted(seen)              # ambiguous already - stop and let the caller refuse
            if seen and first_at is None:
                first_at = time.monotonic()
            time.sleep(0.1)
        return sorted(seen)

    def _http_json(self, url, timeout=15):
        import urllib.request
        req = urllib.request.Request(
            url, headers={"User-Agent": "tenor-rekey", "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    def _http_download(self, url, dest, max_bytes=MAX_FIRMWARE_BYTES, timeout=180, accept=None):
        """Stream an HTTPS download to `dest`, refusing to exceed `max_bytes` (so a hostile or
        runaway response cannot fill the disk). The initial URL AND the FINAL (post-redirect)
        response URL must both be https - a redirect that downgrades to http is refused, so the
        bytes cannot arrive over plaintext. Returns the number of bytes written."""
        import urllib.request
        if not str(url).lower().startswith("https://"):
            raise RuntimeError("refusing a non-https firmware URL")
        headers = {"User-Agent": "tenor-rekey"}
        if accept:
            headers["Accept"] = accept
        req = urllib.request.Request(url, headers=headers)
        total = 0
        with urllib.request.urlopen(req, timeout=timeout) as r, open(dest, "wb") as f:
            final = r.geturl()
            if not str(final).lower().startswith("https://"):
                raise RuntimeError("firmware download redirected to a non-https URL - refused")
            while True:
                chunk = r.read(65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise RuntimeError("firmware download exceeds the %d-byte size limit - refused"
                                       % max_bytes)
                f.write(chunk)
        return total

    def _latest_release(self, model):
        """The newest OFFICIAL prerelease whose assets include the model-specific app-only
        DFU zip. Returns {'tag','url','size','digest','asset_id'} or None if none is
        published. Releases only (no nightlies / CI artifacts); exact asset-name match."""
        asset = self._dfu_asset_name(model)
        data = self._http_json(GITHUB_RELEASES)
        if not isinstance(data, list):
            msg = data.get("message") if isinstance(data, dict) else "unexpected response"
            raise RuntimeError("GitHub releases: %s" % msg)
        for rel in data:
            if not rel.get("prerelease"):
                continue
            for a in rel.get("assets", []):
                if a.get("name") == asset:
                    return {"tag": rel.get("tag_name") or rel.get("name"),
                            "url": a.get("browser_download_url"),
                            "size": a.get("size"),
                            "digest": a.get("digest"),
                            "asset_id": a.get("id")}
        return None

    def _download_asset(self, rel, dest):
        """Download the PINNED release asset (`rel`, resolved once for this op) to `dest`, all
        checks FAIL CLOSED:
        - pin the exact asset by its ID via the assets API (Accept: application/octet-stream),
          so a delete+replace under the same tag/filename cannot swap the bytes;
        - the asset's declared size MUST be a valid int within the cap, and the bytes written
          MUST equal it (a missing / non-integer / out-of-range size is refused, not skipped);
        - a PRESENT digest MUST be a supported `sha256:<hex>` and match; a malformed or
          unsupported-algorithm digest is refused (never silently ignored). A digest that is
          entirely ABSENT is allowed (the size + asset-ID pin are the completeness guards)."""
        asset_id = rel.get("asset_id")
        if asset_id is None:
            raise RuntimeError("release asset has no id - cannot pin the exact asset (refused)")
        expected = rel.get("size")
        if not (isinstance(expected, int) and not isinstance(expected, bool)
                and 0 < expected <= MAX_FIRMWARE_BYTES):
            raise RuntimeError("release asset has no valid size within the limit - refused")
        url = GITHUB_ASSET % asset_id
        written = self._http_download(url, dest, accept="application/octet-stream")
        if written != expected:
            raise RuntimeError("firmware download is incomplete (%d of %d bytes) - refused"
                               % (written, expected))
        digest = rel.get("digest")
        if digest is not None:
            algo, sep, hexd = str(digest).partition(":")
            hexd = hexd.strip().lower()
            if algo.lower() != "sha256" or not sep or not _is_hex(hexd, 64):
                raise RuntimeError("firmware asset digest is malformed or unsupported (%r) - refused"
                                   % digest)
            actual = self._file_sha(dest)
            if actual.lower() != hexd:
                raise RuntimeError("firmware download digest mismatch (expected %s, got %s) - refused"
                                   % (hexd, actual))
        return written

    def _validate_dfu_zip(self, zip_path):
        """App-only SANITY check on the downloaded OFFICIAL asset (v1 is download-only, so
        this is a sanity gate on a trusted source). Every check FAILS CLOSED:
        - application.dat + application.bin must be present, and no member filename may name a
          bootloader / softdevice image;
        - manifest.json must be PRESENT and its `manifest` object must contain EXACTLY the
          `application` image class and NOTHING else - a softdevice / bootloader /
          softdevice_bootloader entry is refused even if its files dodge the name markers, so
          adafruit-nrfutil (which follows the manifest) can never be steered to a full payload;
          the application entry must also name the standard application.dat/.bin we validate;
        - the application init packet must DECLARE an APPLICATION image whose hash matches.
        Raises on anything unsafe; returns the init-packet info."""
        with zipfile.ZipFile(zip_path) as z:
            names = z.namelist()
            nameset = set(names)
            if "application.dat" not in nameset or "application.bin" not in nameset:
                raise RuntimeError("not a Chameleon application DFU package "
                                   "(missing application.dat / application.bin)")
            for n in names:
                if any(tok in n.lower() for tok in _FULL_MARKERS):
                    raise RuntimeError("this is a FULL DFU package (bootloader + softdevice); "
                                       "only the application-only asset is flashed (brick-safety)")
            if "manifest.json" not in nameset:
                raise RuntimeError("firmware package has no manifest.json; cannot confirm it is "
                                   "application-only (brick-safety)")
            try:
                mani = json.loads(z.read("manifest.json").decode("utf-8"))
                m = mani.get("manifest")
            except (ValueError, UnicodeDecodeError, AttributeError) as e:
                raise RuntimeError("firmware manifest is malformed: %s" % e)
            if not isinstance(m, dict) or "application" not in m:
                raise RuntimeError("firmware manifest declares no application image (brick-safety)")
            extra = sorted(k for k in m if k != "application")
            if extra:
                raise RuntimeError("firmware manifest declares non-application images (%s) - "
                                   "full DFU refused (brick-safety)" % ", ".join(extra))
            app = m.get("application")
            dat_name = app.get("dat_file") if isinstance(app, dict) else None
            bin_name = app.get("bin_file") if isinstance(app, dict) else None
            # The manifest's application must name the exact files we validate, so it cannot
            # point the flasher at a different (renamed full) init packet / image.
            if dat_name != "application.dat" or bin_name != "application.bin":
                raise RuntimeError("firmware manifest application entry does not name the standard "
                                   "application.dat / application.bin (refused)")
            dat = z.read("application.dat")
            binf = z.read("application.bin")
        return _validate_init_packet(dat, binf)

    @staticmethod
    def _flasher_head():
        """Resolve the adafruit-nrfutil flasher WITHOUT relying on PATH. The packaged app spawns
        this daemon with the launchd PATH (/usr/bin:/bin:...), which does NOT include the bundled
        runtime's bin dir, so a bare `["adafruit-nrfutil", ...]` would FileNotFoundError at flash
        time. Resolution order, most-specific first:
          1. the console script next to THIS interpreter (bundle: $RES/python/bin/adafruit-nrfutil;
             also any venv) if present + executable;
          2. else run the importable `nordicsemi` module on this interpreter (the bundle runtime
             has it installed) - `[sys.executable, "-m", "nordicsemi"]`;
          3. else the flasher on PATH (dev) via shutil.which;
          4. else a clear error.
        Returns the argv HEAD (the flasher invocation); the caller appends the dfu arguments."""
        cand = os.path.join(os.path.dirname(sys.executable), "adafruit-nrfutil")
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return [cand]
        if importlib.util.find_spec("nordicsemi") is not None:
            return [sys.executable, "-m", "nordicsemi"]
        found = shutil.which("adafruit-nrfutil")
        if found:
            return [found]
        raise RuntimeError("firmware flasher not found (adafruit-nrfutil / nordicsemi); "
                           "reinstall the app or `pip install adafruit-nrfutil`")

    def _nrfutil_argv(self, zip_path, port):
        """The full adafruit-nrfutil DFU serial command line: the PATH-independent flasher head
        (see _flasher_head) followed by the dfu arguments."""
        return self._flasher_head() + ["dfu", "serial",
                                       "-pkg", zip_path, "-p", port, "-b", "115200"]

    def _popen(self, argv):
        """Spawn the flasher with its progress merged onto one stream (some builds print
        progress on stderr). Overridable in tests."""
        return subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, bufsize=1)

    @staticmethod
    def _flash_percent(line):
        """Extract a 0..100 percent from a flasher output line, or None."""
        m = re.search(r"(\d{1,3})\s*%", line)
        if not m:
            return None
        pct = int(m.group(1))
        return pct if 0 <= pct <= 100 else None

    @staticmethod
    def _file_sha(path):
        """sha256 hexdigest of a file's bytes (used to verify a download against the
        releases API asset digest)."""
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()

    def _run_flash(self, zip_path, port):
        """Run adafruit-nrfutil and stream percent progress as `dfu_flash` events. Raises
        RuntimeError (with the tail of the tool output) on a non-zero exit. This is the
        POINT OF NO CANCEL - only reached once the package is validated and the bootloader
        port is confirmed. Progress emits are best-effort (a broken protocol pipe must not
        stop us DRAINING the flasher, or its stdout pipe fills and it wedges); the finally
        WAITS for the subprocess to fully exit, so the flasher is never left running when
        the caller drops the flash guard."""
        argv = self._nrfutil_argv(zip_path, port)
        self._emit_best_effort({"event": "progress", "method": "dfu_flash",
                                "stage": "flash", "percent": 0})
        proc = self._popen(argv)
        tail = collections.deque(maxlen=20)
        last, code = -1, None
        try:
            if proc.stdout is not None:
                for line in proc.stdout:
                    tail.append(line.rstrip())
                    pct = self._flash_percent(line)
                    if pct is not None and pct != last:
                        last = pct
                        self._emit_best_effort({"event": "progress", "method": "dfu_flash",
                                                "stage": "flash", "percent": pct})
            code = proc.wait()
        except Exception:
            # A streaming glitch or a wait() that raised (e.g. EINTR) must NOT be taken as a
            # failure or (worse) let us return with the child still running; the finally below
            # determines the real exit code once poll() reports the child has actually exited.
            code = None
        finally:
            # Never return while the flasher is still running: an abandoned mid-write child
            # can brick. Keep waiting until poll() actually reports it has exited - a wait()
            # that RAISES is NOT taken as "it exited"; we re-check poll() and wait again. This
            # deliberately blocks (does not give up) rather than clear the flash guard on a
            # still-alive child.
            while proc.poll() is None:
                try:
                    proc.wait()
                except Exception:
                    time.sleep(0.05)
            if code is None:
                code = proc.poll()          # the child has now exited; take its real code
        if code is None:
            raise RuntimeError("firmware flash was interrupted before the flasher reported")
        if code != 0:
            detail = " | ".join(t for t in tail if t)[-300:]
            raise RuntimeError("firmware flash failed (adafruit-nrfutil exit %d): %s"
                               % (code, detail))
        self._emit_best_effort({"event": "progress", "method": "dfu_flash",
                                "stage": "done", "percent": 100})

    def _emit_best_effort(self, obj):
        """emit() that swallows a broken protocol pipe: during a flash the protocol channel
        may be gone, but we must keep draining the flasher rather than abort mid-write."""
        try:
            self.emit(obj)
        except Exception:
            pass

    def dfu_check(self, p):
        """Report the CURRENT firmware (app version + git hash) and the LATEST published
        release tag, so the shell can show "update available". A failed release fetch
        (offline) is not fatal: latest is null and `note` carries the reason."""
        c = self._connect(p.get("port"))
        model = c.get_device_model()
        major, minor = c.get_app_version()
        git = c.get_git_version()
        current = "%d.%d" % (major, minor)
        latest, note = None, None
        try:
            rel = self._latest_release(model)
            latest = rel["tag"] if rel else None
        except Exception as e:
            note = str(e)
        # Heuristic: an update is offered when the latest tag is not already embedded in
        # the running git description (which is e.g. "v2.0.0-3-gdeadbee"). The shell shows
        # both versions plainly, so a false negative just means the user flashes manually.
        update = bool(latest) and (latest not in (git or ""))
        return {"model": "Chameleon Ultra" if model == 0 else "Chameleon Lite",
                "current": current, "git": git, "asset": self._dfu_asset_name(model),
                "latest": latest, "updateAvailable": update, "note": note}

    def dfu_flash(self, p):
        """Update the Chameleon firmware over Nordic Secure DFU. v1 is DOWNLOAD-ONLY: the
        source is ALWAYS the model-specific application-only asset from the official releases
        (no local files, no arbitrary URLs). Params: optional `model` ('ultra'/'lite'), used
        only to recover a device already stuck in DFU whose model cannot be read. Flow, in
        strict brick-safe order:
          1. resolve the target device UNAMBIGUOUSLY. Already in DFU -> require exactly one
             DFU device and an explicit model (never guess). Otherwise bind to exactly one
             connected Chameleon and read its model; SNAPSHOT the DFU ports before rebooting.
          2. resolve the official release ONCE and PIN it; DOWNLOAD the pinned asset into a
             private per-flash temp dir with a complete-transfer + size + digest check.
          3. app-only SANITY check the download (reject a full package).
          4. reboot the bound device (10-byte enter-DFU frame); wait for EXACTLY ONE NEW DFU
             port (a port not in the pre-reboot snapshot) and flash THAT one.
          5. flash with adafruit-nrfutil, streaming percent progress.
        The cooperative cancel is honored ONLY up to the moment the flash write begins; a
        mid-write abort can brick, so it is never checked past step 5. `dfu_flash` is armed as
        flash-pending at DISPATCH (run()), so an EOF/shutdown while it is in flight always
        joins UNBOUNDED and never abandons a flash."""
        supplied_model = self._norm_model(p.get("model"))
        dfu_before = self._find_dfu_ports()          # SNAPSHOT before any reboot (identity binding)
        cdc_ports = self._find_cdc_ports()
        cdc = None
        if cdc_ports:
            # There is a normal Chameleon to reboot. Bind to EXACTLY ONE (never reboot an
            # arbitrary one), read its model FROM HARDWARE, and resolve the DFU port AFTER the
            # reboot as the NEW port relative to `dfu_before` - so a device that was already
            # stuck in DFU is never mistaken for the one we just rebooted.
            if len(cdc_ports) != 1:
                raise RuntimeError("more than one Chameleon is connected; connect only the one "
                                   "to update, then retry")
            # The model MUST come from the connected device; a caller-supplied model choice is
            # only valid for in-DFU recovery (where hardware cannot be read). Reject an override
            # so a live Ultra can never be handed Lite firmware.
            if supplied_model is not None:
                raise RuntimeError("the model is read from the connected device and cannot be "
                                   "overridden; the Ultra/Lite choice is only for recovering a "
                                   "device already in DFU")
            dfu_port = None
            cdc = cdc_ports[0]
            try:
                c = self._connect(cdc)
                raw_model = c.get_device_model()
            except Exception:
                raise RuntimeError("the Chameleon is not reachable to enter DFU: " + _MANUAL_FALLBACK)
            # Validate the HARDWARE model too (fail-closed): an unexpected value (2, future
            # variants) raises here rather than defaulting to Lite firmware.
            model = self._norm_model(raw_model)
        else:
            # No normal device to reboot -> pure recovery of a device ALREADY in DFU (crashed /
            # manual B-button). Require exactly ONE DFU device, and an EXPLICIT model - its model
            # cannot be read in DFU and we never guess (a wrong guess flashes Ultra onto a Lite).
            if not dfu_before:
                raise RuntimeError("the Chameleon is not reachable to enter DFU: " + _MANUAL_FALLBACK)
            if len(dfu_before) != 1:
                raise RuntimeError("more than one Chameleon is in DFU mode; connect only the one "
                                   "to update, then retry")
            dfu_port = dfu_before[0]
            model = supplied_model
            if model is None:
                raise RuntimeError("the Chameleon is already in DFU mode so its model cannot be "
                                   "read; choose Ultra or Lite to flash-recover it")

        tmp = None
        try:
            self.emit({"event": "progress", "method": "dfu_flash", "stage": "prepare", "percent": 0})
            # Resolve the official release ONCE and PIN it for this whole op (no re-resolve).
            rel = self._latest_release(model)
            if not rel or not rel.get("url"):
                raise RuntimeError("no application DFU release found for %s"
                                   % self._dfu_asset_name(model))
            tmp = tempfile.mkdtemp(prefix="cham-dfu-")   # private per-flash dir (mode 0700)
            os.chmod(tmp, 0o700)
            pkg = os.path.join(tmp, self._dfu_asset_name(model))
            self.emit({"event": "progress", "method": "dfu_flash", "stage": "download",
                       "percent": 0, "tag": rel.get("tag")})
            self._download_asset(rel, pkg)           # complete + size-capped + digest-checked
            info = self._validate_dfu_zip(pkg)       # app-only sanity check (reject full)
            self.emit({"event": "progress", "method": "dfu_flash", "stage": "validated", "percent": 0})
            if self._cancel.is_set():                # safe cancel: nothing has been written
                return {"flashed": False, "cancelled": True}

            if dfu_port is None:
                self._drop()                         # free the CMD handle so the raw serial can open the port
                self.emit({"event": "progress", "method": "dfu_flash", "stage": "enter", "percent": 0})
                self._send_enter_dfu(cdc)
                self.emit({"event": "progress", "method": "dfu_flash", "stage": "wait", "percent": 0})
                # Accept only a NEW, attributable DFU port (not present before the reboot), and
                # exactly one - refuse if a second device appears or the target is ambiguous.
                new = self._wait_new_dfu_ports(dfu_before)
                if not new:
                    raise RuntimeError("the Chameleon did not re-appear in DFU mode. Manual fallback: "
                                       + _MANUAL_FALLBACK)
                if len(new) != 1:
                    raise RuntimeError("more than one new Chameleon appeared in DFU after the reboot; "
                                       "disconnect the others and retry")
                dfu_port = new[0]

            # Commit handshake (closes the EOF/dispatch race): announce the uninterruptible
            # write BEFORE the final cancel re-check, so EOF either sees `_flashing` (and joins
            # unbounded) or the worker still sees the cancel here and aborts before any write.
            self._flashing.set()
            try:
                if self._cancel.is_set():            # last safe cancel point, before any write
                    self._flashing.clear()
                    return {"flashed": False, "cancelled": True}
                # POINT OF NO CANCEL: the bootloader is being written; a mid-write abort can
                # brick, so the cancel flag is deliberately not checked again.
                self._run_flash(pkg, dfu_port)
            finally:
                self._flashing.clear()
            return {"flashed": True, "port": dfu_port, "tag": rel.get("tag"),
                    "hash": info.get("hash_type")}
        finally:
            if tmp and os.path.isdir(tmp):
                shutil.rmtree(tmp, ignore_errors=True)

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
        # Route a non-Classic card out of the crypto1 key-recovery chain: an NTAG /
        # Ultralight (SAK 0x00, ATQA 0x0044) can never crypto1-auth, so the whole
        # check-keys + nonce-attack chain would grind to nothing. Read its pages
        # instead (mirrors x7d.decode's guard). A blank/magic Classic (SAK 0x00,
        # ATQA 0x0004) is NOT ntag and falls through to the Classic chain.
        if card_kind(sak[0], atqa) == "ntag":
            out = self.read_ntag(p)
            out["kind"] = "ntag"
            return out
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
                c, n, sk, pool, key_bytes, deadline, attempts, target)

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
        nested/static-nested/hardnested KNOWN sector. None if no key is known yet."""
        for s in sorted(sk):
            for kt in ("A", "B"):
                if kt in sk[s]:
                    return trailer_block(s), kt, sk[s][kt]
        return None

    @staticmethod
    def _uid4(uid):
        """The 4-byte uid the pm3 hardnested nonce-file header wants (packed later as a
        big-endian u32). A 4-byte uid is used as-is; the last 4 bytes of a 7- or 10-byte
        uid are taken (mirrors the CLI's uid_for_file). An unexpected length raises."""
        if len(uid) == 4:
            return bytes(uid)
        if len(uid) == 7:
            return bytes(uid[3:7])
        if len(uid) == 10:
            return bytes(uid[6:10])
        raise ValueError("unexpected uid length %d" % len(uid))

    def _crack_available(self, name):
        """True only when the injected cracker reports the named built binary present, so
        decode attempts (and advertises) an attack strictly when it can actually run it -
        else it degrades gracefully rather than failing mid-attack."""
        try:
            return self.crack is not None and bool(self.crack.available(name))
        except Exception:
            return False

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

    def _hardnested_recover(self, c, known, target_blk, ttype, uid_int, deadline):
        """Hard-PRNG (MFC Ev1) hardnested: with a known key anchoring the reader-side
        auth, acquire the encrypted-nonce blob on-device and crack it host-side. The
        firmware returns the nonce body ALREADY in the pm3 pair layout (per 9 bytes:
        nt_enc1 BE, nt_enc2 BE, packed-parity), so the raw bytes stream straight to the
        cracker (hardnested() prepends the pm3 header via assemble_nonce_bin). Keep
        acquiring until the full 256-value nt_enc MSB distribution is collected (what the
        crack needs), bounded by the run cap, the wall-clock deadline and the cooperative
        cancel; the crack subprocess is itself capped by the remaining budget. Returns
        candidate keys (verified by the caller); [] if nothing usable was collected."""
        blk, kt, keyhex = known
        mkt_known = MfcKeyType.A if kt == "A" else MfcKeyType.B
        mkt_target = MfcKeyType.A if ttype == "A" else MfcKeyType.B
        kb = bytes.fromhex(keyhex)
        body = bytearray()
        seen = set()
        for _ in range(HARDNESTED_MAX_RUNS):
            if self._cancel.is_set() or time.monotonic() > deadline:
                break
            chunk = c.mf1_hard_nested_acquire(False, blk, mkt_known, kb, target_blk, mkt_target)
            if not chunk:
                continue
            chunk = bytes(chunk)
            body += chunk
            # Each 9-byte record is (nt_enc1 BE, nt_enc2 BE, packed-par); the nt_enc2 MSB
            # is byte 4. Track unique MSBs so a complete distribution stops acquisition.
            for off in range(0, len(chunk) - 8, 9):
                seen.add(chunk[off + 4])
            if len(seen) >= HARDNESTED_MSB_TARGET:
                break
        if not body:
            return []
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return []                    # budget spent on acquisition; do not start a slow crack
        ktype = 1 if ttype == "B" else 0    # pm3 header key_type: 0 = KeyA, 1 = KeyB
        try:
            cands, _found = self.crack.hardnested(uid_int, target_blk, ktype, bytes(body),
                                                  timeout=max(1, int(remaining)))
        except subprocess.TimeoutExpired:
            return []                    # crack outran the budget: treat as a miss
        return cands

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

    def _recover_attacks(self, c, n, sk, pool, key_bytes, deadline, attempts, uid=None):
        """Drive the attack chain over the sectors the dictionary left unopened.
        Returns (attempts, cancelled). A sector is 'open' once we hold any key for
        it (A or B), which is all decode needs to read + dump it. A mid-attack card
        swap is caught by the per-sector uid guard in the block-read stage (which
        aborts before any key is learned), so this stage needs no separate guard.
        `uid` (the scanned card uid bytes) is only needed to anchor the hardnested
        nonce-file header; None disables hardnested."""
        cancelled = False
        # PRNG class decides which nested variant applies; if the device has no
        # reader-mode attacks (Lite) this raises and we skip the whole stage.
        try:
            prng = int(c.mf1_detect_prng())
        except (UnexpectedResponseError,) + _DEAD:
            return attempts, cancelled

        # The pm3 hardnested nonce-file header wants the card uid as a big-endian u32.
        uid_int = None
        if uid is not None:
            try:
                uid_int = int.from_bytes(self._uid4(uid), "big")
            except Exception:
                uid_int = None

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
                # Hard-PRNG (MFC Ev1): the light attacks cannot open these sectors. The
                # host hardnested cracker can, but only with (a) its binary built and
                # (b) a key already known on the card to anchor the reader-side auth. If
                # either is missing, report it and stop - no light attack can substitute.
                if uid_int is None or known is None or not self._crack_available("hardnested"):
                    self.emit({"event": "progress", "method": "decode",
                               "stage": "hardnested", "sector": s, "supported": False})
                    break
                self.emit({"event": "progress", "method": "decode", "stage": "hardnested",
                           "sector": s, "known_block": known[0]})
                try:
                    cands = self._hardnested_recover(c, known, target_blk, "A",
                                                     uid_int, deadline)
                except UnexpectedResponseError:
                    cands = []                   # acquisition faulted: treat as a miss
                key, _kt = self._verify_candidates(c, target_blk, cands, ("A", "B"))
                if key:
                    attempts += self._absorb_key(c, n, sk, pool, key_bytes, key)
                continue                         # next unresolved sector (budget-bounded)
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
                try:
                    self.emit(self.handle(req))
                finally:
                    # Disarm the flash-pending guard only once the dfu_flash op has fully
                    # returned (a result, a cancel, or an error) - so from dispatch until
                    # completion an EOF/shutdown always joins unbounded.
                    if req.get("method") == "dfu_flash":
                        self._flash_pending.clear()

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
                # Arm the flash-pending guard at DISPATCH, before the worker can start (and
                # before any EOF could choose the bounded join), closing the race where EOF
                # lands between the last cancel check and _flashing being set.
                if method == "dfu_flash":
                    self._flash_pending.set()
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
        # EXCEPTION: a firmware flash that is DISPATCHED or WRITING is joined UNBOUNDED -
        # abandoning it after 5s and letting the process exit would orphan the flasher and
        # can brick the device, so we wait for it to finish before tearing down.
        self._cancel.set()
        q.put(None)
        if self._flashing.is_set() or self._flash_pending.is_set():
            t.join()
        else:
            t.join(timeout=self.EOF_JOIN_TIMEOUT)
        if not t.is_alive():
            self._drop()


if __name__ == "__main__":
    # Protect the protocol channel: the daemon captured the real stdout at
    # construction; redirect sys.stdout to stderr so any stray library print()
    # (the vendored ChameleonCom prints on frame errors + verbose logging) goes
    # to stderr and never interleaves with the newline-JSON on stdout.
    import signal
    _daemon = Daemon()
    sys.stdout = sys.stderr

    def _guarded_signal(signum, frame):
        # Never die while a firmware flash is dispatched or writing: a SIGTERM/SIGINT then
        # would orphan the flasher and can brick the device. Ignore the signal while a flash
        # is in flight; otherwise exit cleanly (running finally-blocks + EOF teardown).
        if _daemon._flashing.is_set() or _daemon._flash_pending.is_set():
            sys.stderr.write("chameleon_d: signal %d ignored during firmware flash\n" % signum)
            sys.stderr.flush()
            return
        raise SystemExit(0)
    try:
        signal.signal(signal.SIGTERM, _guarded_signal)
        signal.signal(signal.SIGINT, _guarded_signal)
    except (ValueError, OSError):            # not main thread / unsupported: best-effort
        pass
    _daemon.run()
