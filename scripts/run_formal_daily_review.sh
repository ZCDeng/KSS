#!/bin/bash
# 正式每日个股复盘 (formal-daily-review) - 17:20 收盘后 cron wrapper
#
# 行为：通过 bridge subprocess 调 formal-daily-review 任务（依赖完整 KSS Python 环境）
# 失败 → 退出非零让 cron 系统监控接管，不空跑。
#
# 手动测试：
#   bash scripts/run_formal_daily_review.sh
#   bash scripts/run_formal_daily_review.sh --date 2026-07-08
#
# 部署：kss/config/cron_jobs.yaml 清单条目 + scripts/sync_launchd.py（不再手动 crontab -e）。

set -e
set -o pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
: "${KSS_STATE_ROOT:=$PROJECT_ROOT}"
LOG_DIR="$KSS_STATE_ROOT/storage/logs/cron"

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

echo "===== $(date '+%Y-%m-%d %H:%M:%S') formal_daily_review 开始 ====="
mkdir -p "$LOG_DIR"

cd "$PROJECT_ROOT"
source "$PROJECT_ROOT/scripts/lib_cron_chain.sh"

# 事件驱动链 gate（plan 2026-07-14-001 / KTD2）：--date 回填运行跳过链语义。
CHAIN_RUN=1
if [[ "$*" == *date* ]]; then
  CHAIN_RUN=0
fi
if [ "$CHAIN_RUN" -eq 1 ]; then
  kss_gate_or_exit review
fi

# TUSHARE_TOKEN 从 .env 加载
TUSHARE_TOKEN=$(grep -E '^TUSHARE_TOKEN=' "$PROJECT_ROOT/.env" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"')
export TUSHARE_TOKEN

# 链式运行时 mi-signal-pack 刚在上一环跑完，无需重跑；手动/回填运行保留兜跑（软失败不阻断）。
if [ "$CHAIN_RUN" -eq 0 ]; then
  echo "----- $(date '+%Y-%m-%d %H:%M:%S') mi-signal-pack -----"
  set +e
  kss_run_with_timeout 600 "$PYTHON" "$PROJECT_ROOT/scripts/kss_app_bridge.py" run mi-signal-pack 2>&1
  pack_rc=$?
  set -e
  if [ "$pack_rc" -ne 0 ]; then
    echo "WARN: mi-signal-pack exit=$pack_rc （继续 formal-daily-review）"
  fi
fi

echo "----- $(date '+%Y-%m-%d %H:%M:%S') formal-daily-review -----"
# KTD3 超时护栏（复盘 30 分钟档）
kss_run_with_timeout 1800 \
  "$PYTHON" "$PROJECT_ROOT/scripts/kss_app_bridge.py" run formal-daily-review "$@" 2>&1

if [ "$CHAIN_RUN" -eq 1 ]; then
  kss_mark_done review
  # A successful formal review is the normal event path for the investment
  # daily; its own 23:20 calendar entry remains a safe fallback.
  kss_kick_next investment_analysis_daily
fi
