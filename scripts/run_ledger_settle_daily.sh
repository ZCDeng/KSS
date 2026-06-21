#!/bin/bash
# 预测账本结算 - 每个交易日收盘后 cron wrapper (U8 ①).
#
# 包装目的 (对齐 run_paper_trade_daily.sh 风格):
#   1. cron 不读 zshrc → 自己显式从 KSS 项目 .env 加载 TELEGRAM_* 变量
#   2. 用 .venv-desktop Python 绝对路径 (pyarrow/账本依赖装在该 venv)
#   3. 异常返回非零让 cron 系统监控接管
#
# 手动测试:
#   bash scripts/run_ledger_settle_daily.sh
#
# 动作: 回填历史 paper_trade JSON (幂等) + 结算所有 due-open 记录 (真实 T+1/T+2 open + F3 归因).

set -e
set -o pipefail

PROJECT_ROOT="/Users/zcdeng/projects/KSS"
PYTHON="$PROJECT_ROOT/.venv-desktop/bin/python"
KSS_ENV="$PROJECT_ROOT/.env"

echo "===== $(date '+%Y-%m-%d %H:%M:%S') ledger_settle_daily 开始 ====="

# 加载 KSS .env 里的 Telegram 凭据 (安全 grep, 不 source 整个 .env).
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
exec "$PYTHON" scripts/settle_ledger.py --backfill --settle --channel all "$@"
