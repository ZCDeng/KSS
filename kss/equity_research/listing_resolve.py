"""只读上市地解析：门控看解析后的后缀，不以公司名或 ADR 黑名单。

范围内：``.SH`` / ``.SZ`` / ``.BJ`` / ``.HK``。同一中文名同时命中美股别名与
A/港代码时，范围内上市地优先（「阿里巴巴」进 ``09988.HK`` 而不是 ``BABA``）。
不把 WRITE ``resolve`` / ``resolve_stocks`` 暴露给对话；不走 ``get_stock`` 的
688→SH / 否则 SZ 启发式。
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

_IN_SCOPE_SUFFIXES = (".SH", ".SZ", ".BJ", ".HK")
_A_CODE_RE = re.compile(r"\b(\d{6})\.(SH|SZ|BJ)\b", re.I)
_HK_CODE_RE = re.compile(r"\b(\d{1,5})\.HK\b", re.I)
_US_TICKER_RE = re.compile(r"\b([A-Z]{2,5}(?:[.\-][A-Z]{1,2})?)\b")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]{2,}")
_DIGITS_RE = re.compile(r"\b(\d{4,6})\b")
_US_STOP = {
    "ADR", "ADS", "NYSE", "NASDAQ", "HKEX", "STOCK", "TICKER", "CODE",
    "THE", "AND", "FOR", "ETF",
}
_SUFFIX_STOP = {"SH", "SZ", "BJ", "HK", "US", "USA"}


def _state_root() -> Path:
    env = os.environ.get("KSS_STATE_ROOT")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2]


def _load_catalog_items() -> list[dict[str, Any]]:
    from kss.ui_surface.bind_catalog import build_catalog, load_catalog

    cat = load_catalog(rebuild_if_missing=False)
    items = [it for it in (cat.get("items") or []) if isinstance(it, dict)]
    if items:
        return items
    built = build_catalog()
    return [it for it in (built.get("items") or []) if isinstance(it, dict)]


def _load_name_index() -> dict[str, Any]:
    path = _state_root() / "storage" / "macro" / "stock_name_index.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _item_code(item: dict[str, Any]) -> str:
    codes = item.get("codes") or {}
    if not isinstance(codes, dict):
        return ""
    return str(codes.get("primary") or codes.get("code") or "").strip()


def _item_name(item: dict[str, Any]) -> str:
    names = item.get("names") or []
    if names:
        return str(names[0] or "")
    aliases = item.get("aliases") or []
    return str(aliases[0] or "") if aliases else ""


def _normalize_hk(raw: str) -> str:
    digits = re.sub(r"\D", "", raw or "")
    if not digits or len(digits) > 5:
        return ""
    return f"{digits.zfill(5)}.HK"


def _normalize_listed(raw: str) -> str:
    text = (raw or "").strip().upper()
    if not text:
        return ""
    a = _A_CODE_RE.search(text)
    if a and a.group(0).upper() == text:
        return f"{a.group(1)}.{a.group(2).upper()}"
    if text.endswith(".HK"):
        return _normalize_hk(text)
    return text


def _suffix_of(code: str) -> str:
    up = (code or "").upper()
    for suffix in _IN_SCOPE_SUFFIXES:
        if up.endswith(suffix):
            return suffix
    return ""


def _gate_of(code: str) -> str:
    if _suffix_of(code):
        return "in_scope"
    up = (code or "").strip().upper()
    if re.fullmatch(r"[A-Z][A-Z0-9.\-]{1,11}", up) and not any(
        up.endswith(s) for s in _IN_SCOPE_SUFFIXES
    ):
        return "us_or_adr"
    return "unresolved"


def _display_from_index(code: str, name_index: dict[str, Any]) -> str:
    meta = name_index.get("meta") or {}
    if isinstance(meta, dict):
        info = meta.get(code) or meta.get(code.upper()) or {}
        if isinstance(info, dict):
            return str(info.get("name") or "")
    return ""


def _candidate(code: str, display_name: str) -> dict[str, Any]:
    norm = _normalize_listed(code) or code.strip().upper()
    suffix = _suffix_of(norm)
    return {
        "code": norm,
        "suffix": suffix,
        "display_name": display_name,
        "gate": _gate_of(norm),
    }


def _alias_keys(item: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    primary = _item_code(item)
    if primary:
        keys.add(primary.strip().upper())
        keys.add(primary.replace(".", "").upper())
        hk = _normalize_hk(primary) if primary.upper().endswith(".HK") or re.fullmatch(r"\d{1,5}", primary) else ""
        if hk:
            keys.add(hk)
            keys.add(hk.replace(".HK", ""))
            keys.add(str(int(re.sub(r"\D", "", hk) or "0")))
    for raw in list(item.get("aliases") or []) + list(item.get("names") or []):
        text = str(raw or "").strip()
        if not text:
            continue
        keys.add(text.upper())
        keys.add(text)
        if re.fullmatch(r"\d{1,5}(?:\.HK)?", text, re.I):
            hk = _normalize_hk(text)
            if hk:
                keys.add(hk)
                keys.add(hk.replace(".HK", ""))
    return {k for k in keys if k}


def _name_matched(item: dict[str, Any], token: str) -> bool:
    if len(token) < 2:
        return False
    needle = token.strip()
    if not needle:
        return False
    haystacks = [str(x) for x in list(item.get("names") or []) + list(item.get("aliases") or [])]
    for hay in haystacks:
        if not hay:
            continue
        if hay == needle or needle in hay or hay in needle:
            return True
    return False


def _lookup_name_index(token: str, name_index: dict[str, Any]) -> list[tuple[str, str]]:
    hits: list[tuple[str, str]] = []
    by_name = name_index.get("byName") or {}
    by_code = name_index.get("byCode") or {}
    if isinstance(by_name, dict):
        code = by_name.get(token)
        if code:
            hits.append((str(code), _display_from_index(str(code), name_index) or token))
    if isinstance(by_code, dict) and re.fullmatch(r"\d{6}", token):
        code = by_code.get(token)
        if code:
            hits.append((str(code), _display_from_index(str(code), name_index)))
    return hits


def resolve_listing(
    query: str,
    *,
    catalog_items: list[dict[str, Any]] | None = None,
    name_index: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """解析名称或代码。返回 candidates + 汇总 gate（in_scope / us_or_adr / unresolved）。"""
    text = (query or "").strip()
    items = catalog_items if catalog_items is not None else _load_catalog_items()
    index = name_index if name_index is not None else _load_name_index()

    found: dict[str, dict[str, Any]] = {}

    def push(code: str, display_name: str) -> None:
        cand = _candidate(code, display_name)
        if cand["gate"] == "unresolved" or not cand["code"]:
            return
        key = cand["code"]
        prev = found.get(key)
        if prev is None or (not prev.get("display_name") and cand["display_name"]):
            found[key] = cand
        elif prev is not None and not prev.get("display_name"):
            prev["display_name"] = cand["display_name"]

    if not text:
        return {
            "gate": "unresolved",
            "enter_coverage": False,
            "picker": False,
            "candidates": [],
        }

    for match in _A_CODE_RE.finditer(text):
        code = f"{match.group(1)}.{match.group(2).upper()}"
        push(code, _display_from_index(code, index))

    for match in _HK_CODE_RE.finditer(text):
        code = _normalize_hk(match.group(0))
        if code:
            push(code, _display_from_index(code, index))

    latin = text.upper()
    for match in _US_TICKER_RE.finditer(latin):
        ticker = match.group(1)
        if ticker in _US_STOP or ticker in _SUFFIX_STOP:
            continue
        if ticker.endswith((".SH", ".SZ", ".BJ", ".HK")):
            continue
        if re.search(r"\d", ticker):
            continue
        push(ticker, "")

    cjk_tokens = _CJK_RE.findall(text)
    digit_tokens = _DIGITS_RE.findall(text)

    for token in digit_tokens:
        if len(token) == 6:
            for code, name in _lookup_name_index(token, index):
                push(code, name)

    name_tokens = list(cjk_tokens)
    if text in cjk_tokens or _CJK_RE.fullmatch(text):
        if text not in name_tokens:
            name_tokens.append(text)
    elif not _A_CODE_RE.search(text) and not _HK_CODE_RE.search(text) and _CJK_RE.search(text):
        name_tokens.append(text)

    for token in name_tokens:
        for code, name in _lookup_name_index(token, index):
            push(code, name)

    for item in items:
        primary = _item_code(item)
        if not primary:
            continue
        aliases = _alias_keys(item)
        display = _item_name(item)
        matched = False
        explicit = {_normalize_listed(m.group(0)) for m in _A_CODE_RE.finditer(text)}
        explicit |= {_normalize_hk(m.group(0)) for m in _HK_CODE_RE.finditer(text)}
        explicit |= {m.group(1) for m in _US_TICKER_RE.finditer(latin)
                     if m.group(1) not in _US_STOP and m.group(1) not in _SUFFIX_STOP}
        for token in digit_tokens:
            if token.upper() in aliases or _normalize_hk(token) in aliases:
                matched = True
                break
        if not matched:
            for token in explicit:
                if token in aliases or token.replace(".", "") in aliases:
                    matched = True
                    break
        if not matched:
            for token in name_tokens:
                if _name_matched(item, token):
                    matched = True
                    break
        if matched:
            push(primary, display or _display_from_index(_normalize_listed(primary) or primary, index))

    # 名称命中 catalog 后，补上 name-index 里同名的另一市场（A/H 双边）。
    for token in name_tokens:
        for code, name in _lookup_name_index(token, index):
            push(code, name)

    candidates = list(found.values())
    in_scope = [c for c in candidates if c["gate"] == "in_scope"]
    us_or_adr = [c for c in candidates if c["gate"] == "us_or_adr"]

    if in_scope:
        gated = in_scope
        gate = "in_scope"
    elif us_or_adr:
        gated = us_or_adr
        gate = "us_or_adr"
    else:
        gated = []
        gate = "unresolved"

    for cand in gated:
        if not cand.get("display_name"):
            cand["display_name"] = _display_from_index(cand["code"], index)

    return {
        "gate": gate,
        "enter_coverage": gate == "in_scope",
        "picker": False,
        "candidates": gated,
    }
