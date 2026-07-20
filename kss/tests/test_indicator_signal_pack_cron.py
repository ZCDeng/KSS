"""U8: 通用日终 cron 清单条目 + bridge run_task/RUN_TASKS 接线单测."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import kss_app_bridge as b  # noqa: E402


def test_indicator_signal_pack_in_run_tasks_whitelist() -> None:
    assert "indicator-signal-pack" in b.RUN_TASKS


def test_cron_manifest_has_indicator_signal_pack_job() -> None:
    from kss.config import cron_manifest

    assert cron_manifest.title_for("indicator_signal_pack") == "指标信号包（注册表基元库条目）"
    assert cron_manifest.category_for("indicator_signal_pack") == "扫描选股"
    assert cron_manifest.catchup_eligible("indicator_signal_pack") is True


def test_indicator_signal_pack_wrapper_script_exists_and_executable() -> None:
    wrapper = Path(__file__).resolve().parents[2] / "scripts" / "run_indicator_signal_pack_daily.sh"
    assert wrapper.exists()
    assert wrapper.stat().st_mode & 0o111  # 至少一个可执行位


def test_run_indicator_signal_pack_missing_full_env(monkeypatch) -> None:
    """无完整 Python 环境时明确降级，不静默假成功（同 mi-signal-pack 既有行为）。"""
    monkeypatch.setattr(b, "_full_python", lambda: None)
    out = b.run_task("indicator-signal-pack", [])
    assert out["status"] == "failed"
    assert out["exitCode"] == 127


def test_script_main_refreshes_solidified_entries_and_skips_mi(tmp_path: Path, monkeypatch) -> None:
    """端到端：main() 只遍历 kind=primitive 的 active 条目，MI（mi_legacy）不受影响。"""
    import importlib.util

    import numpy as np
    import pandas as pd

    monkeypatch.setenv("KSS_STATE_ROOT", str(tmp_path))
    monkeypatch.setenv("KSS_PROJECT_ROOT", str(Path(__file__).resolve().parents[2]))

    # kss.prediction.ledger / kss.backtest.factor_health 的默认库路径是模块级常量，
    # 用 Path(__file__) 算死的真仓库路径——不读 KSS_STATE_ROOT（与 kss.indicators.* 的
    # state_root() 惯例不同，这是账本模块既有设计，不在本次改动范围内）。必须显式重定向，
    # 否则测试会真的写进本机生产账本数据库。
    import kss.backtest.factor_health as factor_health_mod
    import kss.prediction.ledger as ledger_mod

    monkeypatch.setattr(ledger_mod, "DEFAULT_LEDGER_PATH", tmp_path / "storage" / "prediction_ledger" / "ledger.db")
    monkeypatch.setattr(factor_health_mod, "DEFAULT_HEALTH_DB", tmp_path / "storage" / "factor_health" / "factor_health.db")

    n = 400
    rng = np.random.default_rng(11)
    close = 80 + np.cumsum(rng.normal(0.15, 0.9, n))
    df = pd.DataFrame(
        {
            "trade_date": pd.bdate_range("2023-01-02", periods=n),
            "open": close, "high": close + 1, "low": close - 1, "close": close,
        }
    )
    df.to_csv(tmp_path / "cs_data_688017.csv", index=False)

    from kss.indicators.registry import KIND_PRIMITIVE, RegistryEntry, upsert_entry

    upsert_entry(
        RegistryEntry(
            id="ma1", name="均线交叉示例", kind=KIND_PRIMITIVE, family="ma_cross",
            params={"fast": 5, "slow": 20, "kind": "sma"},
            rules_path="storage/indicator_rules/ma1.yaml",
            signals_dir="storage/indicator_signals/ma1",
            symbols=["688017.SH"],
        )
    )

    spec = importlib.util.spec_from_file_location(
        "run_indicator_signal_pack_u8",
        Path(__file__).resolve().parents[2] / "scripts" / "run_indicator_signal_pack.py",
    )
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setattr(sys, "argv", ["run_indicator_signal_pack.py"])
    spec.loader.exec_module(mod)
    rc = mod.main()
    assert rc == 0

    import sqlite3

    conn = sqlite3.connect(tmp_path / "storage" / "kss.db")
    row = conn.execute(
        "SELECT 1 FROM indicator_signal_packs WHERE entry_id=? AND symbol=?", ("ma1", "688017.SH")
    ).fetchone()
    mi_row = conn.execute("SELECT 1 FROM mi_signal_packs LIMIT 1").fetchone()
    conn.close()
    assert row is not None
    assert mi_row is None  # MI 不在本脚本范围内，未被触碰

    from kss.prediction.ledger import PredictionLedger

    ledger = PredictionLedger(db_path=tmp_path / "storage" / "prediction_ledger" / "ledger.db")
    records = ledger.query()
    assert any(r["strategy"] == "ma1" for r in records)


def _load_pack_script(tmp_path: Path, monkeypatch):
    """按既有 U8 测试惯例动态加载脚本模块，隔离 KSS_STATE_ROOT/账本默认路径。"""
    import importlib.util

    import kss.backtest.factor_health as factor_health_mod
    import kss.prediction.ledger as ledger_mod

    monkeypatch.setenv("KSS_STATE_ROOT", str(tmp_path))
    monkeypatch.setenv("KSS_PROJECT_ROOT", str(Path(__file__).resolve().parents[2]))
    monkeypatch.setattr(ledger_mod, "DEFAULT_LEDGER_PATH", tmp_path / "storage" / "prediction_ledger" / "ledger.db")
    monkeypatch.setattr(factor_health_mod, "DEFAULT_HEALTH_DB", tmp_path / "storage" / "factor_health" / "factor_health.db")

    spec = importlib.util.spec_from_file_location(
        "run_indicator_signal_pack_u3",
        Path(__file__).resolve().parents[2] / "scripts" / "run_indicator_signal_pack.py",
    )
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setattr(sys, "argv", ["run_indicator_signal_pack.py"])
    spec.loader.exec_module(mod)
    return mod


def _write_cs_data(tmp_path: Path, code: str, n: int = 400, seed: int = 11) -> None:
    import numpy as np
    import pandas as pd

    rng = np.random.default_rng(seed)
    close = 80 + np.cumsum(rng.normal(0.15, 0.9, n))
    df = pd.DataFrame(
        {
            "trade_date": pd.bdate_range("2023-01-02", periods=n),
            "open": close, "high": close + 1, "low": close - 1, "close": close,
        }
    )
    df.to_csv(tmp_path / f"cs_data_{code}.csv", index=False)


def _has_pack_row(tmp_path: Path, entry_id: str, symbol: str) -> bool:
    import sqlite3

    conn = sqlite3.connect(tmp_path / "storage" / "kss.db")
    row = conn.execute(
        "SELECT 1 FROM indicator_signal_packs WHERE entry_id=? AND symbol=?", (entry_id, symbol)
    ).fetchone()
    conn.close()
    return row is not None


def test_symbols_present_ignores_watchlist(tmp_path: Path, monkeypatch) -> None:
    from kss.indicators.registry import KIND_PRIMITIVE, RegistryEntry, upsert_entry
    from kss.storage.watchlist import set_watchlist

    monkeypatch.setenv("KSS_STATE_ROOT", str(tmp_path))
    _write_cs_data(tmp_path, "688017")
    _write_cs_data(tmp_path, "688322", seed=12)
    set_watchlist(["688322.SH"], db_path=tmp_path / "storage" / "kss.db")
    upsert_entry(
        RegistryEntry(
            id="ma1", name="均线交叉示例", kind=KIND_PRIMITIVE, family="ma_cross",
            params={"fast": 5, "slow": 20, "kind": "sma"},
            symbols=["688017.SH"],
        ),
        db_path=tmp_path / "storage" / "kss.db",
    )
    mod = _load_pack_script(tmp_path, monkeypatch)
    assert mod.main() == 0
    assert _has_pack_row(tmp_path, "ma1", "688017.SH")
    assert not _has_pack_row(tmp_path, "ma1", "688322.SH")


def test_unsolidified_empty_symbols_falls_back_to_watchlist(tmp_path: Path, monkeypatch) -> None:
    from kss.indicators.registry import KIND_PRIMITIVE, RegistryEntry, upsert_entry
    from kss.storage.watchlist import set_watchlist

    monkeypatch.setenv("KSS_STATE_ROOT", str(tmp_path))
    _write_cs_data(tmp_path, "688017")
    set_watchlist(["688017.SH"], db_path=tmp_path / "storage" / "kss.db")
    upsert_entry(
        RegistryEntry(id="sr", name="支撑阻力", kind=KIND_PRIMITIVE, family="sr_level", params={}, symbols=[]),
        db_path=tmp_path / "storage" / "kss.db",
    )
    mod = _load_pack_script(tmp_path, monkeypatch)
    assert mod.main() == 0
    assert _has_pack_row(tmp_path, "sr", "688017.SH")


def test_solidified_empty_symbols_does_not_fallback(tmp_path: Path, monkeypatch, capsys) -> None:
    """已固化但 symbols 被清空的条目不外溢到自选股池（不重新拉全池）."""
    from kss.indicators.registry import KIND_PRIMITIVE, RegistryEntry, upsert_entry
    from kss.storage.watchlist import set_watchlist

    monkeypatch.setenv("KSS_STATE_ROOT", str(tmp_path))
    _write_cs_data(tmp_path, "688017")
    set_watchlist(["688017.SH"], db_path=tmp_path / "storage" / "kss.db")
    upsert_entry(
        RegistryEntry(
            id="sr", name="支撑阻力", kind=KIND_PRIMITIVE, family="sr_level", params={},
            symbols=[], solidified_at="2026-07-01",
        ),
        db_path=tmp_path / "storage" / "kss.db",
    )
    mod = _load_pack_script(tmp_path, monkeypatch)
    assert mod.main() == 0
    assert not _has_pack_row(tmp_path, "sr", "688017.SH")
    assert "已固化但标的列表为空" in capsys.readouterr().out


def test_empty_symbols_and_empty_watchlist_skips_with_reason(tmp_path: Path, monkeypatch, capsys) -> None:
    from kss.indicators.registry import KIND_PRIMITIVE, RegistryEntry, upsert_entry

    monkeypatch.setenv("KSS_STATE_ROOT", str(tmp_path))
    upsert_entry(
        RegistryEntry(id="sr", name="支撑阻力", kind=KIND_PRIMITIVE, family="sr_level", params={}, symbols=[]),
        db_path=tmp_path / "storage" / "kss.db",
    )
    mod = _load_pack_script(tmp_path, monkeypatch)
    assert mod.main() == 0
    assert "自选股池为空" in capsys.readouterr().out
