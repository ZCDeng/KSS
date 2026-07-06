#!/bin/bash
# cs_data 增量更新 - 日更与盘后双模式 wrapper
#
# 行为：
#   - 增量更新【整个股票池】cs_data（股票 daily+daily_basic / ETF fund_daily / 指数 index_daily，按 kind 自适应）
#   - 紧接着刷新指数行情入库 market_strip.json（仪表盘指数条）—— 折进同一日更，不另设独立 cron
#   - throttle=0.5s（免费版 ~120 次/分钟，0.5s 安全）
#
# 手动测试：
#   bash scripts/run_update_data_daily.sh
#   bash scripts/run_update_data_daily.sh --post-close
#
# cron 部署：
#   30 8 * * 1-5 /Users/zcdeng/projects/KSS/scripts/run_update_data_daily.sh >> /Users/zcdeng/projects/KSS/storage/logs/cron/update_data_daily.log 2>&1
#   5 18 * * 1-5 /Users/zcdeng/projects/KSS/scripts/run_update_data_daily.sh --post-close >> /Users/zcdeng/projects/KSS/storage/logs/cron/update_data_daily_eod.log 2>&1

set -e
set -o pipefail

PROJECT_ROOT="/Users/zcdeng/projects/KSS"
PYTHON="/opt/homebrew/opt/python@3.11/bin/python3.11"
HERMES_ENV="/Users/zcdeng/projects/agentos-stack/hermes_agent/.env"
LOG_DIR="$PROJECT_ROOT/storage/logs/cron"

POST_CLOSE=0
UPDATE_ARGS=()
for arg in "$@"; do
  if [ "$arg" = "--post-close" ]; then
    POST_CLOSE=1
    continue
  fi
  UPDATE_ARGS+=("$arg")
done

RUN_MODE="日内任务"
if [ "$POST_CLOSE" -eq 1 ]; then
  RUN_MODE="盘后补跑（T+0）"
fi

mkdir -p "$LOG_DIR"
echo "===== $(date '+%Y-%m-%d %H:%M:%S') update_data_daily-wrapper 开始 | mode=${RUN_MODE} ====="

# Tushare token 从 Hermes .env 加载（用于 cron 场景）
# 注意：grep -E 限定单行，避开 .env 里 cookie 等不规则行
if [ -f "$HERMES_ENV" ]; then
  TUSHARE_TOKEN=$( (grep -E '^TUSHARE_TOKEN=' "$HERMES_ENV" || true) | head -1 | cut -d= -f2-)
  TUSHARE_TOKEN="${TUSHARE_TOKEN%\"}"; TUSHARE_TOKEN="${TUSHARE_TOKEN#\"}"
  if [ -n "$TUSHARE_TOKEN" ]; then
    export TUSHARE_TOKEN
    echo "[wrapper] token loaded: yes"
  fi
fi
# fallback：若 .env 没 TUSHARE_TOKEN，尝试 KSS 项目里的 token 文件
if [ -z "$TUSHARE_TOKEN" ] && [ -f "$HOME/.tushare/token" ]; then
  export TUSHARE_TOKEN=$(cat "$HOME/.tushare/token")
  echo "[wrapper] token fallback: ok"
elif [ -z "${TUSHARE_TOKEN:-}" ]; then
  echo "[wrapper] token loaded: no"
fi

run_with_retry() {
  local label="$1"
  shift
  local max_attempts=3
  local attempt=1
  local sleep_seconds=8

  while [ "$attempt" -le "$max_attempts" ]; do
    echo "[wrapper] [${label}] attempt=${attempt}/${max_attempts} start"
    if "$@"; then
      echo "[wrapper] [${label}] success"
      return 0
    fi

    local rc=$?
    echo "[wrapper] [${label}] failed rc=${rc}"
    if [ "$attempt" -lt "$max_attempts" ]; then
      echo "[wrapper] [${label}] retry after ${sleep_seconds}s"
      sleep "$sleep_seconds"
      attempt=$((attempt + 1))
      sleep_seconds=$((sleep_seconds * 2))
    else
      echo "[wrapper] [${label}] retry exhausted"
      return "$rc"
    fi
  done
}

cd "$PROJECT_ROOT"
UPDATE_ARGS=(--throttle 0.5 "${UPDATE_ARGS[@]}")
# 1) 全量增量更新整个股票池 cs_data（kind 自适应：stock→daily / fund→fund_daily / index→index_daily）
if [ "$POST_CLOSE" -eq 1 ]; then
  # 盘后任务保留今天的收盘口径（通常与交易日 T+0 场景一致）
  UPDATE_ARGS=(--end "$(date '+%Y-%m-%d')" "${UPDATE_ARGS[@]}")
  echo "[wrapper] [step-1] post-close mode: force end=${UPDATE_ARGS[1]}"
fi
run_with_retry "update_cs_data" "$PYTHON" scripts/update_cs_data.py "${UPDATE_ARGS[@]}"

# 2) 指数行情入库（market_strip.json，仪表盘指数条）—— 折进日更，不另设 cron
echo "[wrapper] [step-2] refresh_market_strip"
run_with_retry "refresh_market_strip" "$PYTHON" scripts/refresh_market_strip.py

echo "[wrapper] update_data_daily-wrapper finished"
