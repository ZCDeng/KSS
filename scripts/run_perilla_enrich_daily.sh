#!/bin/bash
# 紫苏叶个股富化缓存预热 - 每日 cron wrapper (默认停用, enabled:false)。
#
# 预热 core+main 票的机构持仓/PE/美股对标缓存, 供 App/MCP 热路径命中。
# cron 不读 zshrc -> 显式加载 .env 的 TUSHARE_TOKEN; 用 Homebrew Python 绝对路径。
#
# 手动测试 (冒烟, 只跑1只):
#   bash scripts/run_perilla_enrich_daily.sh --limit 1
#
# cron 部署 (每交易日 18:10, 北证/沪深收盘 + tushare 延迟 buffer):
#   10 18 * * 1-5 /Users/zcdeng/projects/KSS/scripts/run_perilla_enrich_daily.sh >> /Users/zcdeng/projects/KSS/storage/logs/cron/perilla_enrich_daily.log 2>&1

set -e
set -o pipefail

PROJECT_ROOT="/Users/zcdeng/projects/KSS"
PYTHON="/opt/homebrew/opt/python@3.11/bin/python3.11"
KSS_ENV="$PROJECT_ROOT/.env"

echo "===== $(date '+%Y-%m-%d %H:%M:%S') perilla_enrich_daily 开始 ====="

if [ -f "$KSS_ENV" ]; then
  TUSHARE_TOKEN=$( (grep -E '^TUSHARE_TOKEN=' "$KSS_ENV" || true) | head -1 | cut -d= -f2-)
  TUSHARE_TOKEN="${TUSHARE_TOKEN%\"}"; TUSHARE_TOKEN="${TUSHARE_TOKEN#\"}"
  export TUSHARE_TOKEN
fi

cd "$PROJECT_ROOT"
"$PYTHON" scripts/refresh_perilla_enrich.py "$@"

echo "===== $(date '+%Y-%m-%d %H:%M:%S') perilla_enrich_daily 结束 ====="
