"""影子纸交易分轨 U4."""

from __future__ import annotations

from pathlib import Path

from kss.storage import paper_trade as formal
from kss.storage import paper_trade_shadow as shadow


def test_shadow_write_read(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    payload = {
        "prediction_date": "2026-07-30",
        "strategy_id": "style_short_reversal",
        "top_n": 2,
        "picks": [
            {
                "symbol": "600000.SH",
                "factor_value": -0.05,
                "rank_pct": 0.9,
                "rank_position": 1,
                "planned_weight": 0.5,
                "selection_reason": "r1",
            },
            {
                "symbol": "600001.SH",
                "factor_value": -0.04,
                "rank_pct": 0.8,
                "rank_position": 2,
                "planned_weight": 0.5,
                "selection_reason": "r2",
            },
        ],
    }
    shadow.write_style_day(payload, db_path=db)
    got = shadow.read_style_day("2026-07-30", "style_short_reversal", db_path=db)
    assert got is not None
    assert len(got["picks"]) == 2
    assert got["picks"][0]["symbol"] == "600000.SH"
    assert shadow.day_exists("2026-07-30", "style_short_reversal", db_path=db)


def test_formal_and_shadow_isolated(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    formal.write_day(
        {
            "prediction_date": "2026-07-30",
            "generated_at": "t",
            "strategy": "log_mv_reverse",
            "top_pct": 0.2,
            "top_n": 1,
            "picks": [
                {
                    "symbol": "688000.SH",
                    "factor_value": 1.0,
                    "rank_pct": 0.1,
                    "rank_position": 1,
                    "planned_weight": 1.0,
                }
            ],
        },
        db_path=db,
    )
    shadow.write_style_day(
        {
            "prediction_date": "2026-07-30",
            "strategy_id": "style_value",
            "top_n": 1,
            "picks": [
                {
                    "symbol": "600000.SH",
                    "factor_value": 0.5,
                    "rank_pct": 0.9,
                    "rank_position": 1,
                    "planned_weight": 1.0,
                }
            ],
        },
        db_path=db,
    )
    fday = formal.read_day("2026-07-30", db_path=db)
    assert fday is not None
    assert fday["strategy"] == "log_mv_reverse"
    assert fday["picks"][0]["symbol"] == "688000.SH"
    sday = shadow.read_style_day("2026-07-30", "style_value", db_path=db)
    assert sday is not None
    assert sday["picks"][0]["symbol"] == "600000.SH"
    # formal API 不会扫到影子票
    assert all(p["symbol"] != "600000.SH" for p in fday["picks"])
