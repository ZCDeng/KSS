"""Live Harness 回合：agents.create + followup + whenIdle；CI 用 stub LLM，不碰真密钥。"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "scripts"))

from kss.agent.harness_kernel import (  # noqa: E402
    HarnessKernel,
    _agent_options_payload,
    _map_dsh_provider,
    prepare_dsh_home,
    stop_harness_kernel,
)

_LIVE = _ROOT / "scripts" / "kss_harness_live.mjs"
_SECRET = "secret-live-key-do-not-log"


def _node_eval(source: str, *, timeout: float = 30) -> dict:
    result = subprocess.run(
        ["node", "--input-type=module", "-e", source],
        cwd=str(_ROOT),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(f"node eval failed:\n{result.stdout}\n{result.stderr}")
    line = result.stdout.strip().splitlines()[-1]
    return json.loads(line)


def test_project_session_event_maps_assistant_chunks() -> None:
    href = _LIVE.resolve().as_posix()
    out = _node_eval(
        f"""
import {{ projectSessionEvent }} from 'file://{href}';
const chunk = projectSessionEvent({{
  type: 'assistant/chunk',
  seq: 1,
  time: 0,
  data: {{ chunk: {{ type: 'text-delta', index: 0, text: '盘面' }} }},
}});
const ignore = projectSessionEvent({{
  type: 'turn/start',
  seq: 0,
  time: 0,
  data: {{ turn: 1 }},
}});
console.log(JSON.stringify({{ chunk, ignore }}));
"""
    )
    assert out["chunk"] == {
        "type": "message_delta",
        "text": "盘面",
        "delta": "盘面",
        "origin": "text-delta",
        "content_index": 0,
    }
    assert out["ignore"] is None


def test_project_session_event_maps_text_chunks() -> None:
    href = _LIVE.resolve().as_posix()
    out = _node_eval(
        f"""
import {{ projectSessionEvent, shouldResumeCreateError }} from 'file://{href}';
const chunk = projectSessionEvent({{
  type: 'text-chunks',
  seq: 2,
  time: 0,
  data: {{ texts: ['我来', '帮你', '上手'] }},
}});
console.log(JSON.stringify({{
  chunk,
  resumeDisk: shouldResumeCreateError(
    new Error('session "s" already has a persisted log on disk; load/resume it instead of creating')
  ),
  resumeExists: shouldResumeCreateError(new Error('session "s" already exists')),
  other: shouldResumeCreateError(new Error('boom')),
}}));
"""
    )
    assert out["chunk"] == {
        "type": "message_delta", "text": "我来帮你上手", "delta": "我来帮你上手",
        "origin": "text-chunks",
    }
    assert out["resumeDisk"] is True
    assert out["resumeExists"] is True
    assert out["other"] is False


def test_project_session_event_maps_reasoning_deltas() -> None:
    href = _LIVE.resolve().as_posix()
    out = _node_eval(
        f"""
import {{ projectSessionEvent }} from 'file://{href}';
const delta = projectSessionEvent({{
  type: 'assistant/chunk',
  seq: 1,
  time: 0,
  data: {{ chunk: {{ type: 'reasoning-delta', index: 0, text: '先核对板块' }} }},
}});
const packed = projectSessionEvent({{
  type: 'reasoning-chunks',
  seq: 2,
  time: 0,
  data: {{ index: 0, texts: ['再核对', '数字'] }},
}});
const empty = projectSessionEvent({{
  type: 'assistant/chunk',
  data: {{ chunk: {{ type: 'reasoning-delta', index: 0, text: '' }} }},
}});
console.log(JSON.stringify({{ delta, packed, empty }}));
"""
    )
    assert out["delta"] == {
        "type": "thinking_delta",
        "text": "先核对板块",
        "delta": "先核对板块",
        "content_index": 0,
    }
    assert out["packed"] == {
        "type": "thinking_delta",
        "text": "再核对数字",
        "delta": "再核对数字",
        "content_index": 0,
    }
    assert out["empty"] is None


def test_project_session_event_maps_tool_calls() -> None:
    href = _LIVE.resolve().as_posix()
    out = _node_eval(
        f"""
import {{ projectSessionEvent }} from 'file://{href}';
const start = projectSessionEvent({{
  type: 'tool/call',
  seq: 3,
  time: 0,
  data: {{ name: 'get_orientation', callId: 'c1', arguments: '{{}}' }},
}});
const end = projectSessionEvent({{
  type: 'tool/result',
  seq: 4,
  time: 0,
  data: {{ name: 'get_orientation' }},
}});
console.log(JSON.stringify({{ start, end }}));
"""
    )
    assert out["start"] == {"type": "tool_start", "name": "get_orientation", "tool": "get_orientation"}
    assert out["end"] == {"type": "tool_end", "name": "get_orientation", "tool": "get_orientation"}


def test_run_live_turn_uses_followup_and_when_idle() -> None:
    href = _LIVE.resolve().as_posix()
    out = _node_eval(
        f"""
import {{ runLiveTurn }} from 'file://{href}';

const agent = {{
  followed: null,
  session: {{ id: 'live-s1', header: {{ id: 'live-s1' }} }},
  followup(message) {{ this.followed = message; }},
  async whenIdle() {{
    if (!this.followed) return;
    this._emit({{
      type: 'assistant/chunk',
      data: {{ chunk: {{ type: 'text-delta', index: 0, text: 'KSS live stub' }} }},
    }});
  }},
  cancel() {{}},
  steer() {{}},
}};
const ctx = {{
  agents: {{
    async create(opts) {{
      const agentCtx = {{
        on(name, fn) {{
          agent._emit = (event) => fn({{ id: 'live-s1' }}, event);
          return () => {{}};
        }},
      }};
      await opts?.setup?.(agentCtx);
      return {{ agent, async dispose() {{}} }};
    }},
    get() {{ return agent; }},
  }},
  on() {{ return () => {{}}; }},
}};
const deps = {{
  SessionId: (id) => id,
  createUserMessage: (input) => ({{ role: 'user', ...input }}),
  attachSessionPolicy: (ag, spec) => {{ ag.policy = spec; }},
  inheritResearchPolicy() {{}},
  resolveDesktopApproval() {{}},
  setApprovalPrompt() {{}},
}};
const result = await runLiveTurn(ctx, deps, {{
  surface: 'desktop',
  sessionId: 'live-s1',
  input: '盘面定向',
}});
console.log(JSON.stringify({{
  ok: result.ok,
  status: result.status,
  text: result.assistant_text,
  followed: Boolean(agent.followed),
  owned: agent.policy?.owned,
  types: result.events.map((e) => e.type),
}}));
"""
    )
    assert out["ok"] is True
    assert out["status"] == "completed"
    assert out["followed"] is True
    assert out["owned"] is True
    assert "KSS live stub" in out["text"]
    assert "turn_start" in out["types"]
    assert "message_delta" in out["types"]


def test_run_live_turn_forwards_reasoning_before_visible_text() -> None:
    href = _LIVE.resolve().as_posix()
    out = _node_eval(
        f"""
import {{ runLiveTurn }} from 'file://{href}';

const agent = {{
  followed: null,
  session: {{ id: 'live-think', header: {{ id: 'live-think' }} }},
  followup(message) {{ this.followed = message; }},
  async whenIdle() {{
    if (!this.followed) return;
    this._emit({{
      type: 'assistant/chunk',
      data: {{ chunk: {{ type: 'reasoning-delta', index: 0, text: '先核对' }} }},
    }});
    this._emit({{
      type: 'assistant/chunk',
      data: {{ chunk: {{ type: 'reasoning-delta', index: 0, text: '证据' }} }},
    }});
    this._emit({{
      type: 'assistant/chunk',
      data: {{ chunk: {{ type: 'text-delta', index: 1, text: '结论' }} }},
    }});
  }},
  cancel() {{}},
  steer() {{}},
}};
const ctx = {{
  agents: {{
    async create(opts) {{
      const agentCtx = {{
        on(name, fn) {{
          agent._emit = (event) => fn({{ id: 'live-think' }}, event);
          return () => {{}};
        }},
      }};
      await opts?.setup?.(agentCtx);
      return {{ agent, async dispose() {{}} }};
    }},
    get() {{ return agent; }},
  }},
  on() {{ return () => {{}}; }},
}};
const deps = {{
  SessionId: (id) => id,
  createUserMessage: (input) => ({{ role: 'user', ...input }}),
  attachSessionPolicy: (ag, spec) => {{ ag.policy = spec; }},
  inheritResearchPolicy() {{}},
  resolveDesktopApproval() {{}},
  setApprovalPrompt() {{}},
}};
const forwarded = [];
const result = await runLiveTurn(ctx, deps, {{
  surface: 'desktop',
  sessionId: 'live-think',
  input: '为什么慢',
  onEvent: (event) => forwarded.push(event.type),
}});
console.log(JSON.stringify({{
  ok: result.ok,
  text: result.assistant_text,
  types: result.events.map((e) => e.type),
  forwarded,
  thinking: result.events.filter((e) => e.type === 'thinking_delta').map((e) => e.delta),
}}));
"""
    )
    assert out["ok"] is True
    assert out["text"] == "结论"
    assert out["thinking"] == ["先核对", "证据"]
    assert out["types"][:6] == [
        "turn_start",
        "message_start",
        "thinking_start",
        "thinking_delta",
        "thinking_delta",
        "thinking_end",
    ]
    assert "message_delta" in out["types"]
    assert out["forwarded"] == out["types"]


def _live_agent_source(*, create_throws: bool = False, emit: bool = True) -> str:
    create_body = (
        "throw new Error('session \"live-s1\" already has a persisted log on disk; load/resume it instead of creating');"
        if create_throws
        else "return publish(opts);"
    )
    emit_body = (
        """
    if (!this.followed) return;
    this._emit({
      type: 'text-chunks',
      seq: 2,
      data: { texts: ['KSS live stub'] },
    });
"""
        if emit
        else "return;"
    )
    return f"""
const agent = {{
  followed: null,
  cancelled: 0,
  session: {{ id: 'live-s1', header: {{ id: 'live-s1' }}, events: [], seq: 0 }},
  followup(message) {{ this.followed = message; this.session.seq += 1; }},
  async whenIdle() {{ {emit_body} }},
  cancel() {{ this.cancelled += 1; }},
  steer() {{}},
}};
async function publish(opts) {{
  const agentCtx = {{
    on(name, fn) {{
      agent._emit = (event) => fn({{ id: 'live-s1' }}, event);
      return () => {{}};
    }},
  }};
  await opts?.setup?.(agentCtx);
  return {{ agent, async dispose() {{}} }};
}}
const ctx = {{
  agents: {{
    async create(opts) {{ {create_body} }},
    async resume(opts) {{ return publish(opts); }},
    get() {{ return agent; }},
  }},
  on() {{ return () => {{}}; }},
}};
const deps = {{
  SessionId: (id) => id,
  createUserMessage: (input) => ({{ role: 'user', ...input }}),
  attachSessionPolicy: (ag, spec) => {{ ag.policy = spec; }},
  inheritResearchPolicy() {{}},
  resolveDesktopApproval() {{}},
  setApprovalPrompt() {{}},
}};
"""


def test_run_live_turn_resumes_persisted_session() -> None:
    href = _LIVE.resolve().as_posix()
    out = _node_eval(
        f"""
import {{ runLiveTurn }} from 'file://{href}';
{_live_agent_source(create_throws=True, emit=True)}
const result = await runLiveTurn(ctx, deps, {{
  surface: 'desktop',
  sessionId: 'live-s1',
  input: '盘面定向',
}});
console.log(JSON.stringify({{
  ok: result.ok,
  status: result.status,
  text: result.assistant_text,
  followed: Boolean(agent.followed),
  cancelled: agent.cancelled,
}}));
"""
    )
    assert out["ok"] is True
    assert out["status"] == "completed"
    assert out["followed"] is True
    assert out["cancelled"] >= 1
    assert "KSS live stub" in out["text"]


def test_run_live_turn_empty_completion_fails_closed() -> None:
    href = _LIVE.resolve().as_posix()
    out = _node_eval(
        f"""
import {{ runLiveTurn }} from 'file://{href}';
{_live_agent_source(create_throws=False, emit=False)}
const result = await runLiveTurn(ctx, deps, {{
  surface: 'desktop',
  sessionId: 'live-s1',
  input: '盘面定向',
}});
console.log(JSON.stringify({{
  ok: result.ok,
  status: result.status,
  error: result.error,
  text: result.assistant_text,
  followed: Boolean(agent.followed),
}}));
"""
    )
    assert out["ok"] is False
    assert out["status"] == "unavailable"
    assert out["error"] == "empty_completion"
    assert out["text"] == ""
    assert out["followed"] is True


def test_run_live_turn_drops_duplicate_delta_stream() -> None:
    """raw text-delta 与合批 text-chunks 双流并发时正文不得翻倍。"""
    href = _LIVE.resolve().as_posix()
    out = _node_eval(
        f"""
import {{ runLiveTurn }} from 'file://{href}';
const agent = {{
  followed: null,
  session: {{ id: 'live-dup', header: {{ id: 'live-dup' }}, events: [], seq: 0 }},
  followup(message) {{ this.followed = message; this.session.seq += 1; }},
  async whenIdle() {{
    if (!this.followed) return;
    this._emit({{
      type: 'assistant/chunk',
      data: {{ chunk: {{ type: 'text-delta', index: 0, text: '大盘平稳' }} }},
    }});
    this._emit({{
      type: 'text-chunks',
      seq0: 9,
      data: {{ texts: ['大盘', '平稳'] }},
    }});
  }},
  cancel() {{}},
  steer() {{}},
}};
const ctx = {{
  agents: {{
    async create(opts) {{
      const agentCtx = {{
        on(name, fn) {{
          agent._emit = (event) => fn({{ id: 'live-dup' }}, event);
          return () => {{}};
        }},
      }};
      await opts?.setup?.(agentCtx);
      return {{ agent, async dispose() {{}} }};
    }},
    get() {{ return agent; }},
  }},
  on() {{ return () => {{}}; }},
}};
const deps = {{
  SessionId: (id) => id,
  createUserMessage: (input) => ({{ role: 'user', ...input }}),
  attachSessionPolicy: (ag, spec) => {{ ag.policy = spec; }},
  inheritResearchPolicy() {{}},
  resolveDesktopApproval() {{}},
  setApprovalPrompt() {{}},
}};
const result = await runLiveTurn(ctx, deps, {{
  surface: 'desktop',
  sessionId: 'live-dup',
  input: '今天大盘',
}});
console.log(JSON.stringify({{
  text: result.assistant_text,
  deltas: result.events.filter((e) => e.type === 'message_delta').length,
}}));
"""
    )
    assert out["text"] == "大盘平稳"
    assert out["deltas"] == 1


def test_desktop_session_streams_all_events_despite_dict_id_reuse() -> None:
    """id(event) 去重必须持引用：dict 被 GC 后地址复用曾把新事件当重复丢掉，
    表现为 UI 流式冻结、confirm_required 丢失（2026-08-16 凌晨线上事故）。"""
    import asyncio
    import time as _time

    from kss.agent.desktop_host import DesktopHarnessHost, DesktopTurnRequest
    from kss.agent.harness_kernel import NodeDesktopSession

    total = 300

    class FakeKernel:
        driver = "dsh"
        alive = True

        def request(self, cmd, payload=None, *, timeout=0, on_event=None, approval_timeout=None):
            for i in range(total):
                # 每轮新建 dict 且不保留引用；配合 sleep 让上一事件先被消费释放，
                # 逼出 CPython 的字典地址复用。
                on_event({"type": "message_delta", "text": f"t{i}", "delta": f"t{i}"})
                _time.sleep(0.001)
            return {"ok": True, "status": "completed", "assistant_text": "回合完成"}

    async def go():
        # 只计数、只存字符串副本：若保留 event dict 引用，地址不会复用，
        # 旧 bug 测不出来。
        stats = {"deltas": 0}

        async def emit(event):
            if event.get("type") == "message_delta":
                stats["deltas"] += 1

        host = DesktopHarnessHost(
            session=object(),
            grant_write=lambda *a, **k: None,
            revoke_grant=lambda *a, **k: None,
        )
        host.emit = emit
        session = NodeDesktopSession(FakeKernel())
        result = await session.run(
            DesktopTurnRequest(session_id="s1", client_turn_id="c1", input="盘面", run_id="r1"),
            host,
        )
        for _ in range(500):
            if stats["deltas"] >= total:
                break
            await asyncio.sleep(0.01)
        assert stats["deltas"] == total, f"事件被丢弃：{stats['deltas']}/{total}"
        assert result.status == "completed"

    asyncio.run(go())


def test_scripted_kernel_does_not_leak_planted_secret() -> None:
    stop_harness_kernel()
    kernel = HarnessKernel(
        driver="scripted",
        extra_env={"DEEPSEEK_API_KEY": _SECRET},
    )
    hello = kernel.start()
    try:
        ping = kernel.request("ping")
        blob = json.dumps({"hello": hello, "ping": ping}, ensure_ascii=False)
        assert _SECRET not in blob
        assert hello.get("type") == "hello"
        assert ping.get("driver") == "scripted"
    finally:
        kernel.close()
        stop_harness_kernel()


@pytest.fixture
def stub_kernel(tmp_path: Path):
    stop_harness_kernel()
    home = prepare_dsh_home(tmp_path / "dsh-home")
    kernel = HarnessKernel(
        driver="dsh",
        dsh_home=home,
        extra_env={
            "KSS_HARNESS_STUB_LLM": "1",
            "DEEPSEEK_API_KEY": _SECRET,
        },
        startup_timeout=20.0,
    )
    hello = kernel.start()
    assert hello.get("type") == "hello"
    assert hello.get("driver") == "dsh"
    try:
        yield kernel
    finally:
        kernel.close()
        stop_harness_kernel()


def test_live_stub_desktop_turn_owned_by_harness(stub_kernel: HarnessKernel) -> None:
    body = stub_kernel.request(
        "desktop.turn",
        {"session_id": "live-desktop", "input": "盘面定向"},
        timeout=120,
    )
    blob = json.dumps(body, ensure_ascii=False)
    assert _SECRET not in blob
    assert "kss-stub-placeholder" not in blob
    assert body.get("ok") is True, body
    assert body.get("status") == "completed"
    assert "KSS live stub" in str(body.get("assistant_text") or "")
    types = [e.get("type") for e in body.get("events") or []]
    assert "turn_start" in types
    ping = stub_kernel.request("ping")
    assert ping.get("agents") is True
    assert _SECRET not in json.dumps(ping)


def test_live_stub_research_overlay_json(stub_kernel: HarnessKernel) -> None:
    cwd = _ROOT / ".build" / "live-research-attempt"
    cwd.mkdir(parents=True, exist_ok=True)
    prompt = (
        '最终只输出一个 JSON 对象，不要 Markdown：\\n'
        '{"status":"succeeded|incomplete","claims":[],"evidence_refs":[],'
        '"artifact_refs":[],"open_questions":[],"warnings":[]}'
    )
    body = stub_kernel.request(
        "research.turn",
        {
            "session_id": "live-research",
            "attempt_id": "a1",
            "cwd": str(cwd),
            "prompt": prompt,
            "allowlist": ["bash", "write"],
        },
        timeout=120,
    )
    assert body.get("ok") is True, body
    parsed = json.loads(body.get("assistant_text") or "")
    assert parsed["status"] == "succeeded"
    assert "claims" in parsed
    from kss.research.runner import AgentResearchTaskRunner

    runner = AgentResearchTaskRunner.__new__(AgentResearchTaskRunner)
    assert runner._parse_result(body["assistant_text"]) is not None


def test_kss_catalog_maps_to_official_deepseek() -> None:
    assert _map_dsh_provider("deepseek") == "deepseek-official"
    assert _map_dsh_provider("kss-primary") == "deepseek-official"
    assert _map_dsh_provider("openai") == "openai"


def test_dsh_kernel_rejects_ready_without_agents() -> None:
    kernel = HarnessKernel(driver="dsh", startup_timeout=0.2)
    kernel._alive = True
    kernel._ready = {"type": "ready", "agents": False}
    kernel._stderr_tail = [
        "[kss-harness-host] dsh boot failed: Cannot find module './logs/protobuf'",
    ]
    try:
        kernel._require_dsh_agents()
    except RuntimeError as exc:
        assert "logs/protobuf" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")



def test_approval_emitter_falls_back_to_sole_active_turn() -> None:
    href = _LIVE.resolve().as_posix()
    out = _node_eval(
        f"""
import {{ approvalSessionIds, resolveTurnEmitter }} from 'file://{href}';
const ids = approvalSessionIds({{
  agent: {{ session: {{ header: {{ id: 'child-session' }} }} }},
  callId: 'c1',
  toolName: 'bash',
}});
const emitters = new Map([['local-desktop', (ev) => ev]]);
const emit = resolveTurnEmitter(emitters, ids);
console.log(JSON.stringify({{ ids, matched: Boolean(emit) }}));
"""
    )
    assert "child-session" in out["ids"]
    assert out["matched"] is True



def test_kernel_timeout_kills_node_process() -> None:
    """A wedged desktop.turn must SIGTERM Node so the next turn is not queued on unread stdin."""
    proc = subprocess.Popen(
        ["sleep", "30"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    kernel = HarnessKernel(driver="scripted")
    kernel._proc = proc
    kernel._alive = True
    kernel._hello = {"type": "hello"}
    reader = threading.Thread(target=kernel._read_stdout, name="test-kernel-timeout", daemon=True)
    reader.start()
    try:
        with pytest.raises(TimeoutError, match="desktop.turn"):
            kernel.request("desktop.turn", {"session_id": "s-timeout"}, timeout=0.4)
        assert proc.poll() is not None
        assert kernel.alive is False
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2)


def test_kernel_approval_event_extends_turn_wait(tmp_path: Path) -> None:
    """approval_request pauses the 180s budget so the operator can tap 允许."""
    fake = tmp_path / "fake_host.py"
    fake.write_text(
        "import json, sys, time\n"
        "print(json.dumps({'type': 'hello', 'protocol': 1, 'driver': 'scripted'}), flush=True)\n"
        "for line in sys.stdin:\n"
        "    msg = json.loads(line)\n"
        "    cmd = msg.get('cmd')\n"
        "    if cmd == 'desktop.turn':\n"
        "        print(json.dumps({'type': 'event', 'id': msg['id'], "
        "'event': {'type': 'approval_request', 'call_id': 'c1'}}), flush=True)\n"
        "    elif cmd in {'abort', 'shutdown'}:\n"
        "        break\n",
        encoding="utf-8",
    )
    proc = subprocess.Popen(
        [sys.executable, "-u", str(fake)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    kernel = HarnessKernel(driver="scripted")
    kernel._proc = proc
    kernel._alive = True
    kernel._hello = {"type": "hello"}
    reader = threading.Thread(target=kernel._read_stdout, name="test-kernel-approval", daemon=True)
    reader.start()
    started = time.monotonic()
    try:
        with pytest.raises(TimeoutError, match="desktop.turn"):
            kernel.request(
                "desktop.turn",
                {"session_id": "s-approve"},
                timeout=0.3,
                approval_timeout=0.8,
            )
        elapsed = time.monotonic() - started
        assert elapsed >= 0.7, elapsed
        assert proc.poll() is not None
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2)


def test_agent_options_payload_uses_explicit_route(monkeypatch) -> None:
    monkeypatch.delenv("KSS_HARNESS_PROVIDER", raising=False)
    monkeypatch.delenv("KSS_HARNESS_MODEL", raising=False)
    payload = _agent_options_payload({
        "provider_id": "deepseek",
        "model_id": "deepseek-v4-pro",
        "thinking_level": "high",
    })
    assert payload == {
        "provider": "deepseek-official",
        "model": "deepseek-v4-pro",
        "reasoning_effort": "high",
    }


def test_agent_options_payload_env_override_wins(monkeypatch) -> None:
    monkeypatch.setenv("KSS_HARNESS_PROVIDER", "openai")
    monkeypatch.setenv("KSS_HARNESS_MODEL", "gpt-x")
    monkeypatch.setenv("KSS_HARNESS_REASONING_EFFORT", "max")
    payload = _agent_options_payload({
        "provider_id": "deepseek",
        "model_id": "deepseek-v4-pro",
        "thinking_level": "high",
    })
    assert payload == {"provider": "openai", "model": "gpt-x", "reasoning_effort": "max"}


_FAKE_LLM_CTX = """
const ctx = {
  llm: {
    listProviders() {
      return [{ id: 'deepseek-official', name: 'DeepSeek' }];
    },
    async listModels(provider) {
      return [
        { provider, id: 'deepseek-v4-pro', name: 'DeepSeek-V4-Pro' },
        { provider, id: 'deepseek-v4-flash', name: 'DeepSeek-V4-Flash' },
      ];
    },
    async resolveModelInfo(provider, model) {
      return {
        provider,
        id: model,
        name: model,
        inputModalities: ['text'],
        context: { contextWindow: 1000000 },
        defaultMaxTokens: 256000,
        reasoning: {
          efforts: [
            { id: 'off', name: 'Off' },
            { id: 'high', name: 'High' },
            { id: 'max', name: 'Max' },
          ],
          defaultEffort: 'high',
        },
      };
    },
  },
  agentDefaultModel: {
    currentSelection() {
      return { provider: 'deepseek-official', model: 'deepseek-v4-flash' };
    },
  },
};
"""


def test_clamp_reasoning_effort_maps_kss_levels() -> None:
    href = _LIVE.resolve().as_posix()
    out = _node_eval(
        f"""
import {{ clampReasoningEffort }} from 'file://{href}';
{_FAKE_LLM_CTX}
const noReasoningCtx = {{
  llm: {{
    async resolveModelInfo() {{ return {{ provider: 'p', id: 'm', name: 'm' }}; }},
  }},
}};
console.log(JSON.stringify({{
  exact: await clampReasoningEffort(ctx, 'deepseek-official', 'deepseek-v4-pro', 'high'),
  medium: await clampReasoningEffort(ctx, 'deepseek-official', 'deepseek-v4-pro', 'medium'),
  low: await clampReasoningEffort(ctx, 'deepseek-official', 'deepseek-v4-pro', 'low'),
  xhigh: await clampReasoningEffort(ctx, 'deepseek-official', 'deepseek-v4-pro', 'xhigh'),
  unknown: await clampReasoningEffort(ctx, 'deepseek-official', 'deepseek-v4-pro', 'weird'),
  empty: await clampReasoningEffort(ctx, 'deepseek-official', 'deepseek-v4-pro', ''),
  none: await clampReasoningEffort(noReasoningCtx, 'p', 'm', 'high') ?? null,
  passthrough: await clampReasoningEffort({{}}, 'p', 'm', 'medium'),
}}));
"""
    )
    assert out["exact"] == "high"
    assert out["medium"] == "high"
    assert out["low"] == "off"
    assert out["xhigh"] == "max"
    assert out["unknown"] == "high"  # defaultEffort
    assert out.get("empty") is None
    assert out["none"] is None
    assert out["passthrough"] == "medium"


def test_list_harness_models_shapes_catalog() -> None:
    href = _LIVE.resolve().as_posix()
    out = _node_eval(
        f"""
import {{ listHarnessModels }} from 'file://{href}';
{_FAKE_LLM_CTX}
console.log(JSON.stringify(await listHarnessModels(ctx)));
"""
    )
    providers = out["providers"]
    assert len(providers) == 1
    assert providers[0]["id"] == "deepseek-official"
    models = providers[0]["models"]
    assert [m["model_id"] for m in models] == ["deepseek-v4-pro", "deepseek-v4-flash"]
    assert models[0]["context_window"] == 1000000
    assert models[0]["default_max_tokens"] == 256000
    assert [e["id"] for e in models[0]["reasoning_efforts"]] == ["off", "high", "max"]
    assert models[0]["default_reasoning_effort"] == "high"
    assert out["default_selection"] == {
        "provider": "deepseek-official",
        "model": "deepseek-v4-flash",
        "reasoning_effort": None,
    }


def test_run_live_turn_applies_per_turn_selection() -> None:
    href = _LIVE.resolve().as_posix()
    out = _node_eval(
        f"""
import {{ runLiveTurn }} from 'file://{href}';

const agent = {{
  followed: null,
  session: {{ id: 'sel-s1', header: {{ id: 'sel-s1' }} }},
  followup(message) {{ this.followed = message; }},
  async whenIdle() {{
    if (!this.followed) return;
    this._emit({{
      type: 'assistant/chunk',
      data: {{ chunk: {{ type: 'text-delta', index: 0, text: 'ok' }} }},
    }});
  }},
  cancel() {{}},
  steer() {{}},
}};
let captured = null;
const ctx = {{
  agents: {{
    async create(opts) {{
      const agentCtx = {{
        on(name, fn) {{
          agent._emit = (event) => fn({{ id: 'sel-s1' }}, event);
          return () => {{}};
        }},
      }};
      await opts?.setup?.(agentCtx);
      return {{ agent, async dispose() {{}} }};
    }},
    get() {{ return agent; }},
  }},
  on() {{ return () => {{}}; }},
  llm: {{
    async resolveModelInfo(provider, model) {{
      return {{
        provider, id: model, name: model,
        reasoning: {{
          efforts: [
            {{ id: 'off', name: 'Off' }},
            {{ id: 'high', name: 'High' }},
            {{ id: 'max', name: 'Max' }},
          ],
          defaultEffort: 'high',
        }},
      }};
    }},
  }},
}};
const deps = {{
  SessionId: (id) => id,
  createUserMessage: (input) => ({{ role: 'user', ...input }}),
  installModelSelection: (agentCtx, selection) => {{ captured = selection; }},
  attachSessionPolicy: (ag, spec) => {{ ag.policy = spec; }},
  inheritResearchPolicy() {{}},
  resolveDesktopApproval() {{}},
  setApprovalPrompt() {{}},
}};
const result = await runLiveTurn(ctx, deps, {{
  surface: 'desktop',
  sessionId: 'sel-s1',
  input: '盘面',
  provider: 'deepseek-official',
  model: 'deepseek-v4-pro',
  reasoningEffort: 'medium',
}});
console.log(JSON.stringify({{ ok: result.ok, current: captured?.current ?? null }}));
"""
    )
    assert out["ok"] is True
    assert out["current"] == {
        "provider": "deepseek-official",
        "model": "deepseek-v4-pro",
        "reasoningEffort": "high",
    }


def test_live_stub_models_list_returns_catalog(stub_kernel: HarnessKernel) -> None:
    body = stub_kernel.request("models.list", {}, timeout=120)
    assert body.get("ok") is True, body
    providers = {p["id"]: p for p in body.get("providers") or []}
    assert "deepseek-official" in providers
    models = {m["model_id"]: m for m in providers["deepseek-official"]["models"]}
    assert "deepseek-v4-flash" in models
    flash = models["deepseek-v4-flash"]
    assert flash["reasoning_efforts"], flash
    effort_ids = {e["id"] for e in flash["reasoning_efforts"]}
    assert {"off", "high", "max"} <= effort_ids
    selection = body.get("default_selection")
    assert selection and selection["provider"] == "deepseek-official"


_VISION = _ROOT / "harness" / "kss-plugins" / "src" / "vision.js"


def test_vision_request_and_parse_shapes() -> None:
    href = _VISION.resolve().as_posix()
    out = _node_eval(
        f"""
import {{ buildVisionRequestBody, parseVisionResponse, visionEndpoint, mediaTypeForPath }} from 'file://{href}';
const body = buildVisionRequestBody({{
  model: 'gpt-vision',
  intent: '提取表格数字',
  mediaType: 'image/png',
  base64: 'QUJD',
}});
const parsedFenced = parseVisionResponse({{
  choices: [{{ message: {{ content: '```json\\n{{"ocr_text":"北证50 1024.5","layout_regions":[{{"role":"table","text":"x"}}],"semantic_description":"K线图","warnings":[]}}\\n```' }} }}],
}});
const parsedLoose = parseVisionResponse({{
  choices: [{{ message: {{ content: '这不是 JSON' }} }}],
}});
console.log(JSON.stringify({{
  model: body.model,
  temperature: body.temperature,
  imageUrl: body.messages[1].content[1].image_url.url,
  intentText: body.messages[1].content[0].text,
  endpoint: visionEndpoint('https://gateway.acme.example/v1/'),
  endpointKeep: visionEndpoint('https://x.example/v1/chat/completions'),
  media: mediaTypeForPath('/tmp/shot.JPG'),
  fencedOcr: parsedFenced.ocr_text,
  fencedRegions: parsedFenced.layout_regions.length,
  looseWarnings: parsedLoose.warnings,
  looseDesc: parsedLoose.semantic_description,
}}));
"""
    )
    assert out["model"] == "gpt-vision"
    assert out["temperature"] == 0
    assert out["imageUrl"] == "data:image/png;base64,QUJD"
    assert "提取表格数字" in out["intentText"]
    assert out["endpoint"] == "https://gateway.acme.example/v1/chat/completions"
    assert out["endpointKeep"] == "https://x.example/v1/chat/completions"
    assert out["media"] == "image/jpeg"
    assert out["fencedOcr"] == "北证50 1024.5"
    assert out["fencedRegions"] == 1
    assert out["looseWarnings"] == ["non_json_vision_output"]
    assert out["looseDesc"] == "这不是 JSON"


def test_vision_stub_mode_and_missing_credential(tmp_path: Path) -> None:
    href = _VISION.resolve().as_posix()
    image = tmp_path / "shot.png"
    image.write_bytes(b"\x89PNG fake")
    out = _node_eval(
        f"""
import {{ runVisionAnalysis }} from 'file://{href}';
process.env.KSS_HARNESS_STUB_LLM = '1';
const stub = await runVisionAnalysis({{
  route: {{ provider_id: 'openai', model_id: 'gpt-vision', base_url: 'https://api.openai.com/v1', api_key_env: 'OPENAI_API_KEY' }},
  filePath: {json.dumps(str(image))},
  intent: '',
}});
delete process.env.KSS_HARNESS_STUB_LLM;
delete process.env.KSS_VISION_PROBE_KEY;
const missing = await runVisionAnalysis({{
  route: {{ provider_id: 'openai', model_id: 'gpt-vision', base_url: 'https://api.openai.com/v1', api_key_env: 'KSS_VISION_PROBE_KEY' }},
  filePath: {json.dumps(str(image))},
  intent: '',
}});
console.log(JSON.stringify({{ stub, missingError: missing.error }}));
"""
    )
    assert out["stub"]["ok"] is True
    assert out["stub"]["provenance"] == "untrusted_model_output"
    assert out["stub"]["evidence"]["warnings"] == ["stub_mode"]
    assert out["missingError"] == "vision_credential_missing"
