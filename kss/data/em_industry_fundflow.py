"""东财行业资金流 —— Tushare ``moneyflow_ind_dc`` 的 HTTP 兜底.

两条口，按目标日是否为上海日历「今天」分流（2026-09 探针核对）：

1. **当日**（17:30 cron）：``push2delay.eastmoney.com`` clist ``m:90 t:2``，
   ~496 行东财行业全量，字段与 Tushare ``content_type=='行业'`` 同口径。
   ``push2.eastmoney.com`` / ``push2his`` 本机不通，不要改走那两条。
2. **隔日回填**：``datacenter-web`` ``RPT_INDUSTRY_FUNDFLOW``，可按
   ``TRADE_DATE`` 过滤，但是 **~128 行粗板块**（是 496 细分层的子集，
   没有「半导体设备」这一档）。数字与 Tushare 重叠名几乎逐字段相等。

返回列对齐 Tushare ``moneyflow_ind_dc`` 行业子集：``trade_date`` /
``content_type`` / ``ts_code`` / ``name`` / ``pct_change`` / ``close`` /
``net_amount`` / ``net_amount_rate`` / ``buy_elg_amount`` /
``buy_elg_amount_rate``；另加 ``em_source``（``em_push2delay`` 或
``em_datacenter``）标明哪条口，调用方不得把 128 行假装成细分全量.

数据层契约对齐 :mod:`kss.data.dragon_tiger_client`：网络错 / 空响应 /
必需列缺失 / 非法日期 → ``None`` + warning，不外抛。默认 session
``trust_env=False``，不走 macOS Clash 系统代理。
"""

from __future__ import annotations

import logging
import math
import time
from datetime import datetime
from typing import Any, Final
from zoneinfo import ZoneInfo

import pandas as pd
import requests

logger = logging.getLogger(__name__)

SOURCE_PUSH2DELAY: Final[str] = "em_push2delay"
SOURCE_DATACENTER: Final[str] = "em_datacenter"

_DC_URL: Final[str] = "https://datacenter-web.eastmoney.com/api/data/v1/get"
_CLIST_URL: Final[str] = "https://push2delay.eastmoney.com/api/qt/clist/get"
_HEADERS: Final[dict[str, str]] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "Chrome/117.0.0.0 Safari/537.36"
    ),
    "Referer": "https://data.eastmoney.com/",
}
_TIMEOUT_SECONDS: Final[float] = 12.0
_MAX_ATTEMPTS: Final[int] = 2
_BACKOFF_BASE_SECONDS: Final[float] = 1.0
_CLIST_PAGE_SIZE: Final[int] = 100
_CLIST_MAX_PAGES: Final[int] = 8
_DC_PAGE_SIZE: Final[int] = 500
_DC_MAX_PAGES: Final[int] = 4
_SHANGHAI: Final[ZoneInfo] = ZoneInfo("Asia/Shanghai")

_REQUIRED: Final[tuple[str, ...]] = (
    "name",
    "pct_change",
    "net_amount_rate",
    "buy_elg_amount_rate",
)

_CLIST_FIELDS: Final[str] = (
    "f12,f14,f2,f3,f62,f184,f66,f69,f72,f75,f78,f81,f84,f87,f204,f205,f124"
)


def shanghai_today_ymd() -> str:
    """上海日历当天 ``YYYYMMDD``（与 launchd 复盘时区一致）."""
    return datetime.now(_SHANGHAI).strftime("%Y%m%d")


def fetch_industry_fundflow_em(
    trade_date: str,
    *,
    session: requests.Session | None = None,
    now_ymd: str | None = None,
) -> pd.DataFrame | None:
    """拉取东财行业资金流，映射成 Tushare ``moneyflow_ind_dc`` 行业列.

    Args:
        trade_date: 目标交易日，``YYYYMMDD``.
        session: 可注入的 :class:`requests.Session`（测试 mock）；``None``
            时新建直连 session（``trust_env=False``）.
        now_ymd: 覆盖「今天」判定，测试用；``None`` 取上海当天.

    Returns:
        行业资金流 DataFrame；失败 → ``None``.
        当日优先 ``push2delay`` 全量，失败再降到 datacenter 粗表；
        隔日只走 datacenter.
    """
    try:
        datetime.strptime(trade_date, "%Y%m%d")
    except ValueError:
        logger.warning("[em_ind] trade_date 格式非法: %r", trade_date)
        return None

    sess = session or _direct_session()
    today = now_ymd if now_ymd is not None else shanghai_today_ymd()
    if trade_date == today:
        live = _fetch_push2delay(sess, trade_date)
        if live is not None:
            return live
        logger.warning(
            "[em_ind] %s push2delay 失败，降到 datacenter 粗板块",
            trade_date,
        )
    return _fetch_datacenter(sess, trade_date)


def _direct_session() -> requests.Session:
    sess = requests.Session()
    sess.trust_env = False
    sess.proxies = {"http": None, "https": None}  # type: ignore[dict-item]
    sess.headers.update(_HEADERS)
    return sess


def _get_json(
    sess: requests.Session,
    url: str,
    params: dict[str, str],
    *,
    label: str,
) -> dict[str, Any] | None:
    last_exc: Exception | None = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            r = sess.get(
                url, params=params, headers=_HEADERS, timeout=_TIMEOUT_SECONDS
            )
            r.raise_for_status()
            data = r.json()
            if isinstance(data, dict):
                return data
            logger.warning("[em_ind] %s 响应非 JSON 对象: %r", label, type(data))
            return None
        except (requests.RequestException, ValueError) as exc:
            last_exc = exc
            if attempt >= _MAX_ATTEMPTS:
                logger.warning(
                    "[em_ind] %s 最终失败（已重试 %d 次）: %s",
                    label, _MAX_ATTEMPTS - 1, exc,
                )
                return None
            wait = _BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
            logger.info(
                "[em_ind] %s 第 %d 次失败: %s；%.1fs 后重试",
                label, attempt, exc, wait,
            )
            time.sleep(wait)
    if last_exc is not None:
        logger.warning("[em_ind] %s 失败: %s", label, last_exc)
    return None


def _fetch_push2delay(
    sess: requests.Session, trade_date: str
) -> pd.DataFrame | None:
    params = {
        "pn": "1",
        "pz": str(_CLIST_PAGE_SIZE),
        "po": "1",
        "np": "1",
        "ut": "b2884a393a59ad64002292a3e90d46a5",
        "fltt": "2",
        "invt": "2",
        "fid0": "f62",
        "fs": "m:90 t:2",
        "stat": "1",
        "fields": _CLIST_FIELDS,
    }
    first = _get_json(sess, _CLIST_URL, params, label=f"push2delay {trade_date}")
    inner = (first or {}).get("data") if first else None
    if not isinstance(inner, dict):
        return None
    diff = inner.get("diff")
    if not isinstance(diff, list) or not diff:
        logger.info("[em_ind] %s push2delay 返回空", trade_date)
        return None
    rows: list[dict[str, Any]] = list(diff)
    total = inner.get("total")
    try:
        n_total = int(total) if total is not None else len(rows)
    except (TypeError, ValueError):
        n_total = len(rows)
    n_pages = max(1, math.ceil(n_total / _CLIST_PAGE_SIZE))
    n_pages = min(n_pages, _CLIST_MAX_PAGES)
    for page in range(2, n_pages + 1):
        params["pn"] = str(page)
        payload = _get_json(
            sess, _CLIST_URL, params, label=f"push2delay {trade_date} p{page}"
        )
        nxt = (payload or {}).get("data") if payload else None
        page_diff = nxt.get("diff") if isinstance(nxt, dict) else None
        if not isinstance(page_diff, list) or not page_diff:
            logger.warning("[em_ind] %s push2delay 第 %d 页空，中止翻页", trade_date, page)
            break
        rows.extend(page_diff)
        time.sleep(0.2)
    df = _normalize_clist(rows, trade_date)
    if df is None:
        return None
    logger.info(
        "[em_ind] %s push2delay 行业 %d 行（全量当日）",
        trade_date, len(df),
    )
    return df


def _fetch_datacenter(
    sess: requests.Session, trade_date: str
) -> pd.DataFrame | None:
    date_iso = datetime.strptime(trade_date, "%Y%m%d").strftime("%Y-%m-%d")
    params = {
        "reportName": "RPT_INDUSTRY_FUNDFLOW",
        "columns": "ALL",
        "filter": f"(TRADE_DATE='{date_iso}')",
        "pageNumber": "1",
        "pageSize": str(_DC_PAGE_SIZE),
        "sortColumns": "NET_INFLOW",
        "sortTypes": "-1",
        "source": "WEB",
        "client": "WEB",
    }
    first = _get_json(
        sess, _DC_URL, params, label=f"datacenter {trade_date}"
    )
    if first is None:
        return None
    result = first.get("result")
    raw_rows = result.get("data") if isinstance(result, dict) else None
    if not isinstance(raw_rows, list) or not raw_rows:
        logger.info("[em_ind] %s datacenter 返回空（疑似非交易日）", trade_date)
        return None
    rows: list[dict[str, Any]] = list(raw_rows)
    try:
        pages = int((result or {}).get("pages") or 1)
    except (TypeError, ValueError):
        pages = 1
    pages = min(max(pages, 1), _DC_MAX_PAGES)
    for page in range(2, pages + 1):
        params["pageNumber"] = str(page)
        payload = _get_json(
            sess, _DC_URL, params, label=f"datacenter {trade_date} p{page}"
        )
        nxt = (payload or {}).get("result") if payload else None
        extra = nxt.get("data") if isinstance(nxt, dict) else None
        if not isinstance(extra, list) or not extra:
            break
        rows.extend(extra)
        time.sleep(0.2)
    df = _normalize_datacenter(rows, trade_date)
    if df is None:
        return None
    logger.warning(
        "[em_ind] %s datacenter 粗板块 %d 行（非东财细分全量，缺「半导体设备」等 L3）",
        trade_date, len(df),
    )
    return df


def _ts_code(board: object) -> str:
    code = str(board or "").strip()
    if not code:
        return ""
    return code if code.endswith(".DC") else f"{code}.DC"


def _normalize_clist(
    rows: list[dict[str, Any]], trade_date: str
) -> pd.DataFrame | None:
    records: list[dict[str, Any]] = []
    for row in rows:
        records.append({
            "trade_date": trade_date,
            "content_type": "行业",
            "ts_code": _ts_code(row.get("f12")),
            "name": row.get("f14"),
            "pct_change": row.get("f3"),
            "close": row.get("f2"),
            "net_amount": row.get("f62"),
            "net_amount_rate": row.get("f184"),
            "buy_elg_amount": row.get("f66"),
            "buy_elg_amount_rate": row.get("f69"),
            "em_source": SOURCE_PUSH2DELAY,
        })
    return _finalize(pd.DataFrame(records), trade_date, SOURCE_PUSH2DELAY)


def _normalize_datacenter(
    rows: list[dict[str, Any]], trade_date: str
) -> pd.DataFrame | None:
    records: list[dict[str, Any]] = []
    for row in rows:
        records.append({
            "trade_date": trade_date,
            "content_type": "行业",
            "ts_code": _ts_code(row.get("BOARD_CODE")),
            "name": row.get("BOARD_NAME"),
            "pct_change": row.get("CHANGE_RATE"),
            "close": pd.NA,
            "net_amount": row.get("NET_INFLOW"),
            "net_amount_rate": row.get("NET_INFLOW_RATIO"),
            "buy_elg_amount": row.get("SUPERDEAL_NET"),
            "buy_elg_amount_rate": row.get("SUPERDEAL_NET_RATIO"),
            "em_source": SOURCE_DATACENTER,
        })
    return _finalize(pd.DataFrame(records), trade_date, SOURCE_DATACENTER)


def _finalize(
    df: pd.DataFrame, trade_date: str, source: str
) -> pd.DataFrame | None:
    if df.empty:
        logger.info("[em_ind] %s %s 规范化后为空", trade_date, source)
        return None
    if any(c not in df.columns for c in _REQUIRED):
        logger.warning(
            "[em_ind] %s %s 必需列缺失（需 %s），cols=%s",
            trade_date, source, _REQUIRED, list(df.columns),
        )
        return None
    df = df.dropna(subset=["name"]).copy()
    df["name"] = df["name"].astype(str).str.strip()
    df = df[df["name"] != ""].copy()
    numeric_cols = (
        "pct_change",
        "close",
        "net_amount",
        "net_amount_rate",
        "buy_elg_amount",
        "buy_elg_amount_rate",
    )
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if df["name"].duplicated().any():
        df = df.drop_duplicates(subset=["name"], keep="first")
    # 缺净流入率的行对热度评分无用
    df = df.dropna(subset=["pct_change", "net_amount_rate"]).reset_index(drop=True)
    if df.empty:
        logger.warning("[em_ind] %s %s 数值列全空", trade_date, source)
        return None
    return df
