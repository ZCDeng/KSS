#!/usr/bin/env python3
"""增量刷新北证 50 日线缓存到最新交易日。

读取最新 bj50 扫描表里的成分股，对每只股票从本地缓存
``storage/bj_cache/<code>_daily.csv`` 的最后一个交易日起，向 Tushare
``pro.daily`` 拉到今天，去重排序后回写（保持与 scan_bj50 相同的列与 ISO 日期格式）。

桌面 App 的「刷新北证日线」任务调用本脚本。需要可联网环境 + TUSHARE_TOKEN。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
_KSS_STATE = Path(__import__("os").environ.get("KSS_STATE_ROOT") or PROJECT_ROOT)  # U1: bundle-mode 写入重定向
sys.path.insert(0, str(PROJECT_ROOT))

from kss.data.tushare_client import TushareClient, _fetch_with_retry  # noqa: E402

CACHE_DIR = _KSS_STATE / "storage" / "bj_cache"
SCAN_DIR = _KSS_STATE / "storage" / "reports" / "bj50_scan"


def _pool_symbols() -> list[str]:
    scans = sorted(SCAN_DIR.glob("scan_*.csv"))
    if scans:
        df = pd.read_csv(scans[-1])
        return [s for s in df["ts_code"].tolist() if isinstance(s, str) and s]
    return [p.name.replace("_daily.csv", "") for p in CACHE_DIR.glob("*_daily.csv")]


def main() -> int:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    today = pd.Timestamp.now().normalize().strftime("%Y%m%d")
    pro = TushareClient().get_pro()
    symbols = _pool_symbols()
    if not symbols:
        print("没有北证成分股可刷新（缺 scan 表与缓存）", file=sys.stderr)
        return 1

    updated = 0
    total_new = 0
    failed: list[str] = []
    for symbol in symbols:
        cache = CACHE_DIR / f"{symbol}_daily.csv"
        start = "20230101"
        old: pd.DataFrame | None = None
        if cache.exists():
            old = pd.read_csv(cache, parse_dates=["trade_date"])
            if not old.empty:
                start = old["trade_date"].max().strftime("%Y%m%d")

        df = _fetch_with_retry(
            lambda s=symbol, st=start: pro.daily(ts_code=s, start_date=st, end_date=today),
            f"daily {symbol} ({start}~{today})",
        )
        if df is None or df.empty:
            if old is None:
                failed.append(symbol)
            continue

        df["trade_date"] = pd.to_datetime(df["trade_date"])
        if old is not None:
            df = pd.concat([old, df], ignore_index=True)
        df = df.drop_duplicates(subset=["trade_date"]).sort_values("trade_date").reset_index(drop=True)
        new_rows = len(df) - (0 if old is None else len(old))
        df.to_csv(cache, index=False)
        if new_rows > 0:
            updated += 1
            total_new += new_rows

    print(f"刷新北证日线完成: {updated}/{len(symbols)} 只有新增, 共 {total_new} 行, 截止 {today}")
    if failed:
        print(f"无数据/失败 {len(failed)} 只: {', '.join(failed[:10])}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
