#!/usr/bin/env python3
"""单独补 macro_daily.parquet 中 yld_* 缺失的交易日.

P0 回填发现 ``yc_cb`` 接口 2000 行硬上限 + chunk_days=3 横跨周末时
会被 Tushare 截断到最后 2 个交易日，导致 ~20% 交易日的国债收益率缺失.
本脚本扫描 parquet，对每个 shibor 有但 yld_10y 没有的交易日，单独发
1 日请求拉 yc_cb，然后 upsert 回 parquet.

用法::

    python3 scripts/backfill_macro_gaps.py             # 补全所有 gap
    python3 scripts/backfill_macro_gaps.py --max 50    # 只补前 50 个（测试）
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from kss.data.macro_client import MacroClient, pivot_yield_curve  # noqa: E402
from kss.macro.derived import compute_rate_changes  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


DAILY_PARQUET = PROJECT_ROOT / "storage" / "macro" / "macro_daily.parquet"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--max", type=int, default=None, help="最多补多少天（测试用）")
    p.add_argument("--sleep", type=float, default=0.3, help="每次 API 调用间隔秒数")
    args = p.parse_args()

    if not DAILY_PARQUET.exists():
        logger.error("daily parquet 不存在: %s", DAILY_PARQUET)
        return 1

    df = pd.read_parquet(DAILY_PARQUET)
    # 缺失 = shibor 有但 yld_10y 没有
    gaps = df[df["shibor_3m"].notna() & df["yld_10y"].isna()]["trade_date"].tolist()
    logger.info("发现 %d 个 yld 缺失日（shibor 有）", len(gaps))

    if args.max:
        gaps = gaps[: args.max]
        logger.info("限制只补前 %d 个", len(gaps))

    client = MacroClient()
    filled_rows: list[dict] = []
    for i, date in enumerate(gaps, 1):
        # 单日请求，不可能 truncate
        long_df = client.fetch_cn_yield_curve(date, date, chunk_days=1, sleep_between=0)
        if long_df is None or long_df.empty:
            logger.warning("[%d/%d] %s 仍无数据（节假日 or 上游缺）", i, len(gaps), date)
            continue
        wide = pivot_yield_curve(long_df)
        if wide is None or wide.empty:
            continue
        for _, r in wide.iterrows():
            filled_rows.append(r.to_dict())
        if i % 50 == 0:
            logger.info("进度 %d/%d", i, len(gaps))
        time.sleep(args.sleep)

    if not filled_rows:
        logger.warning("没有补到任何数据")
        return 0

    fill_df = pd.DataFrame(filled_rows)
    fill_df["trade_date"] = fill_df["trade_date"].astype(str)
    logger.info("补到 %d 条新数据", len(fill_df))

    # Merge into existing parquet：先把 yld_* 列从 fill_df 写入 df 同日行
    yld_cols = [c for c in fill_df.columns if c.startswith("yld_")]
    df = df.set_index("trade_date")
    fill_df = fill_df.set_index("trade_date")
    for col in yld_cols:
        df.loc[fill_df.index, col] = fill_df[col]
    df = df.reset_index()

    # 重算 Δr 列（因为新增了 yld 行可能影响 d5/d20 计算）
    rate_cols = [c for c in df.columns if c.startswith(("shibor_", "yld_")) and not c.endswith(("_d5", "_d20"))]
    df_clean = df[["trade_date"] + rate_cols].sort_values("trade_date").reset_index(drop=True)
    df_with_deltas = compute_rate_changes(df_clean, rate_cols, windows=(5, 20))
    # 把新的 _d5/_d20 覆盖回去
    df = df.drop(columns=[c for c in df.columns if c.endswith(("_d5", "_d20"))], errors="ignore")
    df = df.merge(
        df_with_deltas[["trade_date"] + [c for c in df_with_deltas.columns if c.endswith(("_d5", "_d20"))]],
        on="trade_date",
        how="left",
    )

    df = df.sort_values("trade_date").reset_index(drop=True)
    df.to_parquet(DAILY_PARQUET, index=False)
    logger.info("写回 %s 共 %d 行", DAILY_PARQUET, len(df))
    return 0


if __name__ == "__main__":
    sys.exit(main())
