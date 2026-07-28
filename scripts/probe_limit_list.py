#!/usr/bin/env python3
"""U0 探针：Tushare limit_list_d 是否对本账号可用（连板 metric 前置门）.

用法::

    .venv/bin/python scripts/probe_limit_list.py
    .venv/bin/python scripts/probe_limit_list.py --trade-date 20260724

成功：stdout 打印行数 + 关键字段 + max limit_times，exit 0。
失败：stderr 说明，exit 1。
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _candidate_dates(explicit: str | None) -> list[str]:
    if explicit:
        return [explicit.replace("-", "")]
    today = datetime.now().date()
    out: list[str] = []
    for i in range(0, 12):
        d = today - timedelta(days=i)
        if d.weekday() >= 5:
            continue
        out.append(d.strftime("%Y%m%d"))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trade-date", default=None, help="YYYYMMDD")
    args = parser.parse_args()

    from kss.data.tushare_client import TushareClient

    pro = TushareClient().get_pro()
    last_err: str | None = None
    for trade_date in _candidate_dates(args.trade_date):
        try:
            df = pro.limit_list_d(trade_date=trade_date)
        except Exception as exc:  # noqa: BLE001
            last_err = f"{trade_date}: {type(exc).__name__}: {exc}"
            continue
        if df is None or df.empty:
            last_err = f"{trade_date}: empty"
            continue
        cols = list(df.columns)
        need = {"limit_times", "limit"}
        missing = need - set(cols)
        if missing:
            print(
                f"FAIL {trade_date}: rows={len(df)} missing cols={sorted(missing)} have={cols}",
                file=sys.stderr,
            )
            return 1
        max_board = float(df["limit_times"].max())
        n_up = int((df["limit"] == "U").sum()) if "limit" in df.columns else -1
        print(
            f"OK trade_date={trade_date} rows={len(df)} "
            f"cols_ok=limit_times,limit max_limit_times={max_board:g} up_rows={n_up}"
        )
        return 0

    print(f"FAIL limit_list_d unavailable: {last_err}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
