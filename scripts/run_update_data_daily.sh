#!/bin/bash
# cs_data 增量更新 - 每日 8:30 cron wrapper（在 9:00 选股之前补数据）.
#
# 行为：
#   - 增量更新【整个股票池】cs_data（股票 daily+daily_basic / ETF fund_daily / 指数 index_daily，按 kind 自适应）
#   - 紧接着刷新指数行情入库 market_strip.json（仪表盘指数条）—— 折进同一日更，不另设独立 cron
#   - throttle=0.5s（免费版 ~120 次/分钟，0.5s 安全）
#
# 手动测试：
#   bash scripts/run_update_data_daily.sh
#
# cron 部署（每个交易日 8:30 开盘前 30 分钟）：
#   30 8 * * 1-5 /Users/zcdeng/projects/KSS/scripts/run_update_data_daily.sh >> /Users/zcdeng/projects/KSS/storage/logs/cron/update_data_daily.log 2>&1

set -e
set -o pipefail

PROJECT_ROOT="/Users/zcdeng/projects/KSS"
PYTHON="/opt/homebrew/opt/python@3.11/bin/python3.11"
HERMES_ENV="/Users/zcdeng/projects/agentos-stack/hermes_agent/.env"

echo "===== $(date '+%Y-%m-%d %H:%M:%S') update_cs_data 开始 ====="

# Tushare token 从 ~/.zshrc / Hermes .env 加载
# 注意：grep -E 限定单行，避开 .env 里 cookie 等不规则行
if [ -f "$HERMES_ENV" ]; then
  TUSHARE_TOKEN=$(grep -E '^TUSHARE_TOKEN=' "$HERMES_ENV" | head -1 | cut -d= -f2-)
  TUSHARE_TOKEN="${TUSHARE_TOKEN%\"}"; TUSHARE_TOKEN="${TUSHARE_TOKEN#\"}"
  if [ -n "$TUSHARE_TOKEN" ]; then
    export TUSHARE_TOKEN
    echo "[wrapper] loaded TUSHARE_TOKEN length=${#TUSHARE_TOKEN}"
  fi
fi
# fallback：若 .env 没 TUSHARE_TOKEN，尝试 KSS 项目里的 token 文件
if [ -z "$TUSHARE_TOKEN" ] && [ -f "$HOME/.tushare/token" ]; then
  export TUSHARE_TOKEN=$(cat "$HOME/.tushare/token")
fi

cd "$PROJECT_ROOT"
# 1) 全量增量更新整个股票池 cs_data（kind 自适应：stock→daily / fund→fund_daily / index→index_daily）
"$PYTHON" scripts/update_cs_data.py --throttle 0.5 "$@"

# 2) 指数行情入库（market_strip.json，仪表盘指数条）—— 折进日更，不另设 cron
echo "===== $(date '+%Y-%m-%d %H:%M:%S') refresh_market_strip 开始 ====="
"$PYTHON" scripts/refresh_market_strip.py
