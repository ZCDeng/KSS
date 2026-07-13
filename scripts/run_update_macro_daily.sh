#!/bin/bash
# 宏观分母端数据增量更新 - 每日 8:35 cron wrapper（在 update_data_daily 8:30 之后）.
#
# 行为：
#   - 拉 Tushare shibor / yc_cb（中债国债收益率）/ cn_m / cn_cpi / cn_ppi
#   - 拉 AkShare bond_china_yield（信用利差，按日存档 CSV）
#   - 增量更新到 storage/macro/macro_daily.parquet + macro_monthly.parquet
#
# 手动测试：
#   bash scripts/run_update_macro_daily.sh
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

echo "===== $(date '+%Y-%m-%d %H:%M:%S') update_macro_daily 开始 ====="

# Tushare token：Keychain 优先，dev 回落项目 .env，再回落 $HOME/.tushare/token。
source "$PROJECT_ROOT/scripts/lib_cron_credentials.sh"
if kss_load_credential TUSHARE_TOKEN "$KSS_ENV"; then
  echo "[wrapper] loaded TUSHARE_TOKEN length=${#TUSHARE_TOKEN}"
elif [ -f "$HOME/.tushare/token" ]; then
  export TUSHARE_TOKEN=$(cat "$HOME/.tushare/token")
fi

cd "$PROJECT_ROOT"
exec "$PYTHON" scripts/update_macro_daily.py "$@"
