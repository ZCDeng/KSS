"""隔夜美股名单与 merge 顺序。"""
from __future__ import annotations

from scripts.overnight_us_universe import (
    OVERNIGHT_US_UNIVERSE,
    merge_overnight_quotes,
)


def test_universe_has_twelve_no_spacex():
    assert len(OVERNIGHT_US_UNIVERSE) == 12
    codes = [r["code"] for r in OVERNIGHT_US_UNIVERSE]
    assert "SPACEX" not in codes
    assert codes[0] == "MCHI"
    assert codes[1] == "IXIC"
    assert codes[-1] == "AVGO"
    assert codes.index("NVDA") < codes.index("TSLA")


def test_merge_preserves_universe_order_skips_missing():
    fetched = [
        {"code": "TSLA", "close": 200.0, "pct": 1.0, "date": "20260709"},
        {"code": "IXIC", "close": 25000.0, "pct": -0.5, "date": "20260708"},
        {"code": "NVDA", "close": 100.0, "pct": 2.0},  # missing close ok? has both
    ]
    out = merge_overnight_quotes(fetched)
    assert [x["code"] for x in out] == ["IXIC", "NVDA", "TSLA"]
    assert out[0]["name"]  # default name from universe


def test_merge_all_fail_empty():
    assert merge_overnight_quotes([]) == []
    assert merge_overnight_quotes([{"code": "IXIC"}]) == []  # no close/pct
