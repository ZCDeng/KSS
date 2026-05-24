"""宏观单日快照拼装（容错降级，缺项不抛）."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import pandas as pd

from kss.data.macro_client import MacroClient, pivot_yield_curve

logger = logging.getLogger(__name__)


@dataclass
class MacroSnapshot:
    """单日宏观快照.

    Attributes:
        trade_date: 目标交易日，``YYYYMMDD``.
        shibor: Shibor 多期限利率 DataFrame（最近 N 个交易日含目标日）。失败 ``None``.
        yield_curve_wide: 中债国债收益率曲线宽表（含 ``yld_3m`` / ``yld_1y`` /
            ``yld_5y`` / ``yld_10y`` / ``yld_30y``，按 ``trade_date`` 行）。失败 ``None``.
        money_supply: M0/M1/M2 月度（含目标日所在月及前 N 个月）。失败 ``None``.
        cpi: CPI 月度。失败 ``None``.
        ppi: PPI 月度。失败 ``None``.
        credit_curve: AkShare 信用利差矩阵（仅最新一日，目标日历史值需另存）。
            未拉取或失败为 ``None``.
        missing: 拉取失败的字段名列表，供日志/报告标注.
    """

    trade_date: str
    shibor: pd.DataFrame | None = None
    yield_curve_wide: pd.DataFrame | None = None
    money_supply: pd.DataFrame | None = None
    cpi: pd.DataFrame | None = None
    ppi: pd.DataFrame | None = None
    credit_curve: pd.DataFrame | None = None
    missing: list[str] = field(default_factory=list)


def load_macro_snapshot(
    trade_date: str,
    lookback_days: int = 30,
    lookback_months: int = 6,
    include_credit: bool = True,
    client: MacroClient | None = None,
) -> MacroSnapshot:
    """拉取 ``trade_date`` 当日宏观快照，附带回溯窗口供后续 Δr 计算.

    Args:
        trade_date: 目标交易日，``YYYYMMDD``.
        lookback_days: 日频字段（shibor / yield_curve）回溯天数；30 足够覆盖 Δr_20d.
        lookback_months: 月频字段（money_supply / cpi / ppi）回溯月数.
        include_credit: 是否调 AkShare 拉信用利差（耗时较长，可关）.
        client: 注入 :class:`MacroClient`（测试用，生产传 ``None`` 自动创建）.

    Returns:
        :class:`MacroSnapshot`；任一字段失败仅记入 ``missing``，其他字段不受影响.
    """
    client = client or MacroClient()
    snap = MacroSnapshot(trade_date=trade_date)

    daily_start = _shift_days(trade_date, -lookback_days)
    snap.shibor = client.fetch_shibor(daily_start, trade_date)
    if snap.shibor is None:
        snap.missing.append("shibor")

    yc_long = client.fetch_cn_yield_curve(daily_start, trade_date)
    if yc_long is None:
        snap.missing.append("yield_curve")
    else:
        snap.yield_curve_wide = pivot_yield_curve(yc_long)
        if snap.yield_curve_wide is None:
            snap.missing.append("yield_curve_pivot")

    month_start = _shift_months(trade_date[:6], -lookback_months)
    target_month = trade_date[:6]
    snap.money_supply = client.fetch_cn_money_supply(month_start, target_month)
    if snap.money_supply is None:
        snap.missing.append("money_supply")
    snap.cpi = client.fetch_cn_cpi(month_start, target_month)
    if snap.cpi is None:
        snap.missing.append("cpi")
    snap.ppi = client.fetch_cn_ppi(month_start, target_month)
    if snap.ppi is None:
        snap.missing.append("ppi")

    if include_credit:
        snap.credit_curve = client.fetch_credit_yield_curve_akshare()
        if snap.credit_curve is None:
            snap.missing.append("credit_curve")

    if snap.missing:
        logger.warning(
            "MacroSnapshot %s 缺失字段: %s", trade_date, ", ".join(snap.missing)
        )
    return snap


def _shift_days(yyyymmdd: str, delta: int) -> str:
    dt = datetime.strptime(yyyymmdd, "%Y%m%d") + timedelta(days=delta)
    return dt.strftime("%Y%m%d")


def _shift_months(yyyymm: str, delta: int) -> str:
    """``202405`` -1 → ``202404``；跨年正确处理。"""
    year, month = int(yyyymm[:4]), int(yyyymm[4:6])
    total = year * 12 + (month - 1) + delta
    new_year, new_month = divmod(total, 12)
    return f"{new_year:04d}{new_month + 1:02d}"
