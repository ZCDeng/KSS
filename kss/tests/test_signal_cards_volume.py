"""U4: volume_spike CSV 读取与空 volume_ratio。"""

from __future__ import annotations

from pathlib import Path

from kss.signal_cards.volume_spike import (
    PCT_CHG_ABS_TH,
    VOLUME_RATIO_TH,
    generate_for_date,
)


def _write_csv(path: Path, rows: list[str]) -> None:
    header = (
        "ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,"
        "vol,amount,turnover_rate,volume_ratio,pe,pb,total_mv\n"
    )
    path.write_text(header + "\n".join(rows) + "\n", encoding="utf-8")


def test_volume_spike_both_thresholds(tmp_path: Path) -> None:
    p = tmp_path / "cs_data_688017.csv"
    _write_csv(
        p,
        [
            "688017.SH,2026-07-28,1,1,1,1,1,0,5.0,1,1,1,3.0,1,1,1",
            "688017.SH,2026-07-27,1,1,1,1,1,0,5.0,1,1,1,1.0,1,1,1",  # vr low
        ],
    )
    # only high vr day
    cards = generate_for_date("20260728", root=tmp_path)
    assert len(cards) == 1
    assert cards[0]["trade_date"] == "20260728"
    assert cards[0]["direction"] is None
    assert cards[0]["threshold_source"] == "convention"
    assert cards[0]["metrics"]["volume_ratio"] == 3.0


def test_single_threshold_no_card(tmp_path: Path) -> None:
    p = tmp_path / "cs_data_688017.csv"
    _write_csv(
        p,
        ["688017.SH,2026-07-28,1,1,1,1,1,0,5.0,1,1,1,1.0,1,1,1"],  # vr < 2
    )
    assert generate_for_date("20260728", root=tmp_path) == []
    _write_csv(
        p,
        ["688017.SH,2026-07-28,1,1,1,1,1,0,0.5,1,1,1,5.0,1,1,1"],  # pct low
    )
    assert generate_for_date("20260728", root=tmp_path) == []


def test_empty_volume_ratio_insufficient(tmp_path: Path) -> None:
    p = tmp_path / "cs_data_688017.csv"
    _write_csv(
        p,
        ["688017.SH,2026-07-28,1,1,1,1,1,0,5.0,1,1,1,,1,1,1"],
    )
    cards = generate_for_date("20260728", root=tmp_path)
    assert len(cards) == 1
    assert cards[0]["coverage"] == "insufficient_data"


def test_missing_file_no_raise(tmp_path: Path) -> None:
    assert generate_for_date("20260728", root=tmp_path) == []


def test_date_conversion(tmp_path: Path) -> None:
    p = tmp_path / "cs_data_688017.csv"
    _write_csv(
        p,
        ["688017.SH,2026-07-28,1,1,1,1,1,0,5.0,1,1,1,3.0,1,1,1"],
    )
    cards = generate_for_date("2026-07-28", root=tmp_path)
    assert cards[0]["trade_date"] == "20260728"


def test_does_not_scan_cs_data_subdir(tmp_path: Path) -> None:
    sub = tmp_path / "cs_data"
    sub.mkdir()
    _write_csv(
        sub / "cs_data_688017.csv",
        ["688017.SH,2026-07-28,1,1,1,1,1,0,9.0,1,1,1,9.0,1,1,1"],
    )
    assert generate_for_date("20260728", root=tmp_path) == []
