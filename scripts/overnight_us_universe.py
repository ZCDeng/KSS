"""隔夜美股固定名单 + 纯合并（可单测）。

产品：今日看盘「隔夜美股」跑马灯；顺序锁定；SpaceX 不在列。
"""
from __future__ import annotations

from typing import Any, Iterable, Literal, TypedDict

Kind = Literal["index_global", "yfinance"]


class UniverseRow(TypedDict):
    code: str
    name: str
    kind: Kind


# 顺序 = 产品 KD4（去掉 SpaceX）
OVERNIGHT_US_UNIVERSE: tuple[UniverseRow, ...] = (
    {"code": "MCHI", "name": "MSCI中国指数ETF", "kind": "yfinance"},
    {"code": "IXIC", "name": "纳斯达克综合指数", "kind": "index_global"},
    {"code": "DJI", "name": "道琼斯指数", "kind": "index_global"},
    {"code": "XIN9", "name": "富时中国A50指数", "kind": "index_global"},
    {"code": "ROBO", "name": "ROBO全球机器人", "kind": "yfinance"},
    {"code": "BOTZ", "name": "GX机器人与AI", "kind": "yfinance"},
    {"code": "NVDA", "name": "英伟达", "kind": "yfinance"},
    {"code": "SOXX", "name": "半导体ETF-iShares", "kind": "yfinance"},
    {"code": "SMH", "name": "半导体ETF-VanEck", "kind": "yfinance"},
    {"code": "TSLA", "name": "特斯拉", "kind": "yfinance"},
    {"code": "MU", "name": "美光科技", "kind": "yfinance"},
    {"code": "AVGO", "name": "博通", "kind": "yfinance"},
)


def merge_overnight_quotes(
    fetched: Iterable[dict[str, Any]],
    *,
    universe: tuple[UniverseRow, ...] | list[dict[str, Any]] = OVERNIGHT_US_UNIVERSE,
) -> list[dict[str, Any]]:
    """按 universe 顺序合并已成功报价；缺数跳过，不重排。

    每项 fetched 至少含 ``code``；可选 close/pct/date/source/name。
    ``universe`` 可为默认 tuple 或含 code/name/kind 的 list（用户 append 扩展）。
    """
    by_code = {str(x.get("code", "")).upper(): x for x in fetched if x.get("code")}
    out: list[dict[str, Any]] = []
    for row in universe:
        code = str(row["code"]).upper()
        hit = by_code.get(code)
        if not hit:
            continue
        close = hit.get("close")
        pct = hit.get("pct")
        if close is None or pct is None:
            continue
        out.append({
            "code": code,
            "name": hit.get("name") or row.get("name") or code,
            "close": float(close),
            "pct": float(pct),
            "date": hit.get("date") or "",
            "source": hit.get("source") or row.get("kind") or "",
        })
    return out


def universe_from_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """把任意 code/name/kind 行规范为 merge 可用 list。"""
    out: list[dict[str, Any]] = []
    for row in rows:
        code = str(row.get("code") or "").upper()
        if not code:
            continue
        out.append({
            "code": code,
            "name": str(row.get("name") or code),
            "kind": str(row.get("kind") or "yfinance"),
        })
    return out
