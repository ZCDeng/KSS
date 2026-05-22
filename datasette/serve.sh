#!/usr/bin/env bash
# 启动 KSS datasette + agent。
# 首次跑前：
#   uv venv && source .venv/bin/activate
#   uv pip install datasette datasette-llm datasette-agent pandas pyarrow llm-anthropic
#   uv pip install -e ./plugins/datasette_kss_tushare
#   python build_db.py
#
# 然后访问 http://127.0.0.1:8001/-/agent

set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -f kss.db ]]; then
  echo "kss.db not found, building..."
  python build_db.py
fi

export TUSHARE_TOKEN="${TUSHARE_TOKEN:-}"

exec datasette serve kss.db \
  --metadata metadata.yml \
  --root \
  --secret "kss-local-dev" \
  --setting sql_time_limit_ms 10000 \
  --setting max_returned_rows 5000 \
  --host 127.0.0.1 \
  --port 8001
