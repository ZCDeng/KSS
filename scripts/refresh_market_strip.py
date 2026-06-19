#!/usr/bin/env python3
"""刷新总览第一行市场速览：A500ETF(563360/159361) 当日行情 + 北向资金净流入。

- ETF 行情走 Tushare ``fund_daily``（OHLC + pct_chg）。
- 北向资金读本地 ``storage/macro/hsgt_daily.parquet`` 最新一行 ``north_money``（万元）。

产出 ``storage/macro/market_strip.json``，由 kss_app_bridge 离线读取（仅标准库）。
用法： python scripts/refresh_market_strip.py
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "storage" / "macro" / "market_strip.json"
HSGT = ROOT / "storage" / "macro" / "hsgt_daily.parquet"

ETFS = [
    ("563360.SH", "A500ETF"),
    ("159361.SZ", "A500ETF"),
]

# (ts_code, 名称, 是否全球指数)。上证走 index_daily，纳指/恒生走 index_global。
INDICES = [
    ("000001.SH", "上证指数", False),
    ("IXIC", "纳斯达克", True),
    ("HSI", "恒生指数", True),
]

# 总览底部「指数一览」：13 个常用 A 股指数，均走 index_daily。
INDEX_BOARD = [
    ("000001.SH", "上证指数"), ("399001.SZ", "深证成指"), ("399006.SZ", "创业板指"),
    ("000688.SH", "科创50"), ("000698.SH", "科创100"), ("000680.SH", "科创综指"),
    ("000300.SH", "沪深300"), ("000016.SH", "上证50"), ("000905.SH", "中证500"),
    ("000852.SH", "中证1000"), ("000510.SH", "中证A500"), ("932000.CSI", "中证2000"),
    ("899050.BJ", "北证50"),
]


def _load_env() -> None:
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())


def main() -> None:
    _load_env()
    import sys
    sys.path.insert(0, str(ROOT))
    from kss.data.tushare_client import TushareClient, _fetch_with_retry

    pro = TushareClient().get_pro()

    etfs: list[dict] = []
    etf_date = ""
    for code, name in ETFS:
        df = _fetch_with_retry(
            lambda: pro.fund_daily(ts_code=code, start_date="20260101", end_date="20261231"),
            f"fund_daily {code}",
        )
        if df is None or df.empty:
            continue
        df = df.sort_values("trade_date")
        r = df.iloc[-1]
        etf_date = max(etf_date, str(r["trade_date"]))
        etfs.append({
            "code": code,
            "name": name,
            "close": round(float(r["close"]), 3),
            "pct": round(float(r["pct_chg"]), 2),
        })

    indices: list[dict] = []
    for code, name, is_global in INDICES:
        if is_global:
            df = _fetch_with_retry(
                lambda: pro.index_global(ts_code=code, start_date="20260101", end_date="20261231"),
                f"index_global {code}",
            )
        else:
            df = _fetch_with_retry(
                lambda: pro.index_daily(ts_code=code, start_date="20260101", end_date="20261231"),
                f"index_daily {code}",
            )
        if df is None or df.empty:
            continue
        df = df.sort_values("trade_date")
        r = df.iloc[-1]
        indices.append({
            "code": code,
            "name": name,
            "close": round(float(r["close"]), 2),
            "pct": round(float(r["pct_chg"]), 2),
            "date": str(r["trade_date"]),
        })

    index_board: list[dict] = []
    for ts_code, name in INDEX_BOARD:
        df = _fetch_with_retry(
            lambda: pro.index_daily(ts_code=ts_code, start_date="20260101", end_date="20261231"),
            f"index_daily {ts_code}",
        )
        if df is None or df.empty:
            continue
        df = df.sort_values("trade_date")
        r = df.iloc[-1]
        index_board.append({
            "code": ts_code,
            "name": name,
            "close": round(float(r["close"]), 2),
            "pct": round(float(r["pct_chg"]), 2),
            "date": str(r["trade_date"]),
        })

    north_money = None
    north_date = ""
    if HSGT.exists():
        hs = pd.read_parquet(HSGT).sort_values("trade_date")
        if not hs.empty:
            last = hs.iloc[-1]
            north_money = round(float(last["north_money"]), 2)   # 万元
            north_date = str(last["trade_date"])

    payload = {
        "date": etf_date or north_date,
        "etfDate": etf_date,
        "northDate": north_date,
        "northMoney": north_money,      # 万元（前端 /10000 → 亿）
        "etfs": etfs,
        "indices": indices,
        "indexBoard": index_board,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(f"✅ 写入 {OUT.name}: {payload}")


if __name__ == "__main__":
    main()
