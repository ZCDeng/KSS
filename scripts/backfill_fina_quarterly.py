#!/usr/bin/env python3
"""全市场财务季频回填 —— P4 risk_filters 的数据底座.

拉取 ``stock_basic`` + 逐股 ``fina_indicator``，落地到：

- ``storage/macro/stock_basic.parquet`` —— 全市场代码 / name / list_date / delist_date
- ``storage/macro/fina_quarterly.parquet`` —— 全市场季频财务指标
  （含 ``debt_to_assets`` / ``n_income_attr_p`` / ``bps``）

``kss/strategies/risk_filters.py`` 的高杠杆 + 连亏 + 负净资产检查依赖这两个文件.

用法::

    # 首次全量回填（默认从 2018 至今）：
    python3 scripts/backfill_fina_quarterly.py

    # 增量（只补缓存里没有的股票）：
    python3 scripts/backfill_fina_quarterly.py --resume

    # 限定日期 + 调试限速：
    python3 scripts/backfill_fina_quarterly.py --since 20220101 --limit 50 --sleep 0.5

注意:

- Tushare ``fina_indicator`` 单股查询，5000 只 * 0.3s ≈ 25 分钟
- API 配额 fina_indicator 至少需要 2000 积分；不足时调用会被 503
- 用 ``--resume`` 避免重复抓取；脚本按 ts_code 在 cache 里出现即跳过
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from kss.data.tushare_client import TushareClient  # noqa: E402
from kss.strategies.risk_filters import (  # noqa: E402
    load_fina_cache,
    load_stock_basic_cache,
    save_fina_cache,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


STORAGE_ROOT = PROJECT_ROOT / "storage" / "macro"
STOCK_BASIC_PARQUET = STORAGE_ROOT / "stock_basic.parquet"
FINA_PARQUET = STORAGE_ROOT / "fina_quarterly.parquet"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--since", type=str, default="20180101",
                   help="财务公告起始日（默认 20180101）")
    p.add_argument("--end", type=str, default=None,
                   help="财务公告截止日（默认今天）")
    p.add_argument("--resume", action="store_true",
                   help="只拉缓存里没有的股票（增量模式）")
    p.add_argument("--limit", type=int, default=None,
                   help="调试用：只处理前 N 只股")
    p.add_argument("--sleep", type=float, default=0.3,
                   help="股票间睡眠秒数，避免触发频率限制")
    p.add_argument("--exchange", type=str, default="",
                   help="仅拉单交易所 SSE / SZSE，默认全市场")
    return p.parse_args()


def fetch_or_load_stock_basic(client: TushareClient, exchange: str, refresh: bool) -> pd.DataFrame:
    """优先读 stock_basic 缓存；refresh=True 时强制重拉."""
    if not refresh:
        cached = load_stock_basic_cache(STOCK_BASIC_PARQUET)
        if cached is not None and not cached.empty:
            logger.info("stock_basic 缓存命中 %d 只", len(cached))
            return cached

    df = client.fetch_stock_basic(exchange=exchange, list_status="L")
    if df is None or df.empty:
        raise RuntimeError("stock_basic 拉取失败 / 返回空")

    # 同时拉退市清单合并（让 ST 过滤器看得到 delist_date）
    delisted = client.fetch_stock_basic(exchange=exchange, list_status="D")
    if delisted is not None and not delisted.empty:
        df = pd.concat([df, delisted], ignore_index=True).drop_duplicates(
            subset=["ts_code"], keep="last"
        )

    STOCK_BASIC_PARQUET.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(STOCK_BASIC_PARQUET, index=False)
    logger.info("stock_basic 落地 %s (%d 只)", STOCK_BASIC_PARQUET, len(df))
    return df


def backfill_fina(
    client: TushareClient,
    ts_codes: list[str],
    start: str,
    end: str,
    sleep: float,
    existing: set[str],
) -> int:
    """逐股拉财务指标 + 增量落地；返回新增股票数."""
    new_count = 0
    batch: list[pd.DataFrame] = []
    BATCH_FLUSH = 50

    for i, code in enumerate(ts_codes):
        if code in existing:
            continue
        df = client.fetch_fina_indicator(code, start=start, end=end)
        if df is not None and not df.empty:
            batch.append(df)
            new_count += 1
        if sleep > 0:
            time.sleep(sleep)

        if len(batch) >= BATCH_FLUSH:
            merged = pd.concat(batch, ignore_index=True)
            save_fina_cache(merged, FINA_PARQUET)
            logger.info("flush batch: 写入 %d 行 (累计新增 %d 股)", len(merged), new_count)
            batch = []

        if (i + 1) % 100 == 0:
            logger.info("进度 %d/%d (新增 %d)", i + 1, len(ts_codes), new_count)

    if batch:
        merged = pd.concat(batch, ignore_index=True)
        save_fina_cache(merged, FINA_PARQUET)
        logger.info("final flush: 写入 %d 行", len(merged))

    return new_count


def main() -> int:
    args = parse_args()
    end = args.end or datetime.now().strftime("%Y%m%d")

    client = TushareClient()
    basic = fetch_or_load_stock_basic(client, args.exchange, refresh=not args.resume)
    ts_codes = sorted(basic["ts_code"].dropna().astype(str).unique().tolist())
    if args.limit:
        ts_codes = ts_codes[: args.limit]

    existing: set[str] = set()
    if args.resume:
        cached = load_fina_cache(FINA_PARQUET)
        if cached is not None and not cached.empty:
            existing = set(cached["ts_code"].dropna().astype(str).unique())
            logger.info("fina 缓存已有 %d 只股，--resume 跳过", len(existing))

    logger.info("待处理 %d 只股，窗口 %s ~ %s", len(ts_codes) - len(existing), args.since, end)
    n_new = backfill_fina(client, ts_codes, args.since, end, args.sleep, existing)
    logger.info("回填完成，新增 %d 只股；总缓存 %s", n_new, FINA_PARQUET)
    return 0


if __name__ == "__main__":
    sys.exit(main())
