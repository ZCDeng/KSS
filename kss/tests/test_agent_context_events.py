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
