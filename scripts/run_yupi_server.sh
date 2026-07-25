#!/bin/bash
# KSS 托管 yupi-hot-monitor（launchd KeepAlive 用，前台 node）。
# 不在此脚本里 detach：launchd 负责重启。
#
# 端口纪律：启动前 reclaim 占用 18765 的孤儿进程，避免
# ensure(already_healthy) 后仍 exec 第二实例 → EADDRINUSE 崩溃循环。
set -e
set -o pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export KSS_STATE_ROOT="${KSS_STATE_ROOT:-$HOME/Library/Application Support/KSS}"

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

cd "$PROJECT_ROOT"
exec "$PYTHON" - <<'PY'
import os
import sys

from kss.news.yupi_runtime import (
    ensure,
    port,
    reclaim_port,
    resolve_model,
    resolve_openrouter_key,
    server_dir,
)

# 安装/构建；不后台起第二进程
r = ensure(start=False)
print(
    "[yupi] install:",
    {k: r.get(k) for k in ("ok", "base_url", "port", "model", "action", "error", "has_openrouter_key")},
    flush=True,
)
if not r.get("ok"):
    sys.exit(1)

from kss.news.yupi_runtime import _server_entry

entry = _server_entry()
if entry is None:
    print("[yupi] missing server entry after install", file=sys.stderr)
    sys.exit(1)
argv, kind = entry

# launchd 前台 job 必须独占端口：清掉 Popen 孤儿 / 旧实例
killed = reclaim_port()
if killed:
    print(f"[yupi] reclaimed port {port()} from pids={killed}", flush=True)

env = os.environ.copy()
env["PORT"] = str(port())
env["YUPI_AI_MODEL"] = resolve_model()
env["KSS_YUPI_MODEL"] = resolve_model()
key = resolve_openrouter_key()
if key:
    env["OPENROUTER_API_KEY"] = key

print(f"[yupi] foreground {kind} PORT={env['PORT']} model={env['YUPI_AI_MODEL']}", flush=True)
os.chdir(server_dir())
os.execve(argv[0], argv, env)
PY
