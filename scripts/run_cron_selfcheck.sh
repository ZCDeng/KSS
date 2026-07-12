#!/bin/zsh
# ---------------------------------------------------------------------------
# 开机/登录自检：补跑因关机漏跑的 launchd 任务。
#
# 由 com.zcdeng.kss.selfcheck（RunAtLoad=true）在每次登录后触发。
# 登录瞬间其它 LaunchAgent 可能尚未 bootstrap 完成，先睡 90s 让其就绪，
# 再调 bridge cron-catchup —— 只 kickstart「应跑未跑」且启用的任务，
# selfcheck 自身永不参与，停用任务跳过。判定逻辑与应用内「一键补跑」完全一致。
# ---------------------------------------------------------------------------
set -u

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
: "${KSS_STATE_ROOT:=$PROJECT_ROOT}"
LOG="$PROJECT_ROOT/storage/logs/cron/selfcheck.log"

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

cd "$PROJECT_ROOT" || exit 1
mkdir -p "$PROJECT_ROOT/storage/logs/cron"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] selfcheck wake, sleep 90s 等待其它 agent bootstrap" >> "$LOG"
sleep 90

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 运行 cron-catchup …" >> "$LOG"
"$PYTHON" "$PROJECT_ROOT/scripts/kss_app_bridge.py" cron-catchup >> "$LOG" 2>&1
echo "[$(date '+%Y-%m-%d %H:%M:%S')] selfcheck done" >> "$LOG"
