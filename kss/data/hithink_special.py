"""HiThink special-data adapters → existing SectorSnapshot column contracts.

Maps official limit-up / anomaly / dragon-tiger envelopes onto the columns
``fetch_ths_hot`` and ``fetch_dragon_tiger`` already emit so commentary /
``render_*_line`` stay unchanged (KTD2).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Sequence
from zoneinfo import ZoneInfo

import pandas as pd

from kss.data.hithink_client import HithinkClient, ymd_to_date_ms, ymd_to_iso

logger = logging.getLogger(__name__)

_REASON_TRUNCATE = 80
_LIMIT_UP_PAGE_SIZE = 200
_LIMIT_UP_PAGE_CAP = 20
_AGENT_LIMIT_UP_ROWS = 50
_THS_HOT_COLUMNS = (
    "code", "name", "reason", "pct_change", "close",
    "turnover_rate", "amount", "market",
)
_LHB_COLUMNS = ("code", "name", "net_amount", "reason", "turnover_rate", "amount")


def _today_ymd() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d")


def _market_from_thscode(thscode: str) -> str:
    if thscode.endswith(".SH"):
        return "沪"
    if thscode.endswith(".SZ"):
        return "深"
    if thscode.endswith(".BJ"):
        return "北"
    return ""


def _ticker(row: MappingLike) -> str:
    ticker = str(row.get("ticker") or "").strip()
    if ticker:
        return ticker
    thscode = str(row.get("thscode") or "")
    return thscode.split(".", 1)[0]


def _join_tags(values: Any) -> str:
    """Join keyword / concept values. Live dragon-tiger uses ``{name: ...}`` objects."""
    if not isinstance(values, (list, tuple)):
        return ""
    parts: list[str] = []
    for v in values:
        if isinstance(v, dict):
            name = str(v.get("name") or "").strip()
            if name:
                parts.append(name)
            continue
        s = str(v).strip()
        if s:
            parts.append(s)
    return "+".join(parts)


def _limit_up_reason(row: MappingLike, anomaly: MappingLike | None) -> str:
    reason = str(row.get("limit_up_reason") or "").strip()
    if reason:
        return reason
    if anomaly:
        tags = _join_tags(anomaly.get("keyword_list"))
        if tags:
            return tags
        content = str(anomaly.get("analysis_content") or "").strip()
        if content:
            return content[:_REASON_TRUNCATE]
    return ""


MappingLike = dict[str, Any]


def _anomaly_index(data: Any) -> dict[str, dict[str, Any]]:
    items = (data or {}).get("item") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for row in items:
        if not isinstance(row, dict):
            continue
        code = str(row.get("thscode") or "").strip()
        if code and code not in out:
            out[code] = row
    return out


def iter_limit_up_items(
    client: HithinkClient,
    *,
    date_ms: int | None = None,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for page in range(1, _LIMIT_UP_PAGE_CAP + 1):
        data = client.get_limit_up_pool(date_ms=date_ms, page=page, size=_LIMIT_UP_PAGE_SIZE)
        if not isinstance(data, dict):
            break
        chunk = data.get("item") or []
        if not isinstance(chunk, list):
            break
        items.extend(row for row in chunk if isinstance(row, dict))
        pages = int((data.get("pagination") or {}).get("pages") or 1)
        if page >= pages or not chunk:
            break
    return items


def map_ths_hot(
    limit_up_items: Sequence[dict[str, Any]],
    anomaly_data: Any | None,
) -> pd.DataFrame | None:
    if not limit_up_items:
        return None
    anomaly_by = _anomaly_index(anomaly_data)
    rows: list[dict[str, Any]] = []
    for row in limit_up_items:
        thscode = str(row.get("thscode") or "").strip()
        code = _ticker(row)
        if not code:
            continue
        reason = _limit_up_reason(row, anomaly_by.get(thscode))
        if not reason:
            continue
        rows.append({
            "code": code,
            "name": str(row.get("name") or "").strip(),
            "reason": reason,
            "pct_change": row.get("price_change_ratio_pct"),
            "close": row.get("last_price"),
            "turnover_rate": None,
            "amount": row.get("seal_money"),
            "market": _market_from_thscode(thscode),
        })
    if not rows:
        return None
    df = pd.DataFrame(rows)
    for col in ("pct_change", "close", "turnover_rate", "amount"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.reset_index(drop=True)


def map_dragon_tiger(data: Any) -> pd.DataFrame | None:
    """Official ``net_value`` is the net amount field; unit locked by fixture tests."""
    if not isinstance(data, dict):
        return None
    items = data.get("stock_items") or []
    if not isinstance(items, list) or not items:
        return None
    rows: list[dict[str, Any]] = []
    for row in items:
        if not isinstance(row, dict):
            continue
        code = _ticker(row)
        if not code:
            continue
        net = row.get("net_value")
        if net is None:
            buy = pd.to_numeric(row.get("buy_value"), errors="coerce")
            sell = pd.to_numeric(row.get("sell_value"), errors="coerce")
            if pd.isna(buy) or pd.isna(sell):
                continue
            net = float(buy) - float(sell)
        reason = str(row.get("limit_reason") or "").strip()
        if not reason:
            reason = _join_tags(row.get("concept_list"))
        if not reason:
            continue
        rows.append({
            "code": code,
            "name": str(row.get("name") or "").strip(),
            "net_amount": net,
            "reason": reason,
            "turnover_rate": None,
            "amount": None,
        })
    if not rows:
        return None
    df = pd.DataFrame(rows)
    df["net_amount"] = pd.to_numeric(df["net_amount"], errors="coerce")
    df = df.dropna(subset=["net_amount"])
    if df.empty:
        return None
    return df.sort_values("net_amount", ascending=False).reset_index(drop=True)


def fetch_hithink_ths_hot(
    trade_date: str,
    *,
    client: HithinkClient | None = None,
) -> pd.DataFrame | None:
    try:
        date_ms = ymd_to_date_ms(trade_date)
    except ValueError:
        logger.warning("[hithink_hot] trade_date 非法: %r", trade_date)
        return None
    cli = client or HithinkClient()
    if not cli.api_key:
        return None
    items = iter_limit_up_items(cli, date_ms=date_ms)
    anomaly = None
    if trade_date == _today_ymd():
        anomaly = cli.get_anomaly_analysis_list()
    return map_ths_hot(items, anomaly)


def fetch_hithink_dragon_tiger(
    trade_date: str,
    *,
    client: HithinkClient | None = None,
) -> pd.DataFrame | None:
    try:
        date_iso = ymd_to_iso(trade_date)
    except ValueError:
        logger.warning("[hithink_lhb] trade_date 非法: %r", trade_date)
        return None
    cli = client or HithinkClient()
    if not cli.api_key:
        return None
    data = cli.get_dragon_tiger_list(board_type="all", date=date_iso)
    return map_dragon_tiger(data)


def limit_up_tool_payload(date_text: str = "") -> dict[str, Any]:
    """Structured rows for ``hithink-limit-up`` (agent cites fields verbatim)."""
    ymd = (date_text or "").replace("-", "").strip() or _today_ymd()
    try:
        date_ms = ymd_to_date_ms(ymd)
    except ValueError:
        return {"error": "invalid_date", "hint": f"日期须为 YYYYMMDD / YYYY-MM-DD，收到 {date_text!r}"}
    client = HithinkClient()
    if not client.api_key:
        return {"error": "not_configured", "hint": "未配置 HITHINK_FINANCE_API_KEY"}
    items = iter_limit_up_items(client, date_ms=date_ms)
    anomaly = client.get_anomaly_analysis_list() if ymd == _today_ymd() else None
    anomaly_by = _anomaly_index(anomaly)
    rows: list[dict[str, Any]] = []
    for row in items[:_AGENT_LIMIT_UP_ROWS]:
        thscode = str(row.get("thscode") or "")
        rows.append({
            "thscode": thscode,
            "name": row.get("name"),
            "limit_up_time": row.get("limit_up_time"),
            "reason": _limit_up_reason(row, anomaly_by.get(thscode)),
            "continue_day_cnt": row.get("continue_day_cnt"),
            "pct_change": row.get("price_change_ratio_pct"),
            "last_price": row.get("last_price"),
        })
    return {
        "trade_date": ymd,
        "source": "hithink",
        "eligibility": "forward_observed",
        "count": len(items),
        "truncated": len(items) > _AGENT_LIMIT_UP_ROWS,
        "rows": rows,
    }
