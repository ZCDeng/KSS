"""技术因子生成器."""

from __future__ import annotations

from typing import Sequence

import numpy as np
import pandas as pd


class TechnicalFactors:
    """技术指标因子集合，基于 OHLCV 数据计算经典技术分析指标."""

    @staticmethod
    def momentum(close: pd.Series, periods: Sequence[int] = (1, 5, 10, 20, 60)) -> dict[str, pd.Series]:
        """计算多期动量因子.

        Args:
            close: 收盘价序列.
            periods: 动量计算周期列表，默认 [1, 5, 10, 20, 60] 日.

        Returns:
            字典，键为 ``mom_{p}d``，值为对应周期的收益率序列.
        """
        # fill_method=None：pandas 2.x 默认 'pad' 会把 NaN 前向填充，导致动量
        # 因子在缺失日错误地计算为相对昨日值的变化率（实际本就停牌）.
        return {f"mom_{p}d": close.pct_change(p, fill_method=None) for p in periods}

    @staticmethod
    def moving_averages(
        close: pd.Series, periods: Sequence[int] = (5, 10, 20, 60)
    ) -> dict[str, pd.Series]:
        """计算均线比率与斜率因子.

        Args:
            close: 收盘价序列.
            periods: 均线周期列表，默认 [5, 10, 20, 60] 日.

        Returns:
            字典，包含 ``ma_{p}_ratio``（价格/均线）和 ``ma_{p}_slope``（均线 5 日变化率）.
        """
        result: dict[str, pd.Series] = {}
        for p in periods:
            ma = close.rolling(p).mean()
            result[f"ma_{p}_ratio"] = close / ma
            result[f"ma_{p}_slope"] = ma.pct_change(5, fill_method=None)
        return result

    @staticmethod
    def macd(close: pd.Series) -> dict[str, pd.Series]:
        """计算 MACD 指标因子.

        Args:
            close: 收盘价序列.

        Returns:
            字典，包含 ``macd``、``macd_signal``、``macd_hist``.
        """
        ema12 = close.ewm(span=12).mean()
        ema26 = close.ewm(span=26).mean()
        macd_line = ema12 - ema26
        signal_line = macd_line.ewm(span=9).mean()
        return {
            "macd": macd_line,
            "macd_signal": signal_line,
            "macd_hist": macd_line - signal_line,
        }

    @staticmethod
    def rsi(close: pd.Series, period: int = 14) -> dict[str, pd.Series]:
        """计算 RSI 相对强弱指标.

        Args:
            close: 收盘价序列.
            period: RSI 计算周期，默认 14 日.

        Returns:
            字典，键为 ``rsi_{period}``.
        """
        delta = close.diff()
        gain = delta.where(delta > 0, 0.0)
        loss = -delta.where(delta < 0, 0.0)
        avg_gain = gain.rolling(period).mean()
        avg_loss = loss.rolling(period).mean()
        rs = avg_gain / (avg_loss + 1e-8)
        return {f"rsi_{period}": 100 - (100 / (1 + rs))}

    @staticmethod
    def kdj(
        high: pd.Series, low: pd.Series, close: pd.Series
    ) -> dict[str, pd.Series]:
        """计算 KDJ 随机指标.

        Args:
            high: 最高价序列.
            low: 最低价序列.
            close: 收盘价序列.

        Returns:
            字典，包含 ``kdj_k``、``kdj_d``、``kdj_j``.
        """
        lowest_9 = low.rolling(9).min()
        highest_9 = high.rolling(9).max()
        rsv = (close - lowest_9) / (highest_9 - lowest_9 + 1e-8) * 100
        k = rsv.ewm(com=2).mean()
        d = k.ewm(com=2).mean()
        return {
            "kdj_k": k,
            "kdj_d": d,
            "kdj_j": 3 * k - 2 * d,
        }

    @staticmethod
    def bollinger(close: pd.Series, period: int = 20) -> dict[str, pd.Series]:
        """计算布林带因子（价格归一化版本）.

        Args:
            close: 收盘价序列.
            period: 布林带周期，默认 20 日.

        Returns:
            字典，包含 ``boll_upper``、``boll_lower``、``boll_width``、``boll_position``.
                其中 upper/lower/width 以当前价格归一化，position 为价格在带内的位置.
        """
        ma = close.rolling(period).mean()
        std = close.rolling(period).std()
        upper = ma + 2 * std
        lower = ma - 2 * std
        width = upper - lower
        position = (close - lower) / (width + 1e-8)
        return {
            "boll_upper": upper / close,
            "boll_lower": lower / close,
            "boll_width": width / close,
            "boll_position": position,
        }

    @staticmethod
    def atr(
        high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14
    ) -> pd.Series:
        """计算 ATR 真实波幅（Wilder 平滑）.

        Args:
            high: 最高价序列.
            low: 最低价序列.
            close: 收盘价序列.
            period: 平滑周期，默认 14 日.

        Returns:
            ATR 序列（绝对价格单位）.
        """
        prev_close = close.shift(1)
        tr = pd.concat(
            [(high - low).abs(), (high - prev_close).abs(), (low - prev_close).abs()],
            axis=1,
        ).max(axis=1)
        return tr.ewm(alpha=1.0 / period, adjust=False).mean()

    @staticmethod
    def candlestick(
        open_: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series
    ) -> dict[str, pd.Series]:
        """计算 K 线形态因子.

        Args:
            open_: 开盘价序列.
            high: 最高价序列.
            low: 最低价序列.
            close: 收盘价序列.

        Returns:
            字典，包含 ``amplitude``、``body_ratio``、``upper_shadow``、``lower_shadow``.
        """
        body_top = pd.Series(np.maximum(open_, close), index=close.index)
        body_bottom = pd.Series(np.minimum(open_, close), index=close.index)
        return {
            "amplitude": (high - low) / close,
            "body_ratio": (close - open_) / close,
            "upper_shadow": (high - body_top) / close,
            "lower_shadow": (body_bottom - low) / close,
        }

    @staticmethod
    def price_position(
        close: pd.Series,
        high: pd.Series,
        low: pd.Series,
        periods: Sequence[int] = (20, 60),
    ) -> dict[str, pd.Series]:
        """计算价格在近期高低点区间中的位置.

        Args:
            close: 收盘价序列.
            high: 最高价序列.
            low: 最低价序列.
            periods: 回望周期列表，默认 [20, 60] 日.

        Returns:
            字典，包含 ``close_to_high_{p}d`` 和 ``close_to_low_{p}d``.
        """
        result: dict[str, pd.Series] = {}
        for p in periods:
            result[f"close_to_high_{p}d"] = close / high.rolling(p).max()
            result[f"close_to_low_{p}d"] = close / low.rolling(p).min()
        return result

    @staticmethod
    def mi(
        close: pd.Series, periods: Sequence[int] = (12,)
    ) -> dict[str, pd.Series]:
        """计算 MI 动量指标（通达信 / 天勤 tqsdk 口径）.

        公式::

            A_t  = C_t - C_{t-N}
            MI_t = (A_t + (N-1) * MI_{t-1}) / N

        第二步等价于 ``SMA(A, N, 1)`` / ``ewm(alpha=1/N, adjust=False)``.

        Args:
            close: 收盘价序列.
            periods: 回看与平滑周期列表，默认 ``(12,)``（tqsdk 默认）.

        Returns:
            字典，每个周期包含：
            - ``mi_a_{n}``: 原始动量差 A
            - ``mi_{n}``: 平滑后的 MI
        """
        result: dict[str, pd.Series] = {}
        for n in periods:
            if n <= 0:
                raise ValueError(f"MI 周期必须为正整数，收到 {n}")
            a = close.diff(n)
            # alpha=1/n → MI_t = α·A_t + (1-α)·MI_{t-1}
            mi = a.ewm(alpha=1.0 / n, adjust=False).mean()
            result[f"mi_a_{n}"] = a
            result[f"mi_{n}"] = mi
        return result
