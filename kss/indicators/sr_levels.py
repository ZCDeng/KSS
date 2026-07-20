"""S/R 位识别：分形 pivot → ATR 容差聚类 → 触及计分（含多周期汇聚）.

独立可复用模块——图表按需投影（bridge stock_detail）与信号基元族 sr_level
（kss.indicators.primitives/rules）共同消费本模块的 ``detect_levels``/
``causal_features``，互不重复实现算法（见 plan 2026-07-20-001 KTD1）。

因果性约定：``detect_levels``/``causal_features`` 只使用截至 asof（或数据末尾）
的信息；分形 pivot 在位置 ``i`` 需右侧 ``pivot_window`` 根 bar 齐备才算确认，
确认发生在 ``i + pivot_window``——早于此的调用方看不到该 pivot。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from kss.features.technical import TechnicalFactors

FAMILY_SR_LEVEL = "sr_level"

DEFAULT_LEVEL_PARAMS: dict[str, Any] = {
    "pivot_window": 5,
    "atr_period": 14,
    "cluster_atr_mult": 1.0,
    "recency_halflife": 60,
    "multi_timeframe": False,
    "mtf_weight": 0.5,
}

_MAX_LEVELS_RETURNED = 6


@dataclass
class Level:
    """一条识别出的支撑/阻力位。"""

    price: float
    kind: str  # "support" | "resistance"
    score: float
    touches: int
    last_touch_date: str


@dataclass
class _Cluster:
    """一簇邻近 pivot 价的运行态（增量维护，避免逐 bar 全量重聚类）。"""

    prices: list[float] = field(default_factory=list)
    touch_dates: list[pd.Timestamp] = field(default_factory=list)

    @property
    def center(self) -> float:
        return float(np.mean(self.prices))

    def add(self, price: float, date: pd.Timestamp) -> None:
        self.prices.append(price)
        self.touch_dates.append(date)


def _rolling_pivots(high: np.ndarray, low: np.ndarray, window: int) -> tuple[np.ndarray, np.ndarray]:
    """因果分形 pivot：位置 i 需右侧 window 根 bar 齐备才算确认（确认时刻 = i+window）。"""
    n = len(high)
    is_high = np.zeros(n, dtype=bool)
    is_low = np.zeros(n, dtype=bool)
    for i in range(window, n - window):
        seg_h = high[i - window : i + window + 1]
        seg_l = low[i - window : i + window + 1]
        if high[i] >= seg_h.max():
            is_high[i] = True
        if low[i] <= seg_l.min():
            is_low[i] = True
    return is_high, is_low


def _merge_into(clusters: list[_Cluster], price: float, date: pd.Timestamp, tolerance: float) -> None:
    for c in clusters:
        if abs(c.center - price) <= tolerance:
            c.add(price, date)
            return
    nc = _Cluster()
    nc.add(price, date)
    clusters.append(nc)


def _score_cluster(cluster: _Cluster, asof: pd.Timestamp, halflife: int) -> float:
    """触及计分 = Σ 每次触及的近因衰减（半衰期 halflife 个自然日）。"""
    score = 0.0
    for d in cluster.touch_dates:
        age_days = max((asof - pd.Timestamp(d)).days, 0)
        score += 0.5 ** (age_days / max(halflife, 1))
    return score


def _atr_series(d: pd.DataFrame, period: int) -> pd.Series:
    return TechnicalFactors.atr(d["high"], d["low"], d["close"], period=period)


def _weekly_resample(d: pd.DataFrame) -> pd.DataFrame:
    w = d.set_index("trade_date")
    weekly = w.resample("W-FRI").agg({"open": "first", "high": "max", "low": "min", "close": "last"})
    return weekly.dropna(how="all").reset_index()


def detect_levels(
    df: pd.DataFrame, params: dict[str, Any] | None = None, *, asof: str | None = None
) -> list[Level]:
    """输入日线 OHLCV，输出当前有效支撑/阻力位（按评分降序，截断前 ``_MAX_LEVELS_RETURNED`` 条）.

    因果性：先按 asof 裁剪数据，其余计算只读裁剪后的数据——追加未来 bar 不改变
    asof 之前的结果。样本不足（< pivot_window*2+5 根）或空输入返回空列表，不抛异常。
    """
    p = {**DEFAULT_LEVEL_PARAMS, **(params or {})}
    if df is None or df.empty:
        return []
    d = df.copy()
    d["trade_date"] = pd.to_datetime(d["trade_date"])
    if asof is not None:
        d = d[d["trade_date"] <= pd.Timestamp(asof)]
    d = d.sort_values("trade_date").reset_index(drop=True)
    n = len(d)
    window = int(p["pivot_window"])
    if n < window * 2 + 5:
        return []

    atr = _atr_series(d, int(p["atr_period"]))
    tolerance = float(atr.iloc[-1] * p["cluster_atr_mult"]) if pd.notna(atr.iloc[-1]) else 0.0
    if not np.isfinite(tolerance) or tolerance <= 0:
        tolerance = float(d["close"].iloc[-1] * 0.01)

    is_high, is_low = _rolling_pivots(d["high"].values, d["low"].values, window)
    dates = d["trade_date"].values
    high_clusters: list[_Cluster] = []
    low_clusters: list[_Cluster] = []
    for i in range(n):
        if is_high[i]:
            _merge_into(high_clusters, float(d["high"].iloc[i]), pd.Timestamp(dates[i]), tolerance)
        if is_low[i]:
            _merge_into(low_clusters, float(d["low"].iloc[i]), pd.Timestamp(dates[i]), tolerance)

    asof_ts = pd.Timestamp(d["trade_date"].iloc[-1])
    halflife = int(p["recency_halflife"])

    weekly_centers: list[float] = []
    if p.get("multi_timeframe"):
        weekly = _weekly_resample(d)
        w_window = min(window, 2)
        if len(weekly) >= w_window * 2 + 3:
            wh, wl = _rolling_pivots(weekly["high"].values, weekly["low"].values, w_window)
            for i in range(len(weekly)):
                if wh[i]:
                    weekly_centers.append(float(weekly["high"].iloc[i]))
                if wl[i]:
                    weekly_centers.append(float(weekly["low"].iloc[i]))

    levels: list[Level] = []
    for kind, clusters in (("resistance", high_clusters), ("support", low_clusters)):
        for c in clusters:
            score = _score_cluster(c, asof_ts, halflife)
            if weekly_centers and any(abs(c.center - wc) <= tolerance for wc in weekly_centers):
                score *= 1.0 + float(p.get("mtf_weight", 0.0))
            levels.append(
                Level(
                    price=round(c.center, 3),
                    kind=kind,
                    score=round(score, 4),
                    touches=len(c.prices),
                    last_touch_date=str(pd.Timestamp(max(c.touch_dates)).date()),
                )
            )
    levels.sort(key=lambda lv: lv.score, reverse=True)
    return levels[:_MAX_LEVELS_RETURNED]


def causal_features(df: pd.DataFrame, params: dict[str, Any] | None = None) -> pd.DataFrame:
    """逐 bar 因果特征：最近支撑/阻力价与强度——供 ``kss.indicators.rules`` 的
    ``sr_level`` 族消费（增量维护簇状态，O(n·k)，k=簇数，不逐 bar 全量重跑聚类）。
    """
    p = {**DEFAULT_LEVEL_PARAMS, **(params or {})}
    d = df.copy()
    d["trade_date"] = pd.to_datetime(d["trade_date"])
    n = len(d)
    out = pd.DataFrame(index=d.index)
    out["nearest_support"] = np.nan
    out["nearest_resistance"] = np.nan
    out["support_strength"] = 0.0
    out["resistance_strength"] = 0.0
    window = int(p["pivot_window"])
    if n < window * 2 + 5:
        return out

    atr = _atr_series(d, int(p["atr_period"]))
    is_high, is_low = _rolling_pivots(d["high"].values, d["low"].values, window)
    highs = d["high"].values
    lows = d["low"].values
    closes = d["close"].values
    dates = d["trade_date"].values
    halflife = int(p["recency_halflife"])

    high_clusters: list[_Cluster] = []
    low_clusters: list[_Cluster] = []
    sup_col = out.columns.get_loc("nearest_support")
    res_col = out.columns.get_loc("nearest_resistance")
    sup_str_col = out.columns.get_loc("support_strength")
    res_str_col = out.columns.get_loc("resistance_strength")

    for i in range(n):
        # 位置 j = i - window 处的 pivot 在 i 时刻已确认（右侧 window 根已齐备）。
        j = i - window
        if j >= 0:
            tol = float(atr.iloc[i] * p["cluster_atr_mult"]) if pd.notna(atr.iloc[i]) else 0.0
            if not np.isfinite(tol) or tol <= 0:
                tol = float(closes[i] * 0.01)
            if is_high[j]:
                _merge_into(high_clusters, float(highs[j]), pd.Timestamp(dates[j]), tol)
            if is_low[j]:
                _merge_into(low_clusters, float(lows[j]), pd.Timestamp(dates[j]), tol)

        cur_date = pd.Timestamp(dates[i])
        close = closes[i]
        support_candidates = [c for c in low_clusters if c.center <= close]
        resistance_candidates = [c for c in high_clusters if c.center >= close]
        if support_candidates:
            best = max(support_candidates, key=lambda c: c.center)
            out.iat[i, sup_col] = best.center
            out.iat[i, sup_str_col] = _score_cluster(best, cur_date, halflife)
        if resistance_candidates:
            best = min(resistance_candidates, key=lambda c: c.center)
            out.iat[i, res_col] = best.center
            out.iat[i, res_str_col] = _score_cluster(best, cur_date, halflife)
    return out


def hit_stats(df: pd.DataFrame, params: dict[str, Any] | None = None, *, forward_days: int = 5) -> dict[str, Any]:
    """位命中统计（非因果，体检报告用途）：全历史回溯每次触及后 forward_days 内
    价格反弹/跌破占比——支撑反弹=触后上涨，阻力反弹=触后下跌（成功阻挡）。
    """
    levels = detect_levels(df, params)
    if not levels or df is None or df.empty:
        return {"levels": 0, "touches": 0, "rebound_rate": None, "breakdown_rate": None}

    p = {**DEFAULT_LEVEL_PARAMS, **(params or {})}
    d = df.copy()
    d["trade_date"] = pd.to_datetime(d["trade_date"])
    d = d.sort_values("trade_date").reset_index(drop=True)
    atr = _atr_series(d, int(p["atr_period"]))
    tol = float(atr.iloc[-1] * p["cluster_atr_mult"]) if pd.notna(atr.iloc[-1]) else float(d["close"].iloc[-1] * 0.01)
    if not np.isfinite(tol) or tol <= 0:
        tol = float(d["close"].iloc[-1] * 0.01)

    closes = d["close"].values
    n = len(d)
    total_touches = 0
    rebounds = 0
    breakdowns = 0
    for lv in levels:
        for i in range(n):
            if abs(closes[i] - lv.price) > tol:
                continue
            end = min(i + forward_days, n - 1)
            if end <= i:
                continue
            total_touches += 1
            fwd_ret = closes[end] / closes[i] - 1.0
            if lv.kind == "support":
                if fwd_ret > 0:
                    rebounds += 1
                elif fwd_ret < 0:
                    breakdowns += 1
            else:
                if fwd_ret < 0:
                    rebounds += 1
                elif fwd_ret > 0:
                    breakdowns += 1
    return {
        "levels": len(levels),
        "touches": total_touches,
        "rebound_rate": round(rebounds / total_touches, 4) if total_touches else None,
        "breakdown_rate": round(breakdowns / total_touches, 4) if total_touches else None,
    }


def to_levels_overlay(levels: list[Level], *, status: str = "ok", reason: str = "") -> dict[str, Any]:
    """图表 overlay 投影（bridge 消费）：与信号 pack 状态完全独立——status 只反映
    位计算本身（数据不足/异常），不受信号族 GO/NO-GO 影响（plan KTD1）。

    不含 id 字段——与 ``kss.indicators.pack.to_overlay`` 同惯例，由调用方（bridge）
    按消费端需要的大小写自行注入（Swift 侧 plain JSONDecoder 要求 camelCase）。
    """
    return {
        "status": status,
        "reason": reason,
        "levels": [
            {"price": lv.price, "kind": lv.kind, "strength": lv.score, "touches": lv.touches}
            for lv in levels
        ],
        "markers": [],
        "series": [],
    }
