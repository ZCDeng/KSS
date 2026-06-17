#!/bin/bash
# 688322 & 688017 每日复盘 + 次日预测 - 每个交易日 19:00 cron wrapper.
#
# 与 run_sector_review_daily.sh 对齐:
#   1. cron 不读 zshrc → 显式从 .env 加载 TELEGRAM_* + TUSHARE_TOKEN
#   2. Homebrew Python 绝对路径
#   3. 异常 return 非零让 cron 监控接管
#
# 手动测试:
#   bash scripts/run_daily_review_322_017.sh                    # 实际推送
#   bash scripts/run_daily_review_322_017.sh --dry-run          # 仅打印
#   bash scripts/run_daily_review_322_017.sh --date 20260522    # 指定日期
#
# cron 部署 (每个交易日 19:00):
#   0 19 * * 1-5 /Users/zcdeng/projects/KSS/scripts/run_daily_review_322_017.sh \
#     >> /Users/zcdeng/projects/KSS/storage/logs/cron/daily_review_322_017.log 2>&1

set -e
set -o pipefail

PROJECT_ROOT="/Users/zcdeng/projects/KSS"
PYTHON="/opt/homebrew/opt/python@3.11/bin/python3.11"
KSS_ENV="$PROJECT_ROOT/.env"

echo "===== $(date '+%Y-%m-%d %H:%M:%S') daily_review_322_017 开始 ====="

if [ -f "$KSS_ENV" ]; then
  TELEGRAM_BOT_TOKEN=$(grep -E '^TELEGRAM_BOT_TOKEN=' "$KSS_ENV" | head -1 | cut -d= -f2-)
  TELEGRAM_CHAT_ID=$(grep -E '^TELEGRAM_CHAT_ID=' "$KSS_ENV" | head -1 | cut -d= -f2-)
  TELEGRAM_API_URL=$(grep -E '^TELEGRAM_API_URL=' "$KSS_ENV" | head -1 | cut -d= -f2-)
  TUSHARE_TOKEN=$(grep -E '^TUSHARE_TOKEN=' "$KSS_ENV" | head -1 | cut -d= -f2-)
  TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN%\"}"; TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN#\"}"
  TELEGRAM_CHAT_ID="${TELEGRAM_CHAT_ID%\"}"; TELEGRAM_CHAT_ID="${TELEGRAM_CHAT_ID#\"}"
  TELEGRAM_API_URL="${TELEGRAM_API_URL%\"}"; TELEGRAM_API_URL="${TELEGRAM_API_URL#\"}"
  TUSHARE_TOKEN="${TUSHARE_TOKEN%\"}"; TUSHARE_TOKEN="${TUSHARE_TOKEN#\"}"
  export TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID TELEGRAM_API_URL TUSHARE_TOKEN
  echo "[wrapper] loaded TELEGRAM_BOT_TOKEN length=${#TELEGRAM_BOT_TOKEN} / TUSHARE_TOKEN length=${#TUSHARE_TOKEN}"
else
  echo "[wrapper] WARNING: $KSS_ENV 不存在, telegram 推送将降级到 console"
fi

cd "$PROJECT_ROOT"
exec "$PYTHON" scripts/daily_review_322_017.py --channel all "$@"
