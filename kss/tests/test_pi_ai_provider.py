"""Pinned pi-ai helper protocol and Python adapter tests."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import tempfile
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from kss.agent.pi_ai_provider import PiAIHelperClient, PiAIProvider
from kss.agent.provider import ProviderConfig
from kss.agent.provider_route import (
    ProviderCredential,
    ProviderRoute,
    ProviderRouteSet,
    legacy_routes_from_environment,
)

_REPO = Path(__file__).resolve().parents[2]
_HELPER = _REPO / "helpers" / "pi-ai" / "helper.mjs"


class _FakeHelper:
    def __init__(self, frames_by_provider):
        self.frames_by_provider = frames_by_provider
        self.credential_snapshots = []
        self.streamed = []
        self.aborts = []

    def reload_credentials(self, credentials):
        self.credential_snapshots.append(dict(credentials))
        return [
            {"providerId": provider_id, "type": "api_key"}
            for provider_id in credentials
        ]

    def iter_stream(
        self,
        *,
        route,
        messages,
        tools,
        config,
        request_id,
    ) -> Iterator[dict]:
        self.streamed.append((route.provider_id, request_id, messages, tools, config))
        yield from self.frames_by_provider[route.provider_id]

    def abort_stream(self, stream_request_id, reason):
        self.aborts.append((stream_request_id, reason))

    def close(self):
        pass


class _AbortToken:
    def __init__(self):
        self.aborted = False
        self.reason = None
        self.callbacks = []

    def is_aborted(self):
        return self.aborted

    def add_callback(self, callback):
        self.callbacks.append(callback)

    def abort(self, reason):
        self.reason = reason
        self.aborted = True
        for callback in self.callbacks:
            callback()


def _route(provider_id, model_id):
    return ProviderRoute(
        provider_id=provider_id,
        model_id=model_id,
        supports_images=True,
        supports_thinking=True,
        thinking_level="medium",
        context_window=64_000,
        max_output_tokens=4_096,
    )


def _credentials():
    return {
        "primary": ProviderCredential("primary", "primary-secret"),
        "fallback": ProviderCredential("fallback", "fallback-secret"),
    }


def test_provider_uses_fallback_only_before_output():
    helper = _FakeHelper(
        {
            "primary": [
                {
                    "type": "error",
                    "model": "model-a",
                    "provider": "primary",
                    "error": {
                        "code": "provider_error",
                        "message": "primary down",
                        "phase": "stream",
                    },
                }
            ],
            "fallback": [
                {
                    "type": "text",
                    "model": "model-b",
                    "provider": "fallback",
                    "text": "ok",
                    "content_index": 0,
                },
                {
                    "type": "finish",
                    "model": "model-b",
                    "provider": "fallback",
                    "reason": "stop",
                },
            ],
        }
    )
    provider = PiAIProvider(
        route_resolver=lambda: ProviderRouteSet(
            _route("primary", "model-a"),
            _route("fallback", "model-b"),
        ),
        credential_resolver=_credentials,
        helper=helper,
    )

    async def collect():
        return [
            event
            async for event in provider.stream([{"role": "user", "content": "hi"}])
        ]

    events = asyncio.run(collect())

    assert [event.type for event in events] == ["text", "finish"]
    assert events[0].text == "ok"
    assert [item[0] for item in helper.streamed] == ["primary", "fallback"]
    assert len(helper.credential_snapshots) == 1
    assert provider.authenticated_provider_ids == frozenset({"primary", "fallback"})


def test_provider_never_replays_after_thinking_output():
    helper = _FakeHelper(
        {
            "primary": [
                {
                    "type": "thinking",
                    "model": "model-a",
                    "provider": "primary",
                    "text": "partial reasoning",
                    "content_index": 0,
                },
                {
                    "type": "error",
                    "model": "model-a",
                    "provider": "primary",
                    "error": {
                        "code": "provider_error",
                        "message": "stream broke",
                        "phase": "stream",
                    },
                },
            ],
            "fallback": [
                {
                    "type": "text",
                    "model": "model-b",
                    "provider": "fallback",
                    "text": "duplicate",
                }
            ],
        }
    )
    provider = PiAIProvider(
        route_resolver=lambda: ProviderRouteSet(
            _route("primary", "model-a"),
            _route("fallback", "model-b"),
        ),
        credential_resolver=_credentials,
        helper=helper,
    )

    async def collect():
        return [
            event
            async for event in provider.stream([{"role": "user", "content": "hi"}])
        ]

    events = asyncio.run(collect())

    assert [event.type for event in events] == ["thinking", "error"]
    assert [item[0] for item in helper.streamed] == ["primary"]


def test_provider_maps_interleaved_thinking_usage_and_capabilities():
    route = _route("primary", "model-a")
    helper = _FakeHelper(
        {
            "primary": [
                {"type": "thinking_start", "content_index": 0},
                {
                    "type": "thinking",
                    "text": "reason",
                    "content_index": 0,
                },
                {
                    "type": "thinking_end",
                    "text": "reason",
                    "signature": "opaque",
                    "content_index": 0,
                },
                {
                    "type": "text",
                    "text": "answer",
                    "content_index": 1,
                },
                {
                    "type": "finish",
                    "reason": "stop",
                    "usage": {
                        "input_tokens": 10,
                        "output_tokens": 4,
                        "total_tokens": 14,
                        "reasoning_tokens": 2,
                    },
                },
            ]
        }
    )
    provider = PiAIProvider(
        route_resolver=lambda: ProviderRouteSet(route),
        credential_resolver=lambda: {
            "primary": ProviderCredential("primary", "secret")
        },
        helper=helper,
    )

    async def collect():
        return [
            event
            async for event in provider.stream(
                [{"role": "user", "content": "hi"}],
                config=ProviderConfig(thinking_level="high"),
            )
        ]

    events = asyncio.run(collect())
    caps = provider.model_capabilities("model-a")

    assert [event.type for event in events] == [
        "thinking_start",
        "thinking",
        "thinking_end",
        "text",
        "usage",
        "finish",
    ]
    assert events[2].signature == "opaque"
    assert events[3].content_index == 1
    assert events[4].usage is not None
    assert events[4].usage.reasoning_tokens == 2
    assert caps.context_window == 64_000
    assert caps.max_output_tokens == 4_096
    assert caps.supports_images is True
    assert caps.supports_thinking is True


def test_abort_callback_targets_current_helper_stream():
    token = _AbortToken()

    class AbortingHelper(_FakeHelper):
        def iter_stream(self, **kwargs):
            token.abort("client_stop")
            yield {
                "type": "error",
                "error": {
                    "code": "aborted",
                    "message": "client_stop",
                    "phase": "abort",
                },
            }

    helper = AbortingHelper({"primary": []})
    provider = PiAIProvider(
        route_resolver=lambda: ProviderRouteSet(_route("primary", "model-a")),
        credential_resolver=lambda: {
            "primary": ProviderCredential("primary", "secret")
        },
        helper=helper,
    )

    async def collect():
        return [
            event
            async for event in provider.stream(
                [{"role": "user", "content": "hi"}],
                abort_token=token,
            )
        ]

    events = asyncio.run(collect())

    assert len(events) == 1
    assert events[0].error is not None
    assert events[0].error.code == "aborted"
    assert helper.aborts and helper.aborts[0][1] == "client_stop"


def test_provider_route_validation_and_legacy_env(monkeypatch):
    with pytest.raises(ValueError, match="https"):
        ProviderRoute(
            provider_id="custom",
            model_id="model",
            base_url="http://remote.example/v1",
        )
    local = ProviderRoute(
        provider_id="custom",
        model_id="anthropic/claude-sonnet",
        base_url="http://localhost:11434/v1",
    )
    assert local.base_url == "http://localhost:11434/v1"

    for key in [
        "KSS_LLM_PRIMARY_KEY",
        "KSS_LLM_PRIMARY_BASE_URL",
        "KSS_LLM_PRIMARY_MODEL",
        "KSS_LLM_FALLBACK_KEY",
        "KSS_LLM_FALLBACK_BASE_URL",
        "KSS_LLM_FALLBACK_MODEL",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "DEEPSEEK_API_KEY",
    ]:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("KSS_LLM_PRIMARY_KEY", "secret")
    monkeypatch.setenv("KSS_LLM_PRIMARY_MODEL", "gateway-model")
    monkeypatch.setenv("KSS_LLM_PRIMARY_BASE_URL", "https://gateway.example/v1")

    routes, credentials = legacy_routes_from_environment()

    assert routes.primary.provider_id == "kss-primary"
    assert routes.primary.model_id == "gateway-model"
    assert credentials["kss-primary"].as_helper_dict()["key"] == "secret"
    assert "secret" not in repr(credentials["kss-primary"])
    assert "key" not in routes.as_dict()["primary"]


def test_provider_route_accepts_secret_presence_flags(monkeypatch):
    for key in [
        "KSS_LLM_PRIMARY_KEY",
        "KSS_LLM_PRIMARY_CREDENTIAL_PRESENT",
        "KSS_LLM_PRIMARY_BASE_URL",
        "KSS_LLM_PRIMARY_MODEL",
        "KSS_LLM_FALLBACK_KEY",
        "KSS_LLM_FALLBACK_CREDENTIAL_PRESENT",
        "KSS_LLM_FALLBACK_BASE_URL",
        "KSS_LLM_FALLBACK_MODEL",
        "OPENAI_API_KEY",
        "OPENAI_API_KEY_PRESENT",
        "OPENAI_BASE_URL",
        "DEEPSEEK_API_KEY",
        "DEEPSEEK_API_KEY_PRESENT",
    ]:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("KSS_LLM_PRIMARY_CREDENTIAL_PRESENT", "1")
    monkeypatch.setenv("KSS_LLM_PRIMARY_MODEL", "gateway-model")
    monkeypatch.setenv("KSS_LLM_PRIMARY_BASE_URL", "https://gateway.example/v1")

    routes, credentials = legacy_routes_from_environment()

    assert routes.primary.provider_id == "kss-primary"
    assert routes.primary.model_id == "gateway-model"
    assert credentials == {}


@pytest.mark.skipif(shutil.which("node") is None, reason="Node unavailable")
def test_real_helper_mock_protocol_never_echoes_secret():
    client = PiAIHelperClient(
        node_path=shutil.which("node"),
        helper_path=_HELPER,
        mock=True,
    )
    try:
        hello = client.start()
        metadata = client.reload_credentials(
            {"mock": ProviderCredential("mock", "sk-never-echo-this")}
        )
        models = client.list_models()
        events = list(
            client.iter_stream(
                route=_route("mock", "mock-model"),
                messages=[{"role": "user", "content": "hello"}],
                tools=None,
                config=ProviderConfig(),
            )
        )
    finally:
        client.close()

    assert hello["protocol_version"] == 1
    assert hello["pi_ai_version"] == "0.82.1"
    assert metadata == [{"providerId": "mock", "type": "api_key"}]
    assert models[0].supports_images is True
    assert [event["type"] for event in events] == [
        "text_start",
        "text",
        "text_end",
        "usage",
        "finish",
    ]
    assert "sk-never-echo-this" not in repr((hello, metadata, models, events))


@pytest.mark.skipif(
    shutil.which("node") is None or not (_HELPER.parent / "node_modules").exists(),
    reason="Node helper production dependencies unavailable",
)
def test_real_helper_refresh_normalizes_options_object():
    client = PiAIHelperClient(
        node_path=shutil.which("node"),
        helper_path=_HELPER,
    )
    try:
        models = client.list_models()
        result = client.command(
            "models.refresh",
            options={"allow_network": False, "force": False},
            timeout=15.0,
        )
    finally:
        client.close()

    assert len(models) > 100
    assert len({model.provider_id for model in models}) > 10
    assert result["refreshed"] is True
    assert result["aborted"] is False
    assert isinstance(result["errors"], list)


@pytest.mark.skipif(shutil.which("node") is None, reason="Node unavailable")
def test_helper_loads_keychain_snapshot_from_locked_one_shot_socket():
    socket_root = Path(tempfile.mkdtemp(prefix="kss-pi-", dir="/tmp"))
    socket_path = socket_root / "credentials.sock"
    nonce = "nonce-123"
    secret = "sk-socket-only-secret"
    ready = threading.Event()
    received = []

    def serve():
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(socket_path))
        os.chmod(socket_path, 0o600)
        server.listen(1)
        ready.set()
        next_nonces = ["nonce-456", "nonce-789"]
        for next_nonce in next_nonces:
            connection, _ = server.accept()
            with connection:
                line = connection.makefile("r", encoding="utf-8").readline()
                request = json.loads(line)
                received.append(request)
                response = {
                    "nonce": request["nonce"],
                    "next_nonce": next_nonce,
                    "credentials": {
                        "mock": {"type": "api_key", "key": secret}
                    },
                }
                connection.sendall((json.dumps(response) + "\n").encode())
        server.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    assert ready.wait(timeout=2)
    client = PiAIHelperClient(
        node_path=shutil.which("node"),
        helper_path=_HELPER,
        mock=True,
    )
    try:
        metadata = client.reload_credentials_from_socket(socket_path, nonce)
        second_metadata = client.reload_credentials_from_socket(socket_path, nonce)
    finally:
        client.close()
    thread.join(timeout=2)
    socket_path.unlink(missing_ok=True)
    socket_root.rmdir()

    assert [request["nonce"] for request in received] == [nonce, "nonce-456"]
    assert metadata == [{"providerId": "mock", "type": "api_key"}]
    assert second_metadata == metadata
    assert secret not in repr(metadata)


@pytest.mark.skipif(shutil.which("node") is None, reason="Node unavailable")
def test_real_helper_streams_against_loopback_openai_endpoint():
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802 - stdlib callback name
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            chunks = [
                {
                    "id": "chatcmpl-local",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "mock-model",
                    "choices": [{
                        "index": 0,
                        "delta": {"role": "assistant", "content": "OK"},
                        "finish_reason": None,
                    }],
                },
                {
                    "id": "chatcmpl-local",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "mock-model",
                    "choices": [{
                        "index": 0,
                        "delta": {},
                        "finish_reason": "stop",
                    }],
                },
            ]
            for chunk in chunks:
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

        def log_message(self, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    client = PiAIHelperClient(
        node_path=shutil.which("node"),
        helper_path=_HELPER,
    )
    try:
        client.reload_credentials({
            "local": ProviderCredential("local", "loopback-only-key")
        })
        frames = list(client.iter_stream(
            route=ProviderRoute(
                provider_id="local",
                model_id="mock-model",
                base_url=f"http://127.0.0.1:{server.server_port}/v1",
            ),
            messages=[{"role": "user", "content": "ping"}],
            tools=[],
            config=ProviderConfig(timeout=5),
        ))
    finally:
        client.close()
        server.shutdown()

    assert any(frame.get("type") == "text" and frame.get("text") == "OK" for frame in frames)
    assert any(frame.get("type") == "finish" for frame in frames)


def test_release_packaging_pins_node_and_pi_ai():
    package = (_REPO / "helpers" / "pi-ai" / "package.json").read_text(encoding="utf-8")
    prepare = (_REPO / "script" / "prepare_pi_ai_helper.sh").read_text(encoding="utf-8")
    release = (_REPO / "script" / "sign_and_build.sh").read_text(encoding="utf-8")

    assert '"@earendil-works/pi-ai": "0.82.1"' in package
    assert 'NODE_VERSION="22.19.0"' in prepare
    assert "c59006db713c770d6ec63ae16cb3edc11f49ee093b5c415d667bb4f436c6526d" in prepare
    assert "npm-cli.js" in prepare
    assert "--ignore-scripts" in prepare
    assert "sbom --omit=dev --sbom-format=spdx" in prepare
    assert "find \"$STAGE/payload/helper/node_modules\" -type f -name '*.node'" in prepare
    assert "--jitless" not in prepare
    assert 'cp -R "$PI_AI_BUILD_ROOT/runtime" "$APP_RESOURCES/pi-ai-runtime"' in release
    assert '--entitlements "$NODE_ENTITLEMENTS"' in release
    assert "com.apple.security.cs.allow-jit" in (
        _REPO / "script" / "NodeHelper.entitlements"
    ).read_text(encoding="utf-8")
    assert 'codesign --verify --strict --verbose=2 "$APP_RESOURCES/pi-ai-runtime/bin/node"' in release
