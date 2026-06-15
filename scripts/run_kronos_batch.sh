#!/bin/bash
# Kronos 离线批处理推理 - 周度 cron wrapper（不进每日实盘关键路径）.
#
# 包装目的（同 run_paper_trade_daily.sh）：
#   1. cron 不读 zshrc → 用 Homebrew Python 绝对路径
#   2. 失败返回非零让 cron 系统监控接管
#
# 手动测试：
#   bash scripts/run_kronos_batch.sh --artifact storage/kronos/artifacts/freeze_2026-02-13
#
# cron 部署（每周日 20:00，离线、与 log_mv 零连接）：
#   0 20 * * 0 /Users/zcdeng/projects/KSS/scripts/run_kronos_batch.sh --artifact <冻结目录> >> /Users/zcdeng/projects/KSS/storage/logs/cron/kronos_batch.log 2>&1

set -e
set -o pipefail

PROJECT_ROOT="/Users/zcdeng/projects/KSS"
PYTHON="/opt/homebrew/opt/python@3.11/bin/python3.11"

echo "===== $(date '+%Y-%m-%d %H:%M:%S') kronos_batch 开始 ====="

cd "$PROJECT_ROOT"
exec "$PYTHON" scripts/kronos_batch_infer.py "$@"
