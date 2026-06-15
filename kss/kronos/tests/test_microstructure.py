"""U6 微结构校验 + 合成生成测试（R12/R15, AE4）."""

from __future__ import annotations

import numpy as np
import pandas as pd

from kss.kronos.microstructure_check import (
    check_microstructure,
    limit_pct_for,
    validate_batch,
)
from kss.kronos.synth_gen import ADVERSARIAL_SCENARIOS, generate_batch


def _frame_from_pct(pct_chg, gaps=None, start=10.0, symbol_open_eq_pre=False):
    """按逐日收益构造合成 K 线（带 pre_close）；gaps 控制 open 相对前收的跳空."""
    pct_chg = np.asarray(pct_chg, dtype=float)
    n = len(pct_chg)
    pre = np.empty(n)
    close = np.empty(n)
    prev = start
    for i in range(n):
        pre[i] = prev
        close[i] = prev * (1 + pct_chg[i])
        prev = close[i]
    if gaps is None:
        open_ = pre.copy() if symbol_open_eq_pre else pre * 1.005  # 默认带 0.5% 跳空
    else:
        open_ = pre * (1 + np.asarray(gaps, dtype=float))
    return pd.DataFrame({
        "open": open_, "high": np.maximum(open_, close) * 1.001,
        "low": np.minimum(open_, close) * 0.999, "close": close,
        "pre_close": pre, "volume": np.ones(n), "amount": np.ones(n),
    })


def test_limit_pct_for_board() -> None:
    assert limit_pct_for("688008.SH") == 0.20
    assert limit_pct_for("300750.SZ") == 0.20
    assert limit_pct_for("600519.SH") == 0.10
    assert limit_pct_for("000001.SZ") == 0.10


class TestCheckMicrostructure:
    def test_happy_full_microstructure_valid(self) -> None:
        """happy: 含触板(20%) + 跳空 + 不越界 → valid."""
        # 科创板 ±20%：放一根 +20% 触板，其余温和；带跳空。
        df = _frame_from_pct([0.20, 0.01, -0.02, 0.005, 0.20])
        rep = check_microstructure(df, "688008.SH")
        assert rep.valid is True
        assert rep.has_limit_hit and rep.has_gap and not rep.over_limit

    def test_ae4_no_limit_hit_invalid(self) -> None:
        """Covers AE4: 缺涨跌停截断（无触板 + 越界）→ 无效，不进诊断."""
        # 主板 ±10%，但放一根 +15%（越界，截断未生效），无触板。
        df = _frame_from_pct([0.15, 0.01, -0.02, 0.005])
        rep = check_microstructure(df, "600519.SH")
        assert rep.valid is False
        assert rep.over_limit is True
        assert any("越过涨跌停" in r for r in rep.reasons)

    def test_ae4_no_gap_invalid(self) -> None:
        """Covers AE4: 缺 T+1 跳空（open 恒等于前收）→ 无效."""
        df = _frame_from_pct([0.10, 0.01, -0.05], symbol_open_eq_pre=True)
        rep = check_microstructure(df, "600519.SH")
        assert rep.valid is False
        assert rep.has_gap is False
        assert any("跳空" in r for r in rep.reasons)

    def test_edge_limit_rate_deviation_warns(self) -> None:
        """edge: 触板率显著偏离真实分布 → 告警（不改 valid）."""
        # 5 根里 4 根触板 → 触板率 80%，真实给 5% → 偏离大。
        df = _frame_from_pct([0.10, 0.10, 0.10, 0.10, 0.01])
        rep = check_microstructure(df, "600519.SH", real_limit_rate=0.05)
        assert rep.warnings
        assert any("偏离真实" in w for w in rep.warnings)


class TestValidateBatch:
    def test_batch_invalid_when_majority_fail(self) -> None:
        """多数无效 → 整批判无效（不进 U7）."""
        good = _frame_from_pct([0.20, 0.01, 0.20])
        bad = _frame_from_pct([0.30, 0.01])  # 越界
        frames = [("688008.SH#a", good), ("688008.SH#b", bad), ("688008.SH#c", bad)]
        bv = validate_batch(frames, min_valid_frac=0.5)
        assert bv.valid is False
        assert bv.n_valid == 1 and bv.n_total == 3

    def test_batch_valid_when_majority_pass(self) -> None:
        good = _frame_from_pct([0.20, 0.01, 0.20])
        frames = [("688008.SH#a", good), ("688008.SH#b", good)]
        bv = validate_batch(frames, min_valid_frac=0.5)
        assert bv.valid is True


class _StubPredictor:
    def predict(self, *, df, x_timestamp, y_timestamp, pred_len, T, top_p, **kw):
        last = float(df["close"].iloc[-1])
        # 制造一根触板 + 跳空的科创板路径
        close = last * np.array([1.20, 1.21, 1.19, 1.20, 1.22])[:pred_len]
        return pd.DataFrame({
            "open": close * 1.005, "high": close * 1.01, "low": close * 0.99,
            "close": close, "volume": np.ones(pred_len), "amount": np.ones(pred_len),
        }, index=pd.Index(y_timestamp))


def test_generate_batch_produces_frames_with_microstructure_fields() -> None:
    """合成帧带 pre_close/pct_chg/trade_date，可直接进微结构校验."""
    dates = pd.bdate_range("2025-01-02", periods=120)
    rng = np.random.default_rng(0)
    close = 10 + np.cumsum(rng.normal(0, 0.1, 120))
    seed = pd.DataFrame({
        "ts_code": "688008.SH", "trade_date": dates,
        "open": close, "high": close + 0.1, "low": close - 0.1, "close": close,
        "vol": rng.uniform(1e5, 5e5, 120), "amount": rng.uniform(1e5, 5e5, 120),
    })
    out = generate_batch(
        _StubPredictor(), {"688008.SH": seed},
        scenario=ADVERSARIAL_SCENARIOS[0], n_paths=2, lookback_bars=60, pred_len=5,
    )
    assert len(out) == 2
    sym, frame = out[0]
    assert {"pre_close", "pct_chg", "trade_date"} <= set(frame.columns)
    rep = check_microstructure(frame, "688008.SH")
    assert rep.has_limit_hit  # stub 造了 20% 触板
