"""覆盖路径门控：意图识别、R12 收尾用语、本路径预算。

不在模块顶层 import agent.service，避免与 chat_loop 循环依赖。
"""

from __future__ import annotations

import re
from typing import Any

R12_INCOMPLETE = "无法完成"
R12_PHRASES = ("证据不足", "超出范围", "无法完成")

COVERAGE_MAX_STEPS = 16
COVERAGE_TIMEOUT_SECONDS = 900.0
COVERAGE_KEEPALIVE_SECONDS = 15.0

_EXPLAINER = re.compile(r"为什么(?:动|涨|跌)")
_RESEARCH = re.compile(
    r"研究|分析|估值|覆盖|值不值得买|财报|业绩|电话会|指引"
)


def is_coverage_intent(text: str) -> bool:
    """投研覆盖意图；同一句命中为什么动/涨/跌时复盘优先（F5）。"""
    raw = (text or "").strip()
    if not raw:
        return False
    if _EXPLAINER.search(raw):
        return False
    return bool(_RESEARCH.search(raw))


def coverage_run_options() -> Any:
    from kss.agent.service import RuntimeRunOptions

    return RuntimeRunOptions(
        coverage_path=True,
        coverage_closer=True,
        max_steps=COVERAGE_MAX_STEPS,
        timeout_seconds=COVERAGE_TIMEOUT_SECONDS,
    )
