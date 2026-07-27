"""pi-ai provider bridge backed by a long-lived, signed Node helper.

The Python AgentRuntime remains the orchestration owner. This module delegates
only provider/model/auth/stream concerns to pi-ai and preserves KSS's fallback
invariant: another route may be tried only before any model output.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import queue
import shutil
import subprocess
import threading
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from kss.agent.provider import (
    AbortSignal,
    ModelCapabilities,
    ProviderConfig,
    ProviderError,
    ProviderEvent,
    ProviderUsage,
)
from kss.agent.provider_route import (
    ProviderCredential,
    ProviderModel,
    ProviderRoute,
    ProviderRouteSet,
    legacy_routes_from_environment,
)

logger = logging.getLogger(__name__)

_HELPER_PROTOCOL_VERSION = 1
_PI_AI_VERSION = "0.82.1"
_DEFAULT_HELPER_TIMEOUT = 10.0


class PiAIHelperError(RuntimeError):
    """A sanitized helper/process/protocol failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class PiAIHelperClient:
    """Thread-safe NDJSON client for the pi-ai helper process."""

    def __init__(
        self,
        *,
        node_path: str | Path | None = None,
        helper_path: str | Path | None = None,
        mock: bool = False,
        startup_timeout: float = _DEFAULT_HELPER_TIMEOUT,
    ) -> None:
        self.node_path = Path(node_path) if node_path else _find_node()
        self.helper_path = Path(helper_path) if helper_path else _find_helper()
        self.mock = mock
        self.startup_timeout = startup_timeout
        self._process: subprocess.Popen[str] | None = None
        self._write_lock = threading.Lock()
        self._lifecycle_lock = threading.Lock()
        self._pending: dict[str, queue.Queue[dict[str, Any]]] = {}
        self._reader_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._hello: dict[str, Any] | None = None
        self._generation = 0
        self._credential_socket_path: str | None = None
        self._next_credential_nonce: str | None = None

    @property
    def is_running(self) -> bool:
        process = self._process
        return process is not None and process.poll() is None

    @property
    def generation(self) -> int:
        return self._generation

    def start(self) -> dict[str, Any]:
        if self.is_running and self._hello is not None:
            return dict(self._hello)
        with self._lifecycle_lock:
            if self.is_running and self._hello is not None:
                return dict(self._hello)
            if not self.node_path.is_file():
                raise PiAIHelperError("node_unavailable", f"Node runtime not found: {self.node_path}")
            if not self.helper_path.is_file():
                raise PiAIHelperError("helper_unavailable", f"pi-ai helper not found: {self.helper_path}")
            args = [
                str(self.node_path),
                str(self.helper_path),
            ]
            if self.mock:
                args.append("--mock")
            try:
                process = subprocess.Popen(
                    args,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    bufsize=1,
                    close_fds=True,
                    env=_minimal_helper_environment(),
                )
            except OSError as exc:
                raise PiAIHelperError("helper_start_failed", str(exc)) from exc
            self._process = process
            self._generation += 1
            self._reader_thread = threading.Thread(
                target=self._read_stdout,
                name="kss-pi-ai-stdout",
                daemon=True,
            )
            self._stderr_thread = threading.Thread(
                target=self._read_stderr,
                name="kss-pi-ai-stderr",
                daemon=True,
            )
            self._reader_thread.start()
            self._stderr_thread.start()
        try:
            hello = self.command("hello", timeout=self.startup_timeout)
        except Exception:
            self.close(force=True)
            raise
        if int(hello.get("protocol_version", 0)) != _HELPER_PROTOCOL_VERSION:
            self.close(force=True)
            raise PiAIHelperError("protocol_mismatch", "unsupported pi-ai helper protocol")
        if str(hello.get("pi_ai_version")) != _PI_AI_VERSION:
            self.close(force=True)
            raise PiAIHelperError("version_mismatch", "unexpected pi-ai helper version")
        self._hello = dict(hello)
        return dict(hello)

    def command(
        self,
        command: str,
        *,
        timeout: float = _DEFAULT_HELPER_TIMEOUT,
        **payload: Any,
    ) -> dict[str, Any]:
        self._ensure_started(command)
        request_id = uuid.uuid4().hex
        inbox = self._register(request_id)
        try:
            self._send({"request_id": request_id, "command": command, **payload})
            try:
                frame = inbox.get(timeout=timeout)
            except queue.Empty as exc:
                raise PiAIHelperError("helper_timeout", f"pi-ai helper timed out: {command}") from exc
            if frame.get("type") != "response":
                raise PiAIHelperError("protocol_error", "unexpected non-response frame")
            return _response_result(frame)
        finally:
            self._pending.pop(request_id, None)

    def reload_credentials(
        self,
        credentials: Mapping[str, ProviderCredential],
    ) -> list[dict[str, str]]:
        payload = {
            provider_id: credential.as_helper_dict()
            for provider_id, credential in credentials.items()
        }
        result = self.command("auth.reload", credentials=payload)
        metadata = result.get("credentials")
        return list(metadata) if isinstance(metadata, list) else []

    def reload_credentials_from_socket(
        self,
        socket_path: str | Path,
        nonce: str,
    ) -> list[dict[str, str]]:
        """Load a one-shot Keychain snapshot without exposing it to Python."""

        resolved_path = str(socket_path)
        effective_nonce = (
            self._next_credential_nonce
            if self._credential_socket_path == resolved_path and self._next_credential_nonce
            else nonce
        )
        result = self.command(
            "auth.reload_from_socket",
            socket_path=resolved_path,
            nonce=effective_nonce,
        )
        next_nonce = result.get("next_nonce")
        self._credential_socket_path = resolved_path
        self._next_credential_nonce = (
            str(next_nonce) if isinstance(next_nonce, str) and next_nonce else None
        )
        metadata = result.get("credentials")
        return list(metadata) if isinstance(metadata, list) else []

    def reset_credential_socket_nonce(self) -> None:
        self._credential_socket_path = None
        self._next_credential_nonce = None

    def list_models(self, provider_id: str | None = None) -> list[ProviderModel]:
        result = self.command("models.list", provider_id=provider_id)
        raw = result.get("models")
        if not isinstance(raw, list):
            raise PiAIHelperError("protocol_error", "models.list returned invalid payload")
        return [ProviderModel.from_dict(item) for item in raw if isinstance(item, Mapping)]

    def refresh_models(self, provider_id: str | None = None) -> None:
        self.command("models.refresh", provider_id=provider_id, timeout=30.0)

    def iter_stream(
        self,
        *,
        route: ProviderRoute,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]] | None,
        config: ProviderConfig,
        request_id: str | None = None,
    ) -> Iterator[dict[str, Any]]:
        self._ensure_started("stream.start")
        stream_request_id = request_id or uuid.uuid4().hex
        inbox = self._register(stream_request_id)
        timeout_ms = int((config.timeout if config.timeout is not None else 90.0) * 1000)
        payload = {
            "request_id": stream_request_id,
            "command": "stream.start",
            "route": route.as_dict(),
            "messages": list(messages),
            "tools": list(tools or ()),
            "config": {
                "temperature": config.temperature,
                "timeout_ms": timeout_ms,
                "max_output_tokens": route.max_output_tokens,
            },
        }
        self._send(payload)
        try:
            while True:
                frame = inbox.get()
                frame_type = frame.get("type")
                if frame_type == "event":
                    event = frame.get("event")
                    if isinstance(event, dict):
                        yield event
                    continue
                if frame_type == "response":
                    _response_result(frame)
                    return
                raise PiAIHelperError("protocol_error", "unexpected helper stream frame")
        finally:
            self._pending.pop(stream_request_id, None)

    def abort_stream(self, stream_request_id: str, reason: str = "aborted") -> None:
        """Fire-and-forget abort safe to invoke from an AbortToken callback."""

        if not self.is_running:
            return
        request_id = uuid.uuid4().hex
        try:
            self._send(
                {
                    "request_id": request_id,
                    "command": "stream.abort",
                    "stream_request_id": stream_request_id,
                    "reason": reason,
                }
            )
        except PiAIHelperError:
            return

    def close(self, *, force: bool = False) -> None:
        with self._lifecycle_lock:
            process = self._process
            if process is None:
                return
            if process.poll() is None and not force:
                try:
                    self.command("shutdown", timeout=2.0)
                except PiAIHelperError:
                    force = True
            if process.poll() is None:
                if force:
                    process.kill()
                else:
                    process.terminate()
                try:
                    process.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    process.kill()
            for pipe in (process.stdin, process.stdout, process.stderr):
                if pipe is not None:
                    pipe.close()
            self._process = None
            self._hello = None

    def _ensure_started(self, command: str) -> None:
        if command == "hello":
            if not self.is_running:
                raise PiAIHelperError("helper_not_started", "pi-ai helper is not running")
            return
        if not self.is_running:
            self.start()

    def _register(self, request_id: str) -> queue.Queue[dict[str, Any]]:
        inbox: queue.Queue[dict[str, Any]] = queue.Queue()
        self._pending[request_id] = inbox
        return inbox

    def _send(self, payload: Mapping[str, Any]) -> None:
        process = self._process
        if process is None or process.poll() is not None or process.stdin is None:
            raise PiAIHelperError("helper_unavailable", "pi-ai helper is not running")
        serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with self._write_lock:
            try:
                process.stdin.write(serialized + "\n")
                process.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                raise PiAIHelperError("helper_disconnected", "pi-ai helper disconnected") from exc

    def _read_stdout(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        try:
            for line in process.stdout:
                try:
                    frame = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning("[pi-ai] ignored malformed helper frame")
                    continue
                request_id = frame.get("request_id")
                inbox = self._pending.get(request_id) if isinstance(request_id, str) else None
                if inbox is not None:
                    inbox.put(frame)
        finally:
            error = {
                "type": "response",
                "ok": False,
                "error": {
                    "code": "helper_exited",
                    "message": "pi-ai helper exited",
                },
            }
            for inbox in list(self._pending.values()):
                inbox.put(error)

    def _read_stderr(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        for line in process.stderr:
            message = line.strip()
            if message and "disabling flag --expose_wasm" not in message:
                logger.debug("[pi-ai helper] %s", message[:500])


RouteResolver = Callable[[], ProviderRouteSet]
CredentialResolver = Callable[[], Mapping[str, ProviderCredential]]
CredentialSocketResolver = Callable[[], tuple[str | Path, str]]


class PiAIProvider:
    """Provider-neutral adapter driven by pi-ai through the helper."""

    def __init__(
        self,
        *,
        route_resolver: RouteResolver | None = None,
        credential_resolver: CredentialResolver | None = None,
        credential_socket_resolver: CredentialSocketResolver | None = None,
        helper: PiAIHelperClient | None = None,
        default_config: ProviderConfig | None = None,
    ) -> None:
        self._uses_legacy_environment = (
            route_resolver is None and credential_resolver is None
        )
        self._route_resolver = route_resolver
        self._credential_resolver = credential_resolver
        self._credential_socket_resolver = credential_socket_resolver
        self._helper = helper or PiAIHelperClient()
        self._default_config = default_config or ProviderConfig()
        self._catalog: dict[tuple[str, str], ProviderModel] = {}
        self._active_stream_ids: set[str] = set()
        self._active_stream_lock = threading.Lock()
        self._credential_lock = threading.Lock()
        self._credentials_loaded = False
        self._credential_generation = -1
        self._authenticated_provider_ids: set[str] = set()

    @property
    def name(self) -> str:
        return "pi-ai"

    def start(self) -> dict[str, Any]:
        """Start and version-check the helper without loading credentials."""

        return self._helper.start()

    @property
    def is_available(self) -> bool:
        return self._helper.is_running

    @property
    def authenticated_provider_ids(self) -> frozenset[str]:
        with self._credential_lock:
            return frozenset(self._authenticated_provider_ids)

    def model_capabilities(self, model: str | None = None) -> ModelCapabilities:
        try:
            routes = (
                legacy_routes_from_environment()[0]
                if self._uses_legacy_environment
                else self._require_route_resolver()()
            )
        except ValueError:
            return ModelCapabilities()
        route = next(
            (item for item in routes.ordered() if model is None or item.model_id == model),
            routes.primary,
        )
        catalog = self._catalog.get((route.provider_id, route.model_id))
        if catalog is not None:
            return ModelCapabilities(
                context_window=catalog.context_window,
                max_output_tokens=catalog.max_output_tokens,
                supports_tools=catalog.supports_tools,
                supports_thinking=catalog.supports_thinking,
                supports_images=catalog.supports_images,
            )
        return ModelCapabilities(
            context_window=route.context_window,
            max_output_tokens=route.max_output_tokens,
            supports_tools=route.supports_tools,
            supports_thinking=route.supports_thinking,
            supports_images=route.supports_images,
        )

    def list_models(self, provider_id: str | None = None) -> list[ProviderModel]:
        models = self._helper.list_models(provider_id)
        self._catalog.update(
            ((model.provider_id, model.model_id), model) for model in models
        )
        return models

    def refresh_models(self, provider_id: str | None = None) -> list[ProviderModel]:
        self._helper.refresh_models(provider_id)
        return self.list_models(provider_id)

    def stream_sync(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        config: ProviderConfig | None = None,
        abort_token: AbortSignal | None = None,
    ) -> Iterator[ProviderEvent]:
        """Synchronous compatibility surface used by the legacy ChatClient."""

        inbox: queue.Queue[ProviderEvent | BaseException | object] = queue.Queue()
        finished = object()

        def produce() -> None:
            async def collect() -> None:
                async for event in self.stream(
                    messages,
                    tools,
                    config,
                    abort_token,
                ):
                    inbox.put(event)

            try:
                asyncio.run(collect())
            except BaseException as exc:  # noqa: BLE001
                inbox.put(exc)
            finally:
                inbox.put(finished)

        threading.Thread(
            target=produce,
            name="kss-pi-ai-sync-stream",
            daemon=True,
        ).start()
        while True:
            item = inbox.get()
            if item is finished:
                return
            if isinstance(item, BaseException):
                raise item
            if isinstance(item, ProviderEvent):
                yield item

    def abort_active_stream(self) -> None:
        with self._active_stream_lock:
            stream_ids = tuple(self._active_stream_ids)
        for stream_id in stream_ids:
            self._helper.abort_stream(stream_id, "client_abort")

    def invalidate_credentials(self, *, reset_broker_nonce: bool = False) -> None:
        """Require a fresh broker snapshot before the next model stream."""

        with self._credential_lock:
            self._credentials_loaded = False
            self._credential_generation = -1
            self._authenticated_provider_ids.clear()
        if reset_broker_nonce:
            self._helper.reset_credential_socket_nonce()

    def reload_credentials(self) -> frozenset[str]:
        """Synchronously replace the helper's in-memory credential snapshot."""

        if self._credential_socket_resolver is not None:
            socket_path, nonce = self._credential_socket_resolver()
            metadata = self._helper.reload_credentials_from_socket(socket_path, nonce)
        else:
            credentials = (
                legacy_routes_from_environment()[1]
                if self._uses_legacy_environment
                else self._require_credential_resolver()()
            )
            metadata = self._helper.reload_credentials(credentials)
        provider_ids = {
            str(item.get("providerId") or item.get("provider_id"))
            for item in metadata
            if isinstance(item, Mapping)
            and (item.get("providerId") or item.get("provider_id"))
        }
        with self._credential_lock:
            self._authenticated_provider_ids = provider_ids
            self._credentials_loaded = True
            self._credential_generation = getattr(self._helper, "generation", 0)
        return frozenset(provider_ids)

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        config: ProviderConfig | None = None,
        abort_token: AbortSignal | None = None,
    ):
        effective = config or self._default_config
        if _is_aborted(abort_token):
            yield _aborted_event(effective.model or "", getattr(abort_token, "reason", None))
            return
        try:
            if self._uses_legacy_environment:
                routes, legacy_credentials = legacy_routes_from_environment()
            else:
                routes = self._require_route_resolver()()
                legacy_credentials = None
            with self._credential_lock:
                credentials_loaded = (
                    self._credentials_loaded and self._helper.is_running
                    and self._credential_generation
                    == getattr(self._helper, "generation", 0)
                )
            if not credentials_loaded:
                if legacy_credentials is not None:
                    metadata = await asyncio.to_thread(
                        self._helper.reload_credentials, legacy_credentials
                    )
                    provider_ids = {
                        str(item.get("providerId") or item.get("provider_id"))
                        for item in metadata
                        if isinstance(item, Mapping)
                        and (item.get("providerId") or item.get("provider_id"))
                    }
                    with self._credential_lock:
                        self._authenticated_provider_ids = provider_ids
                        self._credentials_loaded = True
                        self._credential_generation = getattr(self._helper, "generation", 0)
                else:
                    await asyncio.to_thread(self.reload_credentials)
        except Exception as exc:  # noqa: BLE001 - process and credential stores vary.
            yield _helper_error_event(effective.model or "", exc, phase="create")
            return

        failures: list[str] = []
        ordered = routes.ordered()
        for candidate_index, raw_route in enumerate(ordered):
            route = raw_route
            if effective.model:
                route = replace(route, model_id=effective.model)
            if effective.thinking_level:
                route = replace(
                    route,
                    thinking_level=effective.thinking_level,
                    supports_thinking=effective.thinking_level != "off",
                )
            stream_id = uuid.uuid4().hex
            with self._active_stream_lock:
                self._active_stream_ids.add(stream_id)
            if abort_token is not None:
                callback = getattr(abort_token, "add_callback", None)
                if callable(callback):
                    callback(
                        lambda current=stream_id: self._helper.abort_stream(
                            current,
                            str(getattr(abort_token, "reason", None) or "aborted"),
                        )
                    )
            emitted_output = False
            iterator: Iterator[dict[str, Any]] | None = None
            try:
                iterator = self._helper.iter_stream(
                    route=route,
                    messages=messages,
                    tools=tools,
                    config=effective,
                    request_id=stream_id,
                )
                while True:
                    frame = await asyncio.to_thread(_next_or_none, iterator)
                    if frame is None:
                        break
                    events = _provider_events_from_helper(frame, route, candidate_index)
                    for event in events:
                        if event.type == "error":
                            if event.error and event.error.code == "aborted":
                                yield event
                                return
                            if not emitted_output and candidate_index + 1 < len(ordered):
                                failures.append(event.error.message if event.error else "provider error")
                                raise _TryFallback
                        else:
                            emitted_output = True
                        yield event
                return
            except _TryFallback:
                continue
            except Exception as exc:  # noqa: BLE001 - helper errors are normalized below.
                if _is_aborted(abort_token):
                    yield _aborted_event(route.model_id, getattr(abort_token, "reason", None))
                    return
                if emitted_output:
                    yield _helper_error_event(
                        route.model_id,
                        exc,
                        phase="stream",
                        code="stream_interrupted",
                        candidate_index=candidate_index,
                    )
                    return
                failures.append(str(exc))
                if candidate_index + 1 < len(ordered):
                    continue
                yield _helper_error_event(
                    route.model_id,
                    exc,
                    phase="create",
                    code="provider_unavailable",
                    candidate_index=candidate_index,
                    failures=failures,
                )
                return
            finally:
                with self._active_stream_lock:
                    self._active_stream_ids.discard(stream_id)

    def close(self) -> None:
        self._helper.close()
        self.invalidate_credentials()

    def _require_route_resolver(self) -> RouteResolver:
        if self._route_resolver is None:
            raise ValueError("route resolver is not configured")
        return self._route_resolver

    def _require_credential_resolver(self) -> CredentialResolver:
        if self._credential_resolver is None:
            raise ValueError("credential resolver is not configured")
        return self._credential_resolver


class _TryFallback(Exception):
    pass


def _provider_events_from_helper(
    frame: Mapping[str, Any],
    route: ProviderRoute,
    candidate_index: int,
) -> list[ProviderEvent]:
    event_type = str(frame.get("type") or "")
    model = str(frame.get("model") or route.model_id)
    provider = str(frame.get("provider") or route.provider_id)
    content_index = _optional_int(frame.get("content_index"))
    metadata = {"candidate_index": candidate_index}
    if frame.get("response_id"):
        metadata["response_id"] = str(frame["response_id"])

    if event_type == "usage":
        usage = _usage_from_mapping(frame.get("usage"))
        return [ProviderEvent(type="usage", model=model, provider=provider, usage=usage, metadata=metadata)]
    if event_type == "finish":
        result: list[ProviderEvent] = []
        usage = _usage_from_mapping(frame.get("usage"))
        if usage is not None:
            result.append(ProviderEvent(type="usage", model=model, provider=provider, usage=usage, metadata=metadata))
        result.append(
            ProviderEvent(
                type="finish",
                model=model,
                provider=provider,
                finish_reason=str(frame.get("reason") or "stop"),
                metadata=metadata,
            )
        )
        return result
    if event_type == "error":
        raw = frame.get("error") if isinstance(frame.get("error"), Mapping) else {}
        phase = str(raw.get("phase") or "stream")
        if phase not in {"create", "stream", "tool_arguments", "abort"}:
            phase = "stream"
        return [
            ProviderEvent(
                type="error",
                model=model,
                provider=provider,
                error=ProviderError(
                    code=str(raw.get("code") or "provider_error"),
                    message=str(raw.get("message") or "provider error"),
                    phase=phase,  # type: ignore[arg-type]
                    retryable=bool(raw.get("retryable", False)),
                ),
                metadata=metadata,
            )
        ]
    if event_type == "tool_call":
        args = frame.get("args")
        if not isinstance(args, Mapping):
            return [
                ProviderEvent(
                    type="error",
                    model=model,
                    provider=provider,
                    error=ProviderError(
                        code="invalid_tool_arguments_type",
                        message="tool args must be an object",
                        phase="tool_arguments",
                        tool_call_id=_optional_string(frame.get("id")),
                        tool_name=_optional_string(frame.get("name")),
                    ),
                    metadata=metadata,
                )
            ]
        return [
            ProviderEvent(
                type="tool_call",
                model=model,
                provider=provider,
                tool_call_id=_optional_string(frame.get("id")),
                tool_name=_optional_string(frame.get("name")),
                tool_arguments=dict(args),
                content_index=content_index,
                metadata=metadata,
            )
        ]
    supported = {
        "text_start",
        "text",
        "text_end",
        "thinking_start",
        "thinking",
        "thinking_end",
        "tool_call_start",
        "tool_call_update",
    }
    if event_type not in supported:
        return []
    return [
        ProviderEvent(
            type=event_type,  # type: ignore[arg-type]
            model=model,
            provider=provider,
            text=_optional_string(frame.get("text")),
            content_index=content_index,
            signature=_optional_string(frame.get("signature")),
            redacted=bool(frame.get("redacted", False)),
            metadata=metadata,
        )
    ]


def _usage_from_mapping(value: Any) -> ProviderUsage | None:
    if not isinstance(value, Mapping):
        return None
    return ProviderUsage(
        input_tokens=_nonnegative_int(value.get("input_tokens")),
        output_tokens=_nonnegative_int(value.get("output_tokens")),
        total_tokens=_nonnegative_int(value.get("total_tokens")),
        cached_input_tokens=_optional_int(value.get("cached_input_tokens")),
        cache_write_tokens=_optional_int(value.get("cache_write_tokens")),
        reasoning_tokens=_optional_int(value.get("reasoning_tokens")),
    )


def _helper_error_event(
    model: str,
    exc: Exception,
    *,
    phase: str,
    code: str | None = None,
    candidate_index: int | None = None,
    failures: Sequence[str] = (),
) -> ProviderEvent:
    helper_code = code or getattr(exc, "code", "helper_unavailable")
    message = str(exc)
    if failures and len(failures) > 1:
        message = "all provider routes failed"
    metadata: dict[str, Any] = {}
    if candidate_index is not None:
        metadata["candidate_index"] = candidate_index
    return ProviderEvent(
        type="error",
        model=model,
        provider="pi-ai",
        error=ProviderError(
            code=str(helper_code),
            message=message,
            phase=phase,  # type: ignore[arg-type]
            retryable=False,
        ),
        metadata=metadata,
    )


def _aborted_event(model: str, reason: Any) -> ProviderEvent:
    return ProviderEvent(
        type="error",
        model=model,
        provider="pi-ai",
        error=ProviderError(
            code="aborted",
            message=str(reason or "aborted"),
            phase="abort",
            retryable=False,
        ),
    )


def _response_result(frame: Mapping[str, Any]) -> dict[str, Any]:
    if not frame.get("ok"):
        raw = frame.get("error") if isinstance(frame.get("error"), Mapping) else {}
        raise PiAIHelperError(
            str(raw.get("code") or "helper_error"),
            str(raw.get("message") or "pi-ai helper error"),
        )
    result = frame.get("result")
    return dict(result) if isinstance(result, Mapping) else {}


def _next_or_none(iterator: Iterator[dict[str, Any]]) -> dict[str, Any] | None:
    try:
        return next(iterator)
    except StopIteration:
        return None


def _is_aborted(token: AbortSignal | None) -> bool:
    checker = getattr(token, "is_aborted", None) if token is not None else None
    return bool(checker()) if callable(checker) else False


def _nonnegative_int(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return max(parsed, 0)


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_string(value: Any) -> str | None:
    return str(value) if value is not None else None


def _resource_root() -> Path:
    # Bundled layout: Resources/kss/agent/pi_ai_provider.py.
    return Path(__file__).resolve().parents[2]


def _find_node() -> Path:
    configured = os.getenv("KSS_PI_AI_NODE", "").strip()
    candidates = [
        Path(configured) if configured else None,
        _resource_root() / "pi-ai-runtime" / "bin" / "node",
        _resource_root() / ".build" / "pi-ai-helper" / "runtime" / "bin" / "node",
    ]
    for candidate in candidates:
        if candidate is not None and candidate.is_file():
            return candidate
    development = shutil.which("node")
    return Path(development) if development else Path("/nonexistent/kss-pi-ai-node")


def _find_helper() -> Path:
    configured = os.getenv("KSS_PI_AI_HELPER", "").strip()
    candidates = [
        Path(configured) if configured else None,
        _resource_root() / "pi-ai-helper" / "helper.mjs",
        _resource_root() / "helpers" / "pi-ai" / "helper.mjs",
        _resource_root() / ".build" / "pi-ai-helper" / "helper" / "helper.mjs",
    ]
    for candidate in candidates:
        if candidate is not None and candidate.is_file():
            return candidate
    return Path("/nonexistent/kss-pi-ai-helper.mjs")


def _minimal_helper_environment() -> dict[str, str]:
    # Built-in provider auth must resolve exclusively through the injected
    # CredentialStore. Do not leak the parent process' API-key environment.
    allowed = {
        "LANG",
        "LC_ALL",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
    }
    return {key: value for key, value in os.environ.items() if key in allowed}


__all__ = [
    "PiAIHelperClient",
    "PiAIHelperError",
    "PiAIProvider",
]
