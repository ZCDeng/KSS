"""只读上市地解析：门控看解析后的后缀，不以公司名或 ADR 黑名单。

范围内：``.SH`` / ``.SZ`` / ``.BJ`` / ``.HK``。同一中文名同时命中美股别名与
A/港代码时，范围内上市地优先。无后缀数字不套用 ``get_stock`` 的 688→SH / 否则 SZ。
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from kss.ui_surface.bind_catalog import _item_match_score

IN_SCOPE_SUFFIXES = (".SH", ".SZ", ".BJ", ".HK")
_A_SHARE_RE = re.compile(r"(?<![\d.])(\d{6})\.(SH|SZ|BJ)(?![\w.])", re.I)
_HK_RE = re.compile(r"(?<![\d.])(\d{1,5})\.HK(?![\w.])", re.I)
_LATIN_TICKER_RE = re.compile(r"^[A-Za-z]{1,5}(?:[.-][A-Za-z]{1,2})?$")
_STOP = frozenset({
    "研究", "一下", "分析", "估值", "覆盖", "研报", "股票", "美股", "港股",
    "adr", "us", "etf",
})


def _state_root() -> Path:
    env = os.environ.get("KSS_STATE_ROOT")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2]


def _load_catalog_items() -> list[dict[str, Any]]:
    from kss.ui_surface.bind_catalog import load_catalog

    cat = load_catalog()
    items = cat.get("items") if isinstance(cat, dict) else None
    return [it for it in (items or []) if isinstance(it, dict)]


def _load_name_index() -> dict[str, Any]:
    path = _state_root() / "storage" / "macro" / "stock_name_index.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _normalize_code(code: str) -> str:
    raw = (code or "").strip().upper()
    if not raw:
        return ""
    hk = re.fullmatch(r"(\d{1,5})\.HK", raw)
    if hk:
        return f"{int(hk.group(1)):05d}.HK"
    a_share = re.fullmatch(r"(\d{6})\.(SH|SZ|BJ)", raw)
    if a_share:
        return f"{a_share.group(1)}.{a_share.group(2)}"
    return raw


def _suffix(code: str) -> str:
    upper = code.upper()
    for suffix in IN_SCOPE_SUFFIXES:
        if upper.endswith(suffix):
            return suffix
    return ""


def _candidate_gate(code: str) -> str:
    return "in_scope" if _suffix(code) else "us_or_adr"


def _item_code(item: dict[str, Any]) -> str:
    codes = item.get("codes") if isinstance(item.get("codes"), dict) else {}
    raw = str(codes.get("primary") or codes.get("code") or item.get("code") or "")
    return _normalize_code(raw)


def _item_name(item: dict[str, Any]) -> str:
    names = item.get("names") or []
    if names:
        return str(names[0])
    return str(item.get("name") or "")


def _index_name(code: str, index: dict[str, Any]) -> str:
    meta = index.get("meta") if isinstance(index.get("meta"), dict) else {}
    info = meta.get(code) if isinstance(meta.get(code), dict) else {}
    return str(info.get("name") or "")


def _needles(query: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []

    def push(value: str) -> None:
        token = (value or "").strip()
        if not token:
            return
        key = token.lower()
        if key in seen or key in _STOP or token in _STOP:
            return
        seen.add(key)
        out.append(token)

    push(query)
    for part in re.split(r"[\s,，、;；/\\|]+", query):
        push(part)
    for match in _A_SHARE_RE.finditer(query):
        push(match.group(0))
    for match in _HK_RE.finditer(query):
        push(match.group(0))
    return out


def _lookup_display(
    code: str,
    items: list[dict[str, Any]],
    index: dict[str, Any],
) -> str:
    named = _index_name(code, index)
    if named:
        return named
    for item in items:
        if _item_code(item) == code:
            return _item_name(item)
    return ""


def resolve_listing(
    query: str,
    *,
    catalog_items: list[dict[str, Any]] | None = None,
    name_index: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """解析名称或代码，返回带后缀候选与门控结果。"""
    text = (query or "").strip()
    items = catalog_items if catalog_items is not None else _load_catalog_items()
    index = name_index if name_index is not None else _load_name_index()
    if not isinstance(index, dict):
        index = {}

    found: dict[str, dict[str, Any]] = {}

    def add(code: str, display: str = "") -> None:
        normalized = _normalize_code(code)
        if not normalized:
            return
        existing = found.get(normalized)
        name = display or _lookup_display(normalized, items, index)
        if existing is not None:
            if name and not existing.get("display_name"):
                existing["display_name"] = name
            return
        found[normalized] = {
            "code": normalized,
            "suffix": _suffix(normalized),
            "display_name": name,
            "gate": _candidate_gate(normalized),
        }

    if not text:
        return _payload(text, [])

    for match in _A_SHARE_RE.finditer(text):
        add(f"{match.group(1)}.{match.group(2).upper()}")
    for match in _HK_RE.finditer(text):
        add(f"{match.group(1)}.HK")

    needles = _needles(text)
    by_name = index.get("byName") if isinstance(index.get("byName"), dict) else {}
    by_code = index.get("byCode") if isinstance(index.get("byCode"), dict) else {}

    for needle in needles:
        for item in items:
            if _item_match_score(item, needle) > 0:
                add(_item_code(item), _item_name(item))
        mapped = by_name.get(needle)
        if mapped:
            add(str(mapped))
        if len(needle) >= 2 and re.search(r"[\u4e00-\u9fff]", needle):
            for name, code in by_name.items():
                if needle == name or needle in str(name) or str(name) in needle:
                    add(str(code))

        ticker = needle.upper()
        if _LATIN_TICKER_RE.fullmatch(needle):
            hit_code = ""
            for item in items:
                if _item_code(item) == ticker:
                    hit_code = ticker
                    add(ticker, _item_name(item))
                    break
            if not hit_code:
                add(ticker)

        if re.fullmatch(r"\d{6}", needle):
            mapped_code = by_code.get(needle)
            if mapped_code:
                add(str(mapped_code))
        elif re.fullmatch(r"\d{1,5}", needle):
            padded = f"{int(needle):05d}.HK"
            for item in items:
                if _item_code(item) == padded:
                    add(padded, _item_name(item))
                    break

    in_scope = [row for row in found.values() if row["gate"] == "in_scope"]
    us_or_adr = [row for row in found.values() if row["gate"] == "us_or_adr"]
    chosen = in_scope or us_or_adr
    chosen.sort(key=lambda row: (row.get("suffix") or "", row["code"]))
    return _payload(text, chosen)


def _payload(query: str, candidates: list[dict[str, Any]]) -> dict[str, Any]:
    if any(row["gate"] == "in_scope" for row in candidates):
        gate = "in_scope"
    elif candidates:
        gate = "us_or_adr"
    else:
        gate = "unresolved"
    return {
        "query": query,
        "gate": gate,
        "enter_coverage": gate == "in_scope",
        "picker": False,
        "candidates": candidates,
    }
