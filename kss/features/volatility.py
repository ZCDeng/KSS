"""波动率因子生成器."""

from __future__ import annotations

from typing import Sequence

import pandas as pd


class VolatilityFactors:
    """波动率相关因子，衡量价格变动的不确定性与风险."""

    @staticmethod
    def volatility(
        close: pd.Series, periods: Sequence[int] = (5, 20, 60)
    ) -> dict[str, pd.Series]:
        """计算多期历史波动率（日收益率滚动标准差）.

        Args:
            close: 收盘价序列.
            periods: 波动率计算周期列表，默认 [5, 20, 60] 日.

        Returns:
            字典，键为 ``volatility_{p}d``.
        """
        daily_ret = close.pct_change()
        return {f"volatility_{p}d": daily_ret.rolling(p).std() for p in periods}

    @staticmethod
    def atr(
        high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14
    ) -> dict[str, pd.Series]:
        """计算 Average True Range (ATR) 因子，以价格归一化.

        使用简化公式：ATR = (high - low).rolling(period).mean() / close

        Args:
            high: 最高价序列.
            low: 最低价序列.
            close: 收盘价序列.
            period: ATR 计算周期，默认 14 日.

        Returns:
            字典，键为 ``atr_{period}``.
        """
        return {f"atr_{period}": (high - low).rolling(period).mean() / close}
