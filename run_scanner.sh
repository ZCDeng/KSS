#!/bin/bash
# 科创板多指标共振扫描定时任务
# 安装：``crontab -e`` 后加入：
#   30 15 * * 1-5  /Users/zcdeng/projects/KSS/run_scanner.sh
# 每周一至周五 15:30 运行（收盘后）

set -euo pipefail

PROJECT_DIR="/Users/zcdeng/projects/KSS"
PYTHON_BIN="/opt/homebrew/bin/python3.11"
LOG_FILE="$PROJECT_DIR/storage/logs/cron/scanner.log"
mkdir -p "$(dirname "$LOG_FILE")"

# 环境变量：Tushare token
# ~/.tushare_token 缺失也不报错，TushareClient 内部会再尝试其它路径与环境变量
export TUSHARE_TOKEN="$(cat "$HOME/.tushare_token" 2>/dev/null || echo "")"

# 切换工作目录（monitor.py 依赖 cwd 下 best_params.json 与 cs_data/）
cd "$PROJECT_DIR"

# 运行扫描。默认分析 688322 与 688017；如需扩列在此追加股票代码。
# monitor.py 在分析失败时以非零退出码结束，配合 set -e 让 cron 真正"知道"出了事。
"$PYTHON_BIN" "$PROJECT_DIR/monitor.py" 688322 688017 >> "$LOG_FILE" 2>&1

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 扫描完成" >> "$LOG_FILE"
