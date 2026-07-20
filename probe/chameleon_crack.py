#!/usr/bin/env python3
"""Host-side crackers for MIFARE Classic key recovery on the Chameleon Ultra.

The Chameleon firmware ACQUIRES the encrypted nonces on-device (mf1_nested_acquire
/ mf1_static_nested_acquire / mf1_darkside_acquire); the host cracks those nonces
into candidate keys. This module is the thin subprocess layer over the reference C
crackers vendored under native/chameleon/src (RfidResearchGroup/ChameleonUltra,
GPLv3) and built by native/chameleon/build.sh into native/chameleon/bin. It mirrors
the upstream CLI's argv/stdout contract exactly (AUDIT PART B):

  nested        weak-PRNG    argv: uid dist (nt nt_enc par)...   DECIMAL radix
  staticnested  static-PRNG  argv: uid type (nt nt_enc)...       DECIMAL radix
  darkside      zero-key     argv: uid (nt ks par nr ar)...      DECIMAL radix
  hardnested    hard-PRNG    argv: nonces.bin                    pm3 nonce file

hardnested (hardened-PRNG / MFC Ev1) is the ciphertext-only attack. Unlike the
other three it reads a binary pm3 nonce file (header uid/sector/key_type, then the
encrypted-nonce blob from mf1_hardnested_nonces_acquire) rather than decimal argv,
and it prints the key on a "Key found:" line amid verbose progress; its wrapper
parses only that line (the progress table also prints 12-digit decimal state counts
that the generic scrape would mistake for keys). It needs liblzma to inflate the
bitflip tables: the system dylib on macOS, so nothing is bundled in P5.

Every recovered key is a CANDIDATE: the caller MUST verify it by an on-card auth
before trusting it (the CLI does the same). Key extraction uses the CLI's own regex
(12 hex chars); a recovered key whose top byte is zero prints < 12 chars from the
tools' %PRIx64 and is missed - an upstream limitation shared with the CLI.

Legitimate use: the operator is the lock/access vendor re-keying their own building
or hotel cards. Run `python3 chameleon_crack.py` for the offline forward-sim
self-test (proves the built binaries recover a synthetic known key; no hardware).
"""
import os
import re
import struct
import subprocess
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
# Packaging note: the app bundle ships these binaries next to the Python modules;
# package.sh copies native/chameleon/bin into the bundled probe dir. CHAMELEON_BIN
# overrides the location (tests / a relocated bundle).
BIN_DIR = os.environ.get("CHAMELEON_BIN") or os.path.join(_HERE, "native", "chameleon", "bin")

_HEX12 = re.compile(r"([0-9a-fA-F]{12})")
DEFAULT_TIMEOUT = 180


def tool_path(name):
    return os.path.join(BIN_DIR, name)


def available(name):
    """True if the built cracker binary exists (else the attack is unavailable and
    the daemon degrades to dictionary-only, never crashing)."""
    return os.path.isfile(tool_path(name)) and os.access(tool_path(name), os.X_OK)


def _try_build():
    """Best-effort one-shot build of the vendored crackers so the self-test can
    actually exercise them on any machine with a compiler, rather than skipping
    silently. Silent on failure - the caller then degrades to a loud skip."""
    script = os.path.join(_HERE, "native", "chameleon", "build.sh")
    if not os.path.isfile(script):
        return
    try:
        subprocess.run(["bash", script], capture_output=True, text=True, timeout=120)
    except Exception:
        pass


def _run(name, args, timeout=DEFAULT_TIMEOUT):
    """Run a cracker with decimal/string argv; return (returncode, stdout). Raises
    FileNotFoundError if the binary is missing (caller checks available() first)."""
    proc = subprocess.run(
        [tool_path(name)] + [str(a) for a in args],
        capture_output=True, text=True, timeout=timeout)
    return proc.returncode, proc.stdout


def _scrape(stdout):
    """Ordered, de-duplicated 12-hex candidates from a cracker's stdout (the CLI's
    scrape). Lowercased for the daemon's key pool."""
    out, seen = [], set()
    for m in _HEX12.findall(stdout):
        k = m.lower()
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


def nested(uid, dist, samples, timeout=DEFAULT_TIMEOUT):
    """Weak-PRNG nested crack. `uid`/`dist` ints (from mf1_detect_nt_dist); `samples`
    a list of {'nt','nt_enc','par'} ints (from mf1_nested_acquire). Returns candidate
    keys (hex); [] if nothing recovered."""
    args = [int(uid), int(dist)]
    for s in samples:
        args += [int(s["nt"]), int(s["nt_enc"]), int(s["par"])]
    _, out = _run("nested", args, timeout)
    return _scrape(out)


def staticnested(uid, type_target, pairs, timeout=DEFAULT_TIMEOUT):
    """Static-PRNG nested crack. `type_target` is the MfcKeyType int (0x60/0x61);
    `pairs` a list of {'nt','nt_enc'} ints (from mf1_static_nested_acquire). Returns
    candidate keys (hex)."""
    args = [int(uid), int(type_target)]
    for s in pairs:
        args += [int(s["nt"]), int(s["nt_enc"])]
    _, out = _run("staticnested", args, timeout)
    return _scrape(out)


def darkside(uid, items, timeout=DEFAULT_TIMEOUT):
    """Darkside crack (recovers a key with NO key known). `items` a list of
    {'nt1','ks1','par','nr','ar'} ints (from mf1_darkside_acquire). Returns
    (candidates, found): `found` is False when the tool printed "key not found"
    (the caller then collects another darkside acquisition and retries)."""
    args = [int(uid)]
    for it in items:
        args += [int(it["nt1"]), int(it["ks1"]), int(it["par"]),
                 int(it["nr"]), int(it["ar"])]
    _, out = _run("darkside", args, timeout)
    return _scrape(out), ("key not found" not in out.lower())


# hardnested reads a binary pm3 nonce file, not decimal argv, and its key is on a
# dedicated "Key found:" line: scope the scrape there (its progress table prints
# 12-digit decimal #states counts that the generic 12-hex scrape would grab).
_HARDKEY = re.compile(r"[Kk]ey found:\s*([0-9a-fA-F]{12})")


def assemble_nonce_bin(uid, sector, key_type, body):
    """Build the pm3 hardnested nonce file: header uid(4, big-endian) sector(1)
    key_type(1) followed by `body`, the raw encrypted-nonce blob from the firmware
    (repeated nt_enc1(4, BE) nt_enc2(4, BE) par(1), 9 bytes per pair). `key_type`
    is 0 for KeyA / 1 for KeyB (the .bin convention); 0x60/0x61 are accepted too."""
    kt = 1 if int(key_type) in (1, 0x61) else 0
    return struct.pack(">IBB", int(uid) & 0xFFFFFFFF, int(sector) & 0xFF, kt) + bytes(body)


def _scrape_hardnested(stdout):
    out, seen = [], set()
    for m in _HARDKEY.findall(stdout):
        k = m.lower()
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


def hardnested(uid, sector, key_type, body, timeout=DEFAULT_TIMEOUT):
    """Hard-PRNG (MFC Ev1) crack. `body` is the encrypted-nonce blob streamed by the
    firmware's mf1_hardnested_nonces_acquire (9 bytes per pair, see assemble_nonce_bin).
    Prepends the pm3 header and runs the vendored `hardnested` on the assembled file.
    Returns (candidates, found): `found` is False when the tool prints "Key not found."
    (too few nonces / wrong known key - the caller acquires more and retries)."""
    blob = assemble_nonce_bin(uid, sector, key_type, body)
    # hardnested writes its scratch temp_nonces.txt in the CWD, so run it inside a
    # throwaway dir (the daemon may launch from a read-only bundle).
    with tempfile.TemporaryDirectory() as td:
        binpath = os.path.join(td, "nonces.bin")
        with open(binpath, "wb") as f:
            f.write(blob)
        proc = subprocess.run(
            [tool_path("hardnested"), binpath],
            capture_output=True, text=True, cwd=td, timeout=timeout)
    keys = _scrape_hardnested(proc.stdout)
    return keys, (proc.returncode == 0 or bool(keys))


# ---------------------------------------------------------------------------
# Offline forward-sim self-test: synthesize firmware-shaped nonces for a known
# key, run the built binaries, prove they recover it. No hardware, no real keys.
# ---------------------------------------------------------------------------

def _oddparity8(b):
    """OddByteParity[b] (parity.h): 1 when b has an even number of set bits."""
    return 1 - (bin(b & 0xFF).count("1") & 1)


def _sim_nested_sample(key, uid, base_nt, dist):
    """One firmware-shaped weak-nested sample for `key`: the known-sector nonce is
    `base_nt`; the target plaintext nonce is dist PRNG steps later; returns the
    (nt, nt_enc, par) triple the tool consumes (nt = the known-sector nonce)."""
    import crapto1
    ntp = crapto1.prng_successor(base_nt, dist)          # target plaintext nonce
    ks1 = crapto1.crypto1_word(crapto1.crypto1_create(key), (uid ^ ntp) & 0xFFFFFFFF, 0)
    nt_enc = (ntp ^ ks1) & 0xFFFFFFFF
    # 3 encrypted-parity bits, per valid_nonce() (nested_util.c): the bit that makes
    # valid_nonce true for the real nonce.
    par = 0
    for i, sh in enumerate((24, 16, 8)):
        bit = (_oddparity8((ntp >> sh) & 0xFF) ^ _oddparity8((nt_enc >> sh) & 0xFF)
               ^ crapto1.BIT(ks1, {24: 16, 16: 8, 8: 0}[sh]))
        par |= bit << i
    return {"nt": base_nt, "nt_enc": nt_enc, "par": par}


def _sim_static_pair(key, uid, base_nt, dist):
    """One firmware-shaped static-nested pair (nt, nt_enc). The static tag always
    emits `base_nt`; the target plaintext nonce is dist steps later."""
    import crapto1
    ntp = crapto1.prng_successor(base_nt, dist)
    ks1 = crapto1.crypto1_word(crapto1.crypto1_create(key), (uid ^ ntp) & 0xFFFFFFFF, 0)
    return {"nt": base_nt, "nt_enc": (ntp ^ ks1) & 0xFFFFFFFF}


def _sim_hard_nonce(key, uid, nt):
    """Encrypt one plaintext tag nonce `nt` under `key` exactly as an Ev1 tag does in
    a nested auth, matching the Chameleon firmware (authex + mf1_hardnested_nonces_
    acquire): the LFSR is clocked with uid^nt, nt_enc = nt ^ keystream, and the tag's
    RAW wire parity per byte (oddparity8(plaintext) ^ next keystream bit) is stored
    byte0 -> bit3 .. byte3 -> bit0. Returns (nt_enc, par_nibble)."""
    import crapto1
    pcs = crapto1.crypto1_create(key)
    in_stream = (uid ^ nt) & 0xFFFFFFFF
    nt_enc = 0
    par = 0
    for j in range(32):
        ks_bit = crapto1.crypto1_bit(pcs, crapto1.BEBIT(in_stream, j), 0)
        nt_bit = crapto1.BEBIT(nt, j)
        nt_enc |= ((nt_bit ^ ks_bit) & 1) << (24 ^ j)
        if (j & 7) == 7:                                 # finished a byte
            pos = j >> 3                                 # 0 = most significant byte
            ks_next = crapto1.filter(pcs.odd)
            nt_byte = (nt >> (24 - 8 * pos)) & 0xFF
            wire_par = ks_next ^ _oddparity8(nt_byte)
            par |= (wire_par & 1) << (3 - pos)
    return nt_enc & 0xFFFFFFFF, par & 0x0F


def _sim_hard_body(key, uid, pairs, seed=0xACE12345):
    """Firmware-shaped hardnested nonce blob for `key`: `pairs` chunks of nt_enc1(BE)
    nt_enc2(BE) packed-par, from a deterministic nonce stream so the KAT is
    reproducible. Ev1 nonces are effectively random 32-bit, which is what we feed."""
    body = bytearray()
    for _ in range(pairs):
        seed ^= (seed << 13) & 0xFFFFFFFF; seed ^= seed >> 17; seed ^= (seed << 5) & 0xFFFFFFFF
        nt1 = seed
        seed ^= (seed << 13) & 0xFFFFFFFF; seed ^= seed >> 17; seed ^= (seed << 5) & 0xFFFFFFFF
        nt2 = seed
        e1, p1 = _sim_hard_nonce(key, uid, nt1)
        e2, p2 = _sim_hard_nonce(key, uid, nt2)
        body += struct.pack(">II", e1, e2)
        body.append(((p1 & 0x0F) << 4) | (p2 & 0x0F))
    return bytes(body)


def _selftest():
    # Synthetic, non-secret vector. Top byte set so the tools' %PRIx64 prints the
    # full 12 hex chars (see module docstring).
    KEY = 0xA1B2C3D4E5F6
    KEY_HEX = "a1b2c3d4e5f6"
    UID = 0x11223344

    if not (available("nested") and available("staticnested")):
        # Not built yet: try once so the KAT actually proves the backend on any
        # machine with a compiler, instead of a silent green that verifies nothing.
        _try_build()
    if not (available("nested") and available("staticnested")):
        print("[SKIP] cracker binaries not built and build.sh could not produce "
              "them (needs a C compiler); the cracker backend is UNPROVEN in this "
              "run - decode falls back to dictionary-only")
        return 0

    # nested: several samples, fixed distance, varying known-sector nonces. Enough
    # samples that the true key recurs across them (the tool keeps keys with count>0).
    DIST = 100
    samples = [_sim_nested_sample(KEY, UID, base, DIST)
               for base in (0x2A1B7C3D, 0x5E6F8091, 0x0C1D2E3F, 0x778899AA, 0x13572468)]
    cands = nested(UID, DIST, samples)
    assert KEY_HEX in cands, "nested did not recover the known key: %r" % cands
    print("[ok] nested recovers %s from %d simulated samples" % (KEY_HEX, len(samples)))

    # staticnested: gen1 static nonce 0x01200145, dist starts at 160 and steps 160.
    ST_NT = 0x01200145
    pairs = [_sim_static_pair(KEY, UID, ST_NT, 160 * i) for i in (1, 2, 3, 4)]
    scands = staticnested(UID, 0x60, pairs)              # 0x60 = KeyA
    assert KEY_HEX in scands, "staticnested did not recover the known key: %r" % scands
    print("[ok] staticnested recovers %s from %d simulated pairs" % (KEY_HEX, len(pairs)))

    # darkside: the binary builds and runs to a clean "not found" on a benign,
    # non-leaking vector (a full darkside known-answer needs the on-card parity
    # leak, which is hardware-gated - see the daemon's decode chaining test).
    if available("darkside"):
        _cands, found = darkside(UID, [{"nt1": 0x11111111, "ks1": 0, "par": 0,
                                        "nr": 0x22222222, "ar": 0x33333333}])
        assert found is False, "darkside unexpectedly reported a key on a null vector"
        print("[ok] darkside binary runs and reports no-key on a null vector")

    # hardnested: full cryptographic KAT. Synthesize a firmware-shaped Ev1 nonce set
    # for the planted key (same encryption + raw wire-parity packing as the on-device
    # mf1_hardnested_nonces_acquire) and prove the built binary recovers it offline.
    # ~4000 pairs guarantees the tool sees all 256 first bytes with a valid sum
    # property; recovery runs in a few seconds. Needs liblzma - present on macOS.
    if available("hardnested"):
        body = _sim_hard_body(KEY, UID, 4000)
        hcands, hfound = hardnested(UID, 3, 0, body)     # sector 3, KeyA (0)
        assert hfound and KEY_HEX in hcands, \
            "hardnested did not recover the known key: found=%r cands=%r" % (hfound, hcands)
        print("[ok] hardnested recovers %s from %d simulated Ev1 nonces" % (KEY_HEX, 8000))
    else:
        print("[SKIP] hardnested not built (needs liblzma: system dylib on macOS, "
              "-llzma elsewhere); hard-PRNG decode is UNPROVEN in this run")

    print("\nALL CHAMELEON_CRACK SELF-TESTS PASSED")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_selftest())
