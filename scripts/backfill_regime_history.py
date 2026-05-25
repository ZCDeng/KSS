#!/usr/bin/env python3
"""历史宏观周期阶段回填.

把 :func:`kss.macro.regime.classify_history` 跑在 ``storage/macro/macro_daily.parquet``
全量历史上，落地 ``storage/macro/regime_daily.parquet``，供下游 sector commentary
/ combo_scan 消费.

流程:

1. 增量拉 PMI / VAI / margin / 北向（覆盖落地的 macro_daily 时间区间）
2. 组装日频指标 panel：e_trend (月→日 ffill) + r_trend + liquidity + yc_slope + yc_slope_change
3. 调 ``classify_history`` 跑 expanding-window 分位数 + 滞后平滑
4. 输出 ``storage/macro/regime_daily.parquet``

用法::

    python3 scripts/backfill_regime_history.py                  # 用现有 storage，不拉新
    python3 scripts/backfill_regime_history.py --refetch        # 重新拉 PMI/VAI/margin/hsgt
    python3 scripts/backfill_regime_history.py --since 20200101 # 限定回填窗口
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from kss.data.macro_client import MacroClient  # noqa: E402
from kss.macro.derived import (  # noqa: E402
    compute_e_trend,
    compute_liquidity_index,
    yc_slope_change,
    yield_curve_slope,
)
from kss.macro.regime import classify_history, load_config  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _atomic_to_parquet(df: pd.DataFrame, path: "Path") -> None:
    """Write parquet via .tmp + os.replace to prevent torn reads by concurrent readers.

    All macro parquet writes go through this helper so ``scan_combo_signals._lookup_*``
    never sees a half-written file (which would silently fall back to None per the
    swallow-exception pattern).
    """
    import os
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    df.to_parquet(tmp, index=False)
    os.replace(tmp, path)


STORAGE_ROOT = PROJECT_ROOT / "storage" / "macro"
DAILY_PARQUET = STORAGE_ROOT / "macro_daily.parquet"
MONTHLY_PARQUET = STORAGE_ROOT / "macro_monthly.parquet"
PMI_PARQUET = STORAGE_ROOT / "pmi_monthly.parquet"
VAI_PARQUET = STORAGE_ROOT / "vai_monthly.parquet"
MARGIN_PARQUET = STORAGE_ROOT / "margin_daily.parquet"
HSGT_PARQUET = STORAGE_ROOT / "hsgt_daily.parquet"
REGIME_PARQUET = STORAGE_ROOT / "regime_daily.parquet"

# PMI 在 cn_pmi 返回中的列名（制造业总指数）
# Tushare 返回大写列：PMI010000 = 制造业 PMI 总指数；MONTH = 月份
_PMI_COL_CANDIDATES = ("PMI010000", "pmi010000", "pmi", "pmi_mfg")
_PMI_MONTH_CANDIDATES = ("MONTH", "month")
# 工业增加值同比候选列（Tushare 实际接口名待确认；缺失时 e_trend 退化为 PMI-only）
_VAI_COL_CANDIDATES = ("vai_yoy", "vai_accu_yoy", "value")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--since", type=str, default=None, help="回填起始日 YYYYMMDD")
    p.add_argument("--end", type=str, default=None, help="回填截止日 YYYYMMDD")
    p.add_argument(
        "--refetch", action="store_true",
        help="重新拉取 PMI/VAI/margin/hsgt 覆盖已有 storage（默认只用本地缓存）",
    )
    return p.parse_args()


# ---------------------------------------------------------------------- #
# 数据拉取（增量）
# ---------------------------------------------------------------------- #


def ensure_pmi_vai(client: MacroClient, start_m: str, end_m: str, refetch: bool) -> tuple[pd.DataFrame | None, pd.DataFrame | None]:
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


def ensure_margin(client: MacroClient, start: str, end: str, refetch: bool) -> pd.DataFrame | None:
    """日频两融拉取 + 按 trade_date 聚合（沪深合计）.

    无论 refetch 与否，**都与已有缓存合并**——避免每日刷新窗口（30 天）覆盖
    多年历史导致 ``compute_liquidity_index`` 的 ``pct_change(252)`` yoy 全 NaN.
    """
    # 非 refetch + 缓存够新 → 直接返回
    if MARGIN_PARQUET.exists() and not refetch:
        df = pd.read_parquet(MARGIN_PARQUET)
        if not df.empty and "trade_date" in df.columns and df["trade_date"].max() >= end:
            return df

    raw = client.fetch_margin(start, end)
    if raw is None or raw.empty:
        logger.warning("margin 拉取无数据")
        # 拉取失败但缓存存在 → 返回旧缓存（fail-safe）
        if MARGIN_PARQUET.exists():
            cached = pd.read_parquet(MARGIN_PARQUET)
            return cached if not cached.empty else None
        return None
    raw["trade_date"] = raw["trade_date"].astype(str)
    # 不管 refetch，都 merge 老缓存——窗口 fetch 不能覆盖历史
    if MARGIN_PARQUET.exists():
        old = pd.read_parquet(MARGIN_PARQUET)
        if not old.empty:
            raw = pd.concat([old, raw]).drop_duplicates(
                subset=["trade_date", "exchange_id"], keep="last"
            )
    MARGIN_PARQUET.parent.mkdir(parents=True, exist_ok=True)
    _atomic_to_parquet(raw, MARGIN_PARQUET)
    logger.info("margin %s 共 %d 行", MARGIN_PARQUET, len(raw))
    return raw


def ensure_hsgt(client: MacroClient, trade_dates: list[str], refetch: bool) -> pd.DataFrame | None:
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
    _atomic_to_parquet(merged, HSGT_PARQUET)
    return merged


def _load_or_fetch(path: Path, fetcher, key: str, refetch: bool) -> pd.DataFrame | None:
    """简化缓存：本地有 + 非 refetch → 直接读；否则调 fetcher 并落地."""
    if path.exists() and not refetch:
        return pd.read_parquet(path)
    df = fetcher()
    if df is None or df.empty:
        logger.warning("fetch %s 无数据", path.name)
        return None
    _atomic_to_parquet(df, path)
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

    # --- r_trend：用 yld_10y_d20（直接是 P0 已算的 Δr_20d，单位 pp）---
    if "yld_10y_d20" in daily.columns:
        panel["r_trend"] = daily.sort_values("trade_date")["yld_10y_d20"].reset_index(drop=True)
    else:
        panel["r_trend"] = pd.NA

    # --- yc_slope：long(10y) - short(1y) ---
    if {"yld_10y", "yld_1y"}.issubset(daily.columns):
        wide = daily[["trade_date", "yld_10y", "yld_1y"]].copy()
        slope = yield_curve_slope(wide)
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

    # --- e_trend：PMI + VAI（月 → 日 ffill）---
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

    # --- liquidity：monthly M2 yoy + daily hsgt + daily margin ---
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


def _assemble_e_monthly(pmi: pd.DataFrame | None, vai: pd.DataFrame | None) -> pd.DataFrame | None:
    """把 PMI（cn_pmi 多列）+ VAI 合成成 ``compute_e_trend`` 期望的格式.

    Tushare ``cn_pmi`` 返回大写列（MONTH / PMI010000 …），需要先归一化.
    """
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
    """把 Tushare ``moneyflow_hsgt`` 输出 (north_money / south_money) 归一为
    ``trade_date`` + ``net_amount``（北向净流入，单位亿）.

    Tushare API 偶尔会以 str 形式返回数字（特别是早期返回缺失日），落到
    parquet 后整列变 object dtype。统一用 :func:`pd.to_numeric` 强制 coerce
    （非法值 → NaN）后再除法.
    """
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
    """归一为 ``trade_date`` + ``rzye``（融资余额，元）.

    Tushare ``margin`` 返回 SSE/SZSE 分行，这里按 trade_date 求和.
    rzye 同样可能是 object 类型，强制 coerce 防御.
    """
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


# ---------------------------------------------------------------------- #
# Main
# ---------------------------------------------------------------------- #


def main() -> int:
    args = parse_args()

    if not DAILY_PARQUET.exists():
        logger.error("缺少 %s，请先跑 scripts/update_macro_daily.py", DAILY_PARQUET)
        return 1
    if not MONTHLY_PARQUET.exists():
        logger.error("缺少 %s，请先跑 scripts/update_macro_daily.py", MONTHLY_PARQUET)
        return 1

    daily = pd.read_parquet(DAILY_PARQUET)
    monthly = pd.read_parquet(MONTHLY_PARQUET)

    # 窗口裁剪
    if args.since:
        daily = daily[daily["trade_date"] >= args.since]
    if args.end:
        daily = daily[daily["trade_date"] <= args.end]
    if daily.empty:
        logger.error("窗口内无 daily 数据")
        return 1

    start_d, end_d = daily["trade_date"].min(), daily["trade_date"].max()
    start_m, end_m = start_d[:6], end_d[:6]
    logger.info("回填窗口 %s ~ %s (月 %s ~ %s)", start_d, end_d, start_m, end_m)

    client = MacroClient()
    pmi, vai = ensure_pmi_vai(client, start_m, end_m, args.refetch)
    margin = ensure_margin(client, start_d, end_d, args.refetch)
    trade_dates = sorted(daily["trade_date"].astype(str).unique().tolist())
    hsgt = ensure_hsgt(client, trade_dates, args.refetch) if args.refetch else (
        pd.read_parquet(HSGT_PARQUET) if HSGT_PARQUET.exists() else None
    )

    panel = build_indicator_panel(daily, monthly, pmi, vai, margin, hsgt)
    coverage = panel[[c for c in panel.columns if c != "trade_date"]].notna().mean()
    logger.info("指标覆盖率: %s", coverage.to_dict())

    cfg = load_config()
    regime_df = classify_history(panel, cfg)
    _atomic_to_parquet(regime_df, REGIME_PARQUET)

    stage_counts = regime_df["stage"].value_counts().to_dict()
    logger.info("regime_daily.parquet 写入 %d 行，stage 分布: %s",
                len(regime_df), stage_counts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
