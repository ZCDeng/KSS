#!/bin/bash
# 信号卡日终构建 — 挂在 hotspot_rotation_daily 链后 + 晚间兜底
#
# 手动：
#   bash scripts/run_signal_cards_daily.sh
#   bash scripts/run_signal_cards_daily.sh --date 20260717
#   bash scripts/run_signal_cards_daily.sh --backfill 20260522 20260728

set -e
set -o pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
: "${KSS_STATE_ROOT:=$PROJECT_ROOT}"
export KSS_STATE_ROOT
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

echo "===== $(date '+%Y-%m-%d %H:%M:%S') signal_cards_daily 开始 ====="
mkdir -p "$LOG_DIR"

cd "$PROJECT_ROOT"
source "$PROJECT_ROOT/scripts/lib_cron_chain.sh"

CHAIN_RUN=1
if [ "$#" -gt 0 ]; then
  CHAIN_RUN=0
fi
if [ "$CHAIN_RUN" -eq 1 ]; then
  kss_gate_or_exit signal_cards
  TARGET_DAY=$("$PYTHON" "$PROJECT_ROOT/scripts/check_pipeline_gate.py" \
    --task signal_cards --action target-day \
    --data-root "$KSS_STATE_ROOT" --state-root "$KSS_STATE_ROOT")
  if [ -z "${TARGET_DAY:-}" ]; then
    echo "[chain] signal_cards: 无法解析目标交易日" >&2
    exit 1
  fi
  echo "[chain] signal_cards: 构建目标日 $TARGET_DAY"
  kss_run_with_timeout 600 \
    "$PYTHON" "$PROJECT_ROOT/scripts/build_signal_cards.py" --date "$TARGET_DAY" 2>&1
  kss_mark_done signal_cards
else
  kss_run_with_timeout 600 \
    "$PYTHON" "$PROJECT_ROOT/scripts/build_signal_cards.py" "$@" 2>&1
fi
echo "===== $(date '+%Y-%m-%d %H:%M:%S') signal_cards_daily 结束 ====="
