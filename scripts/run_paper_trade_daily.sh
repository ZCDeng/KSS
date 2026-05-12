#!/bin/bash
# log_mv 反向纸交易 - 每日 9:00 cron wrapper.
#
# 包装目的：
#   1. cron 不读 zshrc → 自己显式从 KSS 项目 .env 加载 TELEGRAM_* 变量
#   2. 用 Homebrew Python 绝对路径，避免 cron PATH 与 shell 不一致
#   3. 异常不要让 cron 邮件爆炸——失败时返回非零让 cron 系统监控接管
#
# 手动测试：
#   bash scripts/run_paper_trade_daily.sh
#
# cron 部署（每个交易日 9:00）：
#   0 9 * * 1-5 /Users/zcdeng/projects/KSS/scripts/run_paper_trade_daily.sh >> /tmp/kss_daily.log 2>&1

set -e
set -o pipefail

PROJECT_ROOT="/Users/zcdeng/projects/KSS"
PYTHON="/opt/homebrew/opt/python@3.11/bin/python3.11"
KSS_ENV="$PROJECT_ROOT/.env"

# 时间戳便于 log 追踪
echo "===== $(date '+%Y-%m-%d %H:%M:%S') paper_trade_daily 开始 ====="

# 加载 KSS .env 里的 Telegram 凭据.
# 不 source 整个 .env：.env 里可能混有含特殊字符的行（cookie/jwt 等），
# bash source 会因 `.foo=bar` 之类的行当作 sourcing 命令而失败.
# 改为安全 grep 只取需要的三个变量.
if [ -f "$KSS_ENV" ]; then
  TELEGRAM_BOT_TOKEN=$(grep -E '^TELEGRAM_BOT_TOKEN=' "$KSS_ENV" | head -1 | cut -d= -f2-)
  TELEGRAM_CHAT_ID=$(grep -E '^TELEGRAM_CHAT_ID=' "$KSS_ENV" | head -1 | cut -d= -f2-)
  TELEGRAM_API_URL=$(grep -E '^TELEGRAM_API_URL=' "$KSS_ENV" | head -1 | cut -d= -f2-)
  # 去掉可能的两端引号（.env 里有些用 "value" 包裹）
  TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN%\"}"; TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN#\"}"
  TELEGRAM_CHAT_ID="${TELEGRAM_CHAT_ID%\"}"; TELEGRAM_CHAT_ID="${TELEGRAM_CHAT_ID#\"}"
  TELEGRAM_API_URL="${TELEGRAM_API_URL%\"}"; TELEGRAM_API_URL="${TELEGRAM_API_URL#\"}"
  export TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID TELEGRAM_API_URL
  echo "[wrapper] loaded TELEGRAM_BOT_TOKEN length=${#TELEGRAM_BOT_TOKEN} / TELEGRAM_CHAT_ID length=${#TELEGRAM_CHAT_ID} / TELEGRAM_API_URL=${TELEGRAM_API_URL:-<unset>}"
else
  echo "[wrapper] WARNING: $KSS_ENV 不存在，telegram 推送将降级到 console"
fi

cd "$PROJECT_ROOT"
exec "$PYTHON" scripts/paper_trade_log_mv.py --channel all "$@"
