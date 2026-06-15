"""东财全市场龙虎榜（datacenter-web 无鉴权 HTTP）.

数据源：``datacenter-web.eastmoney.com/api/data/v1/get``，reportName
``RPT_DAILYBILLBOARD_DETAILS``，盘后约 18:00 当日数据齐. KSS 用途是把当日龙虎榜
净买入 + 上榜原因聚合进板块复盘 prompt，给 LLM 一份「今天游资/机构在抢哪些、
为什么上榜」的资金面动因，补 Tushare 概念资金流只有板块聚合、没有席位级动向
的盲点.

设计纪律（对齐 :mod:`kss.data.ths_client`）：

- 数据层契约：网络错 / HTTP 错 / 非交易日空响应 / 必需列缺失 → 返回 ``None`` +
  warning，不外抛.
- 反封：东财限流（每秒 >5 触发封 IP）。本接口板块复盘每日仅调 1 次，串行无并发；
  单次 2 次尝试 + 指数退避即可，不要并发批量调.
- 字段重命名为蛇形 + 项目口径（``net_amount`` 单位元，与 ``moneyflow_ind_dc``
  一致），只保留复盘真正用到的列.
- ⚠️ 首次接入需用一天真实响应核对 ``_COLUMN_RENAME`` 左侧的东财原始列名
  （reportName 字段名可能随东财改版漂移）；核对方式见 ``__main__`` 自检块.
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
_TIMEOUT_SECONDS: Final[float] = 10.0
_MAX_ATTEMPTS: Final[int] = 2
_BACKOFF_BASE_SECONDS: Final[float] = 1.0
_PAGE_SIZE: Final[int] = 500

# ⚠️ 左侧为东财原始列名，首次接入用真实响应核对（见模块 docstring）.
_COLUMN_RENAME: Final[dict[str, str]] = {
    "SECURITY_CODE": "code",
    "SECURITY_NAME_ABBR": "name",
    "BILLBOARD_NET_AMT": "net_amount",   # 龙虎榜净买额，单位元
    "EXPLANATION": "reason",             # 上榜原因（涨幅偏离/换手等）
    "TURNOVERRATE": "turnover_rate",
    "ACCUM_AMOUNT": "amount",
}
# 缺这几列即判失败（与 ths_hot 要求 reason 必存在同理）.
_REQUIRED: Final[tuple[str, ...]] = ("name", "net_amount", "reason")


def fetch_dragon_tiger(
    trade_date: str,
    *,
    session: requests.Session | None = None,
) -> pd.DataFrame | None:
    """拉取东财当日全市场龙虎榜明细.

    Args:
        trade_date: 目标交易日，``YYYYMMDD``（例 ``"20260612"``）.
        session: 复用的 :class:`requests.Session`，便于测试注入与 keep-alive.
            ``None`` 时按需创建一次性 session.

    Returns:
        DataFrame，列：``code`` / ``name`` / ``net_amount``(元) / ``reason`` /
        ``turnover_rate`` / ``amount``，按 ``net_amount`` 降序.
        失败（网络错、非交易日空响应、必需列缺失、非法日期）→ ``None``.
    """
    try:
        date_iso = datetime.strptime(trade_date, "%Y%m%d").strftime("%Y-%m-%d")
    except ValueError:
        logger.warning("[lhb] trade_date 格式非法: %r", trade_date)
        return None

    params = {
        "reportName": "RPT_DAILYBILLBOARD_DETAILS",
        "columns": "ALL",
        "filter": f"(TRADE_DATE='{date_iso}')",
        "pageNumber": "1",
        "pageSize": str(_PAGE_SIZE),
        "sortColumns": "BILLBOARD_NET_AMT",
        "sortTypes": "-1",
        "source": "WEB",
        "client": "WEB",
    }
    sess = session or requests.Session()

    data: object = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            r = sess.get(
                _URL, params=params, headers=_HEADERS, timeout=_TIMEOUT_SECONDS
            )
            r.raise_for_status()
            data = r.json()
            break
        except (requests.RequestException, ValueError) as exc:
            if attempt >= _MAX_ATTEMPTS:
                logger.warning(
                    "[lhb] %s 最终失败（已重试 %d 次）: %s",
                    trade_date, _MAX_ATTEMPTS - 1, exc,
                )
                return None
            wait = _BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
            logger.info(
                "[lhb] %s 第 %d 次失败: %s；%.1fs 后重试",
                trade_date, attempt, exc, wait,
            )
            time.sleep(wait)

    if not isinstance(data, dict):
        logger.warning("[lhb] %s 响应非 JSON 对象: %r", trade_date, type(data))
        return None

    result = data.get("result")
    rows = (result or {}).get("data") if isinstance(result, dict) else None
    if not rows:
        # 东财非交易日 result 为 null（success=true 但无数据）
        logger.info("[lhb] %s 返回空（疑似非交易日）", trade_date)
        return None

    raw_df = pd.DataFrame(rows)
    keep = [c for c in _COLUMN_RENAME if c in raw_df.columns]
    df = raw_df[keep].rename(columns=_COLUMN_RENAME)
    if any(c not in df.columns for c in _REQUIRED):
        logger.warning(
            "[lhb] %s 必需列缺失（需 %s），原始 cols=%s（核对东财字段名）",
            trade_date, _REQUIRED, list(raw_df.columns),
        )
        return None

    for col in ("net_amount", "turnover_rate", "amount"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.sort_values("net_amount", ascending=False).reset_index(drop=True)
    return df


if __name__ == "__main__":  # pragma: no cover
    # 字段核对自检：跑一天真实响应，确认 _COLUMN_RENAME 左侧列名命中.
    import sys

    logging.basicConfig(level=logging.INFO)
    date_arg = sys.argv[1] if len(sys.argv) > 1 else "20260612"
    out = fetch_dragon_tiger(date_arg)
    if out is None:
        print(f"[自检] {date_arg} 返回 None —— 检查日期/字段名/网络")
    else:
        print(f"[自检] {date_arg} 上榜 {len(out)} 行，列={list(out.columns)}")
        print(out.head(5).to_string(index=False))
