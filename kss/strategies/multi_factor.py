"""多因子线性加权组合器 —— z-score 合成版.

与 :class:`~kss.strategies.ensemble.MultiPeriodEnsemble` 的区别：

- ``MultiPeriodEnsemble`` 输入是各 horizon 的 rank-pct 打分，输出 rank-pct 融合，
  适合 daily_forecast 中"哪只股票更值得买"的截面排序问题.
- ``MultiFactorCombiner`` 输入是各因子的 rolling z-score，输出合成 z-score，
  适合单股票"现在应不应该持仓"的时序阈值问题——直接喂给
  :meth:`~kss.backtest.single_stock.SingleStockAnalyzer.multi_factor_backtest`.

设计要点：

- **支持负权重**：``weights={"pb": -0.5}`` 表示 pb 是反向因子（IC<0），合成时自动 flip.
- **归一化**：默认 ``normalize=True`` 把 Σ|w| 归一到 1，保证合成 z-score
  与单因子等阶可比（阈值 ±1 不需要重调）.
- **工厂方法**：``from_scan_table`` / ``from_ic_table`` 把
  :class:`SingleStockAnalyzer` 已有的诊断输出直接转成权重，零样板.
"""

from __future__ import annotations

from typing import Mapping, Sequence

import numpy as np
import pandas as pd


class MultiFactorCombiner:
    """多因子线性加权组合器（z-score 合成）.

    Attributes:
        weights: ``{因子名: 权重}``；权重可正可负，0 权重的因子会被滤掉.
    """

    def __init__(
        self,
        weights: Mapping[str, float],
        normalize: bool = True,
    ) -> None:
        """初始化.

        Args:
            weights: 因子权重映射；权重为 0 或 NaN 的条目会被丢弃.
            normalize: 是否按 ``Σ |权重|`` 归一化到 1（默认 True）.

        Raises:
            ValueError: 权重为空或所有权重之和的绝对值为 0.
        """
        cleaned: dict[str, float] = {}
        for k, v in weights.items():
            f = float(v)
            if not np.isfinite(f) or f == 0.0:
                continue
            cleaned[str(k)] = f
        if not cleaned:
            raise ValueError("weights 不能为空（全 0 / NaN 的权重会被丢弃）")
        if normalize:
            total = sum(abs(v) for v in cleaned.values())
            if total <= 0:
                raise ValueError("权重 |绝对值总和| 必须 > 0")
            cleaned = {k: v / total for k, v in cleaned.items()}
        self.weights: dict[str, float] = cleaned

    # ------------------------------------------------------------------ #
    # 工厂方法
    # ------------------------------------------------------------------ #

    @classmethod
    def equal_weighted(cls, factors: Sequence[str]) -> "MultiFactorCombiner":
        """等权组合.

        Args:
            factors: 因子名列表.

        Returns:
            归一化后每个因子权重 = ``1 / len(factors)``.
        """
        if not factors:
            raise ValueError("factors 不能为空")
        w = 1.0 / len(factors)
        return cls({f: w for f in factors}, normalize=False)

    @classmethod
    def from_scan_table(
        cls,
        scan_table: pd.DataFrame,
        top_k: int = 5,
        weight_col: str = "sharpe",
        clip_negative: bool = True,
    ) -> "MultiFactorCombiner":
        """按 ``SingleStockAnalyzer.scan_factors`` 输出表加权.

        Args:
            scan_table: 含 ``factor`` 列和 ``weight_col`` 列的 DataFrame
                （:meth:`SingleStockAnalyzer.scan_factors` 输出）.
            top_k: 取 ``weight_col`` 降序前 K 个.
            weight_col: 权重来源列，默认 ``"sharpe"``；也可用 ``"annual"``.
            clip_negative: ``True`` 时把负权重设为 0（即"这些因子不参与组合"），
                适合 ``weight_col="sharpe"`` 且只想做多胜率组合的情形.

        Returns:
            归一化后的 combiner.

        Raises:
            ValueError: 列缺失或 top_k 之后无有效权重.
        """
        if "factor" not in scan_table.columns or weight_col not in scan_table.columns:
            raise ValueError(f"scan_table 缺少必需列 factor / {weight_col}")
        top = scan_table.dropna(subset=[weight_col]).head(top_k)
        weights: dict[str, float] = {}
        for _, row in top.iterrows():
            v = float(row[weight_col])
            if clip_negative and v < 0:
                continue
            weights[str(row["factor"])] = v
        if not weights:
            raise ValueError(
                f"scan_table top_{top_k} 中 {weight_col} 列无 > 0 的有效权重"
            )
        return cls(weights, normalize=True)

    @classmethod
    def from_ic_table(
        cls,
        ic_table: pd.DataFrame,
        horizon: str = "future_return_5d",
        top_k: int = 5,
        signed: bool = True,
    ) -> "MultiFactorCombiner":
        """按 ``SingleStockAnalyzer.time_series_ic`` 输出表加权.

        Args:
            ic_table: 含 ``factor`` / ``horizon`` / ``ic`` 列的 DataFrame.
            horizon: 选择哪个 horizon 的 IC 作为权重（默认 ``future_return_5d``）.
            top_k: 取 ``|IC|`` 降序前 K 个.
            signed: ``True`` 时权重保留 IC 符号（反向因子得负权重，合成时自动 flip）；
                ``False`` 时只取 ``|IC|``（需调用方保证因子方向已对齐）.

        Returns:
            归一化后的 combiner.
        """
        if "factor" not in ic_table.columns or "ic" not in ic_table.columns:
            raise ValueError("ic_table 缺少必需列 factor / ic")
        sub = ic_table.copy()
        if "horizon" in sub.columns:
            sub = sub[sub["horizon"] == horizon]
        if sub.empty:
            raise ValueError(f"ic_table 中无 horizon={horizon!r} 的行")
        sub = sub.dropna(subset=["ic"])
        sub = sub.assign(abs_ic=sub["ic"].abs()).sort_values("abs_ic", ascending=False)
        top = sub.head(top_k)
        weights: dict[str, float] = {}
        for _, row in top.iterrows():
            ic = float(row["ic"])
            if not np.isfinite(ic) or ic == 0:
                continue
            weights[str(row["factor"])] = ic if signed else abs(ic)
        if not weights:
            raise ValueError(f"ic_table top_{top_k} 无有效 IC")
        return cls(weights, normalize=True)

    # ------------------------------------------------------------------ #
    # 操作
    # ------------------------------------------------------------------ #

    def restrict_to(self, allowed: Sequence[str]) -> "MultiFactorCombiner":
        """筛选权重，仅保留 ``allowed`` 中的因子，重新归一化.

        用于"|IC| 加权但只取与 Sharpe Top K 交集"等组合策略.

        Args:
            allowed: 允许保留的因子名集合.

        Returns:
            新的 combiner（不修改 self）.
        """
        allowed_set = set(allowed)
        kept = {k: v for k, v in self.weights.items() if k in allowed_set}
        if not kept:
            raise ValueError(f"restrict_to 后无任何因子留存；allowed={list(allowed)}")
        return MultiFactorCombiner(kept, normalize=True)

    @property
    def factors(self) -> list[str]:
        """权重非零的因子名列表（按权重降序）."""
        return [
            k for k, _ in sorted(
                self.weights.items(), key=lambda x: abs(x[1]), reverse=True,
            )
        ]

    def combine_zscores(self, z_dict: Mapping[str, pd.Series]) -> pd.Series:
        """加权合成 z-score 序列.

        合成公式：``combined_z[t] = Σ w_i × z_i[t]``，缺失因子的 z 用 0（中性）填充.

        Args:
            z_dict: 因子名 → rolling z-score 序列（``pd.Series``）.

        Returns:
            合成后的 z-score Series（命名为 ``combined_z``）.

        Raises:
            ValueError: 提供的 z_dict 与 combiner 因子无任何交集.
        """
        used = {k: z_dict[k] for k in self.weights if k in z_dict}
        if not used:
            raise ValueError(
                f"z_dict 与 combiner.factors 无交集；combiner.factors={self.factors}"
            )
        # 对齐 Index：用第一条序列的 Index 作为参考
        ref_index = next(iter(used.values())).index
        combined = pd.Series(0.0, index=ref_index, name="combined_z")
        for f, z in used.items():
            z_aligned = z.reindex(ref_index).fillna(0.0)
            combined = combined + self.weights[f] * z_aligned
        return combined

    # ------------------------------------------------------------------ #
    # repr / 调试
    # ------------------------------------------------------------------ #

    def __repr__(self) -> str:
        items = ", ".join(f"{k}={v:+.3f}" for k, v in self.weights.items())
        return f"MultiFactorCombiner({items})"

    def __len__(self) -> int:
        return len(self.weights)
