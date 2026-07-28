"""limit_board 聚合单测。"""

from __future__ import annotations

import pandas as pd

from kss.ui_surface.limit_board import aggregate_limit_board


def test_aggregate_max_board_and_tiers() -> None:
    df = pd.DataFrame([
        {"limit": "U", "limit_times": 1, "open_times": 0},
        {"limit": "U", "limit_times": 2, "open_times": 1},
        {"limit": "U", "limit_times": 4, "open_times": 0},
        {"limit": "D", "limit_times": 1, "open_times": 0},
    ])
    board = aggregate_limit_board(df)
    assert board is not None
    assert board["maxBoard"] == 4
    assert board["total"] == 3
    assert board["sealRate"] == pytest_approx_2_of_3()
    levels = {t["level"]: t["count"] for t in board["tiers"]}
    assert levels[1] == 1
    assert levels[2] == 1
    assert levels[4] == 1


def pytest_approx_2_of_3() -> float:
    return 2 / 3


def test_empty_returns_none() -> None:
    assert aggregate_limit_board(pd.DataFrame()) is None


def test_zero_open_times_seal() -> None:
    df = pd.DataFrame([
        {"limit": "U", "limit_times": 1, "open_times": 0},
        {"limit": "U", "limit_times": 1, "open_times": 0},
    ])
    board = aggregate_limit_board(df)
    assert board is not None
    assert board["sealRate"] == 1.0
    assert board["breakRate"] == 0.0


def test_missing_open_times_seal_null() -> None:
    df = pd.DataFrame([
        {"limit": "U", "limit_times": 3},
    ])
    board = aggregate_limit_board(df)
    assert board is not None
    assert board["maxBoard"] == 3
    assert board["sealRate"] is None
