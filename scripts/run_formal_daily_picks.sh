#!/bin/bash
# 正式每日选股 (formal-daily-picks) - 17:00 收盘后 cron wrapper
#
# 行为：通过 bridge subprocess 调 formal-daily-picks 任务（依赖完整 KSS Python 环境）
# 失败 → 退出非零让 cron 系统监控接管，不空跑。
#
# 手动测试：
#   bash scripts/run_formal_daily_picks.sh
#   bash scripts/run_formal_daily_picks.sh --date 2026-07-08
#
# cron 部署（每个交易日 17:00 收盘后）：
#   0 17 * * 1-5 /Users/zcdeng/projects/KSS/scripts/run_formal_daily_picks.sh >> /Users/zcdeng/projects/KSS/storage/logs/cron/formal_daily_picks.log 2>&1

set -e
set -o pipefail

PROJECT_ROOT="/Users/zcdeng/projects/KSS"
export KSS_STATE_ROOT="$PROJECT_ROOT"
LOG_DIR="$PROJECT_ROOT/storage/logs/cron"

echo "===== $(date '+%Y-%m-%d %H:%M:%S') formal_daily_picks 开始 ====="
mkdir -p "$LOG_DIR"

cd "$PROJECT_ROOT"
# TUSHARE_TOKEN 从 .env 加载（KSS_STATE_ROOT=/Users/zcdeng/projects/KSS → STATE_ROOT=PROJECT_ROOT）
TUSHARE_TOKEN=$(grep -E '^TUSHARE_TOKEN=' "$PROJECT_ROOT/.env" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"')
KSS_STATE_ROOT="$PROJECT_ROOT" TUSHARE_TOKEN="$TUSHARE_TOKEN" \
  "$PROJECT_ROOT/venv/bin/python" "$PROJECT_ROOT/scripts/kss_app_bridge.py" run formal-daily-picks --force "$@" 2>&1

# 二次校验：当日 JSON 必须落在 paper_trade（与 bridge 内断言双保险）
TODAY=$(date '+%Y-%m-%d')
# 若用户传了 --date / date= 则跳过「今日」硬检（脚本参数由 bridge 解析；这里只检今日 cron）
if [[ "$*" != *date* ]]; then
  LOG_FILE="$PROJECT_ROOT/storage/paper_trade/${TODAY}.json"
  if [ ! -f "$LOG_FILE" ]; then
    echo "[formal_daily_picks] ALERT: 日志未落盘 $LOG_FILE" >&2
    exit 2
  fi
  echo "[formal_daily_picks] ok: $LOG_FILE ($(wc -c < "$LOG_FILE") bytes)"
fi
