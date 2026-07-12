#!/bin/bash
# 数据目录刷新 - 每日 8:50 cron wrapper（在 macro 8:35 / cs_data 8:30 更新之后重建字典）.
#
# 用 .venv-desktop 解释器（有 pyarrow 24，project-owned，不被 brew cleanup GC）—— 见 plan KTD-8a。
# 失败语义：生成器对单数据集 fail-soft、对「磁盘有 parquet 但零解析成功」fail-loud(非零退出)。
#
# 手动测试：bash scripts/run_data_catalog_daily.sh

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

echo "===== $(date '+%Y-%m-%d %H:%M:%S') data_catalog 开始 ====="

cd "$PROJECT_ROOT"
exec "$PYTHON" scripts/build_data_catalog.py "$@"
