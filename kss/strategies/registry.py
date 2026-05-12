"""策略注册中心 —— 上线前强制走 is_deployable 门槛.

每一个想"上线"（接入实盘 / 推送实盘信号）的策略，必须先通过 :class:`StrategyRegistry`
的 :meth:`register` 注册。注册时会调用 :meth:`Significance.is_deployable`
做硬性门槛检查（Sharpe / p-value / Deflated Sharpe），未通过则抛
:class:`DeploymentBlockedError`，阻止"高 Sharpe 实为事后偏差"的策略误上线。

用例::

    from kss.strategies.registry import StrategyRegistry, DeploymentBlockedError

    registry = StrategyRegistry()
    try:
        registry.register("log_mv_reverse", net_returns, n_trials=5)
    except DeploymentBlockedError as exc:
        print(exc.failures, exc.metrics)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pandas as pd

from kss.backtest.significance import Significance, StrategyFamily

logger = logging.getLogger(__name__)


@dataclass
class RegisteredStrategy:
    """注册成功的策略记录."""

    name: str
    registered_at: datetime
    metrics: dict[str, Any] = field(default_factory=dict)
    notes: str = ""


class DeploymentBlockedError(RuntimeError):
    """策略未通过 :meth:`Significance.is_deployable` 门槛."""

    def __init__(
        self,
        name: str,
        failures: list[str],
        metrics: dict[str, Any],
    ) -> None:
        self.name = name
        self.failures = failures
        self.metrics = metrics
        super().__init__(
            f"策略 {name!r} 未通过上线门槛: {failures}; metrics={metrics}"
        )


class StrategyRegistry:
    """策略注册中心.

    上线前必须调 :meth:`register` 检查 DSR / Sharpe / p-value 三道门槛.
    用例::

        registry = StrategyRegistry()
        registry.register("log_mv_reverse", net_returns, n_trials=5)

    Raises:
        DeploymentBlockedError: 策略未通过门槛.
    """

    def __init__(
        self,
        dsr_threshold: float = 0.4,
        pvalue_threshold: float = 0.05,
        min_sharpe: float = 0.5,
        default_n_trials: int = 10,
    ) -> None:
        """初始化注册中心.

        Args:
            dsr_threshold: DSR 最小阈值. 默认 ``0.4``（LdP 建议"上线最低"）.
                旧版 ``0.7`` 实证下连科创板 size factor 都过不去，过严.
            pvalue_threshold: p 值上限.
            min_sharpe: 最低 Sharpe.
            default_n_trials: 未指定 ``n_trials`` / ``strategy_family`` 时的默认 trials.
        """
        self.dsr_threshold = dsr_threshold
        self.pvalue_threshold = pvalue_threshold
        self.min_sharpe = min_sharpe
        self.default_n_trials = default_n_trials
        self._registered: dict[str, RegisteredStrategy] = {}

    # ------------------------------------------------------------------ #
    # 注册
    # ------------------------------------------------------------------ #

    def register(
        self,
        name: str,
        returns: pd.Series,
        n_trials: int | None = None,
        strategy_family: StrategyFamily | None = None,
        notes: str = "",
    ) -> RegisteredStrategy:
        """注册策略；未过门槛抛 :class:`DeploymentBlockedError`.

        Args:
            name: 策略名（同名二次注册会被覆盖，并打 warning log）.
            returns: 日度净收益序列.
            n_trials: 用于 DSR 的回测试验次数；显式优先级最高.
            strategy_family: ``"prior"`` / ``"single_factor"`` / ``"small_grid"`` /
                ``"tuned"`` / ``"mined"``，决定默认 trials（详见
                :meth:`Significance.is_deployable`）. 未提供则用 ``default_n_trials``.
            notes: 备注（写进 :class:`RegisteredStrategy.notes`）.

        Returns:
            :class:`RegisteredStrategy`：注册成功的记录.

        Raises:
            DeploymentBlockedError: 任一门槛未通过.
        """
        # 优先级：显式 n_trials > strategy_family > self.default_n_trials
        if n_trials is None and strategy_family is None:
            trials: int | None = self.default_n_trials
        else:
            trials = n_trials  # 可能为 None，让 is_deployable 走 strategy_family 推断
        details = Significance.is_deployable(
            returns,
            dsr_threshold=self.dsr_threshold,
            pvalue_threshold=self.pvalue_threshold,
            min_sharpe=self.min_sharpe,
            n_trials=trials,
            strategy_family=strategy_family,
            return_details=True,
        )
        assert isinstance(details, dict)  # return_details=True 必返回 dict

        metrics = {
            "sharpe": details["sharpe"],
            "p_value": details["p_value"],
            "dsr": details["dsr"],
            "n": details["n"],
            "n_trials": details.get("n_trials", trials),
            "strategy_family": details.get("strategy_family", strategy_family),
            "thresholds": {
                "dsr": self.dsr_threshold,
                "p_value": self.pvalue_threshold,
                "sharpe": self.min_sharpe,
            },
        }

        if not details["passed"]:
            logger.warning(
                "策略 %s 未通过上线门槛: failures=%s metrics=%s",
                name,
                details["failures"],
                metrics,
            )
            raise DeploymentBlockedError(
                name=name,
                failures=list(details["failures"]),
                metrics=metrics,
            )

        if name in self._registered:
            logger.warning("策略 %s 已注册，本次注册将覆盖原记录", name)

        record = RegisteredStrategy(
            name=name,
            registered_at=datetime.now(),
            metrics=metrics,
            notes=notes,
        )
        self._registered[name] = record
        logger.info("策略 %s 注册成功: metrics=%s", name, metrics)
        return record

    # ------------------------------------------------------------------ #
    # 查询
    # ------------------------------------------------------------------ #

    def is_registered(self, name: str) -> bool:
        """策略是否已注册."""
        return name in self._registered

    def get(self, name: str) -> RegisteredStrategy | None:
        """获取注册记录；不存在返回 ``None``."""
        return self._registered.get(name)

    def list_strategies(self) -> list[str]:
        """列出所有已注册策略名."""
        return list(self._registered.keys())


__all__ = [
    "DeploymentBlockedError",
    "RegisteredStrategy",
    "StrategyRegistry",
]
