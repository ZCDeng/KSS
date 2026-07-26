"""KSS-specific application service built on the provider-neutral AgentRuntime."""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping
from uuid import uuid4

from kss.agent.context import ContextAssembler, ContextAssembly
from kss.agent.jsonl import utc_timestamp
from kss.agent.memory_store import MemoryRecall, MemoryStore
from kss.agent.provider import OpenAICompatibleProvider
from kss.agent.runtime import AgentRuntime, RuntimeTurn
from kss.agent.session_store import (
    QueuedInputLimitError,
    RunAdmissionError,
    SessionStore,
)
from kss.agent.skills import SkillManager, SkillResourceError
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
        self._source_queue_ids: dict[tuple[str, str], str] = {}
        self._transcripts: dict[str, Any] = {}
        self.runtime = AgentRuntime(
            self._execute_turn,
            model=self.model or None,
            model_resolver=lambda: os.getenv("KSS_LLM_MODEL") or None,
            message_loader=self.sessions.read_messages,
            run_admission=self._admit_run,
            persistence_barrier=self._persist_turn,
            queue_store=self.sessions,
            runner_owns_turn_boundaries=True,
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
        source_queue_id: str | None = None,
    ) -> RunResult:
        """Run one idempotency-checked turn through the shared Runtime."""
        key = (session_id, client_turn_id)
        self._request_writes[key] = request_write
        if source_queue_id:
            self._source_queue_ids[key] = source_queue_id
        try:
            return await self.runtime.run_turn(
                session_id,
                client_turn_id,
                sanitize_user_text(input),
                emit,
            )
        finally:
            self._request_writes.pop(key, None)
            self._source_queue_ids.pop(key, None)

    def steer(
        self,
        run_id: str,
        client_message_id: str,
        input: str,
        source_queue_id: str | None = None,
    ) -> dict[str, Any]:
        """Queue a steering message without changing the current tool batch."""
        return self._queue_input(
            "steering",
            run_id,
            client_message_id,
            input,
            source_queue_id=source_queue_id,
        )

    def follow_up(
        self,
        run_id: str,
        client_message_id: str,
        input: str,
        source_queue_id: str | None = None,
    ) -> dict[str, Any]:
        """Queue one follow-up for processing after the current task settles."""
        return self._queue_input(
            "follow_up",
            run_id,
            client_message_id,
            input,
            source_queue_id=source_queue_id,
        )

    def queued_inputs(
        self,
        session_id: str | None = None,
        run_id: str | None = None,
    ) -> list[Any]:
        """Return pending/restored inputs for protocol hydration."""
        return self.runtime.queued_inputs(session_id=session_id, run_id=run_id)

    def discard_queued_input(
        self, session_id: str, queue_id: str
    ) -> dict[str, Any]:
        """Discard one pending/restored item and return the new queue snapshot."""
        try:
            item = self.runtime.discard_queued_input(session_id, queue_id)
        except (KeyError, ValueError) as exc:
            return {
                "rejected": True,
                "error": "queue_discard_rejected",
                "reason": str(exc),
                "queued_inputs": [
                    asdict(value) for value in self.queued_inputs(session_id=session_id)
                ],
            }
        return {
            "item": asdict(item),
            "queued_inputs": [
                asdict(value) for value in self.queued_inputs(session_id=session_id)
            ],
        }

    def _queue_input(
        self,
        mode: str,
        run_id: str,
        client_message_id: str,
        input: str,
        *,
        source_queue_id: str | None,
    ) -> dict[str, Any]:
        cleaned = sanitize_user_text(input)
        if not cleaned.strip():
            return {
                "rejected": True,
                "error": "invalid_input",
                "reason": "queued input is empty after sanitization",
            }
        try:
            if mode == "steering":
                item = self.runtime.steer(
                    run_id,
                    client_message_id,
                    cleaned,
                    source_queue_id=source_queue_id,
                )
            else:
                item = self.runtime.follow_up(
                    run_id,
                    client_message_id,
                    cleaned,
                    source_queue_id=source_queue_id,
                )
        except KeyError as exc:
            if "source queued input" in str(exc):
                return {
                    "rejected": True,
                    "error": "source_queue_invalid",
                    "reason": str(exc),
                }
            return {
                "rejected": True,
                "error": "unknown_run",
                "reason": "run_settling",
            }
        except QueuedInputLimitError:
            return {
                "rejected": True,
                "error": "queue_limit",
                "reason": "每个 run 最多排队 8 条输入",
            }
        except RuntimeError:
            return {
                "rejected": True,
                "error": "run_settling",
                "reason": "run_settling",
            }
        except ValueError as exc:
            if "source queued input" in str(exc):
                return {
                    "rejected": True,
                    "error": "source_queue_invalid",
                    "reason": str(exc),
                }
            return {
                "rejected": True,
                "error": "invalid_input",
                "reason": str(exc),
            }
        return {
            "item": asdict(item),
            "queued_inputs": [
                asdict(value) for value in self.queued_inputs(run_id=run_id)
            ],
        }

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
        source_queue_id = self._source_queue_ids.get((session_id, client_turn_id))
        self.sessions.append_message(
            session_id,
            current_user,
            source_queue_id=source_queue_id,
        )
        if not had_user:
            title = current_user.content.strip().replace("\n", " ")[:40] or "新会话"
            self.sessions.rename_session(session_id, title)
        await turn.emit(
            "turn_start",
            {
                "client_turn_id": client_turn_id,
                "kind": "initial",
                "step": 0,
                "message_id": current_user.id,
            },
        )

        recall_items = self.memories.recall(
            turn.input, now_ms=int(time.time() * 1000), limit=5
        )
        if recall_items:
            recall_payloads = _recall_wire(recall_items)
            self.sessions.append_entry(
                session_id,
                "recall",
                {"run_id": run_id, "items": recall_payloads},
            )
            await turn.emit(
                "recall",
                {"items": recall_payloads, "recalls": recall_payloads},
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
        current_assembly = assembly
        await turn.emit(
            "context_usage",
            {"context_usage": _usage_wire(assembly.usage.to_dict())},
        )
        registry = chat_loop.ToolRegistry()
        def load_skill(args: dict[str, Any]) -> dict[str, Any]:
            skill_id = str(args.get("skill_id") or "")
            try:
                return {
                    "skill_id": skill_id,
                    "content": self.skills.load_skill(skill_id),
                    "provenance": "skill",
                }
            except SkillResourceError as exc:
                return {
                    **exc.as_dict(),
                    "is_error": True,
                    "provenance": "skill",
                }

        registry.register_handler("load_skill", load_skill)

        def read_skill_resource(args: dict[str, Any]) -> dict[str, Any]:
            skill_id = str(args.get("skill_id") or "")
            path = str(args.get("path") or "")
            try:
                resource = self.skills.read_resource_info(
                    skill_id,
                    path,
                    offset=int(args.get("offset") or 0),
                    limit=int(args.get("max_chars") or 12_000),
                )
                return {
                    "skill_id": resource.skill_id,
                    "path": resource.relative_path,
                    "content": resource.content,
                    "offset": resource.offset,
                    "next_offset": resource.next_offset,
                    "truncated": resource.truncated,
                    "size_bytes": resource.byte_size,
                    "provenance": "skill_resource",
                }
            except SkillResourceError as exc:
                return {
                    **exc.as_dict(),
                    "is_error": True,
                    "provenance": "skill_resource",
                }

        registry.register_handler("read_skill_resource", read_skill_resource)

        source_user_entry = current_user.id

        async def propose_memory(args: dict[str, Any]) -> dict[str, Any]:
            kind = str(args.get("kind") or "preference")
            if kind not in {"preference", "decision", "thesis"}:
                raise ValueError("memory kind must be preference, decision, or thesis")
            record = self.memories.propose(
                kind,
                str(args.get("text") or ""),
                source_session=session_id,
                source_entry=source_user_entry,
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
        async def runtime_write_gate(**kwargs: Any) -> dict[str, Any]:
            return await request_write(**kwargs, emit_event=turn.emit)

        boundary_open = True
        message_open = False
        first_internal_turn_start = True

        async def emit_loop(event: dict[str, Any]) -> None:
            nonlocal boundary_open, first_internal_turn_start, message_open
            event_type = str(event.get("type") or "event")
            payload = {key: value for key, value in event.items() if key != "type"}
            if event_type == "turn_start":
                if first_internal_turn_start:
                    first_internal_turn_start = False
                    boundary_open = True
                    return
                boundary_open = True
            elif event_type == "message_start":
                message_open = True
            elif event_type == "message_end":
                message_open = False
            elif event_type == "turn_end":
                boundary_open = False
            elif event_type == "chunk" and not message_open:
                message_open = True
                await turn.emit(
                    "message_start",
                    {"role": "assistant", "kind": "initial"},
                )
            if event_type == "done":
                if message_open:
                    message_open = False
                    await turn.emit("message_end", {"role": "assistant"})
                if boundary_open:
                    boundary_open = False
                    await turn.emit(
                        "turn_end",
                        {"reason": str(payload.get("reason") or "stop")},
                    )
                return
            await turn.emit(_loop_event_name(event_type), payload)

        def queue_snapshot() -> list[Any]:
            return [
                item
                for item in turn.queued_inputs()
                if item.status == "queued"
            ]

        async def emit_queue_applied(item: Any) -> None:
            items = turn.queued_inputs()
            await turn.emit(
                "queue_update",
                {
                    "operation": "applied",
                    "item": asdict(item),
                    "queued_inputs": [asdict(value) for value in items],
                    "steering_count": sum(
                        1 for value in items if value.mode == "steering"
                    ),
                    "follow_up_count": sum(
                        1 for value in items if value.mode == "follow_up"
                    ),
                },
            )

        async def take_steering() -> list[dict[str, Any]] | None:
            applied: list[dict[str, Any]] = []
            for queued in queue_snapshot():
                if queued.mode != "steering":
                    continue
                item = turn.apply_queued_input(queued.id)
                await emit_queue_applied(item)
                applied.append(
                    {
                        "role": "user",
                        "content": item.content,
                        "message_id": item.client_message_id,
                    }
                )
            return applied or None

        async def take_follow_up() -> dict[str, Any] | None:
            nonlocal current_assembly, source_user_entry
            queued = next(
                (
                    item
                    for item in queue_snapshot()
                    if item.mode == "follow_up"
                ),
                None,
            )
            if queued is None:
                return None
            item = turn.apply_queued_input(queued.id)
            source_user_entry = item.client_message_id
            await emit_queue_applied(item)

            follow_up_recalls = self.memories.recall(
                item.content,
                now_ms=int(time.time() * 1000),
                limit=5,
            )
            if follow_up_recalls:
                payloads = _recall_wire(follow_up_recalls)
                self.sessions.append_entry(
                    session_id,
                    "recall",
                    {
                        "run_id": run_id,
                        "queue_item_id": item.id,
                        "items": payloads,
                    },
                )
                await turn.emit(
                    "recall",
                    {
                        "queue_item_id": item.id,
                        "items": payloads,
                        "recalls": payloads,
                    },
                )
            current_assembly = await self._assemble_context(
                turn,
                skills=skill_summaries,
                memories=follow_up_recalls,
                goal=item.content,
            )
            await turn.emit(
                "context_usage",
                {
                    "context_usage": _usage_wire(
                        current_assembly.usage.to_dict()
                    )
                },
            )
            return {
                "role": "user",
                "content": item.content,
                "message_id": item.client_message_id,
            }

        def transform_context(
            conversation: list[dict[str, Any]],
        ) -> list[dict[str, Any]]:
            return _merge_context(current_assembly, conversation)

        history = [convert_to_llm(message) for message in assembly.kept_messages]
        try:
            transcript = await chat_loop.run_turn(
                history,
                emit_loop,
                runtime_write_gate,
                abort_token=turn.abort_token,
                tool_registry=registry,
                transform_context=transform_context,
                take_steering=take_steering,
                take_follow_up=take_follow_up,
                emit_internal_boundaries=True,
            )
        except BaseException:
            if message_open:
                message_open = False
                await turn.emit(
                    "message_end",
                    {"role": "assistant", "reason": "aborted"},
                )
            if boundary_open:
                boundary_open = False
                await turn.emit("turn_end", {"reason": "aborted"})
            raise
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
        goal: str | None = None,
    ) -> ContextAssembly:
        previous = self.sessions.latest_compaction(turn.state.session_id)
        kwargs = {
            "session_id": turn.state.session_id,
            "messages": list(turn.messages),
            "skills": skills,
            "memories": memories,
            "goal": turn.input if goal is None else goal,
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


def _recall_wire(items: list[MemoryRecall]) -> list[dict[str, Any]]:
    """Serialize the exact structured objects used for model injection."""
    return [item.as_dict() for item in items]


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
