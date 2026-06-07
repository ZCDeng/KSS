"""动量 regime (R3) 判定 —— 科创板是否处于动量行情.

定义与验证见 docs/solutions/etf_flow_signal_lessons.md 与
storage/reports/momentum_regime_research_20260607.md:

    R3 = mom20 > +8% 且 breadth_5dma >= 2%

- ``mom20``: 科创50 指数 (000688.SH) 20 个交易日收益
  (研究用主题复合 NAV, 生产用指数等价代理, 少 9 个 API 调用)
- ``breadth_5dma``: cs_data 全池当日涨幅 >=9.5% 家数占比的 5 日均值.
  cs_data 由 8:30 cron 拉 T-1, 最近 1-2 天结构性不完整 → 取最近一个
  池规模 >= MIN_POOL 的交易日, 实时滞后约 2 个交易日, 输出里如实标注.

一年回测覆盖 27%, 命中 2025Q3/2026Q2 动量季、2025Q4 调整季仅 3%.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from kss.data.tushare_client import TushareClient

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]

MOM_INDEX = "000688.SH"   # 科创50
MOM_TH = 8.0              # mom20 阈值 %
BREADTH_TH = 0.02         # breadth_5dma 阈值
BIG_UP_PCT = 9.5          # "大阳" 日涨幅阈值 %
MIN_POOL = 300            # 单日池规模下限 (低于视为数据不完整日)


def _mom20(trade_date: str, client: TushareClient) -> float | None:
    """科创50 指数 20 个交易日收益 %."""
    start = (
        datetime.strptime(trade_date, "%Y%m%d") - timedelta(days=45)
    ).strftime("%Y%m%d")
    df = client.fetch_index_daily(MOM_INDEX, start, trade_date)
    if df is None or len(df) < 21:
        logger.warning("momentum_regime: 科创50 指数数据不足 (%s)", trade_date)
        return None
    df = df.sort_values("trade_date")
    closes = df["close"].astype(float)
    return float((closes.iloc[-1] / closes.iloc[-21] - 1) * 100)


def _breadth_5dma(trade_date: str, cs_dir: Path) -> tuple[float | None, str | None]:
    """全池大阳家数占比 5 日均值 + 实际数据日期.

    Returns:
        ``(breadth, data_date)``; 可用交易日不足 5 个时 ``(None, None)``.
    """
    start_cal = (
        datetime.strptime(trade_date, "%Y%m%d") - timedelta(days=25)
    ).strftime("%Y-%m-%d")
    end_cal = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}"
    counts: dict[str, int] = {}
    totals: dict[str, int] = {}
    for f in cs_dir.glob("cs_data_*.csv"):
        try:
            df = pd.read_csv(f, usecols=["trade_date", "pct_chg"])
        except (ValueError, OSError):
            continue
        df = df[(df["trade_date"] >= start_cal) & (df["trade_date"] <= end_cal)]
        for d, p in zip(df["trade_date"], df["pct_chg"]):
            totals[d] = totals.get(d, 0) + 1
            if p >= BIG_UP_PCT:
                counts[d] = counts.get(d, 0) + 1
    valid = sorted(d for d, n in totals.items() if n >= MIN_POOL)
    if len(valid) < 5:
        return None, None
    last5 = valid[-5:]
    breadth = sum(counts.get(d, 0) / totals[d] for d in last5) / 5
    return breadth, last5[-1].replace("-", "")


def build_regime_status(
    trade_date: str,
    client: TushareClient | None = None,
    cs_dir: Path | None = None,
) -> dict[str, Any] | None:
    """R3 动量 regime 状态.

    Returns:
        ``{"in_regime": bool, "mom20": float, "mom20_th": float,
        "breadth_5dma": float|None, "breadth_th": float,
        "breadth_data_date": str|None}``;
        mom20 取不到时返回 ``None`` (breadth 单独缺失 → in_regime 按
        mom20 单条件保守判 False, 但仍返回已有字段).
    """
    if client is None:
        client = TushareClient()
    cs_dir = cs_dir or PROJECT_ROOT

    mom = _mom20(trade_date, client)
    if mom is None:
        return None
    breadth, bdate = _breadth_5dma(trade_date, cs_dir)
    if breadth is None:
        logger.warning("momentum_regime: breadth 不可用, in_regime 保守判 False")
    in_regime = mom > MOM_TH and breadth is not None and breadth >= BREADTH_TH
    return {
        "in_regime": in_regime,
        "mom20": round(mom, 2),
        "mom20_th": MOM_TH,
        "breadth_5dma": round(breadth, 4) if breadth is not None else None,
        "breadth_th": BREADTH_TH,
        "breadth_data_date": bdate,
    }
