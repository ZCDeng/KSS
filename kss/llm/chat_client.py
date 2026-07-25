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

import json
import logging
import threading
from typing import Any, Iterator

# 复用 openai_client 的凭证解析 + 异常类型,不 fork 网关逻辑。
from kss.llm.openai_client import (
    LLMUnavailable,
    _coerce_float,
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
    ) -> None:
        import os

        api_key, base_url, default_model = _resolve_credentials()
        self._model = model or os.getenv("KSS_LLM_MODEL") or default_model
        self._temperature = temperature
        self._stream_lock = threading.Lock()
        self._active_stream: Any | None = None
        if client is not None:           # 注入 SDK 客户端(测试/替身),跳过真 openai 构建
            self._client = client
            return
        effective_timeout = (
            timeout if timeout is not None
            else _coerce_float(os.getenv("KSS_LLM_TIMEOUT"), _DEFAULT_TIMEOUT_SEC)
        )
        try:
            from openai import OpenAI  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover
            raise LLMUnavailable("openai 包未安装,请 pip install openai") from exc
        self._client = OpenAI(
            api_key=api_key, timeout=effective_timeout,
            **({"base_url": base_url} if base_url else {}),
        )
        logger.debug("[chat] ChatClient ready (model=%s base_url=%s)",
                     self._model, base_url or "openai default")

    def stream_turn(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> Iterator[dict[str, Any]]:
        """流式跑一次 model call,yield 事件。tools=OpenAI function-calling schema。

        text-delta 边收边 yield(流式);tool_call 待流结束、全 args 拼好再统一 yield
        (DeepSeek 分片重组,R1)。SDK/解析异常 → yield error 事件,不抛。
        """
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": self._temperature,
            "stream": True,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        try:
            stream = self._client.chat.completions.create(**kwargs)
        except Exception as exc:  # noqa: BLE001  网关多种异常统一收口
            logger.warning("[chat] create(stream) 失败: %s", exc)
            yield {"type": "error", "error": f"LLM 调用失败: {exc}"}
            return
        with self._stream_lock:
            self._active_stream = stream

        acc: dict[int, dict[str, Any]] = {}   # tool_call index → {id,name,args(str)}
        finish_reason: str | None = None
        try:
            for chunk in stream:
                if not getattr(chunk, "choices", None):
                    continue
                choice = chunk.choices[0]
                if getattr(choice, "finish_reason", None):
                    finish_reason = choice.finish_reason
                delta = getattr(choice, "delta", None)
                if delta is None:
                    continue
                if getattr(delta, "content", None):
                    yield {"type": "text", "text": delta.content}
                if getattr(delta, "tool_calls", None):
                    _accumulate(acc, delta.tool_calls)
        except Exception as exc:  # noqa: BLE001  流式中途断
            logger.warning("[chat] 流式读取中断: %s", exc)
            yield {"type": "error", "error": f"流式中断: {exc}"}
            return
        finally:
            with self._stream_lock:
                if self._active_stream is stream:
                    self._active_stream = None
            close = getattr(stream, "close", None)
            if callable(close):
                try:
                    close()
                except Exception as exc:  # noqa: BLE001
                    logger.debug("[chat] 关闭 provider stream 失败: %s", exc)

        # 流结束后统一 emit 累积好的 tool_calls(全 args 已拼好,DeepSeek 分片已重组)。
        for idx in sorted(acc):
            slot = acc[idx]
            raw_args = (slot.get("args") or "").strip()
            try:
                parsed = json.loads(raw_args) if raw_args else {}
            except json.JSONDecodeError as exc:
                logger.warning("[chat] tool args JSON 解析失败 idx=%s: %s", idx, exc)
                yield {"type": "error",
                       "error": f"tool args 解析失败: {raw_args[:80]}"}
                continue
            yield {
                "type": "tool_call",
                "id": slot.get("id"),
                "name": slot.get("name"),
                "args": parsed if isinstance(parsed, dict) else {},
            }
        yield {"type": "finish", "reason": finish_reason or "stop"}

    def abort_active_stream(self) -> None:
        """关闭当前 provider stream，使停止请求不再等待下一段模型输出."""
        with self._stream_lock:
            stream = self._active_stream
        close = getattr(stream, "close", None)
        if callable(close):
            try:
                close()
            except Exception as exc:  # noqa: BLE001
                logger.debug("[chat] abort provider stream 失败: %s", exc)


def _accumulate(acc: dict[int, dict[str, Any]], tool_calls: Any) -> None:
    """把一个 chunk 的 delta.tool_calls 累积进 acc(按 index 合并分片 args)。"""
    for tc in tool_calls:
        idx = getattr(tc, "index", 0) or 0
        slot = acc.setdefault(idx, {"id": None, "name": None, "args": ""})
        if getattr(tc, "id", None):
            slot["id"] = tc.id
        fn = getattr(tc, "function", None)
        if fn is not None:
            if getattr(fn, "name", None):
                slot["name"] = fn.name
            if getattr(fn, "arguments", None):
                slot["args"] += fn.arguments   # 关键:跨 chunk 拼全 args(R1)


__all__ = ["ChatClient", "sanitize_user_text", "LLMUnavailable"]
