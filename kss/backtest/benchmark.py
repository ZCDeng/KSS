"""基准对比与归因 —— 与指数对齐计算 alpha / beta / 超额收益.

输入 Tushare 风格的指数 CSV（含 ``trade_date``、``close``），即可：

- 计算与策略日历对齐的指数日收益.
- 用 OLS 回归 ``r_strategy - rf = α + β × (r_bench - rf)`` 得到年化 α、β 与 p 值.
- 输出累计超额曲线与超额最大回撤.

依赖 statsmodels；缺失时回退到 numpy 手算（仅 α/β，无 t-stat）.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd

try:
    import statsmodels.api as sm
    _HAS_STATSMODELS = True
except ImportError:  # pragma: no cover
    _HAS_STATSMODELS = False

logger = logging.getLogger(__name__)

_TRADING_DAYS = 252


class Benchmark:
    """基准指数封装.

    支持 Tushare 风格的指数行情 CSV：``ts_code, trade_date, close, ...``.
    ``trade_date`` 可为 ``YYYY-MM-DD`` 或 ``YYYYMMDD``，自动解析为 ``pd.Timestamp``.
    """

    def __init__(
        self,
        index_path: str | Path,
        date_col: str = "trade_date",
        close_col: str = "close",
        rf_annual: float = 0.0,
    ) -> None:
        """加载指数 CSV.

        Args:
            index_path: 指数 CSV 路径（Tushare 风格）.
            date_col: 交易日列名.
            close_col: 收盘价列名.
            rf_annual: 无风险年利率（小数），用于扣减后计算超额收益.

        Raises:
            FileNotFoundError: 文件不存在.
            ValueError: 必需列缺失或无有效行情.
        """
        self.index_path = Path(index_path)
        if not self.index_path.exists():
            raise FileNotFoundError(f"基准指数文件不存在: {index_path}")

        df = pd.read_csv(self.index_path)
        if date_col not in df.columns or close_col not in df.columns:
            raise ValueError(
                f"指数 CSV 缺少必需列 {date_col}/{close_col}；现有列: {list(df.columns)}"
            )

        df[date_col] = pd.to_datetime(df[date_col].astype(str), errors="coerce")
        df = df.dropna(subset=[date_col, close_col]).sort_values(date_col)
        if df.empty:
            raise ValueError(f"指数 CSV 无有效行情: {index_path}")

        self._df = df.reset_index(drop=True)
        self.date_col = date_col
        self.close_col = close_col
        self.rf_annual = float(rf_annual)
        self.rf_daily = (1 + self.rf_annual) ** (1 / _TRADING_DAYS) - 1

    # ------------------------------------------------------------------ #
    # 对齐与收益
    # ------------------------------------------------------------------ #

    def aligned_returns(
        self,
        strategy_returns: pd.Series,
    ) -> pd.Series:
        """与策略日历对齐的指数日收益.

        策略日历可能与指数日历偶有差异（停牌、新股上市），未对齐的日 forward-fill
        到上一交易日的 close 收益（保守做法，等价于"指数当天没动"）.

        Args:
            strategy_returns: 索引为 ``pd.Timestamp`` 的策略日收益序列.

        Returns:
            与 ``strategy_returns.index`` 完全对齐的指数日收益.
        """
        if strategy_returns.empty:
            return pd.Series(dtype=float)
        idx = pd.to_datetime(strategy_returns.index)
        bench = self._df.set_index(self.date_col)[self.close_col]
        bench_ret = bench.pct_change()
        aligned = bench_ret.reindex(idx).ffill().fillna(0.0)
        aligned.index = strategy_returns.index
        return aligned.rename("benchmark_return")

    # ------------------------------------------------------------------ #
    # alpha / beta 回归
    # ------------------------------------------------------------------ #

    def alpha_beta(self, strategy_returns: pd.Series) -> dict[str, float]:
        """OLS: ``r_strategy - rf = α + β × (r_bench - rf) + ε``.

        Args:
            strategy_returns: 索引为 ``pd.Timestamp`` 的策略日收益.

        Returns:
            字典字段：

            - ``alpha_daily``      : 日度 α
            - ``alpha_annual``     : 年化 α（按 ``(1 + alpha_daily)^252 - 1``）
            - ``alpha_t_stat``     : α 的 t 统计量（statsmodels 可用时）
            - ``alpha_p_value``    : α 的 p 值
            - ``beta``             : 市场敏感度
            - ``r_squared``        : OLS 拟合优度
            - ``info_ratio``       : 信息比率 = E[超额] / σ[超额] × sqrt(252)
            - ``n``                : 样本数
        """
        s = strategy_returns.dropna()
        if len(s) < 5:
            return {
                "alpha_daily": float("nan"),
                "alpha_annual": float("nan"),
                "alpha_t_stat": float("nan"),
                "alpha_p_value": float("nan"),
                "beta": float("nan"),
                "r_squared": float("nan"),
                "info_ratio": float("nan"),
                "n": len(s),
            }

        b = self.aligned_returns(s)
        # 扣无风险利率
        excess_s = s.values - self.rf_daily
        excess_b = b.values - self.rf_daily

        if _HAS_STATSMODELS:
            X = sm.add_constant(excess_b)
            ols = sm.OLS(excess_s, X).fit()
            alpha_daily = float(ols.params[0])
            beta = float(ols.params[1])
            alpha_t = float(ols.tvalues[0])
            alpha_p = float(ols.pvalues[0])
            r_sq = float(ols.rsquared)
        else:  # pragma: no cover
            # numpy 手算 OLS
            x = np.column_stack([np.ones_like(excess_b), excess_b])
            coef, *_ = np.linalg.lstsq(x, excess_s, rcond=None)
            alpha_daily, beta = float(coef[0]), float(coef[1])
            yhat = x @ coef
            ss_res = float(np.sum((excess_s - yhat) ** 2))
            ss_tot = float(np.sum((excess_s - excess_s.mean()) ** 2))
            r_sq = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
            alpha_t = float("nan")
            alpha_p = float("nan")

        # 信息比率：相对基准的超额（不再扣 rf）
        active = s.values - b.values
        active_mean = float(np.mean(active))
        active_std = float(np.std(active, ddof=1))
        info_ratio = (
            active_mean / active_std * np.sqrt(_TRADING_DAYS) if active_std > 0 else 0.0
        )

        return {
            "alpha_daily": alpha_daily,
            "alpha_annual": (1 + alpha_daily) ** _TRADING_DAYS - 1,
            "alpha_t_stat": alpha_t,
            "alpha_p_value": alpha_p,
            "beta": beta,
            "r_squared": r_sq,
            "info_ratio": float(info_ratio),
            "n": len(s),
        }

    # ------------------------------------------------------------------ #
    # 超额收益曲线
    # ------------------------------------------------------------------ #

    def excess_curve(self, strategy_returns: pd.Series) -> pd.Series:
        """累计超额净值差（策略 - 基准）.

        Args:
            strategy_returns: 策略日收益序列.

        Returns:
            索引与 ``strategy_returns`` 一致的累计超额序列（首日为 0）.
        """
        if strategy_returns.empty:
            return pd.Series(dtype=float)
        b = self.aligned_returns(strategy_returns)
        active = strategy_returns - b
        # 累计简单加和（实际超额，不复合）—— 复合更"准"但首版用简单累加便于阅读
        return active.cumsum().rename("cum_excess")

    def excess_max_dd(self, strategy_returns: pd.Series) -> float:
        """超额收益的最大回撤（基于复合曲线）.

        Args:
            strategy_returns: 策略日收益序列.

        Returns:
            最大回撤（负数）；空序列返回 0.0.
        """
        if strategy_returns.empty:
            return 0.0
        b = self.aligned_returns(strategy_returns)
        active = strategy_returns - b
        cum = (1 + active).cumprod()
        running_max = cum.cummax()
        dd = (cum - running_max) / running_max
        return float(dd.min())
