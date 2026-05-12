#!/bin/bash
# log_mv 反向纸交易 - 每周五 17:00 周报 cron wrapper.
#
# 行为：
#   - 从 KSS 项目 .env 加载 Telegram 凭据
#   - 跑 weekly_summary.py 默认 lookback=7
#   - 启用 --alert-on-degradation：当 Sharpe < baseline 1.74 × 50% 时退出码 2
#     便于 cron 系统监控接管告警
#
# 手动测试：
#   bash scripts/run_paper_trade_weekly.sh
#
# cron 部署（每周五 17:00 收盘后）：
#   0 17 * * 5 /Users/zcdeng/projects/KSS/scripts/run_paper_trade_weekly.sh >> /tmp/kss_weekly.log 2>&1

set -e
set -o pipefail

PROJECT_ROOT="/Users/zcdeng/projects/KSS"
PYTHON="/opt/homebrew/opt/python@3.11/bin/python3.11"
KSS_ENV="$PROJECT_ROOT/.env"

echo "===== $(date '+%Y-%m-%d %H:%M:%S') paper_trade_weekly 开始 ====="

# 加载 KSS .env Telegram 凭据（安全 grep 法，避开不规则 .env 行 source 错误）
if [ -f "$KSS_ENV" ]; then
  TELEGRAM_BOT_TOKEN=$(grep -E '^TELEGRAM_BOT_TOKEN=' "$KSS_ENV" | head -1 | cut -d= -f2-)
  TELEGRAM_CHAT_ID=$(grep -E '^TELEGRAM_CHAT_ID=' "$KSS_ENV" | head -1 | cut -d= -f2-)
  TELEGRAM_API_URL=$(grep -E '^TELEGRAM_API_URL=' "$KSS_ENV" | head -1 | cut -d= -f2-)
  TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN%\"}"; TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN#\"}"
  TELEGRAM_CHAT_ID="${TELEGRAM_CHAT_ID%\"}"; TELEGRAM_CHAT_ID="${TELEGRAM_CHAT_ID#\"}"
  TELEGRAM_API_URL="${TELEGRAM_API_URL%\"}"; TELEGRAM_API_URL="${TELEGRAM_API_URL#\"}"
  export TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID TELEGRAM_API_URL
  echo "[wrapper] loaded TELEGRAM_BOT_TOKEN length=${#TELEGRAM_BOT_TOKEN} / TELEGRAM_CHAT_ID length=${#TELEGRAM_CHAT_ID} / TELEGRAM_API_URL=${TELEGRAM_API_URL:-<unset>}"
else
  echo "[wrapper] WARNING: $KSS_ENV 不存在，telegram 推送将降级到 console"
fi

cd "$PROJECT_ROOT"
exec "$PYTHON" scripts/weekly_summary.py --channel all --alert-on-degradation "$@"
