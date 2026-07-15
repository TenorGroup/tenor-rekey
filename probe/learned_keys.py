#!/usr/bin/env python3
"""learned_keys - a persistent reranker over the static key dictionary.

When a card is decoded, the engine recovers per-sector keys that authed live on
the card. This cache stores those verified keys ranked by success (hit count and
recency) so a later decode can try recently/frequently-successful keys first,
ahead of the big static dictionary. Keys are 12-hex-char lowercase strings; card
uids are compact lowercase hex. The on-disk form is a JSON list of entries and is
written atomically so a crash mid-save never corrupts the store.
"""
import json
import math
import os
import tempfile
import time

_HEXCHARS = set("0123456789abcdef")


def _valid_key(k):
    """A key must be exactly 12 lowercase hex chars. Uppercase is rejected, not
    repaired: keys arrive already normalized from the engine."""
    return isinstance(k, str) and len(k) == 12 and all(c in _HEXCHARS for c in k)


def _norm_uid(uid):
    """Lowercase a uid and strip spaces, or return None when falsy."""
    if not uid:
        return None
    return "".join(str(uid).split()).lower()


class LearnedKeyCache:
    MAX_ENTRIES = 512          # quota: total learned keys kept
    DEFAULT_TOP_N = 128        # default number returned by top_keys
    MAX_UIDS_PER_KEY = 32      # cap uids list length per entry

    def __init__(self, path=None, now=None):
        if path is None:
            path = os.environ.get("X7_LEARNED_PATH") or os.path.expanduser(
                "~/Library/Application Support/tenor-rekey/learned_keys.json")
        self.path = path
        self._now = now or time.time
        self.load()

    # ---- persistence ----
    def load(self):
        """Read entries from disk. A missing file or any corrupt/malformed JSON
        yields an empty store and never raises. Each entry is validated and
        repaired; entries without a valid key are dropped."""
        self.entries = []
        try:
            with open(self.path, "r") as f:
                raw = json.load(f)
        except FileNotFoundError:
            return
        except (OSError, ValueError):
            # An existing but unreadable or corrupt file loads as empty; a later
            # save overwrites it. For this re-learnable cache that is acceptable.
            return
        if not isinstance(raw, list):
            return
        # Coalesce by key so a hand-edited file with duplicate keys cannot distort
        # ranking, stats, or quota (record/top_keys only see the first duplicate).
        by_key = {}
        for e in raw:
            fixed = self._coerce_entry(e)
            if fixed is None:
                continue
            k = fixed["key"]
            prev = by_key.get(k)
            by_key[k] = self._merge(prev, fixed) if prev else fixed
        self.entries = list(by_key.values())

    def _merge(self, a, b):
        """Fold a duplicate on-disk key into one entry."""
        uids = a["uids"] + [u for u in b["uids"] if u not in a["uids"]]
        return {
            "key": a["key"],
            "hits": a["hits"] + b["hits"],
            "last_used": max(a["last_used"], b["last_used"]),
            "first_seen": min(a["first_seen"], b["first_seen"]),
            "uids": uids[-self.MAX_UIDS_PER_KEY:],
            "site": a["site"] or b["site"],
        }

    def _coerce_entry(self, e):
        if not isinstance(e, dict) or not _valid_key(e.get("key")):
            return None
        try:
            hits = int(e.get("hits", 1))
        except (TypeError, ValueError):
            hits = 1
        uids = []
        for u in (e.get("uids") if isinstance(e.get("uids"), list) else []):
            nu = _norm_uid(u)
            if nu:
                uids.append(nu)
        site = e.get("site")
        return {
            "key": e["key"],
            "hits": max(1, hits),
            "last_used": self._as_float(e.get("last_used")),
            "first_seen": self._as_float(e.get("first_seen")),
            "uids": uids[-self.MAX_UIDS_PER_KEY:],
            "site": site if isinstance(site, str) else None,
        }

    def _as_float(self, v):
        try:
            f = float(v)
        except (TypeError, ValueError):
            return self._now()
        # Reject NaN/inf: they make the (hits, last_used) ordering non-total and
        # are not valid JSON to write back.
        return f if math.isfinite(f) else self._now()

    def save(self):
        """Atomic write: temp file in the target dir, then os.replace onto path."""
        parent = os.path.dirname(self.path) or "."
        os.makedirs(parent, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=parent, prefix=".learned-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(self.entries, f, allow_nan=False)
            os.replace(tmp, self.path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # ---- query ----
    def top_keys(self, uid=None, limit=None):
        """Ranked key hex strings, deduped, at most `limit` (default DEFAULT_TOP_N).
        Entries whose uids include the given uid rank ahead of the rest; within a
        group, order by (hits desc, last_used desc)."""
        limit = self.DEFAULT_TOP_N if limit is None else limit
        if not isinstance(limit, int) or limit <= 0:
            return []
        nuid = _norm_uid(uid)
        rank = lambda e: (-e["hits"], -e["last_used"])
        matched = sorted((e for e in self.entries if nuid and nuid in e["uids"]), key=rank)
        others = sorted((e for e in self.entries if not (nuid and nuid in e["uids"])), key=rank)
        out, seen = [], set()
        for e in matched + others:
            k = e["key"]
            if _valid_key(k) and k not in seen:
                seen.add(k)
                out.append(k)
            if len(out) >= limit:
                break
        return out

    def _find(self, key):
        for e in self.entries:
            if e["key"] == key:
                return e
        return None

    # ---- update ----
    def record(self, recovered, uid=None, site=None):
        """Record verified keys. Invalid keys are silently skipped. Returns the
        number of keys recorded (new entries plus incremented ones)."""
        nuid = _norm_uid(uid)
        now = self._now()
        count = 0
        for key in recovered:
            if not _valid_key(key):
                continue
            count += 1
            e = self._find(key)
            if e is None:
                self.entries.append({
                    "key": key, "hits": 1, "last_used": now, "first_seen": now,
                    "uids": [nuid] if nuid else [], "site": site,
                })
                continue
            e["hits"] += 1
            e["last_used"] = now
            if site is not None:
                e["site"] = site
            if nuid and nuid not in e["uids"]:
                e["uids"].append(nuid)
                if len(e["uids"]) > self.MAX_UIDS_PER_KEY:
                    e["uids"] = e["uids"][-self.MAX_UIDS_PER_KEY:]
        if len(self.entries) > self.MAX_ENTRIES:
            # evict the lowest-ranked (least hits, then least recent) from the front
            self.entries.sort(key=lambda e: (e["hits"], e["last_used"]))
            self.entries = self.entries[len(self.entries) - self.MAX_ENTRIES:]
        self.save()
        return count

    def stats(self):
        top = sorted(self.entries, key=lambda e: (-e["hits"], -e["last_used"]))[:20]
        return {
            "count": len(self.entries),
            "total_hits": sum(e["hits"] for e in self.entries),
            "top": [{"key": e["key"], "hits": e["hits"], "last_used": e["last_used"],
                     "uids": len(e["uids"]), "site": e["site"]} for e in top],
        }

    def clear(self):
        self.entries = []
        self.save()
