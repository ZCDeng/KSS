"""PE 动态计算（纯函数）：PE_TTM 现值 + 历史分位.

分位算法与 ``scripts/scan_bj50.py`` 一致：``(历史序列 < 现值).mean()``。
序列点数不足或 PE 非正 → 分位 None（不外推）。
"""

from __future__ import annotations

from typing import Any

import pandas as pd

_MIN_POINTS = 30


def pe_percentile(
    pe_series: list[float] | pd.Series,
    pe_now: float | None,
    min_points: int = _MIN_POINTS,
) -> float | None:
    """PE 历史分位 = 历史序列中低于现值的占比.

    Returns:
        [0,1] 分位；现值非正、序列有效点 < min_points 时返回 None.
    """
    if pe_now is None or pe_now <= 0:
        return None
    vals = [
        float(x) for x in pe_series
        if x is not None and float(x) == float(x) and float(x) > 0
    ]
    if len(vals) < min_points:
        return None
    return round(sum(1 for x in vals if x < pe_now) / len(vals), 4)


def pe_dynamics(df: pd.DataFrame | None) -> dict[str, Any]:
    """从 daily_basic 历史窗口算 PE_TTM 现值 + 历史分位.

    Args:
        df: ``fetch_daily_basic_history`` 返回（含 ``trade_date`` / ``pe_ttm``）.

    Returns:
        ``status`` ∈ {``ok``, ``unavailable``}. ``ok`` 时含 ``pe_ttm`` /
        ``percentile`` / ``n_points`` / ``as_of``.
    """
    if df is None or df.empty or "pe_ttm" not in df:
        return {"status": "unavailable"}

    d = df.sort_values("trade_date")
    series = pd.to_numeric(d["pe_ttm"], errors="coerce").dropna()
    if series.empty:
        return {"status": "unavailable", "reason": "no_pe_ttm"}

    pe_now = float(series.iloc[-1])
    pct = pe_percentile(series.tolist(), pe_now)
    return {
        "status": "ok",
        "pe_ttm": round(pe_now, 2),
        "percentile": pct,
        "n_points": int(len(series)),
        "as_of": str(d.iloc[-1].get("trade_date", "")),
    }
