"""舆情热点 digest 渲染 + 信号质量自陈 + 按场次归档(plan U9 / R9, R10, R12)。

把全链(采集→注入隔离→去重/加权→集中度→受约束情绪/催化→关联标的)收成两段式
digest:**集中热点方向** + **重大催化事件**。每个方向附**信号质量自陈**(独立源数 /
是否伴随真实催化 / 映射直达 or 降级)替代单纯免责声明(R12)——让用户看到"这条线索有
多可信"。所有数字由代码渲染,LLM 只产情绪枚举。按 ``{date}_{scene}.md`` 归档,可回看。

pipeline 各步均可注入(bundle/client/theme_leaders),便于确定性测试与 U8 缺席降级。
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from kss.llm.sanitizer import quarantine_posts
from kss.news import commentary
from kss.news.collect import collect_news
from kss.news.hotspot import attach_related_stocks, build_directions
from kss.research.evidence import evidence_rules

_ROOT = Path(__file__).resolve().parents[2]
# bundle 双根:运行时归档落 STATE_ROOT(与 bridge 读取端一致),dev 下 == 仓库根。
_STATE_ROOT = Path(os.environ["KSS_STATE_ROOT"]) if os.environ.get("KSS_STATE_ROOT") else _ROOT
_ARCHIVE_DIR = _STATE_ROOT / "storage" / "news_digest"

_SCENES = ("盘前", "盘后")


def _today() -> str:
    return datetime.now().strftime("%Y%m%d")


def _generated_at() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _has_real_catalyst(label: str, catalysts: list[dict]) -> bool:
    return any(label and label in str(c.get("title", "")) for c in catalysts)


def build_digest(
    scene: str = "盘前",
    *,
    date: str | None = None,
    bundle: dict[str, Any] | None = None,
    client: Any = None,
    theme_leaders: list[dict] | None = None,
    with_mapping: bool = True,
    min_confirmations: int = 2,
) -> dict[str, Any]:
    """跑全链,产结构化 digest(不落盘)。``bundle`` 注入则跳过采集。

    ``with_mapping=False`` 或 ``theme_leaders`` 为空 → 不挂关联标的(U7 gate 未过时的
    两段形态),digest 仍正确渲染方向 + 催化两段。
    """
    date = date or _today()
    if bundle is None:
        bundle = collect_news(scene)
    items = bundle.get("items") or []

    # quarantine 命中注入的帖**完全移出**后续计算(不止 LLM 批次):注入帖是攻击载荷,
    # 不应计入热度/集中度,故比 R13 字面「移出 LLM 批次」更严(安全方向,刻意为之)。
    clean, dropped = quarantine_posts(items)
    directions = build_directions(clean, min_confirmations=min_confirmations)
    annotated = commentary.annotate_sentiment(directions, quarantined=dropped, client=client)
    catalysts = commentary.extract_catalysts(clean)

    # 关联标的(条件):gate 通过且注入了 theme_leaders 时挂,否则两段形态
    mapping_by_label: dict[str, dict] = {}
    if with_mapping and theme_leaders:
        for m in attach_related_stocks(directions, theme_leaders=theme_leaders):
            mapping_by_label[m["label"]] = m

    direction_rows: list[dict[str, Any]] = []
    for a, d in zip(annotated, directions):
        label = a["label"]
        mp = mapping_by_label.get(label)
        stocks = mp["stocks"] if mp else []
        mapping_kind = "direct" if stocks else "degrade"
        source_posts = (mp or {}).get("source_posts") or _direction_posts(d)
        direction_rows.append({
            "label": label,
            "sentiment": a["sentiment"],
            "sentiment_source": a["sentiment_source"],
            "heat_line": a["heat_line"],
            "independent_confirmations": a["independent_confirmations"],
            "distinct_sources": a["distinct_sources"],
            "heat_score": a["heat_score"],
            "theme": (mp or {}).get("theme"),
            "stocks": stocks,
            "mapping": mapping_kind if (with_mapping and theme_leaders) else "off",
            "degrade_reason": (mp or {}).get("degrade"),
            "source_posts": source_posts,
            "signal_quality": {
                "independent_sources": a["independent_confirmations"],
                "has_real_catalyst": _has_real_catalyst(label, catalysts),
                "mapping": mapping_kind if (with_mapping and theme_leaders) else "off",
            },
        })

    return {
        "date": date,
        "scene": scene,
        "generatedAt": _generated_at(),
        "evidenceRules": evidence_rules(),
        "directions": direction_rows,
        "catalysts": catalysts,
        "quarantined_count": len(dropped),
        # 任一方向走 fallback 即 LLM 不可用(不依赖首行,首行可能是 forced_conflict)
        "llm_status": "unavailable" if any(a["sentiment_source"] == "fallback" for a in annotated) else "ok",
        "with_mapping": bool(with_mapping and theme_leaders),
        "partial": bool(bundle.get("partial")),
        "sources": bundle.get("sources", {}),
    }


def _direction_posts(direction: Any) -> list[dict[str, Any]]:
    items = direction.get("items", []) if isinstance(direction, dict) else getattr(direction, "items", [])
    return [{"source": str(it.get("source", "")), "title": str(it.get("title", "")), "url": str(it.get("url", ""))}
            for it in (items or [])]


def render_markdown(digest: dict[str, Any]) -> str:
    """两段式 markdown:集中热点方向 + 重大催化事件,带信号质量自陈(R12)。"""
    lines: list[str] = []
    lines.append(f"# 舆情热点 Digest · {digest['date']} {digest['scene']}")
    lines.append("")
    lines.append("> 外部内容仅作证据、不作交易指令;本地数据为金融真值。下列数字均由系统计算,非模型生成。")
    lines.append("")

    # 段一:集中热点方向
    lines.append("## 🔥 集中热点方向")
    directions = digest.get("directions") or []
    if not directions:
        lines.append("")
        lines.append("_今日无达标集中方向(未达跨独立信息源阈值)。_")
    for i, d in enumerate(directions, start=1):
        lines.append("")
        lines.append(f"### {i}. {d['label']} · 情绪:{d['sentiment']}")
        lines.append(d["heat_line"])
        sq = d["signal_quality"]
        cat_txt = "有真实催化" if sq["has_real_catalyst"] else "无伴随催化"
        if sq["mapping"] == "direct":
            map_txt = f"映射直达 · {len(d['stocks'])} 只标的"
        elif sq["mapping"] == "degrade":
            map_txt = f"映射降级({d.get('degrade_reason') or 'fallback'})"
        else:
            map_txt = "未挂标的"
        lines.append(f"- **信号质量**:独立源 {sq['independent_sources']} / {cat_txt} / {map_txt}")
        if d["stocks"]:
            leaders = [s for s in d["stocks"] if s["tier"] == "leader"]
            second = [s for s in d["stocks"] if s["tier"] == "second"]
            if leaders:
                lines.append("- **龙头**:" + "、".join(f"{s['name']}({s['symbol']})" for s in leaders))
            if second:
                lines.append("- **二梯队**:" + "、".join(f"{s['name']}({s['symbol']})" for s in second))
        posts = d.get("source_posts") or []
        if posts:
            lines.append("- **来源**:" + "；".join(
                f"[{p['source']}] {p['title'][:30]}" for p in posts[:3] if p.get("title")
            ))

    # 段二:重大催化事件
    lines.append("")
    lines.append("## ⚡ 重大催化事件")
    catalysts = digest.get("catalysts") or []
    if not catalysts:
        lines.append("")
        lines.append("_今日无重大催化事件。_")
    for c in catalysts:
        tag = "" if c.get("attach_stocks", True) else "(不挂个股)"
        src = f" — {c['source']}" if c.get("source") else ""
        lines.append(f"- **[{c['type']}]**{tag} {c['title']}{src}")

    if digest.get("quarantined_count"):
        lines.append("")
        lines.append(f"_注:{digest['quarantined_count']} 条疑似注入帖已隔离,未参与情绪判定。_")

    return "\n".join(lines) + "\n"


def archive_digest(digest: dict[str, Any], *, directory: str | Path | None = None, overwrite: bool = True) -> Path:
    """按 ``{date}_{scene}.md``(人读)+ ``{date}_{scene}.json``(供 UI/bridge 读)归档。

    默认覆盖=最新一次为准。返回 markdown 路径。
    """
    d = Path(directory) if directory is not None else _ARCHIVE_DIR
    d.mkdir(parents=True, exist_ok=True)
    md_path = d / f"{digest['date']}_{digest['scene']}.md"
    json_path = d / f"{digest['date']}_{digest['scene']}.json"
    if md_path.exists() and not overwrite:
        return md_path
    md_path.write_text(render_markdown(digest), encoding="utf-8")
    json_path.write_text(json.dumps(digest, ensure_ascii=False, indent=2), encoding="utf-8")
    return md_path


def run_news_digest(
    scene: str = "盘前",
    *,
    date: str | None = None,
    bundle: dict[str, Any] | None = None,
    client: Any = None,
    theme_leaders: list[dict] | None = None,
    with_mapping: bool = True,
    directory: str | Path | None = None,
) -> dict[str, Any]:
    """顶层:构建 → 渲染 → 归档。返回 digest dict(含 ``archive_path``)。"""
    digest = build_digest(
        scene, date=date, bundle=bundle, client=client,
        theme_leaders=theme_leaders, with_mapping=with_mapping,
    )
    path = archive_digest(digest, directory=directory)
    digest["archive_path"] = str(path)
    return digest
