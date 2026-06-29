#!/bin/bash
# 舆情热点 digest · 盘前场 cron wrapper(plan U10)。
#
# 与 run_sector_review_daily.sh 对齐:
#   1. cron 不读 zshrc → 显式从 KSS .env 加载 TUSHARE_TOKEN(取龙头/快照需要),
#      从 Hermes .env 加载 LLM 凭据(情绪判定走 OPENAI SDK)。
#   2. Homebrew Python 绝对路径,避免 cron PATH 不一致。
#   3. entry 先探活 seek 容器;不在则退出非零让 cron 监控接管,不空跑。
#
# 手动测试:
#   bash scripts/run_news_digest_premarket.sh
#
# cron 部署(交易日 8:40,盘前,先于 9:30 开盘):
#   40 8 * * 1-5 /Users/zcdeng/projects/KSS/scripts/run_news_digest_premarket.sh >> /Users/zcdeng/projects/KSS/storage/logs/cron/news_digest_premarket.log 2>&1

set -e
set -o pipefail

PROJECT_ROOT="/Users/zcdeng/projects/KSS"
PYTHON="/opt/homebrew/opt/python@3.11/bin/python3.11"
KSS_ENV="$PROJECT_ROOT/.env"

echo "===== $(date '+%Y-%m-%d %H:%M:%S') news_digest_premarket 开始 ====="

# KSS .env:Tushare(取龙头/快照)。安全 grep,不 source 整个 .env。
if [ -f "$KSS_ENV" ]; then
  TUSHARE_TOKEN=$(grep -E '^TUSHARE_TOKEN=' "$KSS_ENV" | head -1 | cut -d= -f2-)
  TUSHARE_TOKEN="${TUSHARE_TOKEN%\"}"; TUSHARE_TOKEN="${TUSHARE_TOKEN#\"}"
  export TUSHARE_TOKEN
fi

# Hermes .env:LLM 凭据(情绪判定)。key 优先级 OPENAI_API_KEY > DEEPSEEK_API_KEY。
HERMES_ENV="/Users/zcdeng/projects/agentos-stack/hermes_agent/.env"
if [ -f "$HERMES_ENV" ]; then
  _load_env_val() {
    local val
    val=$(grep -E "^$1=" "$HERMES_ENV" 2>/dev/null | head -1 | cut -d= -f2- | sed 's/^"//;s/"$//') || true
    printf '%s' "$val"
  }
  OPENAI_API_KEY=$(_load_env_val "OPENAI_API_KEY")
  OPENAI_BASE_URL=$(_load_env_val "OPENAI_BASE_URL")
  DEEPSEEK_API_KEY=$(_load_env_val "DEEPSEEK_API_KEY")
  KSS_LLM_MODEL=$(_load_env_val "KSS_LLM_MODEL")
  [ -n "$OPENAI_API_KEY" ] && export OPENAI_API_KEY
  [ -n "$OPENAI_BASE_URL" ] && export OPENAI_BASE_URL
  [ -n "$DEEPSEEK_API_KEY" ] && export DEEPSEEK_API_KEY
  [ -n "$KSS_LLM_MODEL" ] && export KSS_LLM_MODEL
fi

cd "$PROJECT_ROOT"
exec "$PYTHON" scripts/run_news_digest.py --scene 盘前 "$@"
