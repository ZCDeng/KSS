"""OpenAI 兼容 LLM 客户端薄封装.

环境变量约定（按优先级）：

1. ``OPENAI_API_KEY`` + 可选 ``OPENAI_BASE_URL`` —— 走 OpenAI / oneAPI 网关
2. fallback ``DEEPSEEK_API_KEY`` + ``https://api.deepseek.com/v1``
3. ``KSS_LLM_MODEL`` 决定具体 model id，default ``gpt-4o-mini`` (OpenAI 路径) /
   ``deepseek-chat`` (DeepSeek 路径)
"""

from __future__ import annotations

import logging
import os
from typing import Final

logger = logging.getLogger(__name__)

_DEEPSEEK_BASE_URL: Final[str] = "https://api.deepseek.com/v1"
_DEFAULT_MODEL_OPENAI: Final[str] = "gpt-4o-mini"
_DEFAULT_MODEL_DEEPSEEK: Final[str] = "deepseek-chat"
_DEFAULT_TIMEOUT_SEC: Final[float] = 30.0
_DEFAULT_MAX_RETRIES: Final[int] = 2  # OpenAI SDK 内置一次 + 我们一次
_DEFAULT_TEMPERATURE: Final[float] = 0.6


class LLMUnavailable(RuntimeError):
    """LLM 调用无法完成（key 缺、429 重试耗尽、超时、auth 失败）."""


class LLMClient:
    """单次 chat completion 调用器.

    用法::

        client = LLMClient()
        text = client.complete(system="你是投顾", user="今天大盘怎么样")

    失败一律抛 :class:`LLMUnavailable`，调用方负责降级.
    """

    def __init__(
        self,
        model: str | None = None,
        timeout: float = _DEFAULT_TIMEOUT_SEC,
        max_retries: int = _DEFAULT_MAX_RETRIES,
        temperature: float = _DEFAULT_TEMPERATURE,
    ) -> None:
        api_key, base_url, default_model = _resolve_credentials()
        self._model = model or os.getenv("KSS_LLM_MODEL") or default_model
        self._temperature = temperature

        # Lazy import：openai SDK ~50ms import 成本，commentary fallback 路径不需要
        try:
            from openai import OpenAI  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - 装好包后不会触发
            raise LLMUnavailable(
                "openai 包未安装，请 pip install openai"
            ) from exc

        kwargs: dict[str, object] = {
            "api_key": api_key,
            "timeout": timeout,
            "max_retries": max_retries,
        }
        if base_url:
            kwargs["base_url"] = base_url
        self._client = OpenAI(**kwargs)
        logger.debug(
            "[llm] LLMClient ready (model=%s base_url=%s)",
            self._model, base_url or "openai default",
        )

    def complete(self, system: str, user: str) -> str:
        """单次 chat completion，返回 ``message.content`` 字符串.

        Args:
            system: system role prompt（角色 / 输出格式约束）.
            user: user role prompt（数据 + 任务）.

        Returns:
            模型返回的文本（已 strip）；空字符串视为失败.

        Raises:
            LLMUnavailable: 调用失败（网络、auth、空响应）.
        """
        try:
            resp = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=self._temperature,
            )
        except Exception as exc:  # OpenAI SDK 多种异常，统一收口
            logger.warning("[llm] chat.completions.create 失败: %s", exc)
            raise LLMUnavailable(f"LLM API 调用失败: {exc}") from exc

        if not resp.choices:
            raise LLMUnavailable("LLM 返回 choices 为空")
        content = resp.choices[0].message.content
        if not content or not content.strip():
            raise LLMUnavailable("LLM 返回内容为空")
        return content.strip()


def _resolve_credentials() -> tuple[str, str | None, str]:
    """返回 ``(api_key, base_url, default_model)``，按优先级解析环境变量.

    Raises:
        LLMUnavailable: 两条路径都没 key.
    """
    openai_key = os.getenv("OPENAI_API_KEY", "").strip()
    if openai_key:
        return openai_key, os.getenv("OPENAI_BASE_URL", "").strip() or None, _DEFAULT_MODEL_OPENAI

    deepseek_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if deepseek_key:
        return deepseek_key, _DEEPSEEK_BASE_URL, _DEFAULT_MODEL_DEEPSEEK

    raise LLMUnavailable(
        "未配置 OPENAI_API_KEY 或 DEEPSEEK_API_KEY；"
        "wrapper 应从 Hermes .env grep 注入"
    )
