"""美股对标取数 (yFinance) + 缓存 + 优雅降级.

紫苏叶理论核心论点之一: A 股卡脖子公司「市值 < 行业龙头 1/10」. 要量化这个 gap,
需要对标龙头(多为美股上市的全球竞争者)的估值. 本模块给一个 ticker 返回
``{pe, market_cap, price, currency, status}``, 任何失败都降级为 status 标记而非抛出.

设计约束(R3/R4/R6):
- 带 kss.db 缓存（perilla_enrich_cache 表，kind="us_peer"，ts_code 借用存 ticker；
  U15 割接自 storage/us_peer_cache/<ticker>.json——该目录在生产中从未真正落过
  地，因为唯一实际调用方 aggregate.enrich() 一直显式传 cache_dir 覆盖此模块自带
  默认值，实际写入位置一直是 storage/perilla_cache/），默认 1 天有效，避免重复打 Yahoo。
- yFinance 是非官方 Yahoo API、无 key、需外网; 断网/未知 ticker → status="unavailable".
- ticker 为 None(该股无干净美股对标) → status="no_peer", 完全不触网.
"""

from __future__ import annotations

import json
import logging
from datetime import date as _date
from pathlib import Path
from typing import Any

from kss.storage.perilla_cache import read_cache_entry, write_cache_entry

logger = logging.getLogger(__name__)

_KIND = "us_peer"


def _fetch_live(ticker: str) -> dict[str, Any]:
    """实时拉 yFinance. 价格/市值走 fast_info(快), PE 走 .info(慢、易缺).

    分离成独立函数便于测试 monkeypatch. 失败向上抛, 由 fetch_us_peer 兜底降级.
    """
    import yfinance as yf

    t = yf.Ticker(ticker)
    fi = t.fast_info

    def _fi(key: str) -> Any:
        # fast_info 在不同版本下是 dict-like 或属性对象, 两种都兜.
        if hasattr(fi, "get"):
            return fi.get(key)
        return getattr(fi, key, None)

    price = _fi("lastPrice") or _fi("last_price")
    market_cap = _fi("marketCap") or _fi("market_cap")
    currency = _fi("currency") or "USD"

    pe = None
    try:
        info = t.info  # 慢且偶发抛错, 单独包: 缺 PE 不影响 price/mcap
        if isinstance(info, dict):
            pe = info.get("trailingPE")
    except Exception as exc:  # noqa: BLE001
        logger.debug("us_peer %s .info 取 PE 失败: %s", ticker, exc)

    return {
        "pe": _num(pe),
        "market_cap": _num(market_cap),
        "price": _num(price),
        "currency": currency,
    }


def _num(v: Any) -> float | None:
    try:
        if v is None:
            return None
        f = float(v)
        return f if f == f else None  # NaN → None
    except (TypeError, ValueError):
        return None


def fetch_us_peer(
    ticker: str | None,
    *,
    max_age_days: int = 1,
    db_path: Path | None = None,
    today: _date | None = None,
    cache_only: bool = False,
) -> dict[str, Any]:
    """取美股对标估值, 带缓存 + 降级.

    Args:
        ticker: 美股代码(如 ``LRCX``); ``None`` 表示该股无干净对标.
        max_age_days: 缓存有效天数; 超过则重新拉.
        db_path: kss.db 路径; ``None`` → 直取不缓存(测试用).
        today: 覆盖"今天"(测试用).
        cache_only: 只读缓存、绝不触网; 未命中 → unavailable(reason=cache_miss).

    Returns:
        dict, 必含 ``status`` ∈ {``ok``, ``no_peer``, ``unavailable``}.
        ``ok`` 时含 ``ticker/pe/market_cap/price/currency/as_of``.
    """
    if not ticker:
        return {"status": "no_peer"}

    ticker = ticker.upper()
    today = today or _date.today()

    # 1) 缓存命中且未过期 → 直接用, 不触网.
    cached = _read_cache(ticker, db_path) if db_path is not None else None
    if cached is not None:
        age = _age_days(cached.get("as_of"), today)
        if age is not None and age <= max_age_days:
            return cached

    if cache_only:
        return {"status": "unavailable", "reason": "cache_miss"}

    # 2) 实时拉; 失败 → 降级(若有旧缓存则回退旧缓存 + stale 标记).
    try:
        live = _fetch_live(ticker)
    except Exception as exc:  # noqa: BLE001
        logger.warning("us_peer %s 取数失败: %s", ticker, exc)
        if cached is not None:
            return {**cached, "status": "ok", "stale": True}
        return {"status": "unavailable", "reason": f"{type(exc).__name__}: {exc}"}

    result = {
        "status": "ok",
        "ticker": ticker,
        "as_of": today.strftime("%Y-%m-%d"),
        **live,
    }
    if db_path is not None:
        _write_cache(ticker, result, db_path)
    return result


def fetch_usdcny(
    *,
    max_age_days: int = 1,
    db_path: Path | None = None,
    today: _date | None = None,
    cache_only: bool = False,
) -> float | None:
    """USD→CNY 汇率(yFinance ``CNY=X``)，用于 A 股/美股市值倍数换算.

    复用 us_peer 的缓存与降级路径；不可达时返回 None（市值倍数随之降级，
    但 PE 对比无需汇率不受影响）。
    """
    r = fetch_us_peer("CNY=X", max_age_days=max_age_days, db_path=db_path,
                      today=today, cache_only=cache_only)
    if r.get("status") == "ok":
        return _num(r.get("price"))
    return None


def _read_cache(ticker: str, db_path: Path) -> dict[str, Any] | None:
    entry = read_cache_entry(ticker, _KIND, db_path)
    if entry is None:
        return None
    payload, _cached_at = entry
    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        logger.debug("us_peer 缓存读失败 %s: %s", ticker, exc)
        return None


def _write_cache(ticker: str, data: dict[str, Any], db_path: Path) -> None:
    try:
        write_cache_entry(ticker, _KIND, json.dumps(data, ensure_ascii=False), data.get("as_of"), db_path)
    except OSError as exc:
        logger.debug("us_peer 缓存写失败 %s: %s", ticker, exc)


def _age_days(as_of: str | None, today: _date) -> int | None:
    if not as_of:
        return None
    try:
        y, m, d = (int(x) for x in as_of.split("-"))
        return (today - _date(y, m, d)).days
    except (ValueError, TypeError):
        return None
