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

    def add(ts: str, name: str, industry: str, kind: str) -> None:
        if not ts or "." not in ts:
            return
        by_code[ts.split(".")[0]] = ts
        meta[ts] = {"name": name, "industry": industry, "kind": kind}
        if name and name not in by_name:
            by_name[name] = ts
            pairs.append([name, ts])

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

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"byName": by_name, "byCode": by_code, "pairs": pairs, "meta": meta}, ensure_ascii=False), encoding="utf-8")
    print(f"✅ {OUT.name}: {len(by_code)} 代码（股票 {n_stock} + ETF {len(by_code) - n_stock}）")


if __name__ == "__main__":
    main()
