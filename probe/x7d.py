#!/usr/bin/env python3
"""x7d - JSON-over-stdio daemon wrapping the verified X7 engine (x7lib).

The native macOS (SwiftUI) front-end speaks THIS contract and never touches the
Python internals. The contract is deliberately NARROW so the engine can later be
swapped for a native Swift + vendored-C implementation without changing the UI
(architecture decision 2026-06-19: ship A-first, design the daemon like C is
inevitable).

Wire format - newline-delimited JSON on stdin/stdout:
  request : {"id": <n>, "method": "<name>", "params": {...}}
  response: {"id": <n>, "result": {...}}  |  {"id": <n>, "error": "<msg>"}
  event   : {"event": "progress", "method": "<name>", ...}   (id-less, unsolicited)

Methods (the whole surface): info, poll, decode, apdu, write_mfd, nested_recover.
Hex is lowercase space-separated ("01 02 03 04"); keys are 12-char hex.
"""
import sys
import json
from x7 import X7, hx
from x7lib import X7Card, trailer_block, DEFAULT_KEYS


def _sector_of(b):
    return b // 4 if b < 128 else 32 + (b - 128) // 16


class Daemon:
    METHODS = ("info", "poll", "decode", "read_ntag", "apdu", "write_mfd",
               "nested_recover", "keys_default")

    def __init__(self):
        self.card = None

    def _open(self):
        """Open + RF-init the reader once; reuse across commands (Codex r1: start
        the engine at launch, not lazily per action)."""
        if self.card is None:
            self.card = X7Card()
            self.card.init_rf()
        return self.card

    def emit(self, obj):
        sys.stdout.write(json.dumps(obj) + "\n")
        sys.stdout.flush()

    # ---- methods -----------------------------------------------------------

    def info(self, p):
        c = self._open()
        out = {}
        for name, op in (("model", 0x68), ("serial", 0x69), ("hw", 0x6C)):
            d, r = c.x.cmd([0xFF, 0x00, op], reads=4, timeout=400)
            raw = r[0] if r else b""
            pl = raw[4:raw[1] - 2] if raw and len(raw) > 1 and raw[1] >= 6 else b""
            s = bytes(pl).split(b"\x00")[0].decode("latin1", "replace")
            out[name] = "".join(ch for ch in s if ch.isprintable())
        return out

    def poll(self, p):
        c = self._open()
        i = c.wait_for_card()
        if not i:
            return {"present": False}
        return {"present": True, "uid": hx(i["uid"]), "atqa": hx(i["atqa"]),
                "sak": i["sak"]}

    def keys_default(self, p):
        """The built-in key dictionary, so the app can seed its editable list
        from a single source (x7lib) instead of duplicating it."""
        return {"keys": list(DEFAULT_KEYS)}

    def decode(self, p):
        c = self._open()
        keys = p.get("keys") or DEFAULT_KEYS

        def prog(s, n, f):
            self.emit({"event": "progress", "method": "decode", "sector": s,
                       "total": n, "keytype": (f[0] if f else None),
                       "key": (f[1] if f else None)})
        d = c.dump(keys=keys, progress=prog)
        blocks = {str(b): (hx(v) if v else None) for b, v in d["blocks"].items()}
        keys = {str(s): ([k[0], k[1]] if k else None) for s, k in d["keys"].items()}
        return {"uid": hx(d["uid"]), "atqa": hx(d["atqa"]), "sak": d["sak"],
                "sectors": d["sectors"],
                "recovered": sum(1 for k in d["keys"].values() if k),
                "blocks": blocks, "keys": keys}

    def read_ntag(self, p):
        """Dump an NTAG21x / Ultralight (SAK 0x00) as 4-byte pages."""
        c = self._open()
        i = c.wait_for_card()
        if not i:
            return {"present": False}
        pages = c.read_ntag()
        return {"present": True, "uid": hx(i["uid"]), "sak": i["sak"],
                "pages": {str(k): hx(v) for k, v in pages.items()}}

    def apdu(self, p):
        c = self._open()
        i = c.wait_for_card()
        if not i:
            return {"present": False}
        resp = c.apdu(p["hex"])
        return {"present": True, "uid": hx(i["uid"]), "sak": i["sak"],
                "resp": (hx(resp) if resp else None)}

    def write_mfd(self, p):
        """params: blocks {blk: hex16}, keys {sector:[kt,key]}, trailers, uid."""
        c = self._open()
        i = c.wait_for_card()
        if not i:
            return {"present": False}
        blocks = {int(b): bytes.fromhex(v.replace(" ", "")) for b, v in p["blocks"].items() if v}
        keys = {int(s): v for s, v in (p.get("keys") or {}).items()}
        ok, fail = 0, []
        for b in sorted(blocks):
            if b == 0 and not p.get("uid"):
                continue
            s = _sector_of(b)
            if b == trailer_block(s) and not p.get("trailers"):
                continue
            k = keys.get(s)
            kk = k[1] if k else "ffffffffffff"
            wrote = False
            for kt in ([k[0], "A", "B"] if k else ["A", "B"]):
                for _ in range(3):
                    if not c.poll():
                        continue
                    if not c.auth(trailer_block(s), kk, kt):
                        break
                    if c.write_block(b, blocks[b]):
                        wrote = True
                        break
                if wrote:
                    break
            ok += 1 if wrote else 0
            if not wrote:
                fail.append(b)
            self.emit({"event": "progress", "method": "write_mfd",
                       "block": b, "ok": wrote})
        return {"present": True, "wrote": ok, "failed": fail}

    def nested_recover(self, p):
        """params: known_blk, known_key, target_blk, known_kt, target_kt,
        window, max_samples. Returns the recovered key (proven by on-card auth)."""
        c = self._open()
        if not c.uid:
            c.wait_for_card()

        def prog(phase, n, x):
            self.emit({"event": "progress", "method": "nested_recover",
                       "phase": phase, "n": n, "info": str(x)})
        import x7crypto
        key = x7crypto.nested_recover_key(
            c, p["known_blk"], p["known_key"], p["target_blk"],
            known_kt=p.get("known_kt", "A"), target_kt=p.get("target_kt", "A"),
            window=p.get("window", 4096), max_samples=p.get("max_samples", 8),
            on_progress=prog)
        return {"key": key, "target_blk": p["target_blk"]}

    # ---- dispatch ----------------------------------------------------------

    def handle(self, req):
        rid = req.get("id")
        method = req.get("method")
        if method not in self.METHODS:
            return {"id": rid, "error": "unknown method: %r" % method}
        try:
            return {"id": rid, "result": getattr(self, method)(req.get("params") or {})}
        except Exception as e:
            return {"id": rid, "error": "%s: %s" % (type(e).__name__, e)}

    def run(self):
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
            except ValueError as e:
                self.emit({"error": "bad json: %s" % e})
                continue
            self.emit(self.handle(req))
        if self.card:
            self.card.close()


if __name__ == "__main__":
    Daemon().run()
