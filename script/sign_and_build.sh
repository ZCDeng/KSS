#!/usr/bin/env bash
# U8：构建自包含、Developer-ID 签名 + 公证的 .app（KTD6 最小签名 / KTD7 公证，
# plan 2026-07-12-005 / U10）。
#
# 与 build_and_run.sh（dev，靠 KSS_PROJECT_ROOT=repo）不同，本脚本把代码 baseline
# 拷进 Contents/Resources（bundle-mode resolveRoots 找 Resources/scripts/kss_app_bridge.py），
# Python 运行时不入包 —— 首启由 U2 bootstrap 到 ~/Library/Application Support/KSS/venv。
#
# 前置：完整 Xcode（CLT 无 codesign/notarytool）+ Keychain 内 Developer ID Application 证书
# + notarytool Keychain profile（一次性：`xcrun notarytool store-credentials <profile> \
#   --apple-id <id> --team-id <TEAMID> --password <app-specific-password>`；密码只这一次
#   人工输入，永不进脚本/仓库，之后凭 profile 名引用）。
# 用法：KSS_SIGN_IDENTITY="Developer ID Application: 你的名字 (TEAMID)" script/sign_and_build.sh
#      KSS_NOTARY_PROFILE=<profile> 覆盖默认 profile 名；KSS_SKIP_NOTARIZE=1 跳过公证（逃生舱）。
set -euo pipefail

NOTARY_PROFILE="${KSS_NOTARY_PROFILE:-Prism}"
SKIP_NOTARIZE="${KSS_SKIP_NOTARIZE:-0}"

APP_NAME="KSSDesktop"
BUNDLE_ID="com.zcdeng.KSSDesktop"
MIN_SYSTEM_VERSION="14.0"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_DIR="$ROOT_DIR/dist"
APP_BUNDLE="$DIST_DIR/$APP_NAME.app"
APP_CONTENTS="$APP_BUNDLE/Contents"
APP_MACOS="$APP_CONTENTS/MacOS"
APP_HELPERS="$APP_CONTENTS/Helpers"
APP_RESOURCES="$APP_CONTENTS/Resources"
APP_BINARY="$APP_MACOS/$APP_NAME"
INFO_PLIST="$APP_CONTENTS/Info.plist"
ENTITLEMENTS="$ROOT_DIR/script/KSSDesktop.entitlements"
NODE_ENTITLEMENTS="$ROOT_DIR/script/NodeHelper.entitlements"
PI_AI_BUILD_ROOT="$ROOT_DIR/.build/pi-ai-helper"
HARNESS_BUILD_ROOT="$ROOT_DIR/.build/harness-node"

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
# pi-ai is a signed, self-contained provider helper. Preparation pins both the
# Node runtime archive checksum and npm dependency lock; release bundles never
# fall back to a system Node installation.
KSS_PI_AI_OUTPUT_ROOT="$PI_AI_BUILD_ROOT" "$ROOT_DIR/script/prepare_pi_ai_helper.sh"
# Harness Node kernel: pinned Node + profile/plugins tree (npm ci in .build).
KSS_HARNESS_OUTPUT_ROOT="$HARNESS_BUILD_ROOT" "$ROOT_DIR/script/prepare_harness_node.sh"
# 强制 swiftpm 原生 build-system（与 build_and_run.sh 一致）：默认 build system
# 产出 Contents/ 布局的资源包，运行时 Bundle.module 定位不到 →
# resource_bundle_accessor.swift 启动即 SIGTRAP。native 落平铺资源包，布局可被找到。
SWIFT_BUILD_FLAGS="-c release --build-system native"
swift build $SWIFT_BUILD_FLAGS
swift build $SWIFT_BUILD_FLAGS --product KSSResearchSchedulerHelper
# 不调用 `swift build --show-bin-path`：native 会在 release/KSSDesktop 上 mkdir，
# 与已链接的同名二进制冲突（File exists）。
BUILD_BIN_PATH=""
for cand in \
  "$ROOT_DIR/.build/arm64-apple-macosx/release" \
  "$ROOT_DIR/.build/release" \
  "$ROOT_DIR/.build/x86_64-apple-macosx/release"; do
  if [ -x "$cand/$APP_NAME" ]; then BUILD_BIN_PATH="$cand"; break; fi
done
if [ -z "$BUILD_BIN_PATH" ]; then
  echo "ERROR：找不到 release 二进制 $APP_NAME" >&2
  exit 1
fi
echo "二进制：$BUILD_BIN_PATH/$APP_NAME"
SCHEDULER_HELPER="$BUILD_BIN_PATH/KSSResearchSchedulerHelper"
if [ ! -x "$SCHEDULER_HELPER" ]; then
  echo "ERROR：找不到 release 二进制 KSSResearchSchedulerHelper" >&2
  exit 1
fi

# ---- 组装 bundle ----
# 上次若误 chmod a-w Resources，普通 rm 会 Permission denied
if [ -d "$APP_BUNDLE" ]; then
  chmod -R u+w "$APP_BUNDLE" 2>/dev/null || true
  rm -rf "$APP_BUNDLE"
fi
mkdir -p "$APP_MACOS" "$APP_HELPERS" "$APP_RESOURCES"
cp "$BUILD_BIN_PATH/$APP_NAME" "$APP_BINARY"
chmod +x "$APP_BINARY"
cp "$SCHEDULER_HELPER" "$APP_HELPERS/KSSResearchSchedulerHelper"
chmod +x "$APP_HELPERS/KSSResearchSchedulerHelper"
[ -f "$ROOT_DIR/script/AppIcon.icns" ] && cp "$ROOT_DIR/script/AppIcon.icns" "$APP_RESOURCES/AppIcon.icns"
RESOURCE_BUNDLE="${APP_NAME}_${APP_NAME}.bundle"
APP_RESOURCE_BUNDLE="$APP_RESOURCES/$RESOURCE_BUNDLE"
[ -d "$BUILD_BIN_PATH/$RESOURCE_BUNDLE" ] && cp -R "$BUILD_BIN_PATH/$RESOURCE_BUNDLE" "$APP_RESOURCE_BUNDLE"

copy_resource_item() {
  local item="$1"
  local dest_parent="$2"

  if [ -e "$ROOT_DIR/$item" ]; then
    mkdir -p "$dest_parent"
    # rsync 排除缓存/状态，避免脏文件进签名包。
    # 不排除 'storage/'：代码 baseline 从不拷贝仓库顶层 storage/（不在 item 列表里），
    # 无锚点的 --exclude 'storage/' 会匹配树内任意深度同名目录——之前把 kss/storage/
    # （真实 Python 子包，kss.news.rewrite 运行期 import 它）也一并排除掉了，
    # 打包出的 app 点开资讯雷达切换赛道就 ModuleNotFoundError: No module named
    # 'kss.storage'。顶层 storage/ 真正的兜底是下面 `rm -rf "$APP_RESOURCES/storage"`
    # （只删顶层路径，不影响 kss/storage/）。
    if command -v rsync >/dev/null 2>&1; then
      rsync -a --delete \
        --exclude '.git/' --exclude '.git' --exclude '.DS_Store' \
        --exclude '__pycache__/' --exclude '*.py[cod]' \
        --exclude '.pytest_cache/' --exclude '.ruff_cache/' --exclude '*.egg-info/' \
        --exclude '.cache/' --exclude 'cache/' --exclude 'caches/' \
        --exclude '.omx/' --exclude '.codex/' --exclude 'state/' --exclude '.state/' --exclude 'logs/' \
        "$ROOT_DIR/$item" "$dest_parent/"
    else
      rm -rf "$dest_parent/$(basename "$item")"
      cp -R "$ROOT_DIR/$item" "$dest_parent/$(basename "$item")"
    fi
  fi
}

# ---- 代码 baseline 进 Resources（bundle-mode 的签名内脚本源；KTD7 第三层兜底）----
# 运行时 venv 不拷（U2 bootstrap 到 state root）；仅拷代码 + 依赖清单。
# deploy/launchd：cron-rerun 白名单真源；缺则 App 内重跑报 unknown label。
for item in scripts kss deploy pyproject.toml uv.lock backtest_etf_radar.py run_scanner.sh; do
  copy_resource_item "$item" "$APP_RESOURCES"
done

# ---- pi-ai provider helper（固定 Node 22.19.0 arm64 + pi-ai 0.82.1）----
cp -R "$PI_AI_BUILD_ROOT/runtime" "$APP_RESOURCES/pi-ai-runtime"
cp -R "$PI_AI_BUILD_ROOT/helper" "$APP_RESOURCES/pi-ai-helper"

# ---- DeepSeek Harness Node kernel（同一 Node 指纹；profile + plugins，非 git 内 node_modules）----
cp -R "$HARNESS_BUILD_ROOT/runtime" "$APP_RESOURCES/harness-runtime"
mkdir -p "$APP_RESOURCES/harness"
cp -R "$HARNESS_BUILD_ROOT/harness/kss-profile" "$APP_RESOURCES/harness/kss-profile"
cp -R "$HARNESS_BUILD_ROOT/harness/kss-plugins" "$APP_RESOURCES/harness/kss-plugins"

# ---- Agent skills 进 Resources（bundle-mode 只读发现面）----
for skills_root in .claude/skills .agents/skills; do
  copy_resource_item "$skills_root" "$APP_RESOURCES/$(dirname "$skills_root")"
done
# 签名前硬清：任何事后写入（pyc / 误拷 storage）都会让 sealed resource 失效 → Gatekeeper 拒开
find "$APP_RESOURCES" \( -name '.git' -o -name '__pycache__' -o -name '.pytest_cache' -o -name '.ruff_cache' -o -name '*.egg-info' -o -name '.cache' -o -name 'cache' -o -name 'caches' -o -name '.omx' -o -name '.codex' -o -name 'state' -o -name '.state' -o -name 'logs' \) \
  -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "$APP_RESOURCES" \( -name '*.py[cod]' -o -name '.DS_Store' -o -name '*.db' \) \
  -type f -delete 2>/dev/null || true
# 禁止把可变状态打进包（ledger / mi_signals 等）
rm -rf "$APP_RESOURCES/storage" 2>/dev/null || true
for skills_root in "$APP_RESOURCES/.claude/skills" "$APP_RESOURCES/.agents/skills"; do
  [ -d "$skills_root" ] && chmod -R a-w "$skills_root"
done
# 不 chmod a-w：只读会让下次 rm -rf dist/*.app 失败，且 Python 写 pyc 应靠 env 禁写而非锁目录

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
# 先签 Node helper executable 和内嵌资源 bundle（若有），再签顶层 .app。
if find "$APP_RESOURCES/pi-ai-helper/node_modules" -type f -name '*.node' -print -quit | grep -q .; then
  echo "ERROR: pi-ai helper contains unsupported native .node modules." >&2
  exit 1
fi
if [ ! -x "$APP_RESOURCES/harness-runtime/bin/node" ]; then
  echo "ERROR: Harness Node runtime missing from bundle." >&2
  exit 1
fi
if [ ! -f "$APP_RESOURCES/harness/kss-profile/node_modules/@deepseek-ai/dsh/lib/bin.js" ]; then
  echo "ERROR: bundled Harness profile is missing the dsh entry used at launch." >&2
  exit 1
fi
# Prefer explicit Apple HTTP TSA — bare --timestamp sometimes fails with
# "The timestamp service is not available" when the default endpoint flakes.
CODESIGN_TIMESTAMP="${KSS_CODESIGN_TIMESTAMP:-http://timestamp.apple.com/ts01}"
# Nested Harness addons (koffi / node-pty / sharp + libvips) are Mach-O and
# must be signed before the parent bundle is sealed.
while IFS= read -r native; do
  [ -n "$native" ] || continue
  echo "签名 Harness native: $native"
  codesign --force --options runtime --timestamp="$CODESIGN_TIMESTAMP" \
    --sign "$SIGN_IDENTITY" "$native"
  codesign --verify --strict --verbose=2 "$native"
done < <(
  find "$APP_RESOURCES/harness" -type f -name '*.dylib' | sort
  find "$APP_RESOURCES/harness" -type f -name '*.node' | sort
)
codesign --force --options runtime --timestamp="$CODESIGN_TIMESTAMP" \
  --entitlements "$NODE_ENTITLEMENTS" \
  --sign "$SIGN_IDENTITY" "$APP_RESOURCES/pi-ai-runtime/bin/node"
codesign --verify --strict --verbose=2 "$APP_RESOURCES/pi-ai-runtime/bin/node"
if ! codesign -d --entitlements :- "$APP_RESOURCES/pi-ai-runtime/bin/node" 2>&1 \
  | grep -q 'com.apple.security.cs.allow-jit'; then
  echo "ERROR: signed Node helper is missing allow-jit entitlement." >&2
  exit 1
fi
# The Harness kernel runs dsh with this nested Node binary. Independent
# availability from Python, but live writes still require a Node grant first.
codesign --force --options runtime --timestamp="$CODESIGN_TIMESTAMP" \
  --entitlements "$NODE_ENTITLEMENTS" \
  --sign "$SIGN_IDENTITY" "$APP_RESOURCES/harness-runtime/bin/node"
codesign --verify --strict --verbose=2 "$APP_RESOURCES/harness-runtime/bin/node"
if ! codesign -d --entitlements :- "$APP_RESOURCES/harness-runtime/bin/node" 2>&1 \
  | grep -q 'com.apple.security.cs.allow-jit'; then
  echo "ERROR: signed Harness Node is missing allow-jit entitlement." >&2
  exit 1
fi
# The scheduler helper is a nested executable.  It owns the ephemeral
# Keychain credential broker used by launchd jobs, so it must be independently
# signed before sealing the parent application bundle.
codesign --force --options runtime --timestamp="$CODESIGN_TIMESTAMP" \
  --entitlements "$ENTITLEMENTS" \
  --sign "$SIGN_IDENTITY" "$APP_HELPERS/KSSResearchSchedulerHelper"
codesign --verify --strict --verbose=2 "$APP_HELPERS/KSSResearchSchedulerHelper"
# SwiftPM 资源包是平铺目录（无 Info.plist）→ codesign 拒签；补最小 Info.plist 使其成合法 bundle。
if [ -d "$APP_RESOURCE_BUNDLE" ]; then
  # SwiftPM 资源包布局二选一，决定是否补 Info.plist：
  #  - 旧(flat / --build-system native)：平铺目录无 Info.plist → codesign 拒签，
  #    补根级最小 Info.plist 使其成合法 bundle。
  #  - 新(swiftpm 默认 build system)：已是 Contents/Info.plist 标准 bundle → 直接签；
  #    若再往根目录塞 Info.plist 反而触发「unsealed contents present in the bundle root」。
  if [ ! -f "$APP_RESOURCE_BUNDLE/Contents/Info.plist" ]; then
    cat >"$APP_RESOURCE_BUNDLE/Info.plist" <<RESPLIST
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
  codesign --force --options runtime --timestamp="$CODESIGN_TIMESTAMP" \
    --sign "$SIGN_IDENTITY" "$APP_RESOURCE_BUNDLE"
fi
codesign --force --options runtime --timestamp="$CODESIGN_TIMESTAMP" \
  --entitlements "$ENTITLEMENTS" \
  --sign "$SIGN_IDENTITY" "$APP_BUNDLE"

echo "验证签名："
codesign --verify --strict --verbose=2 "$APP_BUNDLE"
codesign -dv --verbose=4 "$APP_BUNDLE" 2>&1 | grep -E 'Authority|Identifier|Runtime' || true

# ---- 公证（KTD7，U10）：默认开启，交付对象首次打开不再见「无法验证」弹窗。
# ditto 打 zip 只是提交容器（notarytool 不吃裸 .app 目录）；staple 落回 .app 本体，
# zip 提交完即弃——不是最终交付产物。
if [ "$SKIP_NOTARIZE" = "1" ]; then
  echo ""
  echo "跳过公证（KSS_SKIP_NOTARIZE=1）。"
else
  NOTARY_ZIP="$DIST_DIR/${APP_NAME}-notarize.zip"
  echo ""
  echo "打包提交容器：$NOTARY_ZIP"
  rm -f "$NOTARY_ZIP"
  ditto -c -k --keepParent "$APP_BUNDLE" "$NOTARY_ZIP"

  # 注意：中文全角标点紧贴 $VAR 在 set -u 下可能被解析成「未绑定变量名」——一律用 ${VAR}
  echo "提交公证 (profile=${NOTARY_PROFILE}, --wait, usually a few minutes)..."
  if ! NOTARY_OUTPUT="$(xcrun notarytool submit "$NOTARY_ZIP" --keychain-profile "${NOTARY_PROFILE}" --wait 2>&1)"; then
    echo "$NOTARY_OUTPUT" >&2
    echo "ERROR: notarize submit failed." >&2
    echo "  Check Keychain profile: xcrun notarytool store-credentials ${NOTARY_PROFILE} --apple-id <id> --team-id <TEAMID> --password <app-specific-password>" >&2
    echo "  Or skip: KSS_SKIP_NOTARIZE=1" >&2
    exit 1
  fi
  echo "$NOTARY_OUTPUT"
  if ! echo "$NOTARY_OUTPUT" | grep -q "status: Accepted"; then
    SUBMISSION_ID="$(echo "$NOTARY_OUTPUT" | grep -o 'id: [a-f0-9-]*' | head -1 | cut -d' ' -f2)"
    echo "ERROR: notarize not Accepted." >&2
    if [ -n "$SUBMISSION_ID" ]; then
      echo "  Log: xcrun notarytool log ${SUBMISSION_ID} --keychain-profile ${NOTARY_PROFILE}" >&2
    fi
    exit 1
  fi

  echo "装订公证票据..."
  xcrun stapler staple "$APP_BUNDLE"
  rm -f "$NOTARY_ZIP"
  echo "公证完成，Gatekeeper 首开不再需要手动信任。"
fi

echo ""
echo "完成：$APP_BUNDLE"
echo "首启会 uv bootstrap Python 运行时到 ~/Library/Application Support/KSS/venv（需联网 + uv）。"
