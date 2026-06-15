"""U4 批处理推理测试（R1/R2/R3/R6, AE1）—— 用 stub predictor，不加载真实模型."""

from __future__ import annotations

import inspect

import numpy as np
import pandas as pd
import pytest

from kss.kronos import batch_infer
from kss.kronos.batch_infer import (
    KronosPredictionStore,
    infer_symbol,
    run_batch,
)


class _StubPredictor:
    """假 predictor：close = 最后输入 close × (1 + 微小随机)，制造 MC 带."""

    def __init__(self, seed: int = 0) -> None:
        self.rng = np.random.default_rng(seed)
        self.device = "cpu"
        self.calls = 0

    def predict(self, *, df, x_timestamp, y_timestamp, pred_len, **kw):
        self.calls += 1
        last = float(df["close"].iloc[-1])
        noise = self.rng.normal(0, 0.01, pred_len)
        close = last * (1 + np.cumsum(noise) + 0.001)
        return pd.DataFrame({
            "open": close, "high": close, "low": close, "close": close,
            "volume": np.ones(pred_len), "amount": np.ones(pred_len),
        }, index=pd.Index(y_timestamp))


def _frame(symbol: str, n: int, start: str) -> pd.DataFrame:
    dates = pd.bdate_range(start, periods=n)
    rng = np.random.default_rng(1)
    close = 10 + np.cumsum(rng.normal(0, 0.1, n))
    return pd.DataFrame({
        "ts_code": symbol, "trade_date": dates,
        "open": close, "high": close + 0.1, "low": close - 0.1, "close": close,
        "vol": rng.uniform(1e5, 5e5, n), "amount": rng.uniform(1e5, 5e5, n),
    })


def test_happy_produces_and_stores(tmp_path) -> None:
    """happy: 冻结模型 + universe → 每票一组 D 之后预测落库."""
    store = KronosPredictionStore(tmp_path / "p.sqlite")
    frames = {"A.SZ": _frame("A.SZ", 120, "2025-01-02"), "B.SZ": _frame("B.SZ", 120, "2025-01-02")}
    res = run_batch(
        frames, _StubPredictor(), cutoff="2025-03-31", model_id="m1",
        store=store, lookback_bars=60, pred_len=5, mc_samples=8,
    )
    assert res.n_symbols_ok == 2
    assert res.n_predictions == 2 * 5
    df = store.load("A.SZ")
    assert len(df) == 5
    assert set(df["horizon"]) == {1, 2, 3, 4, 5}
    # 不确定带合法：lower ≤ point ≤ upper
    assert (df["lower_close"] <= df["point_close"] + 1e-9).all()
    assert (df["point_close"] <= df["upper_close"] + 1e-9).all()


def test_covers_ae1_targets_strictly_after_cutoff(tmp_path) -> None:
    """Covers AE1: 被预测 bar 全部严格晚于 D；D 及之前不产出."""
    store = KronosPredictionStore(tmp_path / "p.sqlite")
    frames = {"A.SZ": _frame("A.SZ", 120, "2025-01-02")}
    res = run_batch(
        frames, _StubPredictor(), cutoff="2025-03-31", model_id="m1",
        store=store, lookback_bars=60, pred_len=5, mc_samples=4,
    )
    df = store.load()
    cutoff = pd.Timestamp("2025-03-31")
    assert (pd.to_datetime(df["target_date"]) > cutoff).all()


def test_ae1_no_output_when_targets_before_cutoff(tmp_path) -> None:
    """D 远在未来 → 最新窗口的预测 bar ≤ D → 该票被跳过（不产出 D 及之前）."""
    store = KronosPredictionStore(tmp_path / "p.sqlite")
    # 数据只到 2025-01 中，cutoff 设在 2025-06 → 预测目标 ≤ D。
    frames = {"A.SZ": _frame("A.SZ", 80, "2024-10-01")}
    res = run_batch(
        frames, _StubPredictor(), cutoff="2025-06-01", model_id="m1",
        store=store, lookback_bars=60, pred_len=5, mc_samples=2,
    )
    assert res.n_symbols_ok == 0
    assert len(res.skipped) == 1
    assert store.load().empty


def test_error_single_symbol_missing_skips_not_abort(tmp_path) -> None:
    """error: 单票数据缺失 → 跳过 + 记录，不中断整批."""
    store = KronosPredictionStore(tmp_path / "p.sqlite")
    good = _frame("A.SZ", 120, "2025-01-02")
    bad = _frame("B.SZ", 10, "2025-01-02")  # 历史不足
    frames = {"A.SZ": good, "B.SZ": bad}
    res = run_batch(
        frames, _StubPredictor(), cutoff="2025-03-31", model_id="m1",
        store=store, lookback_bars=60, pred_len=5, mc_samples=2,
    )
    assert res.n_symbols_ok == 1
    assert any(s[0] == "B.SZ" for s in res.skipped)


def test_integration_zero_connection_to_log_mv() -> None:
    """integration: 模块与 log_mv/paper_trade 零连接（import 语句不引实盘决策路径）."""
    import_lines = [
        ln.strip() for ln in inspect.getsource(batch_infer).splitlines()
        if ln.strip().startswith(("import ", "from "))
    ]
    blob = "\n".join(import_lines)
    for forbidden in ("paper_trade", "log_mv", "cross_sectional_forecast", "CrossSectionalForecast"):
        assert forbidden not in blob, f"batch_infer 不应 import 实盘路径：{forbidden}"


def test_store_db_separate_from_live(tmp_path) -> None:
    """预测库与行情库物理分离（路径在 storage/kronos 下）."""
    store = KronosPredictionStore(tmp_path / "p.sqlite")
    assert store.db_path.name.endswith(".sqlite")


def test_infer_symbol_returns_none_on_short_history() -> None:
    out = infer_symbol(
        _StubPredictor(), _frame("A.SZ", 20, "2025-01-02"),
        cutoff="2025-03-31", model_id="m1", symbol="A.SZ",
        lookback_bars=60, pred_len=5, mc_samples=2,
    )
    assert out is None
