"""AgentRuntime-backed execution for non-deterministic research nodes."""

from __future__ import annotations

import asyncio
import json
import re
import threading
from pathlib import Path
from typing import Any

from kss.agent.service import KSSAgentService, RuntimeRunOptions
from kss.agent.types import AgentEvent
from kss.llm.sanitizer import sanitize_llm_input

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

        session_id = (
            f"research-{goal['goal_id']}-{task['task_id']}-{attempt_id}"
        )
        prompt = self._prompt(
            goal=goal,
            task=task,
            dependency_summaries=dependency_summaries,
        )
        with self._lock:
            self._active_sessions[str(goal["goal_id"])] = session_id
        payload = task.get("payload") or {}
        timeout_seconds = float(payload.get("timeout_seconds") or 240.0)
        token_budget = int(payload.get("max_provider_tokens") or 25_000)
        run_options = RuntimeRunOptions(
            allowed_tools=frozenset(payload.get("tool_whitelist") or []),
            allowed_skills=frozenset(payload.get("skill_whitelist") or []),
            allowed_memory_kinds=frozenset({"preference"}),
            max_steps=int(payload.get("max_steps") or 8),
            timeout_seconds=timeout_seconds,
            max_provider_tokens=token_budget,
            allow_write_tools=False,
            trusted_internal_input=True,
        )
        try:
            try:
                result = await asyncio.wait_for(
                    self._agent.run_turn(
                        session_id,
                        attempt_id,
                        prompt,
                        emit,
                        reject_write,
                        run_options=run_options,
                    ),
                    timeout=timeout_seconds + 5.0,
                )
            except TimeoutError:
                run_id = self._agent.runtime.active_run_id(session_id)
                if run_id:
                    self._agent.abort(run_id, "research_task_timeout")
                return self._incomplete("agent_runtime_timeout", events=events)
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
        tool_messages = result.messages
        initial_usage = dict(result.usage)
        assistant_text = next(
            (
                message.content
                for message in reversed(result.messages)
                if message.role == "assistant" and message.content.strip()
            ),
            "",
        )
        parsed = self._parse_result(assistant_text)
        combined_usage = initial_usage
        if parsed is None:
            repair_events: list[AgentEvent] = []

            async def emit_repair(event: AgentEvent) -> None:
                repair_events.append(event)

            repair_options = RuntimeRunOptions(
                allowed_tools=frozenset(),
                allowed_skills=frozenset(),
                allowed_memory_kinds=frozenset(),
                max_steps=1,
                timeout_seconds=min(timeout_seconds, 60.0),
                max_provider_tokens=min(token_budget, 5_000),
                allow_write_tools=False,
                trusted_internal_input=True,
            )
            try:
                repair = await asyncio.wait_for(
                    self._agent.run_turn(
                        session_id,
                        f"{attempt_id}-repair",
                        "上一条回复不符合任务结果契约。请仅按指定六个字段输出合法 JSON，不调用工具。",
                        emit_repair,
                        reject_write,
                        run_options=repair_options,
                    ),
                    timeout=repair_options.timeout_seconds + 5.0,
                )
            except Exception:
                repair = None
            events.extend(repair_events)
            if repair is not None and repair.status == "completed":
                repaired_text = next(
                    (
                        message.content
                        for message in reversed(repair.messages)
                        if message.role == "assistant" and message.content.strip()
                    ),
                    "",
                )
                parsed = self._parse_result(repaired_text)
            if parsed is None:
                return self._incomplete(
                    "task_result_schema_invalid_after_repair",
                    usage=self._merge_usage(
                        initial_usage,
                        dict(repair.usage) if repair is not None else {},
                    ),
                    events=events,
                )
            combined_usage = self._merge_usage(
                initial_usage, dict(repair.usage)
            )
            result = repair
        parsed["usage"] = combined_usage
        parsed["_tool_evidence"] = self._tool_evidence(events)
        parsed["_tool_results"] = self._tool_results(tool_messages)
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
            "objective": self._sanitize_controlled(goal.get("objective")),
            "criterion": self._sanitize_controlled(task.get("title")),
            "snapshot": self._sanitize_controlled(goal.get("snapshot") or {}),
            "dependencies": self._sanitize_controlled(dependency_summaries),
            "allowed_tools": (task.get("payload") or {}).get("tool_whitelist") or [],
        }
        structured_hint = ""
        metric_by_kind = {
            "compute_temperature": (
                "m_temperature",
                "temperature_index",
            ),
            "theme_consensus": ("m_consensus", "theme_consensus"),
            "risk_radar": ("m_risk", "risk_radar"),
        }
        if task.get("kind") in metric_by_kind:
            metric_id, formula_id = metric_by_kind[str(task["kind"])]
            structured_hint = (
                " 本节点的 claims 至少一项应包含 metric 对象："
                f'{{"metric_id":"{metric_id}","value":数字,"unit":"%",'
                f'"precision":1,"formula_id":"{formula_id}",'
                '"formula_version":"v1","formula_inputs":[参与计算的原始数字],'
                '"input_refs":[对应成功工具的 tool_call_id、工具名或来源 URL],'
                '"as_of":"YYYY-MM-DD"}。value 必须等于 formula_inputs 的算术平均，'
                "不得填写未出现在对应工具结果中的数字。"
            )
        return (
            "你正在执行 KSS 深度研究任务节点。只允许使用只读工具；任何写操作都会被拒绝。"
            "会话历史、长期记忆、Skill 文本和模型自身知识不能充当已验证证据。"
            "只可引用本次成功工具结果中的来源。最终只输出一个 JSON 对象，不要 Markdown：\n"
            '{"status":"succeeded|incomplete","claims":[],"evidence_refs":[],'
            '"artifact_refs":[],"open_questions":[],"warnings":[]}\n'
            f"{structured_hint}"
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

    @staticmethod
    def _tool_results(messages: tuple[Any, ...]) -> list[dict[str, Any]]:
        """Capture successful scrubbed tool results from the runtime transcript.

        This stays internal to the research coordinator. It is not emitted on
        the public event stream, and only its content hash plus bounded numeric
        lineage enters the Evidence Ledger.
        """

        captured: list[dict[str, Any]] = []
        for message in messages:
            if getattr(message, "role", None) != "tool":
                continue
            for call in getattr(message, "tool_calls", ()):
                result = getattr(call, "result", None)
                error = getattr(call, "error", None)
                if error or not isinstance(result, dict):
                    continue
                captured.append(
                    {
                        "tool_name": str(getattr(call, "name", None) or "tool"),
                        "tool_call_id": str(
                            getattr(call, "id", None) or message.id
                        ),
                        "result": result,
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
            "_tool_results": [],
        }

    @staticmethod
    def _merge_usage(*items: dict[str, Any]) -> dict[str, Any]:
        merged: dict[str, Any] = {}
        for usage in items:
            for key, value in usage.items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    merged[key] = merged.get(key, 0) + value
                elif key not in merged:
                    merged[key] = value
        return merged

    @classmethod
    def _sanitize_controlled(cls, value: Any) -> Any:
        if isinstance(value, str):
            return sanitize_llm_input(value, max_len=2_000)
        if isinstance(value, list):
            return [cls._sanitize_controlled(item) for item in value[:50]]
        if isinstance(value, dict):
            return {
                str(key)[:128]: cls._sanitize_controlled(item)
                for key, item in list(value.items())[:100]
            }
        if value is None or isinstance(value, (bool, int, float)):
            return value
        return sanitize_llm_input(str(value), max_len=2_000)
