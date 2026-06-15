"""U2 适配器测试：输入契约 + 可交易性过滤（R1, R17）."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from kss.data.suspension_data import SuspensionData
from kss.kronos.adapter import (
    KRONOS_INPUT_COLS,
    build_inference_input,
    filter_tradable,
    next_trading_days,
    normalize_ohlcv,
)


def _make_cs_data(symbol: str = "300002.SZ", n: int = 120, start: str = "2024-01-02") -> pd.DataFrame:
    """合成 cs_data 单票 DataFrame（含 vol/amount > 0）."""
    dates = pd.bdate_range(start=start, periods=n)
    rng = np.random.default_rng(7)
    close = 10 + np.cumsum(rng.normal(0, 0.1, n))
    return pd.DataFrame({
        "ts_code": symbol,
        "trade_date": dates.strftime("%Y-%m-%d"),
        "open": close + rng.normal(0, 0.05, n),
        "high": close + np.abs(rng.normal(0, 0.1, n)),
        "low": close - np.abs(rng.normal(0, 0.1, n)),
        "close": close,
        "vol": rng.uniform(1e5, 5e5, n),
        "amount": rng.uniform(1e5, 5e5, n),
    })


class TestNormalizeOhlcv:
    def test_happy_columns_dtype_monotonic(self) -> None:
        """happy: 列名/dtype 正确、时间戳单调递增（输入故意乱序）."""
        df = _make_cs_data(n=30).sample(frac=1, random_state=1)  # 打乱顺序
        out = normalize_ohlcv(df)
        assert list(out.columns) == ["trade_date", *KRONOS_INPUT_COLS]
        assert pd.api.types.is_datetime64_any_dtype(out["trade_date"])
        assert out["trade_date"].is_monotonic_increasing
        # vol → volume 改名生效
        assert "volume" in out.columns and "vol" not in out.columns

    def test_missing_required_col_raises(self) -> None:
        """契约违例：缺价格列 → ValueError fail loud."""
        df = _make_cs_data(n=10).drop(columns=["close"])
        with pytest.raises(ValueError, match="缺必需列"):
            normalize_ohlcv(df)


class TestBuildInferenceInput:
    def test_happy_window_and_timestamps(self) -> None:
        """happy: 窗口长度 = lookback，x/y 时间戳为 Series 且单调递增."""
        df = _make_cs_data(n=120)
        out = build_inference_input(df, lookback_bars=60, pred_len=5)
        assert out is not None
        assert len(out.df) == 60
        assert list(out.df.columns) == KRONOS_INPUT_COLS
        assert isinstance(out.x_timestamp, pd.Series)
        assert isinstance(out.y_timestamp, pd.Series)
        assert out.x_timestamp.is_monotonic_increasing
        assert len(out.y_timestamp) == 5
        # y 必须严格晚于 x 末尾（隔离前提，U3 会进一步断言）
        assert out.y_timestamp.iloc[0] > out.x_timestamp.iloc[-1]

    def test_edge_insufficient_history_skipped(self) -> None:
        """edge: 历史不足 lookback → 返回 None 并记录原因."""
        df = _make_cs_data(n=40)
        out = build_inference_input(df, lookback_bars=60, pred_len=5, symbol="300002.SZ")
        assert out is None

    def test_explicit_y_timestamp_length_mismatch(self) -> None:
        """显式 y_timestamp 长度与 pred_len 不符 → None."""
        df = _make_cs_data(n=120)
        bad_y = pd.Series(pd.bdate_range("2025-01-01", periods=3))
        out = build_inference_input(df, lookback_bars=60, pred_len=5, y_timestamp=bad_y)
        assert out is None

    def test_nan_in_window_skipped(self) -> None:
        """窗口含 NaN → None（Kronos 归一化不接受 NaN）."""
        df = _make_cs_data(n=80)
        df.loc[df.index[-1], "close"] = np.nan
        out = build_inference_input(df, lookback_bars=60, pred_len=5)
        assert out is None


def test_next_trading_days_excludes_last_and_is_business_days() -> None:
    last = pd.Timestamp("2025-01-03")  # 周五
    out = next_trading_days(last, 3)
    assert len(out) == 3
    assert (out > last).all()
    # 全是工作日（weekday < 5）
    assert (out.dt.weekday < 5).all()


class _StubSuspension(SuspensionData):
    """跳过文件 IO 的轻量 SuspensionData（镜像 test_adversarial._Stub）."""

    def __init__(self, suspension: dict, st: dict) -> None:
        self.suspension_path = None  # type: ignore[assignment]
        self.st_path = None  # type: ignore[assignment]
        self.suspension = suspension
        self.st = st


class TestFilterTradable:
    def test_covers_r17_removes_suspended_st_zerovol(self) -> None:
        """Covers R17: 停牌 / ST / 零成交标的过滤后不出现在输出集."""
        d = pd.Timestamp("2024-03-01")
        panel = pd.DataFrame({
            "ts_code": ["A.SZ", "B.SZ", "C.SZ", "D.SZ"],
            "trade_date": [d, d, d, d],
            "vol": [1e5, 1e5, 0.0, 1e5],      # C 零成交
            "amount": [1e5, 1e5, 0.0, 1e5],
        })
        sus = _StubSuspension(
            suspension={"A.SZ": {d.normalize()}},  # A 停牌
            st={"B.SZ": {d.normalize()}},          # B 处于 ST
        )
        out = filter_tradable(panel, sus, symbol_col="ts_code")
        survivors = set(out["ts_code"])
        assert survivors == {"D.SZ"}

    def test_min_amount_threshold(self) -> None:
        """流动性门槛：amount < min_amount 被剔."""
        d = pd.Timestamp("2024-03-01")
        panel = pd.DataFrame({
            "ts_code": ["A.SZ", "B.SZ"],
            "trade_date": [d, d],
            "vol": [1e5, 1e5],
            "amount": [100.0, 1e6],
        })
        out = filter_tradable(panel, None, symbol_col="ts_code", min_amount=1000.0)
        assert set(out["ts_code"]) == {"B.SZ"}

    def test_no_suspension_only_zerovol(self) -> None:
        """suspension=None：仅做零成交过滤."""
        d = pd.Timestamp("2024-03-01")
        panel = pd.DataFrame({
            "ts_code": ["A.SZ", "B.SZ"],
            "trade_date": [d, d],
            "vol": [0.0, 1e5],
            "amount": [0.0, 1e5],
        })
        out = filter_tradable(panel, None, symbol_col="ts_code")
        assert set(out["ts_code"]) == {"B.SZ"}
