"""X7 (XIXEI Smart Reader) client - vendor HID envelope cracked from nfcPro.

Frame (64-byte HID report, device sees it starting at the 0x01 marker; we prepend
a 0x00 hidapi report-id byte on write):

  request : [0x01][total][seq_lo][seq_hi][payload...][cksum][0xFE] 00pad...
  response: [0x02][total][seq_lo][seq_hi][payload...][cksum][0xFD] 00pad...
  total = len(payload) + 6   (== index of trailer + 1)
  cksum = (~sum(bytes from marker .. last payload byte)) & 0xFF
  seq   = 16-bit counter, host increments by 2 per command; device echoes seq|1
"""
import x7hid

VID, PID = 0x2518, 0x6022


def hx(b):
    return " ".join("%02x" % x for x in b)


class X7:
    def __init__(self):
        self.dev = x7hid.Device(vid=VID, pid=PID)
        self.seq = 0

    def encode(self, payload):
        body = bytes([0x01, len(payload) + 6, self.seq & 0xFF, (self.seq >> 8) & 0xFF]) + bytes(payload)
        body += bytes([(~sum(body)) & 0xFF, 0xFE])
        rep = (body + bytes(64))[:64]
        return b"\x00" + rep

    @staticmethod
    def decode(rep):
        if not rep or len(rep) < 2 or rep[0] != 0x02:
            return None
        total = rep[1]
        if total < 6 or total > len(rep):
            return {"bad": True, "raw": hx(rep[:16])}
        return {
            "seq": rep[2] | (rep[3] << 8),
            "payload": rep[4:total - 2],
            "cksum": rep[total - 2],
            "trailer": rep[total - 1],
        }

    def transceive(self, payload, reads=12, timeout=400):
        self.dev.write(self.encode(payload))
        self.seq = (self.seq + 2) & 0xFFFF
        reports = []
        # The reader returns exactly one 64-byte response per command, so stop as
        # soon as it arrives. `reads` bounds how many empty polls we tolerate first.
        for _ in range(reads):
            r = self.dev.read(64, timeout)
            if r:
                reports.append(r)
                break
        return reports

    def cmd(self, payload, **kw):
        """Send payload, return (decoded_first_response, raw_reports)."""
        reps = self.transceive(payload, **kw)
        dec = self.decode(reps[0]) if reps else None
        return dec, reps

    def close(self):
        self.dev.close()
