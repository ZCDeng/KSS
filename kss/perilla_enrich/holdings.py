"""机构持仓动态计算（纯函数，输入 Tushare top10_floatholders DataFrame）.

两块指标，同一张 ``top10_floatholders`` 表算出：
- ``top10_dynamics`` — 最新一季前十大流通股东结构 + 环比增减仓方向。
- ``northbound_trend`` — 北向（陆股通）持股趋势；北向以 "香港中央结算有限公司"
  名义出现在同表中（本账号无 ``hk_hold`` 端点权限，故从此表提取）。

无 IO、无 LLM；金融数字确定性计算。
"""

from __future__ import annotations

from typing import Any

import pandas as pd

_HK_HOLDER = "香港中央结算"


def _num(v: Any) -> float | None:
    try:
        if v is None:
            return None
        f = float(v)
        return f if f == f else None  # NaN → None
    except (TypeError, ValueError):
        return None


def top10_dynamics(df: pd.DataFrame | None) -> dict[str, Any]:
    """最新一季前十大流通股东结构 + 环比增减仓.

    Returns:
        ``status`` ∈ {``ok``, ``unavailable``}. ``ok`` 时含 ``latest_period`` /
        ``n_holders`` / ``n_increasing`` / ``n_decreasing`` / ``net_direction`` /
        ``movers``（按持股比例降序，含机构名/类型/比例/变动股数）.
    """
    if df is None or df.empty or "end_date" not in df:
        return {"status": "unavailable"}

    latest = df["end_date"].max()
    cur = df[df["end_date"] == latest]
    inc = dec = 0
    movers: list[dict[str, Any]] = []
    for _, r in cur.iterrows():
        chg = _num(r.get("hold_change"))
        ratio = _num(r.get("hold_ratio"))
        if chg is not None and chg > 0:
            inc += 1
        elif chg is not None and chg < 0:
            dec += 1
        movers.append({
            "name": str(r.get("holder_name", "")),
            "type": str(r.get("holder_type", "")),
            "hold_ratio": ratio,
            "change_shares": chg,
        })
    movers.sort(key=lambda m: (m["hold_ratio"] is not None, m["hold_ratio"] or 0), reverse=True)

    if inc > dec:
        net = "increasing"
    elif dec > inc:
        net = "decreasing"
    else:
        net = "flat"

    return {
        "status": "ok",
        "latest_period": str(latest),
        "n_holders": int(len(cur)),
        "n_increasing": inc,
        "n_decreasing": dec,
        "net_direction": net,
        "movers": movers,
    }


def northbound_trend(df: pd.DataFrame | None) -> dict[str, Any]:
    """北向（陆股通）持股趋势，从 "香港中央结算" 行提取.

    Returns:
        ``status`` ∈ {``ok``, ``unavailable``}. ``ok`` 时含 ``latest_period`` /
        ``hold_ratio`` / ``qoq_change`` / ``direction``.
    """
    if df is None or df.empty or "holder_name" not in df:
        return {"status": "unavailable"}

    hk = df[df["holder_name"].astype(str).str.contains(_HK_HOLDER, na=False)]
    if hk.empty:
        return {"status": "unavailable", "reason": "no_northbound_holder"}

    hk = hk.sort_values("end_date")
    latest = hk.iloc[-1]
    ratio = _num(latest.get("hold_ratio"))
    prev_ratio = _num(hk.iloc[-2].get("hold_ratio")) if len(hk) >= 2 else None
    qoq = (ratio - prev_ratio) if (ratio is not None and prev_ratio is not None) else None

    if qoq is None:
        direction = "unknown"
    elif qoq > 0:
        direction = "increasing"
    elif qoq < 0:
        direction = "decreasing"
    else:
        direction = "flat"

    return {
        "status": "ok",
        "latest_period": str(latest.get("end_date", "")),
        "hold_ratio": ratio,
        "qoq_change": round(qoq, 4) if qoq is not None else None,
        "direction": direction,
    }
