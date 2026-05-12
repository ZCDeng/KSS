"""单股票因子深度分析 —— 时序 IC / 分位数收益 / 单因子信号回测.

与横截面 :class:`~kss.backtest.diagnostics.SignalDiagnostics` 的区别：

- **截面**: 同一天多只股票，因子值排序 → 选 Top → 等权组合.
- **时序**: 同一只股票多个交易日，因子值的时间序列 → 与未来收益的相关性
  / 分位区间持有收益 / 阈值买卖择时.

防 look-ahead 关键点：

1. 时序 z-score 用 **rolling window**（过去 N 日），不用全样本统计量.
2. 信号在 ``t`` 日 close 后产生，``t+1`` 开盘建仓，``t+2`` 开盘换仓——
   通过传入由 ``FactorPipeline.add_targets`` 计算的 ``next_day_return`` 实现.
3. 分位数桶界用 **历史 expanding** 分位，不用全样本.
"""

from __future__ import annotations

import logging
import warnings
from typing import TYPE_CHECKING, Literal

import numpy as np
import pandas as pd
from scipy import stats

from kss.backtest.metrics import Metrics

if TYPE_CHECKING:
    from kss.strategies.multi_factor import MultiFactorCombiner

logger = logging.getLogger(__name__)


class SingleStockAnalyzer:
    """单股票因子分析器.

    用例：

    .. code-block:: python

        analyzer = SingleStockAnalyzer(factor_df, ret_col="next_day_return")
        ic_table = analyzer.time_series_ic(
            feature_cols, horizons=["next_day_return"]
        )
        bucket = analyzer.quantile_returns("rsi_14", n_quantiles=5)
        bt = analyzer.signal_backtest("macd_hist", upper=1.0, lower=-1.0)
    """

    def __init__(
        self,
        factor_df: pd.DataFrame,
        date_col: str = "trade_date",
        ret_col: str = "next_day_return",
    ) -> None:
        """初始化.

        Args:
            factor_df: 单股票因子 DataFrame（由 ``FactorPipeline`` 产出，
                包含因子列 + 目标列 ``next_day_return`` / ``future_return_Nd``）.
            date_col: 交易日列名.
            ret_col: 回测期可达单日收益列名（默认 ``next_day_return``，
                由 :meth:`FactorPipeline.add_targets` 计算的 open[t+2]/open[t+1]-1）.

        Raises:
            ValueError: ``ret_col`` 不在 ``factor_df`` 中.
        """
        if ret_col not in factor_df.columns:
            raise ValueError(f"factor_df 缺少收益列 {ret_col}")
        self.df = factor_df.sort_values(date_col).reset_index(drop=True)
        self.date_col = date_col
        self.ret_col = ret_col

    # ------------------------------------------------------------------ #
    # 时序 IC
    # ------------------------------------------------------------------ #

    def time_series_ic(
        self,
        feature_cols: list[str],
        horizons: list[str] | None = None,
        method: Literal["pearson", "spearman"] = "spearman",
        rolling_window: int | None = None,
    ) -> pd.DataFrame:
        """对每个因子算时序 IC（因子值 → 未来收益的跨时间相关性）.

        Args:
            feature_cols: 因子列名.
            horizons: 收益列名（如 ``["next_day_return", "future_return_5d",
                "future_return_10d", "future_return_20d"]``）；缺省仅用 ``self.ret_col``.
            method: 相关系数类型.
            rolling_window: 若提供，则额外输出 ``rolling_ic_mean`` / ``rolling_ic_std``，
                显示因子稳定性；缺省（``None``）只算全样本 IC.

        Returns:
            DataFrame，每行一个 ``(factor, horizon)`` 组合，列：

            - ``factor``         : 因子名
            - ``horizon``        : 收益 horizon 列名
            - ``ic``             : 全样本 IC
            - ``ic_t_stat``      : IC 的 t 统计量（n 较大时近似 N(0,1)）
            - ``ic_p_value``     : p 值
            - ``n``              : 有效样本数
            - ``rolling_ic_mean``: 滚动 IC 均值（若 ``rolling_window`` 提供）
            - ``rolling_ic_std`` : 滚动 IC 标准差
        """
        horizons = horizons or [self.ret_col]
        rows: list[dict[str, object]] = []
        for h in horizons:
            if h not in self.df.columns:
                logger.warning("horizon %s 不在 factor_df 中，跳过", h)
                continue
            for col in feature_cols:
                if col not in self.df.columns:
                    continue
                sub = self.df[[col, h]].dropna()
                if len(sub) < 10:
                    rows.append({
                        "factor": col, "horizon": h,
                        "ic": float("nan"), "ic_t_stat": float("nan"),
                        "ic_p_value": float("nan"), "n": len(sub),
                    })
                    continue
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    if method == "spearman":
                        rho, p = stats.spearmanr(sub[col].values, sub[h].values)
                    else:
                        rho, p = stats.pearsonr(sub[col].values, sub[h].values)
                # IC 显著性 t-stat（Fisher transform 近似）
                n = len(sub)
                t_stat = (
                    rho * np.sqrt((n - 2) / max(1e-12, 1 - rho ** 2))
                    if np.isfinite(rho) else float("nan")
                )
                record: dict[str, object] = {
                    "factor": col, "horizon": h,
                    "ic": float(rho) if np.isfinite(rho) else float("nan"),
                    "ic_t_stat": float(t_stat) if np.isfinite(t_stat) else float("nan"),
                    "ic_p_value": float(p) if np.isfinite(p) else float("nan"),
                    "n": n,
                }
                if rolling_window:
                    # 滚动窗口 IC：每窗口内重新算 Spearman
                    roll_ic = self._rolling_ic(
                        self.df[col], self.df[h], window=rolling_window, method=method
                    )
                    record["rolling_ic_mean"] = float(roll_ic.mean())
                    record["rolling_ic_std"] = float(roll_ic.std())
                rows.append(record)

        return pd.DataFrame(rows).sort_values(
            ["horizon", "ic"], key=lambda s: s.abs() if s.name == "ic" else s,
            ascending=[True, False],
        ).reset_index(drop=True)

    @staticmethod
    def _rolling_ic(
        x: pd.Series,
        y: pd.Series,
        window: int,
        method: Literal["pearson", "spearman"] = "spearman",
    ) -> pd.Series:
        """滚动窗口 IC（同一对序列上）.

        pandas 自带 ``Series.rolling().corr()`` 是 Pearson；Spearman 需要把每个窗口
        都做秩变换再算 Pearson—— 此处用 ``apply`` 实现.
        """
        merged = pd.concat([x, y], axis=1).dropna()
        if len(merged) < window:
            return pd.Series(dtype=float)

        def _ic(arr: np.ndarray) -> float:
            if len(arr) < window:
                return float("nan")
            xv = arr[:window]
            yv = arr[window:]
            if np.std(xv) == 0 or np.std(yv) == 0:
                return float("nan")
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                if method == "spearman":
                    return float(stats.spearmanr(xv, yv)[0])
                return float(stats.pearsonr(xv, yv)[0])

        # rolling apply：把 x、y 各窗口拼起来传给 _ic
        out: list[float] = []
        x_arr = merged.iloc[:, 0].values
        y_arr = merged.iloc[:, 1].values
        for i in range(len(merged) - window + 1):
            xv = x_arr[i : i + window]
            yv = y_arr[i : i + window]
            if np.std(xv) == 0 or np.std(yv) == 0:
                out.append(float("nan"))
                continue
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                if method == "spearman":
                    out.append(float(stats.spearmanr(xv, yv)[0]))
                else:
                    out.append(float(stats.pearsonr(xv, yv)[0]))
        return pd.Series(out)

    # ------------------------------------------------------------------ #
    # 分位数桶收益（防 look-ahead：用 expanding 分位边界）
    # ------------------------------------------------------------------ #

    def quantile_returns(
        self,
        factor: str,
        n_quantiles: int = 5,
        min_history: int = 60,
    ) -> pd.DataFrame:
        """按因子值历史分位把每天分桶，统计每桶的未来单日收益.

        防 look-ahead：第 t 日的桶边界用 ``[0, t]`` 的历史分位（``expanding``），
        不用全样本分位.

        Args:
            factor: 因子列名.
            n_quantiles: 分桶数.
            min_history: 计算 expanding 分位的最少历史天数；不足时跳过.

        Returns:
            DataFrame，每个桶一行，列：
            ``bucket / n_days / mean_return / total_return / sharpe / win_rate``.
        """
        if factor not in self.df.columns:
            raise ValueError(f"因子 {factor} 不在 factor_df 中")

        x = self.df[factor].values
        r = self.df[self.ret_col].values
        labels = np.full(len(x), -1, dtype=int)

        for i in range(min_history, len(x)):
            past = x[:i]
            past = past[~np.isnan(past)]
            if len(past) < min_history:
                continue
            qs = np.quantile(past, np.linspace(0, 1, n_quantiles + 1)[1:-1])
            if np.any(np.diff(qs) == 0):
                continue
            v = x[i]
            if not np.isfinite(v):
                continue
            labels[i] = int(np.searchsorted(qs, v))

        df = pd.DataFrame({"bucket": labels, "ret": r, factor: x})
        df = df[(df["bucket"] >= 0) & df["ret"].notna()]

        rows: list[dict[str, object]] = []
        for b in range(n_quantiles):
            sub = df[df["bucket"] == b]
            if sub.empty:
                rows.append({
                    "bucket": f"Q{b + 1}", "n_days": 0,
                    "mean_return": float("nan"), "total_return": float("nan"),
                    "sharpe": float("nan"), "win_rate": float("nan"),
                })
                continue
            r_series = sub["ret"]
            m = Metrics.calc(r_series)
            rows.append({
                "bucket": f"Q{b + 1}",
                "n_days": int(len(sub)),
                "mean_return": float(r_series.mean()),
                "total_return": float((1 + r_series).prod() - 1),
                "sharpe": float(m.get("sharpe", 0.0)),
                "win_rate": float(m.get("win", 0.0)),
            })
        return pd.DataFrame(rows)

    # ------------------------------------------------------------------ #
    # 内部共享：z-score → 仓位 → 净收益
    # ------------------------------------------------------------------ #

    @staticmethod
    def _zscore_to_positions(
        z: pd.Series,
        upper: float,
        lower: float,
        long_only: bool,
    ) -> pd.Series:
        """z-score 序列 → 日度仓位（0/1 或 -1/0/1）.

        - ``z > upper`` → 持仓（+1）
        - ``z < lower`` → 空仓（0，``long_only``）或做空（-1）
        - 中间区域 → ``ffill`` 沿用上日仓位（持仓惯性）；无 z 段为 0.
        """
        sig = pd.Series(np.nan, index=z.index, dtype=float)
        sig.loc[z > upper] = 1.0
        if long_only:
            sig.loc[z < lower] = 0.0
        else:
            sig.loc[z < lower] = -1.0
        return sig.ffill().fillna(0.0)

    def _positions_to_returns(
        self,
        positions: pd.Series,
        buy_cost: float,
        sell_cost: float,
    ) -> dict[str, object]:
        """仓位 → 毛/净收益 + 换手 + 交易次数.

        与原 :meth:`signal_backtest` 内联逻辑完全等价（行为已被 test_backtest 锁死）；
        抽出后可被 :meth:`multi_factor_backtest` 复用，避免行为漂移.

        Args:
            positions: 日度仓位 ``pd.Series``.
            buy_cost / sell_cost: 单边费率.

        Returns:
            字典：``net_return / gross_return / turnover_total / n_trades``.
        """
        r = self.df[self.ret_col]
        turnover = positions.diff().abs().fillna(
            positions.abs().iloc[0] if len(positions) else 0
        )
        delta = positions.diff().fillna(positions.iloc[0] if len(positions) else 0)
        cost_rate = pd.Series(0.0, index=positions.index)
        cost_rate.loc[delta > 0] = buy_cost
        cost_rate.loc[delta < 0] = sell_cost
        gross = positions * r
        net = gross - turnover * cost_rate
        mask = r.notna()
        gross = gross.where(mask)
        net = net.where(mask)
        return {
            "gross_return": gross,
            "net_return": net,
            "turnover_total": float(turnover.sum()),
            "n_trades": int((delta.abs() > 1e-9).sum()),
        }

    @staticmethod
    def _rolling_zscore(x: pd.Series, window: int) -> pd.Series:
        """滚动 z-score：mean / std 用 ``[t - window, t]``，避免 look-ahead.

        与 :meth:`signal_backtest` 原行内逻辑严格等价（``+1e-8`` 防除零保留）.
        """
        mean = x.rolling(window, min_periods=window).mean()
        std = x.rolling(window, min_periods=window).std()
        return (x - mean) / (std + 1e-8)

    # ------------------------------------------------------------------ #
    # 单因子信号回测（rolling z-score 阈值策略）
    # ------------------------------------------------------------------ #

    def signal_backtest(
        self,
        factor: str,
        upper: float = 1.0,
        lower: float = -1.0,
        rolling_window: int = 60,
        long_only: bool = True,
        buy_cost: float = 0.001,
        sell_cost: float = 0.002,
    ) -> dict[str, object]:
        """基于因子滚动 z-score 阈值的单股择时回测.

        策略：

        - ``z_t > upper`` → 持仓（信号 = 1）
        - ``z_t < lower`` → 空仓（``long_only=True``）或反向（``long_only=False``）
        - 中间区域 → 沿用上一日仓位（持仓惯性，减少换手）

        防 look-ahead：

        - z-score 用 ``[t - rolling_window, t]`` 历史窗口；
        - 信号在 ``t`` 日收盘后生成，从 ``next_day_return`` 取下一日实盘收益.

        Args:
            factor: 因子列名.
            upper / lower: 持仓阈值（rolling z 单位）.
            rolling_window: z-score 滚动窗口.
            long_only: ``True`` = 只做多；``False`` = z<lower 时反向做空.
            buy_cost / sell_cost: 单边交易成本（小数）.

        Returns:
            字典字段：

            - ``metrics``       : :func:`Metrics.calc` 输出
            - ``net_return``    : 日度净收益 Series
            - ``positions``     : 日度仓位（0/1，或 -1/0/1）
            - ``signal_z``      : 滚动 z-score 序列
            - ``turnover_total``: 区间累计换手率
            - ``n_trades``      : 仓位翻转次数（每次买卖各算一次）
        """
        if factor not in self.df.columns:
            raise ValueError(f"因子 {factor} 不在 factor_df 中")

        z = self._rolling_zscore(self.df[factor], rolling_window)
        pos = self._zscore_to_positions(z, upper, lower, long_only)
        pnl = self._positions_to_returns(pos, buy_cost, sell_cost)
        m = Metrics.calc(pnl["net_return"].dropna())

        return {
            "metrics": m,
            "net_return": pnl["net_return"],
            "gross_return": pnl["gross_return"],
            "positions": pos,
            "signal_z": z,
            "turnover_total": pnl["turnover_total"],
            "n_trades": pnl["n_trades"],
        }

    # ------------------------------------------------------------------ #
    # 多因子组合信号回测
    # ------------------------------------------------------------------ #

    def multi_factor_backtest(
        self,
        combiner: "MultiFactorCombiner",
        upper: float = 1.0,
        lower: float = -1.0,
        rolling_window: int = 60,
        long_only: bool = True,
        buy_cost: float = 0.001,
        sell_cost: float = 0.002,
    ) -> dict[str, object]:
        """多因子线性加权组合信号回测.

        流程：

        1. 对 ``combiner.factors`` 中每个因子算 ``[t - rolling_window, t]`` 的 z-score；
        2. ``combiner.combine_zscores(z_dict)`` 合成 z（支持负权重 = 反向因子）；
        3. 合成 z > ``upper`` → 持仓；< ``lower`` → 空仓/反向；中间 ``ffill``；
        4. 在 ``next_day_return`` 上扣双边成本计算净收益.

        返回 dict 结构与 :meth:`signal_backtest` 同构，便于绘图/对比脚本统一消费.

        Args:
            combiner: ``MultiFactorCombiner`` 实例（来自 :mod:`kss.strategies.multi_factor`）.
            upper / lower / rolling_window / long_only / buy_cost / sell_cost:
                透传给单因子路径，含义同 :meth:`signal_backtest`.

        Returns:
            字典字段：``metrics / net_return / gross_return / positions /
            signal_z / turnover_total / n_trades / weights / factors``.

        Raises:
            ValueError: combiner 的因子列在 ``factor_df`` 中全部缺失.
        """
        # 计算每个因子的 rolling z-score
        z_dict: dict[str, pd.Series] = {}
        for f in combiner.factors:
            if f not in self.df.columns:
                logger.warning("因子 %s 不在 factor_df 中，跳过", f)
                continue
            z_dict[f] = self._rolling_zscore(self.df[f], rolling_window)
        if not z_dict:
            raise ValueError(
                f"combiner 因子 {combiner.factors} 均不在 factor_df 中"
            )

        combined_z = combiner.combine_zscores(z_dict)
        pos = self._zscore_to_positions(combined_z, upper, lower, long_only)
        pnl = self._positions_to_returns(pos, buy_cost, sell_cost)
        m = Metrics.calc(pnl["net_return"].dropna())

        return {
            "metrics": m,
            "net_return": pnl["net_return"],
            "gross_return": pnl["gross_return"],
            "positions": pos,
            "signal_z": combined_z,
            "turnover_total": pnl["turnover_total"],
            "n_trades": pnl["n_trades"],
            "weights": dict(combiner.weights),
            "factors": list(combiner.factors),
        }

    # ------------------------------------------------------------------ #
    # 阈值网格搜索
    # ------------------------------------------------------------------ #

    def threshold_grid_search(
        self,
        factor_or_combiner: "str | MultiFactorCombiner",
        upper_grid: list[float] | None = None,
        lower_grid: list[float] | None = None,
        rolling_window: int = 60,
        long_only: bool = True,
        buy_cost: float = 0.001,
        sell_cost: float = 0.002,
        require_symmetric: bool = False,
    ) -> pd.DataFrame:
        """在 ``(upper, lower)`` 网格上回测，输出每点 Sharpe / 回撤 / 年化.

        除了找单点最优，更建议看**邻域稳健性**：单点 Sharpe 高但邻域差异大 → 过拟合.
        输出含 ``robust_sharpe`` 列（同 row 邻域 Sharpe 中位数），便于挑稳健点.

        Args:
            factor_or_combiner: 因子名字符串或已构造的 ``MultiFactorCombiner``.
            upper_grid: 持仓阈值候选；缺省 ``[0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0]``.
            lower_grid: 空仓阈值候选；缺省 ``[-2.0, -1.5, -1.25, -1.0, -0.75, -0.5, -0.25]``.
            rolling_window: z-score 窗口.
            long_only / buy_cost / sell_cost: 透传给底层回测.
            require_symmetric: ``True`` 时只跑 ``lower = -upper`` 的对称点（降低 grid 大小）.

        Returns:
            DataFrame，列：``upper / lower / annual / sharpe / max_dd / calmar /
            n_trades / total / robust_sharpe``.
        """
        if upper_grid is None:
            upper_grid = [0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0]
        if lower_grid is None:
            lower_grid = [-2.0, -1.5, -1.25, -1.0, -0.75, -0.5, -0.25]

        rows: list[dict[str, object]] = []
        for u in upper_grid:
            for l in lower_grid:
                if require_symmetric and abs(u + l) > 1e-9:
                    continue
                if u <= l:
                    # 阈值反向无意义（持仓阈低于空仓阈）
                    continue
                try:
                    if isinstance(factor_or_combiner, str):
                        bt = self.signal_backtest(
                            factor_or_combiner,
                            upper=u, lower=l,
                            rolling_window=rolling_window,
                            long_only=long_only,
                            buy_cost=buy_cost, sell_cost=sell_cost,
                        )
                    else:
                        bt = self.multi_factor_backtest(
                            factor_or_combiner,
                            upper=u, lower=l,
                            rolling_window=rolling_window,
                            long_only=long_only,
                            buy_cost=buy_cost, sell_cost=sell_cost,
                        )
                except Exception as exc:  # noqa: BLE001
                    logger.debug("threshold (%.2f, %.2f) 失败: %s", u, l, exc)
                    continue
                m = bt["metrics"]
                rows.append({
                    "upper": u, "lower": l,
                    "annual": m.get("annual", float("nan")),
                    "sharpe": m.get("sharpe", float("nan")),
                    "max_dd": m.get("max_dd", float("nan")),
                    "calmar": m.get("calmar", float("nan")),
                    "n_trades": int(bt["n_trades"]),
                    "total": m.get("total", float("nan")),
                })

        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows)
        # 邻域稳健性：每行的 robust_sharpe = 同 upper 或同 lower 相邻 2 格的 Sharpe 中位数
        df["robust_sharpe"] = df.apply(
            lambda row: float(np.median(df[
                (df["upper"].between(row["upper"] - 0.3, row["upper"] + 0.3))
                & (df["lower"].between(row["lower"] - 0.3, row["lower"] + 0.3))
            ]["sharpe"].dropna())) if df["sharpe"].notna().any() else float("nan"),
            axis=1,
        )
        return df.sort_values("sharpe", ascending=False).reset_index(drop=True)

    # ------------------------------------------------------------------ #
    # 批量扫描：对所有因子跑信号回测，按 Sharpe / IR 排名
    # ------------------------------------------------------------------ #

    def scan_factors(
        self,
        feature_cols: list[str],
        upper: float = 1.0,
        lower: float = -1.0,
        rolling_window: int = 60,
        buy_cost: float = 0.001,
        sell_cost: float = 0.002,
    ) -> pd.DataFrame:
        """批量遍历因子做信号回测，给出排序表.

        Args:
            feature_cols: 因子列名.
            upper / lower / rolling_window / buy_cost / sell_cost:
                透传给 :meth:`signal_backtest`.

        Returns:
            按 ``sharpe`` 降序的 DataFrame，列：
            ``factor / annual / sharpe / max_dd / win_rate / n_trades / total / n``.
        """
        rows: list[dict[str, object]] = []
        for col in feature_cols:
            if col not in self.df.columns:
                continue
            try:
                bt = self.signal_backtest(
                    col, upper=upper, lower=lower,
                    rolling_window=rolling_window,
                    buy_cost=buy_cost, sell_cost=sell_cost,
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug("因子 %s 信号回测失败: %s", col, exc)
                continue
            m = bt["metrics"]
            if not m:
                continue
            rows.append({
                "factor": col,
                "annual": m.get("annual", float("nan")),
                "sharpe": m.get("sharpe", float("nan")),
                "max_dd": m.get("max_dd", float("nan")),
                "win_rate": m.get("win", float("nan")),
                "n_trades": int(bt["n_trades"]),
                "total": m.get("total", float("nan")),
                "n": m.get("n", 0),
            })
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows)
        return df.sort_values("sharpe", ascending=False).reset_index(drop=True)
