"""覆盖脊柱：采集、VIE 门、脚本估值/检查器、R9 JSON。只读。"""

from __future__ import annotations

import json
from typing import Any, Callable

from kss.equity_research.checker import run_checker
from kss.equity_research.envelope import Heartbeat
from kss.equity_research.intent import is_coverage_intent, r12_phrase, too_many_names
from kss.equity_research.listing_resolve import resolve_listing
from kss.research.evidence import quarantine_rating_inputs
from kss.equity_research.valuation import calibrate, kelly_lite, three_methods

_HK_CN_HINTS = ("阿里", "腾讯", "美团", "小米", "京东", "网易", "拼多多", "-SW", "-W")


def run_coverage(
    query: str,
    mode: str = "full",
    *,
    catalog_items: list[dict[str, Any]] | None = None,
    name_index: dict[str, Any] | None = None,
    board: dict[str, Any] | None = None,
    assumptions: dict[str, Any] | None = None,
    fundamentals: dict[str, Any] | None = None,
    excerpts: list[dict[str, Any]] | None = None,
    history_years: int | None = None,
    history_quarters: int | None = None,
    vie_priced: bool | None = None,
    on_update: Callable[[dict[str, Any]], None] | None = None,
    heartbeat_interval: float = 15.0,
    published: dict[str, Any] | None = None,
    force_new: bool | None = None,
) -> dict[str, Any]:
    beat = Heartbeat(on_update, min_interval=heartbeat_interval)
    beat.emit("resolve", step="listing")
    if too_many_names(query):
        return _r12("out_of_scope", query=query, mode=mode)
    if published and not _should_rerun(query, force_new):
        beat.emit("cite_published", step="cite")
        return {
            **published,
            "cited_only": True,
            "spine_ran": False,
        }
    listing = resolve_listing(
        query,
        catalog_items=catalog_items,
        name_index=name_index,
    )
    if listing.get("gate") == "us_or_adr":
        return _r12("out_of_scope", query=query, mode=mode, listing=listing)
    if listing.get("gate") != "in_scope":
        return _r12("out_of_scope", query=query, mode=mode, listing=listing)

    kept_excerpts, dropped = quarantine_rating_inputs(excerpts)
    beat.emit("quarantine", step="evidence", dropped=len(dropped))

    candidates = list(listing.get("candidates") or [])
    sides: list[dict[str, Any]] = []
    for cand in candidates:
        sides.append(_side_result(
            cand,
            board=board or {},
            assumptions=assumptions or {},
            fundamentals=fundamentals or {},
            excerpts=kept_excerpts,
            dropped=dropped,
            history_years=history_years,
            history_quarters=history_quarters,
            vie_priced=vie_priced,
            mode=mode,
        ))

    r9 = _merge_r9(sides)
    payload = {
        "status": "ok",
        "r12": None,
        "query": query,
        "mode": "earnings" if mode in {"earnings", "财报"} else "full",
        "gate": "in_scope",
        "enter_coverage": True,
        "picker": False,
        "listing": listing,
        "sides": sides,
        "r9": r9,
        "dropped_excerpts": len(dropped),
        "cited_only": False,
        "spine_ran": True,
    }
    beat.emit("valued", step="done")
    return payload


def _should_rerun(query: str, force_new: bool | None) -> bool:
    if force_new is True:
        return True
    if force_new is False:
        return False
    return is_coverage_intent(query)


def _side_result(
    cand: dict[str, Any],
    *,
    board: dict[str, Any],
    assumptions: dict[str, Any],
    fundamentals: dict[str, Any],
    excerpts: list[dict[str, Any]],
    dropped: list[dict[str, Any]],
    history_years: int | None,
    history_quarters: int | None,
    vie_priced: bool | None,
    mode: str,
) -> dict[str, Any]:
    code = str(cand.get("code") or "")
    suffix = str(cand.get("suffix") or "")
    name = str(cand.get("display_name") or "")
    quote = board.get(code) if isinstance(board.get(code), dict) else board.get("quote")
    if quote is None and len(board) == 1 and "price" in board:
        quote = board
    missing_quote = not isinstance(quote, dict) or quote.get("price") in (None, "", "未获取到")
    board_out = {"status": "未获取到"} if missing_quote else {
        "price": quote.get("price"),
        "change_pct": quote.get("change_pct", "未获取到"),
        "source": "kss",
    }
    years = 3 if history_years is None else history_years
    quarters = 8 if history_quarters is None else history_quarters
    limited = years < 3 or quarters < 8
    history = {
        "years": years,
        "quarters": quarters,
        "limited": limited,
        "note": "历史长度不足 3 年年报 + 8 季，按可获取历史重建" if limited else None,
    }
    vie_needed = suffix == ".HK" and _looks_cn_hk(name, code)
    dropped_vie = vie_needed and bool(dropped) and not excerpts
    priced = True if not vie_needed else bool(vie_priced) and not dropped_vie
    vie = {
        "required": vie_needed,
        "priced": priced if vie_needed else True,
        "note": None if priced else "结构风险未定价，该侧观望",
    }
    local_assumptions = dict(assumptions)
    if not missing_quote and "price" not in local_assumptions:
        local_assumptions["price"] = quote.get("price")
    methods = three_methods(local_assumptions)
    cal = calibrate(methods.get("fair_value"), local_assumptions.get("price"))
    kelly = kelly_lite(local_assumptions, cal.get("upside"))
    action = cal.get("action") or "观望"
    if missing_quote or not vie["priced"]:
        action = "观望"
        kelly = {
            "kelly_lite": None,
            "kelly_skipped": True,
            "kelly_skip_reason": "unpriced_vie_or_missing_quote",
        }
        cal = {**cal, "action": "观望"}
        if missing_quote:
            cal["label"] = cal.get("label")
    checker = run_checker(suffix=suffix, fundamentals=fundamentals, excerpts=excerpts)
    return {
        "code": code,
        "suffix": suffix,
        "display_name": name,
        "board": board_out,
        "history": history,
        "vie": vie,
        "valuation": {
            **methods,
            **cal,
            **kelly,
        },
        "checker": checker,
        "web_quote_used_for_action": False,
        "mode": "earnings" if mode in {"earnings", "财报"} else "full",
    }


def _looks_cn_hk(name: str, code: str) -> bool:
    blob = f"{name} {code}"
    return any(h in blob for h in _HK_CN_HINTS)


def _merge_r9(sides: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not sides:
        return None
    primary = sides[0]["valuation"]
    if primary.get("label") is None and primary.get("action") == "观望" and primary.get("kelly_lite") is None:
        # still a valid R9 block: 观望 is scripted
        pass
    return {
        "label": primary.get("label"),
        "action": primary.get("action"),
        "kelly_lite": primary.get("kelly_lite"),
        "kelly_skipped": primary.get("kelly_skipped"),
        "quality_grade": sides[0]["checker"].get("quality_grade"),
        "fair_value": primary.get("fair_value"),
        "by_side": [
            {
                "code": s["code"],
                "label": s["valuation"].get("label"),
                "action": s["valuation"].get("action"),
                "kelly_lite": s["valuation"].get("kelly_lite"),
            }
            for s in sides
        ],
    }


def _r12(kind: str, **extra: Any) -> dict[str, Any]:
    return {
        "status": kind,
        "r12": r12_phrase(kind),
        "enter_coverage": False,
        "r9": None,
        "spine_ran": True,
        "cited_only": False,
        **extra,
    }


def dumps_stable(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
