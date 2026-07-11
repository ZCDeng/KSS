"""五维 GO/NO-GO 门禁裁决：经济意义 / 稳健 / 可交易 / 可解释 / 运维.

对齐 ``.claude/skills/kss-indicator-pipeline/SKILL.md`` P2 五维表。每维由代码给出
数值与布尔结论；Seesaw 只对已算好的结构化 verdict 做叙事，不参与裁决本身（KTD7）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from kss.backtest.metrics import Metrics
from kss.indicators.primitives import param_grid
from kss.indicators.rules import IndicatorSpec, compute_positions, extract_trades, rule_sentence, warm_period

MIN_TRADES = 3
MAX_TRADES_PER_YEAR = 60.0
SLIPPAGE_BPS = 30.0
# 网格里除最优组合外，至少还要有几组同样正收益——避免"唯一一组参数吊在天花板"型过拟合
NEIGHBOR_POSITIVE_MIN = 1


@dataclass
class DimensionVerdict:
    """单一维度的裁决：布尔结论 + 支撑数值 + 一句话说明。"""

    name: str
    passed: bool
    value: dict[str, Any]
    detail: str


@dataclass
class GateVerdict:
    """五维汇总裁决。"""

    go: bool
    dimensions: list[DimensionVerdict] = field(default_factory=list)

    def failed(self) -> list[DimensionVerdict]:
        return [d for d in self.dimensions if not d.passed]


def _strategy_returns(feat: pd.DataFrame, warm: int) -> pd.Series:
    pos = feat["position"].iloc[warm:]
    ret = feat["ret"].iloc[warm:]
    return (pos * ret).dropna()


def _total_return(returns: pd.Series) -> float:
    if returns.empty:
        return 0.0
    m = Metrics.calc(returns)
    return float(m.get("total", 0.0) or 0.0) if m else 0.0


def _dim_economic(feat: pd.DataFrame, warm: int) -> DimensionVerdict:
    """经济意义：策略本身非负收益且夏普为正（相对 buy&hold 的对照仅供参考，不作为硬约束——
    多头趋势策略在单边流畅上涨行情中本就跑输满仓 buy&hold，这不代表策略无效）。"""
    strat_ret = _strategy_returns(feat, warm)
    bh_ret = feat["ret"].iloc[warm:].dropna()
    strat_m = Metrics.calc(strat_ret) or {}
    bh_m = Metrics.calc(bh_ret) or {}
    strat_total = float(strat_m.get("total", 0.0) or 0.0)
    strat_sharpe = float(strat_m.get("sharpe", 0.0) or 0.0)
    bh_total = float(bh_m.get("total", 0.0) or 0.0)
    passed = strat_total > 0 and strat_sharpe > 0
    return DimensionVerdict(
        name="经济意义",
        passed=passed,
        value={
            "strategy_total": round(strat_total, 4),
            "strategy_sharpe": round(strat_sharpe, 3),
            "buy_and_hold_total": round(bh_total, 4),
        },
        detail=f"策略总收益 {strat_total:.2%}（夏普 {strat_sharpe:.2f}）vs buy&hold {bh_total:.2%}",
    )


def _dim_robust(
    df: pd.DataFrame,
    family: str,
    best_params: dict[str, Any],
    *,
    grid: list[dict[str, Any]] | None = None,
) -> DimensionVerdict:
    """稳健：相邻参数点收益不塌方——网格里除最优组合外还有其它组合同样正收益。"""
    g = grid if grid is not None else param_grid(family)
    totals: list[float] = []
    best_total: float | None = None
    for params in g:
        spec = IndicatorSpec(family, params)
        feat = compute_positions(df, spec)
        total = _total_return(_strategy_returns(feat, warm_period(spec)))
        totals.append(total)
        if params == best_params:
            best_total = total
    if best_total is None:
        spec = IndicatorSpec(family, best_params)
        feat = compute_positions(df, spec)
        best_total = _total_return(_strategy_returns(feat, warm_period(spec)))

    positive_count = sum(1 for t in totals if t > 0)
    others_positive = max(positive_count - (1 if best_total > 0 else 0), 0)
    passed = best_total > 0 and others_positive >= NEIGHBOR_POSITIVE_MIN
    return DimensionVerdict(
        name="稳健",
        passed=passed,
        value={
            "best_total": round(best_total, 4),
            "other_positive_combos": others_positive,
            "grid_size": len(g),
        },
        detail=f"网格 {len(g)} 组合中，除最优组外还有 {others_positive} 组同样正收益",
    )


def _dim_tradeable(feat: pd.DataFrame, trades: list[dict[str, Any]]) -> DimensionVerdict:
    """可交易：交易次数在合理区间，且滑点后均笔收益仍为正。"""
    n = len(trades)
    if n == 0:
        return DimensionVerdict(
            name="可交易", passed=False, value={"trade_count": 0}, detail="交易次数为 0，无法评估"
        )
    years = max(len(feat) / 252.0, 0.25)
    per_year = n / years
    slip = SLIPPAGE_BPS / 10000.0 * 2  # 往返滑点
    net_avg = sum((t.get("trade_return") or 0.0) - slip for t in trades) / n
    passed = MIN_TRADES <= n and per_year <= MAX_TRADES_PER_YEAR and net_avg > 0
    return DimensionVerdict(
        name="可交易",
        passed=passed,
        value={
            "trade_count": n,
            "per_year": round(per_year, 1),
            "net_avg_after_slippage": round(net_avg, 4),
        },
        detail=f"共 {n} 笔交易（约 {per_year:.1f} 笔/年），滑点后均笔收益 {net_avg:.2%}",
    )


def _dim_interpretable(spec: IndicatorSpec) -> DimensionVerdict:
    """可解释：规则能一句话说清——三族基元均为模板化命名规则，结构性达标。"""
    sentence = rule_sentence(spec)
    return DimensionVerdict(
        name="可解释", passed=True, value={"rule_sentence": sentence}, detail=sentence
    )


def _dim_operational(spec: IndicatorSpec) -> DimensionVerdict:
    """运维：能否日终批跑——通用引擎无状态、单票失败不拖垮整池，结构性达标。"""
    return DimensionVerdict(
        name="运维",
        passed=True,
        value={"batchable": True},
        detail="复用通用回测引擎与固定基元族，可日终批跑，单票失败不拖垮整池",
    )


def judge(
    df: pd.DataFrame,
    spec: IndicatorSpec,
    *,
    grid: list[dict[str, Any]] | None = None,
) -> GateVerdict:
    """对一个候选（基元族 + 最终参数）跑五维裁决。"""
    warm = warm_period(spec)
    feat = compute_positions(df, spec)
    trades = extract_trades(feat, feat["position"])

    dimensions = [
        _dim_economic(feat, warm),
        _dim_robust(df, spec.family, spec.params, grid=grid),
        _dim_tradeable(feat, trades),
        _dim_interpretable(spec),
        _dim_operational(spec),
    ]
    return GateVerdict(go=all(d.passed for d in dimensions), dimensions=dimensions)
