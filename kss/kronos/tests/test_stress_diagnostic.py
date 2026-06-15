"""U7 合成压测诊断测试（R12/R13/R14, AE3）."""

from __future__ import annotations

import inspect

import numpy as np
import pandas as pd

from kss.kronos import stress_diagnostic
from kss.kronos.stress_diagnostic import (
    StressDiagnosticReport,
    diagnose,
    synth_frames_to_panel,
)


def _zero_structure_trial(n_stocks: int, n_days: int, seed: int) -> list[tuple[str, pd.DataFrame]]:
    """一批零结构随机游走合成路径（无真实 alpha）."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2026-03-01", periods=n_days)
    frames = []
    for s in range(n_stocks):
        close = 10 * np.cumprod(1 + rng.normal(0, 0.02, n_days))
        frames.append((f"S{s:02d}.SZ", pd.DataFrame({"trade_date": dates, "close": close})))
    return frames


def test_synth_frames_to_panel_builds_factor_and_label() -> None:
    frames = _zero_structure_trial(5, 30, seed=0)
    panel = synth_frames_to_panel(frames)
    assert {"trade_date", "symbol", "factor", "next_day_return"} <= set(panel.columns)
    assert panel["factor"].notna().all()
    assert panel["next_day_return"].notna().all()


def test_covers_ae3_report_is_diagnostic_not_gate() -> None:
    """Covers AE3: 输出是假阳性率诊断报告，非 PASS/FAIL 阻断闸门."""
    trials = [_zero_structure_trial(8, 80, seed=s) for s in range(5)]
    rep = diagnose(trials, strategy_family="mined")
    assert isinstance(rep, StressDiagnosticReport)
    assert rep.is_blocking is False
    assert hasattr(rep, "false_positive_rate")
    # 报告里没有 PASS/FAIL 阻断字段
    assert not hasattr(rep, "blocked")


def test_happy_reproducible_fpr() -> None:
    """happy: 通过校验的合成 trial → 可复现的假阳性率数字."""
    trials = [_zero_structure_trial(8, 80, seed=s) for s in range(6)]
    r1 = diagnose(trials, strategy_family="mined")
    r2 = diagnose(trials, strategy_family="mined")
    assert r1.false_positive_rate is not None
    assert 0.0 <= r1.false_positive_rate <= 1.0
    assert r1.n_trials == r2.n_trials
    assert r1.false_positive_rate == r2.false_positive_rate  # 确定性可复现


def test_mined_fpr_not_above_prior() -> None:
    """更严的 mined 族假阳性率不高于宽松 prior 族（闸门越严越不易被骗）."""
    trials = [_zero_structure_trial(8, 90, seed=s) for s in range(8)]
    mined = diagnose(trials, strategy_family="mined")
    prior = diagnose(trials, strategy_family="prior")
    assert mined.false_positive_rate is not None and prior.false_positive_rate is not None
    assert mined.false_positive_rate <= prior.false_positive_rate + 1e-9


def test_edge_all_invalid_no_misleading_number() -> None:
    """edge: 全部合成批无效 → 标注'无有效样本'而非给误导数字."""
    # 每个 trial 只有 1 bar → 无法构造 factor/label → 无有效回测。
    trials = [_zero_structure_trial(5, 1, seed=s) for s in range(3)]
    rep = diagnose(trials, strategy_family="mined")
    assert rep.n_trials == 0
    assert rep.false_positive_rate is None
    assert rep.note == "无有效样本"


def test_integration_r13_no_train_or_live_write_imports() -> None:
    """Covers R13: 合成数据不写训练/实盘信号路径（模块不 import 这些路径）."""
    import_lines = [
        ln.strip() for ln in inspect.getsource(stress_diagnostic).splitlines()
        if ln.strip().startswith(("import ", "from "))
    ]
    blob = "\n".join(import_lines)
    for forbidden in ("finetune", "batch_infer", "KronosPredictionStore", "paper_trade", "train"):
        assert forbidden not in blob, f"压测诊断不应 import 训练/实盘/落库路径：{forbidden}"
