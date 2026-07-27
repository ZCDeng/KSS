"""Agent Core 的公共数据类型."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Literal, Mapping

MemoryKind = Literal["preference", "decision", "thesis"]
MemoryStatus = Literal["proposed", "approved", "archived", "deleted"]
SessionStatus = Literal["running", "completed", "interrupted", "archived", "deleted"]
RuntimeStatus = Literal["starting", "running", "completed", "failed", "aborted", "interrupted"]
RunTerminalStatus = Literal["completed", "failed", "aborted", "interrupted"]
QueuedInputMode = Literal["steering", "follow_up"]
QueuedInputStatus = Literal["queued", "restored", "applied", "discarded"]
AgentContentType = Literal[
    "text",
    "thinking",
    "image",
    "attachment_ref",
    "tool_call",
]


@dataclass(frozen=True)
class ToolCall:
    """工具调用记录.

    Args:
        id: 工具调用 ID。
        name: 工具名称。
        arguments: 结构化参数。
        result: 工具返回值。
        error: 工具错误信息。
    """

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    result: Any | None = None
    error: str | None = None


@dataclass(frozen=True)
class AgentContentBlock:
    """Provider-neutral message content block.

    Attachment and image blocks contain durable object references, never raw
    file bytes or base64. ``thinking`` is kept separate from visible text so it
    cannot accidentally enter compaction, memories, or evidence.
    """

    type: AgentContentType
    text: str | None = None
    content_index: int | None = None
    signature: str | None = None
    redacted: bool = False
    provider: str | None = None
    model: str | None = None
    attachment_id: str | None = None
    mime_type: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.type not in {
            "text",
            "thinking",
            "image",
            "attachment_ref",
            "tool_call",
        }:
            raise ValueError(f"unsupported content block type: {self.type}")
        if self.content_index is not None and self.content_index < 0:
            raise ValueError("content_index must be non-negative")
        if self.type in {"text", "thinking"} and self.text is None:
            raise ValueError(f"{self.type} content block requires text")
        if self.type in {"image", "attachment_ref"} and not self.attachment_id:
            raise ValueError(f"{self.type} content block requires attachment_id")

    def to_payload(self) -> dict[str, Any]:
        """Return the additive v1 JSON representation."""
        payload: dict[str, Any] = {"type": self.type}
        for key in (
            "text",
            "content_index",
            "signature",
            "provider",
            "model",
            "attachment_id",
            "mime_type",
        ):
            value = getattr(self, key)
            if value is not None:
                payload[key] = value
        if self.redacted:
            payload["redacted"] = True
        if self.metadata:
            payload["metadata"] = dict(self.metadata)
        return payload

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> AgentContentBlock:
        """Decode a content block while ignoring unknown additive fields."""
        metadata = payload.get("metadata")
        return cls(
            type=str(payload["type"]),  # type: ignore[arg-type]
            text=_optional_string(payload.get("text")),
            content_index=_optional_int(payload.get("content_index")),
            signature=_optional_string(payload.get("signature")),
            redacted=bool(payload.get("redacted", False)),
            provider=_optional_string(payload.get("provider")),
            model=_optional_string(payload.get("model")),
            attachment_id=_optional_string(payload.get("attachment_id")),
            mime_type=_optional_string(payload.get("mime_type")),
            metadata=dict(metadata) if isinstance(metadata, Mapping) else {},
        )


@dataclass(frozen=True)
class AgentMessage:
    """Agent 对话消息.

    Args:
        id: 消息 ID。
        role: 消息角色。
        content: 向旧调用方暴露的可见文本。构造时也接受 content block iterable。
        timestamp: Unix 秒级时间戳。
        tool_calls: 消息关联的工具调用。
        content_blocks: 有序 provider-neutral 内容块。
        metadata: 扩展元数据。
    """

    id: str
    role: Literal["system", "user", "assistant", "tool"]
    content: str | Iterable[AgentContentBlock | Mapping[str, Any]]
    timestamp: float
    tool_calls: tuple[ToolCall, ...] = ()
    content_blocks: tuple[AgentContentBlock, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        content = self.content
        blocks = tuple(self.content_blocks)
        if isinstance(content, str):
            visible_text = content
        else:
            if blocks:
                raise ValueError(
                    "pass structured blocks via content or content_blocks, not both"
                )
            blocks = _coerce_content_blocks(content)
            visible_text = visible_text_from_blocks(blocks)
        if blocks:
            blocks = _coerce_content_blocks(blocks)
            if not visible_text:
                visible_text = visible_text_from_blocks(blocks)
        object.__setattr__(self, "content", visible_text)
        object.__setattr__(self, "content_blocks", blocks)

    @property
    def blocks(self) -> tuple[AgentContentBlock, ...]:
        """Return explicit blocks or a synthetic legacy text block."""
        if self.content_blocks:
            return self.content_blocks
        if self.content:
            return (AgentContentBlock(type="text", text=str(self.content)),)
        return ()



@dataclass(frozen=True)
class AgentState:
    """Agent 会话状态.

    Args:
        session_id: 会话 ID。
        status: 会话状态。
        cursor: 单调事件游标。
        active_skill_ids: 已启用技能 ID。
        pinned_skill_ids: 当前会话置顶技能 ID。
        metadata: 扩展元数据。
    """

    session_id: str
    status: SessionStatus = "running"
    cursor: int = 0
    active_skill_ids: tuple[str, ...] = ()
    pinned_skill_ids: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RuntimeState:
    """一次 Agent run 的可观察运行时状态.

    ``AgentState`` 描述可持久化的会话，而本类型描述一个正在执行或已经
    结束的 run。字段保持 provider-neutral；``abort_token`` 故意使用
    ``Any``，避免公共类型反向依赖事件实现。
    """

    run_id: str
    session_id: str
    client_turn_id: str
    model: str | None = None
    status: RuntimeStatus = "starting"
    messages: list[AgentMessage] = field(default_factory=list)
    tools: tuple[Any, ...] = ()
    streaming_message: AgentMessage | None = None
    pending_tool_calls: list[ToolCall] = field(default_factory=list)
    error: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    abort_token: Any | None = field(default=None, repr=False, compare=False)
    started_at: float = 0.0
    finished_at: float | None = None


@dataclass(frozen=True)
class RunResult:
    """Agent run 的终态结果.

    ``messages`` 是 persistence barrier 应当提交的最终快照。barrier
    成功后 Runtime 才会发送 ``turn_end`` 与 ``agent_end``。
    """

    run_id: str
    session_id: str
    client_turn_id: str
    status: RunTerminalStatus
    messages: tuple[AgentMessage, ...] = ()
    error: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    termination_reason: str | None = None


@dataclass(frozen=True)
class QueuedAgentInput:
    """活跃 run 的 steering / follow-up 用户输入.

    ``client_message_id`` 是 run 内幂等键；``id`` 是 append-only queue
    生命周期的稳定标识。崩溃恢复只把 ``status`` 从 ``queued`` 更新为
    ``restored``，不会创建第二个 queue item。
    """

    id: str
    client_message_id: str
    session_id: str
    run_id: str
    mode: QueuedInputMode
    content: str
    status: QueuedInputStatus = "queued"
    created_at: float = 0.0
    applied_at: float | None = None


@dataclass(frozen=True)
class AgentEvent:
    """Agent 事件帧.

    Args:
        id: 事件 ID。
        session_id: 会话 ID。
        run_id: 运行 ID。
        parent_id: 父事件 ID。
        timestamp: Unix 秒级时间戳。
        sequence: 会话内单调序号。
        type: 事件类型。
        payload: 事件负载。
        protocol_version: 协议版本。
    """

    id: str
    session_id: str
    run_id: str
    parent_id: str | None
    timestamp: float
    sequence: int
    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    protocol_version: int = 1


@dataclass(frozen=True)
class Context:
    """组装后的 Agent 上下文.

    Args:
        session_id: 会话 ID。
        text: 可注入模型的上下文文本。
        token_budget: 总 token 预算。
        reserve_tokens: 预留输出 token。
        compacted: 是否经过压缩。
        sections: 六段式上下文区块。
        messages: 可直接传给 LLM 的有序消息。
    """

    session_id: str
    text: str
    token_budget: int = 32_000
    reserve_tokens: int = 8_000
    compacted: bool = False
    sections: dict[str, str] = field(default_factory=dict)
    messages: tuple[AgentMessage, ...] = ()


def convert_to_llm(
    message: AgentMessage,
    *,
    include_thinking: bool = False,
    provider: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Convert a durable ``AgentMessage`` into the provider-facing contract.

    This is the explicit boundary between persisted/UI agent messages and the
    OpenAI-compatible message shape. Callers must pass messages through this
    function before sending them to a provider.
    """
    provider_content = _content_blocks_to_llm(
        message,
        include_thinking=include_thinking,
        provider=provider,
        model=model,
    )
    output: dict[str, Any] = {"role": message.role, "content": provider_content}
    if message.role == "tool":
        output["tool_call_id"] = message.metadata.get("tool_call_id") or message.id
        output["name"] = message.metadata.get("name") or "tool"
    if message.tool_calls and message.role == "assistant":
        output["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments, ensure_ascii=False),
                },
            }
            for call in message.tool_calls
        ]
    return output


def visible_text_from_blocks(
    blocks: Iterable[AgentContentBlock],
) -> str:
    """Render user-visible text without leaking provider thinking."""
    return "".join(
        block.text or ""
        for block in blocks
        if block.type == "text"
    )


def message_content_blocks_from_payload(
    payload: Any,
) -> tuple[AgentContentBlock, ...]:
    """Decode additive JSON content blocks, failing closed on malformed items."""
    if not isinstance(payload, list | tuple):
        return ()
    blocks: list[AgentContentBlock] = []
    for item in payload:
        if not isinstance(item, Mapping):
            continue
        try:
            blocks.append(AgentContentBlock.from_payload(item))
        except (KeyError, TypeError, ValueError):
            continue
    return tuple(blocks)


def _coerce_content_blocks(
    values: Iterable[AgentContentBlock | Mapping[str, Any]],
) -> tuple[AgentContentBlock, ...]:
    blocks: list[AgentContentBlock] = []
    for value in values:
        if isinstance(value, AgentContentBlock):
            blocks.append(value)
        elif isinstance(value, Mapping):
            blocks.append(AgentContentBlock.from_payload(value))
        else:
            raise TypeError("message content blocks must be mappings or AgentContentBlock")
    return tuple(blocks)


def _content_blocks_to_llm(
    message: AgentMessage,
    *,
    include_thinking: bool,
    provider: str | None,
    model: str | None,
) -> str | list[dict[str, Any]]:
    if not message.content_blocks:
        return str(message.content)

    output: list[dict[str, Any]] = []
    for block in message.content_blocks:
        if block.type == "thinking":
            if not include_thinking or block.redacted:
                continue
            if provider is not None and block.provider not in {None, provider}:
                continue
            if model is not None and block.model not in {None, model}:
                continue
            thinking: dict[str, Any] = {
                "type": "thinking",
                "text": block.text or "",
            }
            if block.signature:
                thinking["signature"] = block.signature
            if block.content_index is not None:
                thinking["content_index"] = block.content_index
            output.append(thinking)
        elif block.type == "text":
            item: dict[str, Any] = {"type": "text", "text": block.text or ""}
            if block.content_index is not None:
                item["content_index"] = block.content_index
            output.append(item)
        elif block.type in {"image", "attachment_ref"}:
            item = {
                "type": block.type,
                "attachment_id": block.attachment_id,
            }
            if block.mime_type:
                item["mime_type"] = block.mime_type
            if block.content_index is not None:
                item["content_index"] = block.content_index
            output.append(item)
        elif block.type == "tool_call":
            # Tool calls retain their established top-level provider contract.
            continue

    if not output:
        return ""
    if all(item["type"] == "text" for item in output):
        return "".join(str(item["text"]) for item in output)
    return output


def _optional_string(value: Any) -> str | None:
    return str(value) if value is not None else None


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)
