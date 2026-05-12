"""Unified data loader — orchestrates cache and tushare API calls."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from kss.data.cache_manager import CacheManager
from kss.data.tushare_client import TushareClient

logger = logging.getLogger(__name__)

# Symbol prefixes that map to specific exchanges
_PREFIX_EXCHANGE: dict[str, str] = {
    "688": "SH",  # 科创板
    "689": "SH",  # 科创板（少量）
    "300": "SZ",  # 创业板
    "301": "SZ",  # 创业板（注册制）
    "302": "SZ",  # 创业板（新段）
}


def _detect_exchange(symbol: str) -> str:
    """Auto-detect exchange from symbol prefix.

    Falls back to ``SH`` when no known prefix is matched.
    """
    for prefix, exchange in _PREFIX_EXCHANGE.items():
        if symbol.startswith(prefix):
            return exchange
    return "SH"


def _today_str() -> str:
    """Return today's date as ``YYYYMMDD``."""
    return datetime.now().strftime("%Y%m%d")


class DataLoader:
    """High-level loader that combines caching with tushare fetching.

    Usage::

        client = TushareClient()
        cache = CacheManager("./cs_data")
        loader = DataLoader(client, cache)
        df = loader.load_stock("688008", start="20230101")
    """

    def __init__(
        self,
        client: TushareClient,
        cache_manager: CacheManager,
    ) -> None:
        self.client = client
        self.cache = cache_manager

    def load_stock(
        self,
        symbol: str,
        start: str = "20230101",
        end: str | None = None,
        exchange: str = "SH",
    ) -> pd.DataFrame | None:
        """Load daily data for a single stock, using cache when possible.

        The method follows this resolution order:

        1. If a fresh cache exists, return it.
        2. Otherwise fetch from tushare (``pro.daily`` + ``pro.daily_basic``).
        3. Merge daily_basic fields (``turnover_rate``, ``volume_ratio``,
           ``pe``, ``pb``, ``total_mv``).
        4. Fill missing ``turnover_rate`` / ``volume_ratio`` with defaults.
        5. Sort by ``trade_date``, persist to cache, and return.

        Args:
            symbol: Stock code, e.g. ``688008`` or ``300750``.
            start: Start date in ``YYYYMMDD`` format.
            end: End date in ``YYYYMMDD`` format.  Defaults to today.
            exchange: Exchange suffix (``SH`` or ``SZ``).  If left as the
                default ``SH``, the loader will auto-detect the correct
                exchange from the symbol prefix (``688`` → ``SH``,
                ``300`` → ``SZ``, etc.).

        Returns:
            DataFrame with merged daily + basic data, or ``None`` when the
            symbol cannot be resolved.
        """
        if end is None:
            end = _today_str()

        if exchange == "SH" and not symbol.startswith(("60", "68", "69")):
            exchange = _detect_exchange(symbol)

        # ------------------------------------------------------------------
        # 1. Cache hit (fresh + 内部连续)
        # ------------------------------------------------------------------
        # 同时校验缓存内部连续性（has_internal_gaps），避免 Tushare 部分响应缓存
        # 中间断档而 is_stale 只看 max(trade_date) 看不到。
        if (
            self.cache.exists(symbol)
            and not self.cache.is_stale(symbol)
            and not self.cache.has_internal_gaps(symbol)
        ):
            logger.debug("Cache hit for %s", symbol)
            cached = self.cache.load(symbol)
            if cached is not None:
                # Restrict to requested date range in case the cached window
                # is wider than the caller needs.
                mask = (cached["trade_date"] >= pd.to_datetime(start)) & (
                    cached["trade_date"] <= pd.to_datetime(end)
                )
                return cached.loc[mask].copy()
        elif self.cache.exists(symbol) and self.cache.has_internal_gaps(symbol):
            logger.warning(
                "缓存 %s 内部存在 >%d 天 gap，疑似 Tushare 部分响应，强制重抓",
                symbol,
                self.cache.DEFAULT_MAX_GAP_DAYS,
            )

        # ------------------------------------------------------------------
        # 2. Fetch from tushare
        # ------------------------------------------------------------------
        ts_code = f"{symbol}.{exchange}"
        df = self.client.fetch_daily(ts_code, start, end)
        if df is None or df.empty:
            logger.warning("No daily data returned for %s", ts_code)
            return None

        df["trade_date"] = pd.to_datetime(df["trade_date"])

        # ------------------------------------------------------------------
        # 3. Merge daily_basic
        # ------------------------------------------------------------------
        try:
            basic = self.client.fetch_daily_basic(ts_code, start, end)
            if basic is not None and not basic.empty:
                basic["trade_date"] = pd.to_datetime(basic["trade_date"])
                keep_cols = [
                    "trade_date",
                    "turnover_rate",
                    "volume_ratio",
                    "pe",
                    "pb",
                    "total_mv",
                ]
                keep_cols = [c for c in keep_cols if c in basic.columns]
                df = df.merge(basic[keep_cols], on="trade_date", how="left")
        except Exception as exc:  # noqa: BLE001
            logger.warning("daily_basic merge failed for %s: %s", ts_code, exc)

        # ------------------------------------------------------------------
        # 4. Fill missing columns with sensible defaults
        # ------------------------------------------------------------------
        if "turnover_rate" not in df.columns:
            df["turnover_rate"] = 0.0
        if "volume_ratio" not in df.columns:
            df["volume_ratio"] = 1.0
        df["turnover_rate"] = df["turnover_rate"].fillna(0.0)
        df["volume_ratio"] = df["volume_ratio"].fillna(1.0)

        # ------------------------------------------------------------------
        # 5. Sort and persist
        # ------------------------------------------------------------------
        df = df.sort_values("trade_date").reset_index(drop=True)
        self.cache.save(symbol, df)
        return df

    def load_stock_pool(
        self,
        pool_name: str = "kcb50",
        start: str = "20230101",
        end: str | None = None,
    ) -> pd.DataFrame | None:
        """Load data for an entire symbol pool.

        The pool definition is read from ``{pool_name}_symbols.csv`` in the
        current working directory.  Each row must contain a ``symbol`` column.

        Args:
            pool_name: Basename of the CSV file (e.g. ``kcb50`` →
                ``kcb50_symbols.csv``).
            start: Start date in ``YYYYMMDD`` format.
            end: End date in ``YYYYMMDD`` format.  Defaults to today.

        Returns:
            Combined DataFrame for all successfully loaded symbols with an
            added ``symbol`` column, or ``None`` if no symbols could be
            loaded.
        """
        if end is None:
            end = _today_str()

        csv_path = Path(f"{pool_name}_symbols.csv")
        if not csv_path.exists():
            logger.error("Pool definition not found: %s", csv_path.resolve())
            return None

        try:
            symbols_df = pd.read_csv(csv_path)
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to read %s: %s", csv_path, exc)
            return None

        if "symbol" not in symbols_df.columns:
            logger.error("Missing 'symbol' column in %s", csv_path)
            return None

        symbols = symbols_df["symbol"].astype(str).tolist()
        logger.info("Loading pool '%s' with %d symbols", pool_name, len(symbols))

        frames: list[pd.DataFrame] = []
        for sym in symbols:
            df = self.load_stock(sym, start=start, end=end)
            if df is not None and len(df) > 0:
                df["symbol"] = sym
                frames.append(df)
                logger.info("  %s: %d rows", sym, len(df))
            else:
                logger.warning("  %s: no data", sym)

        if not frames:
            logger.error("No data loaded for pool '%s'", pool_name)
            return None

        combined = pd.concat(frames, ignore_index=True)
        logger.info(
            "Pool '%s' loaded: %d rows, %d unique symbols",
            pool_name,
            len(combined),
            combined["symbol"].nunique(),
        )
        return combined
