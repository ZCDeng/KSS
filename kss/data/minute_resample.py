"""60 分钟 K 规范化，以及按 A 股上下午会话合成 120 分钟 K.

120 分钟不是 Tushare/东财原生周期；只允许从更细的 60 分钟 bar 聚合，
禁止用日线伪造分钟（对齐分钟 PIT 计划：不从日线合成分钟）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# Tushare 60min 完整会话的 bar 结束时刻（不含 09:30 集合竞价快照）。
COMPLETE_60M_TIMES: frozenset[str] = frozenset({"10:30", "11:30", "14:00", "15:00"})


def normalize_stk_mins(df: pd.DataFrame) -> pd.DataFrame:
    """把 ``stk_mins`` 原始表收成指标引擎通用 OHLCV（按时间升序）。"""
    out = df.copy()
    if "trade_time" in out.columns and "bar_end_ts" not in out.columns:
        out = out.rename(columns={"trade_time": "bar_end_ts"})
    if "vol" in out.columns and "volume" not in out.columns:
        out = out.rename(columns={"vol": "volume"})
    out["bar_end_ts"] = pd.to_datetime(out["bar_end_ts"])
    out["trade_date"] = out["bar_end_ts"].dt.normalize()
    for col in ("open", "high", "low", "close", "volume", "amount"):
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    hhmm = out["bar_end_ts"].dt.strftime("%H:%M")
    out = out.loc[hhmm.isin(COMPLETE_60M_TIMES)].copy()
    out = out.sort_values("bar_end_ts").drop_duplicates("bar_end_ts").reset_index(drop=True)
    return out


def resample_session_halves(df: pd.DataFrame) -> pd.DataFrame:
    """60 分钟 → 120 分钟：上午 09:30–11:30、下午 13:00–15:00 各一根.

    用 bar 结束时刻 ≤ 11:30 归上午，其余归下午。缺失半场则当天少一根。
    """
    if df.empty:
        return df.copy()
    work = df.copy()
    ts = pd.to_datetime(work["bar_end_ts"])
    minutes = ts.dt.hour * 60 + ts.dt.minute
    work["_half"] = np.where(minutes <= 11 * 60 + 30, "AM", "PM")
    work["trade_date"] = pd.to_datetime(work["trade_date"]).dt.normalize()
    grouped = work.groupby(["trade_date", "_half"], sort=True)
    agg: dict[str, tuple[str, str]] = {
        "open": ("open", "first"),
        "high": ("high", "max"),
        "low": ("low", "min"),
        "close": ("close", "last"),
        "volume": ("volume", "sum"),
        "bar_end_ts": ("bar_end_ts", "last"),
    }
    # 没有成交额时不要用成交量冒充 amount，否则 VWAP=amount/volume 退化成 1。
    if "amount" in work.columns:
        agg["amount"] = ("amount", "sum")
    out = grouped.agg(**agg).reset_index()
    return out.drop(columns=["_half"])
