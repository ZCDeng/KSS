#!/bin/bash
# 科创板多指标共振扫描定时任务
# 部署：kss/config/cron_jobs.yaml 清单条目 + scripts/sync_launchd.py（不再手动 crontab -e）。
# 每周一至周五 15:30 运行（收盘后）

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_FILE="$PROJECT_DIR/storage/logs/cron/scanner.log"

if [ -n "${KSS_PYTHON:-}" ]; then
    PYTHON_BIN="$KSS_PYTHON"
elif [ -x "$HOME/Library/Application Support/KSS/venv/bin/python3" ]; then
    PYTHON_BIN="$HOME/Library/Application Support/KSS/venv/bin/python3"
elif [ -x "$PROJECT_DIR/.venv-desktop/bin/python" ]; then
    PYTHON_BIN="$PROJECT_DIR/.venv-desktop/bin/python"
else
    echo "no usable python interpreter found (checked KSS_PYTHON, state-root venv, .venv-desktop)" >&2
    exit 1
fi

# 环境变量：Tushare token
# ~/.tushare_token 缺失也不报错，TushareClient 内部会再尝试其它路径与环境变量
export TUSHARE_TOKEN="$(cat "$HOME/.tushare_token" 2>/dev/null || echo "")"

# 切换工作目录（monitor.py 依赖 cwd 下 best_params.json 与 cs_data/）
cd "$PROJECT_DIR"
mkdir -p "$(dirname "$LOG_FILE")"

# 运行扫描。默认分析 688322 与 688017；如需扩列在此追加股票代码。
# monitor.py 在分析失败时以非零退出码结束，配合 set -e 让 cron 真正"知道"出了事。
"$PYTHON_BIN" "$PROJECT_DIR/monitor.py" 688322 688017 >> "$LOG_FILE" 2>&1

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 扫描完成" >> "$LOG_FILE"
