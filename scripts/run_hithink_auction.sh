#!/bin/bash
# 集合竞价落盘 — 交易日 09:20 live / 09:26 final（plan 2026-09-12-001 / U6）.
#
# 凭据纪律：不 grep .env，不 echo Key。经 kss_load_credential 装入
# HITHINK_FINANCE_API_KEY（Keychain 优先）。

set -e
set -o pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
: "${KSS_STATE_ROOT:=$PROJECT_ROOT}"
export KSS_STATE_ROOT

if [ -n "${KSS_PYTHON:-}" ]; then
    PYTHON="$KSS_PYTHON"
elif [ -x "$HOME/Library/Application Support/KSS/venv/bin/python3" ]; then
    PYTHON="$HOME/Library/Application Support/KSS/venv/bin/python3"
elif [ -x "$PROJECT_ROOT/.venv-desktop/bin/python" ]; then
    PYTHON="$PROJECT_ROOT/.venv-desktop/bin/python"
elif [ -x "$PROJECT_ROOT/.venv/bin/python" ]; then
    PYTHON="$PROJECT_ROOT/.venv/bin/python"
else
    echo "no usable python interpreter found" >&2
    exit 1
fi

echo "===== $(date '+%Y-%m-%d %H:%M:%S') hithink auction $* 开始 ====="

# shellcheck source=scripts/lib_cron_credentials.sh
source "$PROJECT_ROOT/scripts/lib_cron_credentials.sh"
if kss_load_credential HITHINK_FINANCE_API_KEY "$PROJECT_ROOT/.env"; then
    echo "[wrapper] hithink creds: keychain/env ok"
else
    echo "[wrapper] hithink creds: missing — auction 将 not_configured" >&2
fi

cd "$PROJECT_ROOT"
exec "$PYTHON" scripts/fetch_hithink_auction.py "$@"
