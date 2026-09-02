"""板块热点轮动快照测试."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from kss.sector.hotspot_rotation import (
    HotspotBoard,
    HotspotRotationSnapshot,
    _aggregate_historical_metrics,
    _apply_classification,
    _build_boards,
    _build_name_to_code_map,
    _classify_board,
    _fetch_leaders_for_boards,
    _load_trade_calendar,
    build_hotspot_rotation_snapshot,
    save_snapshot,
    snapshot_to_dict,
)


@pytest.fixture(autouse=True)
def _patch_em_industry_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """默认关掉东财行业兜底，避免 FakeClient(industry=None) 打真网."""
    monkeypatch.setattr(
        "kss.sector.hotspot_rotation.fetch_industry_fundflow_em",
        lambda trade_date, **kwargs: None,
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
        "is_open": [1] * len(dates),
    })


# ============== Phase 1 / 2 测试 ==============


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
    result = _load_trade_calendar("20260618", 3, client=client, db_path=Path("/nonexistent/kss.db"))
    assert result == ["20260618", "20260617", "20260616"]


def test_load_trade_calendar_fallback_to_archives(tmp_path: Path) -> None:
    from kss.storage.sector_rotation import write_snapshot

    db_path = tmp_path / "kss.db"
    for d in ("20260618", "20260617", "20260616"):
        write_snapshot({"tradeDate": d}, db_path)
    client = FakeTushareClient(trade_cal_df=None)
    result = _load_trade_calendar("20260618", 3, client=client, db_path=db_path)
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
    assert b.topNAppearances == 3  # 三天历史名次 1/1/3 全在 top_n(10) 内
    assert b.streakDays == 2


def test_aggregate_historical_metrics_topn_distinct_from_top3() -> None:
    """持续在 top_n 但很少进 top3：topNAppearances 高、top3Appearances 低."""
    today = [HotspotBoard(name="材料", source="industry", todayRank=4)]
    history = [
        ("20260617", [HotspotBoard(name="材料", source="industry", todayRank=6)]),
        ("20260616", [HotspotBoard(name="材料", source="industry", todayRank=8)]),
        ("20260613", [HotspotBoard(name="材料", source="industry", todayRank=2)]),
    ]
    _aggregate_historical_metrics(today, history, top_n=10)
    b = today[0]
    assert b.top3Appearances == 1   # 只有名次 2 进了 top3
    assert b.topNAppearances == 3   # 6/8/2 都在 top_n 内


def test_classify_board_mainline() -> None:
    b = HotspotBoard(name="芯片", source="industry", todayRank=2, top3Appearances=3)
    cls, conf = _classify_board(b, top_n=10, kaipan_available=False)
    assert cls == "mainline"
    assert conf == "medium"


def test_classify_board_mainline_via_topn() -> None:
    """重标定：持续在 top_n（topNAppearances>=2）也算 sustained → mainline."""
    b = HotspotBoard(
        name="半导体材料", source="industry", todayRank=5,
        top3Appearances=1, topNAppearances=3,
    )
    cls, conf = _classify_board(b, top_n=10, kaipan_available=False)
    assert cls == "mainline"


def test_classify_board_topn_once_not_sustained() -> None:
    """只在 top_n 出现 1 次 → 未达门槛，爆发但不持续 = 妖板而非主线."""
    b = HotspotBoard(
        name="次新", source="concept", todayRank=3,
        top3Appearances=0, topNAppearances=1, rankJump=2,
    )
    cls, _conf = _classify_board(b, top_n=10, kaipan_available=False)
    assert cls == "demonBoard"


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
    assert snap.kaipanBoards == []
    assert "kaipan:disabled" in snap.missing


def test_build_hotspot_rotation_snapshot_with_history(tmp_path: Path) -> None:
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
    db_path = tmp_path / "kss.db"
    save_snapshot(hist, db_path=db_path)

    dates = ["20260618", "20260617", "20260616", "20260613", "20260612"]
    client = FakeTushareClient(
        industry=_make_industry_df(),
        concept=_make_concept_df(),
        trade_cal_df=_trade_cal_for(dates),
    )
    snap = build_hotspot_rotation_snapshot(
        "20260618",
        client=client,
        db_path=db_path,
        lookback_days=5,
        top_n_industry=4,
    )
    assert snap is not None
    chip = next(b for b in snap.industries if b.name == "芯片")
    assert chip.previousRank == 2
    assert chip.rankJump == 1  # 2 -> 1
    assert chip.top3Appearances == 1
    assert chip.streakDays == 0


def test_build_hotspot_rotation_snapshot_both_missing() -> None:
    client = FakeTushareClient(industry=None, concept=None)
    snap = build_hotspot_rotation_snapshot("20260618", client=client)
    assert snap is None


def test_build_hotspot_rotation_snapshot_em_industry_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tushare 行业空、概念有数时，东财兜底应撑起行业榜."""
    em = pd.DataFrame({
        "name": ["航空机场"],
        "ts_code": ["BK0420.DC"],
        "pct_change": [0.77],
        "net_amount_rate": [3.19],
        "buy_elg_amount_rate": [2.5],
        "content_type": ["行业"],
        "em_source": ["em_push2delay"],
    })
    monkeypatch.setattr(
        "kss.sector.hotspot_rotation.fetch_industry_fundflow_em",
        lambda trade_date, **kwargs: em,
    )
    dates = ["20260618", "20260617"]
    client = FakeTushareClient(
        industry=None, concept=_make_concept_df(), trade_cal_df=_trade_cal_for(dates),
    )
    snap = build_hotspot_rotation_snapshot(
        "20260618", client=client, lookback_days=2, enable_kaipan=False, enable_leaders=False,
    )
    assert snap is not None
    assert any(b.name == "航空机场" for b in snap.industries)
    assert "industry:em_datacenter_coarse" not in snap.missing


def test_build_hotspot_rotation_snapshot_all_concept_falls_back_to_em(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tushare 只返回概念行时，过滤后为空，应走东财兜底."""
    em = pd.DataFrame({
        "name": ["航空机场"],
        "ts_code": ["BK0420.DC"],
        "pct_change": [0.77],
        "net_amount_rate": [3.19],
        "buy_elg_amount_rate": [2.5],
        "content_type": ["行业"],
        "em_source": ["em_datacenter"],
    })
    monkeypatch.setattr(
        "kss.sector.hotspot_rotation.fetch_industry_fundflow_em",
        lambda trade_date, **kwargs: em,
    )
    df = _make_industry_df()
    df["content_type"] = "概念"
    dates = ["20260618", "20260617"]
    client = FakeTushareClient(
        industry=df, concept=_make_concept_df(), trade_cal_df=_trade_cal_for(dates),
    )
    snap = build_hotspot_rotation_snapshot(
        "20260618", client=client, lookback_days=2,
        enable_kaipan=False, enable_leaders=False,
    )
    assert snap is not None
    assert any(b.name == "航空机场" for b in snap.industries)
    assert "industry:em_datacenter_coarse" in snap.missing


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

    db_path = tmp_path / "kss.db"
    save_snapshot(snap, db_path=db_path)
    from kss.storage.sector_rotation import read_by_date

    loaded = read_by_date("20260618", db_path)
    assert loaded is not None
    assert loaded["tradeDate"] == "20260618"
    assert loaded["industries"][0]["todayRank"] == 1


# ============== Phase 3 测试 ==============


def test_build_name_to_code_map() -> None:
    data = {
        "today_top": [
            {"name": "芯片", "code": "801001"},
            {"name": "通信", "code": "801660"},
        ]
    }
    m = _build_name_to_code_map(data)
    assert m == {"芯片": "801001", "通信": "801660"}


def test_fetch_leaders_for_boards() -> None:
    boards = [
        HotspotBoard(name="芯片", source="kaipan", boardCode="801001", todayRank=1),
        HotspotBoard(name="通信", source="kaipan", boardCode="801660", todayRank=2),
    ]
    name_to_code = {"芯片": "801001", "通信": "801660"}

    def fake_fetch_long_by_plate(code: str, days: int) -> dict | None:
        return {
            "platecode": code,
            "dates": ["20260618", "20260617"],
            "daily_heads": [
                {"date": "20260618", "heads": [{"code": "600353", "name": "旭光电子", "rank": "龙一"}]},
                {"date": "20260617", "heads": [{"code": "600353", "name": "旭光电子", "rank": "龙一"}]},
            ],
        }

    with patch("kss.sector.hotspot_rotation.fetch_long_by_plate", side_effect=fake_fetch_long_by_plate):
        leaders, missing = _fetch_leaders_for_boards(
            boards, name_to_code, lookback_days=2, top_n_stocks=5, max_boards=10
        )

    assert not missing
    assert len(leaders) == 2
    assert boards[0].leaderStocks is not None
    assert boards[0].leaderStocks[0]["code"] == "600353"
    assert boards[0].leaderStocks[0]["count"] == 2


def test_build_hotspot_rotation_snapshot_with_leaders(tmp_path: Path) -> None:
    dates = ["20260618", "20260617", "20260616"]
    client = FakeTushareClient(
        industry=_make_industry_df(),
        concept=_make_concept_df(),
        trade_cal_df=_trade_cal_for(dates),
    )

    def fake_fetch_plate_rotat_data(source: str, days: int) -> dict:
        if source == "kaipan":
            return {
                "today_top": [
                    {"rank": 1, "name": "芯片", "code": "801001", "value": "10000"},
                    {"rank": 2, "name": "通信", "code": "801660", "value": "8000"},
                ]
            }
        return {"today_top": []}

    def fake_fetch_long_by_plate(code: str, days: int) -> dict | None:
        return {
            "platecode": code,
            "dates": ["20260618", "20260617"],
            "daily_heads": [
                {"date": "20260618", "heads": [{"code": "600353", "name": "旭光电子", "rank": "龙一"}]},
                {"date": "20260617", "heads": [{"code": "600353", "name": "旭光电子", "rank": "龙一"}]},
            ],
        }

    with (
        patch("kss.sector.hotspot_rotation.fetch_plate_rotat_data", side_effect=fake_fetch_plate_rotat_data),
        patch("kss.sector.hotspot_rotation.fetch_long_by_plate", side_effect=fake_fetch_long_by_plate),
    ):
        snap = build_hotspot_rotation_snapshot(
            "20260618",
            client=client,
            db_path=tmp_path / "kss.db",
            lookback_days=3,
            enable_kaipan=True,
            enable_leaders=True,
            leaders_top_n_boards=10,
        )

    assert snap is not None
    assert len(snap.kaipanBoards) == 2
    assert snap.leaderCoverage >= 0.5
    assert len(snap.leaderBoards) >= 2
    chip = next(b for b in snap.kaipanBoards if b.name == "芯片")
    assert chip.leaderStocks is not None
    assert chip.leaderStocks[0]["count"] == 2


def test_build_hotspot_rotation_snapshot_leader_name_mapping(tmp_path: Path) -> None:
    # Tushare 概念名与 THS 名相同，通过 name_to_code 映射获取龙头
    dates = ["20260618", "20260617", "20260616"]
    client = FakeTushareClient(
        industry=_make_industry_df(),
        concept=_make_concept_df(),
        trade_cal_df=_trade_cal_for(dates),
    )

    def fake_fetch_plate_rotat_data(source: str, days: int) -> dict:
        if source == "ths":
            return {
                "today_top": [
                    {"rank": 1, "name": "F5G概念", "code": "886084", "value": "4.94%"},
                ]
            }
        return {"today_top": []}

    def fake_fetch_long_by_plate(code: str, days: int) -> dict | None:
        if code == "886084":
            return {
                "platecode": code,
                "dates": ["20260618", "20260617"],
                "daily_heads": [
                    {"date": "20260618", "heads": [{"code": "600353", "name": "旭光电子", "rank": "龙一"}]},
                ],
            }
        return None

    with (
        patch("kss.sector.hotspot_rotation.fetch_plate_rotat_data", side_effect=fake_fetch_plate_rotat_data),
        patch("kss.sector.hotspot_rotation.fetch_long_by_plate", side_effect=fake_fetch_long_by_plate),
    ):
        snap = build_hotspot_rotation_snapshot(
            "20260618",
            client=client,
            db_path=tmp_path / "kss.db",
            lookback_days=3,
            enable_kaipan=True,  # 需要触发 THS 响应获取 name_to_code
            enable_leaders=True,
            leaders_top_n_boards=10,
        )

    assert snap is not None
    f5g = next(b for b in snap.concepts if b.name == "F5G概念")
    assert f5g.leaderStocks is not None
    assert f5g.leaderStocks[0]["code"] == "600353"
