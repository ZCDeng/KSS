#!/bin/bash
# 开盘前 divergence 快讯 - 每个交易日 9:00 cron wrapper.
#
# 行为：
#   - 从 KSS 项目 .env 加载 Telegram + Tushare 凭据
#   - 跑 morning_divergence_alert.py：有见顶预警才推送，无预警静默
#   - 退出码: 0 正常(含无预警) / 1 雷达构建失败 / 2 Telegram 推送失败
#
# 手动测试：
#   bash scripts/run_morning_divergence_alert.sh --dry-run
#
# launchd 部署（每个交易日 9:00，在 8:30 数据更新后、9:30 开盘前）：
#   deploy/launchd/com.zcdeng.kss.morning_divergence_alert.plist

set -e
set -o pipefail

PROJECT_ROOT="/Users/zcdeng/projects/KSS"
PYTHON="/opt/homebrew/opt/python@3.11/bin/python3.11"
KSS_ENV="$PROJECT_ROOT/.env"

echo "===== $(date '+%Y-%m-%d %H:%M:%S') morning_divergence_alert 开始 ====="

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
  echo "[wrapper] WARNING: $KSS_ENV 不存在，推送将降级到 console"
fi

cd "$PROJECT_ROOT"
exec "$PYTHON" scripts/morning_divergence_alert.py --channel all "$@"
