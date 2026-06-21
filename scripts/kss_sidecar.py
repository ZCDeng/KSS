#!/usr/bin/env python3
"""U5：常驻 Python sidecar —— 取代 subprocess-per-call。

asyncio Unix domain socket 服务，import kss_app_bridge 的 handler（零逻辑 fork），
每连接处理一条 `{"cmd","args"}` 请求 → 一条响应：
  成功 `{"code":0,"stdout":"<envelope json>"}`（stdout 与 subprocess 输出逐字一致）
  失败 `{"code":1,"stderr":"<msg>"}`
pandas 等只在 daemon 启动 import 一次；socket 0700；SIGHUP 重启自身以重载改动的 Python。
Swift 端 socket 不应答(3s)时回退 subprocess。
"""
from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
for p in (str(_REPO), str(_REPO / "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)

import kss_app_bridge as bridge  # noqa: E402

SOCKET_PATH = bridge.STATE_ROOT / "run" / "kss-sidecar.sock"


def _handle_request(line: bytes) -> str:
    try:
        req = json.loads(line)
        cmd = req["cmd"]
        args = [str(a) for a in (req.get("args") or [])]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        return json.dumps({"code": 1, "stderr": f"bad request: {exc}"})
    try:
        payload = bridge.dispatch(cmd, args)
        return json.dumps({"code": 0, "stdout": bridge._envelope_json(payload)})
    except (ValueError, SystemExit) as exc:           # 参数错误 / 护栏：业务失败，非 daemon 崩
        return json.dumps({"code": 1, "stderr": str(exc)})
    except Exception as exc:                            # 意外异常：隔离，daemon 存活
        return json.dumps({"code": 1, "stderr": f"{type(exc).__name__}: {exc}"})


async def _on_connection(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        line = await reader.readline()
        if line:
            resp = _handle_request(line)
            writer.write((resp + "\n").encode("utf-8"))
            await writer.drain()
    except (ConnectionError, asyncio.IncompleteReadError):
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def _serve() -> None:
    SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True)
    if SOCKET_PATH.exists():
        SOCKET_PATH.unlink()
    server = await asyncio.start_unix_server(_on_connection, path=str(SOCKET_PATH))
    os.chmod(SOCKET_PATH, 0o700)
    # PID 文件供 U9 运行时更新后 SIGHUP 重载。
    (SOCKET_PATH.parent / "kss-sidecar.pid").write_text(str(os.getpid()))

    # SIGHUP → exec 自身：重载改动的 Python（保住「改 Python 不重编」DX）。
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(
        signal.SIGHUP,
        lambda: os.execv(sys.executable, [sys.executable, os.path.abspath(__file__)]),
    )
    # SIGTERM/SIGINT → 干净退出。
    stop = loop.create_future()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: stop.done() or stop.set_result(None))

    async with server:
        await stop
    if SOCKET_PATH.exists():
        SOCKET_PATH.unlink()


if __name__ == "__main__":
    asyncio.run(_serve())
