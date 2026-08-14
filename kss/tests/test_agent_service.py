from __future__ import annotations

import asyncio
import base64
import sys
from pathlib import Path

from kss.agent import AgentMessage, KSSAgentService, RuntimeRunOptions
from kss.agent.context import ContextAssembler
from kss.agent.provider import ModelCapabilities, ProviderError, ProviderEvent

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "scripts"))
import kss_chat_loop as chat_loop  # noqa: E402


def test_provider_connection_requires_a_finished_stream(tmp_path):
    service = KSSAgentService(tmp_path, tmp_path)

    class FinishedProvider:
        def stream_sync(self, messages, tools, config):
            assert messages[-1]["content"] == "Reply with OK only."
            assert tools == []
            assert config.model is None
            assert config.route_set is not None
            assert config.route_set.primary.provider_id == "openai-compatible"
            yield ProviderEvent(
                type="text",
                model="model-a",
                provider="primary",
                text="OK",
                metadata={"candidate_index": 0},
            )
            yield ProviderEvent(
                type="finish",
                model="model-a",
                provider="primary",
                finish_reason="stop",
                metadata={"candidate_index": 0},
            )

    service.provider = FinishedProvider()
    result = service.test_provider_connection(
        primary={"provider_id": "openai-compatible", "model_id": "model-a"}
    )

    assert result["ok"] is True
    assert result["status"] == "ready"
    assert result["candidates"][0]["ok"] is True


def test_provider_connection_does_not_accept_partial_output(tmp_path):
    service = KSSAgentService(tmp_path, tmp_path)

    class PartialProvider:
        def stream_sync(self, messages, tools, config):
            yield ProviderEvent(
                type="thinking",
                model="model-a",
                provider="primary",
                text="partial",
                metadata={"candidate_index": 0},
            )
            yield ProviderEvent(
                type="error",
                model="model-a",
                provider="primary",
                error=ProviderError(
                    code="stream_failed",
                    message="stream broke",
                    phase="stream",
                ),
                metadata={"candidate_index": 0},
            )

    service.provider = PartialProvider()
    result = service.test_provider_connection()

    assert result["ok"] is False
    assert result["status"] == "unavailable"
    assert result["candidates"][0]["ok"] is False


def test_service_resolves_images_only_at_provider_boundary_and_persists_thinking(
    monkeypatch, tmp_path
):
    async def scenario():
        service = KSSAgentService(tmp_path, tmp_path)
        # Attachments use the selected session route's capability snapshot, not
        # whichever global model happened to initialize the provider.
        route = service.route_store.load().primary.as_dict()
        route.update(supports_images=True, supports_thinking=True)
        service.set_provider_routes(primary=route)
        selected = tmp_path / "chart.png"
        image_bytes = (
            b"\x89PNG\r\n\x1a\n"
            b"\x00\x00\x00\rIHDR"
            b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
        )
        selected.write_bytes(image_bytes)
        attachment = service.import_attachment(str(selected))
        captured = {}

        async def fake_run_turn(messages, emit, request_write, **kwargs):
            captured["messages"] = messages
            user_blocks = messages[-1]["content"]
            image = next(block for block in user_blocks if block["type"] == "image")
            assert base64.b64decode(image["data"]) == image_bytes
            assert image["mimeType"] == "image/png"
            await emit({"type": "thinking_start", "content_index": 0})
            await emit({
                "type": "thinking_delta",
                "text": "provider reasoning",
                "content_index": 0,
            })
            await emit({"type": "chunk", "text": "可见回答", "content_index": 1})
            await emit({"type": "done", "reason": "stop"})
            return chat_loop.TurnTranscript(
                messages=[
                    *messages,
                    {
                        "role": "assistant",
                        "content": "可见回答",
                        "content_blocks": [
                            {
                                "type": "thinking",
                                "text": "provider reasoning",
                                "content_index": 0,
                                "provider": "mock",
                                "model": "mock-model",
                                "signature": "sig",
                            },
                            {
                                "type": "text",
                                "text": "可见回答",
                                "content_index": 1,
                            },
                        ],
                    },
                ],
                run_state={"status": "done", "reason": "stop"},
            )

        monkeypatch.setattr(chat_loop, "run_turn", fake_run_turn)

        async def no_write(**kwargs):
            raise AssertionError("write gate should not be used")

        result = await service.run_turn(
            "multimodal",
            "multimodal-client",
            "看图",
            lambda event: asyncio.sleep(0),
            no_write,
            attachment_ids=[attachment.id],
        )

        assert result.status == "completed"
        messages = service.sessions.read_messages("multimodal")
        user = next(message for message in messages if message.role == "user")
        assistant = next(
            message for message in messages if message.role == "assistant"
        )
        assert user.metadata["attachment_ids"] == [attachment.id]
        assert any(
            block.type == "image" and block.attachment_id == attachment.id
            for block in user.content_blocks
        )
        assert assistant.content == "可见回答"
        assert assistant.content_blocks[0].type == "thinking"
        assert assistant.content_blocks[0].text == "provider reasoning"
        session_text = (
            tmp_path / "storage" / "agent" / "sessions" / "multimodal.jsonl"
        ).read_text(encoding="utf-8")
        assert base64.b64encode(image_bytes).decode("ascii") not in session_text
        service.close()

    asyncio.run(scenario())


def test_service_enforces_per_run_capability_and_budget_envelope(
    monkeypatch, tmp_path
):
    async def scenario():
        service = KSSAgentService(tmp_path, tmp_path)
        preference = service.memories.propose(
            "preference",
            "偏好结构化输出",
            source_session="source",
            source_entry="pref-entry",
        )
        thesis = service.memories.propose(
            "thesis",
            "旧判断",
            source_session="source",
            source_entry="thesis-entry",
        )
        service.memories.approve(preference.id)
        service.memories.approve(thesis.id)
        captured = {}

        async def fake_run_turn(messages, emit, request_write, **kwargs):
            registry = kwargs["tool_registry"]
            captured["tool_names"] = [
                item["function"]["name"] for item in registry.build_schema()
            ]
            captured["max_steps"] = kwargs["max_steps"]
            captured["timeout"] = kwargs["turn_timeout"]
            assert registry.has_tool("load_skill") is False
            await emit({"type": "chunk", "text": "完成"})
            await emit({"type": "done", "reason": "stop"})
            return chat_loop.TurnTranscript(
                messages=[*messages, {"role": "assistant", "content": "完成"}],
                run_state={
                    "status": "done",
                    "reason": "stop",
                    "usage": {"total_tokens": 30},
                },
            )

        monkeypatch.setattr(chat_loop, "run_turn", fake_run_turn)
        events = []

        async def no_write(**kwargs):
            raise AssertionError("write gate should not be used")

        result = await service.run_turn(
            "restricted",
            "client-restricted",
            "按偏好完成研究",
            events.append,
            no_write,
            run_options=RuntimeRunOptions(
                allowed_tools=frozenset({"research_search"}),
                allowed_skills=frozenset(),
                allowed_memory_kinds=frozenset({"preference"}),
                max_steps=3,
                timeout_seconds=12,
                max_provider_tokens=100,
                allow_write_tools=False,
            ),
        )

        assert result.status == "completed", result
        assert captured == {
            "tool_names": ["research_search"],
            "max_steps": 3,
            "timeout": 12,
        }
        recalls = next(
            event.payload["recalls"] for event in events if event.type == "recall"
        )
        assert [item["kind"] for item in recalls] == ["preference"]

    asyncio.run(scenario())


def test_service_rejects_write_tool_in_read_only_envelope(monkeypatch, tmp_path):
    async def scenario():
        service = KSSAgentService(tmp_path, tmp_path)
        called = False

        async def fake_run_turn(*args, **kwargs):
            nonlocal called
            called = True
            raise AssertionError("provider must not run with forbidden write tool")

        monkeypatch.setattr(chat_loop, "run_turn", fake_run_turn)

        async def no_write(**kwargs):
            raise AssertionError("write gate should not be used")

        result = await service.run_turn(
            "restricted-write",
            "client-restricted-write",
            "执行",
            lambda event: asyncio.sleep(0),
            no_write,
            run_options=RuntimeRunOptions(
                allowed_tools=frozenset({"run_task"}),
                allow_write_tools=False,
            ),
        )

        assert result.status == "failed"
        assert "write tools are forbidden" in str(result.error)
        assert called is False

    asyncio.run(scenario())


def test_service_preserves_bounded_trusted_internal_contract(monkeypatch, tmp_path):
    async def scenario():
        service = KSSAgentService(tmp_path, tmp_path)
        contract = '{"contract":"' + ("证据" * 400) + '"}'
        captured = {}

        async def fake_run_turn(messages, emit, request_write, **kwargs):
            captured["input"] = messages[-1]["content"]
            await emit({"type": "done", "reason": "stop"})
            return chat_loop.TurnTranscript(
                messages=[*messages, {"role": "assistant", "content": "完成"}],
                run_state={"status": "done", "reason": "stop"},
            )

        monkeypatch.setattr(chat_loop, "run_turn", fake_run_turn)

        async def no_write(**kwargs):
            raise AssertionError("write gate should not be used")

        result = await service.run_turn(
            "trusted-contract",
            "trusted-contract-client",
            contract,
            lambda event: asyncio.sleep(0),
            no_write,
            run_options=RuntimeRunOptions(
                allowed_tools=frozenset(),
                allowed_skills=frozenset(),
                allowed_memory_kinds=frozenset(),
                trusted_internal_input=True,
                allow_write_tools=False,
            ),
        )

        assert result.status == "completed"
        assert len(captured["input"]) > 500
        assert captured["input"] == contract

    asyncio.run(scenario())


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


def test_service_preserves_structured_recall_and_wires_skill_resource(
    monkeypatch, tmp_path
):
    async def scenario():
        skill_dir = tmp_path / ".agents" / "skills" / "demo"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: demo\ndescription: Demo skill\n---\n正文",
            encoding="utf-8",
        )
        (skill_dir / "guide.txt").write_text("分页资源", encoding="utf-8")

        service = KSSAgentService(tmp_path, tmp_path)
        memory = service.memories.propose(
            "thesis",
            "旧研究判断需要重新核实",
            source_session="source-session",
            source_entry="source-entry",
        )
        service.memories.approve(memory.id)
        captured = {}

        async def fake_run_turn(messages, emit, request_write, **kwargs):
            registry = kwargs["tool_registry"]
            read_handler = registry.handler("read_skill_resource")
            assert read_handler is not None
            captured["resource"] = read_handler(
                {
                    "skill_id": "demo",
                    "path": "guide.txt",
                    "offset": 0,
                    "max_chars": 4,
                }
            )
            propose_handler = registry.handler("propose_memory")
            assert propose_handler is not None
            captured["candidate"] = await propose_handler(
                {
                    "kind": "decision",
                    "text": "后续使用真实 user message 作为来源",
                    "source": "agent",
                }
            )
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
        events = []

        async def no_write(**kwargs):
            raise AssertionError("write gate should not be used")

        await service.run_turn(
            "structured",
            "client-structured",
            "需要重新核实什么",
            events.append,
            no_write,
        )

        recalls = next(
            event.payload["recalls"] for event in events if event.type == "recall"
        )
        assert recalls[0]["id"] == memory.id
        assert recalls[0]["source_session"] == "source-session"
        assert recalls[0]["source_entry"] == "source-entry"
        assert recalls[0]["review_required"] is True
        assert recalls[0]["score"] > 0
        persisted_recall = next(
            entry["payload"]["items"]
            for entry in service.sessions._read_entries("structured")
            if entry["type"] == "recall"
        )
        assert persisted_recall == recalls

        resource = captured["resource"]
        assert resource["content"] == "分页资源"
        assert resource["size_bytes"] == len("分页资源".encode("utf-8"))
        assert resource["provenance"] == "skill_resource"

        candidate = captured["candidate"]["memory"]
        user_message = service.sessions.read_messages("structured")[0]
        assert candidate["source_entry"] == user_message.id
        assert candidate["source_entry"] != next(
            event.run_id for event in events if event.type == "agent_start"
        )

    asyncio.run(scenario())


def test_service_applies_steering_then_follow_up_in_one_settled_run(
    monkeypatch, tmp_path
):
    async def scenario():
        service = KSSAgentService(tmp_path, tmp_path)
        provider_ready = asyncio.Event()
        consume_queue = asyncio.Event()

        async def fake_run_turn(messages, emit, request_write, **kwargs):
            provider_ready.set()
            await consume_queue.wait()
            transcript_messages = list(messages)

            steering = await kwargs["take_steering"]()
            assert steering is not None
            assert [item["content"] for item in steering] == ["补充约束"]
            transcript_messages.extend(steering)
            await emit({"type": "turn_start", "step": 0, "kind": "initial"})
            await emit({"type": "message_start", "role": "assistant"})
            await emit({"type": "chunk", "text": "已接收补充"})
            await emit({"type": "message_end", "role": "assistant"})
            await emit({"type": "turn_end", "reason": "steering"})
            transcript_messages.append(
                {"role": "assistant", "content": "已接收补充"}
            )

            follow_up = await kwargs["take_follow_up"]()
            assert follow_up is not None
            assert follow_up["content"] == "然后呢"
            transcript_messages.append(follow_up)
            await emit({"type": "turn_start", "step": 1, "kind": "follow_up"})
            await emit({"type": "message_start", "role": "assistant"})
            await emit({"type": "chunk", "text": "继续回答"})
            await emit({"type": "message_end", "role": "assistant"})
            await emit({"type": "turn_end", "reason": "stop"})
            transcript_messages.append(
                {"role": "assistant", "content": "继续回答"}
            )
            await emit({"type": "done", "reason": "stop"})
            return chat_loop.TurnTranscript(
                messages=transcript_messages,
                run_state={"status": "done", "reason": "stop"},
            )

        monkeypatch.setattr(chat_loop, "run_turn", fake_run_turn)
        events = []

        async def no_write(**kwargs):
            raise AssertionError("write gate should not be used")

        task = asyncio.create_task(
            service.run_turn(
                "queued",
                "client-initial",
                "初始问题",
                events.append,
                no_write,
            )
        )
        await provider_ready.wait()
        run_id = service.runtime.active_run_id("queued")
        assert run_id is not None

        first = service.steer(run_id, "client-steer", "补充约束")
        duplicate = service.steer(run_id, "client-steer", "不会重复")
        follow = service.follow_up(run_id, "client-follow", "然后呢")
        assert first["item"]["id"] == duplicate["item"]["id"]
        assert first["item"]["mode"] == "steering"
        assert follow["item"]["mode"] == "follow_up"

        consume_queue.set()
        result = await task
        assert result.status == "completed"
        assert [event.type for event in events].count("agent_start") == 1
        assert [event.type for event in events].count("agent_end") == 1
        assert [event.type for event in events].count("turn_start") == 2
        assert [event.type for event in events].count("turn_end") == 2
        assert [
            event.payload["item"]["mode"]
            for event in events
            if event.type == "queue_update"
            and event.payload.get("operation") == "applied"
        ] == ["steering", "follow_up"]

        messages = service.sessions.read_messages("queued")
        assert [message.content for message in messages if message.role == "user"] == [
            "初始问题",
            "补充约束",
            "然后呢",
        ]
        queue_events = [
            entry["type"]
            for entry in service.sessions._read_entries("queued")
            if entry["type"].startswith("queue_")
        ]
        assert queue_events == [
            "queue_added",
            "queue_added",
            "queue_applied",
            "queue_applied",
        ]
        assert service.queued_inputs(run_id=run_id) == []

    asyncio.run(scenario())


def test_non_coverage_turn_keeps_default_budget(monkeypatch, tmp_path):
    async def scenario():
        service = KSSAgentService(tmp_path, tmp_path)
        captured = {}

        async def fake_run_turn(messages, emit, request_write, **kwargs):
            captured["max_steps"] = kwargs["max_steps"]
            captured["timeout"] = kwargs["turn_timeout"]
            await emit({"type": "chunk", "text": "复盘"})
            await emit({"type": "done", "reason": "stop"})
            return chat_loop.TurnTranscript(
                messages=[*messages, {"role": "assistant", "content": "复盘"}],
                run_state={"status": "done", "reason": "stop", "usage": {"total_tokens": 3}},
            )

        monkeypatch.setattr(chat_loop, "run_turn", fake_run_turn)

        async def no_write(**kwargs):
            raise AssertionError("write")

        events = []
        await service.run_turn("s", "c1", "今天为什么涨", events.append, no_write)
        assert captured == {"max_steps": 8, "timeout": 240.0}

    asyncio.run(scenario())


def test_coverage_timeout_emits_r12_and_not_empty(monkeypatch, tmp_path):
    async def scenario():
        from kss.agent.service import RuntimeRunOptions
        from kss.equity_research.intent import is_r12_text
        service = KSSAgentService(tmp_path, tmp_path)
        chunks = []

        async def fake_run_turn(messages, emit, request_write, **kwargs):
            await emit({"type": "chunk", "text": "半章备忘"})
            await emit({"type": "done", "reason": "timeout"})
            return chat_loop.TurnTranscript(
                messages=[*messages, {"role": "assistant", "content": "半章备忘"}],
                run_state={"status": "done", "reason": "timeout", "usage": {}},
            )

        monkeypatch.setattr(chat_loop, "run_turn", fake_run_turn)
        events = []

        async def no_write(**kwargs):
            raise AssertionError("write")

        result = await service.run_turn(
            "s",
            "c2",
            "研究一下 600519.SH",
            events.append,
            no_write,
            run_options=RuntimeRunOptions(coverage_closer=True, timeout_seconds=1, max_steps=2),
        )
        texts = [getattr(e, "payload", e) for e in events]
        blob = str(texts)
        assert result.termination_reason == "unable_to_complete"
        assert result.error is None
        assert "无法完成" in blob or is_r12_text(blob)

    asyncio.run(scenario())


def test_coverage_midflight_explainer_is_follow_up_not_steer(monkeypatch, tmp_path):
    async def scenario():
        service = KSSAgentService(tmp_path, tmp_path)
        provider_ready = asyncio.Event()
        consume_queue = asyncio.Event()

        async def fake_run_turn(messages, emit, request_write, **kwargs):
            provider_ready.set()
            await consume_queue.wait()
            follow_up = await kwargs["take_follow_up"]()
            assert follow_up is not None
            assert follow_up["content"] == "今天为什么涨"
            await emit({"type": "chunk", "text": "覆盖完成"})
            await emit({"type": "done", "reason": "stop"})
            return chat_loop.TurnTranscript(
                messages=[*messages, follow_up, {"role": "assistant", "content": "覆盖完成"}],
                run_state={"status": "done", "reason": "stop", "usage": {}},
            )

        monkeypatch.setattr(chat_loop, "run_turn", fake_run_turn)

        async def no_write(**kwargs):
            raise AssertionError("write")

        events = []
        task = asyncio.create_task(
            service.run_turn(
                "cov-q",
                "client-cov",
                "研究一下 600519.SH",
                events.append,
                no_write,
                run_options=RuntimeRunOptions(
                    coverage_closer=True, coverage_path=True, timeout_seconds=30, max_steps=4
                ),
            )
        )
        await provider_ready.wait()
        run_id = service.runtime.active_run_id("cov-q")
        assert run_id is not None
        queued = service.steer(run_id, "client-why", "今天为什么涨")
        assert queued.get("rejected") is not True
        assert queued["item"]["mode"] == "follow_up"
        consume_queue.set()
        result = await task
        assert result.status == "completed"

    asyncio.run(scenario())
