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
from x7lib import (X7Card, trailer_block, first_block, sector_count,
                   DEFAULT_KEYS, BUILTIN_KEYS)

# Factory transport config for a MIFARE Classic trailer: KeyA all-FF, access
# bytes FF 07 80 + GPB 69 (the chip's shipped state), KeyB all-FF.
FACTORY_TRAILER = bytes.fromhex("ffffffffffff" "ff078069" "ffffffffffff")


def _sector_of(b):
    return b // 4 if b < 128 else 32 + (b - 128) // 16


class Daemon:
    METHODS = ("info", "poll", "decode", "read_ntag", "apdu", "write_mfd",
               "format", "nested_recover", "keys_default", "keys_builtin_count")

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
        """The small in-binary fast-path key list (legacy; the full dictionary is
        BUILTIN_KEYS, kept daemon-side and never shipped to the UI)."""
        return {"keys": list(DEFAULT_KEYS)}

    def keys_builtin_count(self, p):
        """Size of the bundled curated dictionary, so Settings can show '+N
        built-in' without ever transferring thousands of keys over the pipe."""
        return {"count": len(BUILTIN_KEYS)}

    def decode(self, p):
        c = self._open()
        # The app sends only the USER's editable keys; the big curated dictionary
        # lives here and is appended (user keys tried first).
        user = p.get("user_keys") or p.get("keys") or []
        uset = set(user)
        keys = list(user) + [k for k in BUILTIN_KEYS if k not in uset]

        def prog(s, n, f):
            self.emit({"event": "progress", "method": "decode", "sector": s,
                       "total": n, "keytype": (f[0] if f else None),
                       "key": (f[1] if f else None)})

        def on_try(s, i, n):
            self.emit({"event": "progress", "method": "decode", "sector": s,
                       "total": None, "keys_tried": i, "keys_total": n})
        d = c.dump(keys=keys, progress=prog, on_try=on_try)
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

    def format(self, p):
        """Reset a MIFARE Classic to factory: zero every data block and write the
        factory trailer. params: keys {sector:[kt,key]} from a prior decode (so
        we can auth); falls back to the default key. Block 0 (uid) is left alone.
        Trailer is written LAST per sector so the key only flips to FF when the
        sector's data is already cleared."""
        c = self._open()
        i = c.wait_for_card()
        if not i:
            return {"present": False}
        keys = {int(s): v for s, v in (p.get("keys") or {}).items()}
        zero = bytes(16)
        ok, fail = 0, []
        for s in range(sector_count(i["sak"])):
            tb = trailer_block(s)
            k = keys.get(s)
            kk = k[1] if k else "ffffffffffff"
            kts = [k[0], "A", "B"] if k else ["A", "B"]
            for b in range(first_block(s), tb + 1):
                if b == 0:
                    continue
                data = FACTORY_TRAILER if b == tb else zero
                wrote = False
                for kt in kts:
                    for _ in range(3):
                        if not c.poll():
                            continue
                        if not c.auth(tb, kk, kt):
                            break
                        if c.write_block(b, data):
                            wrote = True
                            break
                    if wrote:
                        break
                ok += 1 if wrote else 0
                if not wrote:
                    fail.append(b)
                self.emit({"event": "progress", "method": "format", "block": b, "ok": wrote})
        return {"present": True, "formatted": ok, "failed": fail}

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
