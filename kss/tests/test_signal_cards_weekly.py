"""U9: 周报聚合 N/M、分栏、覆盖 4/5、下钻 card_id。"""

from __future__ import annotations

from pathlib import Path

from kss.signal_cards.weekly import aggregate_week, render_markdown, resolve_card_ids
from kss.storage.signal_cards import write_cards


def _card(
    cid: str,
    ct: str,
    d: str,
    subject: str,
    *,
    thr: str = "convention",
    direction=None,
    win_rate=None,
    effective_n=None,
    metrics=None,
) -> dict:
    return {
        "card_id": cid,
        "card_type": ct,
        "trade_date": d,
        "subject": subject,
        "threshold_source": thr,
        "direction": direction,
        "win_rate": win_rate,
        "effective_n": effective_n,
        "coverage": "covered",
        "metrics": metrics or {"pctChange": 4.0, "pct_chg": 4.0},
        "rule_id": "t",
    }


def test_persistence_n_m_and_split(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    cards = []
    # subject X: sector_move 3 日 + volume_spike 3 日 → 持续观察（convention）
    for i, d in enumerate(["20260713", "20260715", "20260716"]):
        cards.append(_card(f"s{i}", "sector_move", d, "光刻机", thr="convention"))
        cards.append(_card(f"v{i}", "volume_spike", d, "光刻机", thr="convention"))
    # 仅 2 日不够
    cards.append(_card("s9", "sector_move", "20260717", "短命", thr="convention"))
    cards.append(_card("v9", "volume_spike", "20260717", "短命", thr="convention"))
    # 估值不应进持续
    cards.append(
        _card("val", "valuation", "20260713", "688017.SH", thr="none", metrics={"pe": 10})
    )
    # ETF 带方向
    cards.append(
        _card(
            "e1",
            "etf_flow",
            "20260713",
            "芯片",
            thr="backtested",
            direction="hist_favorable",
            win_rate=0.77,
            effective_n=46,
            metrics={"flow_5d": -3.0},
        )
    )
    write_cards(cards, db_path=db)
    agg = aggregate_week("20260713", "20260717", db_path=db, expected_trade_days=5)
    assert agg["coverage_label"] == "4/5 交易日"  # 13,15,16,17 — 无 14
    # 持续观察含 光刻机
    obs_subjects = {r["subject"] for r in agg["persistent_observations"]}
    assert "光刻机" in obs_subjects
    assert "短命" not in obs_subjects
    for row in agg["persistent_observations"]:
        assert row["card_ids"]
        resolved = resolve_card_ids(row["card_ids"], db_path=db)
        assert len(resolved) == len(row["card_ids"])
    # 估值不在持续栏
    for row in agg["persistent_signals"] + agg["persistent_observations"]:
        assert row["subject"] != "688017.SH" or "valuation" not in row.get("sources", [])

    md = render_markdown(agg)
    assert "持续信号" in md
    assert "持续观察项" in md
    assert "未经回测" in md
    assert "本周无估值快照" in md or "valuation" in md
    assert "4/5 交易日" in md
    # ETF 方向与胜率同框
    assert "win_rate=0.77" in md or "win_rate=0.77" in md.replace(" ", "")
    # 非 ETF 不渲染 direction 词汇于 sector 行（粗检：hist_favorable 只在 etf 上下文）
    assert "hist_favorable" in md  # etf 有


def test_empty_week(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    from kss.storage.db import ensure_schema_at

    ensure_schema_at(db)
    agg = aggregate_week("20260101", "20260105", db_path=db)
    md = render_markdown(agg)
    assert "本周无" in md
    assert md.strip()
