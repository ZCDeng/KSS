"""S/R 位命中统计报告脚本单测（U6）."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import report_sr_hit_stats as report  # noqa: E402


def _wave_df(n: int = 300) -> pd.DataFrame:
    t = np.arange(n)
    close = 105 + 5 * np.sin(2 * np.pi * t / 40)
    return pd.DataFrame(
        {
            "trade_date": pd.bdate_range("2023-01-02", periods=n),
            "open": close, "high": close, "low": close, "close": close,
        }
    )


def test_collect_produces_one_row_per_symbol(tmp_path: Path) -> None:
    _wave_df().to_csv(tmp_path / "cs_data_688017.csv", index=False)
    _wave_df().to_csv(tmp_path / "cs_data_688322.csv", index=False)
    df = report.collect(["688017.SH", "688322.SH"], tmp_path)
    assert len(df) == 2
    assert set(df["symbol"]) == {"688017.SH", "688322.SH"}
    ok_rows = df[df["status"] == "ok"]
    assert len(ok_rows) == 2
    assert (ok_rows["rebound_rate"].fillna(0) >= 0).all()


def test_collect_missing_symbol_recorded_as_skipped(tmp_path: Path) -> None:
    df = report.collect(["688999.SH"], tmp_path)
    assert len(df) == 1
    assert df.iloc[0]["status"] == "skipped"
    assert df.iloc[0]["reason"]


def test_collect_exception_recorded_as_error_not_raised(tmp_path: Path, monkeypatch) -> None:
    def _boom(symbol: str, root: Path):
        raise RuntimeError("模拟故障")

    monkeypatch.setattr(report, "load_ohlcv", _boom)
    df = report.collect(["688017.SH"], tmp_path)
    assert len(df) == 1
    assert df.iloc[0]["status"] == "error"
    assert "模拟故障" in df.iloc[0]["reason"]


def test_main_writes_dated_csv_and_md(tmp_path: Path, monkeypatch) -> None:
    from kss.storage.watchlist import set_watchlist

    monkeypatch.setenv("KSS_STATE_ROOT", str(tmp_path))
    _wave_df().to_csv(tmp_path / "cs_data_688017.csv", index=False)
    set_watchlist(["688017.SH"], db_path=tmp_path / "storage" / "kss.db")
    monkeypatch.setattr(sys, "argv", ["report_sr_hit_stats.py", "--asof", "2026-07-20"])
    rc = report.main()
    assert rc == 0
    out_dir = tmp_path / "storage" / "reports" / "indicator_lab"
    csv_path = out_dir / "sr_hit_stats_2026-07-20.csv"
    md_path = out_dir / "sr_hit_stats_2026-07-20.md"
    assert csv_path.exists()
    assert md_path.exists()
    assert "688017.SH" in md_path.read_text(encoding="utf-8")


def test_main_empty_watchlist_skips_without_error(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("KSS_STATE_ROOT", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["report_sr_hit_stats.py"])
    rc = report.main()
    assert rc == 0
