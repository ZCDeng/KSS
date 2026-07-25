"""Agent Core 的公共数据类型."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


MemoryKind = Literal["preference", "decision", "thesis"]
MemoryStatus = Literal["proposed", "approved", "archived", "deleted"]
SessionStatus = Literal["running", "completed", "interrupted", "archived", "deleted"]


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
class AgentMessage:
    """Agent 对话消息.

    Args:
        id: 消息 ID。
        role: 消息角色。
        content: 文本内容。
        timestamp: Unix 秒级时间戳。
        tool_calls: 消息关联的工具调用。
        metadata: 扩展元数据。
    """

    id: str
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    timestamp: float
    tool_calls: tuple[ToolCall, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


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
