"""美股行情核心服务：Longbridge 优先，yFinance 降级。

本模块只做前向行情快照归一，不落库、不联网强依赖调用方。默认 provider
会延迟 import 外部 SDK；测试路径可通过依赖注入完全隔离网络。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Any, Literal, Protocol
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

US_MARKET_TZ = ZoneInfo("America/New_York")

QuoteStatus = Literal["live", "delayed", "stale", "static", "unavailable"]
MarketPhase = Literal["pre", "regular", "post", "closed"]

LONGBRIDGE_LIVE_MAX_AGE = timedelta(seconds=180)
YFINANCE_DELAYED_MAX_AGE = timedelta(minutes=15)


@dataclass(frozen=True)
class USMarketSymbol:
    """美股跑马灯标的定义。

    Attributes:
        code: 产品展示码。
        name: 中文展示名。
        route: 首选取数路径。
        yfinance_symbol: Yahoo/yFinance 取数代码；指数使用 ``^`` 前缀。
        longbridge_symbol: Longbridge 美股代码。为空则不走 Longbridge。
    """

    code: str
    name: str
    route: Literal["longbridge", "yfinance", "static"]
    yfinance_symbol: str | None = None
    longbridge_symbol: str | None = None


@dataclass(frozen=True)
class ProviderQuote:
    """单个 provider 的原子报价结果。

    ``price`` 与 ``prev_close`` 必须来自同一次 provider 返回；服务层不会把两个
    provider 的字段拼接成一个报价。
    """

    symbol: str
    price: float | None
    prev_close: float | None
    asof: datetime | None = None
    provider: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class USMarketQuote:
    """归一化美股行情输出。"""

    code: str
    name: str
    last: float | None
    prev_close: float | None
    pct: float | None
    source: str | None
    source_as_of: str | None
    received_at: str | None
    market_phase: MarketPhase
    status: QuoteStatus
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """转为 Swift wire 使用的 snake_case JSON dict。"""
        return {
            "code": self.code,
            "name": self.name,
            "last": self.last,
            "prev_close": self.prev_close,
            "pct": self.pct,
            "source": self.source,
            "source_as_of": self.source_as_of,
            "received_at": self.received_at,
            "market_phase": self.market_phase,
            "status": self.status,
            "error": self.error,
        }


@dataclass(frozen=True)
class USMarketCoverage:
    """美股行情覆盖统计。"""

    live: int = 0
    delayed: int = 0
    stale: int = 0
    static: int = 0
    unavailable: int = 0

    def to_dict(self) -> dict[str, int]:
        """转为 Swift wire JSON dict。"""
        return {
            "live": self.live,
            "delayed": self.delayed,
            "stale": self.stale,
            "static": self.static,
            "unavailable": self.unavailable,
        }


@dataclass(frozen=True)
class USMarketSnapshot:
    """顶层美股行情快照响应。"""

    quotes: list[USMarketQuote]
    market_phase: MarketPhase
    received_at: str
    coverage: USMarketCoverage

    @property
    def count(self) -> int:
        """报价条数。"""
        return len(self.quotes)

    def to_dict(self) -> dict[str, Any]:
        """转为 Swift ``USMarketQuotesResponse`` 可解码 dict。"""
        return {
            "quotes": [quote.to_dict() for quote in self.quotes],
            "count": self.count,
            "market_phase": self.market_phase,
            "received_at": self.received_at,
            "coverage": self.coverage.to_dict(),
        }


class BatchQuoteProvider(Protocol):
    """批量报价 provider 协议。"""

    def fetch_quotes(self, symbols: list[str]) -> dict[str, ProviderQuote]:
        """批量获取报价，失败标的可缺席。"""


class SingleQuoteProvider(Protocol):
    """逐标的报价 provider 协议。"""

    def fetch_quote(self, symbol: str) -> ProviderQuote | None:
        """获取单个报价，失败返回 ``None`` 或抛异常。"""


DEFAULT_US_MARKET_UNIVERSE: tuple[USMarketSymbol, ...] = (
    USMarketSymbol("MCHI", "MSCI中国指数ETF", "longbridge", "MCHI", "MCHI.US"),
    USMarketSymbol("IXIC", "纳斯达克综合指数", "yfinance", "^IXIC"),
    USMarketSymbol("DJI", "道琼斯指数", "yfinance", "^DJI"),
    USMarketSymbol("XIN9", "富时中国A50指数", "static"),
    USMarketSymbol("ROBO", "ROBO全球机器人", "longbridge", "ROBO", "ROBO.US"),
    USMarketSymbol("BOTZ", "GX机器人与AI", "longbridge", "BOTZ", "BOTZ.US"),
    USMarketSymbol("NVDA", "英伟达", "longbridge", "NVDA", "NVDA.US"),
    USMarketSymbol("SOXX", "半导体ETF-iShares", "longbridge", "SOXX", "SOXX.US"),
    USMarketSymbol("SMH", "半导体ETF-VanEck", "longbridge", "SMH", "SMH.US"),
    USMarketSymbol("TSLA", "特斯拉", "longbridge", "TSLA", "TSLA.US"),
    USMarketSymbol("MU", "美光科技", "longbridge", "MU", "MU.US"),
    USMarketSymbol("AVGO", "博通", "longbridge", "AVGO", "AVGO.US"),
)


class USMarketQuoteService:
    """美股行情服务。

    路由规则：
    - 9 个美股/ETF 标的优先 Longbridge，缺失或字段不完整时逐标的 yFinance fallback。
    - ``IXIC`` / ``DJI`` 使用 yFinance 的 ``^IXIC`` / ``^DJI``。
    - ``XIN9`` 当前只输出静态占位，避免伪造行情数字。
    """

    def __init__(
        self,
        *,
        longbridge_provider: BatchQuoteProvider | None = None,
        yfinance_provider: SingleQuoteProvider | None = None,
        universe: tuple[USMarketSymbol, ...] = DEFAULT_US_MARKET_UNIVERSE,
        now_provider: Any | None = None,
        market_calendar: Any | None = None,
        longbridge_live_max_age: timedelta = LONGBRIDGE_LIVE_MAX_AGE,
        yfinance_delayed_max_age: timedelta = YFINANCE_DELAYED_MAX_AGE,
    ) -> None:
        self.longbridge_provider = longbridge_provider
        self.yfinance_provider = yfinance_provider
        self.universe = universe
        self._now_provider = now_provider or (lambda: datetime.now(tz=US_MARKET_TZ))
        self._market_calendar = market_calendar
        self.longbridge_live_max_age = longbridge_live_max_age
        self.yfinance_delayed_max_age = yfinance_delayed_max_age

    def fetch_quotes(self, symbols: list[str] | tuple[str, ...] | None = None) -> list[USMarketQuote]:
        """按产品顺序返回行情；单标失败不影响其他标的。"""
        return self.fetch_snapshot(symbols).quotes

    def fetch_snapshot(self, symbols: list[str] | tuple[str, ...] | None = None) -> USMarketSnapshot:
        """返回顶层 serializable 美股行情快照。"""
        wanted = self._select_universe(symbols)
        now = _ensure_ny(self._now_provider())
        received_at = now.isoformat()
        market_phase = self._market_phase(now, None)

        lb_rows = [row for row in wanted if row.route == "longbridge"]
        lb_quotes = self._fetch_longbridge_batch(lb_rows)

        out: list[USMarketQuote] = []
        for row in wanted:
            quote: ProviderQuote | None = None
            if row.route == "static":
                out.append(_static_quote(row, now, market_phase))
                continue

            if row.route == "longbridge":
                quote = lb_quotes.get(row.code)
                if quote is None or not _is_complete_provider_quote(quote):
                    quote = self._fetch_yfinance(row)
            elif row.route == "yfinance":
                quote = self._fetch_yfinance(row)

            out.append(self._to_market_quote(row, quote, now))
        effective_phase = _snapshot_market_phase(out, fallback=market_phase)
        return USMarketSnapshot(
            quotes=out,
            market_phase=effective_phase,
            received_at=received_at,
            coverage=_coverage(out),
        )

    def _select_universe(self, symbols: list[str] | tuple[str, ...] | None) -> tuple[USMarketSymbol, ...]:
        if symbols is None:
            return self.universe
        wanted = {s.strip().upper() for s in symbols if s and s.strip()}
        known = {row.code.upper(): row for row in self.universe}
        out: list[USMarketSymbol] = []
        for code in wanted:
            if code in known:
                out.append(known[code])
            else:
                # 未知 code：显式 unavailable 占位，不静默丢弃
                out.append(
                    USMarketSymbol(
                        code,
                        code,
                        "static",
                        yfinance_symbol=None,
                        longbridge_symbol=None,
                    )
                )
        # 保持 self.universe 产品顺序优先，再附加未知
        ordered: list[USMarketSymbol] = []
        seen: set[str] = set()
        for row in self.universe:
            if row.code.upper() in wanted:
                ordered.append(row)
                seen.add(row.code.upper())
        for row in out:
            if row.code.upper() not in seen:
                ordered.append(row)
                seen.add(row.code.upper())
        return tuple(ordered)

    def _fetch_longbridge_batch(self, rows: list[USMarketSymbol]) -> dict[str, ProviderQuote]:
        if self.longbridge_provider is None or not rows:
            return {}
        request_symbols = [row.longbridge_symbol or row.code for row in rows]
        by_request = {
            (row.longbridge_symbol or row.code).upper(): row.code
            for row in rows
        }
        try:
            raw = self.longbridge_provider.fetch_quotes(request_symbols)
        except Exception as exc:  # noqa: BLE001
            logger.warning("us_market Longbridge 批量取数失败: %s", exc)
            return {}
        out: dict[str, ProviderQuote] = {}
        for key, quote in (raw or {}).items():
            product_code = by_request.get(str(key).upper()) or _strip_us_suffix(str(key).upper())
            if product_code in {row.code for row in rows}:
                out[product_code] = quote
        return out

    def _fetch_yfinance(self, row: USMarketSymbol) -> ProviderQuote | None:
        if self.yfinance_provider is None or not row.yfinance_symbol:
            return None
        try:
            return self.yfinance_provider.fetch_quote(row.yfinance_symbol)
        except Exception as exc:  # noqa: BLE001
            logger.warning("us_market yFinance %s 取数失败: %s", row.code, exc)
            return None

    def _to_market_quote(
        self,
        row: USMarketSymbol,
        quote: ProviderQuote | None,
        now: datetime,
    ) -> USMarketQuote:
        fallback_phase = self._market_phase(now, None)
        if quote is None:
            return _unavailable_quote(row, now, fallback_phase, "no_provider_quote")
        if not _is_complete_provider_quote(quote):
            return _unavailable_quote(row, now, fallback_phase, "incomplete_provider_quote")

        asof = _ensure_ny(quote.asof) if quote.asof is not None else None
        phase = self._market_phase(now, quote.metadata)
        status, error = _quote_status(
            asof=asof,
            now=now,
            market_phase=phase,
            provider=quote.provider,
            metadata=quote.metadata,
            longbridge_live_max_age=self.longbridge_live_max_age,
            yfinance_delayed_max_age=self.yfinance_delayed_max_age,
        )
        last = float(quote.price)  # guarded by _is_complete_provider_quote
        prev_close = float(quote.prev_close)
        pct = None if prev_close == 0 else round((last - prev_close) / prev_close * 100.0, 2)
        return USMarketQuote(
            code=row.code,
            name=row.name,
            last=last,
            prev_close=prev_close,
            pct=pct,
            source=quote.provider or "unknown",
            source_as_of=asof.isoformat() if asof is not None else None,
            received_at=now.isoformat(),
            market_phase=phase,
            status=status,
            error=error,
        )

    def _market_phase(self, now: datetime, metadata: dict[str, Any] | None) -> MarketPhase:
        phase = _phase_from_metadata(metadata or {})
        if phase is not None:
            return phase
        if self._market_calendar is not None:
            try:
                cal_phase = self._market_calendar(now)
                phase = _normalize_market_phase(cal_phase)
                if phase is not None:
                    return phase
            except Exception as exc:  # noqa: BLE001
                logger.debug("us_market calendar phase failed: %s", exc)
        return market_phase_for_datetime(now)


class LongbridgeUSQuoteProvider:
    """Longbridge 美股快照 provider 适配器。"""

    def __init__(self, provider: Any | None = None) -> None:
        self._provider = provider

    def fetch_quotes(self, symbols: list[str]) -> dict[str, ProviderQuote]:
        if not symbols:
            return {}
        provider = self._provider
        if provider is None:
            from kss.data.intraday_client import LongbridgeProvider  # noqa: PLC0415

            provider = LongbridgeProvider()
        result = provider.fetch_quotes(symbols)
        if getattr(result, "error", None):
            return {}
        rows = getattr(result, "rows", []) or []
        out: dict[str, ProviderQuote] = {}
        for row in rows:
            symbol = str(row.get("symbol") or "").upper()
            if not symbol:
                continue
            out[symbol] = ProviderQuote(
                symbol=symbol,
                price=_num(row.get("last_done")),
                prev_close=_num(row.get("prev_close")),
                asof=_parse_dt(row.get("timestamp") or getattr(result, "source_asof_ts", None)),
                provider="longbridge",
                metadata={
                    "trade_status": row.get("trade_status"),
                    "market_phase": row.get("market_phase"),
                },
            )
        return out


class YFinanceQuoteProvider:
    """yFinance 单标快照 provider 适配器。"""

    def fetch_quote(self, symbol: str) -> ProviderQuote | None:
        import yfinance as yf  # noqa: PLC0415

        ticker = yf.Ticker(symbol)
        fast_info = ticker.fast_info

        def pick(*keys: str) -> Any:
            for key in keys:
                value = fast_info.get(key) if hasattr(fast_info, "get") else getattr(fast_info, key, None)
                if value is not None:
                    return value
            return None

        price = _num(pick("lastPrice", "last_price", "regularMarketPrice"))
        prev_close = _num(pick("previousClose", "previous_close", "regularMarketPreviousClose"))
        currency = pick("currency") or "USD"
        asof = _timestamp_from_yfinance_fast_info(fast_info)
        if asof is None:
            asof = _timestamp_from_yfinance_history(ticker)
        if price is None or prev_close is None:
            return None
        return ProviderQuote(
            symbol=symbol,
            price=price,
            prev_close=prev_close,
            asof=asof,
            provider="yfinance",
            metadata={"currency": currency, "delay_minutes": 15, "market_phase": pick("marketState", "market_state")},
        )


def default_us_market_quote_service() -> USMarketQuoteService:
    """构造默认线上服务。"""
    return USMarketQuoteService(
        longbridge_provider=LongbridgeUSQuoteProvider(),
        yfinance_provider=YFinanceQuoteProvider(),
    )


def is_us_regular_session(now: datetime) -> bool:
    """判断美股常规交易时段（不含节假日，标准库无交易日历）。"""
    return market_phase_for_datetime(now) == "regular"


def market_phase_for_datetime(now: datetime) -> MarketPhase:
    """按纽约本地时间粗分交易阶段（不含节假日硬表）。"""
    ny = _ensure_ny(now)
    if ny.weekday() >= 5:
        return "closed"
    t = ny.time()
    if time(4, 0) <= t < time(9, 30):
        return "pre"
    if time(9, 30) <= t < time(16, 0):
        return "regular"
    if time(16, 0) <= t < time(20, 0):
        return "post"
    return "closed"


def _quote_status(
    *,
    asof: datetime | None,
    now: datetime,
    market_phase: MarketPhase,
    provider: str,
    metadata: dict[str, Any],
    longbridge_live_max_age: timedelta,
    yfinance_delayed_max_age: timedelta,
) -> tuple[QuoteStatus, str | None]:
    if asof is None:
        if market_phase == "regular":
            return "stale", "missing_source_as_of"
        return "static", None
    age = now - asof
    if age < timedelta(0):
        age = timedelta(0)
    if market_phase != "regular":
        return "static", None
    provider_name = provider.lower()
    provider_status = str(metadata.get("status") or "").lower()
    if provider_status in {"unavailable", "error"}:
        return "unavailable", provider_status
    if provider_name == "longbridge":
        if age <= longbridge_live_max_age:
            return "live", None
        return "stale", "longbridge_stale"
    if provider_name == "yfinance":
        if age <= yfinance_delayed_max_age:
            return "delayed", None
        return "stale", "yfinance_stale"
    return "stale", "unknown_provider_freshness"


def _static_quote(row: USMarketSymbol, now: datetime, market_phase: MarketPhase) -> USMarketQuote:
    return USMarketQuote(
        code=row.code,
        name=row.name,
        last=None,
        prev_close=None,
        pct=None,
        source="static",
        source_as_of=None,
        received_at=now.isoformat(),
        market_phase=market_phase,
        status="static",
        error="static_placeholder" if row.code == "XIN9" else None,
    )


def _unavailable_quote(row: USMarketSymbol, now: datetime, market_phase: MarketPhase, reason: str) -> USMarketQuote:
    return USMarketQuote(
        code=row.code,
        name=row.name,
        last=None,
        prev_close=None,
        pct=None,
        source=None,
        source_as_of=None,
        received_at=now.isoformat(),
        market_phase=market_phase,
        status="unavailable",
        error=reason,
    )


def _is_complete_provider_quote(quote: ProviderQuote) -> bool:
    return _num(quote.price) is not None and _num(quote.prev_close) is not None


def _ensure_ny(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=US_MARKET_TZ)
    return dt.astimezone(US_MARKET_TZ)


def _parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            return None
    return None


def _timestamp_from_yfinance_fast_info(fast_info: Any) -> datetime | None:
    """尽力从 yFinance fast_info 中解析真实行情时间。"""

    def pick(*keys: str) -> Any:
        for key in keys:
            value = fast_info.get(key) if hasattr(fast_info, "get") else getattr(fast_info, key, None)
            if value is not None:
                return value
        return None

    raw = pick(
        "lastTradeDate",
        "last_trade_date",
        "regularMarketTime",
        "regular_market_time",
        "lastMarketTime",
        "last_market_time",
    )
    return _parse_market_timestamp(raw)


def _timestamp_from_yfinance_history(ticker: Any) -> datetime | None:
    """从 1m bar 最后一行索引读取 yFinance 行情时间。"""
    try:
        hist = ticker.history(period="1d", interval="1m", auto_adjust=False)
    except Exception as exc:  # noqa: BLE001
        logger.debug("us_market yFinance history timestamp failed: %s", exc)
        return None
    if hist is None or getattr(hist, "empty", True):
        return None
    try:
        return _parse_market_timestamp(hist.index[-1])
    except Exception as exc:  # noqa: BLE001
        logger.debug("us_market yFinance history timestamp parse failed: %s", exc)
        return None


def _parse_market_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=US_MARKET_TZ)
        except (OverflowError, OSError, ValueError):
            return None
    if hasattr(value, "to_pydatetime"):
        try:
            return value.to_pydatetime()
        except Exception:  # noqa: BLE001
            return None
    return _parse_dt(str(value))


def _phase_from_metadata(metadata: dict[str, Any]) -> MarketPhase | None:
    for key in ("market_phase", "marketPhase", "market_state", "marketState", "trade_status", "tradeStatus"):
        phase = _normalize_market_phase(metadata.get(key))
        if phase is not None:
            return phase
    return None


def _normalize_market_phase(value: Any) -> MarketPhase | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    if text in {"regular", "normal", "open", "trading", "trade", "intraday"}:
        return "regular"
    if text in {"pre", "premarket", "pre_market", "pre-market"}:
        return "pre"
    if text in {"post", "postmarket", "post_market", "afterhours", "after_hours", "after-hours"}:
        return "post"
    if text in {"closed", "close", "休市", "halted"}:
        return "closed"
    return None


def _coverage(quotes: list[USMarketQuote]) -> USMarketCoverage:
    counts = {status: 0 for status in ("live", "delayed", "stale", "static", "unavailable")}
    for quote in quotes:
        counts[quote.status] += 1
    return USMarketCoverage(
        live=counts["live"],
        delayed=counts["delayed"],
        stale=counts["stale"],
        static=counts["static"],
        unavailable=counts["unavailable"],
    )


def _snapshot_market_phase(
    quotes: list[USMarketQuote],
    *,
    fallback: MarketPhase,
) -> MarketPhase:
    """优先使用 provider 行情阶段，避免节假日按工作日时间持续轮询。"""

    if any(quote.status in {"live", "delayed"} for quote in quotes):
        return "regular"
    provider_phases = {
        quote.market_phase
        for quote in quotes
        if quote.source not in {None, "static"}
        and quote.status != "unavailable"
    }
    if len(provider_phases) == 1:
        return next(iter(provider_phases))
    if provider_phases and "regular" not in provider_phases:
        if "closed" in provider_phases:
            return "closed"
        if "post" in provider_phases:
            return "post"
        if "pre" in provider_phases:
            return "pre"
    return fallback


def _num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _strip_us_suffix(symbol: str) -> str:
    return symbol[:-3] if symbol.endswith(".US") else symbol


__all__ = [
    "DEFAULT_US_MARKET_UNIVERSE",
    "ProviderQuote",
    "QuoteStatus",
    "MarketPhase",
    "USMarketQuote",
    "USMarketCoverage",
    "USMarketSnapshot",
    "USMarketQuoteService",
    "USMarketSymbol",
    "LongbridgeUSQuoteProvider",
    "YFinanceQuoteProvider",
    "default_us_market_quote_service",
    "is_us_regular_session",
    "market_phase_for_datetime",
]
