"""Minimal ctypes binding to hidapi (loads brew's libhidapi.dylib by absolute path).

Avoids pyhidapi's library-search + macOS SIP DYLD-stripping issues by loading the
dylib directly. Only the handful of hidapi functions we need for probing are bound.
"""
import ctypes
import ctypes.util
import os

# Resolve libhidapi.dylib. Inside the packaged .app it is found by the bundle-
# relative candidate (probe sits in Contents/Resources/probe, the dylib in
# Contents/Frameworks). X7_HIDAPI is an optional manual override if you ever need
# to point at a specific dylib. Outside the bundle we fall back to brew, then the
# system search - so dev keeps working with no env at all.
def _candidates():
    out = []
    env = os.environ.get("X7_HIDAPI")
    if env:
        out.append(env)
    here = os.path.dirname(os.path.abspath(__file__))
    # ...Contents/Resources/probe/x7hid.py -> ...Contents/Frameworks/libhidapi.dylib
    out.append(os.path.normpath(os.path.join(here, "..", "..", "Frameworks", "libhidapi.dylib")))
    out += ["/opt/homebrew/lib/libhidapi.dylib", "/usr/local/lib/libhidapi.dylib"]
    return out


def _load():
    cands = _candidates()
    for p in cands:
        if p and os.path.exists(p):
            try:
                return ctypes.CDLL(p)
            except OSError:
                continue   # exists but won't load (arch / signature) - try the next
    found = ctypes.util.find_library("hidapi")
    if found:
        return ctypes.CDLL(found)
    raise OSError(
        "libhidapi.dylib not found. Run: brew install hidapi  (checked %s)"
        % ", ".join(c for c in cands if c)
    )


_lib = _load()


class _DevInfo(ctypes.Structure):
    pass


# Field order matches hidapi's struct hid_device_info up to `next`.
# Trailing fields added in newer hidapi (bus_type) sit AFTER next, so omitting
# them is safe because we never read past next.
_DevInfo._fields_ = [
    ("path", ctypes.c_char_p),
    ("vendor_id", ctypes.c_ushort),
    ("product_id", ctypes.c_ushort),
    ("serial_number", ctypes.c_wchar_p),
    ("release_number", ctypes.c_ushort),
    ("manufacturer_string", ctypes.c_wchar_p),
    ("product_string", ctypes.c_wchar_p),
    ("usage_page", ctypes.c_ushort),
    ("usage", ctypes.c_ushort),
    ("interface_number", ctypes.c_int),
    ("next", ctypes.POINTER(_DevInfo)),
]

_lib.hid_init.restype = ctypes.c_int
_lib.hid_enumerate.restype = ctypes.POINTER(_DevInfo)
_lib.hid_enumerate.argtypes = [ctypes.c_ushort, ctypes.c_ushort]
_lib.hid_free_enumeration.argtypes = [ctypes.POINTER(_DevInfo)]
_lib.hid_open.restype = ctypes.c_void_p
_lib.hid_open.argtypes = [ctypes.c_ushort, ctypes.c_ushort, ctypes.c_wchar_p]
_lib.hid_open_path.restype = ctypes.c_void_p
_lib.hid_open_path.argtypes = [ctypes.c_char_p]
_lib.hid_write.restype = ctypes.c_int
_lib.hid_write.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t]
_lib.hid_read_timeout.restype = ctypes.c_int
_lib.hid_read_timeout.argtypes = [
    ctypes.c_void_p,
    ctypes.c_char_p,
    ctypes.c_size_t,
    ctypes.c_int,
]
_lib.hid_send_feature_report.restype = ctypes.c_int
_lib.hid_send_feature_report.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t]
_lib.hid_get_feature_report.restype = ctypes.c_int
_lib.hid_get_feature_report.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t]
try:
    _lib.hid_get_report_descriptor.restype = ctypes.c_int
    _lib.hid_get_report_descriptor.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t]
    _HAS_DESC = True
except AttributeError:
    _HAS_DESC = False
_lib.hid_close.argtypes = [ctypes.c_void_p]
_lib.hid_error.restype = ctypes.c_wchar_p
_lib.hid_error.argtypes = [ctypes.c_void_p]

_lib.hid_init()


def enumerate(vid=0, pid=0):
    out = []
    head = _lib.hid_enumerate(vid, pid)
    cur = head
    while cur:
        d = cur.contents
        out.append(
            {
                "path": d.path.decode() if d.path else None,
                "vendor_id": d.vendor_id,
                "product_id": d.product_id,
                "serial_number": d.serial_number,
                "manufacturer_string": d.manufacturer_string,
                "product_string": d.product_string,
                "usage_page": d.usage_page,
                "usage": d.usage,
                "interface_number": d.interface_number,
            }
        )
        cur = d.next
    if head:
        _lib.hid_free_enumeration(head)
    return out


class Device:
    def __init__(self, vid=None, pid=None, path=None):
        if path is not None:
            self._h = _lib.hid_open_path(path.encode() if isinstance(path, str) else path)
        else:
            self._h = _lib.hid_open(vid, pid, None)
        if not self._h:
            raise OSError("hid_open failed (device busy, missing, or no permission)")

    def write(self, data: bytes) -> int:
        n = _lib.hid_write(self._h, data, len(data))
        if n < 0:
            raise OSError("hid_write failed: %s" % _lib.hid_error(self._h))
        return n

    def read(self, size: int = 64, timeout_ms: int = 300) -> bytes:
        buf = ctypes.create_string_buffer(size)
        n = _lib.hid_read_timeout(self._h, buf, size, timeout_ms)
        if n < 0:
            raise OSError("hid_read failed: %s" % _lib.hid_error(self._h))
        return buf.raw[:n]

    def send_feature(self, data: bytes) -> int:
        n = _lib.hid_send_feature_report(self._h, data, len(data))
        if n < 0:
            raise OSError("hid_send_feature failed: %s" % _lib.hid_error(self._h))
        return n

    def get_feature(self, size: int = 65, report_id: int = 0) -> bytes:
        buf = ctypes.create_string_buffer(size)
        buf[0] = bytes([report_id])
        n = _lib.hid_get_feature_report(self._h, buf, size)
        if n < 0:
            raise OSError("hid_get_feature failed: %s" % _lib.hid_error(self._h))
        return buf.raw[:n]

    def report_descriptor(self, size: int = 4096) -> bytes:
        if not _HAS_DESC:
            raise OSError("hid_get_report_descriptor not available in this hidapi")
        buf = ctypes.create_string_buffer(size)
        n = _lib.hid_get_report_descriptor(self._h, buf, size)
        if n < 0:
            raise OSError("hid_get_report_descriptor failed")
        return buf.raw[:n]

    def close(self):
        if self._h:
            _lib.hid_close(self._h)
            self._h = None
