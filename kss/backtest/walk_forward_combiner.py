"""单股票时序 walk-forward 多因子组合 —— 在每个 retrain 点重新选权重.

去除 ``scripts/analyze_688017_combo.py`` v1/v2 的核心 look-ahead bias：
``MultiFactorCombiner.from_scan_table`` / ``from_ic_table`` 用全样本统计选权重 →
"未来数据决定了今天该买什么".

正确做法：每个 retrain 时间点 t 上，只用 ``[t - train_window, t]`` 的历史数据：

1. 跑 ``SingleStockAnalyzer.scan_factors`` / ``time_series_ic`` 算 scan_table / ic_table；
2. 用 ``combiner_builder`` 构造 ``MultiFactorCombiner``；
3. 用该 combiner 在 ``[t, t + retrain_freq)`` 段产生合成 z-score、信号、仓位.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np
import pandas as pd

from kss.backtest.metrics import Metrics
from kss.backtest.single_stock import SingleStockAnalyzer
from kss.strategies.multi_factor import MultiFactorCombiner

logger = logging.getLogger(__name__)


# ``combiner_builder`` 函数签名：(历史 SingleStockAnalyzer, feature_cols) → MultiFactorCombiner
CombinerBuilder = Callable[[SingleStockAnalyzer, list[str]], MultiFactorCombiner]


def _daily_sign_ic(
    factor: pd.Series, ret: pd.Series, dates: pd.Series
) -> pd.Series:
    """单股票时序的日级 IC 代理：``sign(因子去均值) × sign(次日收益)`` ∈ {-1,+1}.

    单股票一日仅一条 (因子, 收益)，无法逐日做截面 Spearman；用符号一致性作为日级
    方向信号的代理，正向均值≈方向命中率正偏。index = trade_date 供 health hook 折
    成去重日 IC（有效 n = 去重交易日数）。PIT 清白：只用已观测的训练窗口数据。

    口径 = ``sign_proxy``（非 Rank-IC）：绑定到 ``FactorHealthHook.on_backtest_end``
    时须传 ``method=IC_METHOD_SIGN_PROXY``，否则 #8 仲裁会把符号代理与 rank_ic 当同轴比。
    """
    f = factor.astype(float)
    r = ret.astype(float)
    f_demean = f - f.mean()
    sign_ic = np.sign(f_demean.values) * np.sign(r.values)
    return pd.Series(sign_ic, index=pd.Index(dates.values), dtype=float)


@dataclass
class WalkForwardCombiner:
    """时序滚动多因子组合器.

    Attributes:
        factor_df: 含 ``trade_date`` / 所有 ``feature_cols`` / ``next_day_return`` 的面板.
        feature_cols: 候选因子列名.
        combiner_builder: 每个 retrain 点构造 ``MultiFactorCombiner`` 的工厂函数.
        train_window: 滚动训练窗口（交易日）.
        retrain_freq: 重训频率（每 N 交易日重选权重）.
        rolling_window: 用于因子 rolling z-score 的窗口（不变量；与 train_window 解耦）.
        upper / lower: 持仓阈值.
        long_only: True = 只做多.
        buy_cost / sell_cost: 单边费率.
        ret_col: 实盘可达收益列名.
        date_col: 交易日列.
        min_warm_up: 第一次 retrain 之前需要的最少历史天数（与 train_window 取最大）.
    """

    factor_df: pd.DataFrame
    feature_cols: list[str]
    combiner_builder: CombinerBuilder
    train_window: int = 200
    retrain_freq: int = 20
    rolling_window: int = 60
    upper: float = 1.0
    lower: float = -1.0
    long_only: bool = True
    buy_cost: float = 0.001
    sell_cost: float = 0.002
    ret_col: str = "next_day_return"
    date_col: str = "trade_date"
    # U3 因子健康度 hook：回测结束点把各 retrain 因子 IC 快照入库；默认 None =
    # 不改变现有调用方行为（surgical）。签名 (factor_id, window_start, window_end,
    # ic_series) → None。
    health_hook: Callable[[str, str, str, "pd.Series"], None] | None = None
    min_warm_up: int = field(init=False)

    def __post_init__(self) -> None:
        # min_warm_up：训练前需要 rolling_window 天数来算 z-score，加上 train_window
        self.min_warm_up = max(self.train_window, self.rolling_window)
        # 排序便于切片
        self._df = self.factor_df.sort_values(self.date_col).reset_index(drop=True)

    # ------------------------------------------------------------------ #
    # 主流程
    # ------------------------------------------------------------------ #

    def run(self) -> dict[str, Any]:
        """跑滚动权重 walk-forward.

        Returns:
            字典字段：

            - ``metrics``         : :func:`Metrics.calc` 输出
            - ``net_return``      : 全期日度净收益
            - ``gross_return``    : 毛收益
            - ``positions``       : 仓位
            - ``signal_z``        : 合成 z-score（各 retrain 窗口拼接）
            - ``turnover_total``  : 累计换手率
            - ``n_trades``        : 仓位翻转次数
            - ``weights_history`` : list[dict]，每个 retrain 点的 ``{trade_date, weights}``
            - ``n_retrains``      : retrain 总次数
        """
        n = len(self._df)
        if n < self.min_warm_up + self.retrain_freq:
            raise ValueError(
                f"样本不足：{n} < min_warm_up {self.min_warm_up} + retrain_freq "
                f"{self.retrain_freq}; 至少需要 {self.min_warm_up + self.retrain_freq} 天"
            )

        # 全期合成 z 与权重历史
        combined_z = pd.Series(np.nan, index=self._df.index, dtype=float, name="combined_z")
        weights_history: list[dict[str, Any]] = []
        n_retrains = 0
        last_combiner: MultiFactorCombiner | None = None

        # 每个 retrain 点 i：用 [i - train_window, i) 数据训练，
        # 在 [i, i + retrain_freq) 上用合成 z
        for i in range(self.min_warm_up, n, self.retrain_freq):
            hist_slice = self._df.iloc[max(0, i - self.train_window):i].copy()
            # 历史窗口长度不足或目标列全 NaN → 跳过
            if len(hist_slice) < self.rolling_window + 5:
                continue
            if hist_slice[self.ret_col].notna().sum() < 10:
                continue

            hist_analyzer = SingleStockAnalyzer(
                hist_slice, date_col=self.date_col, ret_col=self.ret_col,
            )
            try:
                combiner = self.combiner_builder(hist_analyzer, self.feature_cols)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "retrain @ idx=%d 构造 combiner 失败: %s（沿用上一次权重）",
                    i, exc,
                )
                if last_combiner is None:
                    continue
                combiner = last_combiner

            n_retrains += 1
            last_combiner = combiner
            weights_history.append({
                "trade_date": self._df[self.date_col].iloc[i],
                "weights": dict(combiner.weights),
                "factors": list(combiner.factors),
            })

            # U3 健康度 hook（surgical）：在 retrain 点用 *训练窗口* 算各因子逐日
            # Rank-IC 并入库。PIT 清白：只用 [i-train_window, i) 历史，绝不引用未来。
            if self.health_hook is not None:
                window_start = str(hist_slice[self.date_col].iloc[0])
                window_end = str(self._df[self.date_col].iloc[i])
                for f in combiner.factors:
                    if f not in hist_slice.columns:
                        continue
                    pair = hist_slice[[self.date_col, f, self.ret_col]].dropna()
                    if len(pair) < 10:
                        continue
                    # 单股票时序场景：一日一条 (因子, 次日收益) 观测，无法逐日定义
                    # 截面 Spearman；用「因子去均值符号 × 收益符号」的逐日一致性作为
                    # 日级 IC 代理（∈{-1,+1}），index=trade_date，交 hook 折成去重日 IC。
                    ic_daily = _daily_sign_ic(
                        pair[f], pair[self.ret_col], pair[self.date_col]
                    )
                    try:
                        self.health_hook(f, window_start, window_end, ic_daily)
                    except Exception as exc:  # noqa: BLE001 —— hook 失败不打断回测
                        logger.warning("health_hook(%s) 失败: %s", f, exc)

            # 在 [i, i + retrain_freq) 上合成 z-score.
            # 关键防 look-ahead：rolling z-score 的 mean/std 用 ``[t - rolling_window, t]``，
            # 但 *因子值本身* 在测试期也是历史可观测的（这正是 z-score 的设计），
            # 唯一外泄风险是 combiner 权重——本类的根本贡献正是杜绝这点.
            test_end = min(i + self.retrain_freq, n)
            # 用 *全样本* 的 rolling z-score（每个时刻 t 仍只用 [t - w, t]），
            # 然后切片到测试期；避免每段重算造成不必要重复.
            for f in combiner.factors:
                if f not in self._df.columns:
                    continue
            z_dict_test = {
                f: SingleStockAnalyzer._rolling_zscore(self._df[f], self.rolling_window)
                for f in combiner.factors
                if f in self._df.columns
            }
            if not z_dict_test:
                continue
            seg_z = combiner.combine_zscores({
                f: z.iloc[i:test_end] for f, z in z_dict_test.items()
            })
            combined_z.iloc[i:test_end] = seg_z.values

        # 信号 → 仓位 → 净收益（复用 SingleStockAnalyzer 私有方法）
        # 注意：合并 analyzer 用全期 df 做 position→returns 的计算
        full_analyzer = SingleStockAnalyzer(
            self._df, date_col=self.date_col, ret_col=self.ret_col,
        )
        positions = SingleStockAnalyzer._zscore_to_positions(
            combined_z, self.upper, self.lower, self.long_only,
        )
        pnl = full_analyzer._positions_to_returns(
            positions, self.buy_cost, self.sell_cost,
        )
        m = Metrics.calc(pnl["net_return"].dropna())

        return {
            "metrics": m,
            "net_return": pnl["net_return"],
            "gross_return": pnl["gross_return"],
            "positions": positions,
            "signal_z": combined_z,
            "turnover_total": pnl["turnover_total"],
            "n_trades": pnl["n_trades"],
            "weights_history": weights_history,
            "n_retrains": n_retrains,
        }

    # ------------------------------------------------------------------ #
    # 便捷工厂
    # ------------------------------------------------------------------ #

    @staticmethod
    def builder_sharpe_topk(
        top_k: int = 5,
        weight_col: str = "sharpe",
        upper: float = 1.0,
        lower: float = -1.0,
        rolling_window: int = 60,
        clip_negative: bool = True,
    ) -> CombinerBuilder:
        """工厂：用 ``scan_factors`` 的 Top K 按 ``sharpe`` 加权.

        与 ``MultiFactorCombiner.from_scan_table`` 的区别：本工厂在每个 retrain 点
        用 **历史** SingleStockAnalyzer 重新 scan_factors，避免全样本 leak.

        Args:
            top_k / weight_col / clip_negative: 透传给 ``from_scan_table``.
            upper / lower / rolling_window: ``scan_factors`` 内部跑信号回测的阈值与窗口
                （注意：这里的阈值是评估"训练期 Sharpe"用，不是最终测试期阈值）.

        Returns:
            ``CombinerBuilder`` 函数.
        """
        def _builder(
            analyzer: SingleStockAnalyzer,
            feature_cols: list[str],
        ) -> MultiFactorCombiner:
            scan_table = analyzer.scan_factors(
                feature_cols, upper=upper, lower=lower,
                rolling_window=rolling_window,
            )
            if scan_table.empty:
                raise ValueError("历史窗口 scan_factors 返回空表")
            return MultiFactorCombiner.from_scan_table(
                scan_table, top_k=top_k, weight_col=weight_col,
                clip_negative=clip_negative,
            )
        return _builder

    @staticmethod
    def builder_ic_topk(
        top_k: int = 5,
        horizon: str = "future_return_5d",
        signed: bool = True,
        method: str = "spearman",
    ) -> CombinerBuilder:
        """工厂：按历史 IC Top K 加权.

        Args:
            top_k / horizon / signed: 透传给 ``MultiFactorCombiner.from_ic_table``.
            method: ``"spearman"`` 或 ``"pearson"``.

        Returns:
            ``CombinerBuilder`` 函数.
        """
        def _builder(
            analyzer: SingleStockAnalyzer,
            feature_cols: list[str],
        ) -> MultiFactorCombiner:
            ic_table = analyzer.time_series_ic(
                feature_cols, horizons=[horizon], method=method,
            )
            if ic_table.empty:
                raise ValueError("历史窗口 time_series_ic 返回空表")
            return MultiFactorCombiner.from_ic_table(
                ic_table, horizon=horizon, top_k=top_k, signed=signed,
            )
        return _builder

    @staticmethod
    def builder_equal_topk_sharpe(
        top_k: int = 5,
        upper: float = 1.0,
        lower: float = -1.0,
        rolling_window: int = 60,
    ) -> CombinerBuilder:
        """工厂：用历史 Sharpe Top K 等权（不按 Sharpe 加权，只用排名筛因子）.

        与 ``builder_sharpe_topk`` 的区别：选出 Top K 后内部等权，
        避免单个因子 Sharpe 极端值主导权重.
        """
        def _builder(
            analyzer: SingleStockAnalyzer,
            feature_cols: list[str],
        ) -> MultiFactorCombiner:
            scan_table = analyzer.scan_factors(
                feature_cols, upper=upper, lower=lower,
                rolling_window=rolling_window,
            )
            top_factors = scan_table.dropna(subset=["sharpe"]).head(top_k)["factor"].tolist()
            if not top_factors:
                raise ValueError("历史窗口 scan_factors 无有效 Top K")
            return MultiFactorCombiner.equal_weighted(top_factors)
        return _builder
