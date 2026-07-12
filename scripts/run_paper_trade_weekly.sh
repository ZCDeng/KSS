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
# 部署：kss/config/cron_jobs.yaml 清单条目 + scripts/sync_launchd.py（不再手动 crontab -e）。

set -e
set -o pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

if [ -n "${KSS_PYTHON:-}" ]; then
    PYTHON="$KSS_PYTHON"
elif [ -x "$HOME/Library/Application Support/KSS/venv/bin/python3" ]; then
    PYTHON="$HOME/Library/Application Support/KSS/venv/bin/python3"
elif [ -x "$PROJECT_ROOT/.venv-desktop/bin/python" ]; then
    PYTHON="$PROJECT_ROOT/.venv-desktop/bin/python"
else
    echo "no usable python interpreter found (checked KSS_PYTHON, state-root venv, .venv-desktop)" >&2
    exit 1
fi
KSS_ENV="$PROJECT_ROOT/.env"

echo "===== $(date '+%Y-%m-%d %H:%M:%S') paper_trade_weekly 开始 ====="

# 加载 KSS .env Telegram 凭据（安全 grep 法，避开不规则 .env 行 source 错误）
source "$PROJECT_ROOT/scripts/lib_cron_credentials.sh"
if kss_load_credential TELEGRAM_BOT_TOKEN "$KSS_ENV"; then
  kss_load_credential TELEGRAM_CHAT_ID "$KSS_ENV" || true
  kss_load_credential TELEGRAM_API_URL "$KSS_ENV" || true
  echo "[wrapper] loaded TELEGRAM_BOT_TOKEN length=${#TELEGRAM_BOT_TOKEN} / TELEGRAM_CHAT_ID length=${#TELEGRAM_CHAT_ID} / TELEGRAM_API_URL=${TELEGRAM_API_URL:-<unset>}"
else
  echo "[wrapper] WARNING: 未在 Keychain / $KSS_ENV 找到 telegram 凭据，推送将降级到 console"
fi

cd "$PROJECT_ROOT"
exec "$PYTHON" scripts/weekly_summary.py --channel all --alert-on-degradation "$@"
