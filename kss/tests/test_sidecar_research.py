from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "scripts"))

import kss_app_bridge as bridge  # noqa: E402
import kss_sidecar as sc  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_harness_crash_domains():
    sc.reset_harness_crash_domains()
    yield
    sc.reset_harness_crash_domains()



class FakeWriter:
    def __init__(self):
        self.buf: list[bytes] = []
        self.closed = False

    def write(self, data: bytes) -> None:
        self.buf.append(data)

    async def drain(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True

    def frames(self) -> list[dict]:
        out = []
        for chunk in self.buf:
            for line in chunk.decode("utf-8").splitlines():
                if line.strip():
                    out.append(json.loads(line))
        return out


def _mk_reader() -> asyncio.StreamReader:
    return asyncio.StreamReader()


def _feed(reader: asyncio.StreamReader, obj: dict) -> None:
    reader.feed_data((json.dumps(obj) + "\n").encode("utf-8"))


def _payload(response_text: str) -> dict:
    response = json.loads(response_text)
    assert response["code"] == 0
    return json.loads(response["stdout"])["data"]


def _install_fake_service(monkeypatch, tmp_path, service) -> None:
    monkeypatch.setattr(bridge, "STATE_ROOT", tmp_path)
    monkeypatch.setattr(bridge, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(sc, "_RESEARCH_SERVICE", service)
    monkeypatch.setattr(sc, "_RESEARCH_SERVICE_ROOTS", (tmp_path, tmp_path))


def test_agent_research_non_stream_actions_dispatch_to_fixture(monkeypatch, tmp_path):
    calls: list[tuple[str, dict]] = []

    class Service:
        def create_goal(self, goal=None, payload=None):
            calls.append(("create", payload))
            return {"goal": "g1", "event": "created", "goal_text": goal}

        def list_goals(self, payload=None):
            calls.append(("list", payload))
            return {"goals": [{"id": "g1"}], "event": "listed"}

        def start_goal(self, goal_id=None, payload=None):
            calls.append(("start", payload))
            return {"goal": goal_id, "event": "started"}

        def audit_goal(self, goal_id=None, payload=None):
            calls.append(("audit", payload))
            return {"goal": goal_id, "event": "audited", "findings": []}

    _install_fake_service(monkeypatch, tmp_path, Service())

    created = _payload(sc._handle_agent_json_command({
        "cmd": "agent-research", "action": "create", "goal": "研究 A",
    }))
    listed = _payload(sc._handle_agent_json_command({
        "cmd": "agent-research", "action": "list",
    }))
    started = _payload(sc._handle_agent_json_command({
        "cmd": "agent-research", "action": "start", "goal_id": "g1",
    }))
    audited = _payload(sc._handle_agent_json_command({
        "cmd": "agent-research", "action": "audit", "goal_id": "g1",
    }))

    assert created["protocol_version"] == 1
    assert created["goal"] == "g1"
    assert listed["goals"] == [{"id": "g1"}]
    assert started["event"] == "started"
    assert audited["findings"] == []
    assert [name for name, _payload in calls] == ["create", "list", "start", "audit"]


def test_agent_research_supports_all_required_actions(monkeypatch, tmp_path):
    class Service:
        def __getattr__(self, name):
            if name in {
                "open_goal", "pause_goal", "resume_goal", "cancel_goal",
                "retry_task", "refresh_snapshot",
            }:
                def method(**kwargs):
                    return {"goal": kwargs.get("goal_id") or "g1", "event": name}
                return method
            raise AttributeError(name)

    _install_fake_service(monkeypatch, tmp_path, Service())

    for action in ("open", "pause", "resume", "cancel", "retry_task", "refresh_snapshot"):
        payload = _payload(sc._handle_agent_json_command({
            "cmd": "agent-research", "action": action, "goal_id": "g1", "task_id": "t1",
        }))
        assert payload["protocol_version"] == 1
        assert payload["goal"] == "g1"
        assert payload["event"]


def test_agent_research_import_corpus_passes_explicit_path(monkeypatch, tmp_path):
    calls: list[dict] = []

    class Service:
        def import_analyst_corpus(self, goal_id=None, payload=None):
            calls.append({"goal_id": goal_id, "payload": payload})
            return {
                "goal_id": goal_id,
                "event": "analyst_corpus_imported",
                "record_count": 1,
            }

    _install_fake_service(monkeypatch, tmp_path, Service())

    payload = _payload(sc._handle_agent_json_command({
        "cmd": "agent-research",
        "action": "import_corpus",
        "goal_id": "g1",
        "path": "/tmp/selected.jsonl",
    }))

    assert payload["event"] == "analyst_corpus_imported"
    assert calls == [{
        "goal_id": "g1",
        "payload": {
            "cmd": "agent-research",
            "action": "import_corpus",
            "goal_id": "g1",
            "path": "/tmp/selected.jsonl",
        },
    }]


def test_agent_research_events_replay_after_sequence(monkeypatch, tmp_path):
    class Service:
        def open_goal(self, goal_id=None):
            return {
                "detail": {
                    "goal_id": goal_id,
                    "profile_id": "investment-weekly-v3",
                    "objective": "研究快照",
                    "status": "running",
                }
            }

        def events(self, goal_id=None, after_sequence=0):
            return [
                {"goal": goal_id, "event": "old", "sequence": 1},
                {"goal": goal_id, "event": "new", "sequence": 3, "payload": {"ok": True}},
            ]

    _install_fake_service(monkeypatch, tmp_path, Service())

    async def go():
        reader, writer = _mk_reader(), FakeWriter()
        _feed(reader, {
            "cmd": "agent-research-events",
            "goal_id": "g1",
            "after_sequence": 1,
        })
        reader.feed_eof()
        await sc._on_connection(reader, writer)
        frames = writer.frames()
        assert len(frames) == 2
        assert frames[0]["event"] == "research_snapshot"
        assert frames[0]["sequence"] == 1
        assert frames[0]["snapshot"]["status"] == "running"
        assert frames[1]["protocol_version"] == 1
        assert frames[1]["goal"] == "g1"
        assert frames[1]["event"] == "new"
        assert frames[1]["sequence"] == 3
        assert frames[1]["event_id"] == "g1:3"
        assert frames[1]["timestamp"]

    asyncio.run(go())


def test_agent_artifacts_actions_dispatch_to_fixture(monkeypatch, tmp_path):
    class Service:
        def list_artifacts(self, goal_id=None, payload=None):
            return {"goal": goal_id, "event": "artifacts_listed", "artifacts": [{"id": "a1"}]}

        def export_draft(self, artifact_id=None, format=None, payload=None):
            return {"goal": payload.get("goal_id"), "event": "draft_exported", "artifact_id": artifact_id, "format": format}

        def publish_artifact(self, artifact_id=None, payload=None):
            return {"goal": payload.get("goal_id"), "event": "published", "artifact_id": artifact_id}

    _install_fake_service(monkeypatch, tmp_path, Service())

    listed = _payload(sc._handle_agent_json_command({
        "cmd": "agent-artifacts", "action": "list", "goal_id": "g1",
    }))
    exported = _payload(sc._handle_agent_json_command({
        "cmd": "agent-artifacts", "action": "export_draft", "goal_id": "g1",
        "artifact_id": "a1", "format": "markdown",
    }))
    published = _payload(sc._handle_agent_json_command({
        "cmd": "agent-artifacts", "action": "publish", "goal_id": "g1",
        "artifact_id": "a1",
    }))

    assert listed["protocol_version"] == 1
    assert listed["artifacts"][0]["id"] == "a1"
    assert exported["event"] == "draft_exported"
    assert published["event"] == "published"


def test_research_protocol_fails_soft_when_core_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr(bridge, "STATE_ROOT", tmp_path)
    monkeypatch.setattr(bridge, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(sc, "_research_service", lambda: None)

    payload = _payload(sc._handle_agent_json_command({
        "cmd": "agent-research", "action": "list",
    }))

    assert payload["protocol_version"] == 1
    assert payload["ok"] is False
    assert payload["error"] in {"research_unavailable", "research_action_unavailable"}


def test_sidecar_constructs_real_research_service_and_lists_profiles(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(bridge, "STATE_ROOT", tmp_path)
    monkeypatch.setattr(bridge, "PROJECT_ROOT", _ROOT)
    monkeypatch.setattr(sc, "_RESEARCH_SERVICE", None)
    monkeypatch.setattr(sc, "_RESEARCH_SERVICE_ROOTS", None)

    payload = _payload(sc._handle_agent_json_command({
        "cmd": "agent-research",
        "action": "list",
    }))

    assert payload["ok"] is True
    assert {
        profile["profile_id"] for profile in payload["profiles"]
    } >= {"investment-weekly-v3", "generic-research-v1"}

    created = _payload(sc._handle_agent_json_command({
        "cmd": "agent-research",
        "action": "create",
        "client_request_id": "real-sidecar-create",
        "profile_id": "investment-weekly-v3",
        "objective": "真实 sidecar 协议创建",
        "inputs": {
            "date_range": "2026-07-13_to_2026-07-17",
            "as_of": "2026-07-17",
        },
    }))
    opened = _payload(sc._handle_agent_json_command({
        "cmd": "agent-research",
        "action": "open",
        "goal_id": created["goal_id"],
    }))

    assert created["goal"]["goal_id"] == created["goal_id"]
    assert opened["goal"]["snapshot"]["as_of"] == "2026-07-17"


def test_f5_scheduled_resume_does_not_attach_desktop_confirm(monkeypatch, tmp_path):
    calls = []

    class Service:
        def resume_goal(self, goal_id=None, **kwargs):
            calls.append({"goal_id": goal_id, "kwargs": kwargs})
            return {
                "goal_id": goal_id,
                "event": "resumed",
                "origin": "scheduled",
                "attach_desktop_answerer": False,
            }

    _install_fake_service(monkeypatch, tmp_path, Service())
    payload = _payload(sc._handle_agent_json_command({
        "cmd": "agent-research",
        "action": "resume",
        "goal_id": "scheduled-goal",
    }))
    src = Path(sc.__file__).read_text(encoding="utf-8")
    assert payload["attach_desktop_answerer"] is False
    assert calls[0]["goal_id"] == "scheduled-goal"
    assert "AUTO_TASKS" not in src
