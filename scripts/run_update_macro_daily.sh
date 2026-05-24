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
# cron 部署（每个交易日 8:35）：
#   35 8 * * 1-5 /Users/zcdeng/projects/KSS/scripts/run_update_macro_daily.sh \
#     >> /Users/zcdeng/projects/KSS/storage/logs/cron/update_macro_daily.log 2>&1

set -e
set -o pipefail

PROJECT_ROOT="/Users/zcdeng/projects/KSS"
PYTHON="/opt/homebrew/opt/python@3.11/bin/python3.11"
HERMES_ENV="/Users/zcdeng/projects/agentos-stack/hermes_agent/.env"

echo "===== $(date '+%Y-%m-%d %H:%M:%S') update_macro_daily 开始 ====="

# Tushare token（与 update_data_daily 同源加载逻辑）
if [ -f "$HERMES_ENV" ]; then
  TUSHARE_TOKEN=$(grep -E '^TUSHARE_TOKEN=' "$HERMES_ENV" | head -1 | cut -d= -f2-)
  TUSHARE_TOKEN="${TUSHARE_TOKEN%\"}"; TUSHARE_TOKEN="${TUSHARE_TOKEN#\"}"
  if [ -n "$TUSHARE_TOKEN" ]; then
    export TUSHARE_TOKEN
    echo "[wrapper] loaded TUSHARE_TOKEN length=${#TUSHARE_TOKEN}"
  fi
fi
if [ -z "$TUSHARE_TOKEN" ] && [ -f "$HOME/.tushare/token" ]; then
  export TUSHARE_TOKEN=$(cat "$HOME/.tushare/token")
fi

cd "$PROJECT_ROOT"
exec "$PYTHON" scripts/update_macro_daily.py "$@"
