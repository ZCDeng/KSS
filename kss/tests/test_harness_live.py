"""Live Harness 回合：agents.create + followup + whenIdle；CI 用 stub LLM，不碰真密钥。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "scripts"))

from kss.agent.harness_kernel import (  # noqa: E402
    HarnessKernel,
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
    assert out["chunk"] == {"type": "message_delta", "text": "盘面", "delta": "盘面"}
    assert out["ignore"] is None


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
