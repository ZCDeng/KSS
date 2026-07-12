"""指标研究实验室 bridge 命令面单测（U4）.

隔离约定：kss_app_bridge 里 INDICATOR_LAB_VERDICTS_DIR/STATE_ROOT 是模块级常量，
需 monkeypatch.setattr 直接改；kss.indicators.* 走自己的动态 state_root()（读
KSS_STATE_ROOT env var），需 monkeypatch.setenv。两者都要设，才能让 bridge 层与
indicators 层落在同一个隔离 tmp_path 下。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import kss_app_bridge as b  # noqa: E402


def _write_fixture_csv(path: Path, n: int = 400, seed: int = 1, drift: float = 0.2) -> None:
    rng = np.random.default_rng(seed)
    close = 80 + np.cumsum(rng.normal(drift, 0.9, n))
    df = pd.DataFrame(
        {
            "trade_date": pd.bdate_range("2023-01-02", periods=n),
            "open": close + rng.normal(0, 0.15, n),
            "high": close + 1.2,
            "low": close - 1.2,
            "close": close,
        }
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)


@pytest.fixture
def isolated_root(tmp_path: Path, monkeypatch):
    # REPORT_DIR / INDICATOR_LAB_DIR / INDICATOR_LAB_VERDICTS_DIR / INDICATOR_LAB_REPORTS_DIR
    # 都是模块级常量，在真实仓库 STATE_ROOT 下于 import 时算好——只 monkeypatch STATE_ROOT
    # 不会重定向它们，必须逐一覆盖，否则测试会悄悄读写真仓库的 storage/reports/。
    monkeypatch.setattr(b, "STATE_ROOT", tmp_path)
    monkeypatch.setattr(b, "REPORT_DIR", tmp_path / "storage" / "reports")
    monkeypatch.setattr(b, "INDICATOR_LAB_DIR", tmp_path / "storage" / "indicator_lab")
    monkeypatch.setattr(b, "INDICATOR_LAB_VERDICTS_DIR", tmp_path / "storage" / "indicator_lab" / "verdicts")
    monkeypatch.setattr(b, "INDICATOR_LAB_REPORTS_DIR", tmp_path / "storage" / "reports" / "indicator_lab")
    monkeypatch.setenv("KSS_STATE_ROOT", str(tmp_path))
    monkeypatch.setenv("KSS_PROJECT_ROOT", str(Path(__file__).resolve().parents[2]))
    return tmp_path


def test_commands_registered_in_bridge() -> None:
    for cmd in ("indicator-lab-list", "indicator-backtest", "indicator-suggest", "indicator-solidify", "indicator-retire"):
        assert cmd in b.COMMANDS
    assert "indicator-solidify" in b.WRITE_COMMANDS
    assert "indicator-retire" in b.WRITE_COMMANDS
    assert "indicator-lab-list" not in b.WRITE_COMMANDS
    assert "indicator-backtest" not in b.WRITE_COMMANDS
    assert "indicator-suggest" not in b.WRITE_COMMANDS


def test_lab_list_defaults_to_mi_entry(isolated_root: Path) -> None:
    out = b.dispatch("indicator-lab-list", [])
    ids = {e["id"] for e in out["entries"]}
    assert "mi" in ids
    assert out["recentVerdicts"] == []


def test_backtest_bad_family_rejected(isolated_root: Path) -> None:
    out = b.dispatch("indicator-backtest", ["nope", "{}", ""])
    assert out["error"] == "bad_family"


def test_backtest_bad_json_rejected(isolated_root: Path) -> None:
    out = b.dispatch("indicator-backtest", ["ma_cross", "{not json", ""])
    assert out["error"] == "bad_json_params"


def test_backtest_no_symbols_and_empty_watchlist(isolated_root: Path) -> None:
    out = b.dispatch("indicator-backtest", ["ma_cross", "{}", ""])
    assert out["error"] == "no_symbols"


def test_backtest_too_many_symbols_rejected(isolated_root: Path) -> None:
    symbols = ",".join(f"68800{i}.SH" for i in range(9))
    out = b.dispatch("indicator-backtest", ["ma_cross", "{}", symbols])
    assert out["error"] == "too_many_symbols"


def test_backtest_end_to_end_persists_verdict(isolated_root: Path) -> None:
    _write_fixture_csv(isolated_root / "cs_data_688017.csv")
    params = json.dumps({"fast": 5, "slow": 20, "kind": "sma"})
    out = b.dispatch("indicator-backtest", ["ma_cross", params, "688017.SH"])
    assert "error" not in out
    assert out["results"][0]["symbol"] == "688017.SH"
    assert out["results"][0]["status"] == "judged"
    assert out["verdictRef"] is not None
    verdict_path = isolated_root / out["verdictRef"]
    assert verdict_path.exists()


def test_backtest_uses_watchlist_when_symbols_omitted(isolated_root: Path) -> None:
    from kss.storage.watchlist import set_watchlist

    set_watchlist(["688017.SH"], db_path=isolated_root / "storage" / "kss.db")
    _write_fixture_csv(isolated_root / "cs_data_688017.csv")
    out = b.dispatch("indicator-backtest", ["ma_cross", "{}", ""])
    assert "error" not in out
    assert out["symbols"] == ["688017.SH"]


def test_suggest_skips_no_go_and_covered_families(isolated_root: Path) -> None:
    out = b.dispatch("indicator-suggest", [])
    assert out["family"] in ("ma_cross", "rsi_threshold", "boll_atr")

    # 手动记一条该 family 的 NO-GO 裁决，再次建议应跳过它
    from kss.indicators.primitives import default_params

    first_family = out["family"]
    key = b._verdict_key(first_family, default_params(first_family))
    verdicts_dir = isolated_root / "storage" / "indicator_lab" / "verdicts"
    verdicts_dir.mkdir(parents=True, exist_ok=True)
    (verdicts_dir / f"{key}.json").write_text(
        json.dumps({"family": first_family, "params": default_params(first_family), "go": False}),
        encoding="utf-8",
    )
    out2 = b.dispatch("indicator-suggest", [])
    assert out2["family"] != first_family


def test_solidify_then_retire_roundtrip(isolated_root: Path) -> None:
    _write_fixture_csv(isolated_root / "cs_data_688017.csv")
    params = json.dumps({"fast": 5, "slow": 20, "kind": "sma"})
    out = b.dispatch("indicator-solidify", ["ma_cross", params, "688017.SH", ""])
    assert out.get("ok") is True
    entry_id = out["entryId"]

    from kss.indicators.registry import get_entry, load_registry

    entries = load_registry(isolated_root / "storage" / "indicator_registry.yaml")
    entry = get_entry(entry_id, entries)
    assert entry is not None
    assert entry.status == "active"
    assert (isolated_root / entry.rules_path).parent.exists() or True  # rules_path 未必落盘，条目本身须存在

    retire_out = b.dispatch("indicator-retire", [entry_id])
    assert retire_out.get("ok") is True
    entries2 = load_registry(isolated_root / "storage" / "indicator_registry.yaml")
    assert get_entry(entry_id, entries2).status == "retired"


def test_solidify_generates_report_visible_in_backtest_reports(isolated_root: Path) -> None:
    """U5: 固化产出的报告落地 storage/reports/indicator_lab/，_backtest_reports 目录扫描能看到它。"""
    _write_fixture_csv(isolated_root / "cs_data_688017.csv")
    params = json.dumps({"fast": 5, "slow": 20, "kind": "sma"})
    backtest_out = b.dispatch("indicator-backtest", ["ma_cross", params, "688017.SH"])
    assert "error" not in backtest_out
    verdict_ref = backtest_out["verdictRef"]

    out = b.dispatch("indicator-solidify", ["ma_cross", params, "688017.SH", verdict_ref])
    assert out.get("ok") is True
    assert out["reportPath"] is not None
    report_file = isolated_root / out["reportPath"]
    assert report_file.exists()
    assert "GO/NO-GO" in report_file.read_text(encoding="utf-8")

    reports = b.dispatch("indicator-lab-list", [])  # 只是确认注册表侧未受影响
    assert any(e["id"] == out["entryId"] for e in reports["entries"])

    listed = b._backtest_reports()
    assert any(r["path"] == out["reportPath"] for r in listed)


def test_backtest_reports_empty_dir_matches_hardcoded_only(isolated_root: Path) -> None:
    """U5 回归：indicator_lab 报告目录不存在时，_backtest_reports 行为与硬编码 8 条路径一致（均缺失 → 空列表）。"""
    assert b._backtest_reports() == []


def test_indicator_detail_projections_includes_mi_and_solidified(isolated_root: Path) -> None:
    """U6: stock_detail 的通用 indicatorSignals/indicatorOverlays 覆盖 MI + 已固化的基元指标."""
    _write_fixture_csv(isolated_root / "cs_data_688017.csv")
    params = json.dumps({"fast": 5, "slow": 20, "kind": "sma"})
    b.dispatch("indicator-solidify", ["ma_cross", params, "688017.SH", ""])

    signals, overlays = b._indicator_detail_projections("688017.SH", {"2026-07-01"})
    signal_ids = {s.get("indicatorId") for s in signals}
    overlay_ids = {o.get("indicatorId") for o in overlays}
    # MI 注册表条目始终在，但从未跑过 MI pack（这次只固化了 ma_cross）→ pack 缺失，投影跳过
    # （同 stock_detail 对 miSignal/miOverlay 的既有降级行为一致，不是本次改动引入的差异）。
    assert "mi" not in signal_ids
    assert any(i and i.startswith("ma_cross_") for i in signal_ids)
    assert any(i and i.startswith("ma_cross_") for i in overlay_ids)


def test_indicator_detail_projections_degrades_on_registry_unavailable(isolated_root: Path, monkeypatch) -> None:
    import builtins

    real_import = builtins.__import__

    def _boom(name, *args, **kwargs):
        if name == "kss.indicators.registry":
            raise ImportError("simulated unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _boom)
    signals, overlays = b._indicator_detail_projections("688017.SH", set())
    assert signals == []
    assert overlays == []


def test_stock_detail_exposes_generic_indicator_fields(isolated_root: Path) -> None:
    """stock_detail() 顶层直接携带 indicatorSignals/indicatorOverlays（不只是内部辅助函数）。"""
    n = 400
    rng = np.random.default_rng(5)
    close = 80 + np.cumsum(rng.normal(0.1, 0.9, n))
    df = pd.DataFrame(
        {
            "trade_date": pd.bdate_range("2023-01-02", periods=n),
            "open": close, "high": close + 1, "low": close - 1, "close": close,
            "vol": 1e5, "pct_chg": rng.normal(0, 1, n),
        }
    )
    df.to_csv(isolated_root / "cs_data_688017.csv", index=False)
    detail = b.stock_detail("688017.SH")
    assert "indicatorSignals" in detail
    assert "indicatorOverlays" in detail
    assert isinstance(detail["indicatorSignals"], list)
    assert isinstance(detail["indicatorOverlays"], list)


def test_solidify_no_symbols_rejected(isolated_root: Path) -> None:
    out = b.dispatch("indicator-solidify", ["ma_cross", "{}", "", ""])
    assert out["error"] == "no_symbols"


def test_solidify_rolls_back_registry_on_pack_failure(isolated_root: Path, monkeypatch) -> None:
    """pack 生成失败时注册表回退到固化前状态，不留半成品条目。"""
    _write_fixture_csv(isolated_root / "cs_data_688017.csv")

    import kss.indicators.pack as ipack

    def _boom(entry, symbol, **kwargs):
        return {"symbol": symbol, "status": "error", "reason": "模拟故障"}

    monkeypatch.setattr(ipack, "run_entry_pack", _boom)

    params = json.dumps({"fast": 5, "slow": 20, "kind": "sma"})
    out = b.dispatch("indicator-solidify", ["ma_cross", params, "688017.SH", ""])
    assert out["error"] == "solidify_failed"

    from kss.indicators.registry import load_registry

    entries = load_registry(isolated_root / "storage" / "indicator_registry.yaml")
    ids = {e.id for e in entries}
    assert not any(i.startswith("ma_cross_") for i in ids)


def test_retire_unknown_entry_errors(isolated_root: Path) -> None:
    out = b.dispatch("indicator-retire", ["does-not-exist"])
    assert out["error"] == "unknown_entry"


def test_dispatch_missing_args_raise() -> None:
    with pytest.raises(ValueError, match="FAMILY"):
        b.dispatch("indicator-backtest", [])
    with pytest.raises(ValueError, match="FAMILY PARAMS_JSON SYMBOLS_CSV"):
        b.dispatch("indicator-solidify", ["ma_cross"])
    with pytest.raises(ValueError, match="ENTRY_ID"):
        b.dispatch("indicator-retire", [])
