"""共享 macro pipeline 组件 (plan 010 / #44).

把以下原本散落在 ``scripts/backfill_regime_history.py`` 的业务逻辑提到 kss 包
内，避免 ``scripts/update_macro_daily.py`` 通过 ``from scripts.backfill_regime_history
import ...`` 做跨脚本 import。两边脚本现在共同从这里 import.

包含:

- :func:`atomic_to_parquet` — 原子 parquet 写（.tmp + os.replace），防 torn read
- :func:`ensure_pmi_vai` / :func:`ensure_margin` / :func:`ensure_hsgt` — 增量缓存 fetch
- :func:`build_indicator_panel` — 把日/月/PMI/VAI/margin/hsgt 拼成 regime 5 列指标 panel

数据契约（cache file 路径）由 :mod:`kss.config.paths` 单点定义；不在此重复.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Callable

import pandas as pd

from kss.config.paths import (
    HSGT_PARQUET,
    MARGIN_PARQUET,
    PMI_PARQUET,
    VAI_PARQUET,
)
from kss.data.macro_client import MacroClient
from kss.macro.derived import (
    compute_e_trend,
    compute_liquidity_index,
    yc_slope_change,
    yield_curve_slope,
)

logger = logging.getLogger(__name__)


# PMI/VAI 的 Tushare 列名候选（大写 / 小写兼容）
_PMI_COL_CANDIDATES: tuple[str, ...] = ("PMI010000", "pmi010000", "pmi", "pmi_mfg")
_PMI_MONTH_CANDIDATES: tuple[str, ...] = ("MONTH", "month")
_VAI_COL_CANDIDATES: tuple[str, ...] = ("vai_yoy", "vai_accu_yoy", "value")


# ---------------------------------------------------------------------- #
# 原子写
# ---------------------------------------------------------------------- #


def atomic_to_parquet(df: pd.DataFrame, path: Path) -> None:
    """Write parquet via .tmp + os.replace to prevent torn reads by concurrent readers.

    所有 macro parquet 写都应走这个 helper，确保 ``scan_combo_signals._lookup_*``
    永远不会读到半文件（半文件会触发 swallow-exception 路径，silent 降级到 None）.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    df.to_parquet(tmp, index=False)
    os.replace(tmp, path)


# ---------------------------------------------------------------------- #
# 数据拉取（增量）
# ---------------------------------------------------------------------- #


def ensure_pmi_vai(
    client: MacroClient, start_m: str, end_m: str, refetch: bool,
) -> tuple[pd.DataFrame | None, pd.DataFrame | None]:
    """拉 PMI + VAI 月频，缓存到 :data:`kss.config.paths.PMI_PARQUET` / ``VAI_PARQUET``."""
    pmi = _load_or_fetch(
        PMI_PARQUET,
        lambda: client.fetch_cn_pmi(start_m, end_m),
        key="month",
        refetch=refetch,
    )
    vai = _load_or_fetch(
        VAI_PARQUET,
        lambda: client.fetch_cn_vai(start_m, end_m),
        key="month",
        refetch=refetch,
    )
    return pmi, vai


def ensure_margin(
    client: MacroClient, start: str, end: str, refetch: bool,
) -> pd.DataFrame | None:
    """日频两融拉取 + 按 trade_date 聚合（沪深合计）.

    无论 refetch 与否，**都与已有缓存合并**——避免每日刷新窗口（30 天）覆盖
    多年历史导致 :func:`compute_liquidity_index` 的 ``pct_change(252)`` yoy 全 NaN.
    """
    if MARGIN_PARQUET.exists() and not refetch:
        df = pd.read_parquet(MARGIN_PARQUET)
        if not df.empty and "trade_date" in df.columns and df["trade_date"].max() >= end:
            return df

    raw = client.fetch_margin(start, end)
    if raw is None or raw.empty:
        logger.warning("margin 拉取无数据")
        if MARGIN_PARQUET.exists():
            cached = pd.read_parquet(MARGIN_PARQUET)
            return cached if not cached.empty else None
        return None
    raw["trade_date"] = raw["trade_date"].astype(str)
    if MARGIN_PARQUET.exists():
        old = pd.read_parquet(MARGIN_PARQUET)
        if not old.empty:
            raw = pd.concat([old, raw]).drop_duplicates(
                subset=["trade_date", "exchange_id"], keep="last"
            )
    atomic_to_parquet(raw, MARGIN_PARQUET)
    logger.info("margin %s 共 %d 行", MARGIN_PARQUET, len(raw))
    return raw


def ensure_hsgt(
    client: MacroClient, trade_dates: list[str], refetch: bool,
) -> pd.DataFrame | None:
    """北向资金按日拉取 + 增量落地（每日一次 API 调用）.

    Tushare ``moneyflow_hsgt`` 单次只能查单日，所以这里做循环 + 限速.
    """
    existing: pd.DataFrame | None = None
    if HSGT_PARQUET.exists() and not refetch:
        existing = pd.read_parquet(HSGT_PARQUET)
        existing["trade_date"] = existing["trade_date"].astype(str)
        have = set(existing["trade_date"])
        trade_dates = [d for d in trade_dates if d not in have]

    if not trade_dates:
        return existing

    logger.info("hsgt 待拉取 %d 个交易日", len(trade_dates))
    rows: list[pd.DataFrame] = [existing] if existing is not None else []
    for i, d in enumerate(trade_dates):
        df = client.fetch_moneyflow_hsgt(d)
        if df is not None and not df.empty:
            rows.append(df)
        if (i + 1) % 50 == 0:
            logger.info("hsgt 进度 %d/%d", i + 1, len(trade_dates))
        time.sleep(0.15)

    if not rows:
        return None
    merged = pd.concat(rows, ignore_index=True).drop_duplicates(
        subset=["trade_date"], keep="last"
    )
    atomic_to_parquet(merged, HSGT_PARQUET)
    return merged


def _load_or_fetch(
    path: Path,
    fetcher: Callable[[], pd.DataFrame | None],
    key: str,
    refetch: bool,
) -> pd.DataFrame | None:
    """简化缓存：本地有 + 非 refetch → 直接读；否则调 fetcher 并落地."""
    if path.exists() and not refetch:
        return pd.read_parquet(path)
    df = fetcher()
    if df is None or df.empty:
        logger.warning("fetch %s 无数据", path.name)
        return None
    atomic_to_parquet(df, path)
    logger.info("落地 %s (%d 行)", path, len(df))
    return df


# ---------------------------------------------------------------------- #
# 指标 panel 组装
# ---------------------------------------------------------------------- #


def build_indicator_panel(
    daily: pd.DataFrame,
    monthly: pd.DataFrame,
    pmi: pd.DataFrame | None,
    vai: pd.DataFrame | None,
    margin: pd.DataFrame | None,
    hsgt: pd.DataFrame | None,
) -> pd.DataFrame:
    """组装 regime 分类器需要的 5 列日频指标 panel.

    Returns:
        含 ``trade_date`` + ``e_trend`` / ``r_trend`` / ``liquidity`` /
        ``yc_slope`` / ``yc_slope_change`` 的 DataFrame.
    """
    panel = daily[["trade_date"]].copy()
    panel["trade_date"] = panel["trade_date"].astype(str)
    panel = panel.sort_values("trade_date").reset_index(drop=True)

    # r_trend：P0 已算的 yld_10y_d20（百分点）
    if "yld_10y_d20" in daily.columns:
        panel["r_trend"] = daily.sort_values("trade_date")["yld_10y_d20"].reset_index(drop=True)
    else:
        panel["r_trend"] = pd.NA

    # yc_slope：长端 - 短端
    if {"yld_10y", "yld_1y"}.issubset(daily.columns):
        wide = daily[["trade_date", "yld_10y", "yld_1y"]].copy()
        slope = yield_curve_slope(wide)
        if slope is not None:
            panel = panel.merge(
                slope.reset_index().rename(columns={"yc_slope": "yc_slope"}),
                on="trade_date", how="left",
            )
        change = yc_slope_change(wide)
        if change is not None:
            panel = panel.merge(
                change.reset_index().rename(columns={"yc_slope_d20": "yc_slope_change"}),
                on="trade_date", how="left",
            )
    if "yc_slope" not in panel.columns:
        panel["yc_slope"] = pd.NA
    if "yc_slope_change" not in panel.columns:
        panel["yc_slope_change"] = pd.NA

    # e_trend：PMI + VAI（月 → 日 ffill）
    e_monthly = _assemble_e_monthly(pmi, vai)
    if e_monthly is not None:
        e_series = compute_e_trend(e_monthly)
        if e_series is not None:
            e_map = e_series.to_dict()
            panel["e_trend"] = [
                _ffill_month_value(e_map, d[:6]) for d in panel["trade_date"]
            ]
        else:
            panel["e_trend"] = pd.NA
    else:
        panel["e_trend"] = pd.NA

    # liquidity：monthly M2 yoy + daily hsgt + daily margin
    hsgt_panel = _normalize_hsgt(hsgt)
    margin_panel = _normalize_margin(margin)
    liq = compute_liquidity_index(
        monthly_panel=monthly,
        hsgt_panel=hsgt_panel,
        margin_panel=margin_panel,
    )
    if liq is not None:
        liq_map = liq.to_dict()
        panel["liquidity"] = [liq_map.get(str(d)) for d in panel["trade_date"]]
    else:
        panel["liquidity"] = pd.NA

    return panel


# ---------------------------------------------------------------------- #
# 内部 helpers
# ---------------------------------------------------------------------- #


def _assemble_e_monthly(
    pmi: pd.DataFrame | None, vai: pd.DataFrame | None,
) -> pd.DataFrame | None:
    """PMI（cn_pmi 大写列）+ VAI → ``compute_e_trend`` 期望格式."""
    parts: list[pd.DataFrame] = []
    if pmi is not None and not pmi.empty:
        col = _first_present_col(pmi, _PMI_COL_CANDIDATES)
        m_col = _first_present_col(pmi, _PMI_MONTH_CANDIDATES)
        if col and m_col:
            parts.append(
                pmi[[m_col, col]].rename(columns={m_col: "month", col: "pmi_mfg"})
            )
    if vai is not None and not vai.empty:
        col = _first_present_col(vai, _VAI_COL_CANDIDATES)
        m_col = _first_present_col(vai, _PMI_MONTH_CANDIDATES)
        if col and m_col:
            parts.append(
                vai[[m_col, col]].rename(columns={m_col: "month", col: "vai_yoy"})
            )
    if not parts:
        return None
    out = parts[0]
    for nxt in parts[1:]:
        out = out.merge(nxt, on="month", how="outer")
    out["month"] = out["month"].astype(str)
    return out.sort_values("month").reset_index(drop=True)


def _normalize_hsgt(hsgt: pd.DataFrame | None) -> pd.DataFrame | None:
    """北向资金归一化（dtype coerce + 单位换算 万元 → 亿）."""
    if hsgt is None or hsgt.empty:
        return None
    df = hsgt.copy()
    df["trade_date"] = df["trade_date"].astype(str)
    if "north_money" in df.columns:
        df["net_amount"] = pd.to_numeric(df["north_money"], errors="coerce") / 100.0
    elif "hgt" in df.columns and "sgt" in df.columns:
        hgt = pd.to_numeric(df["hgt"], errors="coerce").fillna(0)
        sgt = pd.to_numeric(df["sgt"], errors="coerce").fillna(0)
        df["net_amount"] = (hgt + sgt) / 100.0
    else:
        return None
    return df[["trade_date", "net_amount"]]


def _normalize_margin(margin: pd.DataFrame | None) -> pd.DataFrame | None:
    """两融余额归一化（dtype coerce）."""
    if margin is None or margin.empty:
        return None
    df = margin.copy()
    df["trade_date"] = df["trade_date"].astype(str)
    if "rzye" not in df.columns:
        return None
    df["rzye"] = pd.to_numeric(df["rzye"], errors="coerce")
    return df[["trade_date", "rzye"]].copy()


def _first_present_col(df: pd.DataFrame, candidates: tuple[str, ...]) -> str | None:
    for c in candidates:
        if c in df.columns:
            return c
    return None


def _ffill_month_value(month_map: dict, ym: str):
    """月 dict 映射的 ffill：从 ym 往前找最近有值的月份."""
    if ym in month_map:
        return month_map[ym]
    keys = sorted(k for k in month_map if k <= ym)
    if not keys:
        return None
    return month_map[keys[-1]]


__all__ = [
    "atomic_to_parquet",
    "build_indicator_panel",
    "ensure_hsgt",
    "ensure_margin",
    "ensure_pmi_vai",
]
