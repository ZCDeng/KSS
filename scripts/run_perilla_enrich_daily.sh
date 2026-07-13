#!/bin/bash
# 紫苏叶个股富化缓存预热 - 每日 cron wrapper (默认停用, enabled:false)。
#
# 预热 core+main 票的机构持仓/PE/美股对标缓存, 供 App/MCP 热路径命中。
# cron 不读 zshrc -> 显式加载 .env 的 TUSHARE_TOKEN; 用 Homebrew Python 绝对路径。
#
# 手动测试 (冒烟, 只跑1只):
#   bash scripts/run_perilla_enrich_daily.sh --limit 1
#
# 部署：kss/config/cron_jobs.yaml 清单条目 + scripts/sync_launchd.py（不再手动 crontab -e）。

set -e
set -o pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

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

echo "===== $(date '+%Y-%m-%d %H:%M:%S') perilla_enrich_daily 开始 ====="

source "$PROJECT_ROOT/scripts/lib_cron_credentials.sh"
kss_load_credential TUSHARE_TOKEN "$KSS_ENV" || true

cd "$PROJECT_ROOT"
"$PYTHON" scripts/refresh_perilla_enrich.py "$@"

echo "===== $(date '+%Y-%m-%d %H:%M:%S') perilla_enrich_daily 结束 ====="
