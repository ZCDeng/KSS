#!/bin/bash
# Daily run: update data, scan signals, generate report, send notification
set -e

# 定位到项目根目录
cd "$(dirname "$0")/.."

# 激活虚拟环境
source .venv/bin/activate

# 确保报告目录存在
mkdir -p ./reports

TODAY=$(date +%Y%m%d)
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

echo "=========================================="
echo "KSS Daily Run - ${TODAY}"
echo "=========================================="

# 1. 更新数据
echo "[1/4] Updating data..."
kss update --pool kcb50

# 2. 扫描信号
echo "[2/4] Scanning signals..."
kss scan --pool kcb50

# 3. 生成日度报告
echo "[3/4] Generating daily report..."
REPORT_PATH="./reports/daily_${TODAY}.md"
kss report --type daily --output "${REPORT_PATH}"

# 4. 发送通知（若配置）
echo "[4/4] Sending notification..."
kss notify --title "KSS Daily ${TODAY}" --message "Daily report generated: ${REPORT_PATH}" || true

echo ""
echo "Daily run completed successfully."
echo "Report saved to: ${REPORT_PATH}"
