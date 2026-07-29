"""主题龙头卡：theme_registry + crossSourceSignals 的 demonBoard/mainline 推导。"""

from __future__ import annotations

from typing import Any

from kss.sector.themes import load_themes
from kss.signal_cards.common import base_card
from kss.storage import sector_rotation as sector_store


def _fuzzy_match(registry_name: str, snapshot_name: str) -> bool:
    """精确失败时：name in snapshot_name or snapshot_name.startswith(name)。"""
    if registry_name == snapshot_name:
        return True
    if registry_name in snapshot_name:
        return True
    if snapshot_name.startswith(registry_name):
        return True
    # 反向：快照名是注册名的前缀（如 光刻机 vs 光刻机概念）
    if snapshot_name in registry_name:
        return True
    return False


def _board_lookup(snap: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """板块名 → board dict（concepts + industries）。"""
    out: dict[str, dict[str, Any]] = {}
    for key in ("concepts", "industries"):
        for item in snap.get(key) or []:
            if isinstance(item, dict) and item.get("name"):
                out[str(item["name"])] = item
    return out


def _match_boards(
    names: list[str], snap_names: set[str], board_by_name: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for reg_name in names:
        for snap_name in snap_names:
            if _fuzzy_match(reg_name, snap_name):
                board = board_by_name.get(snap_name)
                if board is not None:
                    hits.append(board)
                break
    return hits


def generate_for_date(
    trade_date: str, *, db_path: Any = None, themes_path: Any = None
) -> list[dict[str, Any]]:
    snap = sector_store.read_by_date(trade_date, db_path=db_path)
    if snap is None:
        return []

    td = str(snap.get("tradeDate") or trade_date)
    css = snap.get("crossSourceSignals") or {}
    demon = set(css.get("demonBoard") or [])
    mainline = set(css.get("mainline") or [])
    if not demon and not mainline:
        return []

    board_by_name = _board_lookup(snap)
    themes = load_themes(themes_path)
    cards: list[dict[str, Any]] = []

    for theme_name, bucket in themes.items():
        mapped = list(bucket.concepts) + list(bucket.industries)
        demon_hits = _match_boards(mapped, demon, board_by_name)
        main_hits = _match_boards(mapped, mainline, board_by_name)
        if not demon_hits and not main_hits:
            continue

        all_hits = demon_hits + main_hits
        # 排名最高 = todayRank 最小
        def _rank(b: dict[str, Any]) -> int:
            r = b.get("todayRank")
            try:
                return int(r) if r is not None else 10**9
            except (TypeError, ValueError):
                return 10**9

        top = min(all_hits, key=_rank)
        metrics = {
            "leader_board": top.get("name"),
            "heatScore": top.get("heatScore"),
            "pctChange": top.get("pctChange"),
            "rankJump": top.get("rankJump"),
            "todayRank": top.get("todayRank"),
            "demon_hit_count": len(demon_hits),
            "mainline_hit_count": len(main_hits),
            "demon_boards": [b.get("name") for b in demon_hits],
            "mainline_boards": [b.get("name") for b in main_hits],
        }
        cards.append(
            base_card(
                card_type="theme_leader",
                trade_date=td,
                subject=str(theme_name),
                rule_id="theme_leader_demon_or_mainline",
                metrics=metrics,
                threshold_source="derived",
                coverage="covered",
                data_as_of=td,
                direction=None,
            )
        )
    return cards
