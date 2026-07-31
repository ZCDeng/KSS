"""风格对照日更 runner U3."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from kss.storage.style_contrast import read_day
from kss.strategies.style_runner import run_style_contrast_day
from kss.strategies.styles import STYLE_ORDER


def _mini_panel(n_sym: int = 12, n_days: int = 6) -> pd.DataFrame:
    dates = pd.bdate_range("2026-01-05", periods=n_days)
    rows = []
    for d in dates:
        for i in range(n_sym):
            rows.append(
                {
                    "trade_date": d,
                    "symbol": f"{600000 + i}.SH",
                    "volatility_20d": 0.01 * (i + 1),
                    "pb": 0.5 + 0.1 * i,
                    "ret_5d": -0.08 + 0.01 * i,
                    "sector_momentum_score": float(n_sym - i),
                    "close": 10.0 + i,
                    "open": 10.0 + i,
                    "next_open_ret": 0.001 * ((i % 3) - 1),
                }
            )
    return pd.DataFrame(rows)


def test_run_four_slots_ok(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    panel = _mini_panel()
    date = str(panel["trade_date"].max().date())
    result = run_style_contrast_day(
        panel,
        prediction_date=date,
        top_n=3,
        db_path=db,
        evaluate_gate=False,
    )
    assert result["ok_count"] == 4
    assert result["failed_count"] == 0
    slots = read_day(date, db_path=db)
    assert len(slots) == 4
    assert all(s["status"] == "ok" for s in slots)
    assert all(len(s["picks"]) == 3 for s in slots)


def test_one_style_failure_isolated(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    panel = _mini_panel()
    # 弄坏价值因子 → value 失败，其余成功
    panel["pb"] = np.nan
    date = str(panel["trade_date"].max().date())
    result = run_style_contrast_day(
        panel,
        prediction_date=date,
        top_n=3,
        db_path=db,
        evaluate_gate=False,
    )
    assert result["failed_count"] >= 1
    assert result["ok_count"] >= 2
    slots = {s["style_id"]: s for s in read_day(date, db_path=db)}
    assert slots["style_value"]["status"] == "failed"
    assert slots["style_value"]["error"]
    assert slots["style_low_vol"]["status"] == "ok"


def test_does_not_write_formal_paper_trade(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    panel = _mini_panel()
    date = str(panel["trade_date"].max().date())
    run_style_contrast_day(panel, prediction_date=date, db_path=db, evaluate_gate=False)
    from kss.storage.paper_trade import day_exists, read_day as formal_read

    assert not day_exists(date, db_path=db)
    assert formal_read(date, db_path=db) is None
    assert [s["style_id"] for s in read_day(date, db_path=db)] == list(STYLE_ORDER)
