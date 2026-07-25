"""Provider-neutral agent stream and safe fallback tests."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from kss.agent.provider import (
    OpenAICompatibleProvider,
    ProviderEvent,
    ProviderUsage,
    model_capabilities,
)
from kss.llm.chat_client import ChatClient


def _chunk(content=None, tool_calls=None, finish_reason=None, usage=None):
    delta = SimpleNamespace(content=content, tool_calls=tool_calls)
    choice = SimpleNamespace(delta=delta, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice] if choice is not None else [], usage=usage)


def _usage_chunk(prompt=10, completion=3, cached=2):
    usage = SimpleNamespace(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=prompt + completion,
        prompt_tokens_details=SimpleNamespace(cached_tokens=cached),
    )
    return SimpleNamespace(choices=[], usage=usage)


def _tc(index, *, id=None, name=None, arguments=None):
    function = SimpleNamespace(name=name, arguments=arguments)
    return SimpleNamespace(index=index, id=id, function=function)


class _FailingStream:
    def __init__(self, chunks, error):
        self._chunks = iter(chunks)
        self._error = error
        self.closed = False

    def __iter__(self):
        return self

    def __next__(self):
        try:
            return next(self._chunks)
        except StopIteration:
            raise self._error

    def close(self):
        self.closed = True


class _Stream:
    def __init__(self, chunks):
        self._chunks = iter(chunks)
        self.closed = False

    def __iter__(self):
        return self

    def __next__(self):
        return next(self._chunks)

    def close(self):
        self.closed = True


class _Completions:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class _Client:
    def __init__(self, result):
        self.completions = _Completions(result)
        self.chat = SimpleNamespace(completions=self.completions)


class _AbortToken:
    def __init__(self, aborted=False, reason=None):
        self.aborted = aborted
        self.reason = reason
        self.callbacks = []

    def is_aborted(self):
        return self.aborted

    def add_callback(self, callback):
        self.callbacks.append(callback)
        if self.aborted:
            callback()


def _provider(candidates, clients):
    calls = []

    def factory(key, base, timeout):
        calls.append((key, base, timeout))
        return clients[key]

    provider = OpenAICompatibleProvider(
        credential_resolver=lambda: list(candidates),
        client_factory=factory,
    )
    return provider, calls


def test_primary_failure_before_output_uses_fallback():
    fallback_stream = _Stream([_chunk(content="备用"), _chunk(finish_reason="stop")])
    provider, calls = _provider(
        [("primary", None, "model-a"), ("fallback", None, "model-b")],
        {
            "primary": _Client(RuntimeError("primary down")),
            "fallback": _Client(fallback_stream),
        },
    )

    events = list(provider.stream_sync([{"role": "user", "content": "hi"}]))

    assert [event.type for event in events] == ["text", "finish"]
    assert events[0].text == "备用"
    assert events[0].model == "model-b"
    assert [call[0] for call in calls] == ["primary", "fallback"]


def test_stream_failure_before_output_uses_fallback():
    primary_stream = _FailingStream([], RuntimeError("socket closed"))
    provider, calls = _provider(
        [("primary", None, "model-a"), ("fallback", None, "model-b")],
        {
            "primary": _Client(primary_stream),
            "fallback": _Client(_Stream([_chunk(content="ok", finish_reason="stop")])),
        },
    )

    events = list(provider.stream_sync([{"role": "user", "content": "hi"}]))

    assert [event.type for event in events] == ["text", "finish"]
    assert [call[0] for call in calls] == ["primary", "fallback"]
    assert primary_stream.closed is True


def test_stream_failure_after_text_never_replays_on_fallback():
    primary_stream = _FailingStream(
        [_chunk(content="partial")],
        RuntimeError("stream broke"),
    )
    provider, calls = _provider(
        [("primary", None, "model-a"), ("fallback", None, "model-b")],
        {
            "primary": _Client(primary_stream),
            "fallback": _Client(_Stream([_chunk(content="duplicate")])),
        },
    )

    events = list(provider.stream_sync([{"role": "user", "content": "hi"}]))

    assert [event.type for event in events] == ["text", "error"]
    assert events[1].error is not None
    assert events[1].error.code == "stream_interrupted"
    assert [call[0] for call in calls] == ["primary"]


def test_partial_tool_call_counts_as_output_and_prevents_fallback():
    primary_stream = _FailingStream(
        [_chunk(tool_calls=[_tc(0, id="c1", name="lookup", arguments='{"sym')])],
        RuntimeError("stream broke"),
    )
    provider, calls = _provider(
        [("primary", None, "model-a"), ("fallback", None, "model-b")],
        {
            "primary": _Client(primary_stream),
            "fallback": _Client(_Stream([_chunk(content="duplicate")])),
        },
    )

    events = list(provider.stream_sync([{"role": "user", "content": "hi"}], tools=[{}]))

    assert [event.type for event in events] == ["error"]
    assert events[0].error is not None
    assert events[0].error.code == "stream_interrupted"
    assert [call[0] for call in calls] == ["primary"]


def test_fragmented_tool_call_is_normalized_without_sdk_objects():
    stream = _Stream([
        _chunk(tool_calls=[_tc(0, id="c1", name="lookup")]),
        _chunk(tool_calls=[_tc(0, arguments='{"symbol":')]),
        _chunk(tool_calls=[_tc(0, arguments='"688008.SH"}')]),
        _chunk(finish_reason="tool_calls"),
    ])
    provider, _ = _provider(
        [("primary", None, "model-a")],
        {"primary": _Client(stream)},
    )

    events = list(provider.stream_sync([{"role": "user", "content": "hi"}], tools=[{}]))

    call = next(event for event in events if event.type == "tool_call")
    assert call.tool_call_id == "c1"
    assert call.tool_name == "lookup"
    assert call.tool_arguments == {"symbol": "688008.SH"}
    assert call.as_dict()["args"] == {"symbol": "688008.SH"}


def test_malformed_tool_arguments_emit_structured_error():
    provider, _ = _provider(
        [("primary", None, "model-a")],
        {
            "primary": _Client(_Stream([
                _chunk(tool_calls=[_tc(0, id="bad", name="lookup", arguments="{oops")]),
                _chunk(finish_reason="tool_calls"),
            ])),
        },
    )

    events = list(provider.stream_sync([{"role": "user", "content": "hi"}], tools=[{}]))

    error = next(event for event in events if event.type == "error")
    assert error.error is not None
    assert error.error.code == "malformed_tool_arguments"
    assert error.error.phase == "tool_arguments"
    assert error.error.tool_call_id == "bad"
    assert error.error.tool_name == "lookup"
    assert error.error.raw_arguments == "{oops"


def test_usage_chunk_is_normalized():
    provider, _ = _provider(
        [("primary", None, "model-a")],
        {
            "primary": _Client(_Stream([
                _chunk(content="ok"),
                _usage_chunk(),
                _chunk(finish_reason="stop"),
            ])),
        },
    )

    events = list(provider.stream_sync([{"role": "user", "content": "hi"}]))

    usage = next(event.usage for event in events if event.type == "usage")
    assert usage is not None
    assert usage.as_dict() == {
        "input_tokens": 10,
        "output_tokens": 3,
        "total_tokens": 13,
        "cached_input_tokens": 2,
    }


def test_credentials_are_resolved_for_each_run():
    state = {"key": "first"}
    clients = {
        "first": _Client(_Stream([_chunk(content="one", finish_reason="stop")])),
        "second": _Client(_Stream([_chunk(content="two", finish_reason="stop")])),
    }
    resolutions = []

    def resolver():
        resolutions.append(state["key"])
        return [(state["key"], None, f"model-{state['key']}")]

    provider = OpenAICompatibleProvider(
        credential_resolver=resolver,
        client_factory=lambda key, _base, _timeout: clients[key],
    )
    first = list(provider.stream_sync([{"role": "user", "content": "hi"}]))
    state["key"] = "second"
    second = list(provider.stream_sync([{"role": "user", "content": "hi"}]))

    assert resolutions == ["first", "second"]
    assert first[0].text == "one"
    assert second[0].text == "two"


def test_async_stream_exposes_same_normalized_events():
    provider, _ = _provider(
        [("primary", None, "model-a")],
        {"primary": _Client(_Stream([_chunk(content="async", finish_reason="stop")]))},
    )

    async def collect():
        return [
            event
            async for event in provider.stream([{"role": "user", "content": "hi"}])
        ]

    events = asyncio.run(collect())
    assert [event.type for event in events] == ["text", "finish"]
    assert events[0].text == "async"


def test_pre_aborted_run_never_resolves_credentials_or_creates_client():
    calls = []
    provider = OpenAICompatibleProvider(
        credential_resolver=lambda: calls.append("resolved") or [("key", None, "model")],
        client_factory=lambda *_args: calls.append("created"),
    )

    events = list(provider.stream_sync(
        [{"role": "user", "content": "hi"}],
        abort_token=_AbortToken(aborted=True, reason="client_abort"),
    ))

    assert calls == []
    assert len(events) == 1
    assert events[0].type == "error"
    assert events[0].error is not None
    assert events[0].error.code == "aborted"
    assert events[0].error.message == "client_abort"


def test_model_capabilities_use_conservative_defaults_and_env(monkeypatch):
    monkeypatch.delenv("KSS_LLM_CONTEXT_WINDOW", raising=False)
    monkeypatch.delenv("KSS_LLM_MAX_OUTPUT_TOKENS", raising=False)
    assert model_capabilities("gateway/custom").context_window == 32_000
    assert model_capabilities("gateway/custom").max_output_tokens == 8_000

    monkeypatch.setenv("KSS_LLM_CONTEXT_WINDOW", "65536")
    monkeypatch.setenv("KSS_LLM_MAX_OUTPUT_TOKENS", "4096")
    capabilities = model_capabilities("gateway/custom")
    assert capabilities.context_window == 65_536
    assert capabilities.max_output_tokens == 4_096


def test_chat_client_facade_maps_usage_and_delegates_abort():
    class FakeProvider:
        aborted = False

        def stream_sync(self, messages, tools, config):
            assert messages == [{"role": "user", "content": "hi"}]
            yield ProviderEvent(
                type="usage",
                model="model-a",
                usage=ProviderUsage(input_tokens=4, output_tokens=2, total_tokens=6),
            )

        def abort_active_stream(self):
            self.aborted = True

    provider = FakeProvider()
    client = ChatClient(provider=provider)

    events = list(client.stream_turn([{"role": "user", "content": "hi"}]))
    client.abort_active_stream()

    assert events == [{
        "type": "usage",
        "usage": {"input_tokens": 4, "output_tokens": 2, "total_tokens": 6},
    }]
    assert provider.aborted is True
