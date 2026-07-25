"""Agent Core 上下文预算、完整 turn 裁剪与压缩元数据."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Protocol, Sequence

from kss.agent.types import AgentMessage, Context


class ModelCapabilities(Protocol):
    """模型能力的鸭子接口；provider 可返回对象或同名字段 mapping."""

    context_window: int
    max_output_tokens: int


class TokenEstimator(Protocol):
    """token 估算器鸭子接口."""

    estimated: bool

    def estimate_text(self, text: str) -> int:
        """估算一段文本的 token 数."""


class DeterministicTokenEstimator:
    """无依赖的稳定字符估算器."""

    estimated = True

    def estimate_text(self, text: str) -> int:
        """按四字符一 token 估算，空文本为零."""
        return (len(text) + 3) // 4 if text else 0


@dataclass(frozen=True)
class ContextUsage:
    """实际组装消息的输入预算统计."""

    used: int
    limit: int
    context_window: int
    reserve_tokens: int
    percent: float
    estimated: bool

    def to_dict(self) -> dict[str, Any]:
        """转换为 protocol 可直接序列化的结构."""
        return asdict(self)


@dataclass(frozen=True)
class CompactionRecord:
    """可持久化为正式 ``compaction`` entry 的候选."""

    summary: dict[str, str]
    first_kept_entry_id: str | None
    tokens_before: int
    tokens_after: int
    model: str
    usage: dict[str, Any] = field(default_factory=dict)
    fallback_used: bool = False
    failure_reason: str | None = None

    def to_payload(self) -> dict[str, Any]:
        """转换为 JSONL payload."""
        return asdict(self)


@dataclass(frozen=True)
class ContextAssembly:
    """带预算和压缩候选的完整组装结果."""

    context: Context
    usage: ContextUsage
    compaction_candidate: CompactionRecord | None
    kept_messages: tuple[AgentMessage, ...]
    compaction_source: str = ""

    @property
    def assembled_messages(self) -> tuple[AgentMessage, ...]:
        """返回 Runtime 实际应发送的 context + history 消息."""
        return (*self.context.messages, *self.kept_messages)


class ContextAssembler:
    """按模型预算组装上下文，并只在完整 turn 边界压缩."""

    SECTION_ORDER = ("目标", "偏好", "已完成", "关键决策", "未完成", "关键证据")

    def __init__(
        self,
        *,
        token_budget: int = 32_000,
        reserve_tokens: int = 8_000,
        compact_at_tokens: int | None = None,
        keep_tokens: int = 8_000,
        model_capabilities: ModelCapabilities | Mapping[str, Any] | None = None,
        token_estimator: TokenEstimator | Any | None = None,
    ) -> None:
        """初始化组装器；显式构造参数覆盖模型能力默认值."""
        self.token_budget = token_budget
        self.reserve_tokens = reserve_tokens
        self.compact_at_tokens = compact_at_tokens
        self.keep_tokens = keep_tokens
        self.model_capabilities = model_capabilities
        self.token_estimator = token_estimator or DeterministicTokenEstimator()

    def assemble(
        self,
        *,
        session_id: str,
        messages: list[AgentMessage],
        skills: list[str] | None = None,
        memories: list[str] | None = None,
        goal: str = "",
        preferences: list[str] | None = None,
        completed: list[str] | None = None,
        decisions: list[str] | None = None,
        unfinished: list[str] | None = None,
        evidence: list[str] | None = None,
        **kwargs: Any,
    ) -> Context:
        """兼容旧调用，返回原有 ``Context``."""
        return self.assemble_detailed(
            session_id=session_id,
            messages=messages,
            skills=skills,
            memories=memories,
            goal=goal,
            preferences=preferences,
            completed=completed,
            decisions=decisions,
            unfinished=unfinished,
            evidence=evidence,
            **kwargs,
        ).context

    def assemble_detailed(
        self,
        *,
        session_id: str,
        messages: list[AgentMessage],
        skills: list[str] | None = None,
        memories: list[str] | None = None,
        goal: str = "",
        preferences: list[str] | None = None,
        completed: list[str] | None = None,
        decisions: list[str] | None = None,
        unfinished: list[str] | None = None,
        evidence: list[str] | None = None,
        model: str = "",
        model_capabilities: ModelCapabilities | Mapping[str, Any] | None = None,
        token_estimator: TokenEstimator | Any | None = None,
        previous_compaction: Mapping[str, Any] | None = None,
        summary: str | Mapping[str, Any] | None = None,
        summarizer_model: str | None = None,
        summarizer_usage: Mapping[str, Any] | None = None,
        fallback_reason: str | None = None,
    ) -> ContextAssembly:
        """组装 Runtime 可直接发送的消息，并返回准确覆盖最终消息的 usage."""
        estimator = token_estimator or self.token_estimator
        context_window, reserve, input_limit = self._resolve_budget(
            model_capabilities or self.model_capabilities
        )
        previous_sections = self._summary_sections(
            (previous_compaction or {}).get("summary")
            if isinstance(previous_compaction, Mapping)
            else None
        )
        effective_messages = self._messages_from_previous_boundary(
            messages, previous_compaction
        )
        sections = self._build_sections(
            effective_messages,
            skills=skills or [],
            memories=memories or [],
            goal=goal,
            preferences=preferences or [],
            completed=completed or [],
            decisions=decisions or [],
            unfinished=unfinished or [],
            evidence=evidence or [],
            previous_sections=previous_sections,
        )
        context_messages = self.build_messages(sections)
        tokens_before = self._estimate_messages(
            (*context_messages, *effective_messages), estimator
        )
        compact_at = min(
            input_limit,
            self.compact_at_tokens
            if self.compact_at_tokens is not None
            else input_limit,
        )
        compacted = tokens_before > compact_at
        kept_messages = tuple(effective_messages)
        candidate: CompactionRecord | None = None
        compaction_source = ""

        if compacted:
            keep_budget = min(self.keep_tokens, input_limit)
            kept_messages = self._keep_recent_complete_turns(
                effective_messages, keep_budget, estimator
            )
            dropped_count = max(0, len(effective_messages) - len(kept_messages))
            dropped = effective_messages[:dropped_count]
            compaction_source = self._compaction_source(previous_sections, dropped)
            supplied_sections = self._summary_sections(summary)
            fallback_used = supplied_sections is None
            if supplied_sections is None:
                supplied_sections = self._fallback_sections(compaction_source)
                fallback_reason = fallback_reason or (
                    "summary_missing" if summary is None else "summary_invalid"
                )
            sections = supplied_sections
            context_messages = self.build_messages(sections)
            tokens_after = self._estimate_messages(
                (*context_messages, *kept_messages), estimator
            )
            candidate = CompactionRecord(
                summary=sections,
                first_kept_entry_id=(
                    self._message_entry_id(kept_messages[0])
                    if kept_messages
                    else None
                ),
                tokens_before=tokens_before,
                tokens_after=tokens_after,
                model=summarizer_model or model,
                usage=dict(summarizer_usage or {}),
                fallback_used=fallback_used,
                failure_reason=fallback_reason if fallback_used else None,
            )

        text = self._render(sections)
        context = Context(
            session_id=session_id,
            text=text,
            token_budget=context_window,
            reserve_tokens=reserve,
            compacted=compacted,
            sections=sections,
            messages=self.build_messages(sections),
        )
        used = self._estimate_messages(
            (*context.messages, *kept_messages), estimator
        )
        usage = ContextUsage(
            used=used,
            limit=input_limit,
            context_window=context_window,
            reserve_tokens=reserve,
            percent=min(100.0, (used / input_limit * 100.0) if input_limit else 100.0),
            estimated=bool(getattr(estimator, "estimated", True)),
        )
        if candidate is not None and candidate.tokens_after != used:
            candidate = CompactionRecord(
                summary=candidate.summary,
                first_kept_entry_id=candidate.first_kept_entry_id,
                tokens_before=candidate.tokens_before,
                tokens_after=used,
                model=candidate.model,
                usage=candidate.usage,
                fallback_used=candidate.fallback_used,
                failure_reason=candidate.failure_reason,
            )
        return ContextAssembly(
            context=context,
            usage=usage,
            compaction_candidate=candidate,
            kept_messages=kept_messages,
            compaction_source=compaction_source,
        )

    def fallback_summary(self, text: str) -> str:
        """生成确定性的六段式摘要兜底."""
        return self._render(self._fallback_sections(text))

    def build_messages(self, sections: dict[str, str]) -> tuple[AgentMessage, ...]:
        """把六段上下文转换为有序 LLM 消息."""
        content = self._render({key: sections.get(key, "") for key in self.SECTION_ORDER})
        return (
            AgentMessage(
                id="context-system",
                role="system",
                content=(
                    "以下是 Agent Core 组装的会话上下文，"
                    "请按证据继续执行。"
                ),
                timestamp=0.0,
            ),
            AgentMessage(id="context-user", role="user", content=content, timestamp=0.0),
        )

    def _resolve_budget(
        self, capabilities: ModelCapabilities | Mapping[str, Any] | None
    ) -> tuple[int, int, int]:
        context_window = self._capability_int(
            capabilities, ("context_window", "context_window_tokens"), self.token_budget
        )
        max_output = self._capability_int(
            capabilities, ("max_output_tokens", "max_output"), self.reserve_tokens
        )
        reserve = min(self.reserve_tokens, max_output, max(1, context_window - 1))
        return context_window, reserve, max(1, context_window - reserve)

    def _capability_int(
        self,
        capabilities: ModelCapabilities | Mapping[str, Any] | None,
        names: Sequence[str],
        default: int,
    ) -> int:
        if capabilities is None:
            return default
        for name in names:
            value = (
                capabilities.get(name)
                if isinstance(capabilities, Mapping)
                else getattr(capabilities, name, None)
            )
            if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                return value
        return default

    def _build_sections(
        self,
        messages: list[AgentMessage],
        *,
        skills: list[str],
        memories: list[str],
        goal: str,
        preferences: list[str],
        completed: list[str],
        decisions: list[str],
        unfinished: list[str],
        evidence: list[str],
        previous_sections: dict[str, str] | None,
    ) -> dict[str, str]:
        previous_sections = previous_sections or {}
        return {
            "目标": goal.strip() or previous_sections.get("目标") or "未指定",
            "偏好": self._join_nonempty(
                [previous_sections.get("偏好", ""), *preferences, *memories]
            )
            or "无偏好",
            "已完成": self._join_nonempty(
                [previous_sections.get("已完成", ""), *completed, self._completed(messages)]
            )
            or "无已完成事项",
            "关键决策": self._join_nonempty(
                [previous_sections.get("关键决策", ""), *decisions]
            )
            or "无关键决策",
            "未完成": self._join_nonempty(
                [previous_sections.get("未完成", ""), *unfinished]
            )
            or "无未完成事项",
            "关键证据": self._join_nonempty(
                [
                    previous_sections.get("关键证据", ""),
                    *evidence,
                    *skills,
                    self._known_facts(messages),
                ]
            )
            or "无关键证据",
        }

    def _messages_from_previous_boundary(
        self,
        messages: list[AgentMessage],
        previous_compaction: Mapping[str, Any] | None,
    ) -> list[AgentMessage]:
        if not previous_compaction:
            return list(messages)
        boundary = previous_compaction.get("first_kept_entry_id")
        if not boundary:
            return list(messages)
        for index, message in enumerate(messages):
            if self._message_entry_id(message) == boundary:
                return list(messages[index:])
        # 旧边界已被外部清理时，消息本身已是压缩后的尾部，
        # 不能再次丢弃。
        return list(messages)

    @staticmethod
    def _message_entry_id(message: AgentMessage) -> str:
        """返回真实 JSONL entry id；兼容内存/旧测试消息的 message id."""
        entry_id = message.metadata.get("session_entry_id")
        return str(entry_id) if entry_id else message.id

    def _keep_recent_complete_turns(
        self,
        messages: list[AgentMessage],
        budget: int,
        estimator: TokenEstimator | Any,
    ) -> tuple[AgentMessage, ...]:
        turns = self._complete_turns(messages)
        selected: list[list[AgentMessage]] = []
        used = 0
        for turn in reversed(turns):
            turn_tokens = self._estimate_messages(turn, estimator)
            if selected and used + turn_tokens > budget:
                break
            selected.insert(0, turn)
            used += turn_tokens
        return tuple(message for turn in selected for message in turn)

    def _complete_turns(self, messages: list[AgentMessage]) -> list[list[AgentMessage]]:
        turns: list[list[AgentMessage]] = []
        current: list[AgentMessage] = []
        for message in messages:
            if message.role == "user" and current:
                turns.append(current)
                current = []
            current.append(message)
        if current:
            turns.append(current)
        return turns

    def _compaction_source(
        self,
        previous_sections: dict[str, str] | None,
        dropped: Sequence[AgentMessage],
    ) -> str:
        parts: list[str] = []
        if previous_sections:
            parts.append("上一版压缩摘要:\n" + self._render(previous_sections))
        if dropped:
            parts.append(
                "本次纳入压缩的完整 turns:\n"
                + "\n".join(f"{m.role}: {m.content}" for m in dropped)
            )
        return "\n\n".join(parts)

    def _summary_sections(
        self, summary: str | Mapping[str, Any] | None
    ) -> dict[str, str] | None:
        if isinstance(summary, Mapping):
            sections = {
                key: str(summary.get(key, "")).strip() for key in self.SECTION_ORDER
            }
        elif isinstance(summary, str):
            sections = self._parse_rendered_summary(summary)
        else:
            return None
        if any(not sections.get(key) for key in self.SECTION_ORDER):
            return None
        return sections

    def _parse_rendered_summary(self, summary: str) -> dict[str, str]:
        parsed: dict[str, str] = {}
        current: str | None = None
        buffers: dict[str, list[str]] = {}
        for raw_line in summary.splitlines():
            line = raw_line.strip()
            heading = line.removeprefix("## ").strip() if line.startswith("## ") else None
            if heading in self.SECTION_ORDER:
                current = heading
                buffers.setdefault(current, [])
            elif current is not None and line:
                buffers[current].append(line)
        for key, lines in buffers.items():
            parsed[key] = "\n".join(lines).strip()
        return parsed

    def _fallback_sections(self, text: str) -> dict[str, str]:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        parts = {
            "目标": self._slice_lines(lines, 0, 4),
            "偏好": self._filter_lines(lines, ("偏好", "必须", "不要", "only")),
            "已完成": self._filter_lines(lines, ("已完成", "完成", "done", "passed")),
            "关键决策": self._filter_lines(lines, ("决策", "采用", "decision")),
            "未完成": self._filter_lines(lines, ("未完成", "todo", "剩余", "failed")),
            "关键证据": self._slice_lines(lines, max(0, len(lines) - 8), len(lines)),
        }
        return {key: value or "无" for key, value in parts.items()}

    def _estimate_messages(
        self,
        messages: Sequence[AgentMessage],
        estimator: TokenEstimator | Any,
    ) -> int:
        estimate_messages = getattr(estimator, "estimate_messages", None)
        if callable(estimate_messages):
            return max(0, int(estimate_messages(messages)))
        return sum(
            self._estimate_text(self._message_text(message), estimator)
            for message in messages
        )

    def _estimate_text(self, text: str, estimator: TokenEstimator | Any) -> int:
        estimate_text = getattr(estimator, "estimate_text", None)
        if callable(estimate_text):
            return max(0, int(estimate_text(text)))
        if callable(estimator):
            return max(0, int(estimator(text)))
        return DeterministicTokenEstimator().estimate_text(text)

    def _message_text(self, message: AgentMessage) -> str:
        return json.dumps(
            {
                "role": message.role,
                "content": message.content,
                "tool_calls": [asdict(call) for call in message.tool_calls],
                "metadata": message.metadata,
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )

    def _known_facts(self, messages: list[AgentMessage]) -> str:
        facts = [m.content for m in messages if m.role == "tool" or m.metadata.get("fact")]
        return "\n".join(facts[-20:]) or "无结构化事实"

    def _completed(self, messages: list[AgentMessage]) -> str:
        done = [m.content for m in messages if m.metadata.get("completed")]
        return "\n".join(done[-20:])

    def _render(self, sections: Mapping[str, str]) -> str:
        return "\n\n".join(
            f"## {key}\n{str(sections.get(key, '')).strip()}" for key in self.SECTION_ORDER
        )

    def _join_nonempty(self, values: Sequence[str]) -> str:
        return "\n".join(str(value).strip() for value in values if str(value).strip())

    def _slice_lines(self, lines: list[str], start: int, end: int) -> str:
        return "\n".join(lines[start:end])

    def _filter_lines(self, lines: list[str], needles: tuple[str, ...]) -> str:
        matched = [
            line
            for line in lines
            if any(needle.lower() in line.lower() for needle in needles)
        ]
        return "\n".join(matched[:12])
