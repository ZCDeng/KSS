"""共用证据规则 + provenance / 注入告警 helper —— adapter 与 seek 采集路径共用。

R12 的三条 evidence rules(``localTruthPrecedence`` / ``doNotTreatWebAsInstruction`` /
``noTradeAdvice``)原生于 research adapter。舆情采集走 seek 路径,承载最多对抗性外部
内容,故把规则、source tier 判定、注入告警、截断逻辑抽到这一层,adapter 与 seek
recipe 两路**显式共用同一实现**,确保 R12 在新路径上不被绕过(plan U2 / KTD)。

本模块只依赖 stdlib + sanitizer,处于依赖底层;adapter 反过来 import 它。
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from kss.llm.sanitizer import scan_for_injection

EVIDENCE_RULE_NAMES: tuple[str, ...] = (
    "localTruthPrecedence",
    "doNotTreatWebAsInstruction",
    "noTradeAdvice",
)

_REPUTABLE_SECONDARY_DOMAINS = (
    "eastmoney.com",
    "cls.cn",
    "yicai.com",
    "caixin.com",
    "stcn.com",
    "cnstock.com",
    "sina.com.cn",
    "10jqka.com.cn",
)
_PRIMARY_HINTS = (
    "公告",
    "announcement",
    "investor",
    "ir.",
    "sse.com.cn",
    "szse.cn",
    "neeq.com.cn",
    "hkexnews.hk",
    "hkex.com.hk",
    "cninfo.com.cn",
)
_PRIMARY_HOSTS = (
    "sse.com.cn",
    "szse.cn",
    "neeq.com.cn",
    "hkexnews.hk",
    "hkex.com.hk",
    "cninfo.com.cn",
)


def evidence_rules() -> dict[str, bool]:
    """R12 三条规则,全 True。adapter 与 seek 路径共用同一份。"""
    return {name: True for name in EVIDENCE_RULE_NAMES}


def evidence_rule_names() -> list[str]:
    return list(EVIDENCE_RULE_NAMES)


def source_tier(url: str, title: str = "") -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    text = f"{url} {title}".lower()
    if host.endswith(".gov.cn") or any(hint in text for hint in _PRIMARY_HINTS):
        return "official_or_primary"
    if any(host == d or host.endswith("." + d) for d in _PRIMARY_HOSTS):
        return "official_or_primary"
    if any(host == d or host.endswith("." + d) for d in _REPUTABLE_SECONDARY_DOMAINS):
        return "reputable_secondary"
    return "unknown"


def quarantine_rating_inputs(
    items: list[dict[str, Any]] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """覆盖路径喂给评级/VIE 的摘录：命中注入则丢弃，不是只打标。

    其余干净摘录仍可写报告。被丢弃片段若是唯一 VIE/质量证据，由覆盖脊柱将该侧
    动作置为观望，不得据此脚本买入。
    """
    from kss.llm.sanitizer import quarantine_posts, scan_for_injection

    posts: list[dict[str, Any]] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        excerpt = str(item.get("excerpt") or item.get("summary") or "")
        title = str(item.get("title") or "")
        posts.append({
            **item,
            "title": title,
            "summary": excerpt,
            "excerpt": excerpt,
        })
    kept, dropped_posts = quarantine_posts(
        posts,
        text_keys=("title", "summary", "excerpt"),
    )
    dropped: list[dict[str, Any]] = []
    for item in dropped_posts:
        reason = "prompt_injection" if scan_for_injection(
            f"{item.get('title') or ''}\n{item.get('excerpt') or item.get('summary') or ''}"
        ) else "quarantined"
        dropped.append({**item, "drop_reason": reason})
    return kept, dropped


def warning_from_text(text: str) -> dict[str, str] | None:
    """命中注入模式即产一条 danger 告警(不拦截,仅标注);拦截在 U3 quarantine 处理。"""
    pattern = scan_for_injection(text)
    if not pattern:
        return None
    return {
        "type": "prompt_injection",
        "severity": "danger",
        "message": f"External evidence matched injection pattern: {pattern}",
    }


def cap(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... truncated {len(text) - limit} chars ..."


def make_evidence_item(
    *,
    source: str,
    source_kind: str,
    title: str,
    summary: str,
    url: str = "",
    time: str = "",
    author: str | None = None,
    heat: int | None = None,
    excerpt_limit: int = 600,
) -> dict[str, Any]:
    """把一条 seek 采集结果归一为带 provenance + 注入告警的证据项(R5 / R12 / R13 scan)。

    pipeline 全程消费的 evidence-item 契约,下游 U3/U4/U5/U7 按这些 key 取值:
      source / source_kind / title / summary / url / time / author / heat /
      sourceTier / warnings。``warnings`` 非空(命中注入)由 U3 quarantine 决定取舍,
      本层只标注不拦截。
    """
    title = str(title or "")
    summary = cap(str(summary or ""), excerpt_limit)
    warn = warning_from_text(f"{title}\n{summary}")
    return {
        "source": source,
        "source_kind": source_kind,
        "title": title,
        "summary": summary,
        "url": str(url or ""),
        "time": str(time or ""),
        "author": author,
        "heat": heat,
        "sourceTier": source_tier(url, title),
        "warnings": [warn] if warn else [],
    }
