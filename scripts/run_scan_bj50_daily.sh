#!/bin/bash
# 北证 50 全量扫描 + Telegram Top 5 推送 - 每日 17:45 cron wrapper.
#
# 包装目的:
#   1. cron 不读 zshrc -> 显式从 KSS 项目 .env 加载 TELEGRAM_* / TUSHARE_TOKEN
#   2. 用 Homebrew Python 绝对路径, 避免 cron PATH 与 shell 不一致
#   3. --force-refresh 确保拉到当天的 daily / index_daily / daily_basic
#   4. --push-telegram 推送 Top 5 + 关键变动 (排名↑↓ / 户数警告 / 新雷)
#
# 手动测试 (不发 telegram):
#   bash scripts/run_scan_bj50_daily.sh
# 手动测试 (发 telegram):
#   bash scripts/run_scan_bj50_daily.sh --push
#
# cron 部署 (每个交易日 17:45, 北证收盘 15:00 + tushare 数据延迟 buffer):
#   45 17 * * 1-5 /Users/zcdeng/projects/KSS/scripts/run_scan_bj50_daily.sh --push >> /Users/zcdeng/projects/KSS/storage/logs/cron/scan_bj50_daily.log 2>&1

set -e
set -o pipefail

PROJECT_ROOT="/Users/zcdeng/projects/KSS"
PYTHON="/opt/homebrew/opt/python@3.11/bin/python3.11"
KSS_ENV="$PROJECT_ROOT/.env"

# 解析参数: --push 转为 python 端 --push-telegram
PUSH_FLAG=""
for arg in "$@"; do
  if [ "$arg" = "--push" ] || [ "$arg" = "--push-telegram" ]; then
    PUSH_FLAG="--push-telegram"
  fi
done

# 时间戳便于 log 追踪
echo "===== $(date '+%Y-%m-%d %H:%M:%S') scan_bj50_daily 开始 (push=${PUSH_FLAG:-no}) ====="

# 加载 KSS .env 里的 Telegram + Tushare 凭据 (不 source 整个 .env, 防特殊字符行炸).
if [ -f "$KSS_ENV" ]; then
  TELEGRAM_BOT_TOKEN=$( (grep -E '^TELEGRAM_BOT_TOKEN=' "$KSS_ENV" || true) | head -1 | cut -d= -f2-)
  TELEGRAM_CHAT_ID=$( (grep -E '^TELEGRAM_CHAT_ID=' "$KSS_ENV" || true) | head -1 | cut -d= -f2-)
  TELEGRAM_API_URL=$( (grep -E '^TELEGRAM_API_URL=' "$KSS_ENV" || true) | head -1 | cut -d= -f2-)
  TUSHARE_TOKEN=$( (grep -E '^TUSHARE_TOKEN=' "$KSS_ENV" || true) | head -1 | cut -d= -f2-)
  # 去掉两端引号
  TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN%\"}"; TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN#\"}"
  TELEGRAM_CHAT_ID="${TELEGRAM_CHAT_ID%\"}"; TELEGRAM_CHAT_ID="${TELEGRAM_CHAT_ID#\"}"
  TELEGRAM_API_URL="${TELEGRAM_API_URL%\"}"; TELEGRAM_API_URL="${TELEGRAM_API_URL#\"}"
  TUSHARE_TOKEN="${TUSHARE_TOKEN%\"}"; TUSHARE_TOKEN="${TUSHARE_TOKEN#\"}"
  export TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID TELEGRAM_API_URL TUSHARE_TOKEN
  echo "[wrapper] loaded TELEGRAM_BOT_TOKEN length=${#TELEGRAM_BOT_TOKEN} / TUSHARE_TOKEN length=${#TUSHARE_TOKEN}"
else
  echo "[wrapper] WARNING: $KSS_ENV 不存在, telegram 推送将降级到 console"
fi

# 准备 log 目录
mkdir -p "$PROJECT_ROOT/storage/logs/cron"

cd "$PROJECT_ROOT"
exec "$PYTHON" scripts/scan_bj50.py --force-refresh --threads 4 $PUSH_FLAG
