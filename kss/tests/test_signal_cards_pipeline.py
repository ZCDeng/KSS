"""U6: pipeline 部分失败隔离 + 可复算 + 无数据源明确失败。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from kss.signal_cards.pipeline import GENERATORS, build_for_date
from kss.storage import etf_radar as etf_store
from kss.storage.db import ensure_schema_at
from kss.storage.signal_cards import read_by_date


def _load_build_cli():
    path = Path(__file__).resolve().parents[2] / "scripts" / "build_signal_cards.py"
    spec = importlib.util.spec_from_file_location("build_signal_cards_cli", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_partial_failure_continues(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    etf_store.write_snapshot(
        {
            "trade_date": "20260717",
            "data_date": "20260717",
            "stale": False,
            "themes": {
                "芯片": {
                    "flow_5d": -3.0,
                    "flow_1d": -1.0,
                    "past5_ret": 1.0,
                    "divergence": False,
                    "accel": False,
                    "n_funds": 1,
                    "rank_5d": 1,
                }
            },
        },
        db_path=db,
    )

    def boom(_date: str, **_kw):
        raise RuntimeError("boom")

    gens = [("etf_flow", GENERATORS[0][1]), ("broken", boom)]
    result = build_for_date(
        "20260717", db_path=db, write=True, generators=gens  # type: ignore[arg-type]
    )
    assert result.failed_generators
    assert any(f["generator"] == "broken" for f in result.failed_generators)
    assert any(c["card_type"] == "etf_flow" for c in result.cards)


def test_recomputable(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    etf_store.write_snapshot(
        {
            "trade_date": "20260717",
            "data_date": "20260717",
            "stale": False,
            "themes": {
                "芯片": {
                    "flow_5d": -3.0,
                    "flow_1d": -1.0,
                    "past5_ret": 1.0,
                    "divergence": False,
                    "accel": False,
                    "n_funds": 1,
                    "rank_5d": 1,
                }
            },
        },
        db_path=db,
    )
    gens = [("etf_flow", GENERATORS[0][1])]
    r1 = build_for_date("20260717", db_path=db, write=True, generators=gens)
    r2 = build_for_date("20260717", db_path=db, write=True, generators=gens)
    ids1 = sorted(c["card_id"] for c in r1.cards)
    ids2 = sorted(c["card_id"] for c in r2.cards)
    assert ids1 == ids2
    stored = read_by_date("20260717", db_path=db)
    assert sorted(c["card_id"] for c in stored) == ids1


def test_no_source_day_pipeline_zero_cards(tmp_path: Path) -> None:
    """无任何源数据时 pipeline 产零卡（不抛、不伪成功）。"""
    db = tmp_path / "empty.db"
    ensure_schema_at(db)
    result = build_for_date("20990101", db_path=db, write=True)
    assert result.cards == []
    assert result.written == 0
    # by_type 会被填成各生成器 0 计数——不能用「空 dict」判断无源
    assert result.by_type
    assert all(n == 0 for n in result.by_type.values())


def test_no_source_day_cli_exits_nonzero(tmp_path: Path, capsys) -> None:
    """CLI 对无源日必须非零退出（修 by_type 永非空导致的假成功）。"""
    db = tmp_path / "empty.db"
    ensure_schema_at(db)
    cli = _load_build_cli()
    rc = cli.main(["--date", "20990101", "--db", str(db)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "no cards produced" in err
