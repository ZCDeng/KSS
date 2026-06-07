#!/bin/bash
# daily_review 预测校验周报 - 每周五 19:30 cron wrapper.
#
# 行为：
#   - 从 KSS 项目 .env 加载 Telegram 凭据
#   - 跑 validate_predictions.py 默认 lookback=7（校验本周复盘的次日预测）
#   - 退出码: 0 正常 / 1 无可验证预测（上游 cron 可能挂了）/ 2 Telegram 推送失败
#
# 手动测试：
#   bash scripts/run_prediction_validation_weekly.sh --dry-run
#
# launchd 部署（每周五 19:30，在 19:00 daily_review 刷新数据与复盘之后）：
#   deploy/launchd/com.zcdeng.kss.prediction_validation_weekly.plist

set -e
set -o pipefail

PROJECT_ROOT="/Users/zcdeng/projects/KSS"
PYTHON="/opt/homebrew/opt/python@3.11/bin/python3.11"
KSS_ENV="$PROJECT_ROOT/.env"

echo "===== $(date '+%Y-%m-%d %H:%M:%S') prediction_validation_weekly 开始 ====="

# 加载 KSS .env Telegram 凭据（安全 grep 法，避开不规则 .env 行 source 错误）
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
  echo "[wrapper] loaded TELEGRAM_BOT_TOKEN length=${#TELEGRAM_BOT_TOKEN} / TELEGRAM_CHAT_ID length=${#TELEGRAM_CHAT_ID} / TELEGRAM_API_URL=${TELEGRAM_API_URL:-<unset>} / TUSHARE_TOKEN length=${#TUSHARE_TOKEN}"
else
  echo "[wrapper] WARNING: $KSS_ENV 不存在，telegram 推送将降级到 console"
fi

cd "$PROJECT_ROOT"
set +e
"$PYTHON" scripts/validate_predictions.py --channel all "$@"
STATUS=$?
set -e

# 审计底稿周度入库：只动两个存档目录，失败不影响校验退出码
if git add storage/daily_review storage/etf_radar 2>/dev/null \
   && ! git diff --cached --quiet -- storage/daily_review storage/etf_radar; then
  git commit -m "chore(archive): 复盘/雷达存档周度入库 $(date '+%Y-%m-%d')" \
    -- storage/daily_review storage/etf_radar \
    && git push origin "$(git rev-parse --abbrev-ref HEAD)" \
    || echo "[wrapper] WARNING: 存档 commit/push 失败（不影响校验结果）"
else
  echo "[wrapper] 存档无新增，跳过入库"
fi

exit $STATUS
