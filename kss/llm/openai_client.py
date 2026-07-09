"""OpenAI 兼容 LLM 客户端薄封装.

环境变量约定（按优先级）：

1. 若 ``OPENAI_BASE_URL`` 指向 DeepSeek 且 ``DEEPSEEK_API_KEY`` 已配置
   → 用 DeepSeek key（避免 Keychain 里 OpenAI 槽位旧 key 挡住有余额的 DEEPSEEK key）
2. ``OPENAI_API_KEY`` + 可选 ``OPENAI_BASE_URL`` —— OpenAI / oneAPI / 自建兼容网关
3. fallback ``DEEPSEEK_API_KEY`` + ``https://api.deepseek.com/v1``
4. ``KSS_LLM_MODEL`` 决定具体 model id，default ``gpt-4o-mini`` (OpenAI 路径) /
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
# 生成 1000-1500 中文字（非 streaming）通常 30-60s，DeepSeek 偶尔 >60s；
# 取 90s 余量。cron 链路 17:30 不抢时，余量充足.
_DEFAULT_TIMEOUT_SEC: Final[float] = 90.0
# OpenAI SDK 自带一次重试，我们再加一次；超过这个值 cron 链路会卡 5+ 分钟
# （3 × 90s + 退避），不值得，宁可走 fallback.
_DEFAULT_MAX_RETRIES: Final[int] = 1
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
        timeout: float | None = None,
        max_retries: int = _DEFAULT_MAX_RETRIES,
        temperature: float = _DEFAULT_TEMPERATURE,
    ) -> None:
        api_key, base_url, default_model = _resolve_credentials()
        self._model = model or os.getenv("KSS_LLM_MODEL") or default_model
        self._temperature = temperature
        effective_timeout = (
            timeout
            if timeout is not None
            else _coerce_float(os.getenv("KSS_LLM_TIMEOUT"), _DEFAULT_TIMEOUT_SEC)
        )

        # Lazy import：openai SDK ~50ms import 成本，commentary fallback 路径不需要
        try:
            from openai import OpenAI  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - 装好包后不会触发
            raise LLMUnavailable(
                "openai 包未安装，请 pip install openai"
            ) from exc

        kwargs: dict[str, object] = {
            "api_key": api_key,
            "timeout": effective_timeout,
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


def _is_deepseek_base(base_url: str | None) -> bool:
    if not base_url:
        return False
    return "deepseek.com" in base_url.lower()


def _normalize_deepseek_base(base_url: str | None) -> str:
    """Ensure DeepSeek OpenAI-compatible base ends with ``/v1``."""
    raw = (base_url or _DEEPSEEK_BASE_URL).strip().rstrip("/")
    if not raw:
        return _DEEPSEEK_BASE_URL
    if raw.endswith("/v1"):
        return raw
    return raw + "/v1"


def _resolve_credentials() -> tuple[str, str | None, str]:
    """返回 ``(api_key, base_url, default_model)``，按优先级解析环境变量.

    Raises:
        LLMUnavailable: 两条路径都没 key.
    """
    openai_key = os.getenv("OPENAI_API_KEY", "").strip()
    openai_base = os.getenv("OPENAI_BASE_URL", "").strip() or None
    deepseek_key = os.getenv("DEEPSEEK_API_KEY", "").strip()

    # Dual-key Keychain 常见坑：OPENAI_* 槽位填了 DeepSeek 网关 + 旧 key，
    # 有余额的 DEEPSEEK_API_KEY 被永远挡住。base 指向 deepseek 时优先 DEEPSEEK key。
    if _is_deepseek_base(openai_base) and deepseek_key:
        base = _normalize_deepseek_base(openai_base)
        logger.info("[llm] OPENAI_BASE_URL 为 DeepSeek，使用 DEEPSEEK_API_KEY")
        return deepseek_key, base, _DEFAULT_MODEL_DEEPSEEK

    if openai_key:
        if _is_deepseek_base(openai_base):
            return openai_key, _normalize_deepseek_base(openai_base), _DEFAULT_MODEL_DEEPSEEK
        return openai_key, openai_base, _DEFAULT_MODEL_OPENAI

    if deepseek_key:
        return deepseek_key, _DEEPSEEK_BASE_URL, _DEFAULT_MODEL_DEEPSEEK

    raise LLMUnavailable(
        "未配置 OPENAI_API_KEY 或 DEEPSEEK_API_KEY；"
        "wrapper 应从 Hermes .env grep 注入"
    )


def _coerce_float(raw: str | None, default: float) -> float:
    """env 字符串安全转 float；空 / 非数 → default."""
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("[llm] 无法解析为 float，使用默认: %r → %.1f", raw, default)
        return default
