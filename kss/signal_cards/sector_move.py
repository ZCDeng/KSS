"""板块异动卡。双门槛（涨跌幅 + 排名）+ 日上限；threshold_source=convention（未经回测）。

注意：板块轮动已重标为「妖板情绪」价格面雷达，不得表述为资金流/申赎真值。
"""

from __future__ import annotations

from typing import Any

from kss.signal_cards.common import base_card
from kss.storage import sector_rotation as sector_store

# 未经回测，threshold_source=convention
PCT_CHANGE_TH = 3.0  # |pctChange| >= 此值
RANK_CAP = 15  # todayRank <= 此值（1=最热）
MAX_CARDS_PER_DAY = 30  # 硬上限，防单日爆炸


def _iter_boards(snap: dict[str, Any]) -> list[dict[str, Any]]:
    boards: list[dict[str, Any]] = []
    for key in ("concepts", "industries"):
        for item in snap.get(key) or []:
            if isinstance(item, dict) and item.get("name"):
                boards.append(item)
    return boards


def generate_for_date(
    trade_date: str, *, db_path: Any = None
) -> list[dict[str, Any]]:
    snap = sector_store.read_by_date(trade_date, db_path=db_path)
    if snap is None:
        return []

    td = str(snap.get("tradeDate") or trade_date)
    candidates: list[tuple[float, dict[str, Any]]] = []
    for board in _iter_boards(snap):
        pct = board.get("pctChange")
        rank = board.get("todayRank")
        if pct is None or rank is None:
            continue
        try:
            pct_f = float(pct)
            rank_i = int(rank)
        except (TypeError, ValueError):
            continue
        # 双门槛：同时满足
        if abs(pct_f) < PCT_CHANGE_TH:
            continue
        if rank_i > RANK_CAP:
            continue
        # 排序键：|pct| 降序，同 rank 升序
        candidates.append((-abs(pct_f), rank_i, board, pct_f, rank_i))

    candidates.sort(key=lambda x: (x[0], x[1]))
    cards: list[dict[str, Any]] = []
    for _, _, board, pct_f, rank_i in candidates[:MAX_CARDS_PER_DAY]:
        name = str(board["name"])
        metrics = {
            "pctChange": pct_f,
            "todayRank": rank_i,
            "previousRank": board.get("previousRank"),
            "rankJump": board.get("rankJump"),
            "heatScore": board.get("heatScore"),
            "source": board.get("source"),
            "classification": board.get("classification"),
            "pct_change_th": PCT_CHANGE_TH,
            "rank_cap": RANK_CAP,
            # 明确非资金流
            "signal_domain": "price_heat",
        }
        cards.append(
            base_card(
                card_type="sector_move",
                trade_date=td,
                subject=name,
                rule_id="sector_move_dual_threshold",
                metrics=metrics,
                threshold_source="convention",
                coverage="covered",
                data_as_of=td,
                direction=None,
            )
        )
    return cards
