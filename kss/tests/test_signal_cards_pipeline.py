"""U6: pipeline 部分失败隔离 + 可复算。"""

from __future__ import annotations

from pathlib import Path

from kss.signal_cards.pipeline import GENERATORS, build_for_date
from kss.storage import etf_radar as etf_store
from kss.storage.signal_cards import read_by_date


def test_partial_failure_continues(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    etf_store.write_snapshot(
        {
            "trade_date": "20260717",
            "data_date": "20260717",
            "stale": False,
            "themes": {
                "芯片": {
                    "flow_5d": -3.0,
                    "flow_1d": -1.0,
                    "past5_ret": 1.0,
                    "divergence": False,
                    "accel": False,
                    "n_funds": 1,
                    "rank_5d": 1,
                }
            },
        },
        db_path=db,
    )

    def boom(_date: str, **_kw):
        raise RuntimeError("boom")

    gens = [("etf_flow", GENERATORS[0][1]), ("broken", boom)]
    result = build_for_date(
        "20260717", db_path=db, write=True, generators=gens  # type: ignore[arg-type]
    )
    assert result.failed_generators
    assert any(f["generator"] == "broken" for f in result.failed_generators)
    assert any(c["card_type"] == "etf_flow" for c in result.cards)


def test_recomputable(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    etf_store.write_snapshot(
        {
            "trade_date": "20260717",
            "data_date": "20260717",
            "stale": False,
            "themes": {
                "芯片": {
                    "flow_5d": -3.0,
                    "flow_1d": -1.0,
                    "past5_ret": 1.0,
                    "divergence": False,
                    "accel": False,
                    "n_funds": 1,
                    "rank_5d": 1,
                }
            },
        },
        db_path=db,
    )
    gens = [("etf_flow", GENERATORS[0][1])]
    r1 = build_for_date("20260717", db_path=db, write=True, generators=gens)
    r2 = build_for_date("20260717", db_path=db, write=True, generators=gens)
    ids1 = sorted(c["card_id"] for c in r1.cards)
    ids2 = sorted(c["card_id"] for c in r2.cards)
    assert ids1 == ids2
    stored = read_by_date("20260717", db_path=db)
    assert sorted(c["card_id"] for c in stored) == ids1
