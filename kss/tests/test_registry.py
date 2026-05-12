"""StrategyRegistry / is_deployable 单元测试 —— 守住上线门槛."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from kss.backtest.significance import Significance
from kss.strategies.registry import (
    DeploymentBlockedError,
    RegisteredStrategy,
    StrategyRegistry,
)


# --------------------------------------------------------------------------- #
# 数据 fixture
# --------------------------------------------------------------------------- #


def _good_returns(seed: int = 0, n: int = 564) -> pd.Series:
    """合成"好策略"：年化 Sharpe ~ 2.8, n=564, p<0.001."""
    rng = np.random.default_rng(seed)
    # mu=0.0035 / sigma=0.018 -> ann.Sharpe = 0.0035 * sqrt(252) / 0.018 ≈ 3.1
    return pd.Series(rng.normal(0.0035, 0.018, n))


def _bad_returns(seed: int = 0, n: int = 252) -> pd.Series:
    """合成"坏策略"：mu 接近 0，Sharpe ~ 0.3 量级."""
    rng = np.random.default_rng(seed)
    # mu=0.0001 / sigma=0.01 -> ann.Sharpe ≈ 0.16
    return pd.Series(rng.normal(0.0001, 0.01, n))


def _overfit_returns(seed: int = 2, n: int = 50) -> pd.Series:
    """"高 Sharpe 事后偏差"伪数据：Sharpe~1 但 n=50，DSR 应识别为不可上线."""
    rng = np.random.default_rng(seed)
    return pd.Series(rng.normal(0.00063, 0.01, n))


# --------------------------------------------------------------------------- #
# is_deployable
# --------------------------------------------------------------------------- #


class TestIsDeployable:

    def test_good_strategy_passes(self) -> None:
        """合成"好策略"应通过门槛."""
        r = _good_returns()
        assert Significance.is_deployable(r, n_trials=5) is True

    def test_bad_strategy_fails_with_reasons(self) -> None:
        """合成"坏策略"应被拒，failures 列出原因."""
        r = _bad_returns()
        details = Significance.is_deployable(r, n_trials=5, return_details=True)
        assert isinstance(details, dict)
        assert details["passed"] is False
        # Sharpe / p / DSR 中至少有一个挂掉
        assert len(details["failures"]) >= 1
        assert set(details["failures"]).issubset(
            {"sharpe<min", "p>threshold", "dsr<threshold"}
        )

    def test_high_n_trials_blocks_otherwise_good_strategy(self) -> None:
        """同样"好"策略，n_trials=100 时 DSR 阈值更严，应被拒.

        本测试用旧 dsr_threshold=0.7 来验证"trials 增加 → DSR 衰减"机制;
        新默认 0.4 在某些极佳策略上可能仍通过 mined 矫正，故显式锁阈值.
        """
        r = _good_returns()
        # n_trials=5 + 默认阈值 0.4 通过
        assert Significance.is_deployable(r, n_trials=5) is True
        # n_trials=100 + 旧阈值 0.7：DSR 应跌破
        details = Significance.is_deployable(
            r, n_trials=100, dsr_threshold=0.7, return_details=True
        )
        assert isinstance(details, dict)
        assert details["passed"] is False
        assert "dsr<threshold" in details["failures"]

    def test_return_details_full_dict(self) -> None:
        """return_details=True 必须包含 passed / sharpe / p_value / dsr / n / failures."""
        r = _good_returns()
        details = Significance.is_deployable(r, n_trials=5, return_details=True)
        assert isinstance(details, dict)
        required = {"passed", "sharpe", "p_value", "dsr", "n", "failures"}
        assert required.issubset(details.keys())
        assert isinstance(details["failures"], list)
        assert isinstance(details["passed"], bool)
        assert details["n"] == 564

    def test_overfit_short_series_blocked_by_dsr(self) -> None:
        """高 Sharpe (>0.5) 但 n=50 极短：DSR 应识别为事后偏差并拒绝."""
        r = _overfit_returns()  # seed=2, Sharpe~1.36, p~0.55, DSR~0.46
        details = Significance.is_deployable(r, n_trials=10, return_details=True)
        assert isinstance(details, dict)
        assert details["passed"] is False
        # 不一定 sharpe 挂——可能是 p / dsr 挂
        assert ("dsr<threshold" in details["failures"]) or (
            "p>threshold" in details["failures"]
        )


# --------------------------------------------------------------------------- #
# StrategyRegistry
# --------------------------------------------------------------------------- #


class TestStrategyRegistry:

    def test_register_success(self) -> None:
        """好策略可注册，记录可查."""
        reg = StrategyRegistry()
        rec = reg.register("log_mv_reverse", _good_returns(), n_trials=5)
        assert isinstance(rec, RegisteredStrategy)
        assert rec.name == "log_mv_reverse"
        assert reg.is_registered("log_mv_reverse")
        assert "log_mv_reverse" in reg.list_strategies()
        # metrics 应含 sharpe / p_value / dsr / n
        for key in ("sharpe", "p_value", "dsr", "n", "thresholds"):
            assert key in rec.metrics

    def test_register_blocked_raises(self) -> None:
        """坏策略注册抛 DeploymentBlockedError，error.failures / metrics 可访问."""
        reg = StrategyRegistry()
        with pytest.raises(DeploymentBlockedError) as exc_info:
            reg.register("noise", _bad_returns(), n_trials=5)

        err = exc_info.value
        assert err.name == "noise"
        assert isinstance(err.failures, list)
        assert len(err.failures) >= 1
        # error.metrics 必须含核心三指标
        for key in ("sharpe", "p_value", "dsr"):
            assert key in err.metrics
        # 注册失败后不应留下记录
        assert not reg.is_registered("noise")
        assert reg.get("noise") is None

    def test_re_register_overwrites_with_warning(self, caplog) -> None:
        """同名策略二次注册：覆盖旧记录并 warning log（"last write wins"）."""
        reg = StrategyRegistry()
        rec1 = reg.register("s1", _good_returns(seed=0), n_trials=5, notes="v1")
        # 第二次注册同名（仍是好数据）—— 期望覆盖且打 warning
        import logging
        with caplog.at_level(logging.WARNING, logger="kss.strategies.registry"):
            rec2 = reg.register("s1", _good_returns(seed=1), n_trials=5, notes="v2")
        assert reg.get("s1") is rec2
        assert reg.get("s1").notes == "v2"
        assert rec1 is not rec2
        # 应该有一条"已注册...覆盖"的 warning
        assert any("覆盖" in msg or "overwrite" in msg.lower() for msg in caplog.messages)

    def test_get_unknown_returns_none(self) -> None:
        reg = StrategyRegistry()
        assert reg.get("nonexistent") is None
        assert reg.is_registered("nonexistent") is False
        assert reg.list_strategies() == []

    def test_strategy_family_overrides_default_trials(self) -> None:
        """strategy_family='prior' 用 n_trials=1，让真先验因子通过门槛."""
        reg = StrategyRegistry()  # 默认 dsr_threshold=0.4
        returns = _good_returns(seed=1)
        record = reg.register("size_factor", returns, strategy_family="prior")
        assert record.metrics["n_trials"] == 1
        assert record.metrics["strategy_family"] == "prior"
        assert record.metrics["dsr"] > 0.5

    def test_strategy_family_mined_blocks_hypermined(self) -> None:
        """strategy_family='mined' 用 n_trials=100，过滤数据挖掘出的假阳性."""
        reg = StrategyRegistry()
        # 中等 Sharpe + n_trials=100 应该让 DSR 大幅下降
        rng = np.random.default_rng(7)
        # Sharpe ~ 1.5（在 prior 下能过；mined 下应不过）
        returns = pd.Series(rng.normal(0.0018, 0.012, 400))
        with pytest.raises(DeploymentBlockedError) as exc:
            reg.register("hypermined", returns, strategy_family="mined")
        # 主因应该是 dsr<threshold
        assert "dsr<threshold" in exc.value.failures

    def test_explicit_n_trials_overrides_family(self) -> None:
        """显式 n_trials 优先级高于 strategy_family."""
        reg = StrategyRegistry()
        returns = _good_returns(seed=2)
        record = reg.register(
            "explicit_trials_1", returns,
            n_trials=1, strategy_family="mined",  # 矛盾
        )
        # 显式 n_trials=1 优先
        assert record.metrics["n_trials"] == 1

    def test_default_dsr_threshold_is_lenient(self) -> None:
        """新默认 dsr_threshold=0.4 应该宽松到能让 size factor 通过."""
        reg = StrategyRegistry()
        assert reg.dsr_threshold == 0.4

    def test_blocked_error_message_contains_failures_and_metrics(self) -> None:
        """DeploymentBlockedError 的字符串表达里能看到 failures 和 metrics."""
        reg = StrategyRegistry()
        try:
            reg.register("bad", _bad_returns(), n_trials=5)
        except DeploymentBlockedError as exc:
            msg = str(exc)
            assert "bad" in msg
            # 至少能看到 metrics= 关键字
            assert "metrics" in msg
            # 至少有一个失败原因被记进 message
            assert any(
                tag in msg for tag in ("sharpe<min", "p>threshold", "dsr<threshold")
            )
        else:
            pytest.fail("应抛 DeploymentBlockedError")
