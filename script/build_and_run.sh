#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-run}"
APP_NAME="KSSDesktop"
BUNDLE_ID="com.zcdeng.KSSDesktop"
MIN_SYSTEM_VERSION="14.0"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_DIR="$ROOT_DIR/dist"
APP_BUNDLE="$DIST_DIR/$APP_NAME.app"
APP_CONTENTS="$APP_BUNDLE/Contents"
APP_MACOS="$APP_CONTENTS/MacOS"
APP_BINARY="$APP_MACOS/$APP_NAME"
INFO_PLIST="$APP_CONTENTS/Info.plist"

pkill -x "$APP_NAME" >/dev/null 2>&1 || true

cd "$ROOT_DIR"
swift build
BUILD_BIN_PATH="$(swift build --show-bin-path)"
BUILD_BINARY="$BUILD_BIN_PATH/$APP_NAME"

rm -rf "$APP_BUNDLE"
mkdir -p "$APP_MACOS"
APP_RESOURCES="$APP_CONTENTS/Resources"
mkdir -p "$APP_RESOURCES"
cp "$BUILD_BINARY" "$APP_BINARY"
chmod +x "$APP_BINARY"

# App icon (red K logo) → dock / Finder.
if [ -f "$ROOT_DIR/script/AppIcon.icns" ]; then
  cp "$ROOT_DIR/script/AppIcon.icns" "$APP_RESOURCES/AppIcon.icns"
fi

# Copy the SwiftPM resource bundle (chart.html + lightweight-charts) next to the
# binary so Bundle.module resolves the embedded TradingView chart at runtime.
RESOURCE_BUNDLE="${APP_NAME}_${APP_NAME}.bundle"
if [ -d "$BUILD_BIN_PATH/$RESOURCE_BUNDLE" ]; then
  cp -R "$BUILD_BIN_PATH/$RESOURCE_BUNDLE" "$APP_MACOS/$RESOURCE_BUNDLE"
fi

cat >"$INFO_PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleExecutable</key>
  <string>$APP_NAME</string>
  <key>CFBundleIdentifier</key>
  <string>$BUNDLE_ID</string>
  <key>CFBundleName</key>
  <string>$APP_NAME</string>
  <key>CFBundleIconFile</key>
  <string>AppIcon</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>LSMinimumSystemVersion</key>
  <string>$MIN_SYSTEM_VERSION</string>
  <key>NSPrincipalClass</key>
  <string>NSApplication</string>
</dict>
</plist>
PLIST

open_app() {
  KSS_PROJECT_ROOT="$ROOT_DIR" /usr/bin/open -n "$APP_BUNDLE"
}

case "$MODE" in
  run)
    open_app
    ;;
  --debug|debug)
    KSS_PROJECT_ROOT="$ROOT_DIR" lldb -- "$APP_BINARY"
    ;;
  --logs|logs)
    open_app
    /usr/bin/log stream --info --style compact --predicate "process == \"$APP_NAME\""
    ;;
  --telemetry|telemetry)
    open_app
    /usr/bin/log stream --info --style compact --predicate "subsystem == \"$BUNDLE_ID\""
    ;;
  --verify|verify)
    open_app
    sleep 2
    pgrep -x "$APP_NAME" >/dev/null
    ;;
  *)
    echo "usage: $0 [run|--debug|--logs|--telemetry|--verify]" >&2
    exit 2
    ;;
esac
