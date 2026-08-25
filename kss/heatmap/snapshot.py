"""Public-tape heatmap snapshot (plan U2 / KTD2).

Adapts the MIT a-share-heatmap Eastmoney clist path. Display-only: never
writes backtest, cs_data, or PIT stores. Sample / fallback / undated /
empty payloads fail closed.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

MARKET_KEYS = ("all", "sse", "szse", "hs300", "zza50", "zza500", "main", "cyb", "kcb")
PERIOD_KEYS = ("day", "week", "month", "year")
FLAT_THRESHOLD = 0.1
CACHE_TTL_SECONDS = 8.0
EASTMONEY_HOSTS = (
    "push2delay.eastmoney.com",
    "82.push2.eastmoney.com",
    "7.push2.eastmoney.com",
    "48.push2.eastmoney.com",
    "push2.eastmoney.com",
)
EASTMONEY_FS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"
EASTMONEY_FIELDS = ",".join(
    (
        "f2",
        "f3",
        "f6",
        "f12",
        "f13",
        "f14",
        "f18",
        "f20",
        "f21",
        "f24",
        "f25",
        "f100",
        "f109",
        "f110",
        "f124",
    )
)
PAGE_SIZE = 100
_SHANGHAI = timezone(timedelta(hours=8))

HttpGet = Callable[[str, dict[str, str], float], dict[str, Any]]


class HeatmapSnapshotError(ValueError):
    """Raised when a current public tape cannot be shown."""


_cache_lock = threading.Lock()
_row_cache: tuple[float, list[dict[str, Any]], str, str] | None = None


def _finite(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and value == value and value not in (float("inf"), float("-inf")):
        return float(value)
    if isinstance(value, str):
        try:
            parsed = float(value)
        except ValueError:
            return None
        if parsed == parsed and parsed not in (float("inf"), float("-inf")):
            return parsed
    return None


def _parse_code(symbol: Any, market_flag: Any) -> str | None:
    text = str(symbol or "").strip()
    if not text:
        return None
    if str(market_flag).strip() == "1":
        return f"{text}.SH"
    if text[:1] in {"4", "8", "9"}:
        return f"{text}.BJ"
    return f"{text}.SZ"


def _bare_code(symbol: str) -> str:
    return symbol.split(".", 1)[0]


def _exchange(symbol: str) -> str:
    parts = symbol.split(".")
    return parts[1] if len(parts) == 2 else ""


def in_market(symbol: str, market: str, index_sets: dict[str, set[str]]) -> bool:
    if market == "all":
        return True
    exchange = _exchange(symbol)
    bare = _bare_code(symbol)
    if market == "sse":
        return exchange == "SH"
    if market == "szse":
        return exchange == "SZ"
    if market == "main":
        if exchange == "BJ":
            return False
        if exchange == "SH" and bare.startswith(("688", "689")):
            return False
        if exchange == "SZ" and bare.startswith("30"):
            return False
        return exchange in {"SH", "SZ"}
    if market == "cyb":
        return exchange == "SZ" and bare.startswith("300")
    if market == "kcb":
        return exchange == "SH" and bare.startswith("688")
    if market == "hs300":
        return symbol in index_sets["hs300"]
    if market == "zza50":
        return symbol in index_sets["zza50"]
    if market == "zza500":
        return symbol in index_sets["zza500"]
    return False


def index_sets_from_rows(rows: list[dict[str, Any]]) -> dict[str, set[str]]:
    ranked = sorted(rows, key=lambda row: float(row.get("circMv") or 0), reverse=True)
    codes = [str(row["symbol"]) for row in ranked if row.get("symbol")]
    return {
        "zza50": set(codes[:50]),
        "hs300": set(codes[:300]),
        "zza500": set(codes[:500]),
    }


def trade_date_from_unix(value: Any) -> str | None:
    seconds = _finite(value)
    if seconds is None or seconds <= 0:
        return None
    stamp = datetime.fromtimestamp(seconds, tz=_SHANGHAI)
    return stamp.strftime("%Y%m%d")


def updated_at_from_unix(value: Any) -> str | None:
    seconds = _finite(value)
    if seconds is None or seconds <= 0:
        return None
    return datetime.fromtimestamp(seconds, tz=_SHANGHAI).isoformat()


def change_for_period(row: dict[str, Any], period: str) -> float | None:
    key = {
        "day": "changeDay",
        "week": "changeWeek",
        "month": "changeMonth",
        "year": "changeYear",
    }[period]
    selected = _finite(row.get(key))
    if selected is not None:
        return selected
    return _finite(row.get("changeDay"))


def parse_eastmoney_diff(diff: list[Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in diff:
        if not isinstance(item, dict):
            continue
        symbol = _parse_code(item.get("f12"), item.get("f13"))
        if not symbol:
            continue
        price = _finite(item.get("f2"))
        if price is None or price <= 0:
            continue
        previous = _finite(item.get("f18")) or 0.0
        day = _finite(item.get("f3"))
        if day is None and previous > 0:
            day = (price - previous) / previous * 100.0
        week = _finite(item.get("f109"))
        month = _finite(item.get("f110")) or _finite(item.get("f24"))
        year = _finite(item.get("f25"))
        circ = _finite(item.get("f21")) or _finite(item.get("f20"))
        name = str(item.get("f14") or "").strip()
        industry = str(item.get("f100") or "").strip()
        rows.append(
            {
                "code": _bare_code(symbol),
                "symbol": symbol,
                "name": name,
                "industry": industry or "未分类",
                "circMv": circ or 0.0,
                "price": price,
                "turnover": _finite(item.get("f6")) or 0.0,
                "changeDay": day,
                "changeWeek": week if week is not None else day,
                "changeMonth": month if month is not None else day,
                "changeYear": year if year is not None else day,
                "quoteTs": item.get("f124"),
            }
        )
    return rows


def summarize_tiles(tiles: list[dict[str, Any]]) -> dict[str, float | int]:
    advance = flat = decline = 0
    turnover = 0.0
    for tile in tiles:
        change = float(tile["changePct"])
        turnover += float(tile.get("turnover") or 0.0)
        if abs(change) < FLAT_THRESHOLD:
            flat += 1
        elif change > 0:
            advance += 1
        else:
            decline += 1
    return {
        "advanceCount": advance,
        "flatCount": flat,
        "declineCount": decline,
        "turnoverAmount": turnover,
    }


def reject_unusable(*, source: str, trade_date: str | None, tiles: list[dict[str, Any]]) -> None:
    lowered = source.strip().lower()
    if lowered in {"fallback", "sample", "demo"}:
        raise HeatmapSnapshotError("heatmap snapshot is sample or fallback, not a live tape")
    if not trade_date:
        raise HeatmapSnapshotError("heatmap snapshot has no current trade date")
    if not tiles:
        raise HeatmapSnapshotError("heatmap snapshot constituent list is empty")


def build_snapshot(
    *,
    rows: list[dict[str, Any]],
    market: str,
    period: str,
    source: str,
    trade_date: str | None,
    updated_at: str,
) -> dict[str, Any]:
    if market not in MARKET_KEYS:
        raise HeatmapSnapshotError(f"invalid heatmap market: {market}")
    if period not in PERIOD_KEYS:
        raise HeatmapSnapshotError(f"invalid heatmap period: {period}")
    sets = index_sets_from_rows(rows)
    tiles: list[dict[str, Any]] = []
    for row in rows:
        symbol = str(row.get("symbol") or "")
        if not in_market(symbol, market, sets):
            continue
        change = change_for_period(row, period)
        if change is None:
            continue
        tiles.append(
            {
                "code": row["code"],
                "symbol": symbol,
                "name": row.get("name") or symbol,
                "industry": row.get("industry") or "未分类",
                "circMv": float(row.get("circMv") or 0.0),
                "changePct": change,
                "turnover": float(row.get("turnover") or 0.0),
                "price": float(row.get("price") or 0.0),
            }
        )
    reject_unusable(source=source, trade_date=trade_date, tiles=tiles)
    return {
        "market": market,
        "period": period,
        "updatedAt": updated_at,
        "tradeDate": trade_date,
        "source": "direct",
        "tiles": tiles,
        "summary": summarize_tiles(tiles),
    }


def default_http_get(url: str, headers: dict[str, str], timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = getattr(response, "status", 200)
            if status >= 400:
                raise HeatmapSnapshotError(f"heatmap upstream returned {status}")
            payload = json.loads(response.read().decode("utf-8"))
    except HeatmapSnapshotError:
        raise
    except TimeoutError as exc:
        raise HeatmapSnapshotError("heatmap upstream timed out") from exc
    except urllib.error.HTTPError as exc:
        raise HeatmapSnapshotError(f"heatmap upstream returned {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise HeatmapSnapshotError(f"heatmap upstream failed: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise HeatmapSnapshotError("heatmap upstream payload is not JSON") from exc
    if not isinstance(payload, dict):
        raise HeatmapSnapshotError("heatmap upstream payload is invalid")
    return payload


def _clist_url(host: str, page: int) -> str:
    params = (
        f"pn={page}&pz={PAGE_SIZE}&po=1&np=1"
        "&ut=bd1d9ddb04089700cf9c27f6f7426281"
        "&fltt=2&invt=2&fid=f12"
        f"&fs={EASTMONEY_FS}&fields={EASTMONEY_FIELDS}"
    )
    return f"https://{host}/api/qt/clist/get?{params}"


def fetch_eastmoney_rows(http_get: HttpGet) -> tuple[list[dict[str, Any]], str, str]:
    headers = {
        "Referer": "https://quote.eastmoney.com/",
        "User-Agent": "Mozilla/5.0 (compatible; KSSHeatmap/1.0)",
        "Accept": "application/json, text/plain, */*",
    }
    last_error: Exception | None = None
    first: dict[str, Any] | None = None
    for host in EASTMONEY_HOSTS:
        try:
            first = http_get(_clist_url(host, 1), headers, 10.0)
            break
        except HeatmapSnapshotError as exc:
            last_error = exc
    if first is None:
        raise last_error or HeatmapSnapshotError("heatmap upstream failed")
    data = first.get("data") if isinstance(first.get("data"), dict) else {}
    diff = data.get("diff") if isinstance(data, dict) else None
    if not isinstance(diff, list):
        raise HeatmapSnapshotError("heatmap upstream payload is invalid")
    total = int(_finite(data.get("total")) or len(diff))
    page_count = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    payloads: list[dict[str, Any] | None] = [first] + [None] * max(0, page_count - 1)

    def _fetch_page(page: int) -> dict[str, Any]:
        page_error: Exception | None = None
        for host in EASTMONEY_HOSTS[:2]:
            try:
                return http_get(_clist_url(host, page), headers, 10.0)
            except HeatmapSnapshotError as exc:
                page_error = exc
        raise page_error or HeatmapSnapshotError("heatmap upstream pages incomplete")

    if page_count > 1:
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(_fetch_page, page): page for page in range(2, page_count + 1)}
            for future in as_completed(futures):
                payloads[futures[future] - 1] = future.result()
    resolved = [item for item in payloads if item is not None]
    if len(resolved) != page_count:
        raise HeatmapSnapshotError("heatmap upstream pages incomplete")
    payloads = resolved
    rows: list[dict[str, Any]] = []
    trade_date: str | None = None
    updated_at = ""
    for payload in payloads:
        page_data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        page_diff = page_data.get("diff") if isinstance(page_data, dict) else None
        if not isinstance(page_diff, list):
            raise HeatmapSnapshotError("heatmap upstream payload is invalid")
        rows.extend(parse_eastmoney_diff(page_diff))
        for item in page_diff:
            if not isinstance(item, dict):
                continue
            stamp = updated_at_from_unix(item.get("f124"))
            date = trade_date_from_unix(item.get("f124"))
            if stamp and (not updated_at or stamp > updated_at):
                updated_at = stamp
            if date and (trade_date is None or date > trade_date):
                trade_date = date
    if not trade_date:
        raise HeatmapSnapshotError("heatmap snapshot has no current trade date")
    return rows, trade_date, updated_at or datetime.now(tz=_SHANGHAI).isoformat()


def load_snapshot(
    market: str = "all",
    period: str = "day",
    *,
    http_get: HttpGet | None = None,
    force_refresh: bool = False,
) -> dict[str, Any]:
    getter = http_get or default_http_get
    now = time.monotonic()
    global _row_cache
    with _cache_lock:
        cached = None if force_refresh else _row_cache
        if cached and now - cached[0] < CACHE_TTL_SECONDS:
            rows, trade_date, updated_at = cached[1], cached[2], cached[3]
        else:
            rows, trade_date, updated_at = fetch_eastmoney_rows(getter)
            _row_cache = (now, rows, trade_date, updated_at)
    return build_snapshot(
        rows=rows,
        market=market,
        period=period,
        source="direct",
        trade_date=trade_date,
        updated_at=updated_at,
    )


def reset_cache() -> None:
    global _row_cache
    with _cache_lock:
        _row_cache = None
