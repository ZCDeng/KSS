#!/bin/zsh
# ---------------------------------------------------------------------------
# 开机/登录自检：补跑因关机漏跑的 launchd 任务。
#
# 由 com.zcdeng.kss.selfcheck（RunAtLoad=true）在每次登录后触发。
# 登录瞬间其它 LaunchAgent 可能尚未 bootstrap 完成，先睡 90s 让其就绪，
# 再调 bridge cron-catchup —— 只 kickstart「应跑未跑」且启用的任务，
# selfcheck 自身永不参与，停用任务跳过。判定逻辑与应用内「一键补跑」完全一致。
# ---------------------------------------------------------------------------
set -u

REPO="/Users/zcdeng/projects/KSS"
LOG="$REPO/storage/logs/cron/selfcheck.log"

cd "$REPO" || exit 1
mkdir -p "$REPO/storage/logs/cron"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] selfcheck wake, sleep 90s 等待其它 agent bootstrap" >> "$LOG"
sleep 90

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 运行 cron-catchup …" >> "$LOG"
/usr/bin/python3 "$REPO/scripts/kss_app_bridge.py" cron-catchup >> "$LOG" 2>&1
echo "[$(date '+%Y-%m-%d %H:%M:%S')] selfcheck done" >> "$LOG"
