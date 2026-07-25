from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from kss.agent import AgentMessage, KSSAgentService
from kss.agent.context import ContextAssembler

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "scripts"))
import kss_chat_loop as chat_loop  # noqa: E402


def test_service_persists_compaction_before_terminal_events(monkeypatch, tmp_path):
    async def scenario():
        service = KSSAgentService(tmp_path, tmp_path)
        service.assembler = ContextAssembler(
            compact_at_tokens=1,
            keep_tokens=32,
        )
        service.sessions.create_session(session_id="s1")
        service.sessions.append_message(
            "s1",
            AgentMessage(
                id="old-user",
                role="user",
                content="旧问题" * 40,
                timestamp=1.0,
            ),
        )
        service.sessions.append_message(
            "s1",
            AgentMessage(
                id="old-answer",
                role="assistant",
                content="旧答案" * 40,
                timestamp=2.0,
            ),
        )

        async def summarize(source, abort_token):
            assert "旧问题" in source
            return (
                {
                    "目标": "继续研究",
                    "偏好": "简洁",
                    "已完成": "完成旧轮",
                    "关键决策": "保留顺序工具",
                    "未完成": "当前问题",
                    "关键证据": "旧答案",
                },
                {"input_tokens": 10, "output_tokens": 5},
            )

        service._summarize = summarize

        async def fake_run_turn(messages, emit, request_write, **kwargs):
            effective = kwargs["transform_context"](messages)
            await emit({"type": "chunk", "text": "新答案"})
            await emit({"type": "done", "reason": "stop"})
            return chat_loop.TurnTranscript(
                messages=[
                    *effective,
                    {"role": "assistant", "content": "新答案"},
                ],
                run_state={"status": "done", "reason": "stop"},
            )

        monkeypatch.setattr(chat_loop, "run_turn", fake_run_turn)
        events = []

        async def no_write(**kwargs):
            raise AssertionError("write gate should not be used")

        result = await service.run_turn(
            "s1", "client-1", "新问题", events.append, no_write
        )
        assert result.status == "completed"
        assert service.sessions.latest_compaction("s1")["summary"]["目标"] == "继续研究"
        assert [event.type for event in events].index("compaction_end") < [
            event.type for event in events
        ].index("agent_end")
        assert service.sessions.find_run_by_client_turn_id(
            "s1", "client-1"
        )["status"] == "completed"

    asyncio.run(scenario())


def test_service_persistence_failure_records_emergency_terminal_without_agent_end(
    monkeypatch, tmp_path
):
    async def scenario():
        service = KSSAgentService(tmp_path, tmp_path)

        async def fake_run_turn(messages, emit, request_write, **kwargs):
            await emit({"type": "chunk", "text": "回答"})
            await emit({"type": "done", "reason": "stop"})
            return chat_loop.TurnTranscript(
                messages=[
                    *messages,
                    {"role": "assistant", "content": "回答"},
                ],
                run_state={"status": "done", "reason": "stop"},
            )

        monkeypatch.setattr(chat_loop, "run_turn", fake_run_turn)
        original_append_entry = service.sessions.append_entry

        def fail_run_state(session_id, entry_type, payload=None, **kwargs):
            if entry_type == "run_state":
                raise OSError("transient append failure")
            return original_append_entry(
                session_id, entry_type, payload, **kwargs
            )

        monkeypatch.setattr(service.sessions, "append_entry", fail_run_state)
        events = []

        async def no_write(**kwargs):
            raise AssertionError("write gate should not be used")

        result = await service.run_turn(
            "persist-failure",
            "client-1",
            "问题",
            events.append,
            no_write,
        )

        assert result.status == "failed"
        assert result.termination_reason == "persistence_error"
        assert "agent_end" not in [event.type for event in events]
        record = service.sessions.find_run_by_client_turn_id(
            "persist-failure", "client-1"
        )
        assert record is not None
        assert record["status"] == "failed"
        assert record["reason"] == "persistence_error"

    asyncio.run(scenario())


def test_service_duplicate_check_recovers_same_process_orphan(tmp_path):
    service = KSSAgentService(tmp_path, tmp_path)
    service.sessions.create_session(session_id="orphan")
    service.sessions.start_run(
        "orphan",
        run_id="orphan-run",
        client_turn_id="orphan-client",
    )

    duplicate = service.duplicate_turn("orphan", "orphan-client")

    assert duplicate is not None
    assert duplicate.status == "interrupted"
    assert duplicate.existing_run_id == "orphan-run"


def test_service_second_compaction_iterates_from_previous_summary(monkeypatch, tmp_path):
    async def scenario():
        service = KSSAgentService(tmp_path, tmp_path)
        service.assembler = ContextAssembler(
            compact_at_tokens=1,
            keep_tokens=8,
        )
        service.sessions.create_session(session_id="s2")
        service.sessions.append_message(
            "s2",
            AgentMessage(
                id="old-user",
                role="user",
                content="第一批旧问题" * 60,
                timestamp=1.0,
            ),
        )
        service.sessions.append_message(
            "s2",
            AgentMessage(
                id="old-answer",
                role="assistant",
                content="第一批旧答案" * 60,
                timestamp=2.0,
            ),
        )
        summarize_sources = []

        async def summarize(source, abort_token):
            summarize_sources.append(source)
            if len(summarize_sources) == 1:
                assert "第一批旧问题" in source
                return (
                    {
                        "目标": "第一轮目标",
                        "偏好": "第一轮偏好",
                        "已完成": "第一轮已完成",
                        "关键决策": "第一轮关键决策",
                        "未完成": "第一轮未完成",
                        "关键证据": "第一轮关键证据",
                    },
                    {"input_tokens": 11, "output_tokens": 7},
                )
            assert "上一版压缩摘要" in source
            assert "第一轮目标" in source
            assert "第一轮回答" in source
            return (
                {
                    "目标": "第二轮目标",
                    "偏好": "第二轮偏好",
                    "已完成": "第二轮已完成",
                    "关键决策": "第二轮关键决策",
                    "未完成": "第二轮未完成",
                    "关键证据": "第二轮关键证据",
                },
                {"input_tokens": 13, "output_tokens": 9},
            )

        service._summarize = summarize

        async def fake_run_turn(messages, emit, request_write, **kwargs):
            current = messages[-1]["content"]
            answer = "第二轮回答" if "第二轮" in current else "第一轮回答"
            await emit({"type": "chunk", "text": answer})
            await emit({"type": "done", "reason": "stop"})
            return chat_loop.TurnTranscript(
                messages=[
                    *kwargs["transform_context"](messages),
                    {"role": "assistant", "content": answer},
                ],
                run_state={"status": "done", "reason": "stop"},
            )

        monkeypatch.setattr(chat_loop, "run_turn", fake_run_turn)

        async def no_write(**kwargs):
            raise AssertionError("write gate should not be used")

        await service.run_turn("s2", "client-1", "第一轮问题", lambda e: asyncio.sleep(0), no_write)
        first = service.sessions.latest_compaction("s2")
        assert first["summary"]["目标"] == "第一轮目标"
        await service.run_turn("s2", "client-2", "第二轮问题", lambda e: asyncio.sleep(0), no_write)
        second = service.sessions.latest_compaction("s2")
        assert second["summary"]["目标"] == "第二轮目标"
        assert second["tokens_before"] > second["tokens_after"]
        assert len(summarize_sources) == 2

    asyncio.run(scenario())
