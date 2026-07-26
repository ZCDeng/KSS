from __future__ import annotations

import asyncio

from kss.agent.types import AgentMessage, RunResult
from kss.research.runner import AgentResearchTaskRunner


def test_research_runner_enforces_envelope_and_repairs_schema_once(tmp_path):
    async def scenario():
        runner = AgentResearchTaskRunner(
            state_root=tmp_path,
            project_root=tmp_path,
        )
        calls = []

        class FakeAgent:
            async def run_turn(
                self,
                session_id,
                client_turn_id,
                input,
                emit,
                request_write,
                *,
                run_options,
            ):
                calls.append(
                    {
                        "session_id": session_id,
                        "client_turn_id": client_turn_id,
                        "input": input,
                        "options": run_options,
                    }
                )
                if client_turn_id.endswith("-repair"):
                    content = (
                        '{"status":"succeeded","claims":[],"evidence_refs":[],'
                        '"artifact_refs":[],"open_questions":[],"warnings":[]}'
                    )
                    usage = {"total_tokens": 5}
                else:
                    content = "不是 JSON"
                    usage = {"total_tokens": 10}
                return RunResult(
                    run_id=f"run-{client_turn_id}",
                    session_id=session_id,
                    client_turn_id=client_turn_id,
                    status="completed",
                    messages=(
                        AgentMessage(
                            id=f"message-{client_turn_id}",
                            role="assistant",
                            content=content,
                            timestamp=1.0,
                        ),
                    ),
                    usage=usage,
                )

        runner._agent = FakeAgent()
        result = await runner._run_async(
            goal={
                "goal_id": "goal-1",
                "objective": "完成研究",
                "snapshot": {"snapshot_id": "snapshot-1"},
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
                },
            },
            attempt_id="attempt-1",
            dependency_summaries=[
                {"summary": "依赖证据" * 300}
            ],
        )

        assert result["status"] == "succeeded"
        assert result["usage"]["total_tokens"] == 15
        assert len(calls) == 2
        assert calls[0]["session_id"].endswith("-attempt-1")
        assert calls[0]["options"].allowed_tools == frozenset(
            {"research_search"}
        )
        assert calls[0]["options"].allow_write_tools is False
        assert calls[0]["options"].trusted_internal_input is True
        assert len(calls[0]["input"]) > 500
        assert '"status":"succeeded|incomplete"' in calls[0]["input"]
        assert calls[1]["client_turn_id"] == "attempt-1-repair"
        assert calls[1]["options"].allowed_tools == frozenset()
        assert calls[1]["options"].max_steps == 1

    asyncio.run(scenario())
