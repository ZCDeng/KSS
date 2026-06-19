"""板块热点轮动快照测试."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from kss.sector.hotspot_rotation import (
    HotspotBoard,
    HotspotRotationSnapshot,
    _aggregate_historical_metrics,
    _apply_classification,
    _build_boards,
    _classify_board,
    _load_trade_calendar,
    build_hotspot_rotation_snapshot,
    save_snapshot,
    snapshot_to_dict,
)


class MockPro:
    """模拟 Tushare pro API."""

    def __init__(self, trade_cal_df: pd.DataFrame | None = None) -> None:
        self._trade_cal_df = trade_cal_df

    def trade_cal(self, **kwargs) -> pd.DataFrame | None:
        return self._trade_cal_df


class FakeTushareClient:
    """假的 TushareClient，用于隔离外部 API."""

    def __init__(
        self,
        *,
        industry: pd.DataFrame | None = None,
        concept: pd.DataFrame | None = None,
        trade_cal_df: pd.DataFrame | None = None,
    ) -> None:
        self._industry = industry
        self._concept = concept
        self._pro = MockPro(trade_cal_df)

    def fetch_moneyflow_ind_dc(self, trade_date: str) -> pd.DataFrame | None:
        return self._industry

    def fetch_moneyflow_cnt_ths(self, trade_date: str) -> pd.DataFrame | None:
        return self._concept

    def get_pro(self) -> MockPro:
        return self._pro


def _make_industry_df() -> pd.DataFrame:
    return pd.DataFrame({
        "name": ["芯片", "通信", "医药", "银行"],
        "ts_code": ["801001.SI", "801660.SI", "801045.SI", "801780.SI"],
        "pct_change": [5.23, 3.12, 0.51, -1.20],
        "net_amount_rate": [3.1, 1.8, 0.2, -0.5],
        "buy_elg_amount_rate": [2.5, 1.2, 0.1, -0.3],
        "content_type": ["行业", "行业", "行业", "行业"],
    })


def _make_concept_df() -> pd.DataFrame:
    return pd.DataFrame({
        "name": ["F5G概念", "光纤概念", "算力租赁"],
        "pct_change": [4.94, 3.87, 2.15],
        "net_amount": [1.2e8, 8.5e7, 5.0e7],
    })


def _trade_cal_for(dates: list[str]) -> pd.DataFrame:
    return pd.DataFrame({
        "cal_date": dates,
        "is_open": ["1"] * len(dates),
    })


def test_build_boards_rank_and_heat_score() -> None:
    df = _make_industry_df()
    boards, missing = _build_boards(
        df,
        source="industry",
        name_col="name",
        code_col="ts_code",
        weights={"pct_change": 0.5, "net_amount_rate": 0.3, "buy_elg_amount_rate": 0.2},
        top_n=None,
    )
    assert not missing
    assert len(boards) == 4
    assert boards[0].name == "芯片"
    assert boards[0].todayRank == 1
    assert boards[0].source == "industry"
    assert boards[0].boardCode == "801001.SI"
    assert boards[0].pctChange == pytest.approx(5.23)
    assert boards[0].heatScore is not None
    assert boards[-1].name == "银行"
    assert boards[-1].todayRank == 4


def test_build_boards_top_n_filter() -> None:
    df = _make_industry_df()
    boards, missing = _build_boards(
        df,
        source="industry",
        name_col="name",
        code_col="ts_code",
        weights={"pct_change": 1.0},
        top_n=2,
    )
    assert not missing
    assert len(boards) == 2
    assert {b.name for b in boards} == {"芯片", "通信"}


def test_build_boards_missing_pct_change_logs_missing() -> None:
    df = pd.DataFrame({"name": ["芯片"], "net_amount": [1.0]})
    boards, missing = _build_boards(
        df,
        source="concept",
        name_col="name",
        code_col=None,
        weights={"pct_change": 1.0},
    )
    assert not boards
    assert any("pct_change" in m for m in missing)


def test_load_trade_calendar_from_tushare() -> None:
    dates = ["20260618", "20260617", "20260616", "20260613", "20260612"]
    client = FakeTushareClient(trade_cal_df=_trade_cal_for(dates))
    result = _load_trade_calendar("20260618", 3, client=client, output_dir=Path("/nonexistent"))
    assert result == ["20260618", "20260617", "20260616"]


def test_load_trade_calendar_fallback_to_archives(tmp_path: Path) -> None:
    # 没有 trade_cal，退而扫描归档
    (tmp_path / "20260618.json").write_text("{}")
    (tmp_path / "20260617.json").write_text("{}")
    (tmp_path / "20260616.json").write_text("{}")
    client = FakeTushareClient(trade_cal_df=None)
    result = _load_trade_calendar("20260618", 3, client=client, output_dir=tmp_path)
    assert result == ["20260618", "20260617", "20260616"]


def test_aggregate_historical_metrics() -> None:
    today = [HotspotBoard(name="芯片", source="industry", todayRank=1)]
    history = [
        ("20260617", [HotspotBoard(name="芯片", source="industry", todayRank=1)]),
        ("20260616", [HotspotBoard(name="芯片", source="industry", todayRank=1)]),
        ("20260613", [HotspotBoard(name="芯片", source="industry", todayRank=3)]),
    ]
    _aggregate_historical_metrics(today, history)
    b = today[0]
    assert b.previousRank == 1
    assert b.rankJump == 0
    assert b.top3Appearances == 3
    assert b.streakDays == 2


def test_classify_board_mainline() -> None:
    b = HotspotBoard(name="芯片", source="industry", todayRank=2, top3Appearances=3)
    cls, conf = _classify_board(b, top_n=10, kaipan_available=False)
    assert cls == "mainline"
    assert conf == "medium"


def test_classify_board_demon_board() -> None:
    b = HotspotBoard(name="F5G", source="concept", todayRank=1, top3Appearances=0, rankJump=8)
    cls, conf = _classify_board(b, top_n=10, kaipan_available=False)
    assert cls == "demonBoard"
    assert conf == "medium"


def test_classify_board_fading() -> None:
    b = HotspotBoard(name="算力", source="concept", todayRank=15, top3Appearances=3)
    cls, conf = _classify_board(b, top_n=10, kaipan_available=False)
    assert cls == "oldHotspotFading"
    assert conf == "medium"


def test_apply_classification_cross_signals() -> None:
    boards = [
        HotspotBoard(name="A", source="industry", todayRank=1, top3Appearances=3),
        HotspotBoard(name="B", source="concept", todayRank=12, top3Appearances=0),
    ]
    signals: dict[str, list[str]] = {
        "mainline": [],
        "demonBoard": [],
        "oldHotspotFading": [],
        "satellite": [],
    }
    _apply_classification(boards, top_n=10, kaipan_available=False, cross_signals=signals)
    assert boards[0].classification == "mainline"
    assert boards[1].classification == "satellite"
    assert "A" in signals["mainline"]
    assert "B" in signals["satellite"]


def test_build_hotspot_rotation_snapshot_success() -> None:
    dates = ["20260618", "20260617", "20260616", "20260613", "20260612"]
    client = FakeTushareClient(
        industry=_make_industry_df(),
        concept=_make_concept_df(),
        trade_cal_df=_trade_cal_for(dates),
    )
    snap = build_hotspot_rotation_snapshot(
        "20260618",
        client=client,
        top_n_industry=3,
        top_n_concept=2,
        lookback_days=5,
    )
    assert snap is not None
    assert snap.tradeDate == "20260618"
    assert snap.tradingDaysUsed[0] == "20260618"
    assert len(snap.industries) == 3
    assert len(snap.concepts) == 2
    # 无历史数据时，今日 Top 会被判为 demonBoard（突发热点候选）
    assert snap.industries[0].classification in {"demonBoard", "mainline"}
    assert snap.kaipanBoards == []
    assert "kaipan:disabled" in snap.missing


def test_build_hotspot_rotation_snapshot_with_history(tmp_path: Path) -> None:
    # 构造历史归档
    hist = HotspotRotationSnapshot(
        tradeDate="20260617",
        lookbackDays=1,
        tradingDaysUsed=["20260617"],
        industries=[
            HotspotBoard(name="芯片", source="industry", todayRank=2),
            HotspotBoard(name="通信", source="industry", todayRank=1),
        ],
        concepts=[],
    )
    save_snapshot(hist, output_dir=tmp_path)

    dates = ["20260618", "20260617", "20260616", "20260613", "20260612"]
    client = FakeTushareClient(
        industry=_make_industry_df(),
        concept=_make_concept_df(),
        trade_cal_df=_trade_cal_for(dates),
    )
    snap = build_hotspot_rotation_snapshot(
        "20260618",
        client=client,
        output_dir=tmp_path,
        lookback_days=5,
        top_n_industry=4,
    )
    assert snap is not None
    chip = next(b for b in snap.industries if b.name == "芯片")
    assert chip.previousRank == 2
    assert chip.rankJump == 1  # 2 -> 1, 排名上升
    assert chip.top3Appearances == 1
    assert chip.streakDays == 0


def test_build_hotspot_rotation_snapshot_both_missing() -> None:
    client = FakeTushareClient(industry=None, concept=None)
    snap = build_hotspot_rotation_snapshot("20260618", client=client)
    assert snap is None


def test_build_hotspot_rotation_snapshot_industry_content_type_filter() -> None:
    df = _make_industry_df()
    df.loc[0, "content_type"] = "概念"
    dates = ["20260618", "20260617"]
    client = FakeTushareClient(industry=df, concept=None, trade_cal_df=_trade_cal_for(dates))
    snap = build_hotspot_rotation_snapshot("20260618", client=client, lookback_days=2)
    assert snap is not None
    assert not any(b.name == "芯片" for b in snap.industries)
    assert len(snap.industries) == 3


def test_snapshot_to_dict_and_save(tmp_path: Path) -> None:
    snap = HotspotRotationSnapshot(
        tradeDate="20260618",
        industries=[HotspotBoard(name="芯片", source="industry", todayRank=1)],
    )
    d = snapshot_to_dict(snap)
    assert d["tradeDate"] == "20260618"
    assert d["industries"][0]["name"] == "芯片"

    out = save_snapshot(snap, output_dir=tmp_path)
    assert out.exists()
    loaded = json.loads(out.read_text(encoding="utf-8"))
    assert loaded["tradeDate"] == "20260618"
    assert loaded["industries"][0]["todayRank"] == 1
