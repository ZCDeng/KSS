"""紫苏叶个股富化聚合器：单票 → 三块只读富化 dict.

``enrich(symbol)`` 组装三块，**每块独立 try/except 降级**（单源失败不污染其他块）：
1. ``institutional`` — 前十大流通股东环比 + 北向趋势（Tushare top10_floatholders）。
2. ``valuation_pe`` — PE_TTM 现值 + 历史分位（Tushare daily_basic 历史）。
3. ``us_peer`` — 美股对标 PE/市值 + A 股 vs 对标的 PE 对比 + 市值倍数（yFinance）。

仅对紫苏叶列表（``tier ∈ {core, main}``）的票富化；其他 symbol → ``not_in_perilla_list``。
金融数字全代码确定性计算，无 LLM。
"""

from __future__ import annotations

import io
import logging
from datetime import date as _date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from kss.config.paths import KSS_DB
from kss.perilla_enrich import holdings as _h
from kss.perilla_enrich import us_peer as _us
from kss.perilla_enrich import valuation as _v
from kss.storage.perilla_cache import read_cache_entry, write_cache_entry
from kss.supply_chain.registry import ChainRegistry

logger = logging.getLogger(__name__)

DB_PATH: Path = KSS_DB
_HOLDER_LOOKBACK_DAYS = 720   # 前十大流通股东回看 ~2 年(覆盖多季)
_PE_LOOKBACK_DAYS = 730       # PE 历史回看 ~2 年


def enrich(
    symbol: str,
    *,
    registry: ChainRegistry | None = None,
    client: Any | None = None,
    db_path: Path | None = None,
    today: _date | None = None,
    cache_only: bool = False,
) -> dict[str, Any]:
    """单票富化。返回每块带 ``status`` 的结构化 dict；任意单源失败不抛。

    ``cache_only=True``：只读本地缓存、绝不触网（dashboard 表热路径用，缓存未命中
    则对应块降级 unavailable）。
    """
    reg = registry or ChainRegistry.from_yaml()
    info = reg.get(symbol)
    if info is None or reg.tier(symbol) not in ("core", "main"):
        return {"symbol": symbol, "status": "not_in_perilla_list"}

    today = today or _date.today()
    end = today.strftime("%Y%m%d")

    result: dict[str, Any] = {
        "symbol": info.ts_code,
        "name": info.name,
        "tier": reg.tier(symbol),
        "status": "ok",
    }

    # 懒构造 client（仅在需要 A 股取数时）
    def _client() -> Any:
        nonlocal client
        if client is None:
            from kss.data.tushare_client import TushareClient
            client = TushareClient()
        return client

    a_share_total_mv_wan: float | None = None

    # ── 块1: 机构持仓动态(前十大流通股东 + 北向) ──
    try:
        start = (today - timedelta(days=_HOLDER_LOOKBACK_DAYS)).strftime("%Y%m%d")
        df = _cached_df("holders", info.ts_code, end,
                        lambda: _client().fetch_top10_floatholders(info.ts_code, start, end),
                        db_path, today, cache_only=cache_only)
        result["institutional"] = {
            "top10": _h.top10_dynamics(df),
            "northbound": _h.northbound_trend(df),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("enrich %s institutional 失败: %s", symbol, exc)
        result["institutional"] = {"status": "unavailable", "reason": str(exc)[:120]}

    # ── 块2: PE 动态 ──
    try:
        start = (today - timedelta(days=_PE_LOOKBACK_DAYS)).strftime("%Y%m%d")
        df = _cached_df("pe", info.ts_code, end,
                        lambda: _client().fetch_daily_basic_history(info.ts_code, start, end),
                        db_path, today, cache_only=cache_only)
        result["valuation_pe"] = _v.pe_dynamics(df)
        if df is not None and not df.empty and "total_mv" in df:
            mv = pd.to_numeric(df.sort_values("trade_date")["total_mv"], errors="coerce").dropna()
            if not mv.empty:
                a_share_total_mv_wan = float(mv.iloc[-1])
    except Exception as exc:  # noqa: BLE001
        logger.warning("enrich %s valuation_pe 失败: %s", symbol, exc)
        result["valuation_pe"] = {"status": "unavailable", "reason": str(exc)[:120]}

    # ── 块3: 美股对标 ──
    try:
        result["us_peer"] = _us_peer_block(info, a_share_total_mv_wan,
                                           result.get("valuation_pe"), db_path, today,
                                           cache_only=cache_only)
    except Exception as exc:  # noqa: BLE001
        logger.warning("enrich %s us_peer 失败: %s", symbol, exc)
        result["us_peer"] = {"status": "unavailable", "reason": str(exc)[:120]}

    return result


def _us_peer_block(
    info: Any,
    a_share_total_mv_wan: float | None,
    valuation_pe: dict[str, Any] | None,
    db_path: Path | None,
    today: _date,
    cache_only: bool = False,
) -> dict[str, Any]:
    """美股对标块：取对标估值 + 算 PE 对比(无需汇率) + 市值倍数(需汇率)."""
    peer = _us.fetch_us_peer(info.us_peer_ticker or None, db_path=db_path,
                             today=today, cache_only=cache_only)
    if peer.get("status") != "ok":
        return peer  # no_peer / unavailable 原样返回

    block: dict[str, Any] = {
        "status": "ok",
        "ticker": peer.get("ticker"),
        "name": info.us_peer_name,
        "peer_pe": peer.get("pe"),
        "peer_market_cap_usd": peer.get("market_cap"),
        "peer_price": peer.get("price"),
    }

    # PE 对比(无汇率): A股 PE / 对标 PE
    a_pe = (valuation_pe or {}).get("pe_ttm") if (valuation_pe or {}).get("status") == "ok" else None
    if a_pe and peer.get("pe"):
        block["pe_ratio_a_over_peer"] = round(a_pe / peer["pe"], 2)

    # 市值倍数(需汇率): 对标市值 / A股市值, >10 → 印证"市值<龙头1/10"
    peer_usd = peer.get("market_cap")
    if a_share_total_mv_wan and peer_usd:
        usdcny = _us.fetch_usdcny(db_path=db_path, today=today, cache_only=cache_only)
        if usdcny:
            a_share_usd = a_share_total_mv_wan * 1.0e4 / usdcny  # 万元→元→USD
            block["a_share_market_cap_usd"] = round(a_share_usd, 0)
            block["peer_to_a_mcap_multiple"] = round(peer_usd / a_share_usd, 1)
        else:
            block["mcap_multiple_status"] = "fx_unavailable"
    return block


def _cached_df(
    kind: str,
    ts_code: str,
    stamp: str,
    fetch_fn: Any,
    db_path: Path | None,
    today: _date,
    max_age_days: int = 1,
    cache_only: bool = False,
) -> pd.DataFrame | None:
    """带 kss.db 缓存的 df 取数。db_path=None → 直取不缓存(测试用)。

    ``cache_only=True``：仅读缓存，命中返回、未命中返回 None（绝不触网）。
    """
    if db_path is None:
        return None if cache_only else fetch_fn()

    entry = read_cache_entry(ts_code, kind, db_path)
    if entry is not None:
        payload, cached_at = entry
        age = _age_days(cached_at, today)
        if age is not None and age <= max_age_days:
            try:
                return pd.read_csv(io.StringIO(payload), dtype={"end_date": str, "trade_date": str})
            except (OSError, pd.errors.ParserError):
                pass

    if cache_only:
        return None

    df = fetch_fn()
    if df is not None and not df.empty:
        try:
            write_cache_entry(ts_code, kind, df.to_csv(index=False), today.strftime("%Y-%m-%d"), db_path)
        except OSError as exc:
            logger.debug("perilla_enrich_cache 写失败 %s/%s: %s", ts_code, kind, exc)
    return df


def _age_days(cached_at: str | None, today: _date) -> int | None:
    if not cached_at:
        return None
    try:
        y, m, d = (int(x) for x in cached_at.split("-"))
        return (today - _date(y, m, d)).days
    except (ValueError, TypeError):
        return None
