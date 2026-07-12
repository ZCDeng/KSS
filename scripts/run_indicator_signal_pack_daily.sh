#!/bin/bash
# 指标 Signal Pack 日终批跑 - 17:16（紧随 mi_signal_pack 17:15 之后，早于 formal_daily_review 17:20）
#
# 手动：
#   bash scripts/run_indicator_signal_pack_daily.sh
#   bash scripts/run_indicator_signal_pack_daily.sh --asof 2026-07-10

set -e
set -o pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
: "${KSS_STATE_ROOT:=$PROJECT_ROOT}"
LOG_DIR="$PROJECT_ROOT/storage/logs/cron"

if [ -n "${KSS_PYTHON:-}" ]; then
    PYTHON="$KSS_PYTHON"
elif [ -x "$HOME/Library/Application Support/KSS/venv/bin/python3" ]; then
    PYTHON="$HOME/Library/Application Support/KSS/venv/bin/python3"
elif [ -x "$PROJECT_ROOT/.venv-desktop/bin/python" ]; then
    PYTHON="$PROJECT_ROOT/.venv-desktop/bin/python"
else
    echo "no usable python interpreter found (checked KSS_PYTHON, state-root venv, .venv-desktop)" >&2
    exit 1
fi

echo "===== $(date '+%Y-%m-%d %H:%M:%S') indicator_signal_pack 开始 ====="
mkdir -p "$LOG_DIR"

cd "$PROJECT_ROOT"
TUSHARE_TOKEN=$(grep -E '^TUSHARE_TOKEN=' "$PROJECT_ROOT/.env" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"')
export TUSHARE_TOKEN

"$PYTHON" "$PROJECT_ROOT/scripts/kss_app_bridge.py" run indicator-signal-pack "$@" 2>&1
echo "===== $(date '+%Y-%m-%d %H:%M:%S') indicator_signal_pack 结束 ====="
