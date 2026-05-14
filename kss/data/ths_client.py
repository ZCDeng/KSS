"""同花顺当日强势股归因（zx.10jqka.com.cn 无鉴权 HTTP）.

数据源：``zx.10jqka.com.cn/event/api/getharden``，盘后 15:30 起 ``reason``
字段才完整. KSS 用途是把每只强势股的 ``reason``（如"算力租赁+Token工厂+AI政务"）
聚合到板块复盘 prompt，给 LLM 一份「今天为什么涨」的题材归因，
弥补 Tushare 概念板块只有静态归属、没有当日热点动因的盲点.

设计纪律：

- 数据层契约：失败 / 网络错 / errocode != 0 / 空响应 → 返回 ``None`` + warning，
  不外抛（与 :mod:`kss.data.tushare_client` 一致）.
- 字段重命名为蛇形 + 英文，``zhangfu`` → ``pct_change`` 与项目其他板块数据
  口径一致；只保留 KSS 真正用得到的列，丢弃 ``ddejingliang`` 等冗余.
- 入参 ``trade_date`` 用 ``YYYYMMDD``（与项目约定一致），内部转 ``YYYY-MM-DD``.
- ``errocode`` 拼写非标准但是接口原样，不要纠成 ``errorcode``（来自 SKILL 踩坑文档）.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Final

import pandas as pd
import requests

logger = logging.getLogger(__name__)

_URL_TMPL: Final[str] = (
    "http://zx.10jqka.com.cn/event/api/getharden/"
    "date/{date_iso}/orderby/date/orderway/desc/charset/GBK/"
)
_HEADERS: Final[dict[str, str]] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "Chrome/117.0.0.0 Safari/537.36"
    ),
}
_TIMEOUT_SECONDS: Final[float] = 10.0
_MAX_ATTEMPTS: Final[int] = 2
_BACKOFF_BASE_SECONDS: Final[float] = 1.0

# 输出列重命名（仅保留 KSS 复盘真正用到的字段）
_COLUMN_RENAME: Final[dict[str, str]] = {
    "code": "code",
    "name": "name",
    "reason": "reason",
    "zhangfu": "pct_change",
    "close": "close",
    "huanshou": "turnover_rate",
    "chengjiaoe": "amount",
    "market": "market",
}


def fetch_ths_hot(
    trade_date: str,
    *,
    session: requests.Session | None = None,
) -> pd.DataFrame | None:
    """拉取同花顺当日强势股 + 题材归因.

    Args:
        trade_date: 目标交易日，``YYYYMMDD`` 格式（例 ``"20260513"``）.
        session: 复用的 :class:`requests.Session`，便于测试注入与 keep-alive.
            ``None`` 时按需创建一次性 session.

    Returns:
        DataFrame，列：``code`` / ``name`` / ``reason`` / ``pct_change`` /
        ``close`` / ``turnover_rate`` / ``amount`` / ``market``.
        失败（网络错、errocode!=0、非交易日空响应、字段全异常）→ ``None``.
    """
    try:
        date_iso = datetime.strptime(trade_date, "%Y%m%d").strftime("%Y-%m-%d")
    except ValueError:
        logger.warning("[ths_hot] trade_date 格式非法: %r", trade_date)
        return None

    url = _URL_TMPL.format(date_iso=date_iso)
    sess = session or requests.Session()

    data: object = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            r = sess.get(url, headers=_HEADERS, timeout=_TIMEOUT_SECONDS)
            r.raise_for_status()
            # URL 含 charset/GBK/ —— 强制按 GBK 解码避免 r.json() 拿到乱码
            r.encoding = "gbk"
            data = r.json()
            break
        except (requests.RequestException, ValueError) as exc:
            if attempt >= _MAX_ATTEMPTS:
                logger.warning(
                    "[ths_hot] %s 最终失败（已重试 %d 次）: %s",
                    trade_date, _MAX_ATTEMPTS - 1, exc,
                )
                return None
            wait = _BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
            logger.info(
                "[ths_hot] %s 第 %d 次失败: %s；%.1fs 后重试",
                trade_date, attempt, exc, wait,
            )
            time.sleep(wait)

    if not isinstance(data, dict):
        logger.warning("[ths_hot] %s 响应非 JSON 对象: %r", trade_date, type(data))
        return None

    # errocode 拼写来自上游原样，不要纠成 errorcode
    if data.get("errocode", 0) != 0:
        logger.warning(
            "[ths_hot] %s 业务错误: errocode=%s msg=%r",
            trade_date, data.get("errocode"), data.get("errormsg", ""),
        )
        return None

    rows = data.get("data") or []
    if not rows:
        logger.info("[ths_hot] %s 返回空（疑似非交易日）", trade_date)
        return None

    df = pd.DataFrame(rows)
    keep = [c for c in _COLUMN_RENAME if c in df.columns]
    if not keep or "reason" not in keep:
        logger.warning(
            "[ths_hot] %s 字段缺失（reason 必须存在），原始 cols=%s",
            trade_date, list(df.columns),
        )
        return None
    df = df[keep].rename(columns=_COLUMN_RENAME).reset_index(drop=True)

    for col in ("pct_change", "close", "turnover_rate", "amount"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    return df
