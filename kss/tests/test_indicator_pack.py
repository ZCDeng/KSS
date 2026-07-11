"""通用 Signal Pack 单测（含 MI legacy 委托回归）."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from kss.backtest.indicator_walk_forward import WFConfig
from kss.indicators import pack as ipack
from kss.indicators.primitives import FAMILY_MA_CROSS
from kss.indicators.registry import KIND_MI_LEGACY, KIND_PRIMITIVE, RegistryEntry


def _ma_entry() -> RegistryEntry:
    return RegistryEntry(
        id="ma1",
        name="均线交叉示例",
        kind=KIND_PRIMITIVE,
        family=FAMILY_MA_CROSS,
        params={"fast": 5, "slow": 20, "kind": "sma"},
        rules_path="storage/indicator_rules/ma1.yaml",
        signals_dir="storage/indicator_signals/ma1",
    )


def _write_fixture_csv(path: Path, n: int = 400, seed: int = 42, drift: float = 0.08) -> None:
    rng = np.random.default_rng(seed)
    close = 80 + np.cumsum(rng.normal(drift, 0.85, n))
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


def test_write_read_roundtrip(tmp_path: Path) -> None:
    entry = _ma_entry()
    pack = {
        "schema_version": 1,
        "indicator_id": "ma1",
        "symbol": "688017.SH",
        "asof": "2026-07-10",
        "status": "ok",
        "reason": "",
        "family": "ma_cross",
        "params": {"fast": 5, "slow": 20, "kind": "sma"},
        "unpinned": False,
        "action": "STAY_FLAT",
        "trades": [],
        "trades_preview": [],
        "series": [],
        "rule_sentence": "均线交叉：金叉入场、死叉离场",
        "param_delta": {},
    }
    ipack.write_pack(entry, pack, root=tmp_path)
    loaded = ipack.read_pack(entry, "688017.SH", root=tmp_path)
    assert loaded is not None
    assert loaded["action"] == "STAY_FLAT"


def test_read_pack_bare_code_fallback(tmp_path: Path) -> None:
    entry = _ma_entry()
    pack = {
        "schema_version": 1,
        "indicator_id": "ma1",
        "symbol": "688017.SH",
        "asof": "2026-07-10",
        "status": "ok",
        "action": "BUY",
    }
    ipack.write_pack(entry, pack, root=tmp_path)
    loaded = ipack.read_pack(entry, "688017", root=tmp_path)
    assert loaded is not None
    assert loaded["action"] == "BUY"


def test_to_signal_and_overlay_on_ok_pack() -> None:
    pack = {
        "indicator_id": "ma1",
        "asof": "2026-07-10",
        "status": "ok",
        "reason": "触发入场",
        "action": "BUY",
        "prev_action": "STAY_FLAT",
        "position": "LONG",
        "pred_score": 0.4,
        "pred_bias": "bullish",
        "family": "ma_cross",
        "params": {"fast": 5, "slow": 20, "kind": "sma"},
        "unpinned": False,
        "param_delta": {},
        "trades_preview": [
            {"signal_buy_date": "2026-07-01", "signal_sell_date": None, "trade_return": None, "hold_days": 0}
        ],
        "trades": [],
        "close": 12.3,
        "rule_sentence": "均线交叉：金叉入场、死叉离场",
        "exec_note": "note",
        "series": [{"date": "2026-07-01", "ma_fast": 12.1, "ma_slow": 11.9}],
    }
    sig = ipack.to_signal(pack)
    assert sig["action"] == "BUY"
    assert sig["indicatorId"] == "ma1"

    ov = ipack.to_overlay(pack, history_dates={"2026-07-01"})
    assert ov["status"] == "ok"
    assert len(ov["markers"]) == 1
    assert ov["series"] == pack["series"]

    ov_filtered = ipack.to_overlay(pack, history_dates={"2099-01-01"})
    assert ov_filtered["markers"] == []
    assert ov_filtered["series"] == []

    md = ipack.format_section(pack)
    assert "BUY" in md
    assert "均线交叉" in md


def test_to_signal_and_overlay_on_non_ok_pack() -> None:
    pack = {"indicator_id": "ma1", "status": "skipped", "reason": "样本过短", "unpinned": True}
    ov = ipack.to_overlay(pack)
    assert ov["status"] == "skipped"
    assert ov["markers"] == []
    md = ipack.format_section(pack)
    assert "skipped" in md
    assert "未钉死" in md


def test_build_pack_from_wf_marks_stale() -> None:
    from kss.backtest.indicator_walk_forward import WFResult

    entry = _ma_entry()
    wf = WFResult(status="ok", best_params={"fast": 5, "slow": 20, "kind": "sma"}, replay={"action": {}, "trades": []})
    pack = ipack.build_pack_from_wf(
        "688017.SH", "2026-07-01", entry, wf, unpinned=False, reference_trade_date="2026-07-10"
    )
    assert pack["status"] == "stale"


def test_run_entry_pack_primitive_end_to_end(tmp_path: Path) -> None:
    entry = _ma_entry()
    _write_fixture_csv(tmp_path / "cs_data_688017.csv")
    cfg = WFConfig(train_window=120, retrain_freq=40, holdout_bars=40, min_trades=1)
    p1 = ipack.run_entry_pack(entry, "688017.SH", cfg=cfg, root=tmp_path)
    assert p1["symbol"] == "688017.SH"
    assert p1["status"] in ("ok", "skipped", "error")
    if p1["status"] == "ok":
        assert p1["indicator_id"] == "ma1"
        assert (tmp_path / "storage/indicator_signals/ma1/latest/688017.SH.json").exists()
        p2 = ipack.run_entry_pack(entry, "688017.SH", cfg=cfg, root=tmp_path)
        assert p1["action"] == p2["action"]


def test_run_entry_pack_short_sample_skips(tmp_path: Path) -> None:
    entry = _ma_entry()
    _write_fixture_csv(tmp_path / "cs_data_688999.csv", n=30)
    pack = ipack.run_entry_pack(entry, "688999.SH", root=tmp_path)
    assert pack["status"] == "skipped"
    assert pack["unpinned"] is True


def test_run_entry_pack_mi_legacy_matches_direct_call(tmp_path: Path) -> None:
    """kind=mi_legacy 委托回归：run_entry_pack 产出与直接调用 mi_pack.run_symbol_pack 一致.

    两次跑用各自独立的 root——同一 root 连跑两次会让第二次读到第一次写下的
    prev_action，掩盖真正要比对的"同一起点、两条路径算出同一结果"。
    """
    from kss.strategies import mi_pack as _mi

    code = "688017"
    rules = {
        "defaults": {"entry": "mi_cross_up_0", "exit": "a_cross_dn_mi", "filter": "none"},
        "symbols": {code: {"entry": "mi_cross_up_0", "exit": "a_cross_dn_mi", "filter": "none"}},
    }

    root_a = tmp_path / "via_registry"
    root_b = tmp_path / "direct"
    for root in (root_a, root_b):
        _write_fixture_csv(root / f"cs_data_{code}.csv")
        (root / "storage").mkdir(parents=True, exist_ok=True)
        (root / "storage" / "mi_rules.yaml").write_text(
            yaml.safe_dump(rules, allow_unicode=True), encoding="utf-8"
        )

    mi_entry = RegistryEntry(
        id="mi",
        name="MI 动量",
        kind=KIND_MI_LEGACY,
        rules_path="storage/mi_rules.yaml",
        signals_dir="storage/mi_signals",
    )

    via_registry = ipack.run_entry_pack(mi_entry, "688017.SH", root=root_a)
    direct = _mi.run_symbol_pack("688017.SH", rules=rules, root=root_b)

    assert via_registry["status"] == direct["status"] == "ok"
    for key in ("action", "n", "entry", "exit", "filter", "trades"):
        assert via_registry.get(key) == direct.get(key)

    # 统一入口投影与 MI 专属投影逐字段一致
    sig_registry = ipack.to_any_signal(mi_entry, via_registry)
    sig_direct = _mi.to_mi_signal(direct)
    assert sig_registry == sig_direct

    md_registry = ipack.format_any_section(mi_entry, via_registry)
    md_direct = _mi.format_mi_section(direct)
    assert md_registry == md_direct

    loaded = ipack.read_any_pack(mi_entry, "688017.SH", root=root_a)
    assert loaded is not None
    assert loaded["action"] == direct["action"]
