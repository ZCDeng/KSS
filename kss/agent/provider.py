"""Provider-neutral streaming boundary for the KSS agent runtime.

The agent runtime must not depend on OpenAI SDK chunk objects.  This module
normalizes an OpenAI-compatible stream into a small event vocabulary and owns
the one safe fallback rule: a secondary credential may be tried only before the
primary provider has produced any user-visible/model output.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, AsyncIterator, Callable, Iterator, Literal, Protocol

from kss.llm.openai_client import (
    LLMUnavailable,
    _coerce_float,
    _resolve_credential_candidates,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from kss.agent.provider_route import ProviderRouteSet

ProviderEventType = Literal[
    "text_start",
    "text",
    "text_end",
    "thinking_start",
    "thinking",
    "thinking_end",
    "tool_call_start",
    "tool_call_update",
    "tool_call",
    "usage",
    "finish",
    "error",
]
_DEFAULT_TIMEOUT_SEC = 90.0
_DEFAULT_TEMPERATURE = 0.4


@dataclass(frozen=True)
class ModelCapabilities:
    """Capabilities needed by context assembly and tool orchestration."""

    context_window: int = 32_000
    max_output_tokens: int = 8_000
    supports_tools: bool = True
    supports_thinking: bool = False
    supports_images: bool = False


@dataclass(frozen=True)
class ProviderUsage:
    """Provider-neutral token usage."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cached_input_tokens: int | None = None
    cache_write_tokens: int | None = None
    reasoning_tokens: int | None = None

    def as_dict(self) -> dict[str, int]:
        result = {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
        }
        if self.cached_input_tokens is not None:
            result["cached_input_tokens"] = self.cached_input_tokens
        if self.cache_write_tokens is not None:
            result["cache_write_tokens"] = self.cache_write_tokens
        if self.reasoning_tokens is not None:
            result["reasoning_tokens"] = self.reasoning_tokens
        return result


@dataclass(frozen=True)
class ProviderError:
    """Structured provider failure safe to expose to the runtime."""

    code: str
    message: str
    phase: Literal["create", "stream", "tool_arguments", "abort"]
    retryable: bool = False
    tool_call_id: str | None = None
    tool_name: str | None = None
    raw_arguments: str | None = None

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "phase": self.phase,
            "retryable": self.retryable,
        }
        if self.tool_call_id is not None:
            result["tool_call_id"] = self.tool_call_id
        if self.tool_name is not None:
            result["tool_name"] = self.tool_name
        if self.raw_arguments is not None:
            result["raw_arguments"] = self.raw_arguments
        return result


@dataclass(frozen=True)
class ProviderEvent:
    """One normalized event from a model provider."""

    type: ProviderEventType
    model: str
    provider: str = "openai-compatible"
    text: str | None = None
    content_index: int | None = None
    signature: str | None = None
    redacted: bool = False
    tool_call_id: str | None = None
    tool_name: str | None = None
    tool_arguments: dict[str, Any] | None = None
    usage: ProviderUsage | None = None
    finish_reason: str | None = None
    error: ProviderError | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "type": self.type,
            "model": self.model,
            "provider": self.provider,
        }
        if self.text is not None:
            result["text"] = self.text
        if self.content_index is not None:
            result["content_index"] = self.content_index
        if self.signature is not None:
            result["signature"] = self.signature
        if self.redacted:
            result["redacted"] = True
        if self.tool_call_id is not None:
            result["id"] = self.tool_call_id
        if self.tool_name is not None:
            result["name"] = self.tool_name
        if self.tool_arguments is not None:
            result["args"] = self.tool_arguments
        if self.usage is not None:
            result["usage"] = self.usage.as_dict()
        if self.finish_reason is not None:
            result["reason"] = self.finish_reason
        if self.error is not None:
            result["error"] = self.error.as_dict()
        result.update(self.metadata)
        return result


@dataclass(frozen=True)
class ProviderConfig:
    """Per-call configuration; credentials are intentionally not stored here."""

    model: str | None = None
    temperature: float = _DEFAULT_TEMPERATURE
    timeout: float | None = None
    include_usage: bool = False
    thinking_level: str | None = None
    # A session may choose a non-secret route for one stream without mutating
    # the process-wide default. Legacy providers intentionally ignore it.
    route_set: "ProviderRouteSet | None" = None


class AbortSignal(Protocol):
    """Small protocol shared with the runtime without importing its token type."""

    def add_callback(self, callback: Callable[[], None]) -> None: ...

    def is_aborted(self) -> bool: ...


class LLMProvider(Protocol):
    @property
    def name(self) -> str: ...

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        config: ProviderConfig | None = None,
        abort_token: AbortSignal | None = None,
    ) -> AsyncIterator[ProviderEvent]: ...

    def model_capabilities(self, model: str | None = None) -> ModelCapabilities: ...


CredentialCandidate = tuple[str, str | None, str]
CredentialResolver = Callable[[], list[CredentialCandidate]]
ClientFactory = Callable[[str, str | None, float], Any]


def model_capabilities(model: str | None = None) -> ModelCapabilities:
    """Return conservative capabilities, with explicit environment overrides.

    OpenAI-compatible gateways can expose arbitrary model ids, so guessing a
    context size from the name is unsafe.  The runtime gets stable 32k/8k
    defaults unless deployment configuration supplies trusted values.
    """

    del model  # Model-specific discovery can be added by a provider implementation.
    return ModelCapabilities(
        context_window=_positive_int_env("KSS_LLM_CONTEXT_WINDOW", 32_000),
        max_output_tokens=_positive_int_env("KSS_LLM_MAX_OUTPUT_TOKENS", 8_000),
    )


class OpenAICompatibleProvider:
    """Normalize OpenAI-compatible synchronous SDK streams.

    Credential candidates are resolved at the beginning of every ``stream``
    call.  This is important for Keychain/settings changes in a running app.
    """

    def __init__(
        self,
        *,
        credential_resolver: CredentialResolver = _resolve_credential_candidates,
        client_factory: ClientFactory | None = None,
        default_config: ProviderConfig | None = None,
    ) -> None:
        self._credential_resolver = credential_resolver
        self._client_factory = client_factory or self._build_sdk_client
        self._default_config = default_config or ProviderConfig()
        self._streams_lock = threading.Lock()
        self._active_streams: list[Any] = []

    @property
    def name(self) -> str:
        return "openai-compatible"

    def model_capabilities(self, model: str | None = None) -> ModelCapabilities:
        return model_capabilities(model)

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        config: ProviderConfig | None = None,
        abort_token: AbortSignal | None = None,
    ) -> AsyncIterator[ProviderEvent]:
        iterator = self.stream_sync(messages, tools, config, abort_token)
        while True:
            event = await asyncio.to_thread(_next_or_none, iterator)
            if event is None:
                break
            yield event

    def stream_sync(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        config: ProviderConfig | None = None,
        abort_token: AbortSignal | None = None,
    ) -> Iterator[ProviderEvent]:
        effective = config or self._default_config
        if _is_aborted(abort_token):
            yield self._aborted_event(
                effective.model or os.getenv("KSS_LLM_MODEL") or "",
                abort_token,
            )
            return
        try:
            candidates = self._credential_resolver()
        except Exception as exc:  # noqa: BLE001 - credential sources vary by deployment.
            yield self._error_event(
                effective.model or os.getenv("KSS_LLM_MODEL") or "",
                "credentials_unavailable",
                str(exc),
                phase="create",
            )
            return
        if not candidates:
            yield self._error_event(
                effective.model or "",
                "credentials_unavailable",
                "未配置可用的 LLM 凭据",
                phase="create",
            )
            return

        failures: list[str] = []
        for candidate_index, (api_key, base_url, default_model) in enumerate(candidates):
            model = effective.model or os.getenv("KSS_LLM_MODEL") or default_model
            emitted_model_output = False
            stream: Any | None = None
            failure_phase: Literal["create", "stream"] = "create"
            try:
                timeout = (
                    effective.timeout
                    if effective.timeout is not None
                    else _coerce_float(os.getenv("KSS_LLM_TIMEOUT"), _DEFAULT_TIMEOUT_SEC)
                )
                client = self._client_factory(api_key, base_url, timeout)
                kwargs: dict[str, Any] = {
                    "model": model,
                    "messages": messages,
                    "temperature": effective.temperature,
                    "stream": True,
                }
                if tools:
                    kwargs["tools"] = tools
                    kwargs["tool_choice"] = "auto"
                if effective.include_usage:
                    kwargs["stream_options"] = {"include_usage": True}
                stream = client.chat.completions.create(**kwargs)
                failure_phase = "stream"
                self._track_stream(stream)
                if abort_token is not None:
                    add_callback = getattr(abort_token, "add_callback", None)
                    if callable(add_callback):
                        add_callback(lambda current=stream: self._close_stream(current))

                accumulator: dict[int, dict[str, Any]] = {}
                finish_reason: str | None = None
                for chunk in stream:
                    if _is_aborted(abort_token):
                        raise _ProviderAborted
                    usage = _usage_from_chunk(chunk)
                    if usage is not None:
                        emitted_model_output = True
                        yield ProviderEvent(type="usage", model=model, usage=usage)

                    choices = getattr(chunk, "choices", None)
                    if not choices:
                        continue
                    choice = choices[0]
                    if getattr(choice, "finish_reason", None):
                        finish_reason = str(choice.finish_reason)
                        emitted_model_output = True
                    delta = getattr(choice, "delta", None)
                    if delta is None:
                        continue
                    content = getattr(delta, "content", None)
                    if content:
                        emitted_model_output = True
                        yield ProviderEvent(type="text", model=model, text=str(content))
                    tool_calls = getattr(delta, "tool_calls", None)
                    if tool_calls:
                        # A partial tool call is model output even though the
                        # normalized event is emitted only after reassembly.
                        emitted_model_output = True
                        _accumulate_tool_calls(accumulator, tool_calls)

                if _is_aborted(abort_token):
                    raise _ProviderAborted
                for event in _tool_events(accumulator, model):
                    yield event
                yield ProviderEvent(
                    type="finish",
                    model=model,
                    finish_reason=finish_reason or "stop",
                    metadata={"candidate_index": candidate_index},
                )
                return
            except _ProviderAborted:
                yield self._aborted_event(model, abort_token)
                return
            except Exception as exc:  # noqa: BLE001 - SDK/network errors vary by gateway.
                if _is_aborted(abort_token):
                    yield self._aborted_event(model, abort_token)
                    return
                failure = str(exc)
                failures.append(failure)
                if emitted_model_output:
                    logger.warning("[provider] stream failed after output: %s", exc)
                    yield self._error_event(
                        model,
                        "stream_interrupted",
                        f"流式中断: {exc}",
                        phase="stream",
                        retryable=False,
                        candidate_index=candidate_index,
                    )
                    return
                logger.warning(
                    "[provider] candidate %d failed before output: %s",
                    candidate_index,
                    exc,
                )
                if candidate_index + 1 < len(candidates):
                    continue
                message = (
                    "LLM 调用失败"
                    if len(failures) == 1
                    else "LLM 调用失败（主备均不可用）"
                )
                yield self._error_event(
                    model,
                    "provider_unavailable",
                    f"{message}: {'; '.join(failures)}",
                    phase=failure_phase,
                    retryable=False,
                    candidate_index=candidate_index,
                )
                return
            finally:
                if stream is not None:
                    self._untrack_stream(stream)
                    self._close_stream(stream)

    def abort_active_stream(self) -> None:
        """Close all active SDK streams owned by this provider instance."""

        with self._streams_lock:
            streams = list(self._active_streams)
        for stream in streams:
            self._close_stream(stream)

    def _track_stream(self, stream: Any) -> None:
        with self._streams_lock:
            self._active_streams.append(stream)

    def _untrack_stream(self, stream: Any) -> None:
        with self._streams_lock:
            self._active_streams = [item for item in self._active_streams if item is not stream]

    @staticmethod
    def _close_stream(stream: Any) -> None:
        close = getattr(stream, "close", None)
        if callable(close):
            try:
                close()
            except Exception as exc:  # noqa: BLE001
                logger.debug("[provider] close stream failed: %s", exc)

    @staticmethod
    def _build_sdk_client(api_key: str, base_url: str | None, timeout: float) -> Any:
        try:
            from openai import OpenAI  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover
            raise LLMUnavailable("openai 包未安装,请 pip install openai") from exc
        kwargs: dict[str, Any] = {
            "api_key": api_key,
            "timeout": timeout,
            "max_retries": 1,
        }
        if base_url:
            kwargs["base_url"] = base_url
        return OpenAI(**kwargs)

    @staticmethod
    def _error_event(
        model: str,
        code: str,
        message: str,
        *,
        phase: Literal["create", "stream", "tool_arguments", "abort"],
        retryable: bool = False,
        candidate_index: int | None = None,
    ) -> ProviderEvent:
        metadata = {}
        if candidate_index is not None:
            metadata["candidate_index"] = candidate_index
        return ProviderEvent(
            type="error",
            model=model,
            error=ProviderError(
                code=code,
                message=message,
                phase=phase,
                retryable=retryable,
            ),
            metadata=metadata,
        )

    @staticmethod
    def _aborted_event(model: str, abort_token: AbortSignal | None) -> ProviderEvent:
        reason = getattr(abort_token, "reason", None) or "aborted"
        return ProviderEvent(
            type="error",
            model=model,
            error=ProviderError(
                code="aborted",
                message=str(reason),
                phase="abort",
                retryable=False,
            ),
        )


def _positive_int_env(key: str, default: int) -> int:
    raw = os.getenv(key, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


class _ProviderAborted(Exception):
    """Internal control-flow signal; never exposed outside normalized events."""


def _is_aborted(abort_token: AbortSignal | None) -> bool:
    if abort_token is None:
        return False
    checker = getattr(abort_token, "is_aborted", None)
    return bool(checker()) if callable(checker) else False


def _accumulate_tool_calls(acc: dict[int, dict[str, Any]], tool_calls: Any) -> None:
    for tool_call in tool_calls:
        index = getattr(tool_call, "index", 0) or 0
        slot = acc.setdefault(index, {"id": None, "name": None, "args": ""})
        if getattr(tool_call, "id", None):
            slot["id"] = tool_call.id
        function = getattr(tool_call, "function", None)
        if function is not None:
            if getattr(function, "name", None):
                slot["name"] = function.name
            if getattr(function, "arguments", None):
                slot["args"] += function.arguments


def _tool_events(
    accumulator: dict[int, dict[str, Any]], model: str
) -> Iterator[ProviderEvent]:
    for index in sorted(accumulator):
        slot = accumulator[index]
        raw_arguments = (slot.get("args") or "").strip()
        try:
            arguments = json.loads(raw_arguments) if raw_arguments else {}
        except json.JSONDecodeError as exc:
            yield ProviderEvent(
                type="error",
                model=model,
                error=ProviderError(
                    code="malformed_tool_arguments",
                    message=f"tool args JSON 解析失败: {exc.msg}",
                    phase="tool_arguments",
                    tool_call_id=slot.get("id"),
                    tool_name=slot.get("name"),
                    raw_arguments=raw_arguments[:512],
                ),
            )
            continue
        if not isinstance(arguments, dict):
            yield ProviderEvent(
                type="error",
                model=model,
                error=ProviderError(
                    code="invalid_tool_arguments_type",
                    message="tool args 必须是 JSON object",
                    phase="tool_arguments",
                    tool_call_id=slot.get("id"),
                    tool_name=slot.get("name"),
                    raw_arguments=raw_arguments[:512],
                ),
            )
            continue
        yield ProviderEvent(
            type="tool_call",
            model=model,
            tool_call_id=slot.get("id"),
            tool_name=slot.get("name"),
            tool_arguments=arguments,
        )


def _usage_from_chunk(chunk: Any) -> ProviderUsage | None:
    usage = getattr(chunk, "usage", None)
    if usage is None:
        return None
    input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
    total_tokens = int(getattr(usage, "total_tokens", input_tokens + output_tokens) or 0)
    prompt_details = getattr(usage, "prompt_tokens_details", None)
    cached = getattr(prompt_details, "cached_tokens", None) if prompt_details is not None else None
    return ProviderUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        cached_input_tokens=int(cached) if cached is not None else None,
    )


def _next_or_none(iterator: Iterator[ProviderEvent]) -> ProviderEvent | None:
    try:
        return next(iterator)
    except StopIteration:
        return None


__all__ = [
    "AbortSignal",
    "LLMProvider",
    "ModelCapabilities",
    "OpenAICompatibleProvider",
    "ProviderConfig",
    "ProviderError",
    "ProviderEvent",
    "ProviderUsage",
    "model_capabilities",
]
