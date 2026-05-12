"""信号质量诊断 —— IC / 分位数 / 多空价差 / 单调性 / IC 衰减.

本模块与策略组合解耦：直接基于 ``pred_score`` 与未来收益（``next_day_return``
或 ``future_return_Nd``）评估信号本身的预测力，不依赖 ``top_pct`` 选股。

典型用法：

.. code-block:: python

    panel = result_df.attrs["panel"]  # walk_forward 收集的全市场打分面板
    ic = SignalDiagnostics.ic_series(panel, "pred_score", "next_day_return")
    summary = SignalDiagnostics.ic_summary(ic)
    quant = SignalDiagnostics.quantile_returns(panel, "pred_score", "next_day_return")
    spread = SignalDiagnostics.long_short_spread(quant)
"""

from __future__ import annotations

import warnings
from typing import Literal

import numpy as np
import pandas as pd
from scipy import stats

_TRADING_DAYS = 252


class SignalDiagnostics:
    """信号质量诊断指标集合.

    所有方法都是无状态 ``@staticmethod``，便于在脚本/notebook 中按需组合调用.
    """

    # ------------------------------------------------------------------ #
    # IC / Rank IC
    # ------------------------------------------------------------------ #

    @staticmethod
    def ic(
        pred: pd.Series,
        ret: pd.Series,
        method: Literal["pearson", "spearman"] = "spearman",
    ) -> float:
        """单日（或单截面）IC.

        Args:
            pred: 预测打分序列.
            ret: 对齐的未来收益序列.
            method: ``"spearman"`` 为秩相关（Rank IC，推荐），``"pearson"`` 为线性 IC.

        Returns:
            相关系数；样本不足或退化（std=0）时返回 ``np.nan``.
        """
        s = pd.concat([pred, ret], axis=1).dropna()
        if len(s) < 3:
            return float("nan")
        x = s.iloc[:, 0].values
        y = s.iloc[:, 1].values
        if np.std(x) == 0 or np.std(y) == 0:
            return float("nan")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            if method == "spearman":
                rho, _ = stats.spearmanr(x, y)
            else:
                rho, _ = stats.pearsonr(x, y)
        return float(rho) if np.isfinite(rho) else float("nan")

    @staticmethod
    def ic_series(
        panel: pd.DataFrame,
        pred_col: str,
        ret_col: str,
        date_col: str = "trade_date",
        method: Literal["pearson", "spearman"] = "spearman",
    ) -> pd.Series:
        """按交易日分组计算 IC 时间序列.

        Args:
            panel: 包含 ``date_col``、``pred_col``、``ret_col`` 的 DataFrame.
            pred_col: 预测打分列名.
            ret_col: 未来收益列名.
            date_col: 交易日列名.
            method: 相关系数类型.

        Returns:
            Index 为日期、Value 为当日 IC 的 ``pd.Series``，顺序按日期升序.
        """
        if panel.empty:
            return pd.Series(dtype=float)

        records: list[tuple[pd.Timestamp, float]] = []
        for d, group in panel.groupby(date_col):
            ic_val = SignalDiagnostics.ic(group[pred_col], group[ret_col], method)
            records.append((d, ic_val))

        s = pd.Series(dict(records), name=f"ic_{method}")
        s.index.name = date_col
        return s.sort_index()

    @staticmethod
    def ic_summary(ic: pd.Series) -> dict[str, float]:
        """汇总 IC 时间序列的核心统计量.

        Args:
            ic: ``ic_series`` 返回的日度 IC 序列.

        Returns:
            字典字段：

            - ``ic_mean``       : IC 均值
            - ``ic_std``        : IC 标准差
            - ``ir``            : Information Ratio = ic_mean / ic_std * sqrt(252)
            - ``ic_t_stat``     : IC 的 t 统计量（均值显著偏离 0 的程度）
            - ``ic_positive_rate``: IC > 0 的日占比
            - ``ic_skew``       : IC 偏度
            - ``ic_max_consec_neg``: 最长连续负 IC 天数（信号崩溃风险）
            - ``n``             : 有效日数
        """
        ic = ic.dropna()
        n = len(ic)
        if n == 0:
            return {
                "ic_mean": float("nan"),
                "ic_std": float("nan"),
                "ir": float("nan"),
                "ic_t_stat": float("nan"),
                "ic_positive_rate": float("nan"),
                "ic_skew": float("nan"),
                "ic_max_consec_neg": 0.0,
                "n": 0,
            }

        ic_mean = float(ic.mean())
        ic_std = float(ic.std())
        ir = ic_mean / ic_std * np.sqrt(_TRADING_DAYS) if ic_std > 0 else 0.0
        # IC 序列的 t 统计量：均值 / (std / sqrt(n))
        t_stat = ic_mean / (ic_std / np.sqrt(n)) if ic_std > 0 and n > 1 else 0.0
        positive_rate = float((ic > 0).mean())
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            ic_skew = float(stats.skew(ic.values, bias=False)) if n >= 3 and ic_std > 0 else 0.0

        # 最长连续负 IC 天数
        neg_mask = (ic < 0).astype(int).values
        max_consec_neg = 0
        cur = 0
        for v in neg_mask:
            if v == 1:
                cur += 1
                if cur > max_consec_neg:
                    max_consec_neg = cur
            else:
                cur = 0

        return {
            "ic_mean": ic_mean,
            "ic_std": ic_std,
            "ir": float(ir),
            "ic_t_stat": float(t_stat),
            "ic_positive_rate": positive_rate,
            "ic_skew": ic_skew,
            "ic_max_consec_neg": float(max_consec_neg),
            "n": n,
        }

    # ------------------------------------------------------------------ #
    # 分位数分组
    # ------------------------------------------------------------------ #

    @staticmethod
    def quantile_returns(
        panel: pd.DataFrame,
        pred_col: str,
        ret_col: str,
        n_quantiles: int = 5,
        date_col: str = "trade_date",
    ) -> pd.DataFrame:
        """按预测打分截面分位数构建等权组合并计算每日收益.

        Args:
            panel: 含 ``date_col``、``pred_col``、``ret_col`` 的面板 DataFrame.
            pred_col: 预测打分列名.
            ret_col: 未来收益列名.
            n_quantiles: 分位数数量（默认 5，即五分位）.
            date_col: 交易日列名.

        Returns:
            DataFrame，index 为日期，列为 ``Q1``..``Q{n_quantiles}``，
            每列为该分位组合的等权日收益.
        """
        if panel.empty:
            return pd.DataFrame()

        records: dict[pd.Timestamp, dict[str, float]] = {}
        for d, group in panel.groupby(date_col):
            g = group.dropna(subset=[pred_col, ret_col])
            # 单日股票数 < n_quantiles 无法分组；记 NaN 行
            if len(g) < n_quantiles:
                records[d] = {f"Q{i+1}": float("nan") for i in range(n_quantiles)}
                continue
            # rank(method="first") 打破 tied score，避免分位数大小不均
            ranks = g[pred_col].rank(method="first")
            try:
                q = pd.qcut(
                    ranks,
                    q=n_quantiles,
                    labels=[f"Q{i+1}" for i in range(n_quantiles)],
                )
            except ValueError:
                # 退化情况（如全相同打分），跳过该日
                records[d] = {f"Q{i+1}": float("nan") for i in range(n_quantiles)}
                continue
            day_row: dict[str, float] = {}
            for label, sub in g.groupby(q, observed=True):
                day_row[str(label)] = float(sub[ret_col].mean())
            # 确保所有分位都有列（缺失填 NaN）
            for i in range(n_quantiles):
                day_row.setdefault(f"Q{i+1}", float("nan"))
            records[d] = day_row

        df = pd.DataFrame.from_dict(records, orient="index")
        df = df.reindex(columns=[f"Q{i+1}" for i in range(n_quantiles)])
        df.index.name = date_col
        return df.sort_index()

    @staticmethod
    def long_short_spread(quantile_df: pd.DataFrame) -> pd.Series:
        """多空价差 = 最高分位 - 最低分位 日收益.

        Args:
            quantile_df: ``quantile_returns`` 返回的 DataFrame（列形如 ``Q1..QN``）.

        Returns:
            日度多空价差 ``pd.Series``.
        """
        if quantile_df.empty or quantile_df.shape[1] < 2:
            return pd.Series(dtype=float)
        cols = list(quantile_df.columns)
        return (quantile_df[cols[-1]] - quantile_df[cols[0]]).rename("long_short")

    @staticmethod
    def monotonicity_score(quantile_df: pd.DataFrame) -> float:
        """各分位累计收益与预期分位序的 Spearman 秩相关.

        若信号有效，越高分位（Q5）应累计收益越高；该值接近 +1 = 单调上升，
        接近 -1 = 单调反向，0 附近 = 无序.

        Args:
            quantile_df: ``quantile_returns`` 返回的 DataFrame.

        Returns:
            单调性得分（-1 ~ +1）；无足够数据时返回 ``np.nan``.
        """
        if quantile_df.empty or quantile_df.shape[1] < 3:
            return float("nan")
        cum_per_q = (1 + quantile_df.fillna(0)).prod() - 1
        expected_order = np.arange(len(cum_per_q))  # Q1 索引 0, QN 索引 N-1
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            rho, _ = stats.spearmanr(expected_order, cum_per_q.values)
        return float(rho) if np.isfinite(rho) else float("nan")

    # ------------------------------------------------------------------ #
    # 批量截面 IC 扫描（选因子用）
    # ------------------------------------------------------------------ #

    @staticmethod
    def cross_section_ic_scan(
        panel: pd.DataFrame,
        factor_cols: list[str],
        horizons: list[str] | None = None,
        date_col: str = "trade_date",
        method: Literal["pearson", "spearman"] = "spearman",
    ) -> pd.DataFrame:
        """批量计算 N 因子 × M horizon 的截面 IC summary.

        与 :meth:`ic_decay` 的区别：``ic_decay`` 是固定单因子在多 horizon 上的衰减；
        本方法是多因子 × 多 horizon 的扫描矩阵——用于"哪些因子在哪个 horizon
        上有显著截面预测力"的批量诊断.

        Args:
            panel: 含 ``date_col`` / 因子列 / 所有 ``horizons`` 列的横截面面板.
            factor_cols: 候选因子列名.
            horizons: 收益 horizon 列名；缺省 ``["next_day_return"]``.
            date_col: 交易日列.
            method: 相关系数类型.

        Returns:
            DataFrame，每行一个 ``(factor, horizon)`` 组合，列：

            - ``factor / horizon``
            - ``ic_mean / ic_std / ir``
            - ``ic_t_stat / ic_positive_rate / ic_max_consec_neg``
            - ``n`` (有效日数)
            - ``abs_t_stat`` (用于排序 / 筛选)

            按 ``horizon`` 升序、``abs_t_stat`` 降序排列.
        """
        horizons = horizons or ["next_day_return"]
        missing_horizons = [h for h in horizons if h not in panel.columns]
        if missing_horizons:
            raise ValueError(f"panel 缺少 horizon 列: {missing_horizons}")

        rows: list[dict[str, object]] = []
        for h in horizons:
            for f in factor_cols:
                if f not in panel.columns:
                    continue
                ic_s = SignalDiagnostics.ic_series(
                    panel, f, h, date_col=date_col, method=method,
                )
                summary = SignalDiagnostics.ic_summary(ic_s)
                rows.append({
                    "factor": f,
                    "horizon": h,
                    "ic_mean": summary["ic_mean"],
                    "ic_std": summary["ic_std"],
                    "ir": summary["ir"],
                    "ic_t_stat": summary["ic_t_stat"],
                    "ic_positive_rate": summary["ic_positive_rate"],
                    "ic_max_consec_neg": summary["ic_max_consec_neg"],
                    "n": summary["n"],
                    "abs_t_stat": abs(summary["ic_t_stat"])
                    if np.isfinite(summary["ic_t_stat"]) else 0.0,
                })

        if not rows:
            return pd.DataFrame()
        return pd.DataFrame(rows).sort_values(
            ["horizon", "abs_t_stat"], ascending=[True, False],
        ).reset_index(drop=True)

    # ------------------------------------------------------------------ #
    # IC 衰减
    # ------------------------------------------------------------------ #

    @staticmethod
    def ic_decay(
        panel: pd.DataFrame,
        pred_col: str,
        ret_cols: list[str],
        date_col: str = "trade_date",
        method: Literal["pearson", "spearman"] = "spearman",
    ) -> pd.DataFrame:
        """跨多个预测 horizon 的 IC（衡量信号半衰期）.

        Args:
            panel: 含 ``date_col``、``pred_col`` 及多个 ``ret_cols`` 的面板.
            pred_col: 预测打分列名.
            ret_cols: 不同 horizon 的未来收益列（如 ``["future_return_1d",
                "future_return_5d", "future_return_10d", "future_return_20d"]``）.
            date_col: 交易日列名.
            method: 相关系数类型.

        Returns:
            DataFrame，列：

            - ``horizon``     : 收益列名（horizon 标识）
            - ``ic_mean``     : 平均 IC
            - ``ir``          : 信息比率
            - ``ic_positive_rate``: IC > 0 占比
        """
        rows: list[dict[str, float]] = []
        for col in ret_cols:
            if col not in panel.columns:
                continue
            ic_s = SignalDiagnostics.ic_series(panel, pred_col, col, date_col, method)
            summary = SignalDiagnostics.ic_summary(ic_s)
            rows.append({
                "horizon": col,
                "ic_mean": summary["ic_mean"],
                "ir": summary["ir"],
                "ic_positive_rate": summary["ic_positive_rate"],
            })
        return pd.DataFrame(rows)
