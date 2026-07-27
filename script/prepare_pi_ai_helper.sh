#!/usr/bin/env bash
# Build the pinned, self-contained pi-ai helper payload used by release bundles.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HELPER_SOURCE="$ROOT_DIR/helpers/pi-ai"
OUTPUT_ROOT="${KSS_PI_AI_OUTPUT_ROOT:-$ROOT_DIR/.build/pi-ai-helper}"
CACHE_ROOT="${KSS_PI_AI_CACHE_ROOT:-$ROOT_DIR/.build/pi-ai-cache}"

NODE_VERSION="22.19.0"
NODE_ARCHIVE="node-v${NODE_VERSION}-darwin-arm64.tar.gz"
NODE_URL="https://nodejs.org/dist/v${NODE_VERSION}/${NODE_ARCHIVE}"
NODE_SHA256="c59006db713c770d6ec63ae16cb3edc11f49ee093b5c415d667bb4f436c6526d"
ARCHIVE_PATH="$CACHE_ROOT/$NODE_ARCHIVE"

if [ "$(uname -s)" != "Darwin" ] || [ "$(uname -m)" != "arm64" ]; then
  echo "ERROR: pi-ai release payload is pinned to macOS arm64." >&2
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
mkdir -p "$STAGE/payload/runtime/bin" "$STAGE/payload/helper"
cp "$STAGE/node-v${NODE_VERSION}-darwin-arm64/bin/node" "$STAGE/payload/runtime/bin/node"
cp "$STAGE/node-v${NODE_VERSION}-darwin-arm64/LICENSE" "$STAGE/payload/runtime/LICENSE"
chmod 755 "$STAGE/payload/runtime/bin/node"

cp \
  "$HELPER_SOURCE/package.json" \
  "$HELPER_SOURCE/package-lock.json" \
  "$HELPER_SOURCE/helper.mjs" \
  "$HELPER_SOURCE/THIRD_PARTY_NOTICES.md" \
  "$STAGE/payload/helper/"
(
  cd "$STAGE/payload/helper"
  "$STAGE/payload/runtime/bin/node" \
    "$STAGE/node-v${NODE_VERSION}-darwin-arm64/lib/node_modules/npm/bin/npm-cli.js" \
    ci --omit=dev --ignore-scripts --no-audit --no-fund
  "$STAGE/payload/runtime/bin/node" \
    "$STAGE/node-v${NODE_VERSION}-darwin-arm64/lib/node_modules/npm/bin/npm-cli.js" \
    sbom --omit=dev --sbom-format=spdx > THIRD_PARTY_SBOM.spdx.json
)

if find "$STAGE/payload/helper/node_modules" -type f -name '*.node' -print -quit | grep -q .; then
  echo "ERROR: pi-ai production closure contains native .node modules." >&2
  exit 1
fi

HELLO="$(
  echo '{"request_id":"prepare-smoke","command":"hello"}' |
    "$STAGE/payload/runtime/bin/node" "$STAGE/payload/helper/helper.mjs" --mock
)"
if ! echo "$HELLO" | grep -q '"pi_ai_version":"0.82.1"'; then
  echo "ERROR: pi-ai helper smoke check failed." >&2
  exit 1
fi

rm -rf "$OUTPUT_ROOT/runtime" "$OUTPUT_ROOT/helper"
mv "$STAGE/payload/runtime" "$OUTPUT_ROOT/runtime"
mv "$STAGE/payload/helper" "$OUTPUT_ROOT/helper"

echo "$OUTPUT_ROOT"
