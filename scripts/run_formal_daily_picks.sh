#!/bin/bash
# 正式每日选股 (formal-daily-picks) - 17:00 收盘后 cron wrapper
#
# 行为：通过 bridge subprocess 调 formal-daily-picks 任务（依赖完整 KSS Python 环境）
# 失败 → 退出非零让 cron 系统监控接管，不空跑。
#
# 手动测试：
#   bash scripts/run_formal_daily_picks.sh
#   bash scripts/run_formal_daily_picks.sh --date 2026-07-08
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

echo "===== $(date '+%Y-%m-%d %H:%M:%S') formal_daily_picks 开始 ====="
mkdir -p "$LOG_DIR"

cd "$PROJECT_ROOT"
source "$PROJECT_ROOT/scripts/lib_cron_chain.sh"

# 事件驱动链 gate（plan 2026-07-14-001 / KTD2）：--date 回填运行跳过 gate/标记/踢链，
# 只有常规当日运行走链语义。
CHAIN_RUN=1
if [[ "$*" == *date* ]]; then
  CHAIN_RUN=0
fi
if [ "$CHAIN_RUN" -eq 1 ]; then
  kss_gate_or_exit picks
fi

# TUSHARE_TOKEN 从 .env 加载
TUSHARE_TOKEN=$(grep -E '^TUSHARE_TOKEN=' "$PROJECT_ROOT/.env" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"')
export KSS_STATE_ROOT TUSHARE_TOKEN
# KTD3 超时护栏：选股撞网络断连期不再无限挂死（07-14 悬空事故）。
# formal 失败不挡风格对照（KTD4）；用 +e 暂时放开 set -e
set +e
kss_run_with_timeout 1800 \
  "$PYTHON" "$PROJECT_ROOT/scripts/kss_app_bridge.py" run formal-daily-picks --force "$@" 2>&1
FORMAL_RC=$?
set -e

# 风格对照：与 formal 隔离；对照失败不拖垮 formal 退出码语义
set +e
kss_run_with_timeout 1800 \
  "$PYTHON" "$PROJECT_ROOT/scripts/style_contrast_daily.py" "$@" 2>&1
STYLE_RC=$?
set -e
if [ "$STYLE_RC" -ne 0 ]; then
  echo "[formal_daily_picks] WARN: style_contrast_daily exit=$STYLE_RC（不阻断 formal）" >&2
fi
if [ "$FORMAL_RC" -ne 0 ]; then
  echo "[formal_daily_picks] formal-daily-picks exit=$FORMAL_RC" >&2
  exit "$FORMAL_RC"
fi

# 二次校验：本次运行的产物必须落库。prediction_date 语义 = 数据日（panel 最新交易日），
# 参照系用 gate 的目标数据日——不能用日历今天（跨零点运行时 now() 比数据日快一天而误报，
# plan 2026-07-14-001 全链演练实测坑）。
if [ "$CHAIN_RUN" -eq 1 ]; then
  DB_PATH="$KSS_STATE_ROOT/storage/kss.db"
  TARGET_DAY=$("$PYTHON" "$PROJECT_ROOT/scripts/check_pipeline_gate.py" \
    --task picks --action target-day --data-root "$PROJECT_ROOT" --state-root "$KSS_STATE_ROOT")
  LANDED=$("$PYTHON" -c "
import sys
sys.path.insert(0, '$PROJECT_ROOT')
from kss.storage.paper_trade import day_exists
print('yes' if day_exists('$TARGET_DAY', db_path='$DB_PATH') else 'no')
")
  if [ "$LANDED" != "yes" ]; then
    echo "[formal_daily_picks] ALERT: 目标数据日 $TARGET_DAY 的 picks 未落库（$DB_PATH）" >&2
    exit 2
  fi
  echo "[formal_daily_picks] ok: $TARGET_DAY picks 已落库（$DB_PATH）"
  kss_mark_done picks
  kss_kick_next mi_signal_pack
fi
