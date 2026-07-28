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
    index_stacks = _fetch_index_stacks(pro, _fetch_with_retry)
    # 兼容：indices = 每列当前顶层（第一项），供旧 UI；新 UI 用 indexStacks
    stack_tops = []
    for col in index_stacks:
        items = col.get("items") or []
        if items:
            stack_tops.append({
                "code": items[0]["code"],
                "name": items[0]["name"],
                "close": items[0]["close"],
                "pct": items[0]["pct"],
                "date": items[0].get("date", ""),
            })
    if stack_tops:
        indices = stack_tops

    limit_board = _fetch_limit_board(pro, _fetch_with_retry)

    payload = {
        "date": etf_date or north_date,
        "etfDate": etf_date,
        "northDate": north_date,
        "northMoney": north_money,      # 万元（前端 /10000 → 亿）
        "etfs": etfs,
        "indices": indices,
        "indexBoard": index_board,
        "overnightUS": overnight_us,
        "indexStacks": index_stacks,
        "limitBoard": limit_board,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    n_stack = sum(len(c.get("items") or []) for c in index_stacks)
    lb_note = ""
    if limit_board and limit_board.get("maxBoard") is not None:
        lb_note = f" limitBoard.max={limit_board.get('maxBoard')}"
    print(
        f"✅ 写入 {OUT.name}: etfs={len(etfs)} indices={len(indices)} board={len(index_board)} "
        f"overnightUS={len(overnight_us)} indexStacks={n_stack}{lb_note}"
    )


def _user_overnight_append() -> list[dict]:
    """读 surface 用户追加；失败则空列表。"""
    try:
        from kss.ui_surface.config import load_config

        cfg = load_config()
        return list((cfg.get("overnight_us") or {}).get("append") or [])
    except Exception as exc:  # noqa: BLE001
        logger.warning("load ui_surface overnight append failed: %s", exc)
        return []


def _fetch_overnight_us(pro, fetch_with_retry) -> list[dict]:
    """默认名单 + 用户 append：index_global + yfinance；失败单标跳过。"""
    import sys
    sys.path.insert(0, str(ROOT))
    from scripts.overnight_us_universe import OVERNIGHT_US_UNIVERSE, merge_overnight_quotes

    append = _user_overnight_append()
    universe: list[dict] = [
        {"code": r["code"], "name": r["name"], "kind": r["kind"]}
        for r in OVERNIGHT_US_UNIVERSE
    ]
    seen = {r["code"].upper() for r in universe}
    for item in append:
        code = str(item.get("code") or "").upper()
        if not code or code in seen:
            continue
        seen.add(code)
        universe.append({
            "code": code,
            "name": str(item.get("name") or code),
            "kind": str(item.get("kind") or "yfinance"),
        })

    fetched: list[dict] = []
    for row in universe:
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
    return merge_overnight_quotes(fetched, universe=universe)


def _fetch_limit_board(pro, fetch_with_retry) -> dict | None:
    """涨停情绪：最高连板 + 封板率（limit_list_d）。失败返回 None。"""
    from datetime import datetime, timedelta

    today = datetime.now().date()
    for i in range(0, 10):
        d = today - timedelta(days=i)
        if d.weekday() >= 5:
            continue
        trade_date = d.strftime("%Y%m%d")
        df = fetch_with_retry(
            lambda td=trade_date: pro.limit_list_d(trade_date=td),
            f"limit_list_d {trade_date}",
        )
        if df is None or getattr(df, "empty", True):
            continue
        try:
            from kss.ui_surface.limit_board import aggregate_limit_board

            board = aggregate_limit_board(df)
            if board:
                board["asOf"] = trade_date
                return board
        except Exception as exc:  # noqa: BLE001
            logger.warning("limit_board aggregate failed: %s", exc)
            return None
    return None


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


def _fetch_index_stacks(pro, fetch_with_retry) -> list[dict]:
    """三列堆叠：日线价 + 尽力 1m sparkline。

    产品码（UI/Longbridge）用 ``code``；拉数可用 ``fetch_code``（如 HSTECH→HKTECH）。
    主源失败时按 kind 做短后备链，避免整层被静默丢弃。
    """
    import sys
    sys.path.insert(0, str(ROOT))
    from scripts.index_stack_universe import INDEX_STACKS, YF_TICKERS

    out_cols: list[dict] = []
    for col in INDEX_STACKS:
        items: list[dict] = []
        for it in col["items"]:
            code, name, kind = it["code"], it["name"], it["kind"]
            fetch_code = it.get("fetch_code") or code
            quote = None
            try:
                if kind == "index_daily":
                    quote = _quote_index_daily(pro, fetch_with_retry, fetch_code, name)
                elif kind == "index_global":
                    quote = _quote_index_global(pro, fetch_with_retry, fetch_code, name)
                    if not quote:
                        # 后备：yfinance（恒生科技曾误绑 ETF；仍优于整层缺失）
                        yf_code = YF_TICKERS.get(code, fetch_code)
                        quote = _quote_yfinance(yf_code, name)
                        if quote:
                            quote["source"] = f"yfinance:{yf_code}"
                    if not quote:
                        quote = _quote_hstech_sina(name) if code == "HSTECH" else None
                else:
                    yf_code = YF_TICKERS.get(code, fetch_code)
                    quote = _quote_yfinance(yf_code, name)
            except Exception as exc:  # noqa: BLE001
                logger.warning("indexStack quote %s (fetch=%s): %s", code, fetch_code, exc)
            if not quote:
                logger.warning("indexStack skip %s (%s) — 无可用价源", code, name)
                continue
            quote["code"] = code  # 始终产品码（不泄漏 HKTECH 到 UI）
            quote["name"] = name
            spark = _sparkline_1m(code, kind)
            quote["sparkline"] = spark
            items.append(quote)
        out_cols.append({"id": col["id"], "items": items})
    return out_cols


def _quote_hstech_sina(name: str) -> dict | None:
    """akshare 新浪港股指数日线（HSTECH 真指数后备）。"""
    try:
        import akshare as ak
    except ImportError:
        return None
    try:
        df = ak.stock_hk_index_daily_sina(symbol="HSTECH")
    except Exception as exc:  # noqa: BLE001
        logger.warning("sina HSTECH: %s", exc)
        return None
    if df is None or df.empty:
        return None
    # 列：date open high ... close
    df = df.sort_values("date")
    r = df.iloc[-1]
    close = float(r["close"])
    prev = float(df.iloc[-2]["close"]) if len(df) >= 2 else None
    pct = 0.0 if not prev else (close - prev) / prev * 100.0
    date_s = str(r["date"]).replace("-", "")[:8]
    return {
        "code": "HSTECH",
        "name": name,
        "close": round(close, 2),
        "pct": round(pct, 2),
        "date": date_s,
        "source": "akshare_sina",
    }


def _quote_index_daily(pro, fetch_with_retry, code: str, name: str) -> dict | None:
    df = fetch_with_retry(
        lambda: pro.index_daily(ts_code=code, start_date="20260101", end_date="20261231"),
        f"index_daily {code}",
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
        "source": "index_daily",
    }


def _sparkline_1m(code: str, kind: str) -> list[dict]:
    """当日 1 分钟收盘序列；失败返回 []。降采样 ≤120 点。"""
    pts: list[float] = []
    if kind == "index_daily" and "." in code:
        bare = code.split(".")[0]
        pts = _ak_index_min_closes(bare)
    elif kind in ("index_global", "yfinance"):
        yf_code = code if kind == "yfinance" else code
        from scripts.index_stack_universe import YF_TICKERS
        yf_code = YF_TICKERS.get(code, yf_code if kind == "yfinance" else f"^{code}")
        pts = _yf_intraday_closes(yf_code)
    if not pts:
        return []
    # downsample
    if len(pts) > 120:
        step = max(1, len(pts) // 120)
        pts = pts[::step][:120]
    return [{"c": round(float(c), 2)} for c in pts]


def _ak_index_min_closes(symbol_6: str) -> list[float]:
    try:
        import akshare as ak
        # 东财指数分钟：symbol 为 6 位
        end = pd.Timestamp.now()
        start = end.normalize()  # 当日 00:00
        df = ak.index_zh_a_hist_min_em(
            symbol=symbol_6,
            period="1",
            start_date=start.strftime("%Y-%m-%d %H:%M:%S"),
            end_date=end.strftime("%Y-%m-%d %H:%M:%S"),
        )
        if df is None or df.empty:
            return []
        # 列名随版本：收盘 / close
        col = "收盘" if "收盘" in df.columns else ("close" if "close" in df.columns else None)
        if not col:
            return []
        return [float(x) for x in df[col].dropna().tolist()]
    except Exception as exc:  # noqa: BLE001
        logger.debug("ak index min %s: %s", symbol_6, exc)
        return []


def _yf_intraday_closes(ticker: str) -> list[float]:
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        hist = t.history(period="1d", interval="1m", auto_adjust=False)
        if hist is None or hist.empty or "Close" not in hist.columns:
            return []
        return [float(x) for x in hist["Close"].dropna().tolist()]
    except Exception as exc:  # noqa: BLE001
        logger.debug("yf 1m %s: %s", ticker, exc)
        return []


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
