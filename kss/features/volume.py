"""成交量与成交额因子生成器."""

from __future__ import annotations

from typing import Sequence

import pandas as pd


class VolumeFactors:
    """成交量、成交额及价量关系因子."""

    @staticmethod
    def volume_ratio(
        volume: pd.Series, periods: Sequence[int] = (5, 20)
    ) -> dict[str, pd.Series]:
        """计算成交量均线比率.

        Args:
            volume: 成交量序列.
            periods: 均线周期列表，默认 [5, 20] 日.

        Returns:
            字典，键为 ``vol_ma{p}_ratio``，表示当前成交量 / 对应周期均量.
        """
        return {f"vol_ma{p}_ratio": volume / volume.rolling(p).mean() for p in periods}

    @staticmethod
    def amount_ratio(
        amount: pd.Series, periods: Sequence[int] = (5,)
    ) -> dict[str, pd.Series]:
        """计算成交额均线比率.

        Args:
            amount: 成交额序列.
            periods: 均线周期列表，默认 [5] 日.

        Returns:
            字典，键为 ``amount_ma{p}_ratio``.
        """
        return {f"amount_ma{p}_ratio": amount / amount.rolling(p).mean() for p in periods}

    @staticmethod
    def price_volume_corr(
        close: pd.Series, volume: pd.Series, period: int = 20
    ) -> dict[str, pd.Series]:
        """计算价量相关系数（滚动窗口）.

        Args:
            close: 收盘价序列.
            volume: 成交量序列.
            period: 滚动窗口长度，默认 20 日.

        Returns:
            字典，键为 ``price_vol_corr_{period}d``.
        """
        return {
            f"price_vol_corr_{period}d": close.pct_change().rolling(period).corr(
                volume.pct_change()
            )
        }
