"""Agent Core 上下文组装器."""

from __future__ import annotations

from kss.agent.types import AgentMessage, Context


class ContextAssembler:
    """32k 上下文预算组装器.

    默认预留 8k 输出 token；当输入超过 24k 可用 token 时，压缩保留约 8k token。
    token 估算使用稳定的字符近似，保证无第三方依赖。
    """

    SECTION_ORDER = ("目标", "偏好", "已完成", "关键决策", "未完成", "关键证据")

    def __init__(
        self,
        *,
        token_budget: int = 32_000,
        reserve_tokens: int = 8_000,
        compact_at_tokens: int = 24_000,
        keep_tokens: int = 8_000,
    ) -> None:
        """初始化组装器."""
        self.token_budget = token_budget
        self.reserve_tokens = reserve_tokens
        self.compact_at_tokens = compact_at_tokens
        self.keep_tokens = keep_tokens

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
    ) -> Context:
        """组装上下文.

        Args:
            session_id: 会话 ID。
            messages: 对话消息。
            skills: 已注入技能摘要。
            memories: 召回记忆摘要。
            goal: 当前目标。
            preferences: 用户偏好和长期约束。
            completed: 已完成事项。
            decisions: 关键决策。
            unfinished: 未完成事项。
            evidence: 关键证据。

        Returns:
            可注入模型的上下文对象。
        """
        skills = skills or []
        memories = memories or []
        recent = "\n".join(f"{m.role}: {m.content}" for m in messages)
        sections = {
            "目标": goal.strip() or "未指定",
            "偏好": "\n".join((preferences or []) + memories) or "无偏好",
            "已完成": "\n".join(completed or []) or self._completed(messages),
            "关键决策": "\n".join(decisions or []) or "无关键决策",
            "未完成": "\n".join(unfinished or []) or "无未完成事项",
            "关键证据": "\n".join((evidence or []) + skills + [self._known_facts(messages)]).strip()
            or "无关键证据",
        }
        text = self._render(sections)
        compacted = False
        if self._estimate_tokens(text) + self._estimate_tokens(recent) > self.compact_at_tokens:
            sections["关键证据"] = (
                f"{sections['关键证据']}\n\n历史摘要:\n{self.fallback_summary(recent)}"
            )
            sections = self._compact_sections(sections)
            text = self._render(sections)
            compacted = True
        return Context(
            session_id=session_id,
            text=text,
            token_budget=self.token_budget,
            reserve_tokens=self.reserve_tokens,
            compacted=compacted,
            sections=sections,
            messages=self.build_messages(sections),
        )

    def fallback_summary(self, text: str) -> str:
        """生成确定性的六段式摘要兜底."""
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        parts = {
            "目标": self._slice_lines(lines, 0, 4),
            "偏好": self._filter_lines(lines, ("偏好", "必须", "不要", "only")),
            "已完成": self._filter_lines(lines, ("已完成", "完成", "done", "passed")),
            "关键决策": self._filter_lines(lines, ("决策", "采用", "decision")),
            "未完成": self._filter_lines(lines, ("未完成", "todo", "剩余", "failed")),
            "关键证据": self._slice_lines(lines, max(0, len(lines) - 8), len(lines)),
        }
        return self._render({key: value or "无" for key, value in parts.items()})

    def build_messages(self, sections: dict[str, str]) -> tuple[AgentMessage, ...]:
        """把六段上下文转换为有序 LLM 消息."""
        content = self._render({key: sections.get(key, "") for key in self.SECTION_ORDER})
        return (
            AgentMessage(
                id="context-system",
                role="system",
                content="以下是 Agent Core 组装的会话上下文，请按证据继续执行。",
                timestamp=0.0,
            ),
            AgentMessage(id="context-user", role="user", content=content, timestamp=0.0),
        )

    def _compact_sections(self, sections: dict[str, str]) -> dict[str, str]:
        budget_chars = self.keep_tokens * 4
        fixed = {key: sections.get(key, "") for key in self.SECTION_ORDER if key != "关键证据"}
        fixed_text = self._render(fixed)
        remaining = max(1_000, budget_chars - len(fixed_text))
        recent = sections.get("关键证据", "")
        compact_recent = recent[-remaining:]
        compact = dict(fixed)
        compact["关键证据"] = self.fallback_summary(compact_recent)
        return {key: compact.get(key, "") for key in self.SECTION_ORDER}

    def _known_facts(self, messages: list[AgentMessage]) -> str:
        facts = [m.content for m in messages if m.role == "tool" or m.metadata.get("fact")]
        return "\n".join(facts[-20:]) or "无结构化事实"

    def _completed(self, messages: list[AgentMessage]) -> str:
        done = [m.content for m in messages if m.metadata.get("completed")]
        return "\n".join(done[-20:]) or "无已完成事项"

    def _render(self, sections: dict[str, str]) -> str:
        return "\n\n".join(f"## {key}\n{sections.get(key, '').strip()}" for key in self.SECTION_ORDER)

    def _estimate_tokens(self, text: str) -> int:
        return max(1, (len(text) + 3) // 4)

    def _slice_lines(self, lines: list[str], start: int, end: int) -> str:
        return "\n".join(lines[start:end])

    def _filter_lines(self, lines: list[str], needles: tuple[str, ...]) -> str:
        matched = [line for line in lines if any(needle.lower() in line.lower() for needle in needles)]
        return "\n".join(matched[:12])
