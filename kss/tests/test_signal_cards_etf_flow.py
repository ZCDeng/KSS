"""U2: etf_flow 剂量边界与 stale/divergence/schema-drift。"""

from __future__ import annotations

from pathlib import Path

from kss.signal_cards.etf_flow import dose_bucket, generate_for_date
from kss.storage.db import connect, ensure_schema
from kss.storage import etf_radar as etf_store


def test_dose_boundaries_left_open_right_closed() -> None:
    cases = [
        (-6.0, "flow5d_le_-5", 0.66),
        (-5.0, "flow5d_le_-5", 0.66),
        (-3.0, "flow5d_gt_-5_le_-2", 0.77),
        (-2.0, "flow5d_gt_-5_le_-2", 0.77),
        (-1.0, "flow5d_gt_-2_le_0", 0.71),
        (0.0, "flow5d_gt_-2_le_0", 0.71),
        (1.0, "flow5d_gt_0_le_2", 0.49),
        (2.0, "flow5d_gt_0_le_2", 0.49),
        (3.0, "flow5d_gt_2", 0.50),
    ]
    seen_buckets = set()
    for flow, bucket, wr in cases:
        b, _fwd, win, _dir = dose_bucket(flow)
        assert b == bucket, f"flow={flow} expected {bucket} got {b}"
        assert win == wr
        seen_buckets.add(b)
    # 四边界无重叠：-5/-2/0/+2 各属一档
    assert dose_bucket(-5.0)[0] != dose_bucket(-4.999)[0]
    assert dose_bucket(-2.0)[0] != dose_bucket(-1.999)[0]
    assert dose_bucket(0.0)[0] != dose_bucket(0.001)[0]
    assert dose_bucket(2.0)[0] != dose_bucket(2.001)[0]


def test_direction_implies_win_rate_and_n(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    etf_store.write_snapshot(
        {
            "trade_date": "20260717",
            "data_date": "20260716",
            "stale": False,
            "themes": {
                "芯片": {
                    "flow_1d": -1.0,
                    "flow_5d": -6.0,
                    "past5_ret": 1.0,
                    "grade": "强势确认",
                    "divergence": False,
                    "accel": False,
                    "n_funds": 2,
                    "rank_5d": 1,
                }
            },
            "momentum_regime_r3": {"in_regime": False},
        },
        db_path=db,
    )
    cards = generate_for_date("20260717", db_path=db)
    assert len(cards) == 1
    c = cards[0]
    assert c["direction"] is not None
    assert c["win_rate"] is not None
    assert c["effective_n"] is not None
    assert c["data_as_of"] == "20260716"
    assert c["trade_date"] == "20260717"
    assert c["threshold_source"] == "backtested"


def test_stale_no_direction(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    etf_store.write_snapshot(
        {
            "trade_date": "20260717",
            "data_date": "20260710",
            "stale": True,
            "themes": {
                "芯片": {
                    "flow_5d": -6.0,
                    "flow_1d": -1.0,
                    "past5_ret": 1.0,
                    "divergence": False,
                }
            },
        },
        db_path=db,
    )
    cards = generate_for_date("20260717", db_path=db)
    assert cards[0]["coverage"] == "insufficient_data"
    assert cards[0]["direction"] is None


def test_divergence_not_bullish(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    etf_store.write_snapshot(
        {
            "trade_date": "20260717",
            "data_date": "20260717",
            "stale": False,
            "themes": {
                "芯片": {
                    "flow_5d": 1.0,
                    "flow_1d": 0.5,
                    "past5_ret": 5.0,
                    "divergence": True,
                    "accel": True,
                    "n_funds": 2,
                    "rank_5d": 3,
                }
            },
        },
        db_path=db,
    )
    c = generate_for_date("20260717", db_path=db)[0]
    assert c["rule_id"] == "etf_flow_divergence_top"
    assert c["direction"] == "hist_unfavorable"


def test_drawdown_inflow_not_bottom_fish(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    etf_store.write_snapshot(
        {
            "trade_date": "20260717",
            "data_date": "20260717",
            "stale": False,
            "themes": {
                "芯片": {
                    "flow_5d": 1.5,
                    "flow_1d": 0.8,
                    "past5_ret": -5.0,
                    "divergence": False,
                    "accel": False,
                    "n_funds": 2,
                    "rank_5d": 2,
                }
            },
        },
        db_path=db,
    )
    c = generate_for_date("20260717", db_path=db)[0]
    assert "bottom" in c["rule_id"] or c["metrics"].get("bottom_fish_disproven") is True
    assert c["direction"] != "hist_favorable"
    text = str(c).lower()
    assert "抄底" not in text


def test_schema_drift_missing_keys_no_keyerror(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    etf_store.write_snapshot(
        {
            "trade_date": "20260522",
            "data_date": "20260521",
            "stale": False,
            "themes": {
                "芯片": {
                    "flow_1d": -0.2,
                    "flow_5d": -0.5,
                    "past5_ret": 1.0,
                    "grade": "中性偏多",
                    "divergence": False,
                    # no accel / n_funds / rank_5d
                }
            },
        },
        db_path=db,
    )
    cards = generate_for_date("20260522", db_path=db)
    assert len(cards) == 1
    assert "accel" in cards[0]["metrics"].get("missing_keys", []) or cards[0][
        "metrics"
    ].get("accel") is None


def test_theme_count_not_padded(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    etf_store.write_snapshot(
        {
            "trade_date": "20260611",
            "data_date": "20260611",
            "stale": False,
            "themes": {
                "芯片": {"flow_5d": -1.0, "flow_1d": 0, "past5_ret": 0, "divergence": False},
                "半导体": {"flow_5d": -1.0, "flow_1d": 0, "past5_ret": 0, "divergence": False},
                "机器人": {"flow_5d": -1.0, "flow_1d": 0, "past5_ret": 0, "divergence": False},
                "人工智能": {"flow_5d": -1.0, "flow_1d": 0, "past5_ret": 0, "divergence": False},
            },
        },
        db_path=db,
    )
    assert len(generate_for_date("20260611", db_path=db)) == 4
