"""KSS-specific application service built on the provider-neutral AgentRuntime."""

from __future__ import annotations

import asyncio
import base64
from contextvars import ContextVar
import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping
from uuid import uuid4

from kss.agent.attachments import AttachmentRecord, AttachmentStore
from kss.agent.context import ContextAssembler, ContextAssembly
from kss.agent.jsonl import utc_timestamp
from kss.agent.live_market_context import (
    LiveContextScope,
    LiveMarketContextService,
    scope_context_text,
)
from kss.agent.memory_store import MemoryRecall, MemoryStore
from kss.agent.pi_ai_provider import PiAIProvider
from kss.agent.provider import ModelCapabilities, ProviderConfig
from kss.agent.provider_route import (
    ProviderRoute,
    ProviderRouteSet,
    ProviderRouteStore,
    legacy_routes_from_environment,
)
from kss.agent.runtime import AgentRuntime, RuntimeTurn
from kss.agent.session_store import (
    QueuedInputLimitError,
    RunAdmissionError,
    SessionStore,
)
from kss.agent.skills import SkillManager, SkillResourceError
from kss.agent.types import (
    AgentContentBlock,
    AgentEvent,
    AgentMessage,
    RunResult,
    ToolCall,
    convert_to_llm,
)
from kss.llm.chat_client import ChatClient, sanitize_user_text

EmitEvent = Callable[[AgentEvent], Awaitable[None]]
RequestWrite = Callable[..., Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class DuplicateTurn:
    """Durable idempotency decision for a client turn key."""

    status: str
    existing_run_id: str


@dataclass(frozen=True)
class RuntimeRunOptions:
    """Per-run capability and budget envelope for controlled callers.

    Normal Seesaw turns use the unrestricted defaults. Research nodes provide
    an explicit envelope so a prompt-level whitelist cannot accidentally
    expand into the full desktop tool and Skill surface.
    """

    allowed_tools: frozenset[str] | None = None
    allowed_skills: frozenset[str] | None = None
    allowed_memory_kinds: frozenset[str] | None = None
    max_steps: int = 8
    timeout_seconds: float = 240.0
    max_provider_tokens: int | None = None
    allow_write_tools: bool = True
    trusted_internal_input: bool = False
    profile_id: str | None = None
    coverage_closer: bool = False
    coverage_path: bool = False

    def __post_init__(self) -> None:
        if self.max_steps < 1:
            raise ValueError("max_steps must be positive")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.max_provider_tokens is not None and self.max_provider_tokens < 1:
            raise ValueError("max_provider_tokens must be positive")


class KSSAgentService:
    """Own sessions, context, Skills, memories and the model/tool loop.

    The sidecar supplies only transport callbacks: an event writer and the
    human-in-the-loop write gate.
    """

    def __init__(
        self,
        state_root: str | Path,
        project_root: str | Path,
        *,
        start_provider: bool = False,
    ) -> None:
        self.state_root = Path(state_root)
        self.project_root = Path(project_root)
        self.sessions = SessionStore(self.state_root)
        self.skills = SkillManager(self.project_root, self.state_root)
        self.memories = MemoryStore(self.state_root)
        self.attachments = AttachmentStore(self.state_root)
        self.route_store = ProviderRouteStore(self.state_root)
        self.provider = self._build_provider(start_provider=start_provider)
        self.model = self._active_model()
        self.assembler = ContextAssembler(
            model_capabilities=self.provider.model_capabilities(self.model)
        )
        self._request_writes: dict[tuple[str, str], RequestWrite] = {}
        self._source_queue_ids: dict[tuple[str, str], str] = {}
        self._run_options: dict[tuple[str, str], RuntimeRunOptions] = {}
        self._coverage_sessions: set[str] = set()
        self._turn_attachment_ids: dict[tuple[str, str], tuple[str, ...]] = {}
        self._live_context_scopes: dict[tuple[str, str], Any] = {}
        self._transcripts: dict[str, Any] = {}
        # Compaction can run concurrently for different sessions. A ContextVar
        # carries the route into the summarizer without making an old test or
        # extension hook accept a new positional argument.
        self._summary_route_set: ContextVar[ProviderRouteSet | None] = ContextVar(
            "summary_route_set", default=None
        )
        self.runtime = AgentRuntime(
            self._execute_turn,
            model=self.model or None,
            model_resolver=lambda: self._active_model() or None,
            message_loader=self.sessions.read_messages,
            run_admission=self._admit_run,
            persistence_barrier=self._persist_turn,
            queue_store=self.sessions,
            runner_owns_turn_boundaries=True,
        )

    def _build_provider(self, *, start_provider: bool) -> Any:
        self._provider_start_error: str | None = None
        socket_path = os.getenv("KSS_PI_AI_CREDENTIAL_SOCKET", "").strip()
        nonce = os.getenv("KSS_PI_AI_CREDENTIAL_NONCE", "").strip()
        self._credential_socket: tuple[str, str] | None = (
            (socket_path, nonce) if socket_path and nonce else None
        )
        if self._credential_socket is not None:
            provider = PiAIProvider(
                route_resolver=self.route_store.load,
                credential_socket_resolver=self._credential_socket_snapshot,
            )
        else:
            # One-release compatibility path: pi-ai receives the legacy
            # Keychain-derived environment snapshot in memory only.
            provider = PiAIProvider(
                route_resolver=self.route_store.load,
                credential_resolver=lambda: legacy_routes_from_environment()[1],
            )
        if not start_provider:
            return provider
        try:
            provider.start()
            return provider
        except Exception as exc:  # noqa: BLE001 - helper startup is surfaced via catalog/stream.
            # Do not silently change transport after a helper failure. Settings
            # and the live turn must exercise the same pi-ai route and broker
            # contract, otherwise a green test can validate a different model.
            self._provider_start_error = f"{type(exc).__name__}: {exc}"
            return provider

    def _credential_socket_snapshot(self) -> tuple[str, str]:
        if self._credential_socket is None:
            raise ValueError("credential broker is not configured")
        return self._credential_socket

    def _active_model(self) -> str:
        try:
            if isinstance(self.provider, PiAIProvider):
                return self.route_store.load().primary.model_id
        except Exception:
            pass
        return os.getenv("KSS_LLM_MODEL") or ""

    def _session_primary_route(self, session_id: str) -> ProviderRoute:
        """Resolve the effective non-secret route for one session.

        Missing route metadata is a legacy-session condition, not a provider
        failure. We snapshot the current global default at first use so later
        global changes cannot silently reroute an existing conversation.
        """
        default = self.route_store.load().primary
        # A live turn already has a run_started entry. Do not use get_session
        # here: that public recovery API intentionally turns unfinished runs
        # into interrupted after a sidecar restart.
        state = self.sessions.current_state(session_id)
        raw = (state.metadata.get("provider_route") if state is not None else None)
        if isinstance(raw, Mapping):
            try:
                return ProviderRoute.from_dict(raw)
            except (KeyError, TypeError, ValueError):
                pass
        if state is not None:
            self.sessions.set_provider_route(session_id, default.as_dict())
        return default

    def _session_routes(self, session_id: str) -> ProviderRouteSet:
        primary = self._session_primary_route(session_id)
        fallback = self.route_store.load().fallback
        if fallback is not None and fallback == primary:
            fallback = None
        return ProviderRouteSet(primary=primary, fallback=fallback)

    @staticmethod
    def _route_capabilities(route: ProviderRoute) -> ModelCapabilities:
        return ModelCapabilities(
            context_window=route.context_window,
            max_output_tokens=route.max_output_tokens,
            supports_tools=route.supports_tools,
            supports_thinking=route.supports_thinking,
            supports_images=route.supports_images,
        )

    def provider_catalog(
        self,
        *,
        refresh: bool = False,
        provider_id: str | None = None,
    ) -> dict[str, Any]:
        routes = self.route_store.load()
        models: list[dict[str, Any]] = []
        status = "legacy"
        error: str | None = self._provider_start_error
        if isinstance(self.provider, PiAIProvider):
            status = "ready" if self.provider.is_available else "unavailable"
            try:
                catalog = (
                    self.provider.refresh_models(provider_id)
                    if refresh
                    else self.provider.list_models(provider_id)
                )
                models = [asdict(model) for model in catalog]
                status = "ready"
                error = None
            except Exception as exc:  # noqa: BLE001
                status = "unavailable"
                error = f"{type(exc).__name__}: {exc}"
        providers = self._provider_descriptors(routes=routes, models=models)
        return {
            "provider_backend": (
                "pi-ai" if isinstance(self.provider, PiAIProvider) else "legacy"
            ),
            "status": status,
            "providers": providers,
            "models": models,
            "primary": routes.primary.as_dict(),
            "fallback": routes.fallback.as_dict() if routes.fallback else None,
            "error": error,
        }

    def _provider_descriptors(
        self,
        *,
        routes: ProviderRouteSet,
        models: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        grouped: dict[str, dict[str, Any]] = {}
        authenticated = (
            self.provider.authenticated_provider_ids
            if isinstance(self.provider, PiAIProvider)
            else frozenset()
        )
        for route in routes.ordered():
            grouped.setdefault(
                route.provider_id,
                {
                    "id": route.provider_id,
                    "name": route.provider_id,
                    "auth_kind": "api_key",
                    "base_url": route.base_url,
                    "authenticated": route.provider_id in authenticated,
                    "models": [],
                },
            )
        for model in models:
            if not isinstance(model, dict):
                continue
            provider_id = str(model.get("provider_id") or "unknown")
            provider = grouped.setdefault(
                provider_id,
                {
                    "id": provider_id,
                    "name": provider_id,
                    "auth_kind": "api_key",
                    "base_url": None,
                    "authenticated": provider_id in authenticated,
                    "models": [],
                },
            )
            provider["models"].append(model)
        return [grouped[key] for key in sorted(grouped)]

    def set_provider_routes(
        self,
        *,
        primary: Mapping[str, Any],
        fallback: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        routes = ProviderRouteSet(
            primary=ProviderRoute.from_dict(primary),
            fallback=ProviderRoute.from_dict(fallback) if fallback else None,
        )
        self.route_store.save(routes)
        self.model = routes.primary.model_id
        return self.provider_catalog()

    def reload_provider_credentials(
        self,
        *,
        socket_path: str | None = None,
        nonce: str | None = None,
    ) -> dict[str, Any]:
        if socket_path or nonce:
            if not socket_path or not nonce:
                raise ValueError("credential reload requires socket_path and nonce")
            self._credential_socket = (socket_path, nonce)
        if isinstance(self.provider, PiAIProvider):
            self.provider.invalidate_credentials(reset_broker_nonce=True)
            self.provider.reload_credentials()
        return self.provider_catalog()

    def test_provider_connection(
        self,
        *,
        primary: Mapping[str, Any] | None = None,
        fallback: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run a minimal provider stream probe without exposing credentials."""

        # A session may override the global default route.  The UI must test
        # that exact route rather than reporting a healthy global default for
        # a different model/provider selected in the composer.
        configured_routes = self.route_store.load()
        routes = (
            ProviderRouteSet(
                primary=ProviderRoute.from_dict(primary),
                fallback=(
                    ProviderRoute.from_dict(fallback)
                    if fallback is not None
                    else configured_routes.fallback
                ),
            )
            if primary is not None
            else configured_routes
        )
        started = time.monotonic()
        candidates = [
            {
                "role": "primary" if index == 0 else "fallback",
                "provider_id": route.provider_id,
                "model": route.model_id,
                "ok": False,
                "latency_ms": None,
                "hint": "not reached",
            }
            for index, route in enumerate(routes.ordered())
        ]
        if not candidates:
            catalog = self.provider_catalog()
            catalog.update({
                "source": "llm",
                "ok": False,
                "status": "unavailable",
                "latency_ms": None,
                "hint": "未配置 provider route",
                "candidates": [],
            })
            return catalog

        try:
            for event in self.provider.stream_sync(
                [{"role": "user", "content": "Reply with OK only."}],
                [],
                ProviderConfig(
                    temperature=0,
                    timeout=30,
                    include_usage=True,
                    route_set=routes,
                ),
            ):
                raw_index = event.metadata.get("candidate_index")
                candidate_index = raw_index if isinstance(raw_index, int) else 0
                if not (0 <= candidate_index < len(candidates)):
                    candidate_index = 0
                candidate = candidates[candidate_index]
                if event.model:
                    candidate["model"] = event.model
                candidate["latency_ms"] = round((time.monotonic() - started) * 1000, 1)
                if event.type == "error":
                    message = event.error.message if event.error else "provider error"
                    candidate["hint"] = message
                    candidate["error"] = message
                    continue
                if event.type in {
                    "text_start",
                    "text",
                    "text_end",
                    "thinking_start",
                    "thinking",
                    "thinking_end",
                    "usage",
                }:
                    candidate["hint"] = "streaming"
                if event.type == "finish":
                    candidate["ok"] = True
                    candidate["hint"] = "stream ok"
                    break
            ok = any(bool(candidate.get("ok")) for candidate in candidates)
            latency_ms = round((time.monotonic() - started) * 1000, 1)
            catalog = self.provider_catalog()
            catalog.update({
                "source": "llm",
                "ok": ok,
                "status": "ready" if ok else "unavailable",
                "latency_ms": latency_ms,
                "hint": (
                    "stream ok"
                    if ok
                    else next(
                        (
                            str(candidate.get("hint"))
                            for candidate in candidates
                            if candidate.get("hint")
                            and candidate.get("hint") not in {"not reached", "streaming"}
                        ),
                        "provider stream failed",
                    )
                ),
                "candidates": candidates,
            })
            return catalog
        except Exception as exc:  # noqa: BLE001 - provider helpers fail across process boundaries.
            message = str(exc)
            candidates[0]["hint"] = message
            candidates[0]["error"] = message
            candidates[0]["latency_ms"] = round((time.monotonic() - started) * 1000, 1)
            catalog = self.provider_catalog()
            catalog.update({
                "source": "llm",
                "ok": False,
                "status": "unavailable",
                "latency_ms": round((time.monotonic() - started) * 1000, 1),
                "hint": message,
                "candidates": candidates,
            })
            return catalog

    def import_attachment(
        self,
        source: str,
        *,
        extracted_text: str | None = None,
    ) -> AttachmentRecord:
        return self.attachments.import_file(source, extracted_text=extracted_text)

    def close(self) -> None:
        close = getattr(self.provider, "close", None)
        if callable(close):
            close()

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
        attachment_ids: list[str] | tuple[str, ...] | None = None,
        live_context_scope: Any = None,
        *,
        run_options: RuntimeRunOptions | None = None,
    ) -> RunResult:
        """Run one idempotency-checked turn through the shared Runtime."""
        key = (session_id, client_turn_id)
        self._request_writes[key] = request_write
        if source_queue_id:
            self._source_queue_ids[key] = source_queue_id
        if run_options is not None:
            self._run_options[key] = run_options
            if run_options.coverage_closer:
                self._coverage_sessions.add(session_id)
        if attachment_ids:
            self._turn_attachment_ids[key] = tuple(str(value) for value in attachment_ids)
        if live_context_scope is not None:
            self._live_context_scopes[key] = live_context_scope
        try:
            turn_input = input
            if run_options is None or not run_options.trusted_internal_input:
                turn_input = sanitize_user_text(input)
            elif len(turn_input) > 24_000:
                raise ValueError("trusted internal input exceeds 24000 characters")
            return await self.runtime.run_turn(
                session_id,
                client_turn_id,
                turn_input,
                emit,
            )
        finally:
            self._request_writes.pop(key, None)
            self._source_queue_ids.pop(key, None)
            self._run_options.pop(key, None)
            self._turn_attachment_ids.pop(key, None)
            self._live_context_scopes.pop(key, None)
            self._coverage_sessions.discard(session_id)

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
        if mode == "steering":
            try:
                state = self.runtime.state(run_id)
                session_id = getattr(state, "session_id", None) if state is not None else None
            except Exception:
                session_id = None
            if session_id and session_id in self._coverage_sessions:
                from kss.equity_research.intent import is_explainer_priority
                if is_explainer_priority(cleaned):
                    mode = "follow_up"
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
        run_key = (session_id, client_turn_id)
        explicit_options = run_key in self._run_options
        run_options = self._run_options.get(run_key, RuntimeRunOptions())
        session_routes = self._session_routes(session_id)
        active_route = session_routes.primary
        request_write = self._request_writes[(session_id, client_turn_id)]

        had_user = any(message.role == "user" for message in turn.messages[:-1])
        current_user = turn.messages[-1]
        if not explicit_options:
            from kss.equity_research.envelope import options_for_user_text
            inferred = options_for_user_text(current_user.content)
            if inferred is not None:
                run_options = inferred
        attachment_ids = self._turn_attachment_ids.get(
            (session_id, client_turn_id),
            (),
        )
        if attachment_ids:
            records = self.attachments.validate_turn(
                self.attachments.load_record(value) for value in attachment_ids
            )
            blocks: list[AgentContentBlock] = [
                AgentContentBlock(type="text", text=current_user.content, content_index=0)
            ]
            next_index = 1
            for record in records:
                record_blocks = self.attachments.content_blocks(
                    record,
                    content_index=next_index,
                    include_extracted_text=False,
                )
                blocks.extend(record_blocks)
                next_index += len(record_blocks)
            current_user = AgentMessage(
                id=current_user.id,
                role=current_user.role,
                content=current_user.content,
                timestamp=current_user.timestamp,
                tool_calls=current_user.tool_calls,
                content_blocks=tuple(blocks),
                metadata={
                    **current_user.metadata,
                    "attachment_ids": list(attachment_ids),
                    "attachments": [record.to_payload() for record in records],
                },
            )
            turn.messages[-1] = current_user
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
        if run_options.allowed_memory_kinds is not None:
            recall_items = [
                item
                for item in recall_items
                if item.kind in run_options.allowed_memory_kinds
            ]
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

        live_context_payloads = await self._preload_live_context(
            turn,
            raw_scope=self._live_context_scopes.get((session_id, client_turn_id)),
            user_text=current_user.content,
        )
        live_context_evidence = [
            scope_context_text(payload)
            for payload in live_context_payloads
            if isinstance(payload, Mapping) and not payload.get("error")
        ]

        skill_status = self.skills.status()
        self.sessions.append_entry(
            session_id, "skill_index", {"run_id": run_id, "status": skill_status}
        )
        discovered = self.skills.discover()[0]
        if run_options.profile_id is not None:
            discovered = [
                skill
                for skill in discovered
                if not skill.allowed_profiles
                or run_options.profile_id in skill.allowed_profiles
            ]
        if run_options.allowed_skills is not None:
            discovered = [
                skill
                for skill in discovered
                if skill.id in run_options.allowed_skills
                or skill.name in run_options.allowed_skills
            ]
        skill_summaries = [
            f"{item.name}: {item.description}" for item in discovered if item.enabled
        ]
        pinned = set(self.skills.pinned_skill_ids(session_id))
        for skill in discovered:
            if skill.enabled and (skill.id in pinned or skill.name in pinned):
                skill_summaries.append(
                    f"本会话 Skill {skill.name}:\n{self.skills.load_skill(skill.id)}"
                )

        assembly = await self._assemble_context(
            turn,
            skills=skill_summaries,
            memories=recall_items,
            evidence=live_context_evidence,
            route=active_route,
            route_set=session_routes,
        )
        current_assembly = assembly
        await turn.emit(
            "context_usage",
            {"context_usage": _usage_wire(assembly.usage.to_dict())},
        )
        all_specs = {
            str(spec.get("name") or ""): spec for spec in chat_loop.TOOL_SPECS
        }
        self.skills.available_tools = frozenset(all_specs)
        if run_options.allowed_tools is None:
            selected_specs = list(chat_loop.TOOL_SPECS)
        else:
            unknown_tools = set(run_options.allowed_tools) - set(all_specs)
            if unknown_tools:
                raise ValueError(
                    f"unsupported run tool whitelist: {sorted(unknown_tools)}"
                )
            selected_specs = [
                all_specs[name] for name in run_options.allowed_tools
            ]
        if not run_options.allow_write_tools:
            write_tools = [
                str(spec["name"])
                for spec in selected_specs
                if chat_loop.is_write_command(str(spec.get("command") or ""))
            ]
            if write_tools:
                raise ValueError(
                    f"write tools are forbidden for this run: {sorted(write_tools)}"
                )
        registry = chat_loop.ToolRegistry(selected_specs)

        def skill_is_allowed(skill_id: str) -> bool:
            allowed = run_options.allowed_skills
            if allowed is None:
                return True
            return skill_id in allowed or any(
                skill.name == skill_id and skill.id in allowed
                for skill in discovered
            )

        def load_skill(args: dict[str, Any]) -> dict[str, Any]:
            skill_id = str(args.get("skill_id") or "")
            if not skill_is_allowed(skill_id):
                return {
                    "error": "skill_not_allowed",
                    "skill_id": skill_id,
                    "is_error": True,
                    "provenance": "skill",
                }
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

        if registry.has_tool("load_skill"):
            registry.register_handler("load_skill", load_skill)

        def read_skill_resource(args: dict[str, Any]) -> dict[str, Any]:
            skill_id = str(args.get("skill_id") or "")
            path = str(args.get("path") or "")
            if not skill_is_allowed(skill_id):
                return {
                    "error": "skill_not_allowed",
                    "skill_id": skill_id,
                    "path": path,
                    "is_error": True,
                    "provenance": "skill_resource",
                }
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

        if registry.has_tool("read_skill_resource"):
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

        if registry.has_tool("propose_memory"):
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
                evidence=live_context_evidence,
                goal=item.content,
                route=active_route,
                route_set=session_routes,
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
            return self._prepare_provider_messages(
                _merge_context(
                    current_assembly,
                    conversation,
                    provider=active_route.provider_id,
                    model=active_route.model_id,
                ),
                route=active_route,
            )

        history = self._prepare_provider_messages(
            [
                convert_to_llm(
                    message,
                    include_thinking=True,
                    provider=active_route.provider_id,
                    model=active_route.model_id,
                )
                for message in assembly.kept_messages
            ],
            route=active_route,
        )
        chat_client = ChatClient(
            provider=self.provider,
            route_set=session_routes,
        )
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
                max_steps=run_options.max_steps,
                turn_timeout=run_options.timeout_seconds,
                coverage_path=bool(run_options.coverage_path or run_options.coverage_closer),
                chat_client=chat_client,
            )
        except BaseException as exc:
            if run_options.coverage_closer:
                from kss.equity_research.intent import r12_phrase
                phrase = r12_phrase("incomplete")
                await emit_loop({"type": "chunk", "text": phrase})
                if message_open:
                    message_open = False
                    await turn.emit(
                        "message_end",
                        {"role": "assistant", "reason": "unable_to_complete"},
                    )
                if boundary_open:
                    boundary_open = False
                    await turn.emit("turn_end", {"reason": "unable_to_complete", "coverage_path": True, "disable_legacy_fallback": True})
                return RunResult(
                    run_id=run_id,
                    session_id=session_id,
                    client_turn_id=client_turn_id,
                    status="completed",
                    messages=tuple(turn.messages),
                    error=None,
                    usage=dict(turn.state.usage),
                    termination_reason="unable_to_complete",
                )
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
        self._transcripts[run_id] = _sanitize_transcript(
            transcript.as_dict()
        )
        usage = transcript.run_state.get("usage")
        if isinstance(usage, dict):
            turn.add_usage(**usage)
        provider_tokens = _provider_token_total(turn.state.usage)
        provider_budget_exceeded = bool(
            run_options.max_provider_tokens
            and provider_tokens > run_options.max_provider_tokens
        )
        new_messages = _new_transcript_messages(
            transcript.messages,
            current_user.content,
            run_id=run_id,
        )
        for message in new_messages:
            turn.append_message(message)
        reason = str(transcript.run_state.get("reason") or "completed")
        failed = reason == "error" or provider_budget_exceeded
        provider_error = str(
            transcript.run_state.get("error") or "provider stream failed"
        )
        if provider_budget_exceeded:
            reason = "provider_token_budget_exceeded"
        if run_options.coverage_closer:
            from kss.equity_research.envelope import apply_coverage_closer
            from kss.equity_research.intent import r12_phrase
            aborted = reason in {"aborted", "client_abort"}
            closed, closed_reason, replaced = apply_coverage_closer(
                [{"role": "assistant", "content": ""}],
                reason=reason,
                aborted=aborted,
            )
            if replaced:
                phrase = r12_phrase("incomplete")
                await emit_loop({"type": "chunk", "text": "\n" + phrase})
                reason = closed_reason
                failed = False
                provider_error = None
        return RunResult(
            run_id=run_id,
            session_id=session_id,
            client_turn_id=client_turn_id,
            status="failed" if failed else "completed",
            messages=tuple(turn.messages),
            error=(
                "provider token budget exceeded"
                if provider_budget_exceeded
                else provider_error if failed else None
            ),
            usage=dict(turn.state.usage),
            termination_reason=reason,
        )

    async def _preload_live_context(
        self,
        turn: RuntimeTurn,
        *,
        raw_scope: Any,
        user_text: str,
    ) -> list[dict[str, Any]]:
        scope = LiveContextScope.from_payload(raw_scope, user_text=user_text)
        if scope is None:
            return []
        payloads: list[dict[str, Any]] = []
        if scope.rejected:
            payload = {
                "kind": "market_live_context",
                "scope": scope.to_payload(),
                "error": scope.rejection_reason or "live_context_scope_rejected",
                "is_error": True,
                "eligibility": "forward_observed",
                "provenance": "kss_live_market_context",
            }
            payloads.append(payload)
        elif scope.symbols:
            import kss_app_bridge as bridge  # noqa: PLC0415

            service = LiveMarketContextService(bridge._make_read_only_call(bridge.dispatch))
            payload = await asyncio.to_thread(
                service.get_context,
                symbols=scope.symbols,
                intent=scope.intent,
                reason=f"agent_preflight:{scope.reason}",
                include_intraday_snapshot=scope.include_intraday_snapshot,
                max_symbols=scope.max_symbols,
            )
            payload["scope"] = scope.to_payload()
            payloads.append(payload)
        if payloads:
            self.sessions.append_entry(
                turn.state.session_id,
                "live_context",
                {"run_id": turn.state.run_id, "items": payloads},
            )
            await turn.emit(
                "live_context",
                {
                    "items": payloads,
                    "live_context": payloads[0] if len(payloads) == 1 else payloads,
                },
            )
        return payloads

    def _prepare_provider_messages(
        self,
        messages: list[dict[str, Any]],
        *,
        route: ProviderRoute | None = None,
    ) -> list[dict[str, Any]]:
        """Resolve durable attachment IDs only at the provider boundary."""

        supports_images = self._route_capabilities(
            route or self.route_store.load().primary
        ).supports_images
        prepared: list[dict[str, Any]] = []
        for message in messages:
            content = message.get("content")
            if not isinstance(content, list):
                prepared.append(message)
                continue
            blocks: list[dict[str, Any]] = []
            for raw in content:
                if not isinstance(raw, Mapping):
                    continue
                block = dict(raw)
                block_type = str(block.get("type") or "")
                if block_type == "image":
                    attachment_id = block.get("attachment_id")
                    if not isinstance(attachment_id, str) or not supports_images:
                        continue
                    record = self.attachments.load_record(attachment_id)
                    if record.kind != "image":
                        continue
                    block = {
                        "type": "image",
                        "data": base64.b64encode(
                            self.attachments.load_bytes(record)
                        ).decode("ascii"),
                        "mimeType": record.mime_type,
                    }
                elif block_type == "attachment_ref":
                    attachment_id = block.get("attachment_id")
                    if isinstance(attachment_id, str):
                        record = self.attachments.load_record(attachment_id)
                        extracted = self.attachments.load_extracted_text(record)
                        if extracted:
                            blocks.append(
                                {
                                    "type": "text",
                                    "text": (
                                        f"\n\n[附件 {record.filename} 提取文本]\n"
                                        f"{extracted}\n[附件文本结束]\n"
                                    ),
                                }
                            )
                    continue
                blocks.append(block)
            prepared.append({**message, "content": blocks})
        return prepared

    async def _assemble_context(
        self,
        turn: RuntimeTurn,
        *,
        skills: list[str],
        memories: list[str],
        goal: str | None = None,
        route: ProviderRoute | None = None,
        route_set: ProviderRouteSet | None = None,
        evidence: list[str] | None = None,
    ) -> ContextAssembly:
        active_route = route or self._session_primary_route(turn.state.session_id)
        active_routes = route_set or self._session_routes(turn.state.session_id)
        previous = self.sessions.latest_compaction(turn.state.session_id)
        kwargs = {
            "session_id": turn.state.session_id,
            "messages": list(turn.messages),
            "skills": skills,
            "memories": memories,
            "goal": turn.input if goal is None else goal,
            "evidence": evidence or [],
            "model": active_route.model_id,
            "model_capabilities": self._route_capabilities(active_route),
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
        route_token = self._summary_route_set.set(active_routes)
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
        finally:
            self._summary_route_set.reset(route_token)
        assembly = self.assembler.assemble_detailed(
            **kwargs,
            summary=summary,
            summarizer_model=active_route.model_id,
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
            route_set = self._summary_route_set.get() or self.route_store.load()
            client = ChatClient(
                timeout=30.0,
                provider=self.provider,
                route_set=route_set,
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
                    result.session_id,
                    "transcript",
                    (
                        dict(transcript)
                        if isinstance(transcript, Mapping)
                        else transcript.as_dict()
                    ),
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
    assembly: ContextAssembly,
    conversation: list[dict[str, Any]],
    *,
    provider: str | None = None,
    model: str | None = None,
) -> list[dict[str, Any]]:
    system = [message for message in conversation if message.get("role") == "system"]
    context = [
        convert_to_llm(
            message,
            include_thinking=True,
            provider=provider,
            model=model,
        )
        for message in assembly.context.messages
    ]
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
            and _llm_visible_text(message.get("content")) == current_user_text
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
    if message.get("provider") is not None:
        metadata["provider"] = str(message["provider"])
    if message.get("model") is not None:
        metadata["model"] = str(message["model"])
    content = message.get("content")
    structured_content = message.get("content_blocks")
    blocks: list[AgentContentBlock] = []
    if isinstance(structured_content, list) or isinstance(content, list):
        raw_blocks = (
            structured_content if isinstance(structured_content, list) else content
        )
        for index, raw in enumerate(raw_blocks):
            if not isinstance(raw, Mapping):
                continue
            block_type = str(raw.get("type") or "")
            if block_type not in {"text", "thinking", "image", "attachment_ref"}:
                continue
            normalized = dict(raw)
            normalized["type"] = block_type
            normalized.setdefault("content_index", index)
            if block_type == "thinking":
                normalized["text"] = raw.get("text") or raw.get("thinking") or ""
                normalized["signature"] = (
                    raw.get("signature") or raw.get("thinkingSignature")
                )
                normalized.setdefault("provider", message.get("provider"))
                normalized.setdefault("model", message.get("model"))
            try:
                blocks.append(AgentContentBlock.from_payload(normalized))
            except (KeyError, TypeError, ValueError):
                continue
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
        content=(
            _llm_visible_text(content)
            if isinstance(content, list)
            else "" if content is None else str(content)
        ),
        timestamp=float(message.get("timestamp") or utc_timestamp()),
        tool_calls=tuple(calls),
        content_blocks=tuple(blocks),
        metadata=metadata,
    )


def _llm_visible_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "".join(
        str(item.get("text") or "")
        for item in content
        if isinstance(item, Mapping) and item.get("type") == "text"
    )


def _sanitize_transcript(value: Any) -> Any:
    """Remove transient attachment payloads before append-only persistence."""

    if isinstance(value, list):
        return [_sanitize_transcript(item) for item in value]
    if not isinstance(value, Mapping):
        return value
    is_image_payload = value.get("type") == "image" and "data" in value
    result = {
        str(key): _sanitize_transcript(item)
        for key, item in value.items()
        if not (is_image_payload and key == "data")
    }
    if is_image_payload:
        result["redacted"] = True
    return result


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


def _provider_token_total(usage: Mapping[str, Any]) -> int:
    return int(
        usage.get("total_tokens")
        or usage.get("provider_tokens")
        or (
            int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
            + int(
                usage.get("output_tokens")
                or usage.get("completion_tokens")
                or 0
            )
        )
    )


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


__all__ = ["DuplicateTurn", "KSSAgentService", "RuntimeRunOptions"]
