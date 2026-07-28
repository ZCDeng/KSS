"""ui_surface resolve 单测。"""

from __future__ import annotations

from kss.ui_surface.resolve import (
    METRIC_CATALOG,
    candidate_overnight,
    effective_overnight_universe,
    resolve_metric_props,
    resolve_overnight_preview,
)
from scripts.overnight_us_universe import OVERNIGHT_US_UNIVERSE


def test_candidate_defaults_match_universe() -> None:
    cand = candidate_overnight()
    default_codes = [r["code"] for r in OVERNIGHT_US_UNIVERSE]
    assert [c["code"] for c in cand[: len(default_codes)]] == default_codes


def test_effective_append_after_defaults() -> None:
    u = effective_overnight_universe([
        {"code": "AAPL", "name": "苹果", "kind": "yfinance"},
    ])
    codes = [r["code"] for r in u]
    assert codes[:12] == [r["code"] for r in OVERNIGHT_US_UNIVERSE]
    assert codes[-1] == "AAPL"


def test_preview_pending_user_without_quote() -> None:
    cfg = {
        "overnight_us": {
            "append": [{
                "code": "AAPL",
                "name": "苹果",
                "kind": "yfinance",
                "kind_source": "candidate_table",
            }],
        },
    }
    prev = resolve_overnight_preview({"overnightUS": []}, config=cfg)
    user = [p for p in prev if p["code"] == "AAPL"]
    assert len(user) == 1
    assert user[0]["pending"] is True
    assert user[0]["isUserAppended"] is True


def test_metric_limit_max_board() -> None:
    props = resolve_metric_props(
        {"limitBoard": {"maxBoard": 4, "sealRate": 0.55}},
        "limit_max_board",
    )
    assert props["value"] == 4
    assert "4" in props["valueText"]


def test_metric_missing_reason() -> None:
    props = resolve_metric_props({}, "limit_max_board")
    assert props["value"] is None
    assert props["reason"] == "no_limit_board"


def test_metric_index_from_board() -> None:
    props = resolve_metric_props(
        {
            "indexBoard": [
                {"code": "000688.SH", "name": "科创50", "close": 100.5, "pct": 1.2},
            ],
        },
        "index_kcb50",
    )
    assert props["value"] == 100.5
    assert props["delta"] == 1.2


def test_catalog_has_default() -> None:
    assert "limit_max_board" in METRIC_CATALOG
    assert "north_money" not in METRIC_CATALOG
