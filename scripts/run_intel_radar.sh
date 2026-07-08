#!/bin/bash
# 资讯雷达 12 赛道 RSS 缓存刷新 cron wrapper。
#
# 本脚本只调 bridge 刷新命令（相当于 app 内点「刷新」），零外部依赖。
# bridge 自身调用 kss.news.radar.fetch_radar()（纯 stdlib RSS fetcher）。
#
# cron 部署（交易日 9:00 + 13:00）:
#   0 9,13 * * 1-5 /Users/zcdeng/projects/KSS/scripts/run_intel_radar.sh
#
# 手动测试:
#   bash scripts/run_intel_radar.sh

set -e
set -o pipefail

PROJECT_ROOT="/Users/zcdeng/projects/KSS"
PYTHON="/opt/homebrew/opt/python@3.11/bin/python3.11"

echo "===== $(date '+%Y-%m-%d %H:%M:%S') intel_radar 刷新开始 ====="

cd "$PROJECT_ROOT"
exec "$PYTHON" -c "
from kss.news.radar import fetch_radar
import sys
try:
    data = fetch_radar()
    n = len(data['industries'])
    total = sum(len(ind['items']) for ind in data['industries'])
    print(f'[intel-radar] {n} 赛道 / {total} 条资讯 / 失败 {data[\"stats\"][\"failed_sources\"]} 源')
except Exception as e:
    print(f'[intel-radar] 刷新失败: {e}', file=sys.stderr)
    sys.exit(1)
"
