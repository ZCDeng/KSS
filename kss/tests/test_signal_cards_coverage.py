"""U5: valuation 幂等 + backtest_verdict 门控与命名空间映射。"""

from __future__ import annotations

from pathlib import Path

import yaml

from kss.signal_cards.backtest_verdict import (
    generate_for_date as gen_bt,
    resolve_factor_id,
)
from kss.signal_cards.valuation import generate_for_cached_snapshot, generate_for_date
from kss.storage.db import connect, ensure_schema


def _seed_ic_and_pred(db: Path, *, n_periods_log: int, n_periods_sr: int) -> None:
    with connect(db) as conn:
        ensure_schema(conn)
        conn.execute(
            "INSERT OR REPLACE INTO ic_snapshots "
            "(factor_id, window_end, source, ic_mean, ic_std, icir, ic_positive_rate, "
            "n_periods, ic_t_stat, half_life_1d, half_life_5d, half_life_20d, method, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "pipeline:log_mv",
                "2026-07-17",
                "realized",
                0.05,
                0.1,
                0.2,
                0.6,
                n_periods_log,
                1.0,
                None,
                None,
                None,
                "test",
                None,
            ),
        )
        conn.execute(
            "INSERT OR REPLACE INTO ic_snapshots "
            "(factor_id, window_end, source, ic_mean, ic_std, icir, ic_positive_rate, "
            "n_periods, ic_t_stat, half_life_1d, half_life_5d, half_life_20d, method, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "sr",
                "2026-07-17",
                "realized",
                0.01,
                0.1,
                0.05,
                0.5,
                n_periods_sr,
                0.5,
                None,
                None,
                None,
                "test",
                None,
            ),
        )
        for strategy in ("log_mv_reverse", "sr", "brand_new_strategy"):
            conn.execute(
                "INSERT OR REPLACE INTO predictions "
                "(prediction_id, prediction_date, symbol, strategy, status, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    f"{strategy}-1",
                    "2026-07-17",
                    "688017.SH",
                    strategy,
                    "settled",
                    None,
                    None,
                ),
            )


def test_strategy_to_factor_namespace() -> None:
    assert resolve_factor_id("log_mv_reverse") == "pipeline:log_mv"
    assert resolve_factor_id("sr") == "sr"


def test_backtest_gate_by_n_periods(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    _seed_ic_and_pred(db, n_periods_log=25, n_periods_sr=5)
    cards = gen_bt("20260717", db_path=db)
    by_subj = {c["subject"]: c for c in cards}
    assert by_subj["log_mv_reverse"]["coverage"] == "covered"
    assert by_subj["sr"]["coverage"] == "insufficient_data"
    assert by_subj["sr"]["metrics"]["deficit_days"] == 15
    assert by_subj["brand_new_strategy"]["coverage"] == "insufficient_data"
    for c in cards:
        assert c["direction"] is None


def test_gate_boundary_exactly_20(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    _seed_ic_and_pred(db, n_periods_log=20, n_periods_sr=19)
    cards = gen_bt("20260717", db_path=db)
    by_subj = {c["subject"]: c for c in cards}
    assert by_subj["log_mv_reverse"]["coverage"] == "covered"
    assert by_subj["sr"]["coverage"] == "insufficient_data"


def test_gate_reads_config(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    _seed_ic_and_pred(db, n_periods_log=25, n_periods_sr=5)
    cfg = tmp_path / "th.yaml"
    cfg.write_text(
        yaml.dump({"factor_health": {"realized_ic_min_n": 5}}),
        encoding="utf-8",
    )
    cards = gen_bt("20260717", db_path=db, thresholds_path=cfg)
    by_subj = {c["subject"]: c for c in cards}
    assert by_subj["sr"]["coverage"] == "covered"


def test_valuation_from_cache_and_not_in_list(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    with connect(db) as conn:
        ensure_schema(conn)
        conn.execute(
            "INSERT OR REPLACE INTO perilla_enrich_cache (ts_code, kind, payload, cached_at) "
            "VALUES (?,?,?,?)",
            (
                "688017.SH",
                "pe",
                "ts_code,trade_date,pe,pe_ttm,total_mv,circ_mv\n688017.SH,20260727,10,11,1,1\n",
                "2026-07-27",
            ),
        )
        conn.execute(
            "INSERT OR REPLACE INTO perilla_enrich_cache (ts_code, kind, payload, cached_at) "
            "VALUES (?,?,?,?)",
            (
                "688017.SH",
                "holders",
                "ts_code,ann_date,end_date,holder_name\n688017.SH,2026-07-01,2026-06-30,机构A\n",
                "2026-07-27",
            ),
        )
        # us_peer 不产卡
        conn.execute(
            "INSERT OR REPLACE INTO perilla_enrich_cache (ts_code, kind, payload, cached_at) "
            "VALUES (?,?,?,?)",
            ("AMAT", "us_peer", "{}", "2026-07-27"),
        )
    cards = generate_for_cached_snapshot(
        db_path=db, extra_symbols=["999999.SH"]
    )
    a_share = [c for c in cards if c["subject"] == "688017.SH"]
    assert len(a_share) == 1
    assert a_share[0]["trade_date"] == "20260727"
    assert a_share[0]["metrics"]["pe"] == 11.0
    assert a_share[0]["direction"] is None
    not_in = [c for c in cards if c["coverage"] == "not_in_list"]
    assert any(c["subject"] == "999999.SH" for c in not_in)
    assert not any(c["subject"] == "AMAT" for c in cards)

    # 同 cached_at 幂等
    again = generate_for_cached_snapshot(db_path=db)
    assert len([c for c in again if c["subject"] == "688017.SH"]) == 1

    # 按日入口：非 cached_at 日为空
    assert generate_for_date("20260717", db_path=db) == []
    assert len(generate_for_date("20260727", db_path=db)) == 1
