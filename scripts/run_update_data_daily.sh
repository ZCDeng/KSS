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
# 部署：kss/config/cron_jobs.yaml 清单条目 + scripts/sync_launchd.py（不再手动 crontab -e）。

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
LOG_DIR="$KSS_STATE_ROOT/storage/logs/cron"

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

# Tushare token：Keychain 优先，dev 回落项目 .env，再回落 $HOME/.tushare/token。
source "$PROJECT_ROOT/scripts/lib_cron_credentials.sh"
if kss_load_credential TUSHARE_TOKEN "$KSS_ENV"; then
  echo "[wrapper] token loaded: yes"
elif [ -f "$HOME/.tushare/token" ]; then
  export TUSHARE_TOKEN=$(cat "$HOME/.tushare/token")
  echo "[wrapper] token fallback: ok"
else
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

# exit 0 = ok；exit 1 = 异常 → 重试；exit 2 = stale 缺口 → 不重试、告警、继续 strip
UPDATE_RC=0
attempt=1
sleep_seconds=8
while [ "$attempt" -le 3 ]; do
  echo "[wrapper] [update_cs_data] attempt=${attempt}/3 start"
  set +e
  "$PYTHON" scripts/update_cs_data.py "${UPDATE_ARGS[@]}"
  UPDATE_RC=$?
  set -e
  if [ "$UPDATE_RC" -eq 0 ]; then
    echo "[wrapper] [update_cs_data] success"
    break
  fi
  if [ "$UPDATE_RC" -eq 2 ]; then
    echo "[wrapper] [update_cs_data] completed with STALE gaps (no retry)"
    break
  fi
  echo "[wrapper] [update_cs_data] failed rc=${UPDATE_RC}"
  if [ "$attempt" -lt 3 ]; then
    echo "[wrapper] [update_cs_data] retry after ${sleep_seconds}s"
    sleep "$sleep_seconds"
    attempt=$((attempt + 1))
    sleep_seconds=$((sleep_seconds * 2))
  else
    echo "[wrapper] [update_cs_data] retry exhausted"
    exit "$UPDATE_RC"
  fi
done
if [ "$UPDATE_RC" -eq 2 ]; then
  echo "[wrapper] [ALERT] update_cs_data 存在 ≥4 日 stale 缺口 → $LOG_DIR/update_cs_data_last.json"
  if [ -f "$LOG_DIR/update_cs_data_last.json" ]; then
    cat "$LOG_DIR/update_cs_data_last.json"
  fi
fi

# 事件驱动链触发（plan 2026-07-14-001 / KTD1）：盘后模式且数据更新干净成功（rc=0）
# 时踢下游选股，链式传递 picks→mi→indicator→review。触发点故意放在 step-2 之前——
# 指数条刷新失败与 cs_data 新鲜度无关，不该拦断全链（KTD1 防污染细则(3)）。
# rc=2（数据缺口告警）不触发：缺口数据不该生成产物，下游 gate 也会拦。
if [ "$POST_CLOSE" -eq 1 ] && [ "$UPDATE_RC" -eq 0 ]; then
  source "$PROJECT_ROOT/scripts/lib_cron_chain.sh"
  kss_kick_next formal_daily_picks
fi

# 2) 指数行情入库（market_strip.json，仪表盘指数条）—— 折进日更，不另设 cron
echo "[wrapper] [step-2] refresh_market_strip"
run_with_retry "refresh_market_strip" "$PYTHON" scripts/refresh_market_strip.py

if [ "$UPDATE_RC" -eq 2 ]; then
  echo "[wrapper] update_data_daily-wrapper finished WITH STALE ALERT"
  exit 2
fi
echo "[wrapper] update_data_daily-wrapper finished"
