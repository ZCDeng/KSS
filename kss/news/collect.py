"""舆情采集 —— 多源 → 带 provenance 的结构化证据(plan U2 / R1, R2, R5)。

只返结构化真值,**不调 LLM**(R2)。每源经 ``seek_client.reach`` 拉取,按源类型
解析为统一 evidence-item(契约见 ``kss.research.evidence.make_evidence_item``)。
单源失败容错跳过(``partial``/``failedSteps``),不连坐其余源。源清单读
``kss/config/news_sources.yaml``(R1:可配置、不硬编码)。

evidence-rule 与 provenance / 注入告警走 ``kss.research.evidence`` 的共用 helper,
与 research adapter 同一实现,确保 R12 在这条承载最多对抗内容的路径上不被绕过。
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import yaml

from kss.research.evidence import evidence_rules, make_evidence_item

_DEFAULT_SOURCES = Path(__file__).resolve().parents[1] / "config" / "news_sources.yaml"

_HEAT = re.compile(r"热度[:：]\s*([\d,]+)")
_TAG = re.compile(r"\[[^\]]*\]")
_WEIBO_LINE = re.compile(r"^\s*\d+\.\s+(.*\S)\s*$")
_URL_LINE = re.compile(r"https?://\S+")
_BLOCK_SEP = re.compile(r"\n-{3,}\n")


def _retrieved_at() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def load_sources(path: str | Path | None = None) -> list[dict[str, Any]]:
    """读源清单。文件不存在 → 空列表(容错,不抛)。

    兼容两种结构:U1 多赛道模型的 ``tracks[].sources[]``,以及更早的顶层
    ``sources[]`` 扁平结构。yaml 2026-07-09 起改成分赛道,而这里一直只读顶层
    ``sources``,导致此后恒返回空清单、采集全程空转——两种都认才不会再漏。
    """
    p = Path(path) if path is not None else _DEFAULT_SOURCES
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        return []
    if not isinstance(raw, dict):
        return []

    tracks = raw.get("tracks")
    if isinstance(tracks, list):
        out: list[dict[str, Any]] = []
        for t in tracks:
            if not isinstance(t, dict):
                continue
            for s in t.get("sources") or []:
                if isinstance(s, dict):
                    out.append(s)
        return out

    sources = raw.get("sources")
    return [s for s in (sources or []) if isinstance(s, dict)]


# ---- 各源解析器(纯函数,text -> [partial item dict]) ----

def parse_weibo(text: str) -> list[dict[str, Any]]:
    """微博热榜:``N. <标题> [标签]  热度:<数字>`` + 下一行缩进 URL。"""
    out: list[dict[str, Any]] = []
    lines = (text or "").splitlines()
    i = 0
    while i < len(lines):
        m = _WEIBO_LINE.match(lines[i])
        if not m:
            i += 1
            continue
        raw = m.group(1)
        heat_m = _HEAT.search(raw)
        heat = int(heat_m.group(1).replace(",", "")) if heat_m else None
        title = _HEAT.sub("", raw)
        title = _TAG.sub("", title).strip()
        url = ""
        # URL 常在同一段或紧随的缩进行
        url_m = _URL_LINE.search(raw)
        if url_m:
            url = url_m.group(0)
        elif i + 1 < len(lines):
            nxt = _URL_LINE.search(lines[i + 1])
            if nxt:
                url = nxt.group(0)
                i += 1
        if title:
            out.append({"title": title, "summary": title, "url": url, "heat": heat})
        i += 1
    return out


def parse_search(text: str) -> list[dict[str, Any]]:
    """bocha / anspire 搜索:``标题：/摘要：/来源：<url>`` 块,块间以 ``---`` 分隔。"""
    out: list[dict[str, Any]] = []
    for chunk in _BLOCK_SEP.split(text or ""):
        chunk = chunk.strip()
        if not chunk:
            continue
        t = re.search(r"标题[:：]\s*(.+)", chunk)
        u = re.search(r"来源[:：]\s*(\S+)", chunk)
        s = re.search(r"摘要[:：]\s*([\s\S]*?)(?:\n\s*来源[:：]|\Z)", chunk)
        title = (t.group(1).strip() if t else "")
        url = (u.group(1).strip() if u else "")
        summary = re.sub(r"\s+", " ", s.group(1)).strip() if s else title
        if title or summary:
            out.append({"title": title or summary[:40], "summary": summary, "url": url})
    return out


def parse_twitter(text: str) -> list[dict[str, Any]]:
    """X/Twitter:best-effort。当前 reach-gateway 500、无法定型,故仅在出现
    可识别块时解析,否则返回 [](gateway 恢复后据真实格式补)。"""
    text = (text or "").strip()
    if not text:
        return []
    # 复用搜索块格式(若 gateway 以同构格式返回)
    if "标题" in text and "来源" in text:
        return parse_search(text)
    return []


_PARSERS: dict[str, Callable[[str], list[dict[str, Any]]]] = {
    "reach_weibo_hot": parse_weibo,
    "reach_weibo_search": parse_weibo,
    "bocha_web_search": parse_search,
    "anspire_web_search": parse_search,
    "reach_twitter_search": parse_twitter,
}


def _parse(tool: str, text: str) -> list[dict[str, Any]]:
    fn = _PARSERS.get(tool)
    return fn(text) if fn else []


def collect_news(
    scene: str = "盘前",
    *,
    sources_path: str | Path | None = None,
    reach: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """采集启用源,归一为结构化证据 bundle。

    返回::

        {
          "scene": str, "retrievedAt": iso, "evidenceRules": {...},
          "items": [evidence-item, ...],          # 契约见 evidence.make_evidence_item
          "sources": {源名: 条数},
          "partial": bool, "failedSteps": [源名]   # 有源失败时才有
        }
    """
    if reach is None:
        # seek MCP 2026-08-15 下线,默认改走 comboSearch CLI(同签名)。延迟导入,免常驻开销。
        from kss.research.combosearch_client import reach as _reach
        reach = _reach

    sources = [s for s in load_sources(sources_path) if s.get("enabled", True)]
    items: list[dict[str, Any]] = []
    per_source: dict[str, int] = {}
    failed: list[str] = []

    for s in sources:
        tool = str(s.get("tool") or "")
        name = str(s.get("name") or s.get("key") or tool)
        kind = str(s.get("key") or tool)
        args = dict(s.get("args") or {})
        if not tool:
            continue
        res = reach(tool, **args)
        if not res.get("ok"):
            failed.append(name)
            continue
        count = 0
        for ri in _parse(tool, res.get("text") or ""):
            items.append(
                make_evidence_item(
                    source=name,
                    source_kind=kind,
                    title=ri.get("title", ""),
                    summary=ri.get("summary", ""),
                    url=ri.get("url", ""),
                    time=ri.get("time", ""),
                    author=ri.get("author"),
                    heat=ri.get("heat"),
                )
            )
            count += 1
        per_source[name] = per_source.get(name, 0) + count

    out: dict[str, Any] = {
        "scene": scene,
        "retrievedAt": _retrieved_at(),
        "evidenceRules": evidence_rules(),
        "items": items,
        "sources": per_source,
    }
    if failed:
        out["partial"] = True
        out["failedSteps"] = failed
    return out
