"""LLM 客户端薄封装 —— 复用 Hermes ``.env`` 里的 OpenAI / DeepSeek API key.

设计取舍：

- 走 OpenAI Python SDK v1，因为 ``OPENAI_BASE_URL`` 可指向 oneAPI 网关，
  顺带覆盖 Claude / Gemini 路由；DeepSeek 也兼容 OpenAI 协议（``api.deepseek.com``）.
- 不直接装 ``anthropic`` SDK，避免双 SDK 维护成本.
- 不引入 LangChain / agent framework —— 单次 chat completion 足够.

入口：:class:`kss.llm.openai_client.LLMClient`.
"""

from __future__ import annotations

from kss.llm.openai_client import LLMClient, LLMUnavailable

__all__ = ["LLMClient", "LLMUnavailable"]
