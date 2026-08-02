"""Native macOS reader for the XIXEI X7 - reads ISO14443A card UID.

Uses the PN532 RF-init sequence captured from nfcPro (x7_init.py, ground truth)
then InListPassiveTarget. This is the breakthrough path: the vendor's own GUI
RF-init was not statically reconstructable, but driving the internal PN532 via
the passthrough opcode with nfcPro's exact init bytes works perfectly.
"""
from x7 import X7, hx
from x7_init import INIT_SEQ

POLL = [0xFF, 0x00, 0x00, 0x00, 0x04, 0xD4, 0x4A, 0x01, 0x00]  # InListPassiveTarget MaxTg=1 106A


class X7Reader:
    def __init__(self):
        self.x = X7()

    def init_rf(self):
        for h in INIT_SEQ:
            self.x.cmd(list(bytes.fromhex(h)), reads=3, timeout=400)

    def poll(self):
        """Return dict(uid, atqa, sak) or None if no card."""
        dec, reps = self.x.cmd(POLL, reads=8, timeout=600)
        raw = reps[0] if reps else b""
        i = raw.find(b"\xd5\x4b")
        if i < 0:
            return None
        r = raw[i:]
        if len(r) < 8 or r[2] != 0x01:          # NbTg must be 1
            return None
        atqa, sak, uidlen = r[4:6], r[6], r[7]
        uid = r[8:8 + uidlen]
        if len(uid) != uidlen:
            return None
        return {"uid": bytes(uid), "atqa": bytes(atqa), "sak": sak}

    def close(self):
        self.x.close()


if __name__ == "__main__":
    rd = X7Reader()
    print("Initializing RF (PN532, %d captured commands)..." % len(INIT_SEQ))
    rd.init_rf()
    for k in range(3):
        c = rd.poll()
        if c:
            print("poll %d -> UID=%s  ATQA=%s  SAK=%02x" % (k, hx(c["uid"]).upper(), hx(c["atqa"]), c["sak"]))
        else:
            print("poll %d -> no card" % k)
    rd.close()
