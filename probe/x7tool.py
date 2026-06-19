#!/usr/bin/env python3
"""x7tool - native macOS CLI for the XIXEI X7 reader (restores nfcPro core).

  x7tool info                 device model / serial
  x7tool read                 UID + ATQA + SAK + card type
  x7tool decode [-o out.mfd]  full MIFARE Classic dump (keys + data)
  x7tool write in.mfd [...]    clone a .mfd dump onto a (magic) card
"""
import argparse
import json
import os
import sys
from x7 import X7, hx
from x7lib import X7Card, DEFAULT_KEYS, trailer_block, first_block, sector_count

SAK_TYPE = {0x08: "MIFARE Classic 1K", 0x18: "MIFARE Classic 4K", 0x09: "MIFARE Mini",
            0x00: "MIFARE Ultralight / NTAG", 0x20: "MIFARE DESFire / Plus",
            0x28: "SmartMX-MIFARE 1K", 0x38: "SmartMX-MIFARE 4K"}


def device_info():
    x = X7()
    out = {}
    for name, op in (("model", 0x68), ("serial", 0x69), ("hw", 0x6C)):
        d, r = x.cmd([0xFF, 0x00, op], reads=4, timeout=400)
        raw = r[0] if r else b""
        pl = raw[4:raw[1] - 2] if raw and len(raw) > 1 and raw[1] >= 6 else b""
        out[name] = bytes(pl).split(b"\x00")[0].decode("latin1", "replace")
    x.close()
    return out


def cmd_info(a):
    i = device_info()
    print("Model : %s" % i["model"])
    print("Serial: %s" % i["serial"])
    print("HW    : %s" % i["hw"])


def cmd_read(a):
    c = X7Card(); c.init_rf()
    i = c.wait_for_card()
    if not i:
        c.close(); print("No card on reader."); return
    sak = i["sak"]
    print("UID  : %s" % hx(i["uid"]).upper())
    print("ATQA : %s" % hx(i["atqa"]))
    print("SAK  : %02x  (%s)" % (sak, SAK_TYPE.get(sak, "unknown")))
    if sak == 0x00:                       # NTAG / Ultralight - dump pages
        pages = c.read_ntag()
        print("Pages:")
        for p in sorted(pages):
            print("  %3d: %s" % (p, hx(pages[p])))
    c.close()


def save_mfd(d, path):
    size = 4096 if d["sak"] == 0x18 else 1024
    buf = bytearray(size)
    for b, data in d["blocks"].items():
        if data and (b + 1) * 16 <= size:
            buf[b * 16:b * 16 + 16] = data
    open(path, "wb").write(buf)
    keys = {str(s): (list(k) if k else None) for s, k in d["keys"].items()}
    json.dump({"uid": hx(d["uid"]), "sak": d["sak"], "keys": keys},
              open(path + ".keys.json", "w"), indent=1)


def cmd_decode(a):
    c = X7Card(); c.init_rf()

    def prog(s, n, f):
        sys.stderr.write("\r  reading sector %2d/%d  %s   " %
                         (s + 1, n, (f[0] + ":" + f[1]) if f else "no key"))
        sys.stderr.flush()
    d = c.dump(progress=prog); c.close()
    sys.stderr.write("\n\n")
    print("UID=%s  SAK=%02x  (%s)  %d sectors" %
          (hx(d["uid"]).upper(), d["sak"], SAK_TYPE.get(d["sak"], "?"), d["sectors"]))
    last = max(d["blocks"]) if d["blocks"] else -1
    for b in range(last + 1):
        blk = d["blocks"].get(b)
        s = b // 4 if b < 128 else 32 + (b - 128) // 16
        mark = "  <- sector %d trailer (KeyA|acc|KeyB)" % s if b == trailer_block(s) else ""
        print("  blk %3d: %s%s" % (b, hx(blk) if blk else "?? unreadable", mark))
    print("\nKeys found:")
    for s, k in sorted(d["keys"].items()):
        print("  sector %2d: %s" % (s, (k[0] + " " + k[1]) if k else "NOT FOUND (key unknown)"))
    nfound = sum(1 for k in d["keys"].values() if k)
    print("\n%d/%d sectors recovered." % (nfound, d["sectors"]))
    if a.out:
        save_mfd(d, a.out)
        print("Saved %s (+ %s.keys.json)" % (a.out, a.out))


def cmd_write(a):
    data = open(a.infile, "rb").read()
    nblk = len(data) // 16
    keyf = a.infile + ".keys.json"
    keys = {}
    if os.path.exists(keyf):
        kj = json.load(open(keyf))
        keys = {int(s): v for s, v in kj["keys"].items()}
    c = X7Card(); c.init_rf()
    i = c.wait_for_card()
    if not i:
        print("No card on reader."); c.close(); return
    print("Target UID %s SAK %02x. Writing %d blocks (trailers=%s uid-block=%s)..."
          % (hx(i["uid"]).upper(), i["sak"], nblk, a.trailers, a.uid))
    ok, fail = 0, []
    for b in range(nblk):
        if b == 0 and not a.uid:
            continue
        s = b // 4 if b < 128 else 32 + (b - 128) // 16
        is_trailer = (b == trailer_block(s))
        if is_trailer and not a.trailers:
            continue
        blk = data[b * 16:b * 16 + 16]
        k = keys.get(s)
        kk = k[1] if k else "ffffffffffff"
        wrote = False
        for kt in ([k[0], "A", "B"] if k else ["A", "B"]):   # try recorded key type then both
            for _ in range(3):
                if not c.poll():
                    continue
                if not c.auth(trailer_block(s), kk, kt):
                    break                         # wrong key type for this sector
                if c.write_block(b, blk):
                    wrote = True
                    break
            if wrote:
                break
        ok += 1 if wrote else 0
        if not wrote:
            fail.append(b)
    c.close()
    print("Wrote %d blocks OK. Failed: %s" % (ok, fail if fail else "none"))


def cmd_apdu(a):
    c = X7Card(); c.init_rf()
    i = c.wait_for_card()
    if not i:
        c.close(); print("No card on reader."); return
    print("Card UID %s SAK %02x" % (hx(i["uid"]).upper(), i["sak"]))
    resp = c.apdu(a.hex)
    c.close()
    print("APDU resp: %s" % (hx(resp) if resp else "(no response / error)"))


def main():
    ap = argparse.ArgumentParser(prog="x7tool", description="XIXEI X7 native macOS tool")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("info").set_defaults(func=cmd_info)
    sub.add_parser("read").set_defaults(func=cmd_read)
    dp = sub.add_parser("decode"); dp.add_argument("-o", "--out"); dp.set_defaults(func=cmd_decode)
    pp = sub.add_parser("apdu"); pp.add_argument("hex", help="APDU bytes in hex"); pp.set_defaults(func=cmd_apdu)
    wp = sub.add_parser("write")
    wp.add_argument("infile")
    wp.add_argument("--trailers", action="store_true", help="also write sector trailers (keys/access)")
    wp.add_argument("--uid", action="store_true", help="also write block 0 (UID - magic card only)")
    wp.set_defaults(func=cmd_write)
    a = ap.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
