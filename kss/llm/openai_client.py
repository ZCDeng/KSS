"""OpenAI 兼容 LLM 客户端薄封装.

BYOK 端点泛化（plan 2026-07-12-005 / U3）：``_resolve_credential_candidates()``
返回有序候选 ``[(api_key, base_url, model), ...]``——primary 优先、fallback 其次。

新六键（``KSS_LLM_PRIMARY_KEY/BASE_URL/MODEL``、``KSS_LLM_FALLBACK_*``）优先；
全缺时执行兼容映射，保证旧配置零操作可用：

1. ``DEEPSEEK_API_KEY`` + ``https://api.deepseek.com/v1`` —— 主路径候选
2. ``OPENAI_API_KEY`` + 可选 ``OPENAI_BASE_URL`` —— 备用候选（OpenAI / oneAPI / 自建兼容网关）
3. 若仅有 OPENAI_* 但 ``OPENAI_BASE_URL`` 指向 DeepSeek：该候选走 DeepSeek base
4. ``KSS_LLM_MODEL`` 覆盖具体 model id；缺省
   ``deepseek-v4-flash`` (DeepSeek) / ``gpt-4o-mini`` (OpenAI)

``LLMClient.complete()`` 对主候选的 auth/连接类失败重试一次备用候选（新增行为，
不含在旧版本为）；``ChatClient`` 仅构造时选主候选，流式过程中不切换。

base_url 强制 https（localhost/127.0.0.1 本地推理端点例外），保存/使用前双卡点校验。
"""

from __future__ import annotations

import logging
import os
from typing import Final
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_DEEPSEEK_BASE_URL: Final[str] = "https://api.deepseek.com/v1"
_DEFAULT_MODEL_OPENAI: Final[str] = "gpt-4o-mini"
_DEFAULT_MODEL_DEEPSEEK: Final[str] = "deepseek-v4-flash"
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
        self._candidates = _resolve_credential_candidates()
        self._model_override = model
        self._temperature = temperature
        self._max_retries = max_retries
        self._effective_timeout = (
            timeout
            if timeout is not None
            else _coerce_float(os.getenv("KSS_LLM_TIMEOUT"), _DEFAULT_TIMEOUT_SEC)
        )

        primary_key, primary_base, primary_default_model = self._candidates[0]
        self._model = model or os.getenv("KSS_LLM_MODEL") or primary_default_model
        self._client = self._build_sdk_client(primary_key, primary_base)
        logger.debug(
            "[llm] LLMClient ready (model=%s base_url=%s candidates=%d)",
            self._model, primary_base or "openai default", len(self._candidates),
        )

    def _build_sdk_client(self, api_key: str, base_url: str | None) -> object:
        # Lazy import：openai SDK ~50ms import 成本，commentary fallback 路径不需要
        try:
            from openai import OpenAI  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - 装好包后不会触发
            raise LLMUnavailable(
                "openai 包未安装，请 pip install openai"
            ) from exc

        kwargs: dict[str, object] = {
            "api_key": api_key,
            "timeout": self._effective_timeout,
            "max_retries": self._max_retries,
        }
        if base_url:
            kwargs["base_url"] = base_url
        return OpenAI(**kwargs)

    def complete(self, system: str, user: str) -> str:
        """单次 chat completion，返回 ``message.content`` 字符串.

        主候选失败时重试一次备用候选（若有）——运行期 auth/连接类失败降级（U3 新增行为）。

        Args:
            system: system role prompt（角色 / 输出格式约束）.
            user: user role prompt（数据 + 任务）.

        Returns:
            模型返回的文本（已 strip）；空字符串视为失败.

        Raises:
            LLMUnavailable: 全部候选均调用失败（网络、auth、空响应）.
        """
        try:
            return self._complete_with(self._client, self._model, system, user)
        except Exception as primary_exc:  # noqa: BLE001 - 统一降级判定
            if len(self._candidates) < 2:
                raise LLMUnavailable(f"LLM API 调用失败: {primary_exc}") from primary_exc
            logger.warning("[llm] 主候选失败，降级备用候选: %s", primary_exc)
            fallback_key, fallback_base, fallback_default_model = self._candidates[1]
            fallback_model = (
                self._model_override or os.getenv("KSS_LLM_MODEL") or fallback_default_model
            )
            fallback_client = self._build_sdk_client(fallback_key, fallback_base)
            try:
                return self._complete_with(fallback_client, fallback_model, system, user)
            except Exception as fallback_exc:  # noqa: BLE001
                raise LLMUnavailable(
                    f"LLM API 调用失败（主备均不可用）: 主={primary_exc}; 备={fallback_exc}"
                ) from fallback_exc

    def _complete_with(self, client: object, model: str, system: str, user: str) -> str:
        try:
            resp = client.chat.completions.create(  # type: ignore[attr-defined]
                model=model,
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


def _validate_https(base_url: str | None, *, label: str) -> None:
    """base_url 保存/使用前强制 https（localhost/127.0.0.1 本地推理端点例外）.

    Raises:
        LLMUnavailable: scheme 既非 https 也非本地 http.
    """
    if not base_url:
        return
    parsed = urlparse(base_url)
    if parsed.scheme == "https":
        return
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme == "http" and hostname in {"localhost", "127.0.0.1"}:
        return
    raise LLMUnavailable(
        f"{label} base_url 必须是 https（本地 http://localhost 除外）: {base_url!r}"
    )


def _resolve_credential_candidates() -> list[tuple[str, str | None, str]]:
    """返回有序候选 ``[(api_key, base_url, default_model), ...]``——primary 优先、fallback 其次.

    新六键（``KSS_LLM_PRIMARY_*``/``KSS_LLM_FALLBACK_*``）优先；全缺时执行兼容映射
    （``DEEPSEEK_API_KEY``→primary、``OPENAI_*``→fallback），旧配置零操作可用。

    Raises:
        LLMUnavailable: 无任何候选，或某候选 base_url 校验未过.
    """
    primary_key = os.getenv("KSS_LLM_PRIMARY_KEY", "").strip()
    primary_base = os.getenv("KSS_LLM_PRIMARY_BASE_URL", "").strip() or None
    primary_model = os.getenv("KSS_LLM_PRIMARY_MODEL", "").strip() or None
    fallback_key = os.getenv("KSS_LLM_FALLBACK_KEY", "").strip()
    fallback_base = os.getenv("KSS_LLM_FALLBACK_BASE_URL", "").strip() or None
    fallback_model = os.getenv("KSS_LLM_FALLBACK_MODEL", "").strip() or None

    candidates: list[tuple[str, str | None, str]] = []
    if primary_key:
        _validate_https(primary_base, label="主用")
        candidates.append((primary_key, primary_base, primary_model or _DEFAULT_MODEL_OPENAI))
    if fallback_key:
        _validate_https(fallback_base, label="备用")
        candidates.append((fallback_key, fallback_base, fallback_model or _DEFAULT_MODEL_OPENAI))
    if candidates:
        return candidates

    # 兼容映射：新六键全缺 → 落到旧键的解析优先级语义。
    openai_key = os.getenv("OPENAI_API_KEY", "").strip()
    openai_base = os.getenv("OPENAI_BASE_URL", "").strip() or None
    deepseek_key = os.getenv("DEEPSEEK_API_KEY", "").strip()

    legacy: list[tuple[str, str | None, str]] = []
    if deepseek_key:
        # 若 OPENAI_BASE_URL 也指 deepseek，复用规范化后的 base；否则官方 /v1。
        if _is_deepseek_base(openai_base):
            base = _normalize_deepseek_base(openai_base)
        else:
            base = _DEEPSEEK_BASE_URL
        logger.info("[llm] 使用 DEEPSEEK_API_KEY 主路径")
        legacy.append((deepseek_key, base, _DEFAULT_MODEL_DEEPSEEK))
    if openai_key:
        if _is_deepseek_base(openai_base):
            # 仅 OPENAI key 但 base 指 deepseek：仍走 deepseek 网关 + 该 key
            legacy.append(
                (openai_key, _normalize_deepseek_base(openai_base), _DEFAULT_MODEL_DEEPSEEK)
            )
        else:
            legacy.append((openai_key, openai_base, _DEFAULT_MODEL_OPENAI))

    if not legacy:
        raise LLMUnavailable(
            "未配置任何 LLM 凭据（KSS_LLM_PRIMARY_KEY 或 DEEPSEEK_API_KEY/OPENAI_API_KEY）；"
            "wrapper 应从 Keychain 注入"
        )
    for key, base, _model in legacy:
        _validate_https(base, label="LLM")
    return legacy


def _resolve_credentials() -> tuple[str, str | None, str]:
    """返回 ``(api_key, base_url, default_model)``——`_resolve_credential_candidates()`
    的首选候选，供仅需单一凭证的调用方（如 `ChatClient` 构造）使用."""
    return _resolve_credential_candidates()[0]


def probe_credential_candidate(
    candidate: tuple[str, str | None, str], *, timeout: float = 10.0
) -> dict[str, object]:
    """对单个候选做 1-token 连通性探测（plan 2026-07-12-005 / U4 datasource-test 复用）.

    不做候选间降级——每个候选独立测，调用方（bridge ``datasource-test``）逐个报告。

    Returns:
        ``{"ok": bool, "latency_ms": float | None, "error": str | None, "hint": str | None}``.
    """
    import time as _time

    api_key, base_url, model = candidate
    t0 = _time.monotonic()
    try:
        from openai import OpenAI  # type: ignore[import-not-found]
    except ImportError:
        return {"ok": False, "latency_ms": None, "error": "sdk_missing", "hint": "openai 包未安装"}

    try:
        kwargs: dict[str, object] = {"api_key": api_key, "timeout": timeout, "max_retries": 0}
        if base_url:
            kwargs["base_url"] = base_url
        client = OpenAI(**kwargs)
        client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=1,
        )
    except Exception as exc:  # noqa: BLE001 - 探测面向用户报错，需捕获全部
        latency_ms = (_time.monotonic() - t0) * 1000
        return {
            "ok": False,
            "latency_ms": round(latency_ms, 1),
            "error": type(exc).__name__,
            "hint": str(exc)[:200],
        }
    latency_ms = (_time.monotonic() - t0) * 1000
    return {"ok": True, "latency_ms": round(latency_ms, 1), "error": None, "hint": None}


def _coerce_float(raw: str | None, default: float) -> float:
    """env 字符串安全转 float；空 / 非数 → default."""
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("[llm] 无法解析为 float，使用默认: %r → %.1f", raw, default)
        return default
