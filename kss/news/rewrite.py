"""资讯雷达改写：投研向 (investment) + qmreader 风中文改写 (chinese) + 忠实译文 (translation).

kind:
- investment: 事件/影响/标的线索/待验证
- chinese: 全文流畅中文改写（对齐 qmreader 语言/结构规范，不绑定「乔木」人设；已无 UI 入口）
- translation: 保留 markdown 结构的忠实中文翻译（原文 Tab 按需，plan 2026-07-22-001 U3）
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
    VALID_KINDS,
    beijing_day,
    claim_generating,
    count_ready,
    item_id_for,
    list_drafts,
    read_draft,
    write_draft,
)

logger = logging.getLogger(__name__)

# deepseek-v4-pro 等慢模型 + 数 KB 正文时 30s 易超时（见 storage/intel_rewrites 超时失败）。
# 与中文改写对齐到 90s；max_retries=0 避免串行叠超时。
_TIMEOUT_INVESTMENT = 90.0
_TIMEOUT_CHINESE = 90.0  # 长文改写
_TIMEOUT_TRANSLATION = 90.0
_MAX_RETRIES = 0
_MAX_INPUT_CHARS = 12_000
_MAX_INPUT_CHARS_CHINESE = 16_000
_MAX_INPUT_CHARS_TRANSLATION = 16_000

_INVESTMENT_SYSTEM = """你是「资讯雷达」投研向改写助手。任务：把用户提供的一篇资讯压成可读的投研草稿。

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

_INVESTMENT_USER = """赛道：{track_name}（{track_key}）
来源：{source}
时间：{time}
标题：{title}
URL：{url}
正文模式：{body_mode}
正文/摘要：
{body}

请按四小节输出投研向改写。"""

# 对齐 qmreader QIAOMU_REWRITE 的语言/结构规范，去掉个人品牌人设
_CHINESE_SYSTEM = """你是中文科技与产业内容编辑。擅长把信息密度高的英文报告、外电、机器翻译稿或资讯稿，改写成逻辑清晰、读感流畅的中文文章。

目标读者是有一定专业背景的从业者，时间有限，不喜欢废话。

语言风格：
- 口语化，对话感强，像和读者面对面聊天
- 短段落，多留白，视觉舒适
- 善用生活化类比解释复杂概念
- 始终使用第三人称视角叙述
- 不要用第一人称自称，不要把原文里的 I / we / you 机械直译成作者对读者喊话
- 真诚、不装，专业但不掉书袋
- 数据和案例支撑观点

格式规范：
- 重要观点用 **加粗** 突出
- 全程使用中文标点
- 禁止使用中文破折号和英文破折号
- 禁止使用水平分隔线
- 不输出一级标题，直接从开头钩子进入正文，小标题使用二级或三级标题
- 只输出改写后的中文 Markdown 文章，不要解释过程，不要输出自查清单

禁用表达：
- 禁用句式：不是……而是、想象一下、你有没有想过、值得注意的是、不难理解、毋庸置疑、随着……的发展、对于……来说、在……方面
- 禁用词汇：精准打击、赋能、落地、深度融合、全面布局、强势崛起等空洞套话
- 英文 newsletter 的寒暄、订阅提醒、邮箱打扰、欢迎语不要直译，要删除或改写成真正的信息开场

写作结构：
- 开头前三行必须有钩子
- 每个段落只说一件事
- 每一个数据后面，都解释这说明什么
- 因果关系写清楚
- 小标题要有实际信息量
- 结尾给出对读者真正有用的行动结论，不做空泛总结

忠实度要求：
- 保留原文所有关键数据和核心结论，不遗漏，不夸大
- 可以调整结构和顺序，但不能改变原意
- 不编造原文没有的事实、数字、融资与机构背书
"""

_CHINESE_USER = """来源：{source}
时间：{time}
标题：{title}
URL：{url}

正文/摘要：
{body}

请输出中文改写（Markdown 正文，无一级标题）。"""

_TRANSLATION_SYSTEM = """你是专业中文译者。任务：把外文文章忠实翻译成简体中文。

硬性要求：
1. 逐段忠实翻译，不增删信息，不演绎，不总结，不加译者注。
2. 保留原文 Markdown 结构：## 小标题、- 列表、空行分段一一对应；标题也要翻译。
3. 数字、日期、百分比、金额原样保留；公司/产品/人名用通用中文译名，无通用译名保留原文。
4. 全程中文标点；语句通顺但不改变原意。
5. 只输出译文 Markdown，不要前后缀说明。
"""

_TRANSLATION_USER = """来源：{source}
时间：{time}
标题：{title}
URL：{url}

原文（Markdown）：
{body}

请输出忠实中文译文（Markdown，结构与原文对应）。"""


def _model_name() -> str:
    import os

    return os.environ.get("KSS_LLM_MODEL") or os.environ.get("OPENAI_MODEL") or "gpt-4o-mini"


def _normalize_kind(kind: str | None) -> str:
    k = (kind or "investment").strip().lower()
    if k in ("zh", "cn", "中文", "中文改写"):
        return "chinese"
    if k in ("invest", "投研", "投研改写"):
        return "investment"
    if k in ("translate", "译文", "翻译"):
        return "translation"
    if k not in VALID_KINDS:
        return "investment"
    return k


def build_rewrite_prompt(
    track_key: str,
    track_name: str,
    item: dict[str, Any],
    body: str,
    body_mode: str,
    kind: str = "investment",
) -> tuple[str, str]:
    kind = _normalize_kind(kind)
    if kind == "translation":
        max_chars = _MAX_INPUT_CHARS_TRANSLATION
    elif kind == "chinese":
        max_chars = _MAX_INPUT_CHARS_CHINESE
    else:
        max_chars = _MAX_INPUT_CHARS
    body_clipped = (body or "")[:max_chars]
    if kind == "translation":
        user = _TRANSLATION_USER.format(
            source=item.get("source") or "—",
            time=item.get("time") or "—",
            title=item.get("title") or "",
            url=item.get("url") or "",
            body=body_clipped or "（无正文）",
        )
        return _TRANSLATION_SYSTEM, user
    if kind == "chinese":
        user = _CHINESE_USER.format(
            source=item.get("source") or "—",
            time=item.get("time") or "—",
            title=item.get("title") or "",
            url=item.get("url") or "",
            body=body_clipped or "（无正文）",
        )
        return _CHINESE_SYSTEM, user
    user = _INVESTMENT_USER.format(
        track_name=track_name or track_key,
        track_key=track_key,
        source=item.get("source") or "—",
        time=item.get("time") or "—",
        title=item.get("title") or "",
        url=item.get("url") or "",
        body_mode=body_mode,
        body=body_clipped or "（无正文）",
    )
    return _INVESTMENT_SYSTEM, user


def _parse_sections(text: str) -> dict[str, str]:
    """Best-effort split of markdown ## sections (investment kind)."""
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
    kind: str = "investment",
) -> dict[str, Any]:
    """Generate or return rewrite for one item.

    kind=investment|chinese. respect_top_k reserved for worker.
    """
    _ = respect_top_k
    kind = _normalize_kind(kind)
    title = (item.get("title") or "").strip()
    if not title:
        return {
            "error": "missing title",
            "error_type": "invalid",
            "status": "failed",
            "kind": kind,
        }

    iid = item_id_for(item)
    day = beijing_day()
    existing = read_draft(iid, kind)
    if existing and existing.get("status") == "ready" and not force:
        return {**existing, "from_cache": True, "kind": kind}

    claimed, draft = claim_generating(item, track_key=track_key, day=day, kind=kind)
    if not claimed and draft.get("status") == "ready" and not force:
        return {**draft, "from_cache": True, "kind": kind}
    if not claimed and draft.get("status") == "generating" and not force:
        return {**draft, "from_cache": True, "status": "generating", "kind": kind}

    if body_override is not None:
        body = body_override
        body_mode = body_mode_override or "summary"
        body_err = None
        char_count = len(body or "")
    elif fetch_body:
        if kind == "translation":
            # 译文吃结构化正文：读穿缓存拿 body_md（原文 Tab 已看过 → 大概率命中）
            from kss.storage.article_cache import get_or_fetch

            got = get_or_fetch(item.get("url") or "", item.get("summary") or "")
            body = got.get("body_md") or got.get("body") or ""
        else:
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
            "kind": kind,
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

    system, user = build_rewrite_prompt(
        track_key, track_name, item, body, body_mode, kind=kind
    )
    if kind == "translation":
        timeout = _TIMEOUT_TRANSLATION
    elif kind == "chinese":
        timeout = _TIMEOUT_CHINESE
    else:
        timeout = _TIMEOUT_INVESTMENT
    try:
        client = LLMClient(model=_model_name(), timeout=timeout, max_retries=_MAX_RETRIES)
        text = client.complete(system=system, user=user)
    except LLMUnavailable as e:
        err = str(e)
        et = (
            "timeout"
            if "timeout" in err.lower() or "timed out" in err.lower()
            else "unavailable"
        )
        failed = {
            **draft,
            "kind": kind,
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
    except Exception as e:  # noqa: BLE001
        err = str(e)
        et = "timeout" if "timeout" in err.lower() or "timed out" in err.lower() else "llm"
        failed = {
            **draft,
            "kind": kind,
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
    sections = _parse_sections(text) if kind == "investment" else {}
    ready = {
        **draft,
        "kind": kind,
        "status": "ready",
        "text": text,
        "sections": sections,
        "model": _model_name(),
        "generated_at": datetime.now(BEIJING).strftime("%Y-%m-%d %H:%M:%S"),
        "prompt_system": system[:500],  # trim storage
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
    """Join ready **investment** rewrites into 今日要点 bullets."""
    day = day or beijing_day()
    thr = POOL_THRESHOLD if threshold is None else threshold
    ready = list_drafts(track_key=track_key, day=day, status="ready", kind="investment")
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
            for line in (d.get("text") or "").splitlines():
                line = line.strip().lstrip("-•* ").strip()
                if line and not line.startswith("#"):
                    event = line
                    break
        if not event:
            event = (d.get("title") or "").strip()
        if not event:
            continue
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
    return count_ready(track_key, day, kind="investment")
