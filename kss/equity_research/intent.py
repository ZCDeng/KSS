"""覆盖路径 vs 「为什么动」复盘路径。F5 在 v1 是提示词竞争；代码侧用于信封与队列。"""

from __future__ import annotations

import re

_EXPLAINER = re.compile(r"为什么(?:动|涨|跌)|为啥(?:动|涨|跌)|怎么涨|怎么跌")
_COVERAGE = re.compile(
    r"研究(?:一下)?|深度覆盖|覆盖一下|估值|值不值得买|财报|业绩|指引|电话会|分析一下"
)
_R12_INCOMPLETE = "无法完成"
_R12_OUT_OF_SCOPE = "超出范围"
_R12_INSUFFICIENT = "证据不足"


def is_explainer_priority(text: str) -> bool:
    return bool(_EXPLAINER.search(text or ""))


def is_coverage_intent(text: str) -> bool:
    raw = text or ""
    if is_explainer_priority(raw):
        return False
    return bool(_COVERAGE.search(raw))


def too_many_names(query: str) -> bool:
    """一句两只票：两个显式代码，或「A和B」公司名并列。"""
    text = query or ""
    codes = re.findall(r"\d{6}\.(?:SH|SZ|BJ)|\d{1,5}\.HK", text, flags=re.I)
    if len({c.upper() for c in codes}) >= 2:
        return True
    if re.search(r"[\u4e00-\u9fff]{2,}(?:和|与|、|,)\s*[\u4e00-\u9fff]{2,}", text):
        if is_coverage_intent(text) or bool(_COVERAGE.search(text)):
            return True
    return False


def r12_phrase(kind: str) -> str:
    mapping = {
        "incomplete": f"{_R12_INCOMPLETE}：覆盖未在预算内结束。",
        "out_of_scope": f"{_R12_OUT_OF_SCOPE}：只覆盖沪深北与港股上市地，且每次一只。",
        "insufficient": f"{_R12_INSUFFICIENT}：无法形成独立观点。",
    }
    return mapping.get(kind, f"{_R12_INCOMPLETE}：覆盖未完成。")


def is_r12_text(text: str) -> bool:
    raw = text or ""
    return any(token in raw for token in (_R12_INCOMPLETE, _R12_OUT_OF_SCOPE, _R12_INSUFFICIENT))
