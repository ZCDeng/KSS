"""AgentRuntime-backed execution for non-deterministic research nodes."""

from __future__ import annotations

import asyncio
import json
import re
import threading
from pathlib import Path
from typing import Any

from kss.agent.service import KSSAgentService
from kss.agent.types import AgentEvent

_JSON_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)
_RESULT_KEYS = {
    "status",
    "claims",
    "evidence_refs",
    "artifact_refs",
    "open_questions",
    "warnings",
}


class AgentResearchTaskRunner:
    """Run one research node through the existing stateful AgentRuntime.

    The research overlay deliberately rejects every write request. Successful
    tool terminal events are returned to the coordinator for evidence capture;
    assistant prose by itself never becomes evidence.
    """

    def __init__(self, *, state_root: Path, project_root: Path) -> None:
        self._agent = KSSAgentService(state_root, project_root)
        self._active_sessions: dict[str, str] = {}
        self._lock = threading.Lock()

    def abort(self, goal_id: str, reason: str = "research_paused") -> bool:
        with self._lock:
            session_id = self._active_sessions.get(goal_id)
        if not session_id:
            return False
        run_id = self._agent.runtime.active_run_id(session_id)
        return bool(run_id and self._agent.abort(run_id, reason))

    def run(
        self,
        *,
        goal: dict[str, Any],
        task: dict[str, Any],
        attempt_id: str,
        dependency_summaries: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return asyncio.run(
            self._run_async(
                goal=goal,
                task=task,
                attempt_id=attempt_id,
                dependency_summaries=dependency_summaries,
            )
        )

    async def _run_async(
        self,
        *,
        goal: dict[str, Any],
        task: dict[str, Any],
        attempt_id: str,
        dependency_summaries: list[dict[str, Any]],
    ) -> dict[str, Any]:
        events: list[AgentEvent] = []

        async def emit(event: AgentEvent) -> None:
            events.append(event)

        async def reject_write(**_: Any) -> dict[str, Any]:
            return {
                "error": "research_read_only",
                "is_error": True,
                "hint": "深度研究节点只允许只读工具",
            }

        session_id = f"research-{goal['goal_id']}-{task['task_id']}"
        prompt = self._prompt(
            goal=goal,
            task=task,
            dependency_summaries=dependency_summaries,
        )
        with self._lock:
            self._active_sessions[str(goal["goal_id"])] = session_id
        try:
            try:
                result = await self._agent.run_turn(
                    session_id,
                    attempt_id,
                    prompt,
                    emit,
                    reject_write,
                )
            except Exception as exc:  # provider/runtime errors become durable incomplete results
                return self._incomplete(f"agent_runtime_error: {exc}")
        finally:
            with self._lock:
                self._active_sessions.pop(str(goal["goal_id"]), None)

        if result.status != "completed":
            return self._incomplete(
                result.error or result.termination_reason or result.status,
                usage=dict(result.usage),
            )
        assistant_text = next(
            (
                message.content
                for message in reversed(result.messages)
                if message.role == "assistant" and message.content.strip()
            ),
            "",
        )
        parsed = self._parse_result(assistant_text)
        if parsed is None:
            return self._incomplete(
                "task_result_schema_invalid",
                usage=dict(result.usage),
                events=events,
            )
        parsed["usage"] = dict(result.usage)
        parsed["_tool_evidence"] = self._tool_evidence(events)
        parsed["run_id"] = result.run_id
        return parsed

    def _prompt(
        self,
        *,
        goal: dict[str, Any],
        task: dict[str, Any],
        dependency_summaries: list[dict[str, Any]],
    ) -> str:
        controlled = {
            "objective": goal.get("objective"),
            "criterion": task.get("title"),
            "snapshot": goal.get("snapshot") or {},
            "dependencies": dependency_summaries,
            "allowed_tools": (task.get("payload") or {}).get("tool_whitelist") or [],
        }
        return (
            "你正在执行 KSS 深度研究任务节点。只允许使用只读工具；任何写操作都会被拒绝。"
            "会话历史、长期记忆、Skill 文本和模型自身知识不能充当已验证证据。"
            "只可引用本次成功工具结果中的来源。最终只输出一个 JSON 对象，不要 Markdown：\n"
            '{"status":"succeeded|incomplete","claims":[],"evidence_refs":[],'
            '"artifact_refs":[],"open_questions":[],"warnings":[]}\n'
            f"受控任务输入：{json.dumps(controlled, ensure_ascii=False, sort_keys=True)}"
        )

    def _parse_result(self, text: str) -> dict[str, Any] | None:
        match = _JSON_FENCE.match(text)
        candidate = match.group(1) if match else text
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            return None
        if not isinstance(value, dict) or value.get("status") not in {
            "succeeded",
            "incomplete",
        }:
            return None
        if not _RESULT_KEYS.issubset(value):
            return None
        if any(
            not isinstance(value[key], list)
            for key in _RESULT_KEYS - {"status"}
        ):
            return None
        return {key: value[key] for key in _RESULT_KEYS}

    def _tool_evidence(self, events: list[AgentEvent]) -> list[dict[str, Any]]:
        captured: list[dict[str, Any]] = []
        for event in events:
            if event.type != "tool_end" or event.payload.get("is_error"):
                continue
            drawer = event.payload.get("evidenceDrawer")
            if not isinstance(drawer, dict):
                continue
            sources = drawer.get("externalSources")
            if not isinstance(sources, list):
                continue
            for source in sources:
                if isinstance(source, dict) and source.get("url"):
                    captured.append(
                        {
                            **source,
                            "tool_name": event.payload.get("name"),
                            "tool_event_id": event.id,
                        }
                    )
        return captured

    def _incomplete(
        self,
        warning: str,
        *,
        usage: dict[str, Any] | None = None,
        events: list[AgentEvent] | None = None,
    ) -> dict[str, Any]:
        return {
            "status": "incomplete",
            "claims": [],
            "evidence_refs": [],
            "artifact_refs": [],
            "open_questions": [],
            "warnings": [warning],
            "usage": usage or {},
            "_tool_evidence": self._tool_evidence(events or []),
        }
