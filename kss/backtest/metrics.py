"""回测绩效指标计算."""

from __future__ import annotations

import warnings
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats


# 年化标度（A 股一年约 252 个交易日）
_TRADING_DAYS = 252


class Metrics:
    """策略绩效评估指标集合.

    基于日度收益序列计算总收益、年化收益、夏普比率、最大回撤、
    Calmar 比率、胜率、Sortino、下行波动率、偏度/峰度等指标.
    """

    @staticmethod
    def calc(r: pd.Series) -> dict[str, Any]:
        """计算绩效指标.

        Args:
            r: 日度收益率序列（小数形式，如 0.01 表示 1%）.

        Returns:
            包含以下键的字典：

            - ``total``        : 区间总收益率
            - ``annual``       : 年化收益率
            - ``sharpe``       : 夏普比率（假设无风险利率为 0）
            - ``max_dd``       : 最大回撤（负数）
            - ``calmar``       : Calmar 比率 = 年化收益 / |最大回撤|
            - ``win``          : 胜率（日度收益 > 0 的占比）
            - ``n``            : 有效交易日数
            - ``avg_daily``    : 日均收益
            - ``volatility``   : 年化波动率
            - ``downside_dev`` : 年化下行波动率（仅负收益）
            - ``sortino``      : Sortino 比率（年化收益 / 下行波动率）
            - ``skew``         : 收益序列偏度（fisher=True，正常分布为 0）
            - ``kurt``         : 收益序列超额峰度（正常分布为 0）
        """
        r = r.dropna()
        if len(r) == 0:
            return {}

        cum = (1 + r).cumprod()
        total = cum.iloc[-1] - 1
        n_days = len(r)

        # 年化（按 252 个交易日）
        annual = (1 + total) ** (_TRADING_DAYS / n_days) - 1 if total > -1 else -1.0
        vol = r.std() * np.sqrt(_TRADING_DAYS)
        sharpe = annual / vol if vol > 0 else 0.0

        # 最大回撤
        running_max = cum.cummax()
        dd = (cum - running_max) / running_max
        max_dd = dd.min()

        calmar = annual / abs(max_dd) if max_dd != 0 else 0.0

        # 下行波动率：仅取负收益（不是 < threshold；threshold=0）
        downside = r[r < 0]
        if len(downside) > 1:
            downside_dev = downside.std() * np.sqrt(_TRADING_DAYS)
        else:
            downside_dev = 0.0
        sortino = annual / downside_dev if downside_dev > 0 else 0.0

        # 偏度/峰度 —— 用 scipy 保证与 Deflated Sharpe 公式同源
        # 全等收益序列时 scipy 触发 "precision loss" warning；用 1e-12 容差判退化.
        sample_std = float(r.std())
        is_degenerate = sample_std < 1e-12
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            if n_days >= 3 and not is_degenerate:
                skew = float(stats.skew(r.values, bias=False))
            else:
                skew = 0.0
            if n_days >= 4 and not is_degenerate:
                # fisher=True：正常分布 kurt=0；与 LdP DSR 公式约定一致
                kurt = float(stats.kurtosis(r.values, fisher=True, bias=False))
            else:
                kurt = 0.0
        # 若 scipy 仍因 NaN 返回 NaN，退化到 0
        if not np.isfinite(skew):
            skew = 0.0
        if not np.isfinite(kurt):
            kurt = 0.0

        return {
            "total": total,
            "annual": annual,
            "sharpe": sharpe,
            "max_dd": max_dd,
            "calmar": calmar,
            "win": (r > 0).mean(),
            "n": n_days,
            "avg_daily": r.mean(),
            "volatility": vol,
            "downside_dev": downside_dev,
            "sortino": sortino,
            "skew": skew,
            "kurt": kurt,
        }

    @staticmethod
    def format(metrics_dict: dict[str, Any]) -> str:
        """将指标字典格式化为人类可读字符串（核心指标 - 兼容旧调用）.

        Args:
            metrics_dict: :meth:`calc` 返回的指标字典.

        Returns:
            类似 ``"总:108.8% 年化:54.6% 夏普:1.55 回撤:-12.3% 胜率:58.2%"`` 的字符串.
        """
        if not metrics_dict:
            return "无有效数据"
        return (
            f"总:{metrics_dict['total']*100:.1f}% "
            f"年化:{metrics_dict['annual']*100:.1f}% "
            f"夏普:{metrics_dict['sharpe']:.2f} "
            f"回撤:{metrics_dict['max_dd']*100:.1f}% "
            f"Calmar:{metrics_dict['calmar']:.2f} "
            f"胜率:{metrics_dict['win']*100:.1f}% "
            f"天数:{metrics_dict['n']}"
        )

    @staticmethod
    def format_full(metrics_dict: dict[str, Any]) -> str:
        """完整指标格式化（含 Sortino / 下行波动率 / 偏峰度）.

        Args:
            metrics_dict: :meth:`calc` 返回的指标字典.

        Returns:
            包含核心指标与扩展指标的多行字符串.
        """
        if not metrics_dict:
            return "无有效数据"
        return (
            f"{Metrics.format(metrics_dict)}\n"
            f"Sortino:{metrics_dict.get('sortino', 0.0):.2f} "
            f"下行波动:{metrics_dict.get('downside_dev', 0.0)*100:.1f}% "
            f"偏度:{metrics_dict.get('skew', 0.0):.2f} "
            f"峰度:{metrics_dict.get('kurt', 0.0):.2f}"
        )
