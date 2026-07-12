"""kss/prediction/ledger.py —— 预测生命周期账本测试 (U2).

覆盖:
- 入账 → 结算 → 归因全链路;
- T+2 未到 status=open 不误结算;
- 归因分类规则优先级命中 (纯函数);
- 回放历史 paper_trade 重建账本且 realized_ret 与 _horizon_return 对齐;
- 归因字段无价格幻觉 (attribution_note payload 不含价格字段).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kss.prediction.ledger import (
    ATTR_DATA_MISSING,
    ATTR_EXECUTION_FRICTION,
    ATTR_FACTOR_STALE,
    ATTR_FACTOR_VALID,
    ATTR_REGIME_SHIFT,
    STATUS_DATA_MISSING,
    STATUS_OPEN,
    STATUS_SETTLED,
    AttributionContext,
    PredictionLedger,
    PredictionRecord,
    attribution_note_payload,
    classify_attribution,
    classify_outcome,
    replay_paper_trade,
)


@pytest.fixture()
def ledger(tmp_path: Path) -> PredictionLedger:
    return PredictionLedger(db_path=tmp_path / "ledger.db")


def _rec(pred_date: str = "2026-06-18", symbol: str = "688114.SH", **kw) -> PredictionRecord:
    fields = {
        "pipeline_snapshot": {"factor_col": "log_mv", "top_n": 5},
        "regime_label": "bull_small_cap",
        "factor_value": 14.5,
        "rank_pct": 0.02,
        "rank_position": 1,
        "planned_weight": 0.2,
    }
    fields.update(kw)
    return PredictionRecord(
        prediction_id=f"{pred_date}_{symbol}",
        prediction_date=pred_date,
        symbol=symbol,
        **fields,
    )


# ---------------------------------------------------------------------------
# F1 入账 + 去重
# ---------------------------------------------------------------------------


def test_record_and_dedup(ledger: PredictionLedger) -> None:
    assert ledger.record_prediction(_rec()) is True
    row = ledger.get("2026-06-18_688114.SH")
    assert row is not None
    assert row["status"] == STATUS_OPEN
    assert row["factor_value"] == 14.5
    assert row["pipeline_snapshot"]["factor_col"] == "log_mv"  # JSON 解码回 dict
    # 重复入账默认跳过 (AE-5)
    assert ledger.record_prediction(_rec(factor_value=99.0)) is False
    assert ledger.get("2026-06-18_688114.SH")["factor_value"] == 14.5
    # force 覆盖
    assert ledger.record_prediction(_rec(factor_value=99.0), force=True) is True
    assert ledger.get("2026-06-18_688114.SH")["factor_value"] == 99.0


# ---------------------------------------------------------------------------
# F2 结算 + F3 归因 全链路
# ---------------------------------------------------------------------------


def test_settle_full_chain(ledger: PredictionLedger) -> None:
    ledger.record_prediction(_rec())
    out = ledger.settle("2026-06-18_688114.SH", t1_open=12.35, t2_open=12.58)
    assert out["status"] == STATUS_SETTLED
    assert out["t1_open"] == 12.35
    assert out["t2_open"] == 12.58
    assert out["realized_ret"] == pytest.approx(12.58 / 12.35 - 1)
    assert out["outcome"] == "win"
    # 涨幅约 1.86% > 30bps execution_friction 阈值, regime/IC hook 未给 → factor_valid
    assert out["attribution_category"] == ATTR_FACTOR_VALID


def test_settle_loss_and_flat(ledger: PredictionLedger) -> None:
    ledger.record_prediction(_rec(symbol="688349.SH"))
    out = ledger.settle("2026-06-18_688349.SH", t1_open=12.58, t2_open=12.35)
    assert out["outcome"] == "loss"
    assert out["realized_ret"] < 0


def test_t2_not_arrived_stays_open(ledger: PredictionLedger) -> None:
    """T+2 数据未到 (t2_open 缺失) → status 保持 open, 不误结算 (AE-4)."""
    ledger.record_prediction(_rec())
    out = ledger.settle("2026-06-18_688114.SH", t1_open=12.35, t2_open=None)
    assert out["status"] == STATUS_OPEN
    assert out["realized_ret"] is None
    assert out["outcome"] is None
    assert out["attribution_category"] is None
    # 后续数据到齐可正常结算
    out2 = ledger.settle("2026-06-18_688114.SH", t1_open=12.35, t2_open=12.58)
    assert out2["status"] == STATUS_SETTLED


def test_mark_data_missing(ledger: PredictionLedger) -> None:
    ledger.record_prediction(_rec())
    out = ledger.mark_data_missing("2026-06-18_688114.SH")
    assert out["status"] == STATUS_DATA_MISSING
    assert out["attribution_category"] == ATTR_DATA_MISSING


# ---------------------------------------------------------------------------
# F3 归因规则优先级 (纯函数)
# ---------------------------------------------------------------------------


def test_attribution_priority_data_missing() -> None:
    ctx = AttributionContext(outcome=None, realized_ret=None, has_prices=False)
    assert classify_attribution(ctx) == ATTR_DATA_MISSING


def test_attribution_priority_factor_stale_over_regime() -> None:
    # IC 滚动均值 < 0.02 → factor_stale, 即便 regime 也变 (优先级更高, AE-3)
    ctx = AttributionContext(
        outcome="loss",
        realized_ret=-0.05,
        has_prices=True,
        entry_regime_label="bull",
        settle_regime_label="bear",
        rolling_ic=0.01,
    )
    assert classify_attribution(ctx) == ATTR_FACTOR_STALE


def test_attribution_regime_shift() -> None:
    ctx = AttributionContext(
        outcome="loss",
        realized_ret=-0.05,
        has_prices=True,
        entry_regime_label="bull",
        settle_regime_label="bear",
        rolling_ic=0.10,  # IC 健康 → 不 stale
    )
    assert classify_attribution(ctx) == ATTR_REGIME_SHIFT


def test_attribution_execution_friction() -> None:
    # |realized_ret| = 10bps < 30bps 阈值 → execution_friction
    ctx = AttributionContext(
        outcome="win", realized_ret=0.001, has_prices=True, rolling_ic=0.10,
    )
    assert classify_attribution(ctx) == ATTR_EXECUTION_FRICTION


def test_attribution_factor_valid() -> None:
    ctx = AttributionContext(
        outcome="win", realized_ret=0.05, has_prices=True, rolling_ic=0.10,
    )
    assert classify_attribution(ctx) == ATTR_FACTOR_VALID


def test_attribution_hook_missing_does_not_fabricate() -> None:
    # rolling_ic / settle_regime 缺失 → 这两类不触发, 路由继续向下 (fail-safe)
    ctx = AttributionContext(outcome="win", realized_ret=0.05, has_prices=True)
    assert classify_attribution(ctx) == ATTR_FACTOR_VALID


def test_classify_outcome() -> None:
    assert classify_outcome(0.01) == "win"
    assert classify_outcome(-0.01) == "loss"
    assert classify_outcome(0.0) == "flat"
    assert classify_outcome(None) is None


# ---------------------------------------------------------------------------
# 归因字段无价格幻觉 (纯函数测)
# ---------------------------------------------------------------------------


def test_attribution_note_payload_excludes_prices() -> None:
    record = {
        "attribution_category": "factor_valid",
        "outcome": "win",
        "regime_label": "bull",
        "factor_value": 14.5,
        "rank_pct": 0.02,
        "t1_open": 12.35,
        "t2_open": 12.58,
        "realized_ret": 0.0186,
    }
    payload = attribution_note_payload(record)
    serialized = json.dumps(payload)
    for forbidden in ("t1_open", "t2_open", "realized_ret", "12.35", "12.58", "0.0186"):
        assert forbidden not in serialized
    assert payload["attribution_category"] == "factor_valid"
    assert payload["outcome"] == "win"


# ---------------------------------------------------------------------------
# 回放历史 paper_trade 重建账本 + 与 _horizon_return 对齐
# ---------------------------------------------------------------------------


def _write_paper_log(db_path: Path, date: str, picks: list[dict]) -> None:
    from kss.storage.paper_trade import write_day

    write_day(
        {
            "prediction_date": date,
            "strategy": "log_mv_reverse",
            "top_pct": 0.2,
            "top_n": 5,
            "picks": picks,
        },
        db_path,
    )


def test_replay_rebuilds_ledger_idempotent(ledger: PredictionLedger, tmp_path: Path) -> None:
    db_path = tmp_path / "paper_trade" / "kss.db"
    _write_paper_log(
        db_path,
        "2026-06-18",
        [
            {"symbol": "688114.SH", "factor_value": 14.5, "rank_pct": 0.02,
             "rank_position": 1, "planned_weight": 0.2},
            {"symbol": "688349.SH", "factor_value": 14.6, "rank_pct": 0.04,
             "rank_position": 2, "planned_weight": 0.2},
        ],
    )
    stats = replay_paper_trade(ledger, db_path=db_path)
    assert stats["records"] == 2
    assert ledger.get("2026-06-18_688114.SH")["status"] == STATUS_OPEN
    # 幂等: 二次回放不新增
    stats2 = replay_paper_trade(ledger, db_path=db_path)
    assert stats2["records"] == 0
    assert stats2["skipped"] == 2


def test_replay_settle_aligns_horizon_return(ledger: PredictionLedger, tmp_path: Path) -> None:
    """回放结算的 realized_ret 必须与 _horizon_return(hold=1) 口径一致."""
    db_path = tmp_path / "paper_trade" / "kss.db"
    _write_paper_log(
        db_path,
        "2026-06-18",
        [{"symbol": "688114.SH", "factor_value": 14.5, "rank_pct": 0.02,
          "rank_position": 1, "planned_weight": 0.2}],
    )

    # 模拟 _horizon_return 的取价: T+1 open -> T+2 open
    t1_open, t2_open = 12.35, 12.58

    def fake_horizon(symbol: str, prediction_date: str):
        return (t1_open, t2_open)

    stats = replay_paper_trade(
        ledger, db_path=db_path, settle=True, horizon_return=fake_horizon,
    )
    assert stats["settled"] == 1
    row = ledger.get("2026-06-18_688114.SH")
    assert row["status"] == STATUS_SETTLED
    # 与 _horizon_return 定义 (t2_open / t1_open - 1) 对齐
    expected = t2_open / t1_open - 1
    assert row["realized_ret"] == pytest.approx(expected)


# ---------------------------------------------------------------------------
# 查询索引
# ---------------------------------------------------------------------------


def test_query_by_status_and_due_for_settle(ledger: PredictionLedger) -> None:
    ledger.record_prediction(_rec(pred_date="2026-06-16", symbol="688114.SH"))
    ledger.record_prediction(_rec(pred_date="2026-06-18", symbol="688349.SH"))
    ledger.settle("2026-06-16_688114.SH", t1_open=10.0, t2_open=10.5)
    assert len(ledger.query(status=STATUS_SETTLED)) == 1
    assert len(ledger.query(status=STATUS_OPEN)) == 1
    # T+2 语义: cutoff 之前的 open 记录入结算队列
    due = ledger.open_due_for_settle("2026-06-16")
    assert due == []  # 唯一 open 记录 date=2026-06-18 > cutoff
    due2 = ledger.open_due_for_settle("2026-06-18")
    assert len(due2) == 1


def test_settle_rejects_resettle_protects_pit_snapshot(ledger: PredictionLedger) -> None:
    """已结算记录二次 settle 用不同价 → 拒绝覆盖 PIT 快照(C2 修复)."""
    ledger.record_prediction(_rec())
    pid = "2026-06-18_688114.SH"
    first = ledger.settle(pid, t1_open=10.0, t2_open=11.0)
    assert first["status"] == STATUS_SETTLED
    assert abs(first["realized_ret"] - 0.1) < 1e-9
    # cs_data 价漂移后重结算 → 必须拒绝、保留原快照
    again = ledger.settle(pid, t1_open=10.0, t2_open=9.0)
    assert abs(again["realized_ret"] - 0.1) < 1e-9   # 仍是 0.1，未被 -0.1 覆盖
    assert again["t2_open"] == 11.0
    # force=True 才允许改
    forced = ledger.settle(pid, t1_open=10.0, t2_open=9.0, force=True)
    assert abs(forced["realized_ret"] - (-0.1)) < 1e-9


def test_mark_data_missing_rejects_clobber_of_settled(ledger: PredictionLedger) -> None:
    """已结算记录不可被降级为 data_missing(C2 修复)."""
    ledger.record_prediction(_rec())
    pid = "2026-06-18_688114.SH"
    ledger.settle(pid, t1_open=10.0, t2_open=11.0)
    out = ledger.mark_data_missing(pid)
    assert out["status"] == STATUS_SETTLED   # 未被降级
