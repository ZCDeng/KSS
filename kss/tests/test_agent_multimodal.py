from __future__ import annotations

import json

from kss.agent import (
    AgentContentBlock,
    AgentMessage,
    ContextAssembler,
    SessionStore,
    convert_to_llm,
)


def test_legacy_string_message_keeps_provider_contract():
    message = AgentMessage(
        id="legacy",
        role="user",
        content="旧客户端文本",
        timestamp=1.0,
    )

    assert message.content == "旧客户端文本"
    assert message.content_blocks == ()
    assert message.blocks == (
        AgentContentBlock(type="text", text="旧客户端文本"),
    )
    assert convert_to_llm(message) == {
        "role": "user",
        "content": "旧客户端文本",
    }


def test_structured_content_keeps_visible_text_and_excludes_thinking_by_default():
    message = AgentMessage(
        id="structured",
        role="assistant",
        content=(
            AgentContentBlock(
                type="thinking",
                text="provider reasoning",
                content_index=0,
                signature="signed",
                provider="anthropic",
                model="claude",
            ),
            AgentContentBlock(type="text", text="最终", content_index=1),
            AgentContentBlock(type="text", text="答案", content_index=2),
        ),
        timestamp=2.0,
    )

    assert message.content == "最终答案"
    assert convert_to_llm(message)["content"] == "最终答案"
    assert convert_to_llm(
        message,
        include_thinking=True,
        provider="anthropic",
        model="claude",
    )["content"] == [
        {
            "type": "thinking",
            "text": "provider reasoning",
            "signature": "signed",
            "content_index": 0,
        },
        {"type": "text", "text": "最终", "content_index": 1},
        {"type": "text", "text": "答案", "content_index": 2},
    ]
    assert convert_to_llm(
        message,
        include_thinking=True,
        provider="openai",
        model="gpt",
    )["content"] == "最终答案"


def test_image_and_attachment_refs_use_provider_neutral_blocks():
    message = AgentMessage(
        id="vision",
        role="user",
        content="请看图",
        timestamp=1.0,
        content_blocks=(
            AgentContentBlock(type="text", text="请看图"),
            AgentContentBlock(
                type="image",
                attachment_id="att_image",
                mime_type="image/png",
            ),
            AgentContentBlock(
                type="attachment_ref",
                attachment_id="att_pdf",
                mime_type="application/pdf",
            ),
        ),
    )

    assert convert_to_llm(message)["content"] == [
        {"type": "text", "text": "请看图"},
        {
            "type": "image",
            "attachment_id": "att_image",
            "mime_type": "image/png",
        },
        {
            "type": "attachment_ref",
            "attachment_id": "att_pdf",
            "mime_type": "application/pdf",
        },
    ]


def test_session_store_round_trips_content_blocks_without_raw_attachment_bytes(tmp_path):
    store = SessionStore(tmp_path)
    store.create_session(session_id="blocks")
    message = AgentMessage(
        id="m1",
        role="assistant",
        content="可见答案",
        timestamp=1.0,
        content_blocks=(
            AgentContentBlock(
                type="thinking",
                text="思考",
                signature="sig",
                provider="provider-a",
                model="model-a",
            ),
            AgentContentBlock(type="text", text="可见答案"),
            AgentContentBlock(
                type="image",
                attachment_id="att_123",
                mime_type="image/png",
            ),
        ),
    )

    store.append_message("blocks", message)
    loaded = store.read_messages("blocks")[0]

    assert loaded.content == "可见答案"
    assert loaded.content_blocks == message.content_blocks
    path = tmp_path / "storage" / "agent" / "sessions" / "blocks.jsonl"
    entries = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
    ]
    payload = entries[-1]["payload"]
    assert payload["content"] == "可见答案"
    assert payload["content_blocks"][2]["attachment_id"] == "att_123"
    assert "base64" not in json.dumps(payload)


def test_thinking_is_not_in_compaction_source():
    assembler = ContextAssembler(
        token_budget=64,
        reserve_tokens=8,
        compact_at_tokens=1,
        keep_tokens=1,
    )
    messages = [
        AgentMessage(
            id="u1",
            role="user",
            content="旧问题",
            timestamp=1.0,
        ),
        AgentMessage(
            id="a1",
            role="assistant",
            content=(
                AgentContentBlock(type="thinking", text="不能进入摘要的思考"),
                AgentContentBlock(type="text", text="可见答复"),
            ),
            timestamp=2.0,
        ),
        AgentMessage(
            id="u2",
            role="user",
            content="当前问题",
            timestamp=3.0,
        ),
    ]

    assembly = assembler.assemble_detailed(
        session_id="thinking-compaction",
        messages=messages,
    )

    assert "不能进入摘要的思考" not in assembly.compaction_source
    assert "可见答复" in assembly.compaction_source
