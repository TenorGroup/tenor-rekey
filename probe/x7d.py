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
                   access_bits_valid, trailer_locks_keys, DEFAULT_KEYS, BUILTIN_KEYS)


def _valid_key_hex(k):
    """A daemon-side key sanity check (the engine is its own trust boundary): a key
    must be exactly 12 hex chars, else it cannot be used for auth/substitution."""
    return isinstance(k, str) and len(k) == 12 and all(c in "0123456789abcdefABCDEF" for c in k)

# Factory transport config for a MIFARE Classic trailer: KeyA all-FF, access
# bytes FF 07 80 + GPB 69 (the chip's shipped state), KeyB all-FF.
FACTORY_TRAILER = bytes.fromhex("ffffffffffff" "ff078069" "ffffffffffff")
FACTORY_KEY = bytes.fromhex("ffffffffffff")


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

    def _drop(self):
        """Forget the reader handle so the next _open() re-opens it. Called when a
        hardware op fails (reader unplugged): the cached handle is dead, and a
        fresh X7Card() is the only way to talk to the reader once it is back."""
        if self.card is not None:
            try:
                self.card.close()
            except Exception:
                pass
            self.card = None

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
        # `reader` reports whether the X7 itself is connected (vs `present`, a card
        # on it). A failing hardware op means the reader was unplugged: drop the
        # dead handle so a replug re-opens cleanly. `tries` lets the UI status
        # poll be snappy (few tries) while a decode still uses the full coupling
        # retry. Errors here are normal (unplug), so we answer instead of raising.
        tries = int(p.get("tries", 25))
        try:
            c = self._open()
            i = c.wait_for_card(tries=tries)
        except OSError:
            self._drop()
            return {"present": False, "reader": False}
        if not i:
            return {"present": False, "reader": True}
        return {"present": True, "reader": True, "uid": hx(i["uid"]),
                "atqa": hx(i["atqa"]), "sak": i["sak"]}

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
        target = i["uid"]                      # the card we committed to writing
        # Validate block payloads up front: a block that is not exactly 16 bytes is
        # never sent to the card (write_block would assert mid-card, half-writing
        # it); record it as failed so the caller still gets full accounting.
        blocks, fail = {}, []
        for b, v in p["blocks"].items():
            if not v:
                continue
            try:
                raw = bytes.fromhex(v.replace(" ", ""))
            except ValueError:
                fail.append(int(b))
                continue
            if len(raw) == 16:
                blocks[int(b)] = raw
            else:
                fail.append(int(b))
        keys = {int(s): v for s, v in (p.get("keys") or {}).items()}
        ok, swapped = 0, False
        for b in sorted(blocks):
            if b == 0 and not p.get("uid"):
                continue
            s = _sector_of(b)
            is_trailer = (b == trailer_block(s))
            if is_trailer and not p.get("trailers"):
                continue
            k = keys.get(s)
            data = blocks[b]
            if is_trailer:
                # A trailer that is corrupt OR that locks its own keys can brick the
                # sector forever - never write either. And never write a 000000 key
                # slot: substitute the recovered key, or factory FF if it is unknown.
                if not access_bits_valid(data):
                    fail.append(b)
                    self.emit({"event": "progress", "method": "write_mfd",
                               "block": b, "ok": False, "unsafe": "access-bits"})
                    continue
                if trailer_locks_keys(data):
                    fail.append(b)
                    self.emit({"event": "progress", "method": "write_mfd",
                               "block": b, "ok": False, "unsafe": "trailer-lockout"})
                    continue
                sub = bytes.fromhex(k[1]) if (k and _valid_key_hex(k[1])) else FACTORY_KEY
                d = bytearray(data)
                if d[0:6] == bytes(6):
                    d[0:6] = sub
                if d[10:16] == bytes(6):
                    d[10:16] = sub
                data = bytes(d)
            # Auth the TARGET. Try the source key first (re-clone, or a card the
            # user pre-keyed), then fall back to factory FF so a blank magic card
            # can be written. Blocks are written low-to-high, so a sector's trailer
            # (which flips the key to the source key) lands AFTER its data, while
            # the FF auth still holds. Each key is tried as A and B.
            cand = []
            if k and _valid_key_hex(k[1]):
                cand += [(k[1], k[0]), (k[1], "A"), (k[1], "B")]
            cand += [("ffffffffffff", "A"), ("ffffffffffff", "B")]
            wrote, seen = False, set()
            for kk, kt in cand:
                if (kk, kt) in seen:
                    continue
                seen.add((kk, kt))
                for _ in range(3):
                    if not c.poll():
                        continue
                    if c.uid != target:        # a different card arrived: never write to it
                        swapped = True
                        break
                    if not c.auth(trailer_block(s), kk, kt):
                        break
                    if c.write_block(b, data):
                        wrote = True
                        break
                if wrote or swapped:
                    break
            if swapped:
                break
            ok += 1 if wrote else 0
            if not wrote:
                fail.append(b)
            self.emit({"event": "progress", "method": "write_mfd",
                       "block": b, "ok": wrote})
        if swapped:
            return {"present": True, "wrote": ok, "failed": fail,
                    "error": "card changed during write"}
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
        target = i["uid"]                      # the card we committed to formatting
        keys = {int(s): v for s, v in (p.get("keys") or {}).items()}
        zero = bytes(16)
        ok, fail, swapped = 0, [], False
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
                        if c.uid != target:    # a different card arrived: never erase it
                            swapped = True
                            break
                        if not c.auth(tb, kk, kt):
                            break
                        if c.write_block(b, data):
                            wrote = True
                            break
                    if wrote or swapped:
                        break
                if swapped:
                    break
                ok += 1 if wrote else 0
                if not wrote:
                    fail.append(b)
                self.emit({"event": "progress", "method": "format", "block": b, "ok": wrote})
            if swapped:
                break
        if swapped:
            return {"present": True, "formatted": ok, "failed": fail,
                    "error": "card changed during format"}
        return {"present": True, "formatted": ok, "failed": fail}

    def nested_recover(self, p):
        """params: known_blk, known_key, target_blk, known_kt, target_kt,
        window, max_samples. Returns the recovered key (proven by on-card auth)."""
        c = self._open()
        if not c.uid and not c.wait_for_card():
            return {"present": False}            # no card: match the other ops' shape

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
        except OSError as e:
            # The reader was unplugged mid-op: drop the dead handle so the NEXT
            # command re-opens a fresh one instead of reusing the dead one (only
            # poll() used to do this, leaving every other op wedged until a poll).
            self._drop()
            return {"id": rid, "error": "%s: %s" % (type(e).__name__, e)}
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
