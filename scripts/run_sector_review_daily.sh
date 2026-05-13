#!/bin/bash
# 收盘后板块复盘 - 每个交易日 17:30 cron wrapper.
#
# 包装目的（与 run_paper_trade_daily.sh 对齐）：
#   1. cron 不读 zshrc → 自己显式从 KSS 项目 .env 加载 TELEGRAM_* + TUSHARE_TOKEN 变量
#   2. 用 Homebrew Python 绝对路径，避免 cron PATH 与 shell 不一致
#   3. 异常不让 cron 邮件爆炸——失败时返回非零让 cron 系统监控接管
#
# 手动测试：
#   bash scripts/run_sector_review_daily.sh                      # 实际推送
#   bash scripts/run_sector_review_daily.sh --dry-run            # 仅打印
#   bash scripts/run_sector_review_daily.sh --date 2026-05-12    # 指定日期
#
# cron 部署（每个交易日 17:30 收盘后，等 Tushare pro 数据准备好）：
#   30 17 * * 1-5 /Users/zcdeng/projects/KSS/scripts/run_sector_review_daily.sh >> /Users/zcdeng/projects/KSS/storage/logs/cron/sector_review_daily.log 2>&1

set -e
set -o pipefail

PROJECT_ROOT="/Users/zcdeng/projects/KSS"
PYTHON="/opt/homebrew/opt/python@3.11/bin/python3.11"
KSS_ENV="$PROJECT_ROOT/.env"

# 时间戳便于 log 追踪
echo "===== $(date '+%Y-%m-%d %H:%M:%S') sector_review_daily 开始 ====="

# 加载 KSS .env 里的 Telegram + Tushare 凭据.
# 不 source 整个 .env：.env 里可能混有含特殊字符的行（cookie/jwt 等），
# bash source 会因 `.foo=bar` 之类的行当作 sourcing 命令而失败.
# 改为安全 grep 只取需要的变量.
if [ -f "$KSS_ENV" ]; then
  TELEGRAM_BOT_TOKEN=$(grep -E '^TELEGRAM_BOT_TOKEN=' "$KSS_ENV" | head -1 | cut -d= -f2-)
  TELEGRAM_CHAT_ID=$(grep -E '^TELEGRAM_CHAT_ID=' "$KSS_ENV" | head -1 | cut -d= -f2-)
  TELEGRAM_API_URL=$(grep -E '^TELEGRAM_API_URL=' "$KSS_ENV" | head -1 | cut -d= -f2-)
  TUSHARE_TOKEN=$(grep -E '^TUSHARE_TOKEN=' "$KSS_ENV" | head -1 | cut -d= -f2-)
  # 去掉可能的两端引号
  TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN%\"}"; TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN#\"}"
  TELEGRAM_CHAT_ID="${TELEGRAM_CHAT_ID%\"}"; TELEGRAM_CHAT_ID="${TELEGRAM_CHAT_ID#\"}"
  TELEGRAM_API_URL="${TELEGRAM_API_URL%\"}"; TELEGRAM_API_URL="${TELEGRAM_API_URL#\"}"
  TUSHARE_TOKEN="${TUSHARE_TOKEN%\"}"; TUSHARE_TOKEN="${TUSHARE_TOKEN#\"}"
  export TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID TELEGRAM_API_URL TUSHARE_TOKEN
  echo "[wrapper] loaded TELEGRAM_BOT_TOKEN length=${#TELEGRAM_BOT_TOKEN} / TUSHARE_TOKEN length=${#TUSHARE_TOKEN}"
else
  echo "[wrapper] WARNING: $KSS_ENV 不存在，telegram 推送将降级到 console"
fi

# 加载 Hermes .env 里的 LLM 凭据（OpenAI / DeepSeek / 可选 model）.
# commentary 模块走 OPENAI SDK，key 优先级：OPENAI_API_KEY > DEEPSEEK_API_KEY.
HERMES_ENV="/Users/zcdeng/projects/agentos-stack/hermes_agent/.env"
if [ -f "$HERMES_ENV" ]; then
  _load_env_val() {
    grep -E "^$1=" "$HERMES_ENV" 2>/dev/null | head -1 | cut -d= -f2- | sed 's/^"//;s/"$//'
  }
  OPENAI_API_KEY=$(_load_env_val "OPENAI_API_KEY")
  OPENAI_BASE_URL=$(_load_env_val "OPENAI_BASE_URL")
  DEEPSEEK_API_KEY=$(_load_env_val "DEEPSEEK_API_KEY")
  KSS_LLM_MODEL=$(_load_env_val "KSS_LLM_MODEL")
  [ -n "$OPENAI_API_KEY" ] && { export OPENAI_API_KEY; echo "[wrapper] loaded OPENAI_API_KEY length=${#OPENAI_API_KEY}"; }
  [ -n "$OPENAI_BASE_URL" ] && { export OPENAI_BASE_URL; echo "[wrapper] loaded OPENAI_BASE_URL=$OPENAI_BASE_URL"; }
  [ -n "$DEEPSEEK_API_KEY" ] && { export DEEPSEEK_API_KEY; echo "[wrapper] loaded DEEPSEEK_API_KEY length=${#DEEPSEEK_API_KEY}"; }
  [ -n "$KSS_LLM_MODEL" ] && { export KSS_LLM_MODEL; echo "[wrapper] loaded KSS_LLM_MODEL=$KSS_LLM_MODEL"; }
fi

cd "$PROJECT_ROOT"
exec "$PYTHON" scripts/sector_review.py --channel all "$@"
