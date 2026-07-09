#!/bin/bash
# 收盘后组合信号扫描 - 每个交易日 17:40 cron wrapper
# (在 sector_review 17:30 之后跑, 给 sector_review 5-10 分钟完成的 buffer)
#
# 包装目的 (与 run_sector_review_daily.sh 对齐):
#   1. cron 不读 zshrc → 显式从 KSS .env 加载 TELEGRAM_* + TUSHARE_TOKEN
#   2. 用 Homebrew Python 绝对路径
#   3. 异常用非零退出码让 launchd 监控接管
#
# 手动测试:
#   bash scripts/run_combo_scan_daily.sh                # 实际推送
#   bash scripts/run_combo_scan_daily.sh --dry-run      # 跳过推送
#   bash scripts/run_combo_scan_daily.sh --top-entry 8  # 入场候选改 8 只
#
# launchd 部署: deploy/launchd/com.zcdeng.kss.combo_scan_daily.plist

set -e
set -o pipefail

PROJECT_ROOT="/Users/zcdeng/projects/KSS"
export KSS_STATE_ROOT="$PROJECT_ROOT"
PYTHON="/opt/homebrew/opt/python@3.11/bin/python3.11"
KSS_ENV="$PROJECT_ROOT/.env"

echo "===== $(date '+%Y-%m-%d %H:%M:%S') combo_scan_daily 开始 ====="

# 加载 KSS .env (Telegram + Tushare); 用 grep + cut 避免 source 整文件的引号灾难.
# `|| true` 防 set -e + pipefail: 若 .env 缺某一行（如 TUSHARE_TOKEN 注释掉），
# grep 退出 1 会让整脚本死掉；scan_combo_signals 走纯本地 CSV，Tushare 缺失不影响.
if [ -f "$KSS_ENV" ]; then
  TELEGRAM_BOT_TOKEN=$( (grep -E '^TELEGRAM_BOT_TOKEN=' "$KSS_ENV" || true) | head -1 | cut -d= -f2-)
  TELEGRAM_CHAT_ID=$( (grep -E '^TELEGRAM_CHAT_ID=' "$KSS_ENV" || true) | head -1 | cut -d= -f2-)
  TELEGRAM_API_URL=$( (grep -E '^TELEGRAM_API_URL=' "$KSS_ENV" || true) | head -1 | cut -d= -f2-)
  TUSHARE_TOKEN=$( (grep -E '^TUSHARE_TOKEN=' "$KSS_ENV" || true) | head -1 | cut -d= -f2-)
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

# 默认: 入场 Top 5, board=all, --channel all (console + telegram)
# 可通过位置参数 / flags 覆盖: bash run_combo_scan_daily.sh --top-entry 8 --dry-run
exec "$PYTHON" scan_combo_signals.py --channel all --top-entry 5 --board all "$@"
