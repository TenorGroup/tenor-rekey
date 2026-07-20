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
# The first three are self-contained (libc + pthreads only).
#
#   hardnested    hard-PRNG (MFC EV1) attack (argv: nonces.bin ; pm3 nonce file)
#
# hardnested is the ciphertext-only attack for hardened-PRNG / EV1 cards. It needs
# the larger HardnestedRecovery tree (vendored under src/hardnested_recovery/) plus
# liblzma to inflate the precomputed bitflip tables in tables.c at runtime. We link
# the SYSTEM liblzma (-llzma): macOS ships /usr/lib/liblzma.5.dylib and the SDK the
# liblzma.tbd stub, so no CMake FetchContent / network and nothing to bundle in P5
# (the dylib is present on every macOS). Only the <lzma.h> API headers are absent
# from the SDK, so the 0BSD xz headers are vendored under lzma_headers/; the build
# stays fully offline and self-contained.
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

# hardnested is built from its own isolated subtree (src/hardnested_recovery/),
# which ships its OWN crapto1/crypto1/parity that differ from the flat ones above.
# Keep its include path separate (no -I$SRC) so those headers do not shadow. Link
# the system liblzma; vendored 0BSD headers under lzma_headers/ provide <lzma.h>.
HN="$SRC/hardnested_recovery"
HN_INC="-I$HN -I$HN/pm3 -I$HN/hardnested -I$HN/lzma_headers"
HN_SRCS="$HN/hardnested_main.c $HN/cmdhfmfhard.c $HN/crapto1.c $HN/crypto1.c \
$HN/hardnested/hardnested_bf_core.c $HN/hardnested/hardnested_bruteforce.c \
$HN/hardnested/hardnested_bitarray_core.c $HN/hardnested/tables.c \
$HN/pm3/ui.c $HN/pm3/util.c $HN/pm3/commonutil.c $HN/pm3/util_posix.c"

echo "building hardnested (compiles the 5MB tables.c, takes a moment)"
$CC -O3 -D_GNU_SOURCE -Wno-deprecated-declarations $ARCH $HN_INC $HN_SRCS \
    -llzma -lpthread -lm -o "$BIN/hardnested"

echo "done -> $BIN"
ls -l "$BIN"
