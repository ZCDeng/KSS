#!/usr/bin/env bash
# U8：构建自包含、Developer-ID 签名的 .app（KTD6 最小签名）。
#
# 与 build_and_run.sh（dev，靠 KSS_PROJECT_ROOT=repo）不同，本脚本把代码 baseline
# 拷进 Contents/Resources（bundle-mode resolveRoots 找 Resources/scripts/kss_app_bridge.py），
# Python 运行时不入包 —— 首启由 U2 bootstrap 到 ~/Library/Application Support/KSS/venv。
#
# 前置：完整 Xcode（CLT 无 codesign/notarytool）+ Keychain 内 Developer ID Application 证书。
# 用法：KSS_SIGN_IDENTITY="Developer ID Application: 你的名字 (TEAMID)" script/sign_and_build.sh
set -euo pipefail

APP_NAME="KSSDesktop"
BUNDLE_ID="com.zcdeng.KSSDesktop"
MIN_SYSTEM_VERSION="14.0"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_DIR="$ROOT_DIR/dist"
APP_BUNDLE="$DIST_DIR/$APP_NAME.app"
APP_CONTENTS="$APP_BUNDLE/Contents"
APP_MACOS="$APP_CONTENTS/MacOS"
APP_RESOURCES="$APP_CONTENTS/Resources"
APP_BINARY="$APP_MACOS/$APP_NAME"
INFO_PLIST="$APP_CONTENTS/Info.plist"
ENTITLEMENTS="$ROOT_DIR/script/KSSDesktop.entitlements"

# ---- 签名身份解析（缺则大声失败）----
SIGN_IDENTITY="${KSS_SIGN_IDENTITY:-}"
if [ -z "$SIGN_IDENTITY" ]; then
  SIGN_IDENTITY="$(security find-identity -v -p codesigning 2>/dev/null \
    | grep -o 'Developer ID Application:[^"]*' | head -1 || true)"
fi
if [ -z "$SIGN_IDENTITY" ]; then
  echo "ERROR：未找到 Developer ID Application 证书。" >&2
  echo "  设 KSS_SIGN_IDENTITY 或在 Keychain 导入证书后重试。" >&2
  echo "  当前可用签名身份：" >&2
  security find-identity -v -p codesigning >&2 || true
  exit 1
fi
echo "签名身份：$SIGN_IDENTITY"

cd "$ROOT_DIR"
pkill -x "$APP_NAME" >/dev/null 2>&1 || true
# 强制 swiftpm 原生 build-system（与 build_and_run.sh 一致）：默认 build system
# 产出 Contents/ 布局的资源包，运行时 Bundle.module 定位不到 →
# resource_bundle_accessor.swift 启动即 SIGTRAP。native 落平铺资源包，布局可被找到。
SWIFT_BUILD_FLAGS="-c release --build-system native"
swift build $SWIFT_BUILD_FLAGS
BUILD_BIN_PATH="$(swift build $SWIFT_BUILD_FLAGS --show-bin-path)"

# ---- 组装 bundle ----
rm -rf "$APP_BUNDLE"
mkdir -p "$APP_MACOS" "$APP_RESOURCES"
cp "$BUILD_BIN_PATH/$APP_NAME" "$APP_BINARY"
chmod +x "$APP_BINARY"
[ -f "$ROOT_DIR/script/AppIcon.icns" ] && cp "$ROOT_DIR/script/AppIcon.icns" "$APP_RESOURCES/AppIcon.icns"
RESOURCE_BUNDLE="${APP_NAME}_${APP_NAME}.bundle"
[ -d "$BUILD_BIN_PATH/$RESOURCE_BUNDLE" ] && cp -R "$BUILD_BIN_PATH/$RESOURCE_BUNDLE" "$APP_MACOS/$RESOURCE_BUNDLE"

# ---- 代码 baseline 进 Resources（bundle-mode 的签名内脚本源；KTD7 第三层兜底）----
# 运行时 venv 不拷（U2 bootstrap 到 state root）；仅拷代码 + 依赖清单。
# deploy/launchd：cron-rerun 白名单真源；缺则 App 内重跑报 unknown label。
for item in scripts kss deploy pyproject.toml uv.lock backtest_etf_radar.py run_scanner.sh; do
  if [ -e "$ROOT_DIR/$item" ]; then
    cp -R "$ROOT_DIR/$item" "$APP_RESOURCES/$item"
  fi
done
# 清理拷入的字节码缓存，避免签名包含易变内容。
find "$APP_RESOURCES" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true

cat >"$INFO_PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleExecutable</key><string>$APP_NAME</string>
  <key>CFBundleIdentifier</key><string>$BUNDLE_ID</string>
  <key>CFBundleName</key><string>$APP_NAME</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$(git -C "$ROOT_DIR" describe --tags --always 2>/dev/null || echo "0.1.0")</string>
  <key>LSMinimumSystemVersion</key><string>$MIN_SYSTEM_VERSION</string>
  <key>NSPrincipalClass</key><string>NSApplication</string>
</dict>
</plist>
PLIST

# ---- 签名（KTD6：只签 .app + hardened runtime；运行时是子进程，无逐 dylib 循环）----
# 先签内嵌资源 bundle（若有），再签顶层 .app。
# SwiftPM 资源包是平铺目录（无 Info.plist）→ codesign 拒签；补最小 Info.plist 使其成合法 bundle。
if [ -d "$APP_MACOS/$RESOURCE_BUNDLE" ]; then
  # SwiftPM 资源包布局二选一，决定是否补 Info.plist：
  #  - 旧(flat / --build-system native)：平铺目录无 Info.plist → codesign 拒签，
  #    补根级最小 Info.plist 使其成合法 bundle。
  #  - 新(swiftpm 默认 build system)：已是 Contents/Info.plist 标准 bundle → 直接签；
  #    若再往根目录塞 Info.plist 反而触发「unsealed contents present in the bundle root」。
  if [ ! -f "$APP_MACOS/$RESOURCE_BUNDLE/Contents/Info.plist" ]; then
    cat >"$APP_MACOS/$RESOURCE_BUNDLE/Info.plist" <<RESPLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleIdentifier</key><string>${BUNDLE_ID}.resources</string>
  <key>CFBundleName</key><string>${RESOURCE_BUNDLE%.bundle}</string>
  <key>CFBundlePackageType</key><string>BNDL</string>
  <key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
</dict>
</plist>
RESPLIST
  fi
  codesign --force --options runtime --timestamp \
    --sign "$SIGN_IDENTITY" "$APP_MACOS/$RESOURCE_BUNDLE"
fi
codesign --force --options runtime --timestamp \
  --entitlements "$ENTITLEMENTS" \
  --sign "$SIGN_IDENTITY" "$APP_BUNDLE"

echo "验证签名："
codesign --verify --strict --verbose=2 "$APP_BUNDLE"
codesign -dv --verbose=4 "$APP_BUNDLE" 2>&1 | grep -E 'Authority|Identifier|Runtime' || true

# ---- 公证（DEFERRED-but-ready：证书/凭据到位后取消注释）----
# 自用多机不强制公证；若要消除第二台机首次「无法验证」弹窗，存好 app-specific
# 密码到 Keychain profile 后启用：
#
#   xcrun notarytool submit "$APP_BUNDLE" --keychain-profile "kss-notary" --wait
#   xcrun stapler staple "$APP_BUNDLE"

echo ""
echo "完成：$APP_BUNDLE"
echo "自用机器：直接拷贝运行；第二台首次可能需右键『打开』一次（未公证）。"
echo "首启会 uv bootstrap Python 运行时到 ~/Library/Application Support/KSS/venv（需联网 + uv）。"
