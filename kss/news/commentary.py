"""LLM 情绪标签(受约束)+ 催化事件抽取 + 数字保护(plan U6 / R4 情绪, R6, R13)。

沿用 ``kss.sector.commentary`` 的三分模式:
- ``_news_prompt_payload``:剥数字,只喂 LLM 定性证据。
- LLM 只返情绪枚举(偏多/偏空/分歧);**任一支撑帖命中注入(被 quarantine)→ 代码强制
  "分歧"**,LLM 不可凌驾(R4)。LLM 失败 → 降级为"分歧"(不臆断方向)。
- ``render_news_heat_line``:热度等真值由代码渲染(带 ``<u>``),LLM 文本即便编出数字也
  经中和不进产出(R13 / AE6)。

催化事件抽取 + 分类按关键词由**代码**完成(确定性变换让代码答),非科技催化(商品/降息)
不挂个股(R6)。LLM 不参与催化分类。
"""

from __future__ import annotations

import logging
import re
from typing import Any

from kss.llm import LLMClient, LLMUnavailable
from kss.news.dedup import normalize_text

logger = logging.getLogger(__name__)

SENTIMENTS = ("偏多", "偏空", "分歧")
_DEFAULT_SENTIMENT = "分歧"

# LLM 正文中的数字一律中和(真值走代码渲染)。
_NUMBER_RE = re.compile(r"[0-9０-９]+(?:[.,][0-9]+)?\s*%?\s*(?:万|亿|千|百|个点|倍|pct|bp)?")

# 催化事件类型 → 关键词。顺序即优先级(先命中先归类)。
_CATALYST_TYPES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("政策", ("政策", "印发", "发布", "批复", "批准", "出台", "规划", "意见出台", "立项", "补贴", "试点", "审批", "纳入")),
    ("量产", ("量产", "投产", "产线", "下线", "交付", "出货", "扩产", "装机", "投运")),
    ("涨价", ("涨价", "提价", "调价", "价格上调", "提涨", "上调价格", "报价上涨")),
    ("商品", ("黄金", "金价", "石油", "原油", "油价", "天然气", "白银", "铜价", "铝价", "煤价")),
    ("降息", ("降息", "降准", "加息", "议息", "降准降息", "LPR", "MLF", "美联储", "利率决议")),
)
# 非科技催化(商品/降息)不强行挂 KSS 题材库个股(R6)。
_NO_STOCK_TYPES = frozenset({"商品", "降息"})


# ====================================================================== #
# 数字保护
# ====================================================================== #

def strip_numbers(text: str) -> str:
    """喂 LLM 前剥数字,杜绝 LLM 复述/幻觉金融数字(R13)。"""
    return _NUMBER_RE.sub("", text or "")


def render_news_heat_line(direction: Any) -> str:
    """确定性渲染方向热度真值行(HTML,数字唯一可信来源)。

    direction 可为 U5 的 Direction 或等价 dict;读 independent_confirmations /
    distinct_sources / mention_count / raw_heat_total。
    """
    def g(name: str, default: Any = 0) -> Any:
        if isinstance(direction, dict):
            return direction.get(name, default)
        return getattr(direction, name, default)

    ic = int(g("independent_confirmations", 0))
    srcs = g("distinct_sources", []) or []
    mentions = int(g("mention_count", 0))
    raw = int(g("raw_heat_total", 0))
    line = (
        f"<b>📊 热度(系统数据)</b>:<u>{ic}</u> 个独立信息源 / "
        f"<u>{len(srcs)}</u> 个平台 / 提及 <u>{mentions}</u> 次"
    )
    if raw > 0:
        line += f",原始热度合计 <u>{raw}</u>"
    return line


# ====================================================================== #
# 受约束情绪
# ====================================================================== #

def _label_of(direction: Any) -> str:
    return direction.get("label", "") if isinstance(direction, dict) else getattr(direction, "label", "")


def _items_of(direction: Any) -> list[dict]:
    return direction.get("items", []) if isinstance(direction, dict) else getattr(direction, "items", [])


def _news_prompt_payload(direction: Any) -> dict[str, Any]:
    """LLM 情绪 payload —— 只含去数字的定性证据,无裸数字、无热度计数。"""
    items = _items_of(direction)
    cues = []
    for it in items[:8]:
        title = strip_numbers(str(it.get("title", ""))).strip()
        if title:
            cues.append(title)
    return {
        "label": _label_of(direction),
        "sources": sorted({str(it.get("source", "")) for it in items if it.get("source")}),
        "cues": cues,
    }


def _injection_touched(label: str, quarantined: list[dict] | None) -> bool:
    """方向是否被注入帖针对(被 quarantine 的帖里出现该方向词)→ R4 强制分歧。"""
    if not label or not quarantined:
        return False
    for q in quarantined:
        text = f"{q.get('title', '')} {q.get('summary', '')}"
        if label in text:
            return True
    return False


def _parse_sentiments(raw: str, n: int) -> list[str]:
    """解析 LLM 回复:每行 ``i: 情绪``。只取枚举内 token,其余 → 默认分歧。"""
    out = [_DEFAULT_SENTIMENT] * n
    for line in (raw or "").splitlines():
        m = re.match(r"\s*(\d+)\s*[:：]\s*(\S+)", line)
        if not m:
            continue
        idx = int(m.group(1))
        token = m.group(2)
        sentiment = next((s for s in SENTIMENTS if s in token), _DEFAULT_SENTIMENT)
        if 0 <= idx < n:
            out[idx] = sentiment
    return out


def _sentiment_system() -> str:
    return (
        "你是 A 股舆情情绪判定助手。只输出方向情绪枚举,严禁输出任何数字/涨幅/转发量/价格。"
        "每个方向只能从 偏多 / 偏空 / 分歧 三选一。证据互相矛盾或不足时一律选 分歧。"
        "输出格式:每行 `序号: 情绪`,无多余文字。"
    )


def _sentiment_user(payloads: list[dict[str, Any]]) -> str:
    lines = ["请判断下列方向的舆情情绪(偏多/偏空/分歧):"]
    for i, p in enumerate(payloads):
        cues = "; ".join(p["cues"]) or "(无明确线索)"
        lines.append(f"{i}. 方向「{p['label']}」 来源[{','.join(p['sources'])}] 线索:{cues}")
    return "\n".join(lines)


def annotate_sentiment(
    directions: list[Any],
    *,
    quarantined: list[dict] | None = None,
    client: LLMClient | None = None,
) -> list[dict[str, Any]]:
    """给每个方向打受约束情绪 + 渲染热度真值行。返回带情绪/来源/热度行的 dict 列表。"""
    if not directions:
        return []

    payloads = [_news_prompt_payload(d) for d in directions]
    llm_status = "ok"
    sentiments = [_DEFAULT_SENTIMENT] * len(directions)
    try:
        if client is None:
            client = LLMClient()
        raw = client.complete(system=_sentiment_system(), user=_sentiment_user(payloads))
        # 只从回复里抽情绪枚举(偏多/偏空/分歧),LLM 即便编数字也结构上无法进产出(AE6)。
        sentiments = _parse_sentiments(raw, len(directions))
    except LLMUnavailable as exc:
        logger.warning("[news-commentary] LLM 不可用,情绪降级为分歧: %s", exc)
        llm_status = "unavailable"

    out: list[dict[str, Any]] = []
    for d, base in zip(directions, sentiments):
        label = _label_of(d)
        source = "llm" if llm_status == "ok" else "fallback"
        sentiment = base
        # R4:支撑帖命中注入 → 代码强制分歧,LLM 不可凌驾
        if _injection_touched(label, quarantined):
            sentiment = "分歧"
            source = "forced_conflict"
        out.append({
            "label": label,
            "sentiment": sentiment,
            "sentiment_source": source,
            "heat_line": render_news_heat_line(d),
            "independent_confirmations": int(
                d.get("independent_confirmations", 0) if isinstance(d, dict)
                else getattr(d, "independent_confirmations", 0)
            ),
            "distinct_sources": (d.get("distinct_sources", []) if isinstance(d, dict)
                                 else getattr(d, "distinct_sources", [])),
            "heat_score": int(d.get("heat_score", 0) if isinstance(d, dict)
                              else getattr(d, "heat_score", 0)),
        })
    return out


# ====================================================================== #
# 催化事件抽取(代码,关键词)
# ====================================================================== #

def classify_catalyst(text: str) -> str | None:
    """文本 → 催化类型(政策/量产/涨价/商品/降息),无命中 → None。"""
    t = text or ""
    for ctype, kws in _CATALYST_TYPES:
        if any(kw in t for kw in kws):
            return ctype
    return None


def extract_catalysts(items: list[dict] | None) -> list[dict[str, Any]]:
    """从条目抽催化事件并按类型打标签。同公告去重;非科技催化不挂个股(R6)。"""
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        text = f"{it.get('title', '')} {it.get('summary', '')}"
        ctype = classify_catalyst(text)
        if not ctype:
            continue
        key = f"{ctype}:{normalize_text(it.get('title', '') or it.get('summary', ''))[:40]}"
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "type": ctype,
            "title": str(it.get("title", "")),
            "source": str(it.get("source", "")),
            "url": str(it.get("url", "")),
            "attach_stocks": ctype not in _NO_STOCK_TYPES,
        })
    return out
