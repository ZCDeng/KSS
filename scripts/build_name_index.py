#!/usr/bin/env python3
"""构建 名称/代码 → ts_code 索引，供股票池导入解析（bridge 离线读取）。

数据源 storage/macro/stock_basic.parquet（全市场 ~5800 只）。剔除已退市。
产出 storage/macro/stock_name_index.json：
  { "byName": {名称: ts_code}, "byCode": {6位代码: ts_code}, "pairs": [[名称, ts_code], ...] }
用法： python scripts/build_name_index.py
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "storage" / "macro" / "stock_basic.parquet"
OUT = ROOT / "storage" / "macro" / "stock_name_index.json"


def main() -> None:
    df = pd.read_parquet(SRC)
    if "delist_date" in df.columns:
        df = df[df["delist_date"].isna() | (df["delist_date"] == "")]
    by_name: dict[str, str] = {}
    by_code: dict[str, str] = {}
    pairs: list[list[str]] = []
    meta: dict[str, dict[str, str]] = {}   # ts_code → {name, industry}（供桥接补名称）
    for _, r in df.iterrows():
        ts = str(r["ts_code"])
        name = str(r.get("name") or "").strip()
        industry = str(r.get("industry") or "").strip()
        if not ts or "." not in ts:
            continue
        code6 = ts.split(".")[0]
        by_code[code6] = ts
        meta[ts] = {"name": name, "industry": industry}
        if name and name not in by_name:
            by_name[name] = ts
            pairs.append([name, ts])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"byName": by_name, "byCode": by_code, "pairs": pairs, "meta": meta}, ensure_ascii=False), encoding="utf-8")
    print(f"✅ {OUT.name}: {len(by_name)} 名称 / {len(by_code)} 代码")


if __name__ == "__main__":
    main()
