"""覆盖回合信封：路径本地预算、心跳、R12 收尾。不抬高全局 8 步/240 秒。"""

from __future__ import annotations

import time
from typing import Any, Callable, TYPE_CHECKING

from kss.equity_research.intent import is_coverage_intent, is_r12_text, r12_phrase

if TYPE_CHECKING:
    from kss.agent.service import RuntimeRunOptions

COVERAGE_MAX_STEPS = 12
COVERAGE_TIMEOUT_SECONDS = 900.0
_FAIL_REASONS = frozenset({"timeout", "max_steps", "aborted", "error", "client_abort"})


def coverage_run_options():
    from kss.agent.service import RuntimeRunOptions

    return RuntimeRunOptions(
        max_steps=COVERAGE_MAX_STEPS,
        timeout_seconds=COVERAGE_TIMEOUT_SECONDS,
        coverage_closer=True,
        coverage_path=True,
    )


def options_for_user_text(text: str) -> RuntimeRunOptions | None:
    if is_coverage_intent(text):
        return coverage_run_options()
    return None


class Heartbeat:
    """长工具进度。on_update 会写成 tool_update 帧，重置 Swift idle。"""

    def __init__(
        self,
        on_update: Callable[[dict[str, Any]], None] | None = None,
        *,
        min_interval: float = 15.0,
    ) -> None:
        self._on_update = on_update
        self._min_interval = min_interval
        self._last = 0.0

    def emit(self, message: str, **extra: Any) -> None:
        if self._on_update is None:
            return
        now = time.monotonic()
        if self._last and now - self._last < self._min_interval:
            return
        self._last = now
        payload = {"message": message, **extra}
        self._on_update(payload)


def apply_coverage_closer(
    messages: list[dict[str, Any]] | tuple[Any, ...],
    *,
    reason: str,
    aborted: bool = False,
) -> tuple[list[dict[str, Any]], str, bool]:
    """失败预算下用 R12「无法完成」替换半章。返回 (messages, reason, replaced)."""
    if not aborted and reason not in _FAIL_REASONS:
        return [dict(m) if isinstance(m, dict) else m for m in messages], reason, False
    phrase = r12_phrase("incomplete")
    out: list[dict[str, Any]] = []
    replaced = False
    for raw in messages:
        if not isinstance(raw, dict):
            continue
        msg = dict(raw)
        if msg.get("role") == "assistant":
            content = msg.get("content")
            text = content if isinstance(content, str) else str(content or "")
            if not is_r12_text(text):
                msg["content"] = phrase
                replaced = True
        out.append(msg)
    if not any(m.get("role") == "assistant" for m in out):
        out.append({"role": "assistant", "content": phrase})
        replaced = True
    return out, "unable_to_complete", replaced
