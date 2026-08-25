"""Display-only heatmap snapshot (plan U2). Fail closed on sample / undated / empty."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import kss_app_bridge as b  # noqa: E402
from kss.heatmap.snapshot import (  # noqa: E402
    HeatmapSnapshotError,
    build_snapshot,
    load_snapshot,
    parse_eastmoney_diff,
    reset_cache,
)

TRADE_TS = 1_724_572_800  # 2024-08-25 16:00 +08


def _row(
    code: str,
    *,
    flag: int = 0,
    name: str = "测试",
    industry: str = "银行",
    circ: float = 1e10,
    day: float = 1.2,
    week: float = 3.4,
    month: float = 5.6,
    year: float = 7.8,
    turnover: float = 2e9,
    price: float = 10.0,
    ts: int = TRADE_TS,
) -> dict:
    return {
        "f12": code,
        "f13": flag,
        "f14": name,
        "f2": price,
        "f3": day,
        "f6": turnover,
        "f18": price,
        "f20": circ,
        "f21": circ,
        "f24": month,
        "f25": year,
        "f100": industry,
        "f109": week,
        "f110": month,
        "f124": ts,
    }


def _rows() -> list[dict]:
    return parse_eastmoney_diff(
        [
            _row("000001", flag=0, name="平安银行", circ=8e10, day=1.0, week=2.0),
            _row("600000", flag=1, name="浦发银行", circ=6e10, day=-0.5, week=-1.5),
            _row("300750", flag=0, name="宁德时代", industry="电力设备", circ=9e10, day=0.0, week=4.0),
            _row("688981", flag=1, name="中芯国际", industry="电子", circ=4e10, day=2.5, week=6.0),
        ]
    )


@pytest.fixture(autouse=True)
def _clear_heatmap_cache() -> None:
    reset_cache()
    yield
    reset_cache()


def test_live_fixture_yields_weight_return_industry_and_breadth() -> None:
    out = build_snapshot(
        rows=_rows(),
        market="all",
        period="day",
        source="direct",
        trade_date="20240825",
        updated_at="2024-08-25T16:00:00+08:00",
    )
    assert out["source"] == "direct"
    assert out["tradeDate"] == "20240825"
    assert {tile["symbol"] for tile in out["tiles"]} >= {"000001.SZ", "600000.SH"}
    bank = next(tile for tile in out["tiles"] if tile["symbol"] == "000001.SZ")
    assert bank["circMv"] == 8e10
    assert bank["changePct"] == 1.0
    assert bank["industry"] == "银行"
    summary = out["summary"]
    assert summary["advanceCount"] + summary["flatCount"] + summary["declineCount"] == len(out["tiles"])
    assert summary["turnoverAmount"] > 0


def test_period_and_market_change_return_and_constituents() -> None:
    rows = _rows()
    day = build_snapshot(
        rows=rows,
        market="all",
        period="day",
        source="direct",
        trade_date="20240825",
        updated_at="2024-08-25T16:00:00+08:00",
    )
    week = build_snapshot(
        rows=rows,
        market="all",
        period="week",
        source="direct",
        trade_date="20240825",
        updated_at="2024-08-25T16:00:00+08:00",
    )
    cyb = build_snapshot(
        rows=rows,
        market="cyb",
        period="day",
        source="direct",
        trade_date="20240825",
        updated_at="2024-08-25T16:00:00+08:00",
    )
    ping = next(tile for tile in day["tiles"] if tile["symbol"] == "000001.SZ")
    ping_week = next(tile for tile in week["tiles"] if tile["symbol"] == "000001.SZ")
    assert ping["changePct"] != ping_week["changePct"]
    assert {tile["symbol"] for tile in cyb["tiles"]} == {"300750.SZ"}


@pytest.mark.parametrize("source", ["fallback", "sample", "demo"])
def test_sample_or_fallback_is_failure(source: str) -> None:
    with pytest.raises(HeatmapSnapshotError, match="sample or fallback"):
        build_snapshot(
            rows=_rows(),
            market="all",
            period="day",
            source=source,
            trade_date="20240825",
            updated_at="2024-08-25T16:00:00+08:00",
        )


def test_undated_payload_is_failure() -> None:
    with pytest.raises(HeatmapSnapshotError, match="trade date"):
        build_snapshot(
            rows=_rows(),
            market="all",
            period="day",
            source="direct",
            trade_date=None,
            updated_at="2024-08-25T16:00:00+08:00",
        )


def test_empty_constituents_are_failure() -> None:
    with pytest.raises(HeatmapSnapshotError, match="empty"):
        build_snapshot(
            rows=[],
            market="all",
            period="day",
            source="direct",
            trade_date="20240825",
            updated_at="2024-08-25T16:00:00+08:00",
        )


def test_upstream_5xx_and_timeout_are_failure() -> None:
    def boom(_url: str, _headers: dict[str, str], _timeout: float) -> dict:
        raise HeatmapSnapshotError("heatmap upstream returned 502")

    with pytest.raises(HeatmapSnapshotError, match="502"):
        load_snapshot("all", "day", http_get=boom)

    def timeout(_url: str, _headers: dict[str, str], _timeout: float) -> dict:
        raise HeatmapSnapshotError("heatmap upstream timed out")

    with pytest.raises(HeatmapSnapshotError, match="timed out"):
        load_snapshot("all", "day", http_get=timeout)


def test_dispatch_live_fixture_and_rejects_sample(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = _rows()

    def fake_load(market: str = "all", period: str = "day", **_kwargs):
        return build_snapshot(
            rows=rows,
            market=market,
            period=period,
            source="direct",
            trade_date="20240825",
            updated_at="2024-08-25T16:00:00+08:00",
        )

    monkeypatch.setattr("kss.heatmap.snapshot.load_snapshot", fake_load)
    out = b.dispatch("heatmap-snapshot", ["all", "day"])
    assert out["tiles"]
    assert out["source"] == "direct"

    def fake_sample(*_args, **_kwargs):
        raise HeatmapSnapshotError("heatmap snapshot is sample or fallback, not a live tape")

    monkeypatch.setattr("kss.heatmap.snapshot.load_snapshot", fake_sample)
    with pytest.raises(ValueError, match="sample or fallback"):
        b.dispatch("heatmap-snapshot", ["all", "day"])


def test_command_registered_and_not_a_write() -> None:
    assert "heatmap-snapshot" in b.COMMANDS
    assert "heatmap-snapshot" not in b.WRITE_COMMANDS


def test_helper_does_not_write_backtest_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "storage").mkdir()
    build_snapshot(
        rows=_rows(),
        market="all",
        period="day",
        source="direct",
        trade_date="20240825",
        updated_at="2024-08-25T16:00:00+08:00",
    )
    leftovers = [path for path in tmp_path.rglob("*") if path.is_file()]
    assert leftovers == []
