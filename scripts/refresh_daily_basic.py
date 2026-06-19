#!/usr/bin/env python3
"""刷新每日基本面切片（流通市值 / PE / PB / 换手率）。

紫苏叶选股表需要真实「流通市值」（cs_data 只带 total_mv）。本脚本对最新交易日
调用 Tushare ``daily_basic`` 单日切片（一次返回全市场），裁剪关键列写入
``storage/macro/dailybasic_latest.parquet``，供 kss_app_bridge 离线读取。

用法： python scripts/refresh_daily_basic.py [YYYYMMDD]
不带日期时自动取 cs_data 里的最新交易日。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT_PATH = ROOT / "storage" / "macro" / "dailybasic_latest.parquet"
# 桥接读取用的轻量 JSON（bridge 只依赖标准库，不引入 pandas/parquet）。
JSON_PATH = ROOT / "storage" / "macro" / "dailybasic_latest.json"


def _load_env() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip())


def _latest_trade_date() -> str:
    """从任一 cs_data 文件末行取最新交易日 → YYYYMMDD。"""
    for code in ("688065", "688114", "688008"):
        fp = ROOT / f"cs_data_{code}.csv"
        if fp.exists():
            last = fp.read_text(encoding="utf-8", errors="ignore").strip().splitlines()[-1]
            raw = last.split(",")[1]
            return raw.replace("-", "")
    raise SystemExit("找不到 cs_data 推断最新交易日")


def main() -> None:
    _load_env()
    trade_date = sys.argv[1] if len(sys.argv) > 1 else _latest_trade_date()

    sys.path.insert(0, str(ROOT))
    from kss.data.tushare_client import TushareClient, _fetch_with_retry

    client = TushareClient()
    pro = client.get_pro()
    df = _fetch_with_retry(
        lambda: pro.daily_basic(trade_date=trade_date),
        f"daily_basic {trade_date}",
    )
    if df is None or df.empty:
        raise SystemExit(f"daily_basic({trade_date}) 无数据")

    keep = ["ts_code", "trade_date", "circ_mv", "total_mv", "pe", "pe_ttm", "pb", "turnover_rate"]
    cols = [c for c in keep if c in df.columns]
    out = df[cols].copy()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(OUT_PATH, index=False)

    # 轻量 JSON：{ts_code: {circ_mv, total_mv, pe, pb}}，单位万元（bridge 自行换算）。
    import json
    blob: dict[str, dict] = {"_trade_date": trade_date}
    for _, r in out.iterrows():
        code = str(r["ts_code"])
        blob[code] = {
            k: (None if pd.isna(r.get(k)) else round(float(r.get(k)), 4))
            for k in ("circ_mv", "total_mv", "pe", "pe_ttm", "pb", "turnover_rate")
            if k in out.columns
        }
    JSON_PATH.write_text(json.dumps(blob, ensure_ascii=False), encoding="utf-8")

    print(f"✅ 写入 {OUT_PATH} + {JSON_PATH.name}  ({len(out)} 行, trade_date={trade_date})")
    print(out.head(3).to_string())


if __name__ == "__main__":
    main()
