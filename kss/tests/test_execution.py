"""ExecutionModel 单元测试 —— 涨跌停 / 部分成交 / 开盘滑点 + cross-section 集成."""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
import pytest

from kss.backtest.cost_model import CostModel, ExecutionModel
from kss.backtest.cross_section import factor_cross_section_backtest


# --------------------------------------------------------------------------- #
# 涨跌停判定
# --------------------------------------------------------------------------- #


class TestLimitDetection:
    """is_limit_up / is_limit_down 行级判定."""

    def test_main_board_below_threshold(self) -> None:
        """主板 9.5% 涨幅 < 10% - 0.5% 容差 → 不算涨停."""
        exec_m = ExecutionModel()
        row = pd.Series({"symbol": "600000.SH", "open": 10.95, "pre_close": 10.0})
        assert exec_m.is_limit_up(row) is False

    def test_main_board_at_threshold(self) -> None:
        """主板 10% 涨幅 → 涨停."""
        exec_m = ExecutionModel()
        row = pd.Series({"symbol": "600000.SH", "open": 11.0, "pre_close": 10.0})
        assert exec_m.is_limit_up(row) is True

    def test_main_board_near_threshold_with_tolerance(self) -> None:
        """主板 9.6% 涨幅 ≥ 10% - 0.5% 容差 → 判为涨停（实盘 9.99% 也算）."""
        exec_m = ExecutionModel()
        row = pd.Series({"symbol": "600000.SH", "open": 10.96, "pre_close": 10.0})
        assert exec_m.is_limit_up(row) is True

    def test_kcb_below_20pct(self) -> None:
        """科创板 19.5% < 20% - 0.5% 容差 → 不算涨停."""
        exec_m = ExecutionModel()
        row = pd.Series({"symbol": "688017.SH", "open": 11.95, "pre_close": 10.0})
        assert exec_m.is_limit_up(row) is False

    def test_kcb_at_20pct(self) -> None:
        """科创板 20% 涨幅 → 涨停（自动识别 688 前缀）."""
        exec_m = ExecutionModel()
        row = pd.Series({"symbol": "688017.SH", "open": 12.0, "pre_close": 10.0})
        assert exec_m.is_limit_up(row) is True

    def test_chinext_at_20pct(self) -> None:
        """创业板 300xxx 20% 涨幅 → 涨停."""
        exec_m = ExecutionModel()
        row = pd.Series({"symbol": "300017.SZ", "open": 12.0, "pre_close": 10.0})
        assert exec_m.is_limit_up(row) is True

    def test_limit_down_main_board(self) -> None:
        """主板 -10% → 跌停."""
        exec_m = ExecutionModel()
        row = pd.Series({"symbol": "600000.SH", "open": 9.0, "pre_close": 10.0})
        assert exec_m.is_limit_down(row) is True
        # 对应 open 同样不涨停
        assert exec_m.is_limit_up(row) is False

    def test_limit_down_kcb(self) -> None:
        """科创板 -20% → 跌停；-19% 在容差内 → 也算跌停."""
        exec_m = ExecutionModel()
        row_full = pd.Series({"symbol": "688017.SH", "open": 8.0, "pre_close": 10.0})
        assert exec_m.is_limit_down(row_full) is True
        row_near = pd.Series({"symbol": "688017.SH", "open": 8.04, "pre_close": 10.0})
        assert exec_m.is_limit_down(row_near) is True
        row_safe = pd.Series({"symbol": "688017.SH", "open": 8.2, "pre_close": 10.0})
        assert exec_m.is_limit_down(row_safe) is False

    def test_limit_with_nan_values(self) -> None:
        """NaN / 0 / 缺列 → 容错为 False（不当成涨跌停）."""
        exec_m = ExecutionModel()
        assert exec_m.is_limit_up(pd.Series({"symbol": "600000", "open": np.nan,
                                              "pre_close": 10.0})) is False
        assert exec_m.is_limit_up(pd.Series({"symbol": "600000", "open": 10.0,
                                              "pre_close": 0.0})) is False
        assert exec_m.is_limit_up(pd.Series({"symbol": "600000"})) is False


# --------------------------------------------------------------------------- #
# filter_tradable
# --------------------------------------------------------------------------- #


class TestFilterTradable:

    def _sample_df(self) -> pd.DataFrame:
        return pd.DataFrame([
            # 正常股
            {"symbol": "600000.SH", "open": 10.5, "pre_close": 10.0, "amount": 1e6},
            # 涨停（主板 +10%）
            {"symbol": "600001.SH", "open": 11.0, "pre_close": 10.0, "amount": 1e6},
            # 跌停（主板 -10%）
            {"symbol": "600002.SH", "open": 9.0, "pre_close": 10.0, "amount": 1e6},
            # 科创板涨停（+20%）
            {"symbol": "688017.SH", "open": 12.0, "pre_close": 10.0, "amount": 1e6},
        ])

    def test_filter_buy_drops_limit_up(self) -> None:
        exec_m = ExecutionModel()
        out = exec_m.filter_tradable(self._sample_df(), side="buy")
        symbols = set(out["symbol"].tolist())
        assert "600001.SH" not in symbols  # 主板涨停剔除
        assert "688017.SH" not in symbols  # 科创板涨停剔除
        assert "600000.SH" in symbols  # 正常股保留
        assert "600002.SH" in symbols  # 跌停股可买（不在 buy 过滤范围）

    def test_filter_sell_drops_limit_down(self) -> None:
        exec_m = ExecutionModel()
        out = exec_m.filter_tradable(self._sample_df(), side="sell")
        symbols = set(out["symbol"].tolist())
        assert "600002.SH" not in symbols  # 跌停股剔除
        assert "600001.SH" in symbols
        assert "688017.SH" in symbols
        assert "600000.SH" in symbols

    def test_filter_missing_pre_close_warns_and_passthrough(self) -> None:
        """缺 pre_close 列 → 返回原 day_df + warn."""
        exec_m = ExecutionModel()
        df = pd.DataFrame([{"symbol": "600000.SH", "open": 11.0}])
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            out = exec_m.filter_tradable(df, side="buy")
        assert len(out) == len(df)
        assert any("pre_close" in str(w.message) for w in caught)

    def test_filter_invalid_side_raises(self) -> None:
        exec_m = ExecutionModel()
        with pytest.raises(ValueError, match="side"):
            exec_m.filter_tradable(self._sample_df(), side="invalid")  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# max_tradable_ratio
# --------------------------------------------------------------------------- #


class TestMaxTradableRatio:

    def test_small_position_full_fill(self) -> None:
        """position 远小于 max_volume_pct × amount → 全额成交 1.0."""
        exec_m = ExecutionModel(max_volume_pct=0.05)
        row = pd.Series({"amount": 1_000_000.0})
        # cap = 50_000；下单 1_000 远小于 → 1.0
        assert exec_m.max_tradable_ratio(row, position_value=1_000.0) == 1.0

    def test_large_position_partial_fill(self) -> None:
        """position 大于 cap → 缩减到 cap / position."""
        exec_m = ExecutionModel(max_volume_pct=0.05)
        row = pd.Series({"amount": 1_000_000.0})
        # cap = 50_000；下单 100_000 → 0.5
        ratio = exec_m.max_tradable_ratio(row, position_value=100_000.0)
        assert ratio == pytest.approx(0.5)

    def test_missing_amount_passthrough(self) -> None:
        """缺 amount / NaN → 1.0（保守不限）."""
        exec_m = ExecutionModel()
        assert exec_m.max_tradable_ratio(pd.Series({}), 100.0) == 1.0
        assert exec_m.max_tradable_ratio(pd.Series({"amount": np.nan}), 100.0) == 1.0
        assert exec_m.max_tradable_ratio(pd.Series({"amount": 0.0}), 100.0) == 1.0


# --------------------------------------------------------------------------- #
# slippage_cost
# --------------------------------------------------------------------------- #


class TestSlippageCost:

    def test_slippage_simple(self) -> None:
        """turnover=1.0 × 10 bps → 0.001."""
        exec_m = ExecutionModel(open_slippage_bps=10.0)
        assert exec_m.slippage_cost(1.0) == pytest.approx(0.001)

    def test_slippage_scaled(self) -> None:
        exec_m = ExecutionModel(open_slippage_bps=10.0)
        assert exec_m.slippage_cost(0.5) == pytest.approx(0.0005)
        assert exec_m.slippage_cost(0.0) == 0.0


# --------------------------------------------------------------------------- #
# Constructor validation
# --------------------------------------------------------------------------- #


class TestConstructorValidation:

    def test_invalid_limit_pct(self) -> None:
        with pytest.raises(ValueError):
            ExecutionModel(limit_up_pct=0.0)
        with pytest.raises(ValueError):
            ExecutionModel(limit_up_pct=1.5)

    def test_invalid_max_volume_pct(self) -> None:
        with pytest.raises(ValueError):
            ExecutionModel(max_volume_pct=0.0)
        with pytest.raises(ValueError):
            ExecutionModel(max_volume_pct=1.5)

    def test_invalid_slippage(self) -> None:
        with pytest.raises(ValueError):
            ExecutionModel(open_slippage_bps=-1.0)


# --------------------------------------------------------------------------- #
# 集成：factor_cross_section_backtest with execution
# --------------------------------------------------------------------------- #


def _make_panel_with_market(
    n_dates: int = 60,
    n_stocks: int = 30,
    signal_strength: float = 0.5,
    limit_up_rate: float = 0.15,
    seed: int = 0,
) -> pd.DataFrame:
    """合成带开盘/前收盘/成交量的横截面面板.

    - factor_a 与 next_day_return 正向相关 (signal_strength)；
    - 每日按 limit_up_rate 比例随机选高分股标记为涨停（open 高于 pre_close 11%）；
    - amount 充足，避免触发部分成交（部分成交效果由独立单测覆盖）.
    """
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2024-01-01", periods=n_dates)
    rows = []
    for d in dates:
        # 当日各股因子值 & 收益
        per_day = []
        for s in range(n_stocks):
            f = rng.standard_normal()
            r = signal_strength * f + rng.standard_normal() * 0.5
            per_day.append({
                "trade_date": d,
                "symbol": f"600{s:03d}.SH",
                "factor_a": f,
                "next_day_return": r * 0.005,
                "pre_close": 10.0,
                "open": 10.0,  # 默认正常
                "amount": 1e9,  # 大成交量避免部分成交触发
            })
        # 强制把分数前列的一部分置成涨停 → execution 应剔除
        per_day.sort(key=lambda x: -x["factor_a"])  # 高分在前
        n_lim = max(1, int(len(per_day) * limit_up_rate))
        for i in range(n_lim):
            per_day[i]["open"] = 11.0  # +10% 主板涨停
        rows.extend(per_day)
    return pd.DataFrame(rows)


class TestCrossSectionIntegration:

    def test_execution_reduces_sharpe_or_equal(self) -> None:
        """加 execution 后 Sharpe ≤ 不加（涨停股被滤 + 滑点扣减）.

        构造的数据中高分股有强 alpha，故剔除其中涨停部分 → 收益下降 / Sharpe 下降.
        """
        df = _make_panel_with_market(signal_strength=1.0, limit_up_rate=0.4, seed=42)
        cost = CostModel(buy_cost=0.001, sell_cost=0.002)
        res_plain = factor_cross_section_backtest(
            df, "factor_a", cost, top_pct=0.3, direction="long_high",
        )
        exec_m = ExecutionModel(limit_up_pct=0.10, open_slippage_bps=10)
        res_exec = factor_cross_section_backtest(
            df, "factor_a", cost, top_pct=0.3, direction="long_high",
            execution=exec_m,
        )
        assert res_plain is not None
        assert res_exec is not None
        sharpe_plain = res_plain["net_return"].mean() / (
            res_plain["net_return"].std() + 1e-12
        )
        sharpe_exec = res_exec["net_return"].mean() / (
            res_exec["net_return"].std() + 1e-12
        )
        assert sharpe_exec <= sharpe_plain + 1e-9

    def test_execution_attaches_tradable_columns(self) -> None:
        """启用 execution 后结果应含 n_tradable_pre / n_tradable_post 列."""
        # 用足够大的池（80 股）+ 30% 顶仓 → 一天 24 只，远大于 min_stocks=10，
        # 涨停剔除后剩余仍 > min_stocks，避免触发 fallback.
        df = _make_panel_with_market(n_stocks=80, limit_up_rate=0.3, seed=7)
        cost = CostModel()
        exec_m = ExecutionModel()
        res = factor_cross_section_backtest(
            df, "factor_a", cost, top_pct=0.3, direction="long_high",
            execution=exec_m,
        )
        assert res is not None
        assert "n_tradable_pre" in res.columns
        assert "n_tradable_post" in res.columns
        # 至少应该有几天确实剔除了涨停股 → post < pre
        assert (res["n_tradable_post"] < res["n_tradable_pre"]).any()

    def test_execution_optional_preserves_baseline(self) -> None:
        """不传 execution 时行为与改动前完全一致（核心列对齐）."""
        df = _make_panel_with_market(seed=11)
        cost = CostModel()
        res = factor_cross_section_backtest(
            df, "factor_a", cost, top_pct=0.3, direction="long_high",
        )
        assert res is not None
        assert "n_tradable_pre" not in res.columns
        assert "n_tradable_post" not in res.columns


# --------------------------------------------------------------------------- #
# 4.2 停牌 / ST / 零成交过滤
# --------------------------------------------------------------------------- #


class _StubSuspensionData:
    """轻量 stub，**不**走 CSV 加载——直接喂内部 set."""

    def __init__(
        self,
        suspension: dict[str, set[pd.Timestamp]] | None = None,
        st: dict[str, set[pd.Timestamp]] | None = None,
    ) -> None:
        self.suspension = suspension or {}
        self.st = st or {}

    def is_suspended(self, ts_code: str, date: object) -> bool:
        d = pd.Timestamp(date).normalize()
        return d in self.suspension.get(str(ts_code), set())

    def is_st(self, ts_code: str, date: object) -> bool:
        d = pd.Timestamp(date).normalize()
        return d in self.st.get(str(ts_code), set())


class TestSuspensionFilter:
    """ExecutionModel + SuspensionData 联合过滤."""

    def _day_df(self, trade_date: str = "2024-03-01") -> pd.DataFrame:
        return pd.DataFrame([
            {"symbol": "600000.SH", "trade_date": pd.Timestamp(trade_date),
             "open": 10.5, "pre_close": 10.0, "amount": 1e6},
            {"symbol": "600100.SH", "trade_date": pd.Timestamp(trade_date),
             "open": 10.3, "pre_close": 10.0, "amount": 1e6},
            # 停牌候选
            {"symbol": "600200.SH", "trade_date": pd.Timestamp(trade_date),
             "open": 10.2, "pre_close": 10.0, "amount": 1e6},
            # ST 候选
            {"symbol": "600300.SH", "trade_date": pd.Timestamp(trade_date),
             "open": 10.1, "pre_close": 10.0, "amount": 1e6},
            # 零成交（隐式停牌）
            {"symbol": "600400.SH", "trade_date": pd.Timestamp(trade_date),
             "open": 10.0, "pre_close": 10.0, "amount": 0.0},
        ])

    def test_suspended_filtered_out(self) -> None:
        """停牌名单内的股票被剔除."""
        sus = _StubSuspensionData(
            suspension={"600200.SH": {pd.Timestamp("2024-03-01")}}
        )
        exec_m = ExecutionModel(suspension_data=sus)
        out = exec_m.filter_tradable(self._day_df(), side="buy")
        symbols = set(out["symbol"].tolist())
        assert "600200.SH" not in symbols
        assert "600000.SH" in symbols

    def test_st_filtered_out_on_buy(self) -> None:
        """ST 股票在买入侧被剔除."""
        sus = _StubSuspensionData(
            st={"600300.SH": {pd.Timestamp("2024-03-01")}}
        )
        exec_m = ExecutionModel(suspension_data=sus, exclude_st=True)
        out = exec_m.filter_tradable(self._day_df(), side="buy")
        symbols = set(out["symbol"].tolist())
        assert "600300.SH" not in symbols
        # 卖出侧 ST 仍允许（已持仓减仓）
        out_sell = exec_m.filter_tradable(self._day_df(), side="sell")
        assert "600300.SH" in set(out_sell["symbol"].tolist())

    def test_st_disabled_when_exclude_st_false(self) -> None:
        sus = _StubSuspensionData(
            st={"600300.SH": {pd.Timestamp("2024-03-01")}}
        )
        exec_m = ExecutionModel(suspension_data=sus, exclude_st=False)
        out = exec_m.filter_tradable(self._day_df(), side="buy")
        assert "600300.SH" in set(out["symbol"].tolist())

    def test_zero_volume_filtered(self) -> None:
        """amount=0 的行被视作停牌剔除（即使无 SuspensionData）."""
        exec_m = ExecutionModel(exclude_zero_volume=True)
        out = exec_m.filter_tradable(self._day_df(), side="buy")
        assert "600400.SH" not in set(out["symbol"].tolist())

    def test_zero_volume_can_be_disabled(self) -> None:
        exec_m = ExecutionModel(exclude_zero_volume=False)
        out = exec_m.filter_tradable(self._day_df(), side="buy")
        assert "600400.SH" in set(out["symbol"].tolist())

    def test_no_suspension_data_backward_compat(self) -> None:
        """suspension_data=None → 行为与改造前一致（停牌 / ST 名单跳过）."""
        exec_m = ExecutionModel(suspension_data=None, exclude_zero_volume=False)
        out = exec_m.filter_tradable(self._day_df(), side="buy")
        # 仍剔除涨停（无）+ 不剔除其他 → 全部留下
        assert len(out) == 5

    def test_missing_amount_column_warns_once(self) -> None:
        """缺 amount 列时 graceful warn，但仍正常返回（不阻断）."""
        df = self._day_df().drop(columns=["amount"])
        exec_m = ExecutionModel(exclude_zero_volume=True)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            out = exec_m.filter_tradable(df, side="buy")
        # 缺列时退化为只做涨跌停过滤
        assert len(out) == len(df)
        assert any("amount" in str(w.message) for w in caught)

    def test_missing_date_column_skips_suspension(self) -> None:
        """缺 trade_date 列 → 跳过名单查询，但保留涨跌停过滤."""
        sus = _StubSuspensionData(
            suspension={"600200.SH": {pd.Timestamp("2024-03-01")}}
        )
        df = self._day_df().drop(columns=["trade_date"])
        exec_m = ExecutionModel(suspension_data=sus, exclude_zero_volume=False)
        out = exec_m.filter_tradable(df, side="buy")
        # 缺 date 列 → 名单无法查 → 600200 仍留下
        assert "600200.SH" in set(out["symbol"].tolist())


class TestIsTradable:
    """ExecutionModel.is_tradable 行级综合判定."""

    def test_normal_row_tradable(self) -> None:
        exec_m = ExecutionModel()
        row = pd.Series({
            "symbol": "600000.SH", "open": 10.5, "pre_close": 10.0,
            "amount": 1e6, "trade_date": pd.Timestamp("2024-03-01"),
        })
        assert exec_m.is_tradable(row, side="buy") is True

    def test_limit_up_not_tradable_buy(self) -> None:
        exec_m = ExecutionModel()
        row = pd.Series({
            "symbol": "600000.SH", "open": 11.0, "pre_close": 10.0,
            "amount": 1e6, "trade_date": pd.Timestamp("2024-03-01"),
        })
        assert exec_m.is_tradable(row, side="buy") is False
        # 涨停可卖
        assert exec_m.is_tradable(row, side="sell") is True

    def test_zero_volume_not_tradable(self) -> None:
        exec_m = ExecutionModel(exclude_zero_volume=True)
        row = pd.Series({
            "symbol": "600000.SH", "open": 10.0, "pre_close": 10.0,
            "amount": 0.0, "trade_date": pd.Timestamp("2024-03-01"),
        })
        assert exec_m.is_tradable(row, side="buy") is False

    def test_suspended_not_tradable(self) -> None:
        sus = _StubSuspensionData(
            suspension={"600000.SH": {pd.Timestamp("2024-03-01")}}
        )
        exec_m = ExecutionModel(suspension_data=sus)
        row = pd.Series({
            "symbol": "600000.SH", "open": 10.0, "pre_close": 10.0,
            "amount": 1e6, "trade_date": pd.Timestamp("2024-03-01"),
        })
        assert exec_m.is_tradable(row, side="buy") is False
        assert exec_m.is_tradable(row, side="sell") is False

    def test_st_not_tradable_buy_only(self) -> None:
        sus = _StubSuspensionData(
            st={"600000.SH": {pd.Timestamp("2024-03-01")}}
        )
        exec_m = ExecutionModel(suspension_data=sus, exclude_st=True)
        row = pd.Series({
            "symbol": "600000.SH", "open": 10.0, "pre_close": 10.0,
            "amount": 1e6, "trade_date": pd.Timestamp("2024-03-01"),
        })
        assert exec_m.is_tradable(row, side="buy") is False
        # ST 减仓允许
        assert exec_m.is_tradable(row, side="sell") is True

    def test_is_tradable_with_explicit_date(self) -> None:
        sus = _StubSuspensionData(
            suspension={"600000.SH": {pd.Timestamp("2024-03-01")}}
        )
        exec_m = ExecutionModel(suspension_data=sus)
        row = pd.Series({"symbol": "600000.SH", "open": 10.0, "pre_close": 10.0,
                          "amount": 1e6})  # 无 trade_date
        # 显式传入 date
        assert exec_m.is_tradable(
            row, date=pd.Timestamp("2024-03-01"), side="buy"
        ) is False
        # 不传日期 → 名单查不了 → True
        assert exec_m.is_tradable(row, side="buy") is True
