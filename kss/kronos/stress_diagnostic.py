"""合成压测诊断（U7）—— 在已知零结构合成路径上量化闸门假阳性率.

合成数据由 U6 生成、过微结构校验后进来。它们由模型续写真实历史采样而成，
**不含真实 alpha**（零结构）。把候选策略在这些路径上跑现有横截面回测
（:func:`factor_cross_section_backtest`）+ 上线闸门（:meth:`Significance.is_deployable`），
统计闸门误判 deployable 的比例 = **假阳性率**。

定位（``Covers AE3``）：输出是诊断报告，**不**作阻断闸门——不阻止任何策略上线，
只量化现有闸门在零结构数据上被骗的概率。

隔离（``Covers R13``）：合成数据严格只压测——本模块不 import 任何训练 / 实盘信号 /
预测落库路径，不写任何持久状态。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Literal

import pandas as pd

from kss.backtest.cost_model import CostModel
from kss.backtest.cross_section import factor_cross_section_backtest
from kss.backtest.significance import Significance, StrategyFamily

logger = logging.getLogger(__name__)


def synth_frames_to_panel(
    frames: list[tuple[str, pd.DataFrame]],
    *,
    factor_col: str = "factor",
) -> pd.DataFrame:
    """把一批合成 K 线装配成横截面回测面板（factor + next_day_return）.

    因子用 1 日动量（当日收益），标签用次日收益——零结构数据上该因子无预测力，
    闸门理应拒绝；判 deployable 即假阳性。

    Args:
        frames: ``[(symbol, 合成 OHLCV（含 close/trade_date）), ...]``.
        factor_col: 输出因子列名.

    Returns:
        含 ``trade_date / symbol / {factor_col} / next_day_return`` 的面板.
    """
    rows: list[pd.DataFrame] = []
    for symbol, df in frames:
        d = df.sort_values("trade_date").reset_index(drop=True)
        ret = d["close"].pct_change()
        rec = pd.DataFrame({
            "trade_date": pd.to_datetime(d["trade_date"]),
            "symbol": str(symbol),
            factor_col: ret,                       # 当日收益（动量因子）
            "next_day_return": ret.shift(-1),      # 次日收益（标签）
        })
        rows.append(rec.dropna(subset=[factor_col, "next_day_return"]))
    if not rows:
        return pd.DataFrame(columns=["trade_date", "symbol", factor_col, "next_day_return"])
    return pd.concat(rows, ignore_index=True)


def run_gate_on_panel(
    panel: pd.DataFrame,
    *,
    cost_model: CostModel | None = None,
    factor_col: str = "factor",
    direction: Literal["long_high", "long_low"] = "long_high",
    strategy_family: StrategyFamily = "mined",
    min_stocks: int = 3,
    top_pct: float = 0.3,
) -> dict | None:
    """在一个合成面板上跑横截面回测 + 上线闸门，返回判定明细.

    Returns:
        ``is_deployable(return_details=True)`` 的 dict（含 ``passed``）；
        无有效回测结果时返回 ``None``.
    """
    cost_model = cost_model or CostModel()
    res = factor_cross_section_backtest(
        panel, factor_col=factor_col, cost_model=cost_model,
        top_pct=top_pct, direction=direction, min_stocks=min_stocks,
    )
    if res is None or res.empty or "net_return" not in res.columns:
        return None
    returns = res["net_return"].dropna()
    if len(returns) < 2:
        return None
    return Significance.is_deployable(
        returns, strategy_family=strategy_family, return_details=True
    )


@dataclass
class StressDiagnosticReport:
    """合成压测诊断报告（非阻断闸门，``Covers AE3``）."""

    is_blocking: bool = False  # 永远 False —— 这是诊断不是闸门
    strategy_family: str = "mined"
    n_trials: int = 0           # 有效（过微结构校验且可回测）的合成 trial 数
    n_deployable: int = 0       # 闸门误判 deployable 的次数
    false_positive_rate: float | None = None
    note: str = ""
    per_trial: list[dict] = field(default_factory=list)


def diagnose(
    valid_trials: list[list[tuple[str, pd.DataFrame]]],
    *,
    cost_model: CostModel | None = None,
    strategy_family: StrategyFamily = "mined",
    direction: Literal["long_high", "long_low"] = "long_high",
    min_stocks: int = 3,
) -> StressDiagnosticReport:
    """对多批通过校验的合成路径产出假阳性率诊断.

    Args:
        valid_trials: 已过 U6 微结构校验的合成批列表；每个元素是一批
            ``[(symbol, frame), ...]`` 当作一个独立 trial.
        cost_model: 成本模型.
        strategy_family: 闸门用的策略族（mined 最严）.
        direction / min_stocks: 回测参数.

    Returns:
        :class:`StressDiagnosticReport`；无有效 trial → ``note="无有效样本"``、
        ``false_positive_rate=None``（不给误导数字，``Covers edge``）.
    """
    per_trial: list[dict] = []
    n_deployable = 0
    for i, frames in enumerate(valid_trials):
        panel = synth_frames_to_panel(frames)
        verdict = run_gate_on_panel(
            panel, cost_model=cost_model, strategy_family=strategy_family,
            direction=direction, min_stocks=min_stocks,
        )
        if verdict is None:
            continue
        deployable = bool(verdict["passed"])
        n_deployable += int(deployable)
        per_trial.append({
            "trial": i, "deployable": deployable,
            "sharpe": verdict.get("sharpe"), "dsr": verdict.get("dsr"),
            "n": verdict.get("n"),
        })

    n_trials = len(per_trial)
    if n_trials == 0:
        return StressDiagnosticReport(
            strategy_family=strategy_family, n_trials=0, n_deployable=0,
            false_positive_rate=None, note="无有效样本",
        )
    fpr = n_deployable / n_trials
    logger.info(
        "压测诊断：family=%s trials=%d deployable=%d → FPR=%.3f",
        strategy_family, n_trials, n_deployable, fpr,
    )
    return StressDiagnosticReport(
        strategy_family=strategy_family, n_trials=n_trials, n_deployable=n_deployable,
        false_positive_rate=fpr,
        note=f"零结构合成数据上 {strategy_family} 闸门假阳性率（诊断，非阻断）",
        per_trial=per_trial,
    )
