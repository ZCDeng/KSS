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
import logging
import os
import signal
import sys
from pathlib import Path
from uuid import uuid4

_REPO = Path(__file__).resolve().parents[1]
for p in (str(_REPO), str(_REPO / "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)

import kss_app_bridge as bridge  # noqa: E402

logger = logging.getLogger(__name__)

SOCKET_PATH = bridge.STATE_ROOT / "run" / "kss-sidecar.sock"
PID_PATH = SOCKET_PATH.parent / "kss-sidecar.pid"
VERSION_PATH = SOCKET_PATH.parent / "kss-sidecar.version"

# ---------------------------------------------------------------------------
# U3(plan 004)：chat-turn 长连 handler + 并发 reader 任务 = 写执行唯一点。
# 安全核心(KTD-4):写 dispatch 只在本文件的 reader/handler 路径,kss_chat_loop 不含写执行。
# ---------------------------------------------------------------------------

# 总开关(KTD-4):启动读一次,运行中不重读(防中途翻转)。独立于 kss_mcp._LIVE。
_CHAT_LOOP_LIVE = os.environ.get("KSS_APP_LIVE") == "1"
# 单个 confirm 等待人工 tap 的上限;超时即按拒(KTD-6 B3,防 loop await 挂死)。
_CONFIRM_TIMEOUT = 300.0


def _execute_write(command: str, args: list[str]) -> dict:
    """写执行(reader/auto 路径共用)。先查总开关,关则拒;dispatch 异常隔离为业务错误。"""
    if not _CHAT_LOOP_LIVE:
        return {"error": "not_live",
                "hint": "KSS_APP_LIVE!=1,写操作全拒(_CHAT_LOOP_LIVE 总开关关闭)"}
    try:
        payload = bridge.dispatch(command, args)
        return {"ok": True, "command": command, "result": payload}
    except SystemExit as exc:
        return {"error": "rejected", "detail": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"error": "write_failed", "detail": f"{type(exc).__name__}: {exc}"}


def _reject_all(pending: dict, result: dict) -> None:
    """断连/SIGHUP/收尾:把所有未决 confirm 按拒收尾,防 loop 的 await 永挂(KTD-3/F3)。"""
    for entry in list(pending.values()):
        fut = entry["future"]
        if not fut.done():
            fut.set_result(dict(result))
    pending.clear()


async def _confirm_reader(reader: asyncio.StreamReader, pending: dict) -> None:
    """并发 reader 任务:收 chat-turn-confirm{call_id,approved},是 confirm 处理+写执行的唯一点。
    StreamReader/StreamWriter 是同 fd 独立两半,与 emit 写并发无 fd 争用 → 无 Gap1 死锁。"""
    while True:
        try:
            line = await reader.readline()
        except (ConnectionError, asyncio.IncompleteReadError):
            line = b""
        if not line:                       # EOF / 断连
            _reject_all(pending, {"error": "disconnected", "hint": "连接中断,写按拒收尾"})
            return
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(msg, dict) or msg.get("cmd") != "chat-turn-confirm":
            continue
        call_id = msg.get("call_id")
        approved = bool(msg.get("approved"))
        entry = pending.pop(call_id, None)   # 单用途:取出即删,杜绝重放/串号(F1/B2)
        if entry is None:
            logger.warning("[chat] 丢弃不匹配/已消费 confirm call_id=%r", call_id)
            continue
        fut = entry["future"]
        if fut.done():                       # 幂等:重复 approved 忽略
            continue
        if approved:
            result = await asyncio.to_thread(_execute_write, entry["command"], entry["args"])
        else:
            result = {"error": "denied", "hint": "用户拒绝该写操作"}
        if not fut.done():
            fut.set_result(result)


async def _handle_chat_turn(reader: asyncio.StreamReader,
                            writer: asyncio.StreamWriter, req: dict) -> None:
    """长连聊天一轮:spawn loop 任务 + reader 任务(解 Gap1 死锁);emit 逐帧 drain。"""
    import kss_chat_loop as chat_loop  # 惰性 import(sidecar→chat_loop 单向,KTD-2 红线)

    pending: dict[str, dict] = {}
    write_lock = asyncio.Lock()              # 串行化 writer,emit 与帧不交错

    async def emit(ev: dict) -> None:
        async with write_lock:
            writer.write((json.dumps(ev, ensure_ascii=False) + "\n").encode("utf-8"))
            await writer.drain()             # 每帧 drain(KTD-3 Gap2)

    async def request_write(*, command: str, args: list, tool_name: str, tool_args: dict) -> dict:
        """loop 发来的写意图。auto 任务直接执行(reader/handler 侧),否则 emit confirm 等人工 tap。
        loop 只 await 本协程结果,不持 Future、不调 dispatch(KTD-4)。"""
        if chat_loop.is_auto_task(command, args):
            return await asyncio.to_thread(_execute_write, command, args)   # AUTO 免确认
        call_id = uuid4().hex                 # handler 生成,loop 不能选值(F1)
        fut = asyncio.get_running_loop().create_future()
        pending[call_id] = {"future": fut, "command": command, "args": args}
        await emit({"type": "confirm_required", "call_id": call_id,
                    "tool": tool_name, "command": command, "args": tool_args,
                    "argsText": json.dumps(tool_args, ensure_ascii=False),   # Swift modal 直接显
                    "effect": chat_loop.write_effect_label(command, args)})   # 人话效果(U5)
        try:
            return await asyncio.wait_for(fut, timeout=_CONFIRM_TIMEOUT)
        except asyncio.TimeoutError:          # 超时即拒(B3),删条目防后到 approved 复用
            pending.pop(call_id, None)
            return {"error": "confirm_timeout", "hint": "确认超时,写按拒"}

    raw = req.get("messages") if isinstance(req, dict) else None
    messages = _prepare_messages(raw)

    reader_task = asyncio.create_task(_confirm_reader(reader, pending))
    try:
        await chat_loop.run_turn(messages, emit, request_write)
    except Exception as exc:  # noqa: BLE001  loop 意外异常 → error 帧,daemon 存活
        logger.warning("[chat] run_turn 异常: %s", exc)
        try:
            await emit({"type": "error", "error": f"{type(exc).__name__}: {exc}"})
        except Exception:  # noqa: BLE001
            pass
    finally:
        reader_task.cancel()
        _reject_all(pending, {"error": "turn_ended"})


def _prepare_messages(raw) -> list[dict]:
    """净化 user 输入(R8),其余角色原样。system prompt 由 run_turn 注入(U6)。"""
    from kss.llm.chat_client import sanitize_user_text
    out: list[dict] = []
    for m in (raw or []):
        if not isinstance(m, dict):
            continue
        if m.get("role") == "user":
            out.append({"role": "user", "content": sanitize_user_text(m.get("content", ""))})
        else:
            out.append(m)
    return out


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
        if not line:
            return
        cmd = None
        try:
            req = json.loads(line)
            cmd = req.get("cmd") if isinstance(req, dict) else None
        except json.JSONDecodeError:
            req = None
        if cmd == "chat-turn":               # 长连聊天:不在此 close,handler 跑完为止(KTD-3)
            await _handle_chat_turn(reader, writer, req)
            return
        # legacy 一次性命令:单 readline→单 write→close(保原路不回归)
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
    PID_PATH.write_text(str(os.getpid()))
    # U10：启动时把自身代码版本指纹持久化，Swift 端可快速比对陈旧进程。
    try:
        VERSION_PATH.write_text(bridge._sidecar_version_fingerprint())
    except Exception as exc:  # noqa: BLE001
        logger.warning("[version] 无法写版本文件: %s", exc)

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
    # 清理：socket/pid/version 文件一起移除，避免 orphan 文件。
    for p in (SOCKET_PATH, PID_PATH, VERSION_PATH):
        if p.exists():
            p.unlink()


if __name__ == "__main__":
    asyncio.run(_serve())
