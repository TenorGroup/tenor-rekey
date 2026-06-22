#!/usr/bin/env bash
# Package tenor/rekey into a self-contained, relocatable .app + a drag-to-
# Applications .dmg. The shipped bundle carries its own python runtime, the
# probe engine + key dictionary, and libhidapi - so it runs on a clean macOS
# account with no Homebrew, no Command Line Tools, nothing on PATH.
#
# Signing: a Developer ID Application cert (hardened runtime + entitlements) is
# used automatically if one is installed, else ad-hoc (runs on this Mac, not
# distributable). With a Developer ID cert + a stored notarytool credential the
# build also notarizes + staples the app and the dmg.
#
#   usage:  app/tools/package.sh                       # ad-hoc or Developer-ID-signed
#           NOTARY_PROFILE=tenor-notary app/tools/package.sh   # also notarize + staple
#   (one-time, by the founder, since it needs the Apple ID app-specific password:
#    xcrun notarytool store-credentials tenor-notary \
#        --apple-id <id> --team-id 35ZXMV2YHU --password <app-specific-password>)
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

echo "==> 1/7  build release .app"
cd "$APP_DIR"
xcodegen generate >/dev/null
xcodebuild -project tenorrekey.xcodeproj -scheme tenorrekey -configuration Release \
    -derivedDataPath build CODE_SIGNING_ALLOWED=NO build >/dev/null
APP="$BUILD/Build/Products/Release/tenorrekey.app"
[ -d "$APP" ] || { echo "build produced no app at $APP"; exit 1; }

STAGE="$DIST/tenor-rekey.app"
rm -rf "$STAGE"; mkdir -p "$DIST"
cp -R "$APP" "$STAGE"
# Brand the menu-bar / app-switcher name to "tenor/rekey" (the slash displays
# fine; the on-disk binary stays PRODUCT_NAME). Done before signing so the
# signature covers it.
/usr/libexec/PlistBuddy -c "Set :CFBundleName tenor/rekey" "$STAGE/Contents/Info.plist"
RES="$STAGE/Contents/Resources"
FW="$STAGE/Contents/Frameworks"
mkdir -p "$FW"

echo "==> 2/7  vendor python runtime"
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

echo "==> 3/7  vendor probe engine + dictionary"
rm -rf "$RES/probe"; mkdir -p "$RES/probe/dict"
for m in "${PROBE_MODULES[@]}"; do cp "$PROBE/$m" "$RES/probe/"; done
cp "$PROBE/dict/mfc_keys.dic" "$RES/probe/dict/"

echo "==> 4/7  vendor libhidapi"
HIDAPI_SRC=""
for c in /opt/homebrew/lib/libhidapi.dylib /usr/local/lib/libhidapi.dylib; do
    [ -e "$c" ] && { HIDAPI_SRC="$(readlink -f "$c" 2>/dev/null || python3 -c "import os,sys;print(os.path.realpath(sys.argv[1]))" "$c")"; break; }
done
[ -n "$HIDAPI_SRC" ] || { echo "libhidapi not found (brew install hidapi)"; exit 1; }
cp "$HIDAPI_SRC" "$FW/libhidapi.dylib"
install_name_tool -id "@rpath/libhidapi.dylib" "$FW/libhidapi.dylib" 2>/dev/null || true

echo "==> 5/7  pre-compile + sign"
# Pre-generate every .pyc now so they are sealed by the signature; the app also
# launches python with PYTHONDONTWRITEBYTECODE=1 so it never writes one at runtime
# (a code-signed bundle that mutates itself breaks its own seal).
"$RES/python/bin/python3" -m compileall -q "$RES/python/lib" "$RES/probe" >/dev/null 2>&1 || true

# Use a Developer ID Application cert if one is installed (notarizable: hardened
# runtime + entitlements + secure timestamp); otherwise ad-hoc (runs on this Mac,
# but cannot be notarized). An "Apple Development" cert is NOT a Developer ID cert.
SIGN_ID="$(security find-identity -v -p codesigning 2>/dev/null | sed -n 's/.*"\(Developer ID Application[^"]*\)".*/\1/p' | head -1)"
ENT="$HERE/tenorrekey.entitlements"
if [ -n "$SIGN_ID" ]; then
    echo "    Developer ID: $SIGN_ID  (hardened runtime + entitlements)"
    SIGN=(codesign --force --timestamp --options runtime -s "$SIGN_ID")
    SIGN_ENT=(codesign --force --timestamp --options runtime --entitlements "$ENT" -s "$SIGN_ID")
else
    echo "    no Developer ID Application cert found - ad-hoc signing (NOT notarizable)"
    SIGN=(codesign --force -s -)
    SIGN_ENT=(codesign --force -s -)
fi
# sign inside-out: the dylib + every mach-o the python tree ships, then the
# interpreter and the app last (the interpreter + app carry the entitlements; the
# interpreter is the process that loads the bundled libs via ctypes).
"${SIGN[@]}" "$FW/libhidapi.dylib"
find "$RES/python" \( -name "*.dylib" -o -name "*.so" \) -exec "${SIGN[@]}" {} + 2>/dev/null || true
find "$RES/python/bin" -type f -perm -111 -exec "${SIGN_ENT[@]}" {} + 2>/dev/null || true
"${SIGN_ENT[@]}" "$STAGE/Contents/MacOS/tenorrekey" 2>/dev/null || true
"${SIGN_ENT[@]}" "$STAGE"
codesign --verify --strict --deep "$STAGE" && echo "    codesign verify OK"

# Notarize the .app now (before the dmg) so the dmg ships a stapled app that runs
# offline. Needs a Developer ID signature AND a stored notarytool credential -
# create it once (the founder, it needs the Apple ID app-specific password):
#   xcrun notarytool store-credentials tenor-notary \
#       --apple-id <id> --team-id 35ZXMV2YHU --password <app-specific-password>
# then run:  NOTARY_PROFILE=tenor-notary app/tools/package.sh
NOTARIZE=0
if [ -n "$SIGN_ID" ] && [ -n "${NOTARY_PROFILE:-}" ]; then
    NOTARIZE=1
    echo "==> 6/7  notarize + staple app  (profile: $NOTARY_PROFILE)"
    APPZIP="$DIST/.notarize-app.zip"
    ditto -c -k --keepParent "$STAGE" "$APPZIP"
    xcrun notarytool submit "$APPZIP" --keychain-profile "$NOTARY_PROFILE" --wait
    xcrun stapler staple "$STAGE"
    rm -f "$APPZIP"
else
    echo "==> 6/7  notarize  SKIPPED (need a Developer ID cert + NOTARY_PROFILE=<profile>)"
fi

echo "==> 7/7  build dmg (styled drag-to-Applications)"
DMG="$DIST/tenor-rekey.dmg"
VOL="tenor rekey"
DSTAGE="$DIST/.dmgstage"; RW="$DIST/.rw.dmg"
python3 "$HERE/dmg_background.py" >/dev/null     # writes $DIST/.dmgbg/background.tiff
rm -rf "$DSTAGE" "$RW" "$DMG"; mkdir -p "$DSTAGE/.background"
cp -R "$STAGE" "$DSTAGE/tenor-rekey.app"
ln -s /Applications "$DSTAGE/Applications"
cp "$DIST/.dmgbg/background.tiff" "$DSTAGE/.background/background.tiff"
ICNS="$STAGE/Contents/Resources/AppIcon.icns"
[ -f "$ICNS" ] && cp "$ICNS" "$DSTAGE/.VolumeIcon.icns"
# writable image -> lay it out in Finder (background + icon slots) -> compress.
hdiutil create -srcfolder "$DSTAGE" -volname "$VOL" -fs HFS+ -format UDRW -ov "$RW" >/dev/null
DEV="$(hdiutil attach -readwrite -noverify -noautoopen "$RW" | egrep '^/dev/' | head -1 | awk '{print $1}')"
sleep 1
osascript <<APPLESCRIPT >/dev/null 2>&1 || echo "    (Finder layout skipped - automation not permitted; dmg still valid)"
tell application "Finder"
  tell disk "$VOL"
    open
    set current view of container window to icon view
    set toolbar visible of container window to false
    set statusbar visible of container window to false
    set the bounds of container window to {200, 120, 860, 562}
    set vo to the icon view options of container window
    set arrangement of vo to not arranged
    set icon size of vo to 128
    set text size of vo to 12
    set background picture of vo to file ".background:background.tiff"
    set position of item "tenor-rekey.app" of container window to {175, 205}
    set position of item "Applications" of container window to {485, 205}
    update without registering applications
    delay 1
    close
  end tell
end tell
APPLESCRIPT
[ -f "/Volumes/$VOL/.VolumeIcon.icns" ] && SetFile -a C "/Volumes/$VOL" 2>/dev/null || true
sync; hdiutil detach "$DEV" >/dev/null 2>&1 || hdiutil detach "$DEV" -force >/dev/null 2>&1
hdiutil convert "$RW" -format UDZO -imagekey zlib-level=9 -o "$DMG" >/dev/null
rm -rf "$RW" "$DSTAGE"
if [ "$NOTARIZE" = 1 ]; then
    echo "    notarize + staple dmg"
    xcrun notarytool submit "$DMG" --keychain-profile "$NOTARY_PROFILE" --wait
    xcrun stapler staple "$DMG"
fi

echo ""
echo "built:"
echo "  app  $STAGE$([ "$NOTARIZE" = 1 ] && echo "  (notarized + stapled)")"
echo "  dmg  $DMG  ($(du -h "$DMG" | cut -f1))$([ "$NOTARIZE" = 1 ] && echo "  (notarized + stapled)")"
