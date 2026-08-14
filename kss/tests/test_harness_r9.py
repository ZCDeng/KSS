"""U8 / R9：三表面各完成一次真实 KSS 任务；生产不再把 Python loop 当主人。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "scripts"))

import kss_app_bridge as bridge  # noqa: E402
import kss_chat_loop as chat_loop  # noqa: E402
import kss_sidecar as sidecar  # noqa: E402
from kss.agent.harness_kernel import (  # noqa: E402
    HarnessKernel,
    ensure_harness_kernel,
    stop_harness_kernel,
)
from kss.agent.service import KSSAgentService  # noqa: E402
from kss.research.harness_driver import ResearchHarnessDriver  # noqa: E402
from kss.research.runner import AgentResearchTaskRunner  # noqa: E402


@pytest.fixture
def kernel():
    stop_harness_kernel()
    k = HarnessKernel(driver="scripted")
    hello = k.start()
    assert hello.get("type") == "hello"
    assert hello.get("driver") == "scripted"
    try:
        yield k
    finally:
        k.close()
        stop_harness_kernel()


def test_python_loop_is_not_constructed_until_debug_run_turn(tmp_path: Path) -> None:
    service = KSSAgentService(tmp_path, tmp_path)
    assert service._runtime is None
    assert sidecar._handle_chat_turn.__doc__ is None or "Harness" in (sidecar._handle_chat_turn.__doc__ or "")
    src = Path(sidecar.__file__).read_text(encoding="utf-8")
    assert "await chat_loop.run_turn" not in src


def test_r9_desktop_orientation_on_node_kernel(kernel: HarnessKernel, monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    from kss.agent.desktop_host import DesktopHarnessHost, DesktopTurnRequest

    monkeypatch.setattr(sidecar, "_CHAT_LOOP_LIVE", True)
    host = DesktopHarnessHost(
        session=kernel.desktop_session(),
        grant_write=sidecar.grant_harness_write,
        revoke_grant=sidecar.revoke_harness_write,
    )
    host.execute_tool = lambda **kw: sidecar.execute_harness_tool(**kw)
    events: list[dict] = []

    async def emit(event):
        events.append(event)

    async def go():
        host.emit = emit
        return await host.run(
            DesktopTurnRequest(
                session_id="r9",
                client_turn_id="c1",
                input="盘面定向",
                run_id="run-r9",
            ),
            emit,
        )

    result = asyncio.run(go())
    assert result.status == "completed", result
    assert result.tool_results
    first = result.tool_results[0]
    assert isinstance(first, dict)
    assert first.get("ok") is True or first.get("command") == "orientation" or "orientation" in json.dumps(first)


def test_r9_research_node_writes_workspace_and_overlay(
    kernel: HarnessKernel, tmp_path: Path
) -> None:
    driver = ResearchHarnessDriver(
        state_root=tmp_path,
        project_root=_ROOT,
        session=kernel.research_session(),
    )
    runner = AgentResearchTaskRunner(
        state_root=tmp_path,
        project_root=_ROOT,
        driver=driver,
    )
    out = runner.run(
        goal={"goal_id": "g-r9", "origin": "manual"},
        task={
            "task_id": "t-r9",
            "title": "定向",
            "kind": "analysis",
            "payload": {},
        },
        attempt_id="a-r9",
        dependency_summaries=[],
    )
    assert out.get("status") == "succeeded", out
    assert out.get("harness_status") == "completed"
    workspace = Path(str(out.get("workspace") or ""))
    assert workspace.is_dir()
    assert (workspace / "notes.md").is_file()
    assert workspace.resolve() != _ROOT.resolve()


def test_r9_mcp_lists_and_calls_read_only_orientation(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib
    import types

    class FakeFastMCP:
        def __init__(self, name: str):
            self.name = name
            self.tools: list[str] = []
            self.fns: dict[str, object] = {}

        def tool(self, fn=None, **kwargs):
            def deco(f):
                name = str(kwargs.get("name") or f.__name__)
                self.tools.append(name)
                self.fns[name] = f
                return f

            if callable(fn):
                return deco(fn)
            return deco

        def run(self):
            raise AssertionError("test should not run MCP server")

    fake = FakeFastMCP("kss")
    mod = types.ModuleType("fastmcp")
    mod.FastMCP = lambda name: fake
    monkeypatch.setitem(sys.modules, "fastmcp", mod)
    monkeypatch.setenv("KSS_MCP_LIVE", "0")
    sys.modules.pop("kss_mcp", None)
    kss_mcp = importlib.import_module("kss_mcp")
    assert "get_orientation" in fake.tools
    assert "run_task" not in fake.tools
    fn = fake.fns["get_orientation"]
    result = fn()
    payload = result if isinstance(result, dict) else json.loads(result)
    assert payload.get("error") not in {"not_live_write", "write_blocked"}
    assert "orientation" in json.dumps(payload, ensure_ascii=False) or payload


def test_legacy_chat_turn_does_not_call_python_run_turn(
    monkeypatch: pytest.MonkeyPatch, kernel: HarnessKernel
) -> None:
    import asyncio

    from kss.agent.desktop_host import DesktopHarnessHost

    called: list[bool] = []

    async def boom(*args, **kwargs):
        called.append(True)
        raise AssertionError("kss_chat_loop.run_turn must not own production chat")

    monkeypatch.setattr(chat_loop, "run_turn", boom)
    host = DesktopHarnessHost(
        session=kernel.desktop_session(),
        grant_write=sidecar.grant_harness_write,
        revoke_grant=sidecar.revoke_harness_write,
    )
    host.execute_tool = lambda **kw: sidecar.execute_harness_tool(**kw)
    monkeypatch.setattr(sidecar, "_desktop_harness_host", lambda: host)

    async def go():
        reader, writer = asyncio.StreamReader(), _Writer()
        await sidecar._handle_chat_turn(
            reader, writer, {"messages": [{"role": "user", "content": "盘面"}]}
        )
        return writer.frames()

    frames = asyncio.run(go())
    assert called == []
    assert any(f.get("type") in {"chunk", "message_delta", "done", "agent_end"} for f in frames)


class _Writer:
    def __init__(self):
        self.buf: list[bytes] = []

    def write(self, b):
        self.buf.append(b)

    async def drain(self):
        return None

    def frames(self):
        out = []
        for b in self.buf:
            for ln in b.decode("utf-8").splitlines():
                if ln.strip():
                    out.append(json.loads(ln))
        return out
