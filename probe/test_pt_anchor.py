#!/usr/bin/env python3
"""Unit test for X7Card._pt() PN532-response anchoring (no hardware needed).

Regression test for the seq-byte mis-anchor bug. The 16-bit host sequence
counter (x7.py X7.encode, host +2 per command, device echoes seq|1) lands in
raw[2]/raw[3]. For ~1.17% of commands a seq byte equals 0xD5, and the old
`raw.find(b"\\xd5")` (search from offset 0) anchored the PN532 slice on the seq
byte instead of the real direction byte at offset 5, silently dropping the
command. The fix searches from offset 4 (past marker/total/seq_lo/seq_hi).

Run: python3 test_pt_anchor.py
"""
from x7 import X7
from x7lib import X7Card


def build_response(payload, seq):
    """Build a 64-byte device RESPONSE envelope: 02|total|seq16|payload|cksum|FD."""
    body = bytes([0x02, len(payload) + 6, seq & 0xFF, (seq >> 8) & 0xFF]) + bytes(payload)
    body += bytes([(~sum(body)) & 0xFF, 0xFD])
    return (body + bytes(64))[:64]


class _FakeX:
    """Stand-in for the X7 transport: returns a canned (decoded, [report])."""

    def __init__(self, report):
        self._report = report

    def cmd(self, payload, **kw):
        return X7.decode(self._report), [self._report]

    def close(self):
        pass


def _card_with(report):
    c = X7Card.__new__(X7Card)        # bypass __init__ (no device open)
    c.x = _FakeX(report)
    c.uid = b"\x01\x02\x03\x04"
    return c


# A read-block-style PN532 reply: 00(status) D5 41 00 <16 data> 90 00.
DATA = bytes(range(0x10, 0x20))
PAYLOAD = bytes([0x00, 0xD5, 0x41, 0x00]) + DATA + bytes([0x90, 0x00])


def check(seq, label, header_d5_expected):
    report = build_response(PAYLOAD, seq)

    # Precondition: this seq really puts a 0xD5 in the 4-byte header, so the OLD
    # find-from-0 WOULD mis-anchor there - proving the test exercises the bug.
    naive = report.find(b"\xd5")
    assert naive == header_d5_expected and naive < 5, \
        "%s: expected header 0xD5 at %d, got %d" % (label, header_d5_expected, naive)

    r = _card_with(report)._pt([0xD4, 0x40, 0x01, 0x30, 0x04])
    assert r[:3] == bytes([0xD5, 0x41, 0x00]), "%s: wrong anchor: %r" % (label, r[:4])
    assert r[3:19] == DATA, "%s: wrong data slice: %r" % (label, r[3:19])
    print("[ok] %s (seq=%04x: naive find=%d, fixed anchor=5)" % (label, seq, naive))


if __name__ == "__main__":
    check(0x00D5, "seq_lo=0xD5", header_d5_expected=2)   # raw[2] = seq_lo = 0xD5
    check(0xD500, "seq_hi=0xD5", header_d5_expected=3)   # raw[3] = seq_hi = 0xD5

    # Control: a clean seq must still anchor correctly (no regression).
    rep = build_response(PAYLOAD, 0x0002)
    assert rep.find(b"\xd5") == 5
    out = _card_with(rep)._pt([0xD4, 0x40, 0x01, 0x30, 0x04])
    assert out[:3] == bytes([0xD5, 0x41, 0x00]) and out[3:19] == DATA
    print("[ok] control seq=0002 (no header 0xD5) still anchors at 5")

    print("\nALL _pt ANCHOR TESTS PASSED")
