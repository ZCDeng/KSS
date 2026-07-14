#!/bin/bash
# MI Signal Pack 日终批跑 - 17:15（须早于 formal_daily_review 17:20）
#
# 手动：
#   bash scripts/run_mi_signal_pack_daily.sh
#   bash scripts/run_mi_signal_pack_daily.sh --symbols 688017.SH,688322.SH

set -e
set -o pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
: "${KSS_STATE_ROOT:=$PROJECT_ROOT}"
LOG_DIR="$KSS_STATE_ROOT/storage/logs/cron"

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

echo "===== $(date '+%Y-%m-%d %H:%M:%S') mi_signal_pack 开始 ====="
mkdir -p "$LOG_DIR"

cd "$PROJECT_ROOT"
source "$PROJECT_ROOT/scripts/lib_cron_chain.sh"

# 事件驱动链 gate（plan 2026-07-14-001 / KTD2）：显式传参（回填/定向跑）跳过链语义。
CHAIN_RUN=1
if [ "$#" -gt 0 ]; then
  CHAIN_RUN=0
fi
if [ "$CHAIN_RUN" -eq 1 ]; then
  kss_gate_or_exit mi_signal
fi

TUSHARE_TOKEN=$(grep -E '^TUSHARE_TOKEN=' "$PROJECT_ROOT/.env" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"')
export TUSHARE_TOKEN

# KTD3 超时护栏（信号包 10 分钟档）
kss_run_with_timeout 600 \
  "$PYTHON" "$PROJECT_ROOT/scripts/kss_app_bridge.py" run mi-signal-pack "$@" 2>&1

if [ "$CHAIN_RUN" -eq 1 ]; then
  kss_mark_done mi_signal
  kss_kick_next indicator_signal_pack
fi
echo "===== $(date '+%Y-%m-%d %H:%M:%S') mi_signal_pack 结束 ====="
