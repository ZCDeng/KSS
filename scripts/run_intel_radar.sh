#!/bin/bash
# 资讯雷达 12 赛道 RSS 缓存刷新 + yupi 旁路合并 + Top-K 投研改写 worker。
#
# 1) RSS fetch（stdlib）
# 2) yupi 旁路 ingest/merge（常驻 http://127.0.0.1:3001；失败不拖垮 RSS）
# 3) rewrite worker（KSS_SKIP_REWRITE=1 可跳过）
#
# 依赖：可选常驻 yupi-hot-monitor（OpenRouter 等见其 .env）。无 yupi 时仍刷新 RSS。
# 部署：kss/config/cron_jobs.yaml（盘前+盘后两窗）+ scripts/sync_launchd.py
#
# 手动测试:
#   bash scripts/run_intel_radar.sh
#   KSS_SKIP_YUPI=1 bash scripts/run_intel_radar.sh

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

# 注意：不要用 exec —— fetch 后还要跑 yupi / rewrite
"$PYTHON" -c "
from kss.news.radar import fetch_radar
import os, sys
try:
    data = fetch_radar()
    n = len(data['industries'])
    total = sum(len(ind['items']) for ind in data['industries'])
    print(f'[intel-radar] RSS {n} 赛道 / {total} 条 / 失败 {data[\"stats\"].get(\"failed_sources\", \"?\")} 源')
except Exception as e:
    print(f'[intel-radar] RSS 刷新失败: {e}', file=sys.stderr)
    sys.exit(1)

if os.environ.get('KSS_SKIP_YUPI', '0') == '1':
    print('[intel-yupi] skipped (KSS_SKIP_YUPI=1)')
else:
    try:
        from kss.news.yupi_ingest import ingest_and_merge
        data = ingest_and_merge(data=data)
        y = (data.get('stats') or {}).get('yupi') or {}
        print(f\"[intel-yupi] ok={y.get('ok')} skipped={y.get('skipped')} reason={y.get('reason', '')!r} items={y.get('items', 0)}\")
    except Exception as e:
        print(f'[intel-yupi] merge error (ignored for exit): {e}')
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
