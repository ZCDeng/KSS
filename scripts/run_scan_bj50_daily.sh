#!/bin/bash
# 北证 50 全量扫描 + Telegram Top 5 推送 - 每日 17:45 cron wrapper.
#
# 包装目的:
#   1. cron 不读 zshrc -> 显式从 KSS 项目 .env 加载 TELEGRAM_* / TUSHARE_TOKEN
#   2. 用 Homebrew Python 绝对路径, 避免 cron PATH 与 shell 不一致
#   3. --force-refresh 确保拉到当天的 daily / index_daily / daily_basic
#   4. --push-telegram 推送 Top 5 + 关键变动 (排名↑↓ / 户数警告 / 新雷)
#
# 手动测试 (不发 telegram):
#   bash scripts/run_scan_bj50_daily.sh
# 手动测试 (发 telegram):
#   bash scripts/run_scan_bj50_daily.sh --push
#
# 部署：kss/config/cron_jobs.yaml 清单条目 + scripts/sync_launchd.py（不再手动 crontab -e）。

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
KSS_ENV="$PROJECT_ROOT/.env"

# 解析参数: --push 转为 python 端 --push-telegram
PUSH_FLAG=""
for arg in "$@"; do
  if [ "$arg" = "--push" ] || [ "$arg" = "--push-telegram" ]; then
    PUSH_FLAG="--push-telegram"
  fi
done

# 时间戳便于 log 追踪
echo "===== $(date '+%Y-%m-%d %H:%M:%S') scan_bj50_daily 开始 (push=${PUSH_FLAG:-no}) ====="

# 加载 KSS .env 里的 Telegram + Tushare 凭据 (不 source 整个 .env, 防特殊字符行炸).
source "$PROJECT_ROOT/scripts/lib_cron_credentials.sh"
if kss_load_credential TELEGRAM_BOT_TOKEN "$KSS_ENV"; then
  kss_load_credential TELEGRAM_CHAT_ID "$KSS_ENV" || true
  kss_load_credential TELEGRAM_API_URL "$KSS_ENV" || true
else
  echo "[wrapper] WARNING: 未在 Keychain / $KSS_ENV 找到 telegram 凭据, telegram 推送将降级到 console"
fi
kss_load_credential TUSHARE_TOKEN "$KSS_ENV" || true
echo "[wrapper] loaded TELEGRAM_BOT_TOKEN length=${#TELEGRAM_BOT_TOKEN} / TUSHARE_TOKEN length=${#TUSHARE_TOKEN}"

# 准备 log 目录
mkdir -p "$KSS_STATE_ROOT/storage/logs/cron"

cd "$PROJECT_ROOT"
# 不可 exec：扫描后还需增量刷新 bj_cache 到今日（App 日线真源）。
set +e
"$PYTHON" scripts/scan_bj50.py --force-refresh --threads 4 $PUSH_FLAG
scan_rc=$?
set -e
echo "===== $(date '+%Y-%m-%d %H:%M:%S') refresh_bj_daily 开始 ====="
# 即使扫描部分失败，也尽量把已有缓存推到最新交易日
"$PYTHON" scripts/refresh_bj_daily.py || echo "[wrapper] WARNING: refresh_bj_daily 失败 (rc=$?)"
echo "===== $(date '+%Y-%m-%d %H:%M:%S') scan_bj50_daily 结束 scan_rc=$scan_rc ====="
exit "$scan_rc"
