#!/bin/bash
# 趋势页每日归档 - 每个交易日 18:10 收盘后 launchd wrapper（U3，going-forward）。
#
# 目的：每日把当天三类内容写入 storage/trends/YYYY-MM-DD.json，逐日累积喂趋势页日历。
# 排在 hsgt / etf_radar / paper 更新与 hotspot(17:50) 之后，等数据 buffer。
#
# 用 .venv-desktop（pandas + pyarrow 读 hsgt parquet；ETF 取自 market_strip.json，无需 tushare）。
# TUSHARE_TOKEN 仅为将来 fund_daily 历史回填预留，当前 archive 不直接调 tushare。
#
# 手动测试：
#   bash scripts/archive_trends_daily.sh                 # 落最新交易日
#   bash scripts/archive_trends_daily.sh 2026-06-18      # 指定日期

set -e
set -o pipefail

PROJECT_ROOT="/Users/zcdeng/projects/KSS"
PYTHON="$PROJECT_ROOT/.venv-desktop/bin/python"
KSS_ENV="$PROJECT_ROOT/.env"

echo "===== $(date '+%Y-%m-%d %H:%M:%S') trends_archive_daily 开始 ====="

if [ -f "$KSS_ENV" ]; then
  TUSHARE_TOKEN=$(grep -E '^TUSHARE_TOKEN=' "$KSS_ENV" | head -1 | cut -d= -f2-)
  TUSHARE_TOKEN="${TUSHARE_TOKEN%\"}"; TUSHARE_TOKEN="${TUSHARE_TOKEN#\"}"
  export TUSHARE_TOKEN
fi

cd "$PROJECT_ROOT"

# 默认 latest 交易日；手动指定日期经 "$@" 透传。
"$PYTHON" scripts/archive_trends_daily.py "$@"

echo "===== $(date '+%Y-%m-%d %H:%M:%S') trends_archive_daily 完成 ====="
