"""因子横截面风格策略基类 —— 纯因子排序 Top-N，不依赖 LightGBM.

用于推荐页风格对照（plan 2026-07-31-003）。每风格声明 factor 列、排序方向、
出处标签；``generate_signals`` 出日更名单，``backtest`` 做等权 Top-N 日收益序列，
``evaluate_gate`` 调用 :meth:`Significance.is_deployable` 仅作研究标签。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np
import pandas as pd

from kss.backtest.cost_model import CostModel
from kss.backtest.significance import Significance, StrategyFamily
from kss.models.base import ModelBase
from kss.strategies.base import StrategyBase

SortDirection = Literal["asc", "desc"]


@dataclass(frozen=True)
class StyleMeta:
    """风格元数据（展示与出处）."""

    style_id: str
    name: str
    factor_col: str
    direction: SortDirection
    source_tags: tuple[str, ...]
    reason_template: str  # 可用 {factor_col} {factor_value} {rank_position}
    strategy_family: StrategyFamily = "single_factor"


@dataclass
class GateResult:
    """门禁评估结果（研究标签，不决定是否展示）."""

    deployable: bool
    label: str  # "passed" | "research_blocked"
    metrics: dict[str, Any] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)


class FactorRankStyleStrategy(StrategyBase):
    """纯因子横截面排序风格策略.

    ``model`` / ``feature_cols`` 在接口上保留以兼容 :class:`StrategyBase`，
    纯因子路径可忽略 model（允许 ``None`` 经 kwargs 或传入占位）。
    """

    meta: StyleMeta

    def __init__(
        self,
        meta: StyleMeta,
        *,
        top_n: int = 5,
        cost_model: CostModel | None = None,
        return_col: str = "next_open_ret",
    ) -> None:
        if top_n < 1:
            raise ValueError(f"top_n 必须 >= 1，收到 {top_n}")
        self.meta = meta
        self.top_n = top_n
        self.cost_model = cost_model or CostModel()
        self.return_col = return_col

    # ------------------------------------------------------------------ #
    # 信号
    # ------------------------------------------------------------------ #

    def generate_signals(
        self,
        factor_df: pd.DataFrame,
        feature_cols: list[str] | None = None,
        model: ModelBase | None = None,
        date: str | pd.Timestamp | None = None,
        **kwargs: Any,
    ) -> pd.DataFrame:
        """单日因子排序取 Top-N.

        Returns:
            含 symbol / factor_value / rank_pct / rank_position / planned_weight /
            signal / selection_reason / style_id 的 DataFrame。
        """
        _ = feature_cols, model  # StrategyBase 兼容；纯因子路径不用
        day_df = self._slice_day(factor_df, date)
        ranked = self._rank_day(day_df)
        if ranked.empty:
            raise ValueError(
                f"风格 {self.meta.style_id}: 日期切片后无可用因子 "
                f"{self.meta.factor_col!r}"
            )
        top = ranked.head(self.top_n).copy()
        n = len(top)
        w = 1.0 / n if n else 0.0
        top["planned_weight"] = w
        top["signal"] = "buy"
        top["style_id"] = self.meta.style_id
        top["selection_reason"] = [
            self.meta.reason_template.format(
                factor_col=self.meta.factor_col,
                factor_value=float(row["factor_value"]),
                rank_position=int(row["rank_position"]),
                style_name=self.meta.name,
            )
            for _, row in top.iterrows()
        ]
        return top.reset_index(drop=True)

    def _slice_day(
        self, factor_df: pd.DataFrame, date: str | pd.Timestamp | None
    ) -> pd.DataFrame:
        if "trade_date" not in factor_df.columns or "symbol" not in factor_df.columns:
            raise KeyError("factor_df 需含 trade_date / symbol 列")
        if self.meta.factor_col not in factor_df.columns:
            raise KeyError(
                f"factor_df 缺少因子列 {self.meta.factor_col!r} "
                f"（风格 {self.meta.style_id}）"
            )
        df = factor_df.copy()
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        if date is None:
            target = df["trade_date"].max()
        else:
            target = pd.Timestamp(date)
        day = df[df["trade_date"] == target].copy()
        if day.empty:
            raise ValueError(f"指定日期 {target.date()} 无可用数据")
        return day

    def _rank_day(self, day_df: pd.DataFrame) -> pd.DataFrame:
        col = self.meta.factor_col
        work = day_df.dropna(subset=[col]).copy()
        if work.empty:
            return work
        ascending = self.meta.direction == "asc"
        work = work.sort_values(col, ascending=ascending, kind="mergesort")
        work["factor_value"] = work[col].astype(float)
        # rank_pct 越高越好（与横截面 Top-Pct 语义一致）
        work["rank_pct"] = work[col].rank(
            pct=True, ascending=not ascending
        )
        work["rank_position"] = np.arange(1, len(work) + 1)
        return work

    # ------------------------------------------------------------------ #
    # 回测：等权 Top-N 组合日收益（需面板含 return_col）
    # ------------------------------------------------------------------ #

    def backtest(
        self,
        factor_df: pd.DataFrame,
        feature_cols: list[str] | None = None,
        model: ModelBase | None = None,
        **kwargs: Any,
    ) -> pd.DataFrame:
        """按日取 Top-N 等权，用 ``return_col`` 拼组合日收益.

        Returns:
            列含 trade_date / portfolio_return / n_names 的 DataFrame。
        """
        _ = feature_cols, model
        return_col = kwargs.get("return_col", self.return_col)
        if return_col not in factor_df.columns:
            raise KeyError(
                f"backtest 需要收益列 {return_col!r}；"
                "请在面板上预计算 next open→open 或 close→close 收益"
            )
        df = factor_df.copy()
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        cost = self.cost_model
        # 简化：每日全换手近似双边成本（研究对照用，非精细仿真）
        turn_cost = float(cost.buy_cost + cost.sell_cost)

        records: list[dict[str, Any]] = []
        for dt, day in df.groupby("trade_date", sort=True):
            try:
                ranked = self._rank_day(day)
            except Exception:  # noqa: BLE001 — 单日失败跳过
                continue
            if ranked.empty:
                continue
            top = ranked.head(self.top_n)
            rets = top[return_col].dropna()
            if rets.empty:
                continue
            gross = float(rets.mean())
            net = gross - turn_cost
            records.append(
                {
                    "trade_date": pd.Timestamp(dt),
                    "portfolio_return": net,
                    "gross_return": gross,
                    "n_names": int(len(rets)),
                    "style_id": self.meta.style_id,
                }
            )
        if not records:
            return pd.DataFrame(
                columns=[
                    "trade_date",
                    "portfolio_return",
                    "gross_return",
                    "n_names",
                    "style_id",
                ]
            )
        return pd.DataFrame(records)

    def evaluate_gate(
        self,
        returns: pd.Series,
        *,
        strategy_family: StrategyFamily | None = None,
    ) -> GateResult:
        """对净收益序列做 is_deployable，产出研究标签."""

        family = strategy_family or self.meta.strategy_family
        details = Significance.is_deployable(
            returns.dropna(),
            strategy_family=family,
            return_details=True,
        )
        assert isinstance(details, dict)
        ok = bool(details.get("passed", False))
        failures = [str(f) for f in (details.get("failures") or [])]
        label = "passed" if ok else "research_blocked"
        return GateResult(
            deployable=ok,
            label=label,
            metrics={
                k: v
                for k, v in details.items()
                if k not in ("failures", "passed")
            },
            failures=failures,
        )

    def to_public_meta(self) -> dict[str, Any]:
        """序列化供快照 / bridge 使用."""

        return {
            "style_id": self.meta.style_id,
            "name": self.meta.name,
            "factor_col": self.meta.factor_col,
            "direction": self.meta.direction,
            "source_tags": list(self.meta.source_tags),
            "top_n": self.top_n,
            "strategy_family": self.meta.strategy_family,
        }
