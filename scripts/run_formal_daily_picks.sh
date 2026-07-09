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
LOG_DIR="$PROJECT_ROOT/storage/logs/cron"

echo "===== $(date '+%Y-%m-%d %H:%M:%S') formal_daily_picks 开始 ====="
mkdir -p "$LOG_DIR"

cd "$PROJECT_ROOT"
# TUSHARE_TOKEN 从 .env 加载（KSS_STATE_ROOT=/Users/zcdeng/projects/KSS → STATE_ROOT=PROJECT_ROOT）
TUSHARE_TOKEN=$(grep -E '^TUSHARE_TOKEN=' "$PROJECT_ROOT/.env" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"')
KSS_STATE_ROOT="$PROJECT_ROOT" TUSHARE_TOKEN="$TUSHARE_TOKEN" \
  "$PROJECT_ROOT/venv/bin/python" "$PROJECT_ROOT/scripts/kss_app_bridge.py" run formal-daily-picks "$@" 2>&1
