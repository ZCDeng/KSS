#!/bin/bash
# cron 任务日志轮转 —— 日终触发。见 KTD10（plan 2026-07-12-005 / U7）。
#
# 手动测试：bash scripts/run_rotate_cron_logs.sh
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

echo "===== $(date '+%Y-%m-%d %H:%M:%S') rotate_cron_logs 开始 ====="
cd "$PROJECT_ROOT"
exec "$PYTHON" scripts/rotate_cron_logs.py "$KSS_STATE_ROOT"
