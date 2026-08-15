#!/bin/bash
# 舆情热点 digest · 盘后场 cron wrapper(plan U10)。
#
# 与 run_sector_review_daily.sh 对齐:
#   1. cron 不读 zshrc → 显式从 KSS .env 加载 TUSHARE_TOKEN(取龙头/快照需要),
#      从 Hermes .env 加载 LLM 凭据(情绪判定走 OPENAI SDK)。
#   2. Homebrew Python 绝对路径,避免 cron PATH 不一致。
#   3. entry 先探活 combosearch CLI(2026-08-15 接替下线的 seek);不可用则退出非零
#      让 cron 监控接管,不空跑。
#
# 手动测试:
#   bash scripts/run_news_digest_postclose.sh
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

echo "===== $(date '+%Y-%m-%d %H:%M:%S') news_digest_postclose 开始 ====="

# Tushare(取龙头/快照) + LLM(情绪判定) 凭据。Keychain 优先，dev 回落项目 .env。
source "$PROJECT_ROOT/scripts/lib_cron_credentials.sh"
kss_load_credential TUSHARE_TOKEN "$KSS_ENV" || true
kss_load_credential OPENAI_API_KEY "$KSS_ENV" || true
kss_load_credential OPENAI_BASE_URL "$KSS_ENV" || true
kss_load_credential DEEPSEEK_API_KEY "$KSS_ENV" || true
kss_load_credential KSS_LLM_MODEL "$KSS_ENV" || true

cd "$PROJECT_ROOT"
exec "$PYTHON" scripts/run_news_digest.py --scene 盘后 "$@"
