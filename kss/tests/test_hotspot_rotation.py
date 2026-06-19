"""板块热点轮动单日快照测试."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from kss.sector.hotspot_rotation import (
    HotspotBoard,
    HotspotRotationSnapshot,
    _build_boards,
    build_hotspot_rotation_snapshot,
    save_snapshot,
    snapshot_to_dict,
)


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


class FakeTushareClient:
    """假的 TushareClient，用于隔离外部 API."""

    def __init__(self, *, industry: pd.DataFrame | None = None, concept: pd.DataFrame | None = None) -> None:
        self._industry = industry
        self._concept = concept

    def fetch_moneyflow_ind_dc(self, trade_date: str) -> pd.DataFrame | None:
        return self._industry

    def fetch_moneyflow_cnt_ths(self, trade_date: str) -> pd.DataFrame | None:
        return self._concept


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
    # 按 pct_change 降序，芯片应为第 1
    assert boards[0].name == "芯片"
    assert boards[0].todayRank == 1
    assert boards[0].source == "industry"
    assert boards[0].boardCode == "801001.SI"
    assert boards[0].pctChange == pytest.approx(5.23)
    assert boards[0].heatScore is not None
    # 银行为最后一名
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


def test_build_hotspot_rotation_snapshot_success() -> None:
    client = FakeTushareClient(
        industry=_make_industry_df(),
        concept=_make_concept_df(),
    )
    snap = build_hotspot_rotation_snapshot(
        "20260618",
        client=client,
        top_n_industry=3,
        top_n_concept=2,
    )
    assert snap is not None
    assert snap.tradeDate == "20260618"
    assert snap.tradingDaysUsed == ["20260618"]
    assert len(snap.industries) == 3
    assert len(snap.concepts) == 2
    # Phase 1 占位字段
    assert snap.industries[0].top3Appearances == 0
    assert snap.industries[0].previousRank is None
    assert snap.industries[0].classification == "satellite"
    assert snap.kaipanBoards == []
    assert snap.crossSourceSignals["mainline"] == []


def test_build_hotspot_rotation_snapshot_both_missing() -> None:
    client = FakeTushareClient(industry=None, concept=None)
    snap = build_hotspot_rotation_snapshot("20260618", client=client)
    assert snap is None


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


def test_build_hotspot_rotation_snapshot_industry_content_type_filter() -> None:
    df = _make_industry_df()
    df.loc[0, "content_type"] = "概念"  # 芯片不应被过滤掉，但验证 content_type 过滤逻辑
    # 这里全部仍是 "行业"，除了第 0 行变成 "概念"
    client = FakeTushareClient(industry=df, concept=None)
    snap = build_hotspot_rotation_snapshot("20260618", client=client)
    assert snap is not None
    # content_type != 行业的行应被过滤
    assert not any(b.name == "芯片" for b in snap.industries)
    assert len(snap.industries) == 3
