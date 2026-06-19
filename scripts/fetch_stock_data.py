#!/usr/bin/env python3
"""为导入的股票拉取日线数据，生成 cs_data_<code>.csv（新股建档 / 已存在则增量补到最新）。

写入后该股自动出现在桌面端股票池（_load_stock_summaries 按 cs_data_*.csv 聚合）。
列结构与 update_cs_data.py 一致（FactorPipeline 可消费）。

用法：
    python scripts/fetch_stock_data.py --codes 600519.SH,000001.SZ
    python scripts/fetch_stock_data.py --codes 600519.SH --years 2
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_COLS = [
    "ts_code", "trade_date", "open", "high", "low", "close",
    "pre_close", "change", "pct_chg", "vol", "amount",
    "turnover_rate", "volume_ratio", "pe", "pb", "total_mv",
]


def _load_env() -> None:
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())


def fetch_one(ts_code: str, client, start: str, end: str, kind: str = "stock") -> tuple[bool, str]:
    code6 = ts_code.split(".")[0]
    csv_path = ROOT / f"cs_data_{code6}.csv"

    from kss.data.tushare_client import _fetch_with_retry
    if kind == "fund":
        # ETF/场内基金走 fund_daily（无 daily_basic 的 pe/pb/换手）
        pro = client.get_pro()
        daily = _fetch_with_retry(
            lambda: pro.fund_daily(ts_code=ts_code, start_date=start, end_date=end),
            f"fund_daily {ts_code}",
        )
        daily_basic = None
    elif kind == "index":
        # 指数走 index_daily（仅 OHLC，无估值/换手）
        pro = client.get_pro()
        daily = _fetch_with_retry(
            lambda: pro.index_daily(ts_code=ts_code, start_date=start, end_date=end),
            f"index_daily {ts_code}",
        )
        daily_basic = None
    else:
        daily = client.fetch_daily(ts_code, start, end)
        daily_basic = client.fetch_daily_basic(ts_code, start, end)
    if daily is None or daily.empty:
        return False, f"{ts_code} 无日线数据"

    daily["trade_date"] = pd.to_datetime(daily["trade_date"], format="%Y%m%d")
    if daily_basic is not None and not daily_basic.empty:
        daily_basic["trade_date"] = pd.to_datetime(daily_basic["trade_date"], format="%Y%m%d")
        merged = daily.merge(
            daily_basic[["trade_date", "turnover_rate", "volume_ratio", "pe", "pb", "total_mv"]],
            on="trade_date", how="left",
        )
    else:
        merged = daily.copy()
    for col in EXPECTED_COLS:
        if col not in merged.columns:
            merged[col] = float("nan")
    merged = merged[EXPECTED_COLS]

    # 已存在则合并去重
    if csv_path.exists():
        existing = pd.read_csv(csv_path)
        existing["trade_date"] = pd.to_datetime(existing["trade_date"])
        merged = pd.concat([existing[EXPECTED_COLS], merged], ignore_index=True)
    merged = merged.drop_duplicates(subset=["ts_code", "trade_date"], keep="last")
    merged = merged.sort_values("trade_date").reset_index(drop=True)
    merged["trade_date"] = merged["trade_date"].dt.strftime("%Y-%m-%d")
    merged.to_csv(csv_path, index=False)
    return True, f"{ts_code} → cs_data_{code6}.csv ({len(merged)} 行)"


def main() -> None:
    parser = argparse.ArgumentParser(description="为导入股票拉日线")
    parser.add_argument("--codes", required=True, help="逗号分隔 ts_code，如 600519.SH,000001.SZ")
    parser.add_argument("--years", type=int, default=2, help="回溯年数")
    args = parser.parse_args()

    _load_env()
    sys.path.insert(0, str(ROOT))
    from kss.data.tushare_client import TushareClient

    client = TushareClient()
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=args.years * 365 + 30)).strftime("%Y%m%d")

    # 从名称索引取 kind（stock / fund）以决定用 daily 还是 fund_daily
    import json
    idx_path = ROOT / "storage" / "macro" / "stock_name_index.json"
    meta = {}
    if idx_path.exists():
        try:
            meta = json.loads(idx_path.read_text(encoding="utf-8")).get("meta", {})
        except Exception:
            meta = {}

    codes = [c.strip().upper() for c in args.codes.split(",") if c.strip()]
    ok, fail = [], []
    for ts_code in codes:
        kind = meta.get(ts_code, {}).get("kind", "stock")
        try:
            good, msg = fetch_one(ts_code, client, start, end, kind=kind)
            (ok if good else fail).append(msg)
            print(("✅ " if good else "⚠️ ") + msg)
        except Exception as e:  # noqa: BLE001
            fail.append(f"{ts_code}: {e}")
            print(f"❌ {ts_code}: {e}")

    print(f"\n完成：成功 {len(ok)} / 失败 {len(fail)}")
    if fail and not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
