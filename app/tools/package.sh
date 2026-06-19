#!/usr/bin/env bash
# Package tenor/rekey into a self-contained, relocatable .app + a drag-to-
# Applications .dmg. The shipped bundle carries its own python runtime, the
# probe engine + key dictionary, and libhidapi - so it runs on a clean macOS
# account with no Homebrew, no Command Line Tools, nothing on PATH.
#
# Signing is ad-hoc (founder's own Macs). Developer-ID + notarization is a later
# step that needs the founder's Apple certificate.
#
#   usage: app/tools/package.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "$HERE/.." && pwd)"          # .../app
REPO="$(cd "$APP_DIR/.." && pwd)"          # repo root
PROBE="$REPO/probe"
DIST="$APP_DIR/dist"
CACHE="$DIST/.cache"
BUILD="$APP_DIR/build"

# Relocatable CPython (python-build-standalone, install_only = full stdlib + ctypes).
PY_VER="3.12.13"
PY_TAG="20260610"
PY_TARBALL="cpython-${PY_VER}+${PY_TAG}-aarch64-apple-darwin-install_only_stripped.tar.gz"
PY_URL="https://github.com/astral-sh/python-build-standalone/releases/download/${PY_TAG}/${PY_TARBALL}"
# Pinned to the GitHub release asset digest - this exact runtime gets baked into a
# signed bundle, so a swapped tarball would be silently shipped. Verified each run,
# including the cache, so a poisoned cache cannot survive.
PY_SHA256="f0a7fa7decc75df2b1a789329a44f657c4a15c0a683f197ce46a5cb621bc6ef4"

# Runtime engine modules (the daemon + its import graph) and the dictionary.
PROBE_MODULES=(x7d.py x7lib.py x7.py x7hid.py x7_init.py x7crypto.py crapto1.py)

echo "==> 1/6  build release .app"
cd "$APP_DIR"
xcodegen generate >/dev/null
xcodebuild -project tenorrekey.xcodeproj -scheme tenorrekey -configuration Release \
    -derivedDataPath build CODE_SIGNING_ALLOWED=NO build >/dev/null
APP="$BUILD/Build/Products/Release/tenorrekey.app"
[ -d "$APP" ] || { echo "build produced no app at $APP"; exit 1; }

STAGE="$DIST/tenor-rekey.app"
rm -rf "$STAGE"; mkdir -p "$DIST"
cp -R "$APP" "$STAGE"
RES="$STAGE/Contents/Resources"
FW="$STAGE/Contents/Frameworks"
mkdir -p "$FW"

echo "==> 2/6  vendor python runtime"
mkdir -p "$CACHE"
if [ ! -f "$CACHE/$PY_TARBALL" ]; then
    echo "    downloading $PY_TARBALL"
    curl -fsSL "$PY_URL" -o "$CACHE/$PY_TARBALL"
fi
echo "$PY_SHA256  $CACHE/$PY_TARBALL" | shasum -a 256 -c - >/dev/null \
    || { echo "python runtime checksum mismatch - refusing to bundle"; rm -f "$CACHE/$PY_TARBALL"; exit 1; }
rm -rf "$RES/python"
tar -xzf "$CACHE/$PY_TARBALL" -C "$RES"        # extracts a 'python' dir
[ -x "$RES/python/bin/python3" ] || { echo "python/bin/python3 missing after extract"; exit 1; }
# trim test suites + caches to keep the bundle lean (engine never imports them)
find "$RES/python/lib" -type d -name "test" -prune -exec rm -rf {} + 2>/dev/null || true
find "$RES/python/lib" -type d -name "__pycache__" -prune -exec rm -rf {} + 2>/dev/null || true

echo "==> 3/6  vendor probe engine + dictionary"
rm -rf "$RES/probe"; mkdir -p "$RES/probe/dict"
for m in "${PROBE_MODULES[@]}"; do cp "$PROBE/$m" "$RES/probe/"; done
cp "$PROBE/dict/mfc_keys.dic" "$RES/probe/dict/"

echo "==> 4/6  vendor libhidapi"
HIDAPI_SRC=""
for c in /opt/homebrew/lib/libhidapi.dylib /usr/local/lib/libhidapi.dylib; do
    [ -e "$c" ] && { HIDAPI_SRC="$(readlink -f "$c" 2>/dev/null || python3 -c "import os,sys;print(os.path.realpath(sys.argv[1]))" "$c")"; break; }
done
[ -n "$HIDAPI_SRC" ] || { echo "libhidapi not found (brew install hidapi)"; exit 1; }
cp "$HIDAPI_SRC" "$FW/libhidapi.dylib"
install_name_tool -id "@rpath/libhidapi.dylib" "$FW/libhidapi.dylib" 2>/dev/null || true

echo "==> 5/6  pre-compile + ad-hoc sign"
# Pre-generate every .pyc now so they are sealed by the signature; the app also
# launches python with PYTHONDONTWRITEBYTECODE=1 so it never writes one at runtime
# (a code-signed bundle that mutates itself breaks its own seal).
"$RES/python/bin/python3" -m compileall -q "$RES/python/lib" "$RES/probe" >/dev/null 2>&1 || true
# sign inside-out: the dylib + every mach-o the python tree ships, then the app.
codesign --force -s - "$FW/libhidapi.dylib"
find "$RES/python" \( -name "*.dylib" -o -name "*.so" \) -exec codesign --force -s - {} + 2>/dev/null || true
find "$RES/python/bin" -type f -perm -111 -exec codesign --force -s - {} + 2>/dev/null || true
codesign --force --deep -s - "$STAGE"
codesign --verify --deep "$STAGE" && echo "    codesign verify OK"

echo "==> 6/6  build dmg"
DMG="$DIST/tenor-rekey.dmg"
DMG_STAGE="$DIST/.dmg"
rm -rf "$DMG_STAGE" "$DMG"; mkdir -p "$DMG_STAGE"
cp -R "$STAGE" "$DMG_STAGE/tenor-rekey.app"
ln -s /Applications "$DMG_STAGE/Applications"
[ -f "$APP_DIR/Resources/AppIcon.icns" ] && cp "$APP_DIR/Resources/AppIcon.icns" "$DMG_STAGE/.VolumeIcon.icns"
hdiutil create -volname "tenor rekey" -srcfolder "$DMG_STAGE" -ov -format UDZO "$DMG" >/dev/null
rm -rf "$DMG_STAGE"

echo ""
echo "built:"
echo "  app  $STAGE"
echo "  dmg  $DMG  ($(du -h "$DMG" | cut -f1))"
