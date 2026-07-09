"""Investment rewrite for 资讯雷达 (plan 2026-07-10-001 U2).

Schema: 事件 / 影响 / 标的线索 / 待验证. Reuses LLMClient.complete like digest_ai.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from kss.llm.openai_client import LLMClient, LLMUnavailable
from kss.news.article_fetch import body_or_summary
from kss.news.rewrite_config import (
    AGGREGATE_MAX_BULLETS,
    POOL_THRESHOLD,
    THIN_CONTENT_CHARS,
)
from kss.storage.rewrite_pool import (
    BEIJING,
    beijing_day,
    claim_generating,
    count_ready,
    item_id_for,
    list_drafts,
    read_draft,
    write_draft,
)

logger = logging.getLogger(__name__)

_TIMEOUT_SEC = 30.0
_MAX_RETRIES = 0
_MAX_INPUT_CHARS = 12_000

_SYSTEM_PROMPT = """你是「资讯雷达」投研向改写助手。任务：把用户提供的一篇资讯压成可读的投研草稿。

硬性要求：
1. 严格用下面四个中文小节标题（Markdown ##），顺序固定，每节 1-4 条短句：
## 事件
## 影响
## 标的线索
## 待验证
2. 只客观陈述；不推荐买卖；不预测涨跌；不构成投资建议。
3. 「标的线索」只能写可能相关的公司/板块/主题，并标注「待核实」；没有则写「暂无明确标的线索」。
4. 「待验证」写需要交叉核对的事实或缺口；没有则写「暂无」。
5. 不要输出前后缀寒暄、不要编造文中不存在的数字。
"""

_USER_TEMPLATE = """赛道：{track_name}（{track_key}）
来源：{source}
时间：{time}
标题：{title}
URL：{url}
正文模式：{body_mode}
正文/摘要：
{body}

请按四小节输出投研向改写。"""


def _model_name() -> str:
    import os

    return os.environ.get("KSS_LLM_MODEL") or os.environ.get("OPENAI_MODEL") or "gpt-4o-mini"


def build_rewrite_prompt(
    track_key: str,
    track_name: str,
    item: dict[str, Any],
    body: str,
    body_mode: str,
) -> tuple[str, str]:
    body_clipped = (body or "")[:_MAX_INPUT_CHARS]
    user = _USER_TEMPLATE.format(
        track_name=track_name or track_key,
        track_key=track_key,
        source=item.get("source") or "—",
        time=item.get("time") or "—",
        title=item.get("title") or "",
        url=item.get("url") or "",
        body_mode=body_mode,
        body=body_clipped or "（无正文）",
    )
    return _SYSTEM_PROMPT, user


def _parse_sections(text: str) -> dict[str, str]:
    """Best-effort split of markdown ## sections."""
    keys = ("事件", "影响", "标的线索", "待验证")
    out = {k: "" for k in keys}
    if not text:
        return out
    parts = re.split(r"(?m)^##\s+", text.strip())
    for part in parts:
        part = part.strip()
        if not part:
            continue
        for k in keys:
            if part.startswith(k):
                body = part[len(k) :].lstrip(" \n：:").strip()
                out[k] = body
                break
    return out


def run_rewrite(
    track_key: str,
    track_name: str,
    item: dict[str, Any],
    *,
    force: bool = False,
    fetch_body: bool = True,
    body_override: str | None = None,
    body_mode_override: str | None = None,
    respect_top_k: bool = False,
) -> dict[str, Any]:
    """Generate or return investment rewrite for one item.

    respect_top_k is unused for on-demand (always False); worker enforces K externally.
    """
    _ = respect_top_k  # reserved
    title = (item.get("title") or "").strip()
    if not title:
        return {"error": "missing title", "error_type": "invalid", "status": "failed"}

    iid = item_id_for(item)
    day = beijing_day()
    existing = read_draft(iid)
    if existing and existing.get("status") == "ready" and not force:
        return {**existing, "from_cache": True}

    claimed, draft = claim_generating(item, track_key=track_key, day=day)
    if not claimed and draft.get("status") == "ready" and not force:
        return {**draft, "from_cache": True}
    if not claimed and draft.get("status") == "generating" and not force:
        return {**draft, "from_cache": True, "status": "generating"}

    # body
    if body_override is not None:
        body = body_override
        body_mode = body_mode_override or "summary"
        body_err = None
        char_count = len(body or "")
    elif fetch_body:
        got = body_or_summary(
            url=item.get("url") or "",
            summary=item.get("summary") or "",
        )
        body = got.get("body") or ""
        body_mode = got.get("mode") or "empty"
        body_err = got.get("error")
        char_count = int(got.get("char_count") or len(body))
    else:
        body = item.get("summary") or ""
        body_mode = "summary" if body else "empty"
        body_err = None
        char_count = len(body)

    thin_input = f"{title}\n{body}".strip()
    if len(thin_input) < THIN_CONTENT_CHARS:
        failed = {
            **draft,
            "status": "failed",
            "error": "content too thin",
            "error_type": "thin",
            "body_text": body,
            "body_mode": body_mode,
            "body_char_count": char_count,
            "body_error": body_err,
        }
        write_draft(failed)
        return failed

    system, user = build_rewrite_prompt(track_key, track_name, item, body, body_mode)
    try:
        client = LLMClient(model=_model_name(), timeout=_TIMEOUT_SEC, max_retries=_MAX_RETRIES)
        text = client.complete(system=system, user=user)
    except LLMUnavailable as e:
        failed = {
            **draft,
            "status": "failed",
            "error": str(e),
            "error_type": "unavailable",
            "body_text": body,
            "body_mode": body_mode,
            "body_char_count": char_count,
            "body_error": body_err,
        }
        write_draft(failed)
        return failed
    except Exception as e:  # noqa: BLE001
        err = str(e)
        et = "timeout" if "timeout" in err.lower() or "timed out" in err.lower() else "llm"
        failed = {
            **draft,
            "status": "failed",
            "error": err,
            "error_type": et,
            "body_text": body,
            "body_mode": body_mode,
            "body_char_count": char_count,
            "body_error": body_err,
        }
        write_draft(failed)
        return failed

    text = (text or "").strip()
    sections = _parse_sections(text)
    ready = {
        **draft,
        "status": "ready",
        "text": text,
        "sections": sections,
        "model": _model_name(),
        "generated_at": datetime.now(BEIJING).strftime("%Y-%m-%d %H:%M:%S"),
        "prompt_system": system,
        "body_text": body,
        "body_mode": body_mode,
        "body_char_count": char_count,
        "body_error": body_err,
        "error": None,
        "error_type": None,
    }
    write_draft(ready)
    return ready


def aggregate_track_digest(
    track_key: str,
    day: str | None = None,
    *,
    threshold: int | None = None,
) -> dict[str, Any]:
    """Join ready rewrites into 今日要点 bullets (deterministic, no second LLM)."""
    day = day or beijing_day()
    thr = POOL_THRESHOLD if threshold is None else threshold
    ready = list_drafts(track_key=track_key, day=day, status="ready")
    n = len(ready)
    if n < thr:
        return {
            "mode": "insufficient",
            "text": "",
            "count": n,
            "threshold": thr,
            "draft_ids": [d.get("item_id") for d in ready],
            "day": day,
            "track_key": track_key,
        }

    bullets: list[str] = []
    seen: set[str] = set()
    for d in ready:
        if len(bullets) >= AGGREGATE_MAX_BULLETS:
            break
        sections = d.get("sections") or {}
        event = (sections.get("事件") or "").strip()
        impact = (sections.get("影响") or "").strip()
        if not event:
            # first non-empty line of full text
            for line in (d.get("text") or "").splitlines():
                line = line.strip().lstrip("-•* ").strip()
                if line and not line.startswith("#"):
                    event = line
                    break
        if not event:
            event = (d.get("title") or "").strip()
        if not event:
            continue
        # normalize for dedupe
        key = re.sub(r"\s+", "", event)[:80]
        if key in seen:
            continue
        seen.add(key)
        line = event.replace("\n", " ")
        if impact:
            imp_one = impact.splitlines()[0].strip().lstrip("-•* ").strip()
            if imp_one:
                line = f"{line} · {imp_one}"
        if len(line) > 120:
            line = line[:117] + "…"
        bullets.append(f"- {line}")

    text = "\n".join(bullets) if bullets else "该赛道改写池暂无可聚合要点"
    return {
        "mode": "pool",
        "text": text,
        "count": n,
        "threshold": thr,
        "draft_ids": [d.get("item_id") for d in ready],
        "day": day,
        "track_key": track_key,
        "generated_at": datetime.now(BEIJING).strftime("%Y-%m-%d %H:%M:%S"),
    }


def pool_ready_count(track_key: str, day: str | None = None) -> int:
    return count_ready(track_key, day)
