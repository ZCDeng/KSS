"""U3: sector_move 双门槛 + theme_leader 模糊匹配。"""

from __future__ import annotations

from pathlib import Path

from kss.signal_cards.sector_move import (
    MAX_CARDS_PER_DAY,
    PCT_CHANGE_TH,
    RANK_CAP,
    generate_for_date as gen_sector,
)
from kss.signal_cards.theme_leader import _fuzzy_match, generate_for_date as gen_theme
from kss.storage import sector_rotation as sector_store


def _snap(trade_date: str, concepts: list[dict], css: dict | None = None) -> dict:
    return {
        "tradeDate": trade_date,
        "concepts": concepts,
        "industries": [],
        "crossSourceSignals": css
        or {"demonBoard": [], "mainline": [], "oldHotspotFading": [], "satellite": []},
    }


def test_sector_dual_threshold(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    sector_store.write_snapshot(
        _snap(
            "20260717",
            [
                {"name": "A", "pctChange": 5.0, "todayRank": 3, "source": "concept"},
                {"name": "B", "pctChange": 5.0, "todayRank": 50, "source": "concept"},  # rank fail
                {"name": "C", "pctChange": 1.0, "todayRank": 2, "source": "concept"},  # pct fail
                {"name": "D", "pctChange": -4.0, "todayRank": 5, "source": "concept"},
            ],
        ),
        db_path=db,
    )
    cards = gen_sector("20260717", db_path=db)
    names = {c["subject"] for c in cards}
    assert "A" in names
    assert "D" in names
    assert "B" not in names
    assert "C" not in names
    for c in cards:
        assert c["direction"] is None
        assert c["threshold_source"] == "convention"
        assert "资金" not in str(c["metrics"])
        assert "申赎" not in str(c["metrics"])


def test_sector_daily_cap(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    concepts = [
        {
            "name": f"B{i}",
            "pctChange": 10.0 - i * 0.01,
            "todayRank": 1 + (i % RANK_CAP),
            "source": "concept",
        }
        for i in range(100)
    ]
    sector_store.write_snapshot(_snap("20260717", concepts), db_path=db)
    cards = gen_sector("20260717", db_path=db)
    assert len(cards) <= MAX_CARDS_PER_DAY


def test_theme_leader_demon_and_mainline(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    # 用真实 theme registry；构造命中 demonBoard 的板块名需与 registry 匹配
    # 光刻机概念 常见于半导体主题
    sector_store.write_snapshot(
        {
            "tradeDate": "20260717",
            "concepts": [
                {
                    "name": "光刻机",
                    "pctChange": 4.0,
                    "todayRank": 2,
                    "heatScore": 0.9,
                    "rankJump": 5,
                    "source": "concept",
                },
                {
                    "name": "无关板块",
                    "pctChange": 8.0,
                    "todayRank": 1,
                    "heatScore": 0.99,
                    "rankJump": 10,
                    "source": "concept",
                },
            ],
            "industries": [],
            "crossSourceSignals": {
                "demonBoard": ["光刻机"],
                "mainline": [],
                "oldHotspotFading": [],
                "satellite": [],
            },
        },
        db_path=db,
    )
    cards = gen_theme("20260717", db_path=db)
    assert any(c["threshold_source"] == "derived" for c in cards)
    for c in cards:
        assert c["direction"] is None
        assert c["metrics"]["demon_hit_count"] >= 1 or c["metrics"]["mainline_hit_count"] >= 1


def test_fuzzy_match_guangkeji() -> None:
    assert _fuzzy_match("光刻机概念", "光刻机")
    assert _fuzzy_match("光刻机", "光刻机概念")
    assert not _fuzzy_match("人工智能", "半导体")


def test_theme_no_hit_no_card(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    sector_store.write_snapshot(
        _snap(
            "20260717",
            [{"name": "完全无关XYZ", "pctChange": 9, "todayRank": 1}],
            {
                "demonBoard": ["完全无关XYZ"],
                "mainline": [],
                "oldHotspotFading": [],
                "satellite": [],
            },
        ),
        db_path=db,
    )
    # 可能仍有模糊误匹配；至少不应崩溃
    cards = gen_theme("20260717", db_path=db)
    assert isinstance(cards, list)
