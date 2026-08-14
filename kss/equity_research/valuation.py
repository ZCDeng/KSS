"""改编自 rollingSirius/equity-research-skill 的标定与 Kelly-lite（MIT）。

标签、动作、仓位只由本模块计算；模型只能引用返回 JSON。
"""

from __future__ import annotations

from typing import Any

# 许可：上游 equity-research-skill MIT。此处仅保留标定/仓位算法，数据脊走 KSS。


def three_methods(assumptions: dict[str, Any]) -> dict[str, float]:
    price = _num(assumptions.get("price"))
    eps = _num(assumptions.get("eps"))
    bvps = _num(assumptions.get("bvps"))
    fcf = _num(assumptions.get("fcf"))
    shares = _num(assumptions.get("shares")) or 1.0
    wacc = _num(assumptions.get("wacc")) or 0.10
    growth = _num(assumptions.get("growth")) or 0.03
    pe = _num(assumptions.get("target_pe")) or 15.0
    pb = _num(assumptions.get("target_pb")) or 2.0
    pe_value = (eps * pe) if eps is not None else None
    pb_value = (bvps * pb) if bvps is not None else None
    dcf_value = None
    if fcf is not None and wacc > growth:
        terminal = fcf * (1 + growth) / (wacc - growth)
        dcf_value = (fcf + terminal) / shares
    values = [v for v in (dcf_value, pe_value, pb_value) if v is not None]
    if not values and price is not None:
        values = [price]
    fair = sum(values) / len(values) if values else None
    return {
        "dcf": dcf_value,
        "pe": pe_value,
        "pb": pb_value,
        "fair_value": fair,
        "price": price,
    }


def calibrate(fair_value: float | None, price: float | None) -> dict[str, Any]:
    if fair_value is None or price is None or price == 0:
        return {
            "label": None,
            "action": "观望",
            "upside": None,
        }
    upside = (fair_value - price) / price
    if upside >= 0.20:
        label, action = "低估", "买入"
    elif upside <= -0.20:
        label, action = "高估", "减持"
    else:
        label, action = "合理", "持有"
    return {"label": label, "action": action, "upside": round(upside, 4)}


def kelly_lite(assumptions: dict[str, Any], upside: float | None) -> dict[str, Any]:
    price = _num(assumptions.get("price"))
    win = _num(assumptions.get("win_prob"))
    lose = _num(assumptions.get("lose_prob"))
    if price is None or upside is None or win is None or lose is None:
        return {
            "kelly_lite": None,
            "kelly_skipped": True,
            "kelly_skip_reason": "missing_price_or_scenarios",
        }
    edge = win * max(upside, 0) - lose * abs(min(upside, 0) if upside < 0 else 0.1)
    odds = abs(upside) if upside else 0.1
    raw = edge / odds if odds else 0.0
    clipped = max(0.0, min(0.25, raw))
    return {
        "kelly_lite": round(clipped, 4),
        "kelly_skipped": False,
        "kelly_skip_reason": None,
    }


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
