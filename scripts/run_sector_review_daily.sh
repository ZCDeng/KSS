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

# 时间戳便于 log 追踪
echo "===== $(date '+%Y-%m-%d %H:%M:%S') sector_review_daily 开始 ====="

# 加载 KSS .env 里的 Telegram + Tushare 凭据.
# 不 source 整个 .env：.env 里可能混有含特殊字符的行（cookie/jwt 等），
# bash source 会因 `.foo=bar` 之类的行当作 sourcing 命令而失败.
# 改为安全 grep 只取需要的变量.
source "$PROJECT_ROOT/scripts/lib_cron_credentials.sh"
if kss_load_credential TELEGRAM_BOT_TOKEN "$KSS_ENV"; then
  kss_load_credential TELEGRAM_CHAT_ID "$KSS_ENV" || true
  kss_load_credential TELEGRAM_API_URL "$KSS_ENV" || true
else
  echo "[wrapper] WARNING: 未在 Keychain / $KSS_ENV 找到 telegram 凭据，推送将降级到 console"
fi
kss_load_credential TUSHARE_TOKEN "$KSS_ENV" || true
echo "[wrapper] loaded TELEGRAM_BOT_TOKEN length=${#TELEGRAM_BOT_TOKEN} / TUSHARE_TOKEN length=${#TUSHARE_TOKEN}"

# LLM 凭据（OpenAI / DeepSeek / 可选 model）. commentary 模块走 OPENAI SDK，
# key 优先级：OPENAI_API_KEY > DEEPSEEK_API_KEY. Keychain 优先，dev 回落项目 .env.
kss_load_credential OPENAI_API_KEY "$KSS_ENV" && echo "[wrapper] loaded OPENAI_API_KEY length=${#OPENAI_API_KEY}"
kss_load_credential OPENAI_BASE_URL "$KSS_ENV" && echo "[wrapper] loaded OPENAI_BASE_URL=$OPENAI_BASE_URL"
kss_load_credential DEEPSEEK_API_KEY "$KSS_ENV" && echo "[wrapper] loaded DEEPSEEK_API_KEY length=${#DEEPSEEK_API_KEY}"
kss_load_credential KSS_LLM_MODEL "$KSS_ENV" && echo "[wrapper] loaded KSS_LLM_MODEL=$KSS_LLM_MODEL"

cd "$PROJECT_ROOT"
exec "$PYTHON" scripts/sector_review.py --channel all "$@"
