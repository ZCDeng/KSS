#!/bin/bash
# 因子健康度刷新 - 每个交易日结算之后 cron wrapper (U8 ②).
#
# 包装目的 (对齐 run_ledger_settle_daily.sh 风格):
#   1. cron 不读 zshrc → 自己显式从 KSS 项目 .env 加载 TELEGRAM_* 变量
#   2. 用 .venv-desktop Python 绝对路径
#   3. 异常返回非零让 cron 系统监控接管
#
# 手动测试:
#   bash scripts/run_factor_health_daily.sh
#
# 动作: 对每条 pipeline 算 rank_ic 双源 (近窗后验 / 较早窗先验) → #8 仲裁 → 状态落 factor_health.db.
# 依赖: 应在账本结算 (run_ledger_settle_daily.sh) 之后跑, 让命中前向收益基于已落地数据.

set -e
set -o pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
: "${KSS_STATE_ROOT:=$PROJECT_ROOT}"

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

echo "===== $(date '+%Y-%m-%d %H:%M:%S') factor_health_daily 开始 ====="

# 加载 KSS .env 里的 Telegram 凭据 (安全 grep, 不 source 整个 .env).
source "$PROJECT_ROOT/scripts/lib_cron_credentials.sh"
if kss_load_credential TELEGRAM_BOT_TOKEN "$KSS_ENV"; then
  kss_load_credential TELEGRAM_CHAT_ID "$KSS_ENV" || true
  kss_load_credential TELEGRAM_API_URL "$KSS_ENV" || true
  echo "[wrapper] loaded TELEGRAM_BOT_TOKEN length=${#TELEGRAM_BOT_TOKEN} / TELEGRAM_CHAT_ID length=${#TELEGRAM_CHAT_ID} / TELEGRAM_API_URL=${TELEGRAM_API_URL:-<unset>}"
else
  echo "[wrapper] WARNING: 未在 Keychain / $KSS_ENV 找到 telegram 凭据，推送将降级到 console"
fi

cd "$PROJECT_ROOT"
exec "$PYTHON" scripts/refresh_factor_health.py --channel all "$@"
