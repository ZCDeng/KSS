#!/usr/bin/env bash
# Build the pinned, self-contained DeepSeek Harness Node tree for release bundles.
# Same Node 22.19.0 arm64 pin as the pi-ai helper; profile + plugins are copied
# from the repo and npm ci'd into .build (node_modules stays out of git).
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROFILE_SOURCE="$ROOT_DIR/harness/kss-profile"
PLUGINS_SOURCE="$ROOT_DIR/harness/kss-plugins"
OUTPUT_ROOT="${KSS_HARNESS_OUTPUT_ROOT:-$ROOT_DIR/.build/harness-node}"
CACHE_ROOT="${KSS_HARNESS_CACHE_ROOT:-${KSS_PI_AI_CACHE_ROOT:-$ROOT_DIR/.build/pi-ai-cache}}"

NODE_VERSION="22.19.0"
NODE_ARCHIVE="node-v${NODE_VERSION}-darwin-arm64.tar.gz"
NODE_URL="https://nodejs.org/dist/v${NODE_VERSION}/${NODE_ARCHIVE}"
NODE_SHA256="c59006db713c770d6ec63ae16cb3edc11f49ee093b5c415d667bb4f436c6526d"
ARCHIVE_PATH="$CACHE_ROOT/$NODE_ARCHIVE"

if [ "$(uname -s)" != "Darwin" ] || [ "$(uname -m)" != "arm64" ]; then
  echo "ERROR: Harness Node release payload is pinned to macOS arm64." >&2
  exit 1
fi

if [ ! -f "$PROFILE_SOURCE/package.json" ] || [ ! -f "$PROFILE_SOURCE/package-lock.json" ]; then
  echo "ERROR: missing harness/kss-profile lockfile." >&2
  exit 1
fi
if [ ! -f "$PLUGINS_SOURCE/package.json" ]; then
  echo "ERROR: missing harness/kss-plugins package.json." >&2
  exit 1
fi

mkdir -p "$CACHE_ROOT" "$OUTPUT_ROOT"
if [ ! -f "$ARCHIVE_PATH" ]; then
  curl --fail --location --silent --show-error "$NODE_URL" -o "$ARCHIVE_PATH.tmp"
  mv "$ARCHIVE_PATH.tmp" "$ARCHIVE_PATH"
fi

ACTUAL_SHA="$(shasum -a 256 "$ARCHIVE_PATH" | awk '{print $1}')"
if [ "$ACTUAL_SHA" != "$NODE_SHA256" ]; then
  echo "ERROR: Node archive checksum mismatch." >&2
  exit 1
fi

STAGE="$(mktemp -d "$OUTPUT_ROOT.stage.XXXXXX")"
cleanup() {
  rm -rf "$STAGE"
}
trap cleanup EXIT

tar -xzf "$ARCHIVE_PATH" -C "$STAGE"
mkdir -p "$STAGE/payload/runtime/bin" "$STAGE/payload/harness"
cp "$STAGE/node-v${NODE_VERSION}-darwin-arm64/bin/node" "$STAGE/payload/runtime/bin/node"
cp "$STAGE/node-v${NODE_VERSION}-darwin-arm64/LICENSE" "$STAGE/payload/runtime/LICENSE"
chmod 755 "$STAGE/payload/runtime/bin/node"

copy_tree() {
  local src="$1"
  local dest="$2"
  mkdir -p "$dest"
  if command -v rsync >/dev/null 2>&1; then
    rsync -a --delete \
      --exclude 'node_modules/' --exclude '.git/' --exclude '.DS_Store' \
      --exclude '__pycache__/' --exclude '*.py[cod]' \
      "$src/" "$dest/"
  else
    rm -rf "$dest"
    mkdir -p "$dest"
    cp -R "$src/." "$dest/"
    rm -rf "$dest/node_modules"
  fi
}

copy_tree "$PLUGINS_SOURCE" "$STAGE/payload/harness/kss-plugins"
copy_tree "$PROFILE_SOURCE" "$STAGE/payload/harness/kss-profile"

NPM_CLI="$STAGE/node-v${NODE_VERSION}-darwin-arm64/lib/node_modules/npm/bin/npm-cli.js"
(
  cd "$STAGE/payload/harness/kss-profile"
  "$STAGE/payload/runtime/bin/node" "$NPM_CLI" \
    ci --omit=dev --ignore-scripts --no-audit --no-fund
)

if find "$STAGE/payload/harness" -type f -name '*.node' -print -quit | grep -q .; then
  echo "ERROR: Harness tree contains unsupported native .node modules." >&2
  exit 1
fi

DSH_BIN="$STAGE/payload/harness/kss-profile/node_modules/@deepseek-ai/dsh/lib/bin.js"
if [ ! -f "$DSH_BIN" ]; then
  echo "ERROR: npm ci did not produce the vendored dsh entry used to run the kernel." >&2
  exit 1
fi

rm -rf "$OUTPUT_ROOT/runtime" "$OUTPUT_ROOT/harness"
mv "$STAGE/payload/runtime" "$OUTPUT_ROOT/runtime"
mv "$STAGE/payload/harness" "$OUTPUT_ROOT/harness"

echo "$OUTPUT_ROOT"
