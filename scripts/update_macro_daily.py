#!/usr/bin/env python3
"""每日宏观分母端数据增量更新.

拉取并落地以下宏观面板（按"分子 E / 分母 r"框架，参见
``docs/plans/2026-05-25-001-feat-macro-denominator-feed-plan.md``）：

- 日频：Shibor 多期限 / 中债国债收益率曲线（宽表）/ 信用利差（AkShare）
- 月频：M0/M1/M2 / CPI / PPI

落地路径：

- ``storage/macro/macro_daily.parquet``  —— 日频长寿命面板（append-or-update）
- ``storage/macro/macro_monthly.parquet`` —— 月频长寿命面板
- ``storage/macro/credit_curve/<YYYYMMDD>.csv`` —— AkShare 信用曲线按日存档

用法::

    python3 scripts/update_macro_daily.py                  # 默认拉最近 30 个交易日
    python3 scripts/update_macro_daily.py --since 20240101 # 从指定日补
    python3 scripts/update_macro_daily.py --skip-credit    # 跳过 AkShare 调用

部署：每个交易日 8:35 cron（在 ``update_data_daily`` 8:30 之后）。
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from kss.config.paths import (  # noqa: E402
    CREDIT_DIR,
    DAILY_PARQUET,
    HS300_PE_PARQUET,
    MONTHLY_PARQUET,
    REGIME_PARQUET,
    STORAGE_ROOT,
    VALUATION_PARQUET,
)
from kss.data.macro_client import MacroClient, pivot_yield_curve  # noqa: E402
from kss.macro.derived import compute_rate_changes  # noqa: E402
from kss.macro.pipeline import (  # noqa: E402
    atomic_to_parquet,
    build_indicator_panel,
    ensure_hsgt,
    ensure_margin,
    ensure_pmi_vai,
)
from kss.macro.regime import classify_history, load_config  # noqa: E402
from kss.macro.valuation import compute_hs300_n_from_panel  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


# Path 常量来自 kss.config.paths（plan 010 #44 单点定义）

# HS300 估值默认参数 —— compute_time_premium 的 g/ERP
HS300_DEFAULT_GROWTH = 0.06       # 6% 长期盈利增长（保守估计）
HS300_DEFAULT_ERP = 0.055         # 5.5% 股权风险溢价
HS300_INDEX_CODE = "000300.SH"

# Shibor 列名标准化（原列名 ``on/1w/1m/3m`` 不能作为 parquet 列名前缀混用）
SHIBOR_COL_MAP: dict[str, str] = {
    "on": "shibor_on",
    "1w": "shibor_1w",
    "2w": "shibor_2w",
    "1m": "shibor_1m",
    "3m": "shibor_3m",
    "6m": "shibor_6m",
    "9m": "shibor_9m",
    "1y": "shibor_1y",
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument(
        "--since",
        type=str,
        default=None,
        help="起始日期 YYYYMMDD；默认取已落地最新日的次日，无落地则取今天-30 天",
    )
    p.add_argument(
        "--end",
        type=str,
        default=None,
        help="终止日期 YYYYMMDD；默认今天",
    )
    p.add_argument(
        "--skip-credit",
        action="store_true",
        help="跳过 AkShare 信用曲线（用于离线/Tushare-only 环境）",
    )
    p.add_argument(
        "--skip-regime",
        action="store_true",
        help="跳过宏观阶段重算（默认每日刷新 regime_daily.parquet）",
    )
    p.add_argument(
        "--skip-valuation",
        action="store_true",
        help="跳过 HS300 估值 n 计算（默认每日刷新 valuation_n_daily.parquet）",
    )
    return p.parse_args()


def _ymd(d: datetime) -> str:
    return d.strftime("%Y%m%d")


def _today() -> str:
    return _ymd(datetime.now())


def _default_since() -> str:
    if DAILY_PARQUET.exists():
        df = pd.read_parquet(DAILY_PARQUET)
        if not df.empty and "trade_date" in df.columns:
            last = str(df["trade_date"].max())
            return _ymd(datetime.strptime(last, "%Y%m%d") + timedelta(days=1))
    return _ymd(datetime.now() - timedelta(days=30))


def assemble_daily_panel(client: MacroClient, start: str, end: str) -> pd.DataFrame | None:
    """拉 shibor + yc_cb 拼成日频宽表."""
    shibor = client.fetch_shibor(start, end)
    yc_long = client.fetch_cn_yield_curve(start, end)
    yc_wide = pivot_yield_curve(yc_long) if yc_long is not None else None

    parts: list[pd.DataFrame] = []
    if shibor is not None:
        shibor = shibor.rename(columns={**SHIBOR_COL_MAP, "date": "trade_date"})
        shibor["trade_date"] = shibor["trade_date"].astype(str)
        parts.append(shibor)
    if yc_wide is not None:
        yc_wide["trade_date"] = yc_wide["trade_date"].astype(str)
        parts.append(yc_wide)

    if not parts:
        return None

    merged = parts[0]
    for nxt in parts[1:]:
        merged = merged.merge(nxt, on="trade_date", how="outer")

    rate_cols = [c for c in merged.columns if c.startswith(("shibor_", "yld_"))]
    merged = compute_rate_changes(merged, rate_cols, windows=(5, 20))
    return merged.sort_values("trade_date").reset_index(drop=True)


def assemble_monthly_panel(
    client: MacroClient, start_m: str, end_m: str
) -> pd.DataFrame | None:
    """拉 cn_m + cn_cpi + cn_ppi 拼成月频宽表（按 month 列 outer join）."""
    m = client.fetch_cn_money_supply(start_m, end_m)
    cpi = client.fetch_cn_cpi(start_m, end_m)
    ppi = client.fetch_cn_ppi(start_m, end_m)

    parts: list[pd.DataFrame] = []
    if m is not None:
        cols = ["month"] + [c for c in m.columns if c.startswith(("m0", "m1", "m2"))]
        parts.append(m[cols].copy())
    if cpi is not None:
        cpi_slim = cpi[["month", "nt_val", "nt_yoy", "nt_mom"]].rename(
            columns={"nt_val": "cpi_val", "nt_yoy": "cpi_yoy", "nt_mom": "cpi_mom"}
        )
        parts.append(cpi_slim)
    if ppi is not None:
        ppi_slim = ppi[["month", "ppi_yoy"]].rename(columns={"ppi_yoy": "ppi_yoy"})
        parts.append(ppi_slim)

    if not parts:
        return None

    merged = parts[0]
    for nxt in parts[1:]:
        merged = merged.merge(nxt, on="month", how="outer")
    return merged.sort_values("month").reset_index(drop=True)


def upsert_parquet(path: Path, new_df: pd.DataFrame, key: str) -> int:
    """按 ``key`` 列追加 / 覆盖落地 parquet，返回最终行数.

    走 ``atomic_to_parquet`` (kss.macro.pipeline) 防 torn read.
    """
    if path.exists():
        old = pd.read_parquet(path)
        merged = pd.concat([old[~old[key].isin(new_df[key])], new_df], ignore_index=True)
    else:
        merged = new_df
    merged = merged.sort_values(key).reset_index(drop=True)
    atomic_to_parquet(merged, path)
    return len(merged)


def save_credit_curve(client: MacroClient, trade_date: str) -> bool:
    """AkShare 信用曲线按日落地 CSV（一日一文件）."""
    CREDIT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = CREDIT_DIR / f"{trade_date}.csv"
    if out_path.exists():
        logger.info("信用曲线 %s 已存在，跳过", trade_date)
        return True
    df = client.fetch_credit_yield_curve_akshare()
    if df is None:
        return False
    df.to_csv(out_path, index=False, encoding="utf-8-sig")
    logger.info("信用曲线落地 %s (%d 行)", out_path, len(df))
    return True


def main() -> int:
    args = parse_args()
    start = args.since or _default_since()
    end = args.end or _today()

    logger.info("宏观数据增量更新窗口: %s ~ %s", start, end)

    client = MacroClient()

    # 日频
    daily = assemble_daily_panel(client, start, end)
    if daily is None or daily.empty:
        logger.warning("日频面板无数据返回")
    else:
        total = upsert_parquet(DAILY_PARQUET, daily, key="trade_date")
        logger.info("日频面板 %s 共 %d 行（新增 %d 行）", DAILY_PARQUET, total, len(daily))

    # 月频：默认以 trade_date 所在月为终点回溯 6 个月；backfill 模式下（since 显式给定且
    # 距 end 超过 6 个月）按完整 since~end 范围拉，避免只填到末月前 6 个月。
    end_m = end[:6]
    default_start_m = _shift_month(end_m, -6)
    explicit_start_m = (args.since or "")[:6] if args.since else ""
    if explicit_start_m and explicit_start_m < default_start_m:
        start_m = explicit_start_m
    else:
        start_m = default_start_m
    monthly = assemble_monthly_panel(client, start_m, end_m)
    if monthly is None or monthly.empty:
        logger.warning("月频面板无数据返回")
    else:
        total = upsert_parquet(MONTHLY_PARQUET, monthly, key="month")
        logger.info(
            "月频面板 %s 共 %d 行（窗口新增/覆盖 %d 行）",
            MONTHLY_PARQUET,
            total,
            len(monthly),
        )

    # 信用曲线（AkShare 单日 snapshot，按当前 end 日落地）
    if not args.skip_credit:
        ok = save_credit_curve(client, end)
        if not ok:
            logger.warning("信用曲线拉取失败，跳过")

    # 宏观阶段：刷新 PMI / margin / hsgt + 重算 regime_daily.parquet
    # Fail-loud: 单步失败让 exit code 非 0，cron 能在 launchd 日志显式报错.
    exit_code = 0
    if not args.skip_regime:
        try:
            refresh_regime(client, start, end)
        except Exception as exc:    # noqa: BLE001
            logger.error("regime 刷新失败: %s", exc, exc_info=True)
            exit_code = 2

    # 估值 n：拉 HS300 PE + 用本地 yld_10y 算 n + 落地
    if not args.skip_valuation:
        try:
            refresh_valuation(client, start, end)
        except Exception as exc:    # noqa: BLE001
            logger.error("valuation 刷新失败: %s", exc, exc_info=True)
            exit_code = 2

    return exit_code


def refresh_valuation(client: MacroClient, start: str, end: str) -> None:
    """拉 HS300 ``index_dailybasic`` 增量 + 用 macro_daily 的 yld_10y 算 n，落地 parquet."""
    pe_df = client.fetch_index_dailybasic(HS300_INDEX_CODE, start, end)
    if pe_df is None or pe_df.empty:
        logger.warning("HS300 index_dailybasic 拉取无数据，跳过 valuation")
        return
    pe_df["trade_date"] = pe_df["trade_date"].astype(str)
    upsert_parquet(HS300_PE_PARQUET, pe_df, key="trade_date")

    # 跨窗口算 n 用全量缓存，这样新增日有历史回填一并算出
    pe_full = pd.read_parquet(HS300_PE_PARQUET)
    rates_full = pd.read_parquet(DAILY_PARQUET)
    n_df = compute_hs300_n_from_panel(
        pe_full, rates_full,
        growth_rate=HS300_DEFAULT_GROWTH,
        equity_premium=HS300_DEFAULT_ERP,
    )
    if n_df.empty:
        logger.warning("valuation 合成结果为空（PE/yld 无交集？）")
        return
    atomic_to_parquet(n_df, VALUATION_PARQUET)
    latest = n_df.tail(1).iloc[0]
    n_val = latest["n_years"]
    n_str = f"{n_val:.2f}" if n_val is not None and n_val == n_val else "N/A"
    logger.info(
        "valuation_n_daily.parquet 刷新完成：当日 %s PE=%.2f n=%s",
        latest["trade_date"], float(latest["pe"]), n_str,
    )


def refresh_regime(client: MacroClient, start: str, end: str) -> None:
    """每日宏观阶段刷新：增量拉 PMI/margin/hsgt → 重算 regime_daily.parquet.

    全部数据组装逻辑在 :mod:`kss.macro.pipeline`（plan 010 #44）.

    - PMI 月数据：覆盖性 fetch（接口快，60 行）
    - margin：覆盖窗口内的日期（增量 append）
    - hsgt：仅拉缺失日（单日 API + 限速）
    """
    # Fix #4: 缺基础 parquet 时显式跳过而非抛 FileNotFoundError 被 except 吞掉.
    if not DAILY_PARQUET.exists() or not MONTHLY_PARQUET.exists():
        logger.warning(
            "regime skip: 缺少 %s 或 %s（先跑 update_macro_daily 主流程）",
            DAILY_PARQUET, MONTHLY_PARQUET,
        )
        return

    start_m, end_m = start[:6], end[:6]
    pmi, vai = ensure_pmi_vai(client, start_m, end_m, refetch=True)
    margin = ensure_margin(client, start, end, refetch=True)

    daily = pd.read_parquet(DAILY_PARQUET)
    monthly = pd.read_parquet(MONTHLY_PARQUET)
    trade_dates = sorted(daily["trade_date"].astype(str).unique().tolist())
    hsgt = ensure_hsgt(client, trade_dates, refetch=False)

    panel = build_indicator_panel(daily, monthly, pmi, vai, margin, hsgt)
    cfg = load_config()
    regime_df = classify_history(panel, cfg)
    atomic_to_parquet(regime_df, REGIME_PARQUET)
    latest = regime_df.tail(1).iloc[0] if not regime_df.empty else None
    if latest is not None:
        logger.info(
            "regime_daily.parquet 刷新完成：当日 %s stage=%s (置信度 %.2f)",
            latest["trade_date"], latest["stage"], latest["confidence"],
        )


def _shift_month(yyyymm: str, delta: int) -> str:
    y, m = int(yyyymm[:4]), int(yyyymm[4:6])
    total = y * 12 + (m - 1) + delta
    new_y, new_m = divmod(total, 12)
    return f"{new_y:04d}{new_m + 1:02d}"


if __name__ == "__main__":
    sys.exit(main())
