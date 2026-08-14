#!/usr/bin/env bash
# kss-mcp 启动 wrapper —— 供 .mcp.json 注册,任何 clone 此 repo 的 Claude Code / MCP client
# 都能起同一套 KSS 只读业务工具（pack mcpVisible 投影；无 bash/fs/terminal/live 写）。
#
# 可移植:PROJECT_ROOT 由脚本自身位置推导(不硬编码用户路径);解释器按优先级挑**第一个装了
# fastmcp 的** venv(fastmcp 已在 pyproject,`uv sync` 后任一 venv 均可)。
#
# 手动测试:bash scripts/run_kss_mcp.sh   # 应进入 stdio MCP 循环(无输出,等 client)

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

# 候选解释器(优先 in-repo venv → state-root bundle venv → .venv-desktop → 系统)。
CANDIDATES=(
  "${KSS_PYTHON:-}"
  "$PROJECT_ROOT/venv/bin/python3"
  "$PROJECT_ROOT/.venv/bin/python"
  "$HOME/Library/Application Support/KSS/venv/bin/python3"
  "$PROJECT_ROOT/.venv-desktop/bin/python"
  "$(command -v python3 || true)"
)

PYTHON=""
for c in "${CANDIDATES[@]}"; do
  [ -n "$c" ] && [ -x "$c" ] || continue
  if "$c" -c "import fastmcp" >/dev/null 2>&1; then
    PYTHON="$c"; break
  fi
done

if [ -z "$PYTHON" ]; then
  echo "run_kss_mcp: 找不到装了 fastmcp 的 python;请先 'uv sync'(fastmcp 已在 pyproject)" >&2
  exit 1
fi

export KSS_PROJECT_ROOT="${KSS_PROJECT_ROOT:-$PROJECT_ROOT}"
exec "$PYTHON" "$PROJECT_ROOT/scripts/kss_mcp.py"
