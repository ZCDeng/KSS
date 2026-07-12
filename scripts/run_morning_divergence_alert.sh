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

echo "===== $(date '+%Y-%m-%d %H:%M:%S') morning_divergence_alert 开始 ====="

source "$PROJECT_ROOT/scripts/lib_cron_credentials.sh"
if kss_load_credential TELEGRAM_BOT_TOKEN "$KSS_ENV"; then
  kss_load_credential TELEGRAM_CHAT_ID "$KSS_ENV" || true
  kss_load_credential TELEGRAM_API_URL "$KSS_ENV" || true
else
  echo "[wrapper] WARNING: 未在 Keychain / $KSS_ENV 找到 telegram 凭据，推送将降级到 console"
fi
kss_load_credential TUSHARE_TOKEN "$KSS_ENV" || true
echo "[wrapper] loaded TELEGRAM_BOT_TOKEN length=${#TELEGRAM_BOT_TOKEN} / TUSHARE_TOKEN length=${#TUSHARE_TOKEN}"

cd "$PROJECT_ROOT"
exec "$PYTHON" scripts/morning_divergence_alert.py --channel all "$@"
