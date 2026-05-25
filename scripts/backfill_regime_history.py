#!/usr/bin/env python3
"""历史宏观周期阶段回填.

把 :func:`kss.macro.regime.classify_history` 跑在 ``storage/macro/macro_daily.parquet``
全量历史上，落地 ``storage/macro/regime_daily.parquet``，供下游 sector commentary
/ combo_scan 消费.

实际数据组装 + 拉取逻辑在 :mod:`kss.macro.pipeline`（plan 010 #44），本脚本
只剩 CLI 解析 + 入口编排.

用法::

    python3 scripts/backfill_regime_history.py                  # 用现有 storage，不拉新
    python3 scripts/backfill_regime_history.py --refetch        # 重新拉 PMI/VAI/margin/hsgt
    python3 scripts/backfill_regime_history.py --since 20200101 # 限定回填窗口
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from kss.config.paths import (  # noqa: E402
    DAILY_PARQUET,
    HSGT_PARQUET,
    MONTHLY_PARQUET,
    REGIME_PARQUET,
)
from kss.data.macro_client import MacroClient  # noqa: E402
from kss.macro.pipeline import (  # noqa: E402
    atomic_to_parquet,
    build_indicator_panel,
    ensure_hsgt,
    ensure_margin,
    ensure_pmi_vai,
)
from kss.macro.regime import classify_history, load_config  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--since", type=str, default=None, help="回填起始日 YYYYMMDD")
    p.add_argument("--end", type=str, default=None, help="回填截止日 YYYYMMDD")
    p.add_argument(
        "--refetch", action="store_true",
        help="重新拉取 PMI/VAI/margin/hsgt 覆盖已有 storage（默认只用本地缓存）",
    )
    return p.parse_args()


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
    atomic_to_parquet(regime_df, REGIME_PARQUET)

    stage_counts = regime_df["stage"].value_counts().to_dict()
    logger.info("regime_daily.parquet 写入 %d 行，stage 分布: %s",
                len(regime_df), stage_counts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
