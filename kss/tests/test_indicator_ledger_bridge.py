"""IC 双源闭环接线单测（U8）——demote-only 口径，不判定仲裁本身."""

from __future__ import annotations

from pathlib import Path

import pytest

from kss.indicators.ledger_bridge import (
    prediction_id_for,
    record_from_pack,
    record_pack,
    refresh_factor_health,
    sign_proxy_series,
)
from kss.indicators.registry import KIND_PRIMITIVE, RegistryEntry


def _entry() -> RegistryEntry:
    return RegistryEntry(
        id="ma_cross_abc123",
        name="均线交叉示例",
        kind=KIND_PRIMITIVE,
        family="ma_cross",
        params={"fast": 5, "slow": 20},
        symbols=["688017.SH"],
    )


def _ok_pack(asof: str = "2026-07-10", symbol: str = "688017.SH", action: str = "BUY", score: float = 0.4) -> dict:
    return {"status": "ok", "asof": asof, "symbol": symbol, "action": action, "pred_score": score}


@pytest.fixture
def isolated_dbs(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KSS_STATE_ROOT", str(tmp_path))
    return tmp_path


def test_prediction_id_namespaced_by_entry() -> None:
    entry = _entry()
    assert prediction_id_for(entry, "2026-07-10", "688017.SH") == "2026-07-10_688017.SH_ma_cross_abc123"


def test_prediction_id_does_not_collide_with_default_namespace() -> None:
    """R12/KTD6 前提：新命名空间不与既有 {date}_{symbol} 主键冲突（同日同票双策略并存）。"""
    entry = _entry()
    default_style_id = "2026-07-10_688017.SH"
    namespaced = prediction_id_for(entry, "2026-07-10", "688017.SH")
    assert namespaced != default_style_id


def test_record_from_pack_skips_non_ok_status() -> None:
    entry = _entry()
    assert record_from_pack(entry, {"status": "skipped"}) is None
    assert record_from_pack(entry, {"status": "error"}) is None


def test_record_from_pack_skips_missing_pred_score() -> None:
    entry = _entry()
    pack = {"status": "ok", "asof": "2026-07-10", "symbol": "688017.SH", "action": "BUY"}
    assert record_from_pack(entry, pack) is None


def test_record_from_pack_builds_valid_record() -> None:
    entry = _entry()
    rec = record_from_pack(entry, _ok_pack())
    assert rec is not None
    assert rec.strategy == "ma_cross_abc123"
    assert rec.factor_value == 0.4
    assert rec.planned_weight == 1.0


def test_record_from_pack_flat_action_zero_weight() -> None:
    entry = _entry()
    rec = record_from_pack(entry, _ok_pack(action="STAY_FLAT"))
    assert rec is not None
    assert rec.planned_weight == 0.0


def test_record_pack_writes_and_dedupes(isolated_dbs: Path) -> None:
    from kss.prediction.ledger import PredictionLedger

    entry = _entry()
    ledger = PredictionLedger(db_path=isolated_dbs / "ledger.db")
    first = record_pack(entry, _ok_pack(), ledger=ledger)
    second = record_pack(entry, _ok_pack(), ledger=ledger)
    assert first is True
    assert second is False  # 去重：同 prediction_id 已存在


def test_record_pack_coexists_with_default_namespace_same_day_symbol(isolated_dbs: Path) -> None:
    """同日同票，默认命名空间的记录与本指标命名空间的记录互不冲突（不同主键）。"""
    from kss.prediction.ledger import PredictionLedger, PredictionRecord

    entry = _entry()
    ledger = PredictionLedger(db_path=isolated_dbs / "ledger.db")
    default_ok = ledger.record_prediction(
        PredictionRecord(prediction_id="2026-07-10_688017.SH", prediction_date="2026-07-10", symbol="688017.SH")
    )
    indicator_ok = record_pack(entry, _ok_pack(), ledger=ledger)
    assert default_ok is True
    assert indicator_ok is True
    assert len(ledger.query()) == 2


def test_sign_proxy_series_empty_without_settled_records(isolated_dbs: Path) -> None:
    from kss.prediction.ledger import PredictionLedger

    entry = _entry()
    ledger = PredictionLedger(db_path=isolated_dbs / "ledger.db")
    record_pack(entry, _ok_pack(), ledger=ledger)  # status=open，未结算
    series = sign_proxy_series(entry, ledger=ledger)
    assert series.empty


def test_sign_proxy_series_from_settled_records(isolated_dbs: Path) -> None:
    from kss.prediction.ledger import PredictionLedger

    entry = _entry()
    ledger = PredictionLedger(db_path=isolated_dbs / "ledger.db")
    record_pack(entry, _ok_pack(asof="2026-07-01"), ledger=ledger)
    record_pack(entry, _ok_pack(asof="2026-07-02", score=-0.3), ledger=ledger)
    with ledger._connect() as conn:  # 直接写结算列，绕开真实 settle 流程（测试专用）
        conn.execute(
            "UPDATE predictions SET status='settled', realized_ret=? WHERE prediction_date=?",
            (0.02, "2026-07-01"),
        )
        conn.execute(
            "UPDATE predictions SET status='settled', realized_ret=? WHERE prediction_date=?",
            (0.01, "2026-07-02"),
        )
    series = sign_proxy_series(entry, ledger=ledger)
    assert len(series) == 2
    assert series.loc["2026-07-01"] == 1.0  # factor_value>0, realized_ret>0 → 同号
    assert series.loc["2026-07-02"] == -1.0  # factor_value<0, realized_ret>0 → 异号


def test_refresh_factor_health_none_when_no_settled_data(isolated_dbs: Path) -> None:
    from kss.prediction.ledger import PredictionLedger

    entry = _entry()
    ledger = PredictionLedger(db_path=isolated_dbs / "ledger.db")
    result = refresh_factor_health(entry, "2026-07-10", ledger=ledger)
    assert result is None


def test_refresh_factor_health_writes_sign_proxy_snapshot(isolated_dbs: Path) -> None:
    from kss.backtest.factor_health import IC_METHOD_SIGN_PROXY, FactorHealthTracker
    from kss.prediction.ledger import PredictionLedger

    entry = _entry()
    ledger = PredictionLedger(db_path=isolated_dbs / "ledger.db")
    record_pack(entry, _ok_pack(asof="2026-07-01"), ledger=ledger)
    with ledger._connect() as conn:
        conn.execute(
            "UPDATE predictions SET status='settled', realized_ret=? WHERE prediction_date=?",
            (0.02, "2026-07-01"),
        )
    tracker = FactorHealthTracker(db_path=isolated_dbs / "factor_health.db")
    snap = refresh_factor_health(entry, "2026-07-10", tracker=tracker, ledger=ledger)
    assert snap is not None
    assert snap.method == IC_METHOD_SIGN_PROXY
    assert snap.factor_id == entry.id
