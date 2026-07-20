#!/usr/bin/env bash
# Build the MIFARE Classic key-recovery crackers used by chameleon_d.py.
#
# These are the reference C tools from RfidResearchGroup/ChameleonUltra
# (software/src, GPLv3), vendored verbatim under src/ with their GPL notices
# intact. The Chameleon firmware acquires the encrypted nonces on-device; the
# host cracks them into keys with these tools, exactly as the upstream CLI/GUI
# do. We build only the three tools the P1 decode chain drives:
#
#   nested        weak-PRNG nested attack   (argv: uid dist [nt nt_enc par]...)
#   staticnested  static-PRNG nested attack (argv: uid type [nt nt_enc]...)
#   darkside      zero-known-key foothold   (argv: uid [nt ks par nr ar]...)
#
# hardnested (hard-PRNG) is intentionally out of scope here: it needs the
# HardnestedRecovery tree plus a bundled liblzma and a large build, tracked as a
# follow-up. The three above are self-contained (libc + pthreads only).
#
# Output: bin/<tool> (git-ignored). Sources under src/ are tracked. Run:
#   probe/native/chameleon/build.sh
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="$HERE/src"
BIN="$HERE/bin"
mkdir -p "$BIN"

CC="${CC:-cc}"
# arm64 by default on Apple silicon; override ARCH= for a universal build, e.g.
#   ARCH="-arch arm64 -arch x86_64" probe/native/chameleon/build.sh
ARCH="${ARCH:--arch arm64}"
CFLAGS="-O3 -D_GNU_SOURCE -I$SRC -Wno-deprecated-declarations $ARCH"

COMMON="$SRC/common.c $SRC/crapto1.c $SRC/crypto1.c $SRC/bucketsort.c $SRC/parity.c"

echo "building nested"
$CC $CFLAGS $COMMON "$SRC/nested_util.c" "$SRC/nested.c" -lpthread -o "$BIN/nested"

echo "building staticnested"
$CC $CFLAGS $COMMON "$SRC/nested_util.c" "$SRC/staticnested.c" -lpthread -o "$BIN/staticnested"

echo "building darkside"
$CC $CFLAGS $COMMON "$SRC/mfkey.c" "$SRC/darkside.c" -o "$BIN/darkside"

echo "done -> $BIN"
ls -l "$BIN"
