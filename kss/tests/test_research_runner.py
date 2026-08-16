from __future__ import annotations

from pathlib import Path

from kss.research.harness_driver import (
    ResearchHarnessDriver,
    ResearchTurnResult,
)
from kss.research.runner import AgentResearchTaskRunner


def _ok_json() -> str:
    return (
        '{"status":"succeeded","claims":[],"evidence_refs":[],'
        '"artifact_refs":[],"open_questions":[],"warnings":[]}'
    )


class ScriptedSession:
    def __init__(self, replies: list[ResearchTurnResult] | None = None) -> None:
        self.replies = list(replies or [])
        self.calls = []

    def run(self, request, driver: ResearchHarnessDriver) -> ResearchTurnResult:
        self.calls.append(request)
        if self.replies:
            return self.replies.pop(0)
        return ResearchTurnResult(
            harness_status="completed",
            assistant_text=_ok_json(),
            usage={"total_tokens": 10},
        )


def test_research_runner_enforces_envelope_and_repairs_schema_once(tmp_path):
    session = ScriptedSession(
        [
            ResearchTurnResult(
                harness_status="completed",
                assistant_text="不是 JSON",
                usage={"total_tokens": 10},
            ),
            ResearchTurnResult(
                harness_status="completed",
                assistant_text=_ok_json(),
                usage={"total_tokens": 5},
            ),
        ]
    )
    driver = ResearchHarnessDriver(
        state_root=tmp_path,
        project_root=tmp_path,
        session=session,
    )
    runner = AgentResearchTaskRunner(
        state_root=tmp_path,
        project_root=tmp_path,
        driver=driver,
    )
    result = runner.run(
        goal={
            "goal_id": "goal-1",
            "objective": "完成研究",
            "snapshot": {"snapshot_id": "snapshot-1"},
            "origin": "scheduled",
        },
        task={
            "task_id": "task-1",
            "title": "采集证据",
            "payload": {
                "tool_whitelist": ["research_search"],
                "skill_whitelist": [],
                "max_steps": 4,
                "timeout_seconds": 30,
                "max_provider_tokens": 1_000,
                "write_allowlist": [],
            },
        },
        attempt_id="attempt-1",
        dependency_summaries=[{"summary": "依赖证据" * 300}],
    )

    assert result["status"] == "succeeded"
    assert result["usage"]["total_tokens"] == 15
    assert result["attach_desktop_answerer"] is False
    assert result["agent_preset"] == "research"
    assert len(session.calls) == 2
    assert session.calls[0].origin == "scheduled"
    assert session.calls[0].attach_desktop_answerer is False
    assert session.calls[1].attempt_id == "attempt-1-repair"
    assert "模型文本不能标记研究目标完成" in session.calls[0].prompt
    assert '"status":"succeeded|incomplete"' in session.calls[0].prompt
    assert len(session.calls[0].prompt) > 500


def test_ae2_research_task_writes_workspace_file(tmp_path):
    class WriteSession:
        def run(self, request, driver: ResearchHarnessDriver) -> ResearchTurnResult:
            out = driver.execute_tool(
                request,
                name="write",
                arguments={"path": "note.md", "content": "workspace-ok"},
                call_id="w1",
            )
            assert out["ok"] is True
            return ResearchTurnResult(
                harness_status="completed",
                assistant_text=_ok_json(),
                applied_write_ids=request.applied_write_ids,
            )

    driver = ResearchHarnessDriver(
        state_root=tmp_path,
        project_root=tmp_path / "repo",
        session=WriteSession(),
    )
    (tmp_path / "repo").mkdir()
    runner = AgentResearchTaskRunner(
        state_root=tmp_path, project_root=tmp_path / "repo", driver=driver
    )
    result = runner.run(
        goal={"goal_id": "g1", "objective": "写文件", "origin": "manual"},
        task={
            "task_id": "t1",
            "title": "笔记",
            "payload": {"write_allowlist": ["bash", "write", "edit"]},
        },
        attempt_id="a1",
        dependency_summaries=[],
    )
    note = Path(result["workspace"]) / "note.md"
    assert note.read_text(encoding="utf-8") == "workspace-ok"
    assert tmp_path.resolve() not in note.resolve().parents or "workspaces" in str(note)


def test_ae3_unlisted_live_write_is_denied(tmp_path):
    class LiveSession:
        def __init__(self) -> None:
            self.denied = None

        def run(self, request, driver: ResearchHarnessDriver) -> ResearchTurnResult:
            self.denied = driver.execute_tool(
                request,
                name="run_task",
                arguments={},
                call_id="live-1",
            )
            return ResearchTurnResult(
                harness_status="completed",
                assistant_text=_ok_json(),
            )

    session = LiveSession()
    driver = ResearchHarnessDriver(
        state_root=tmp_path, project_root=tmp_path, session=session
    )
    runner = AgentResearchTaskRunner(
        state_root=tmp_path, project_root=tmp_path, driver=driver
    )
    runner.run(
        goal={"goal_id": "g1", "objective": "x"},
        task={
            "task_id": "t1",
            "title": "t",
            "payload": {"write_allowlist": ["bash", "write"]},
        },
        attempt_id="a1",
        dependency_summaries=[],
    )
    assert session.denied["is_error"] is True
    assert session.denied["error"] == "research_write_denied"


def test_file_write_with_repo_root_cwd_fails(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    driver = ResearchHarnessDriver(state_root=tmp_path, project_root=repo)
    request = driver.prepare_attempt(
        goal_id="g1",
        task={"task_id": "t1", "payload": {"write_allowlist": ["write"]}},
        attempt_id="a1",
    )
    denied = driver.execute_tool(
        request,
        name="write",
        arguments={"path": str(repo / "secrets.txt"), "content": "nope"},
        call_id="root-write",
    )
    assert denied["is_error"] is True
    assert not (repo / "secrets.txt").exists()


def test_child_cannot_add_denied_live_write_or_change_cwd(tmp_path):
    driver = ResearchHarnessDriver(state_root=tmp_path, project_root=tmp_path)
    request = driver.prepare_attempt(
        goal_id="g1",
        task={"task_id": "t1", "payload": {"write_allowlist": ["bash"]}},
        attempt_id="a1",
    )
    denied = driver.execute_tool(
        request,
        name="run_task",
        arguments={},
        call_id="child-live",
        child=True,
        escalate={"tools": ["bash", "run_task"], "cwd": str(tmp_path)},
    )
    retarget = driver.execute_tool(
        request,
        name="write",
        arguments={"path": str(tmp_path / "escape.md"), "content": "x"},
        call_id="child-cwd",
        child=True,
        escalate={"tools": ["write"], "cwd": str(tmp_path)},
    )
    inherited = driver.inherit_child_allowlist(
        request.allowlist,
        requested_tools=["bash", "write"],
        requested_cwd=str(tmp_path),
    )
    assert denied["is_error"] is True
    assert retarget["is_error"] is True
    assert inherited.tools == request.allowlist.tools
    assert inherited.cwd == request.allowlist.cwd


def test_resume_does_not_replay_executed_writes(tmp_path):
    writes: list[str] = []

    class ReplaySession:
        def __init__(self) -> None:
            self.phase = 0

        def run(self, request, driver: ResearchHarnessDriver) -> ResearchTurnResult:
            first = driver.execute_tool(
                request,
                name="write",
                arguments={"path": "note.md", "content": "one"},
                call_id="w1",
            )
            writes.append(str(first.get("skipped") or first.get("ok")))
            if self.phase == 0:
                self.phase = 1
                return ResearchTurnResult(
                    harness_status="interrupted",
                    error="interrupted",
                    applied_write_ids=request.applied_write_ids,
                )
            second = driver.execute_tool(
                request,
                name="write",
                arguments={"path": "note.md", "content": "two"},
                call_id="w2",
            )
            writes.append(str(second.get("ok")))
            return ResearchTurnResult(
                harness_status="completed",
                assistant_text=_ok_json(),
                applied_write_ids=request.applied_write_ids,
            )

    session = ReplaySession()
    driver = ResearchHarnessDriver(
        state_root=tmp_path, project_root=tmp_path, session=session
    )
    runner = AgentResearchTaskRunner(
        state_root=tmp_path, project_root=tmp_path, driver=driver
    )
    task = {
        "task_id": "t1",
        "title": "t",
        "payload": {"write_allowlist": ["write"]},
    }
    goal = {"goal_id": "g1", "objective": "x"}
    first = runner.run(
        goal=goal, task=task, attempt_id="a1", dependency_summaries=[]
    )
    second = runner.run(
        goal=goal, task=task, attempt_id="a2", dependency_summaries=[]
    )
    assert first["harness_status"] == "interrupted"
    assert second["status"] == "succeeded"
    assert writes[0] == "True"
    assert "already_applied" in writes
    assert Path(second["workspace"]).resolve() == Path(first["workspace"]).resolve()
    note = Path(second["workspace"]) / "note.md"
    assert note.read_text(encoding="utf-8") == "two"


def test_new_allowlist_starts_new_workspace(tmp_path):
    session = ScriptedSession()
    driver = ResearchHarnessDriver(
        state_root=tmp_path, project_root=tmp_path, session=session
    )
    runner = AgentResearchTaskRunner(
        state_root=tmp_path, project_root=tmp_path, driver=driver
    )
    first = runner.run(
        goal={"goal_id": "g1", "objective": "x"},
        task={"task_id": "t1", "payload": {"write_allowlist": ["write"]}},
        attempt_id="a1",
        dependency_summaries=[],
    )
    second = runner.run(
        goal={"goal_id": "g1", "objective": "x"},
        task={"task_id": "t1", "payload": {"write_allowlist": ["write", "bash"]}},
        attempt_id="a2",
        dependency_summaries=[],
    )
    assert first["workspace"] != second["workspace"]


def test_f5_scheduled_origin_never_attaches_desktop_answerer(tmp_path):
    session = ScriptedSession()
    driver = ResearchHarnessDriver(
        state_root=tmp_path, project_root=tmp_path, session=session
    )
    runner = AgentResearchTaskRunner(
        state_root=tmp_path, project_root=tmp_path, driver=driver
    )
    result = runner.run(
        goal={"goal_id": "g1", "objective": "x", "origin": "scheduled"},
        task={"task_id": "t1", "payload": {"write_allowlist": ["write"]}},
        attempt_id="a1",
        dependency_summaries=[],
    )
    assert result["attach_desktop_answerer"] is False
    assert all(call.attach_desktop_answerer is False for call in session.calls)
    assert all(call.agent_preset == "research" for call in session.calls)
