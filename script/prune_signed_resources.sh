#!/usr/bin/env bash
# Strip cache/log/state dirs from a signed Resources tree so Gatekeeper
# sealed resources stay clean. Never descend into node_modules: packages such
# as @opentelemetry/otlp-transformer keep source under a directory named logs/.
set -euo pipefail
ROOT="${1:?Resources root required}"
while IFS= read -r dir; do
  [ -n "$dir" ] || continue
  rm -rf "$dir"
done < <(
  find "$ROOT" \( -name node_modules -o -name '.git' \) -prune -o \
    \( -name '__pycache__' -o -name '.pytest_cache' -o -name '.ruff_cache' -o -name '*.egg-info' \
       -o -name '.cache' -o -name 'cache' -o -name 'caches' \
       -o -name '.omx' -o -name '.codex' -o -name 'state' -o -name '.state' -o -name 'logs' \) \
    -type d -print
)
