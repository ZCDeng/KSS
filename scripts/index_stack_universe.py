"""今日看盘第二行：三列指数堆叠名单（产品锁定）。"""
from __future__ import annotations

from typing import TypedDict


class StackItem(TypedDict):
    code: str
    name: str
    kind: str  # index_daily | index_global | yfinance


class StackCol(TypedDict):
    id: str
    items: list[StackItem]


# 列1 主板/北证 · 列2 成长 · 列3 港股（无纳指）
INDEX_STACKS: tuple[StackCol, ...] = (
    {
        "id": "main",
        "items": [
            {"code": "000001.SH", "name": "上证指数", "kind": "index_daily"},
            {"code": "399001.SZ", "name": "深证成指", "kind": "index_daily"},
            {"code": "899050.BJ", "name": "北证50", "kind": "index_daily"},
        ],
    },
    {
        "id": "growth",
        "items": [
            {"code": "000680.SH", "name": "科创综指", "kind": "index_daily"},
            {"code": "399006.SZ", "name": "创业板指", "kind": "index_daily"},
        ],
    },
    {
        "id": "hk",
        "items": [
            {"code": "HSI", "name": "恒生指数", "kind": "index_global"},
            {"code": "HSTECH", "name": "恒生科技", "kind": "yfinance"},  # yf: ^HSTECH
        ],
    },
)

# yfinance 代码映射（strip code → ticker）。恒生科技指数 Yahoo 常无稳定代码；
# 用恒生科技 ETF 02828.HK 作价源代理，展示名仍为「恒生科技」。
YF_TICKERS: dict[str, str] = {
    "HSTECH": "2828.HK",
}


def next_stack_index(current: int, count: int) -> int:
    """轮播下一页；count==0 时返回 0。"""
    if count <= 0:
        return 0
    return (current + 1) % count
