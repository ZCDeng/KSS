#!/bin/bash
# 资讯雷达 12 赛道 RSS 缓存刷新 cron wrapper + Top-K 投研改写 worker。
#
# 本脚本只调 bridge 刷新命令（相当于 app 内点「刷新」），零外部依赖。
# bridge 自身调用 kss.news.radar.fetch_radar()（纯 stdlib RSS fetcher）。
# 刷新成功后串行跑 rewrite worker（KSS_SKIP_REWRITE=1 可跳过）。
#
# 部署：kss/config/cron_jobs.yaml 清单条目 + scripts/sync_launchd.py（不再手动 crontab -e）。
#
# 手动测试:
#   bash scripts/run_intel_radar.sh

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

echo "===== $(date '+%Y-%m-%d %H:%M:%S') intel_radar 刷新开始 ====="

cd "$PROJECT_ROOT"

# 注意：不要用 exec —— fetch 后还要跑 rewrite worker（plan 2026-07-10-001 U3）
"$PYTHON" -c "
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

if [ "${KSS_SKIP_REWRITE:-0}" = "1" ]; then
  echo "[intel-rewrite] skipped (KSS_SKIP_REWRITE=1)"
  echo "===== $(date '+%Y-%m-%d %H:%M:%S') intel_radar 完成 ====="
  exit 0
fi

# worker 失败不拖垮 radar 成功退出码
set +e
"$PYTHON" -c "
from kss.news.rewrite_worker import run_top_k_rewrites
try:
    s = run_top_k_rewrites()
    print(
        f\"[intel-rewrite] tracks={s.get('tracks')} attempted={s.get('attempted')} \"
        f\"ready_new={s.get('ready_new')} failed={s.get('failed')} stopped={s.get('stopped_reason')}\"
    )
except Exception as e:
    print(f'[intel-rewrite] worker error (ignored for exit): {e}')
"
set -e

echo "===== $(date '+%Y-%m-%d %H:%M:%S') intel_radar 完成 ====="
