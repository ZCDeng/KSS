"""东财科创板融资融券明细（datacenter-web 无鉴权 HTTP）.

数据源：``datacenter-web.eastmoney.com/api/data/v1/get``，reportName
``RPTA_WEB_RZRQ_GGMX``，按 ``(DATE='...')(KCB="1")`` 过滤当日全科创板个股两融。
KSS 用途：把科创板整体的融资余额 + 融资净买入聚合进板块复盘，给 LLM 一份
「科创板今天在加杠杆还是降杠杆」的杠杆情绪，补 Tushare 概念资金流没有两融
维度的盲点.

设计纪律（对齐 :mod:`kss.data.dragon_tiger_client`）：

- 数据层契约：网络错 / HTTP 错 / 非交易日空响应 / 必需列缺失 → 返回 ``None`` +
  warning，不外抛.
- 反封：东财限流。科创板单日约 600 只，pageSize 封顶 500 → 2 页；串行翻页 +
  退避，板块复盘每日仅调一次，不并发.
- 字段重命名为蛇形 + 项目口径（``fin_balance`` / ``fin_net_buy`` 单位元）.
- ⚠️ 首次接入已用真实响应（20260612，科创板 597 只）核对字段名；东财 reportName
  字段可能随改版漂移，缺必需列即降级.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Final

import pandas as pd
import requests

logger = logging.getLogger(__name__)

_URL: Final[str] = "https://datacenter-web.eastmoney.com/api/data/v1/get"
_HEADERS: Final[dict[str, str]] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "Chrome/117.0.0.0 Safari/537.36"
    ),
    "Referer": "https://data.eastmoney.com/",
}
_TIMEOUT_SECONDS: Final[float] = 15.0
_MAX_ATTEMPTS: Final[int] = 2
_BACKOFF_BASE_SECONDS: Final[float] = 1.0
_PAGE_SIZE: Final[int] = 500
_MAX_PAGES: Final[int] = 6  # 科创板 ~600 只 / 500 = 2 页；留余量防扩容，封顶防失控

# ⚠️ 左侧为东财原始列名，首次接入用真实响应核对.
_COLUMN_RENAME: Final[dict[str, str]] = {
    "SCODE": "code",
    "SECNAME": "name",
    "RZYE": "fin_balance",    # 融资余额，单位元
    "RZJME": "fin_net_buy",   # 融资净买入额，单位元（= 买入 - 偿还）
}
_REQUIRED: Final[tuple[str, ...]] = ("code", "fin_balance", "fin_net_buy")


def _fetch_page(
    sess: requests.Session, date_iso: str, page: int,
) -> dict | None:
    """拉单页；失败重试到上限 → None."""
    params = {
        "reportName": "RPTA_WEB_RZRQ_GGMX",
        "columns": "SCODE,SECNAME,RZYE,RZJME,KCB,DATE",
        "filter": f"(DATE='{date_iso}')(KCB=\"1\")",
        "pageNumber": str(page),
        "pageSize": str(_PAGE_SIZE),
        "sortColumns": "RZYE",
        "sortTypes": "-1",
        "source": "WEB",
        "client": "WEB",
    }
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            r = sess.get(
                _URL, params=params, headers=_HEADERS, timeout=_TIMEOUT_SECONDS
            )
            r.raise_for_status()
            data = r.json()
            return data if isinstance(data, dict) else None
        except (requests.RequestException, ValueError) as exc:
            if attempt >= _MAX_ATTEMPTS:
                logger.warning(
                    "[margin] 第 %d 页最终失败（已重试 %d 次）: %s",
                    page, _MAX_ATTEMPTS - 1, exc,
                )
                return None
            wait = _BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
            logger.info("[margin] 第 %d 页第 %d 次失败: %s；%.1fs 后重试",
                        page, attempt, exc, wait)
            time.sleep(wait)
    return None


def fetch_kcb_margin(
    trade_date: str,
    *,
    session: requests.Session | None = None,
) -> pd.DataFrame | None:
    """拉取东财当日全科创板个股融资融券明细（翻页合并）.

    Args:
        trade_date: 目标交易日，``YYYYMMDD``（例 ``"20260612"``）.
        session: 复用的 :class:`requests.Session`，便于测试注入.

    Returns:
        DataFrame，列：``code`` / ``name`` / ``fin_balance``(元) /
        ``fin_net_buy``(元)，按融资余额降序.
        失败（网络错、非交易日空响应、必需列缺失、非法日期）→ ``None``.
    """
    try:
        date_iso = datetime.strptime(trade_date, "%Y%m%d").strftime("%Y-%m-%d")
    except ValueError:
        logger.warning("[margin] trade_date 格式非法: %r", trade_date)
        return None

    sess = session or requests.Session()

    first = _fetch_page(sess, date_iso, 1)
    if not isinstance(first, dict):
        return None
    result = first.get("result")
    if not isinstance(result, dict) or not result.get("data"):
        logger.info("[margin] %s 首页空（疑似非交易日）", trade_date)
        return None

    all_rows: list[dict] = list(result["data"])
    total_pages = int(result.get("pages") or 1)
    for page in range(2, min(total_pages, _MAX_PAGES) + 1):
        d = _fetch_page(sess, date_iso, page)
        rows = (d.get("result") or {}).get("data") if isinstance(d, dict) else None
        if rows:
            all_rows.extend(rows)
    if total_pages > _MAX_PAGES:
        logger.warning(
            "[margin] %s 科创板 %d 页超 _MAX_PAGES=%d，仅取前 %d 页",
            trade_date, total_pages, _MAX_PAGES, _MAX_PAGES,
        )

    raw_df = pd.DataFrame(all_rows)
    keep = [c for c in _COLUMN_RENAME if c in raw_df.columns]
    df = raw_df[keep].rename(columns=_COLUMN_RENAME)
    if any(c not in df.columns for c in _REQUIRED):
        logger.warning(
            "[margin] %s 必需列缺失（需 %s），原始 cols=%s",
            trade_date, _REQUIRED, list(raw_df.columns),
        )
        return None

    for col in ("fin_balance", "fin_net_buy"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["fin_balance"]).reset_index(drop=True)
    if df.empty:
        return None
    df = df.sort_values("fin_balance", ascending=False).reset_index(drop=True)
    return df


if __name__ == "__main__":  # pragma: no cover
    import sys
    logging.basicConfig(level=logging.INFO)
    date_arg = sys.argv[1] if len(sys.argv) > 1 else "20260612"
    out = fetch_kcb_margin(date_arg)
    if out is None:
        print(f"[自检] {date_arg} 返回 None")
    else:
        bal = out["fin_balance"].sum() / 1e8
        net = out["fin_net_buy"].sum() / 1e8
        print(f"[自检] {date_arg} 科创板 {len(out)} 只，"
              f"融资余额合计 {bal:.0f} 亿，净买入合计 {net:+.2f} 亿")
        print(out.head(3).to_string(index=False))
