#!/bin/zsh
# ---------------------------------------------------------------------------
# 开机/登录自检：补跑因关机漏跑的 launchd 任务。
#
# 由 com.zcdeng.kss.selfcheck（RunAtLoad=true）在每次登录后触发。
# 登录瞬间其它 LaunchAgent 可能尚未 bootstrap 完成，先睡 90s 让其就绪，
# 再调 bridge cron-catchup —— 只 kickstart「应跑未跑」且启用的任务，
# selfcheck 自身永不参与，停用任务跳过。判定逻辑与应用内「一键补跑」完全一致。
#
# 补跑之后追加一道数据线：自选 cs_data 日线新鲜度（bridge cs-freshness notify）。
# 落后应有日线日 >1 个交易日即推 Telegram——针对 git restore/stash 冲掉根目录
# cs_data_*.csv 这类 cron 正常但数据被回滚的静默事故（宽限 1 个交易日，单次漏跑
# 8:30 日更不告警，交给 catchup 恢复）。
# ---------------------------------------------------------------------------
set -u

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
: "${KSS_STATE_ROOT:=$PROJECT_ROOT}"
LOG="$KSS_STATE_ROOT/storage/logs/cron/selfcheck.log"

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
mkdir -p "$KSS_STATE_ROOT/storage/logs/cron"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] selfcheck wake, sleep 90s 等待其它 agent bootstrap" >> "$LOG"
sleep 90

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 运行 cron-catchup …" >> "$LOG"
"$PYTHON" "$PROJECT_ROOT/scripts/kss_app_bridge.py" cron-catchup >> "$LOG" 2>&1

# Telegram 凭证：Keychain 优先、dev 回落 .env（同其它 cron wrapper）。缺凭证不阻断——
# bridge 侧 TelegramBot 会 log warning 后 return False，检查结果仍进本日志。
source "$PROJECT_ROOT/scripts/lib_cron_credentials.sh"
kss_load_credential TELEGRAM_BOT_TOKEN "$PROJECT_ROOT/.env" \
    || echo "[$(date '+%Y-%m-%d %H:%M:%S')] WARN: TELEGRAM_BOT_TOKEN 未解析，陈旧告警只落日志" >> "$LOG"
kss_load_credential TELEGRAM_CHAT_ID "$PROJECT_ROOT/.env" || true

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 检查自选 cs_data 新鲜度 …" >> "$LOG"
"$PYTHON" "$PROJECT_ROOT/scripts/kss_app_bridge.py" cs-freshness notify >> "$LOG" 2>&1
echo "[$(date '+%Y-%m-%d %H:%M:%S')] selfcheck done" >> "$LOG"
