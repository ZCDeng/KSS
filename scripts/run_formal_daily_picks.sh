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
# TUSHARE_TOKEN 从 .env 加载
TUSHARE_TOKEN=$(grep -E '^TUSHARE_TOKEN=' "$PROJECT_ROOT/.env" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"')
KSS_STATE_ROOT="$KSS_STATE_ROOT" TUSHARE_TOKEN="$TUSHARE_TOKEN" \
  "$PYTHON" "$PROJECT_ROOT/scripts/kss_app_bridge.py" run formal-daily-picks --force "$@" 2>&1

# 二次校验：当日选股必须落在 paper_trade_picks 表（与 bridge 内断言双保险）
# R2-U2: 落盘目标 U15 割接后已从 storage/paper_trade/{date}.json 迁到 kss.db，
# 校验也须跟着改读 kss.db，否则本检查永远查一个不再写入的文件、天天误报。
TODAY=$(date '+%Y-%m-%d')
# 若用户传了 --date / date= 则跳过「今日」硬检（脚本参数由 bridge 解析；这里只检今日 cron）
if [[ "$*" != *date* ]]; then
  DB_PATH="$KSS_STATE_ROOT/storage/kss.db"
  EXISTS=$("$PYTHON" -c "
import sys
sys.path.insert(0, '$PROJECT_ROOT')
from kss.storage.paper_trade import day_exists
print('yes' if day_exists('$TODAY', db_path='$DB_PATH') else 'no')
")
  if [ "$EXISTS" != "yes" ]; then
    echo "[formal_daily_picks] ALERT: $TODAY 未落盘 paper_trade_picks（$DB_PATH）" >&2
    exit 2
  fi
  echo "[formal_daily_picks] ok: $TODAY 已落盘 paper_trade_picks（$DB_PATH）"
fi
