from __future__ import annotations

import pytest

from kss.memory.temporal_decay import (
    apply_decay,
    decay_multiplier,
    parse_date_from_filename,
    timestamp_ms_for_date,
)
from kss.memory.types import Candidate


def test_decay_multiplier_half_life():
    assert decay_multiplier(30, 30) == pytest.approx(0.5)
    assert decay_multiplier(0, 30) == 1.0
    assert decay_multiplier(-1, 30) == 1.0
    assert decay_multiplier(10, 0) == 1.0


def test_parse_date_from_per_symbol_filename_only():
    assert parse_date_from_filename("2026-06-18_688017.SH.md").strftime("%Y-%m-%d") == "2026-06-18"
    assert parse_date_from_filename("2026-06-18.md") is None
    assert parse_date_from_filename("MEMORY.md") is None
    assert parse_date_from_filename("2026-13-40_688017.SH.md") is None


def test_apply_decay_keeps_evergreen_score():
    now = timestamp_ms_for_date("2026-06-18")
    dated = Candidate("dated", "横盘震荡", timestamp_ms_for_date("2026-05-19"), score=1.0)
    evergreen = Candidate("evergreen", "长期逻辑", None, score=0.7)
    out = apply_decay([dated, evergreen], now_ms=now, half_life_days=30)
    assert out[0].score == pytest.approx(0.5)
    assert out[1].score == pytest.approx(0.7)
