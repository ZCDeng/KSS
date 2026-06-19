#!/usr/bin/env python3
"""构建 名称/代码 → ts_code 索引，供股票池导入解析（bridge 离线读取）。

覆盖：A 股全市场（stock_basic.parquet，剔除退市）+ 场内 ETF/基金（tushare fund_basic）。
每条带 kind（stock / fund），fetch_stock_data 据此选 daily / fund_daily。
产出 storage/macro/stock_name_index.json：
  { "byName": {名称: ts}, "byCode": {6位: ts}, "pairs": [[名称, ts], ...], "meta": {ts: {name, industry, kind}} }
用法： python scripts/build_name_index.py
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "storage" / "macro" / "stock_basic.parquet"
OUT = ROOT / "storage" / "macro" / "stock_name_index.json"


def _load_env() -> None:
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())


def main() -> None:
    by_name: dict[str, str] = {}
    by_code: dict[str, str] = {}
    pairs: list[list[str]] = []
    meta: dict[str, dict[str, str]] = {}

    def add(ts: str, name: str, industry: str, kind: str, overwrite_code: bool = True) -> None:
        if not ts or "." not in ts:
            return
        code6 = ts.split(".")[0]
        if overwrite_code or code6 not in by_code:   # 指数不覆盖同号个股
            by_code[code6] = ts
        meta[ts] = {"name": name, "industry": industry, "kind": kind}
        if name and name not in by_name:
            by_name[name] = ts
            pairs.append([name, ts])

    # 常见指数（curated；代码与个股 6 位冲突时只进 byName，不覆盖 byCode）
    INDICES = [
        ("上证指数", "000001.SH"), ("深证成指", "399001.SZ"), ("创业板指", "399006.SZ"),
        ("科创50", "000688.SH"), ("科创100", "000698.SH"), ("科创综指", "000680.SH"),
        ("沪深300", "000300.SH"), ("上证50", "000016.SH"), ("中证500", "000905.SH"),
        ("中证1000", "000852.SH"), ("中证A500", "000510.SH"), ("中证2000", "932000.CSI"),
        ("北证50", "899050.BJ"),
    ]

    # 1) A 股
    df = pd.read_parquet(SRC)
    if "delist_date" in df.columns:
        df = df[df["delist_date"].isna() | (df["delist_date"] == "")]
    for _, r in df.iterrows():
        add(str(r["ts_code"]), str(r.get("name") or "").strip(), str(r.get("industry") or "").strip(), "stock")
    n_stock = len(by_code)

    # 2) 场内 ETF / 基金（tushare fund_basic market=E）
    try:
        _load_env()
        import sys
        sys.path.insert(0, str(ROOT))
        from kss.data.tushare_client import TushareClient, _fetch_with_retry
        pro = TushareClient().get_pro()
        fb = _fetch_with_retry(lambda: pro.fund_basic(market="E"), "fund_basic E")
        if fb is not None and not fb.empty:
            for _, r in fb.iterrows():
                add(str(r["ts_code"]), str(r.get("name") or "").strip(), "ETF/基金", "fund")
    except Exception as e:  # noqa: BLE001
        print(f"⚠️ ETF 索引跳过（{e}）")
    n_fund = len(by_code) - n_stock

    # 3) 指数
    for nm, ts in INDICES:
        add(ts, nm, "指数", "index", overwrite_code=False)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"byName": by_name, "byCode": by_code, "pairs": pairs, "meta": meta}, ensure_ascii=False), encoding="utf-8")
    print(f"✅ {OUT.name}: 股票 {n_stock} + ETF {n_fund} + 指数 {len(INDICES)}")


if __name__ == "__main__":
    main()
