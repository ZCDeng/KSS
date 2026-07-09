#!/usr/bin/env python3
"""刷新总览第一行市场速览：A500ETF(563360/159361) 当日行情 + 北向资金净流入。

- ETF 行情走 Tushare ``fund_daily``（OHLC + pct_chg）。
- 北向资金读本地 ``storage/macro/hsgt_daily.parquet`` 最新一行 ``north_money``（万元）。
- 隔夜美股：Tushare ``index_global``（IXIC/DJI/XIN9）+ yfinance（美股/ETF）。

产出 ``storage/macro/market_strip.json``，由 kss_app_bridge 离线读取（仅标准库）。
用法： python scripts/refresh_market_strip.py
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
_KSS_STATE = Path(__import__("os").environ.get("KSS_STATE_ROOT") or ROOT)  # U1: bundle-mode 写入重定向
OUT = _KSS_STATE / "storage" / "macro" / "market_strip.json"
HSGT = _KSS_STATE / "storage" / "macro" / "hsgt_daily.parquet"

logger = logging.getLogger(__name__)

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

    overnight_us = _fetch_overnight_us(pro, _fetch_with_retry)

    payload = {
        "date": etf_date or north_date,
        "etfDate": etf_date,
        "northDate": north_date,
        "northMoney": north_money,      # 万元（前端 /10000 → 亿）
        "etfs": etfs,
        "indices": indices,
        "indexBoard": index_board,
        "overnightUS": overnight_us,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(f"✅ 写入 {OUT.name}: etfs={len(etfs)} indices={len(indices)} board={len(index_board)} overnightUS={len(overnight_us)}")


def _fetch_overnight_us(pro, fetch_with_retry) -> list[dict]:
    """固定名单隔夜美股：index_global + yfinance；失败单标跳过。"""
    import sys
    sys.path.insert(0, str(ROOT))
    from scripts.overnight_us_universe import OVERNIGHT_US_UNIVERSE, merge_overnight_quotes

    fetched: list[dict] = []
    for row in OVERNIGHT_US_UNIVERSE:
        code, name, kind = row["code"], row["name"], row["kind"]
        try:
            if kind == "index_global":
                item = _quote_index_global(pro, fetch_with_retry, code, name)
            else:
                item = _quote_yfinance(code, name)
            if item:
                fetched.append(item)
        except Exception as exc:  # noqa: BLE001
            logger.warning("overnightUS %s failed: %s", code, exc)
    return merge_overnight_quotes(fetched)


def _quote_index_global(pro, fetch_with_retry, code: str, name: str) -> dict | None:
    df = fetch_with_retry(
        lambda: pro.index_global(ts_code=code, start_date="20260101", end_date="20261231"),
        f"index_global {code}",
    )
    if df is None or df.empty:
        return None
    df = df.sort_values("trade_date")
    r = df.iloc[-1]
    return {
        "code": code,
        "name": name,
        "close": round(float(r["close"]), 2),
        "pct": round(float(r["pct_chg"]), 2),
        "date": str(r["trade_date"]),
        "source": "index_global",
    }


def _quote_yfinance(code: str, name: str) -> dict | None:
    """用 yfinance 日 K 末两日算 close/pct（项目已依赖 yfinance）。"""
    try:
        import yfinance as yf
    except ImportError:
        logger.warning("yfinance not installed; skip %s", code)
        return None
    t = yf.Ticker(code)
    hist = t.history(period="10d", auto_adjust=False)
    if hist is None or hist.empty or "Close" not in hist.columns:
        return None
    closes = hist["Close"].dropna()
    if len(closes) < 1:
        return None
    last = float(closes.iloc[-1])
    prev = float(closes.iloc[-2]) if len(closes) >= 2 else None
    if prev is None or prev == 0:
        pct = 0.0
    else:
        pct = (last - prev) / prev * 100.0
    # index may be tz-aware Timestamp
    d = closes.index[-1]
    date_s = d.strftime("%Y%m%d") if hasattr(d, "strftime") else str(d)[:10].replace("-", "")
    return {
        "code": code,
        "name": name,
        "close": round(last, 2),
        "pct": round(pct, 2),
        "date": date_s,
        "source": "yfinance",
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    main()
