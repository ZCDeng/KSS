from __future__ import annotations

import pytest

from kss.agent import AbortToken, AgentMessage, ContextAssembler, EventSequencer


def test_context_assembler_builds_six_sections_without_compaction():
    assembler = ContextAssembler()
    context = assembler.assemble(
        session_id="s1",
        goal="实现 Agent Core",
        preferences=["只改 kss/agent"],
        completed=["建好包结构"],
        decisions=["JSONL append-only"],
        unfinished=["运行测试"],
        evidence=["pytest owned"],
        messages=[
            AgentMessage(id="m1", role="user", content="请实现", timestamp=1.0),
            AgentMessage(id="m2", role="tool", content="已有 rank API", timestamp=2.0),
        ],
        skills=["alpha: 可用"],
        memories=["偏好：纯新增"],
    )

    assert context.compacted is False
    assert list(context.sections) == ["目标", "偏好", "已完成", "关键决策", "未完成", "关键证据"]
    assert "实现 Agent Core" in context.text
    assert "已有 rank API" in context.sections["关键证据"]
    assert "user: 请实现" not in context.sections["关键证据"]
    assert [message.role for message in context.messages] == ["system", "user"]


def test_context_assembler_compacts_to_deterministic_six_section_summary():
    assembler = ContextAssembler(compact_at_tokens=100, keep_tokens=50)
    messages = [
        AgentMessage(id=str(i), role="user", content=f"第{i}轮 必须 保留 current fact skill memory", timestamp=float(i))
        for i in range(300)
    ]

    context = assembler.assemble(session_id="s2", messages=messages, goal="压缩测试")

    assert context.compacted is True
    assert list(context.sections) == ["目标", "偏好", "已完成", "关键决策", "未完成", "关键证据"]
    assert context.text == assembler.assemble(session_id="s2", messages=messages, goal="压缩测试").text


def test_context_detailed_uses_model_budget_and_counts_all_assembled_messages():
    class ExactEstimator:
        estimated = False

        def estimate_messages(self, messages):
            return len(messages) * 10

    assembler = ContextAssembler(token_estimator=ExactEstimator())
    messages = [
        AgentMessage(id="u1", role="user", content="第一轮", timestamp=1.0),
        AgentMessage(id="a1", role="assistant", content="答复", timestamp=2.0),
        AgentMessage(id="u2", role="user", content="当前输入", timestamp=3.0),
    ]

    detailed = assembler.assemble_detailed(
        session_id="usage",
        messages=messages,
        model_capabilities={"context_window": 100, "max_output_tokens": 20},
    )

    assert detailed.context.token_budget == 100
    assert detailed.context.reserve_tokens == 20
    assert detailed.usage.limit == 80
    assert detailed.usage.used == 50  # 两条 context message + 三条完整历史
    assert detailed.usage.estimated is False
    assert detailed.assembled_messages[-1].id == "u2"


def test_context_compaction_keeps_only_complete_recent_turns_and_builds_record():
    class ExactEstimator:
        estimated = False

        def estimate_messages(self, messages):
            return len(messages) * 10

    summary = {
        "目标": "保留目标",
        "偏好": "保留偏好",
        "已完成": "第一轮完成",
        "关键决策": "采用 JSONL",
        "未完成": "继续验证",
        "关键证据": "测试证据",
    }
    messages = [
        AgentMessage(id="u1", role="user", content="旧问题", timestamp=1.0),
        AgentMessage(id="a1", role="assistant", content="旧答复", timestamp=2.0),
        AgentMessage(id="t1", role="tool", content="旧证据", timestamp=3.0),
        AgentMessage(id="u2", role="user", content="当前问题", timestamp=4.0),
        AgentMessage(id="a2", role="assistant", content="当前答复", timestamp=5.0),
    ]
    assembler = ContextAssembler(keep_tokens=30, token_estimator=ExactEstimator())

    detailed = assembler.assemble_detailed(
        session_id="compact",
        messages=messages,
        model="test-model",
        model_capabilities={"context_window": 70, "max_output_tokens": 10},
        summary=summary,
        summarizer_usage={"input_tokens": 40, "output_tokens": 12},
    )

    assert detailed.context.compacted is True
    assert [message.id for message in detailed.kept_messages] == ["u2", "a2"]
    candidate = detailed.compaction_candidate
    assert candidate is not None
    assert candidate.summary == summary
    assert candidate.first_kept_entry_id == "u2"
    assert candidate.tokens_before == 70
    assert candidate.tokens_after == detailed.usage.used == 40
    assert candidate.model == "test-model"
    assert candidate.usage["output_tokens"] == 12
    assert candidate.fallback_used is False


def test_context_previous_compaction_reuses_summary_and_boundary():
    previous = {
        "summary": {
            "目标": "长期目标",
            "偏好": "简洁",
            "已完成": "旧任务",
            "关键决策": "保留决策",
            "未完成": "新任务",
            "关键证据": "旧证据",
        },
        "first_kept_entry_id": "u2",
    }
    messages = [
        AgentMessage(id="u1", role="user", content="已被摘要覆盖", timestamp=1.0),
        AgentMessage(id="a1", role="assistant", content="旧答复", timestamp=2.0),
        AgentMessage(id="u2", role="user", content="边界问题", timestamp=3.0),
        AgentMessage(id="a2", role="assistant", content="边界答复", timestamp=4.0),
    ]

    detailed = ContextAssembler().assemble_detailed(
        session_id="iterative",
        messages=messages,
        previous_compaction=previous,
    )

    assert detailed.context.compacted is False
    assert [message.id for message in detailed.kept_messages] == ["u2", "a2"]
    assert "保留决策" in detailed.context.sections["关键决策"]
    assert "已被摘要覆盖" not in [
        message.content for message in detailed.kept_messages
    ]


def test_invalid_model_summary_uses_six_section_deterministic_fallback():
    assembler = ContextAssembler(compact_at_tokens=10, keep_tokens=4)
    messages = [
        AgentMessage(
            id=f"u{i}",
            role="user",
            content=f"第{i}轮 必须采用证据",
            timestamp=float(i),
        )
        for i in range(20)
    ]

    detailed = assembler.assemble_detailed(
        session_id="fallback",
        messages=messages,
        summary="没有六段结构",
        fallback_reason="summarizer_validation_failed",
    )

    candidate = detailed.compaction_candidate
    assert candidate is not None
    assert tuple(candidate.summary) == assembler.SECTION_ORDER
    assert all(candidate.summary.values())
    assert candidate.fallback_used is True
    assert candidate.failure_reason == "summarizer_validation_failed"


def test_event_sequencer_emits_v1_monotonic_parented_frames():
    sequencer = EventSequencer(session_id="s1", run_id="r1")
    first = sequencer.frame("start", {"a": 1})
    second = sequencer.frame("delta")

    assert first.protocol_version == 1
    assert first.session_id == "s1"
    assert first.run_id == "r1"
    assert [first.sequence, second.sequence] == [1, 2]
    assert second.parent_id == first.id
    wire = sequencer.to_wire(second)
    assert wire["type"] == "delta"
    assert wire["protocol_version"] == 1


def test_abort_token_raises_after_abort():
    token = AbortToken()
    assert token.is_aborted() is False

    token.abort("停止")
    assert token.is_aborted() is True
    with pytest.raises(RuntimeError, match="停止"):
        token.raise_if_aborted()
