"""KSS-specific application service built on the provider-neutral AgentRuntime."""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping
from uuid import uuid4

from kss.agent.context import ContextAssembler, ContextAssembly
from kss.agent.jsonl import utc_timestamp
from kss.agent.memory_store import MemoryStore
from kss.agent.provider import OpenAICompatibleProvider
from kss.agent.runtime import AgentRuntime, RuntimeTurn
from kss.agent.session_store import RunAdmissionError, SessionStore
from kss.agent.skills import SkillManager
from kss.agent.types import AgentEvent, AgentMessage, RunResult, ToolCall, convert_to_llm
from kss.llm.chat_client import ChatClient, sanitize_user_text

EmitEvent = Callable[[AgentEvent], Awaitable[None]]
RequestWrite = Callable[..., Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class DuplicateTurn:
    """Durable idempotency decision for a client turn key."""

    status: str
    existing_run_id: str


class KSSAgentService:
    """Own sessions, context, Skills, memories and the model/tool loop.

    The sidecar supplies only transport callbacks: an event writer and the
    human-in-the-loop write gate.
    """

    def __init__(self, state_root: str | Path, project_root: str | Path) -> None:
        self.state_root = Path(state_root)
        self.project_root = Path(project_root)
        self.sessions = SessionStore(self.state_root)
        self.skills = SkillManager(self.project_root, self.state_root)
        self.memories = MemoryStore(self.state_root)
        self.provider = OpenAICompatibleProvider()
        self.model = os.getenv("KSS_LLM_MODEL") or ""
        self.assembler = ContextAssembler(
            model_capabilities=self.provider.model_capabilities(self.model)
        )
        self._request_writes: dict[tuple[str, str], RequestWrite] = {}
        self._transcripts: dict[str, Any] = {}
        self.runtime = AgentRuntime(
            self._execute_turn,
            model=self.model or None,
            model_resolver=lambda: os.getenv("KSS_LLM_MODEL") or None,
            message_loader=self.sessions.read_messages,
            run_admission=self._admit_run,
            persistence_barrier=self._persist_turn,
        )

    def duplicate_turn(self, session_id: str, client_turn_id: str) -> DuplicateTurn | None:
        """Return the durable disposition for an already-seen client turn."""
        record = self.sessions.find_run_by_client_turn_id(session_id, client_turn_id)
        if record is None:
            return None
        if (
            record.get("status") == "running"
            and record.get("owner_pid") == os.getpid()
            and self.runtime.active_run_id(session_id) != record.get("run_id")
        ):
            self.sessions.finish_run(
                session_id,
                str(record.get("run_id") or ""),
                status="interrupted",
                reason="recovered_incomplete_run",
            )
            record = self.sessions.find_run_by_client_turn_id(
                session_id, client_turn_id
            ) or record
        status = str(record.get("status") or "running")
        return DuplicateTurn(
            status=status,
            existing_run_id=str(record.get("run_id") or ""),
        )

    async def run_turn(
        self,
        session_id: str,
        client_turn_id: str,
        input: str,
        emit: EmitEvent,
        request_write: RequestWrite,
    ) -> RunResult:
        """Run one idempotency-checked turn through the shared Runtime."""
        key = (session_id, client_turn_id)
        self._request_writes[key] = request_write
        try:
            return await self.runtime.run_turn(
                session_id,
                client_turn_id,
                sanitize_user_text(input),
                emit,
            )
        finally:
            self._request_writes.pop(key, None)

    def abort(self, run_id: str, reason: str = "aborted") -> bool:
        """Forward an abort request to the shared Runtime."""
        return self.runtime.abort(run_id, reason)

    async def wait_for_idle(self, run_id: str) -> RunResult:
        """Wait until a run has crossed its persistence barrier."""
        return await self.runtime.wait_for_idle(run_id)

    def _admit_run(
        self, session_id: str, client_turn_id: str, run_id: str
    ) -> None:
        admission = self.sessions.try_start_run(
            session_id,
            run_id=run_id,
            client_turn_id=client_turn_id,
            orphaned_owner_pid=os.getpid(),
        )
        if not admission.admitted:
            raise RunAdmissionError(admission)

    async def _execute_turn(self, turn: RuntimeTurn) -> RunResult:
        import kss_chat_loop as chat_loop

        session_id = turn.state.session_id
        run_id = turn.state.run_id
        client_turn_id = turn.state.client_turn_id
        request_write = self._request_writes[(session_id, client_turn_id)]

        had_user = any(message.role == "user" for message in turn.messages[:-1])
        current_user = turn.messages[-1]
        self.sessions.append_message(session_id, current_user)
        if not had_user:
            title = current_user.content.strip().replace("\n", " ")[:40] or "新会话"
            self.sessions.rename_session(session_id, title)

        recall_items = self.memories.recall(
            turn.input, now_ms=int(time.time() * 1000), limit=5
        )
        if recall_items:
            self.sessions.append_entry(
                session_id, "recall", {"run_id": run_id, "items": recall_items}
            )
            await turn.emit(
                "recall",
                {"items": recall_items, "recalls": _recall_wire(recall_items)},
            )

        skill_status = self.skills.status()
        self.sessions.append_entry(
            session_id, "skill_index", {"run_id": run_id, "status": skill_status}
        )
        discovered = self.skills.discover()[0]
        skill_summaries = [
            f"{item.name}: {item.description}" for item in discovered if item.enabled
        ]
        pinned = set(self.skills.pinned_skill_ids(session_id))
        for skill in discovered:
            if skill.enabled and (skill.id in pinned or skill.name in pinned):
                skill_summaries.append(
                    f"置顶 Skill {skill.name}:\n{self.skills.load_skill(skill.id)}"
                )

        assembly = await self._assemble_context(
            turn,
            skills=skill_summaries,
            memories=recall_items,
        )
        await turn.emit(
            "context_usage",
            {"context_usage": _usage_wire(assembly.usage.to_dict())},
        )
        registry = chat_loop.ToolRegistry()
        registry.register_handler(
            "load_skill",
            lambda args: {
                "skill_id": str(args.get("skill_id") or ""),
                "content": self.skills.load_skill(
                    str(args.get("skill_id") or "")
                ),
            },
        )

        async def propose_memory(args: dict[str, Any]) -> dict[str, Any]:
            kind = str(args.get("kind") or "preference")
            if kind not in {"preference", "decision", "thesis"}:
                raise ValueError("memory kind must be preference, decision, or thesis")
            record = self.memories.propose(
                kind,
                str(args.get("text") or ""),
                source_session=session_id,
                source_entry=run_id,
                metadata={"source": str(args.get("source") or "agent")},
            )
            payload = _memory_wire(record)
            self.sessions.append_entry(
                session_id,
                "memory_candidate",
                {"run_id": run_id, "memory": payload},
            )
            await turn.emit("memory_candidate", {"memory_candidate": payload})
            return {"memory": payload, "pending_approval": True}

        registry.register_handler("propose_memory", propose_memory)
        message_open = False

        async def runtime_write_gate(**kwargs: Any) -> dict[str, Any]:
            return await request_write(**kwargs, emit_event=turn.emit)

        async def emit_loop(event: dict[str, Any]) -> None:
            nonlocal message_open
            event_type = str(event.get("type") or "event")
            payload = {key: value for key, value in event.items() if key != "type"}
            if event_type == "chunk" and not message_open:
                message_open = True
                await turn.emit("message_start", {"role": "assistant"})
            if event_type == "done":
                if message_open:
                    message_open = False
                    await turn.emit("message_end", {"role": "assistant"})
                return
            await turn.emit(_loop_event_name(event_type), payload)

        history = [convert_to_llm(message) for message in assembly.kept_messages]
        transcript = await chat_loop.run_turn(
            history,
            emit_loop,
            runtime_write_gate,
            abort_token=turn.abort_token,
            tool_registry=registry,
            transform_context=lambda convo: _merge_context(assembly, convo),
        )
        self._transcripts[run_id] = transcript
        usage = transcript.run_state.get("usage")
        if isinstance(usage, dict):
            turn.add_usage(**usage)
        new_messages = _new_transcript_messages(
            transcript.messages,
            current_user.content,
            run_id=run_id,
        )
        for message in new_messages:
            turn.append_message(message)
        reason = str(transcript.run_state.get("reason") or "completed")
        failed = reason == "error"
        return RunResult(
            run_id=run_id,
            session_id=session_id,
            client_turn_id=client_turn_id,
            status="failed" if failed else "completed",
            messages=tuple(turn.messages),
            error="provider stream failed" if failed else None,
            usage=dict(turn.state.usage),
            termination_reason=reason,
        )

    async def _assemble_context(
        self,
        turn: RuntimeTurn,
        *,
        skills: list[str],
        memories: list[str],
    ) -> ContextAssembly:
        previous = self.sessions.latest_compaction(turn.state.session_id)
        kwargs = {
            "session_id": turn.state.session_id,
            "messages": list(turn.messages),
            "skills": skills,
            "memories": memories,
            "goal": turn.input,
            "model": turn.model or self.model,
            "model_capabilities": self.provider.model_capabilities(
                turn.model or self.model
            ),
            "previous_compaction": previous,
        }
        probe = self.assembler.assemble_detailed(**kwargs)
        if probe.compaction_candidate is None:
            return probe

        await turn.emit(
            "compaction_start",
            {
                "reason": "context_budget",
                "context_usage": _usage_wire(probe.usage.to_dict()),
            },
        )
        summary: Mapping[str, Any] | None = None
        summary_usage: dict[str, Any] = {}
        fallback_reason: str | None = None
        try:
            summary, summary_usage = await asyncio.wait_for(
                self._summarize(probe.compaction_source, turn.abort_token),
                timeout=30.0,
            )
        except asyncio.TimeoutError:
            fallback_reason = "summary_timeout"
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            fallback_reason = f"summary_failed:{type(exc).__name__}:{exc}"
        assembly = self.assembler.assemble_detailed(
            **kwargs,
            summary=summary,
            summarizer_model=turn.model or self.model,
            summarizer_usage=summary_usage,
            fallback_reason=fallback_reason,
        )
        if assembly.compaction_candidate is not None:
            self.sessions.append_compaction(
                turn.state.session_id,
                assembly.compaction_candidate,
                run_id=turn.state.run_id,
            )
            await turn.emit(
                "compaction_end",
                {
                    "context_usage": _usage_wire(assembly.usage.to_dict()),
                    "compaction": assembly.compaction_candidate.to_payload(),
                },
            )
        return assembly

    async def _summarize(
        self, source: str, abort_token: Any
    ) -> tuple[dict[str, str], dict[str, Any]]:
        prompt = (
            "把以下旧会话压缩成严格 JSON object。只允许六个键："
            "目标、偏好、已完成、关键决策、未完成、关键证据。"
            "每个值必须是非空字符串，不得遗漏证据来源。\n\n" + source
        )

        def collect() -> tuple[str, dict[str, Any]]:
            client = ChatClient(
                model=os.getenv("KSS_LLM_MODEL") or None,
                timeout=30.0,
            )
            if hasattr(abort_token, "add_callback"):
                abort_token.add_callback(client.abort_active_stream)
            parts: list[str] = []
            usage: dict[str, Any] = {}
            for event in client.stream_turn(
                [{"role": "user", "content": prompt}], tools=None
            ):
                if event.get("type") == "text":
                    parts.append(str(event.get("text") or ""))
                elif event.get("type") == "usage":
                    usage.update(event.get("usage") or {})
                elif event.get("type") == "error":
                    raise RuntimeError(str(event.get("error") or "summary provider error"))
            return "".join(parts), usage

        text, usage = await asyncio.to_thread(collect)
        return _parse_summary(text), usage

    async def _persist_turn(self, turn: RuntimeTurn, result: RunResult) -> None:
        run_id = result.run_id
        try:
            for message in result.messages:
                if (
                    message.role in {"assistant", "tool"}
                    and message.metadata.get("run_id") == run_id
                ):
                    self.sessions.append_message(result.session_id, message)
            transcript = self._transcripts.pop(run_id, None)
            run_state = {
                "run_id": run_id,
                "session_id": result.session_id,
                "client_turn_id": result.client_turn_id,
                "status": result.status,
                "reason": result.termination_reason,
                "usage": dict(result.usage),
                "completed_at": time.time(),
            }
            self.sessions.append_entry(result.session_id, "run_state", run_state)
            if transcript is not None:
                self.sessions.append_entry(
                    result.session_id, "transcript", transcript.as_dict()
                )
            self.sessions.finish_run(
                result.session_id,
                run_id,
                status=result.status,
                reason=result.termination_reason or result.error,
                metadata={"usage": dict(result.usage)},
            )
        except Exception:
            # Best-effort emergency terminal: if the original failure was
            # transient, durable idempotency must not remain "running".
            try:
                self.sessions.finish_run(
                    result.session_id,
                    run_id,
                    status="failed",
                    reason="persistence_error",
                )
            except Exception:
                pass
            raise


def _loop_event_name(event_type: str) -> str:
    return {
        "chunk": "message_delta",
        "tool_call": "tool_start",
        "tool_done": "tool_end",
        "confirm_required": "confirm_required",
    }.get(event_type, event_type)


def _merge_context(
    assembly: ContextAssembly, conversation: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    system = [message for message in conversation if message.get("role") == "system"]
    context = [convert_to_llm(message) for message in assembly.context.messages]
    remaining = [
        message for message in conversation if message.get("role") != "system"
    ]
    return [*system, *context, *remaining]


def _new_transcript_messages(
    messages: list[dict[str, Any]],
    current_user_text: str,
    *,
    run_id: str,
) -> list[AgentMessage]:
    current_index = max(
        (
            index
            for index, message in enumerate(messages)
            if message.get("role") == "user"
            and message.get("content") == current_user_text
        ),
        default=len(messages) - 1,
    )
    return [
        _message_from_llm(message, run_id=run_id)
        for message in messages[current_index + 1 :]
        if message.get("role") in {"assistant", "tool"}
    ]


def _message_from_llm(message: dict[str, Any], *, run_id: str) -> AgentMessage:
    role = message.get("role")
    if role not in {"assistant", "tool"}:
        role = "assistant"
    calls: list[ToolCall] = []
    for raw_call in message.get("tool_calls") or []:
        function = raw_call.get("function") if isinstance(raw_call, dict) else {}
        raw_arguments = function.get("arguments") if isinstance(function, dict) else "{}"
        try:
            arguments = (
                json.loads(raw_arguments) if isinstance(raw_arguments, str) else {}
            )
        except json.JSONDecodeError:
            arguments = {}
        calls.append(
            ToolCall(
                id=str(raw_call.get("id") or uuid4().hex),
                name=str(function.get("name") or raw_call.get("name") or "tool"),
                arguments=arguments if isinstance(arguments, dict) else {},
            )
        )
    metadata: dict[str, Any] = {"run_id": run_id}
    content = message.get("content")
    if role == "tool":
        metadata["tool_call_id"] = message.get("tool_call_id")
        metadata["name"] = message.get("name")
        try:
            result = json.loads(str(content or "{}"))
        except json.JSONDecodeError:
            result = content
        calls.append(
            ToolCall(
                id=str(message.get("tool_call_id") or uuid4().hex),
                name=str(message.get("name") or "tool"),
                result=result,
                error=result.get("error") if isinstance(result, dict) else None,
            )
        )
    return AgentMessage(
        id=str(message.get("id") or uuid4().hex),
        role=role,
        content="" if content is None else str(content),
        timestamp=float(message.get("timestamp") or utc_timestamp()),
        tool_calls=tuple(calls),
        metadata=metadata,
    )


def _parse_summary(text: str) -> dict[str, str]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.startswith("json"):
            cleaned = cleaned[4:].lstrip()
    parsed = json.loads(cleaned)
    if not isinstance(parsed, dict):
        raise ValueError("summary must be a JSON object")
    keys = ContextAssembler.SECTION_ORDER
    result = {key: str(parsed.get(key) or "").strip() for key in keys}
    if any(not result[key] for key in keys):
        raise ValueError("summary is missing a required section")
    return result


def _usage_wire(usage: Mapping[str, Any]) -> dict[str, Any]:
    output = dict(usage)
    output.setdefault("label", f"上下文约 {output.get('used', 0)} / {output.get('limit', 0)}")
    return output


def _recall_wire(items: list[str]) -> list[dict[str, Any]]:
    return [
        {
            "id": f"recall-{index}",
            "title": "本轮召回记忆",
            "source": "长期记忆",
            "excerpt": text,
        }
        for index, text in enumerate(items)
    ]


def _memory_wire(record: Any) -> dict[str, Any]:
    source = record.source_session
    if record.source_entry:
        source = f"{source or 'session'} · {record.source_entry}"
    return {
        "id": record.id,
        "kind": record.kind,
        "text": record.content,
        "content": record.content,
        "source": source,
        "source_session": record.source_session,
        "source_entry": record.source_entry,
        "tags": list(record.tags),
        "status": record.status,
        "archived": record.status == "archived",
        "created_at": record.created_at,
        "expires_at": record.expires_at,
    }


__all__ = ["DuplicateTurn", "KSSAgentService"]
