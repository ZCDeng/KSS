"""daily_review 指标信号段泛化单测（U7）——注册表遍历替代原 MI-only 硬编码块."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, PROJECT_ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


dr = _load("daily_review_u7", "scripts/daily_review.py")


@pytest.fixture
def isolated_state(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KSS_STATE_ROOT", str(tmp_path))
    monkeypatch.setenv("KSS_PROJECT_ROOT", str(PROJECT_ROOT))
    return tmp_path


def test_missing_pack_produces_missing_section(isolated_state: Path) -> None:
    lines = dr._indicator_registry_sections("688017.SH", "688017")
    joined = "\n".join(lines)
    assert "MI 动量" in joined
    assert "missing" in joined or "暂无" in joined


def test_bare_symbol_fallback_used_when_pack_key_empty(isolated_state: Path) -> None:
    lines = dr._indicator_registry_sections("", "688017")
    assert lines  # 仍能走 bare_sym 兜底产出 MI 的缺省段


def test_registry_unavailable_degrades_to_empty(isolated_state: Path, monkeypatch) -> None:
    import builtins

    real_import = builtins.__import__

    def _boom(name, *args, **kwargs):
        if name == "kss.indicators.registry":
            raise ImportError("simulated unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _boom)
    lines = dr._indicator_registry_sections("688017.SH", "688017")
    assert lines == []


def test_solidified_primitive_indicator_section_appears(isolated_state: Path) -> None:
    from kss.backtest.indicator_walk_forward import WFConfig
    from kss.indicators import pack as ipack
    from kss.indicators.registry import KIND_PRIMITIVE, RegistryEntry, upsert_entry

    n = 400
    rng = np.random.default_rng(3)
    close = 80 + np.cumsum(rng.normal(0.2, 0.9, n))
    df = pd.DataFrame(
        {
            "trade_date": pd.bdate_range("2023-01-02", periods=n),
            "open": close + rng.normal(0, 0.15, n),
            "high": close + 1.2,
            "low": close - 1.2,
            "close": close,
        }
    )
    (isolated_state / "cs_data_688017.csv").parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(isolated_state / "cs_data_688017.csv", index=False)

    entry = RegistryEntry(
        id="ma1",
        name="均线交叉示例",
        kind=KIND_PRIMITIVE,
        family="ma_cross",
        params={"fast": 5, "slow": 20, "kind": "sma"},
        rules_path="storage/indicator_rules/ma1.yaml",
        signals_dir="storage/indicator_signals/ma1",
    )
    upsert_entry(entry)
    cfg = WFConfig(train_window=120, retrain_freq=40, holdout_bars=40, min_trades=1)
    ipack.run_entry_pack(entry, "688017.SH", cfg=cfg)

    lines = dr._indicator_registry_sections("688017.SH", "688017")
    joined = "\n".join(lines)
    assert "均线交叉示例 信号" in joined
    assert "MI 动量" in joined  # MI 仍在（默认注册表条目），两段并存


def test_single_entry_failure_does_not_block_others(isolated_state: Path, monkeypatch) -> None:
    """单个指标段格式化失败不拖垮其它指标段（fail-loud 记日志，不整体崩）。"""
    from kss.indicators import pack as ipack

    real_format = ipack.format_any_section

    def _boom(entry, pack):
        if entry.kind != "mi_legacy":
            raise RuntimeError("模拟格式化失败")
        return real_format(entry, pack)

    monkeypatch.setattr(ipack, "format_any_section", _boom)

    from kss.indicators.registry import KIND_PRIMITIVE, RegistryEntry, upsert_entry

    upsert_entry(
        RegistryEntry(
            id="broken1", name="故障指标", kind=KIND_PRIMITIVE, family="rsi_threshold", params={}
        )
    )

    lines = dr._indicator_registry_sections("688017.SH", "688017")
    joined = "\n".join(lines)
    assert "MI 动量" in joined
    assert "故障指标" not in joined
