"""U1(plan 004)：会 tool-calling + 流式 + 多轮的 LLM chat 客户端。

既有 `openai_client.LLMClient.complete()` 是一次性、无工具、无流式、无多轮——聊天 loop 用不了。
本模块新建 `ChatClient.stream_turn()`,复用同一网关/模型/key 解析(零 fork),产出事件流:
  - {"type":"text","text": <delta>}        增量正文(流式逐字)
  - {"type":"tool_call","id","name","args"} 模型要调工具(args 已 JSON 解析为 dict)
  - {"type":"finish","reason"}              本轮结束(reason: stop / tool_calls / ...)
  - {"type":"error","error"}               流式中途/解析失败,优雅降级(不抛穿调用方 loop)

DeepSeek 坑(R1/R-spike):流式时 `delta.tool_calls[].function.arguments` **跨 chunk 分片**,
须按 tool_call index 累积全 args 再 JSON 解析(见 _accumulate / stream_turn 尾部统一 emit)。

注入面(R8):**user 输入**经 `sanitize_user_text`(max_len~500);**tool 结果不经此**——
64-char/500 截断会毁 KB 级 JSON,tool 结果在 loop 层走 tool-role + pattern 扫描(U2)。
"""
from __future__ import annotations

import logging
from typing import Any, Iterator

from kss.agent.provider import (
    OpenAICompatibleProvider,
    ProviderConfig,
    ProviderEvent,
)
from kss.llm.openai_client import (
    LLMUnavailable,
    _resolve_credentials,
)
from kss.llm.sanitizer import sanitize_llm_input

logger = logging.getLogger(__name__)

_DEFAULT_TEMPERATURE = 0.4          # 复盘问答比生成更要稳,略低于 commentary 的 0.6
_DEFAULT_TIMEOUT_SEC = 90.0         # 多轮放宽,与 openai_client 量级一致(KTD-6)
_USER_INPUT_MAX_LEN = 500           # R8:user 输入截断(远超单字段 64,容一句中文问句)


def sanitize_user_text(text: str | None) -> str:
    """R8:净化 **user** 输入(注入扫描 + 字符白名单 + 500 截断)。

    只用于 user-role 文本;tool 结果**绝不**走这里(会截断 KB 级 JSON,U2 另处理)。
    """
    return sanitize_llm_input(text, max_len=_USER_INPUT_MAX_LEN)


class ChatClient:
    """流式 + tool-calling chat 客户端。失败统一抛 :class:`LLMUnavailable`。"""

    def __init__(
        self,
        model: str | None = None,
        timeout: float | None = None,
        temperature: float = _DEFAULT_TEMPERATURE,
        client: Any | None = None,
        provider: OpenAICompatibleProvider | None = None,
    ) -> None:
        import os

        self._model_override = model
        self._temperature = temperature
        self._timeout = timeout
        self._client = client  # Kept for compatibility with existing injected-client tests.
        if provider is not None:
            self._provider = provider
            self._model = model or os.getenv("KSS_LLM_MODEL") or "gpt-4o-mini"
        elif client is not None:
            api_key, base_url, default_model = _resolve_credentials()
            self._model = model or os.getenv("KSS_LLM_MODEL") or default_model
            self._provider = OpenAICompatibleProvider(
                credential_resolver=lambda: [(api_key, base_url, default_model)],
                client_factory=lambda _key, _base, _timeout: client,
            )
        else:
            # Do not snapshot credentials here: provider resolves Keychain/env
            # candidates for every model call.
            self._model = model or os.getenv("KSS_LLM_MODEL") or "gpt-4o-mini"
            self._provider = OpenAICompatibleProvider()
        logger.debug("[chat] ChatClient facade ready (model=%s)", self._model)

    def stream_turn(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> Iterator[dict[str, Any]]:
        """流式跑一次 model call,yield 事件。tools=OpenAI function-calling schema。

        text-delta 边收边 yield(流式);tool_call 待流结束、全 args 拼好再统一 yield
        (DeepSeek 分片重组,R1)。SDK/解析异常 → yield error 事件,不抛。
        """
        config = ProviderConfig(
            model=self._model_override,
            temperature=self._temperature,
            timeout=self._timeout,
            include_usage=True,
        )
        for event in self._provider.stream_sync(messages, tools, config):
            yield _legacy_event(event)

    def abort_active_stream(self) -> None:
        """关闭当前 provider stream，使停止请求不再等待下一段模型输出."""
        self._provider.abort_active_stream()


def _accumulate(acc: dict[int, dict[str, Any]], tool_calls: Any) -> None:
    """Deprecated compatibility helper for callers that imported it directly."""
    from kss.agent.provider import _accumulate_tool_calls

    _accumulate_tool_calls(acc, tool_calls)


def _legacy_event(event: ProviderEvent) -> dict[str, Any]:
    """Map normalized events to the v1 dictionary contract consumed by the loop."""
    if event.type == "text":
        return {"type": "text", "text": event.text or ""}
    if event.type == "tool_call":
        return {
            "type": "tool_call",
            "id": event.tool_call_id,
            "name": event.tool_name,
            "args": event.tool_arguments or {},
        }
    if event.type == "usage":
        return {"type": "usage", "usage": event.usage.as_dict() if event.usage else {}}
    if event.type == "finish":
        return {"type": "finish", "reason": event.finish_reason or "stop"}
    error = event.error
    if error is not None and error.phase == "tool_arguments":
        # Invalid model-generated arguments are a tool-call contract failure,
        # not a provider failure. Preserve the call so the loop can append an
        # is_error tool result and let the model repair its next attempt.
        return {
            "type": "tool_call",
            "id": error.tool_call_id,
            "name": error.tool_name,
            "args": error.raw_arguments or "",
            "argument_error": error.as_dict(),
        }
    return {"type": "error", "error": error.message if error else "未知 provider 错误"}


__all__ = ["ChatClient", "sanitize_user_text", "LLMUnavailable"]
