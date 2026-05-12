"""估值因子生成器 —— 从 daily_basic 数据中提取估值指标."""

from __future__ import annotations

import numpy as np
import pandas as pd


class ValuationFactors:
    """估值类因子，包括市盈率、市净率、市值等."""

    @staticmethod
    def from_daily_basic(df: pd.DataFrame) -> dict[str, pd.Series]:
        """从 ``daily_basic`` 表中提取估值因子.

        提取的字段包括：
        - ``pe`` : 市盈率（若缺失率 > 50% 则跳过）
        - ``pb`` : 市净率
        - ``log_mv`` : 对数总市值

        同时保留 ``vr``（量比）和 ``turnover``（换手率）作为辅助字段.

        Args:
            df: 原始 DataFrame，需包含 ``daily_basic`` 相关列.

        Returns:
            字典，键为因子名，值为对应序列.
        """
        result: dict[str, pd.Series] = {}

        if "pe" in df.columns:
            missing_rate = df["pe"].isna().mean()
            if missing_rate <= 0.5:
                result["pe"] = df["pe"]
        if "pb" in df.columns:
            result["pb"] = df["pb"]

        if "total_mv" in df.columns:
            result["log_mv"] = np.log(df["total_mv"] + 1)

        # 辅助字段：如果原始数据中有则直接透传
        if "volume_ratio" in df.columns:
            result["vr"] = df["volume_ratio"]
        if "turnover_rate" in df.columns:
            result["turnover"] = df["turnover_rate"]

        return result
