#!/bin/bash
# 指标 Signal Pack 日终批跑 - 17:16（紧随 mi_signal_pack 17:15 之后，早于 formal_daily_review 17:20）
#
# 手动：
#   bash scripts/run_indicator_signal_pack_daily.sh
#   bash scripts/run_indicator_signal_pack_daily.sh --asof 2026-07-10

set -e
set -o pipefail

PROJECT_ROOT="/Users/zcdeng/projects/KSS"
LOG_DIR="$PROJECT_ROOT/storage/logs/cron"

echo "===== $(date '+%Y-%m-%d %H:%M:%S') indicator_signal_pack 开始 ====="
mkdir -p "$LOG_DIR"

cd "$PROJECT_ROOT"
TUSHARE_TOKEN=$(grep -E '^TUSHARE_TOKEN=' "$PROJECT_ROOT/.env" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"')
export KSS_STATE_ROOT="$PROJECT_ROOT" TUSHARE_TOKEN="$TUSHARE_TOKEN"

"$PROJECT_ROOT/venv/bin/python" "$PROJECT_ROOT/scripts/kss_app_bridge.py" run indicator-signal-pack "$@" 2>&1
echo "===== $(date '+%Y-%m-%d %H:%M:%S') indicator_signal_pack 结束 ====="
