"""scripts/settle_ledger.py —— 账本结算 driver 测试 (U8 ①).

覆盖:
- 回填 (--backfill) 历史 paper_trade JSON 进账本, 幂等;
- 结算 (--settle) due-open 记录: 真实 T+1/T+2 open → realized_ret + F3 归因 端到端;
- due 判定: prediction_date <= cutoff 入队; T+2 未到 (cs_data 不足) 留 open;
- 超期缺数据 → data_missing (fail loud, 不幻觉填值);
- 幂等: 重复 backfill + settle 不漂移已结算记录.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from kss.prediction.ledger import (
    STATUS_DATA_MISSING,
    STATUS_OPEN,
    STATUS_SETTLED,
    PredictionLedger,
)
import scripts.settle_ledger as sl


@pytest.fixture()
def ledger(tmp_path: Path) -> PredictionLedger:
    return PredictionLedger(db_path=tmp_path / "ledger.db")


@pytest.fixture(autouse=True)
def _clear_cs_cache():
    sl._CS_CACHE.clear()
    yield
    sl._CS_CACHE.clear()


def _write_paper(db_path: Path, date: str, picks: list[dict]) -> None:
    from kss.storage.paper_trade import write_day

    write_day(
        {
            "prediction_date": date,
            "strategy": "log_mv_reverse",
            "top_n": 5,
            "picks": picks,
        },
        db_path,
    )


def _patch_prices(monkeypatch, table: dict[tuple[str, str], tuple[float, float] | None]):
    """打桩 t1_t2_open: (symbol, prediction_date) -> (t1, t2) | None."""
    def _fake(symbol: str, prediction_date: str):
        return table.get((symbol, prediction_date))
    monkeypatch.setattr(sl, "t1_t2_open", _fake)


# --------------------------------------------------------------------------- #
# 回填
# --------------------------------------------------------------------------- #

def test_backfill_records_idempotent(ledger, tmp_path, monkeypatch):
    db_path = tmp_path / "paper" / "kss.db"
    _write_paper(db_path, "2026-06-10", [
        {"symbol": "688322.SH", "factor_value": 14.5, "rank_pct": 0.02,
         "rank_position": 1, "planned_weight": 0.2},
        {"symbol": "688017.SH", "factor_value": 14.8, "rank_pct": 0.04,
         "rank_position": 2, "planned_weight": 0.2},
    ])
    # replay_paper_trade 默认读 kss.db paper_trade_picks; 这里直连账本 + db_path 走 replay 注入
    from kss.prediction.ledger import replay_paper_trade
    s1 = replay_paper_trade(ledger, db_path=db_path, settle=False)
    assert s1["records"] == 2
    # 二次回填幂等: 全部跳过, 不新增
    s2 = replay_paper_trade(ledger, db_path=db_path, settle=False)
    assert s2["records"] == 0
    assert s2["skipped"] == 2
    assert len(ledger.query()) == 2


# --------------------------------------------------------------------------- #
# 结算 + 归因端到端
# --------------------------------------------------------------------------- #

def test_settle_end_to_end_realized_and_attribution(ledger, monkeypatch):
    from kss.prediction.ledger import PredictionRecord
    ledger.record_prediction(PredictionRecord(
        prediction_id="2026-06-10_688322.SH", prediction_date="2026-06-10",
        symbol="688322.SH", factor_value=14.5,
    ))
    # T+1 open=10, T+2 open=11 → realized_ret = 0.1 (win), 远超 friction bps → factor_valid
    _patch_prices(monkeypatch, {("688322.SH", "2026-06-10"): (10.0, 11.0)})
    out = sl.run_settle(ledger, "2026-06-20")
    assert out["due"] == 1
    assert out["settled"] == 1
    row = ledger.get("2026-06-10_688322.SH")
    assert row["status"] == STATUS_SETTLED
    assert row["realized_ret"] == pytest.approx(0.1)
    assert row["outcome"] == "win"
    assert row["attribution_category"] == "factor_valid"
    assert out["attribution"].get("factor_valid") == 1


def test_settle_due_judgment_t2_not_ready_stays_open(ledger, monkeypatch):
    """T+2 未到 (价取不到) 且未超期 → 留 open, 不误结算."""
    from kss.prediction.ledger import PredictionRecord
    ledger.record_prediction(PredictionRecord(
        prediction_id="2026-06-19_688017.SH", prediction_date="2026-06-19",
        symbol="688017.SH", factor_value=14.8,
    ))
    _patch_prices(monkeypatch, {("688017.SH", "2026-06-19"): None})
    # cutoff 仅比预测日晚 1 天 → 未超 stale_settle_days(默认7) → 留 open
    out = sl.run_settle(ledger, "2026-06-20")
    assert out["due"] == 1
    assert out["settled"] == 0
    assert out["still_open"] == 1
    assert ledger.get("2026-06-19_688017.SH")["status"] == STATUS_OPEN


def test_settle_stale_missing_marks_data_missing(ledger, monkeypatch):
    """缺数据且超 stale_settle_days → data_missing (fail loud)."""
    from kss.prediction.ledger import PredictionRecord
    ledger.record_prediction(PredictionRecord(
        prediction_id="2026-05-01_688999.SH", prediction_date="2026-05-01",
        symbol="688999.SH", factor_value=14.9,
    ))
    _patch_prices(monkeypatch, {("688999.SH", "2026-05-01"): None})
    out = sl.run_settle(ledger, "2026-06-20")  # 50 天前, 远超 7
    assert out["data_missing"] == 1
    assert ledger.get("2026-05-01_688999.SH")["status"] == STATUS_DATA_MISSING


def test_settle_cutoff_excludes_future_predictions(ledger, monkeypatch):
    """prediction_date > cutoff 的记录不入结算队列."""
    from kss.prediction.ledger import PredictionRecord
    ledger.record_prediction(PredictionRecord(
        prediction_id="2026-06-25_688322.SH", prediction_date="2026-06-25",
        symbol="688322.SH", factor_value=14.5,
    ))
    _patch_prices(monkeypatch, {("688322.SH", "2026-06-25"): (10.0, 11.0)})
    out = sl.run_settle(ledger, "2026-06-20")  # cutoff 早于预测日
    assert out["due"] == 0
    assert ledger.get("2026-06-25_688322.SH")["status"] == STATUS_OPEN


def test_settle_idempotent_no_drift(ledger, monkeypatch):
    """重复结算不漂移: 已 settled 记录第二次 settle 短路 (重结算守卫)."""
    from kss.prediction.ledger import PredictionRecord
    ledger.record_prediction(PredictionRecord(
        prediction_id="2026-06-10_688322.SH", prediction_date="2026-06-10",
        symbol="688322.SH", factor_value=14.5,
    ))
    _patch_prices(monkeypatch, {("688322.SH", "2026-06-10"): (10.0, 11.0)})
    sl.run_settle(ledger, "2026-06-20")
    first = ledger.get("2026-06-10_688322.SH")["realized_ret"]
    # 价漂移: 第二次给不同价, 守卫应拒绝覆盖 PIT 快照
    _patch_prices(monkeypatch, {("688322.SH", "2026-06-10"): (10.0, 20.0)})
    out2 = sl.run_settle(ledger, "2026-06-20")
    # 已 settled 不在 open_due 队列 → due=0, 价不被覆盖
    assert out2["due"] == 0
    assert ledger.get("2026-06-10_688322.SH")["realized_ret"] == pytest.approx(first)


def test_t1_t2_open_pit_reads_real_cs_data():
    """t1_t2_open 从真实 cs_data 取 T+1/T+2 open (PIT, 不看预测日当日)."""
    # 用真实仓库内 cs_data, 取一个有充足后续数据的早期日期
    prices = sl.t1_t2_open("688322.SH", "2023-01-03")
    assert prices is not None
    t1, t2 = prices
    assert t1 > 0 and t2 > 0
    # 预测日不存在的码 → None (不编造)
    assert sl.t1_t2_open("000000.XX", "2023-01-03") is None
