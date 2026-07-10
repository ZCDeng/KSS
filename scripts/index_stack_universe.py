"""今日看盘第二行：三列指数堆叠名单（产品锁定）。"""
from __future__ import annotations

from typing import NotRequired, TypedDict


class StackItem(TypedDict):
    code: str
    name: str
    kind: str  # index_daily | index_global | yfinance
    # 拉数用码（与产品展示码不同时设，如 恒生科技 产品码 HSTECH / Tushare HKTECH）
    fetch_code: NotRequired[str]


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
            # Tushare index_global 真源码为 HKTECH（非 HSTECH）；产品/UI/Longbridge 仍用 HSTECH
            {
                "code": "HSTECH",
                "name": "恒生科技",
                "kind": "index_global",
                "fetch_code": "HKTECH",
            },
        ],
    },
)

# yfinance 后备 ticker（strip 产品码 → yf）。恒生科技指数无稳定 ^ 码时用 ETF 代理分时。
YF_TICKERS: dict[str, str] = {
    "HSTECH": "2828.HK",
}


def next_stack_index(current: int, count: int) -> int:
    """轮播下一页；count==0 时返回 0。"""
    if count <= 0:
        return 0
    return (current + 1) % count
