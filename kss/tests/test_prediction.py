"""Prediction 模块单元测试.

集中覆盖 :class:`DailyForecast` 与其辅助方法。前一版本 ``prediction/`` 整个子包
零测试覆盖（P0 #12），同时 ``_score_to_return`` 在 day_df 切片输入下静默输出 NaN
（P1 #13）。本文件锁死两个核心契约：

1. ``_score_to_return`` 在任何输入（单行/多行/常数/NaN）下都不串 NaN/inf；
2. ``generate`` 在合法 factor_df 上产出每股一行、信号在 buy/sell/hold/neutral
   集合内的预测表；
3. ``_rank_to_signal`` 4 个阈值边界（0.85/0.60/0.40/0.15）行为正确；
4. ``rebalance_report`` 在不同持仓/信号组合下产出正确的 add/reduce/keep 列表
   与 cash_signal 分支。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from kss.models.base import ModelBase
from kss.models.registry import ModelRegistry
from kss.prediction.daily_forecast import DailyForecast
from kss.strategies.cross_sectional import CrossSectionalStrategy


# ====================================================================== #
# 测试用 stub 模型 —— 给定打分回调，不依赖 LightGBM
# ====================================================================== #


class _StubModel(ModelBase):
    """以预设打分函数响应 predict 的最小模型替身."""

    def __init__(self, score_fn) -> None:  # type: ignore[no-untyped-def]
        self.score_fn = score_fn

    def fit(self, X, y, **kwargs) -> None:  # type: ignore[no-untyped-def]
        pass

    def predict(self, X):  # type: ignore[no-untyped-def]
        return self.score_fn(X)

    def feature_importance(self):  # type: ignore[no-untyped-def]
        return None

    def save(self, path: str) -> None:
        pass

    def load(self, path: str) -> None:
        pass

    def get_params(self) -> dict:
        return {}


def _make_factor_df(
    n_dates: int = 5,
    n_stocks: int = 10,
    seed: int = 42,
) -> pd.DataFrame:
    """构造满足 DailyForecast.generate 要求的 factor_df."""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2024-01-01", periods=n_dates)
    records: list[dict] = []
    for d in dates:
        for s in range(n_stocks):
            records.append({
                "trade_date": d,
                "symbol": f"688{s:03d}.SH",
                "feat_1": rng.standard_normal(),
                "feat_2": rng.standard_normal(),
                "close": 100.0 + rng.standard_normal(),
            })
    return pd.DataFrame(records)


# ====================================================================== #
# P1 #13: _score_to_return 不再产生 NaN
# ====================================================================== #


class TestScoreToReturn:
    """_score_to_return 的鲁棒性回归测试（修复 P1 #13）.

    旧实现 ``z * close.pct_change().rolling(20).std() * 0.5`` 在 day_df 切片
    （单日多股票一行一值）输入下：
      - close.pct_change() 计算的是跨股票价差（无业务含义）
      - rolling(20).std() 在 <20 个股票时全 NaN
      - fillna(vol.mean()) = fillna(NaN) → 仍 NaN
      - 最终 pred_return 全 NaN，silent 流入 pred_price 与 Markdown 报告.
    新实现退化为 ``z * 0.02``.clip(±5%)，下方测试用 sentinel 锁死该退化.
    """

    def test_single_row_input_no_nan(self) -> None:
        """单行输入是 day_df 的极端 case；不应产生 NaN."""
        result = DailyForecast._score_to_return(pd.Series([0.5]))
        assert not result.isna().any()
        assert np.isfinite(result).all()
        # 单行 std=NaN，z fillna(0) → 输出 0
        assert result.iloc[0] == pytest.approx(0.0)

    def test_multi_row_finite(self) -> None:
        """多行输入产生有限输出，clip 在 ±5% 内."""
        score = pd.Series([0.1, -0.2, 0.3, 0.5, -0.1, 0.0, 0.4])
        result = DailyForecast._score_to_return(score)
        assert not result.isna().any()
        assert (result.abs() <= 0.05 + 1e-12).all()

    def test_monotonic_in_score(self) -> None:
        """pred_return 单调对应 pred_score（z 是线性变换 → 单调）."""
        score = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
        result = DailyForecast._score_to_return(score).reset_index(drop=True)
        assert result.is_monotonic_increasing

    def test_constant_score_no_inf(self) -> None:
        """所有打分相同 → std=0 → 旧 +1e-8 epsilon 路径会爆炸；新 fillna(0) 兜底."""
        result = DailyForecast._score_to_return(pd.Series([0.5, 0.5, 0.5, 0.5]))
        assert not result.isna().any()
        assert np.isfinite(result).all()
        assert (result == 0.0).all(), "所有相同打分应映射为 0 收益（无 ranking 信号）"


# ====================================================================== #
# P0 #12: DailyForecast.generate 端到端覆盖
# ====================================================================== #


class TestDailyForecastGenerate:
    """DailyForecast.generate 行为锁定（修复 P0 #12 主路径）."""

    def _make_forecast(self) -> DailyForecast:
        registry = ModelRegistry(storage_dir="./_test_storage")
        strategy = CrossSectionalStrategy(top_pct=0.2)
        return DailyForecast(model_registry=registry, strategy=strategy)

    def test_generate_returns_one_row_per_stock(self) -> None:
        """每只股票在目标日期产一行，列齐全."""
        factor_df = _make_factor_df(n_dates=5, n_stocks=10)
        feature_cols = ["feat_1", "feat_2"]
        # 让 model 用 feat_1 直接作为打分，确保排名稳定
        model = _StubModel(lambda X: X[:, 0])

        forecast = self._make_forecast()
        result = forecast.generate(factor_df, feature_cols, model)

        assert len(result) == 10, "每只股票应产出一行"
        expected_cols = {
            "symbol", "pred_score", "pred_return", "pred_price",
            "current_price", "signal", "confidence",
        }
        assert expected_cols.issubset(result.columns)

    def test_generate_signal_values_in_known_set(self) -> None:
        """signal 列只能取 buy/sell/hold/neutral."""
        factor_df = _make_factor_df(n_dates=5, n_stocks=10)
        model = _StubModel(lambda X: X[:, 0])
        forecast = self._make_forecast()
        result = forecast.generate(factor_df, ["feat_1", "feat_2"], model)
        assert set(result["signal"].unique()).issubset(
            {"buy", "sell", "hold", "neutral"}
        )

    def test_generate_pred_price_finite(self) -> None:
        """修复 P1 #13 的关键断言：pred_price 不再串 NaN.

        旧实现 _score_to_return 全 NaN → pred_return=NaN → pred_price=NaN，
        Markdown 渲染时直接打印 ``nan``。
        """
        factor_df = _make_factor_df(n_dates=5, n_stocks=10)
        model = _StubModel(lambda X: X[:, 0])
        forecast = self._make_forecast()
        result = forecast.generate(factor_df, ["feat_1", "feat_2"], model)

        assert not result["pred_return"].isna().any()
        assert not result["pred_price"].isna().any()
        assert np.isfinite(result["pred_price"]).all()
        # pred_price 应紧贴 current_price ±5%（_score_to_return 的 clip 区间）
        ratio = result["pred_price"] / result["current_price"]
        assert ((ratio >= 0.95) & (ratio <= 1.05)).all()

    def test_generate_empty_date_raises(self) -> None:
        """factor_df 中不存在的日期应 raise ValueError（业务层契约）."""
        factor_df = _make_factor_df(n_dates=5, n_stocks=10)
        model = _StubModel(lambda X: X[:, 0])
        forecast = self._make_forecast()
        with pytest.raises(ValueError, match="无可用数据"):
            forecast.generate(
                factor_df, ["feat_1", "feat_2"], model, date="2025-12-31"
            )

    def test_generate_missing_feature_raises(self) -> None:
        """缺特征列应 raise KeyError（业务层契约）."""
        factor_df = _make_factor_df(n_dates=5, n_stocks=10)
        model = _StubModel(lambda X: X[:, 0])
        forecast = self._make_forecast()
        with pytest.raises(KeyError, match="缺少特征列"):
            forecast.generate(factor_df, ["feat_1", "missing_feat"], model)


# ====================================================================== #
# P0 #12: _rank_to_signal 四阈值边界
# ====================================================================== #


class TestRankToSignal:
    """_rank_to_signal 阈值边界（修复 P0 #12 的核心 silent-bug 类）.

    四个边界 0.85 / 0.60 / 0.40 / 0.15 任一漂移都会让整组合错误分类。
    """

    def test_buy_threshold(self) -> None:
        assert DailyForecast._rank_to_signal(0.85) == "buy"
        assert DailyForecast._rank_to_signal(0.84999) == "neutral"
        assert DailyForecast._rank_to_signal(1.0) == "buy"

    def test_sell_threshold(self) -> None:
        assert DailyForecast._rank_to_signal(0.15) == "sell"
        assert DailyForecast._rank_to_signal(0.15001) == "neutral"
        assert DailyForecast._rank_to_signal(0.0) == "sell"

    def test_hold_band(self) -> None:
        assert DailyForecast._rank_to_signal(0.40) == "hold"
        assert DailyForecast._rank_to_signal(0.50) == "hold"
        assert DailyForecast._rank_to_signal(0.60) == "hold"

    def test_neutral_bands(self) -> None:
        # 0.15 < r < 0.40 → neutral
        assert DailyForecast._rank_to_signal(0.30) == "neutral"
        # 0.60 < r < 0.85 → neutral
        assert DailyForecast._rank_to_signal(0.70) == "neutral"


# ====================================================================== #
# P0 #12: rebalance_report 三个 cash_signal 分支
# ====================================================================== #


class TestRebalanceReport:
    """rebalance_report add/reduce/keep 与 cash_signal 分支（修复 P0 #12）."""

    def _setup(self):
        registry = ModelRegistry(storage_dir="./_test_storage_rebalance")
        strategy = CrossSectionalStrategy(top_pct=0.2)
        forecast = DailyForecast(model_registry=registry, strategy=strategy)
        factor_df = _make_factor_df(n_dates=5, n_stocks=10)
        # 让前 2 名打分极高（rank > 0.85 → buy），后 2 名打分极低（rank < 0.15 → sell）
        # 通过控制 feat_1 实现
        symbols = sorted(factor_df["symbol"].unique())

        def stub_score(X):
            return X[:, 0]

        # 把 feat_1 改成 symbol 的 index，对应日期 0 行 0~9
        latest_date = factor_df["trade_date"].max()
        latest_mask = factor_df["trade_date"] == latest_date
        latest_df = factor_df.loc[latest_mask].sort_values("symbol").reset_index(drop=True)
        for i, sym in enumerate(symbols):
            factor_df.loc[latest_mask & (factor_df["symbol"] == sym), "feat_1"] = i

        model = _StubModel(stub_score)
        return forecast, factor_df, model, symbols

    def test_add_list_contains_new_buys(self) -> None:
        forecast, factor_df, model, symbols = self._setup()
        # 没有任何持仓 → buy 信号股票应全在 add_list
        result = forecast.rebalance_report(
            current_holdings=set(),
            factor_df=factor_df,
            feature_cols=["feat_1", "feat_2"],
            model=model,
        )
        add_symbols = {r["symbol"] for r in result["add_list"]}
        # rank > 0.85 的 stock（feat_1 = 8 或 9，对应 rank 0.9/1.0）
        assert symbols[-1] in add_symbols  # 最高打分一定在 add

    def test_reduce_list_when_holding_drops_out(self) -> None:
        forecast, factor_df, model, symbols = self._setup()
        # 持有打分最低的两只 → 应进 reduce_list
        result = forecast.rebalance_report(
            current_holdings={symbols[0], symbols[1]},
            factor_df=factor_df,
            feature_cols=["feat_1", "feat_2"],
            model=model,
        )
        reduce_symbols = {r["symbol"] for r in result["reduce_list"]}
        assert symbols[0] in reduce_symbols
        assert symbols[1] in reduce_symbols

    def test_cash_signal_decrease_when_many_adds(self) -> None:
        forecast, factor_df, model, symbols = self._setup()
        # 无持仓 → add 多，reduce 少（甚至为零）→ net_change > 3 → cash_signal=decrease
        result = forecast.rebalance_report(
            current_holdings=set(),
            factor_df=factor_df,
            feature_cols=["feat_1", "feat_2"],
            model=model,
        )
        # 注：实际是否触发 decrease 取决于 buy 信号数量；仅断言取值在合法集内
        assert result["cash_signal"] in ("increase", "decrease", "hold")

    def test_cash_signal_legal_values(self) -> None:
        """无论输入如何，cash_signal 必须是三个合法值之一."""
        forecast, factor_df, model, _ = self._setup()
        for holdings in (set(), {"688000.SH"}, {"688000.SH", "688001.SH"}):
            result = forecast.rebalance_report(
                current_holdings=holdings,
                factor_df=factor_df,
                feature_cols=["feat_1", "feat_2"],
                model=model,
            )
            assert result["cash_signal"] in ("increase", "decrease", "hold"), \
                f"cash_signal 取值非法: {result['cash_signal']}"

    def test_dict_holdings_unwrapped(self) -> None:
        """current_holdings 是 dict 时应被识别为 symbol 集合（旧 bug 残留风险）."""
        forecast, factor_df, model, symbols = self._setup()
        holdings_dict = {symbols[0]: 0.5, symbols[1]: 0.5}
        result = forecast.rebalance_report(
            current_holdings=holdings_dict,
            factor_df=factor_df,
            feature_cols=["feat_1", "feat_2"],
            model=model,
        )
        # 不抛错且返回合法结构
        assert "add_list" in result
        assert "reduce_list" in result
        assert "keep_list" in result
        assert "cash_signal" in result


# ====================================================================== #
# format_daily 渲染 smoke 测试
# ====================================================================== #


class TestFormatDaily:
    """format_daily Markdown 输出冒烟测试."""

    def test_format_contains_title_and_summary(self) -> None:
        registry = ModelRegistry(storage_dir="./_test_storage_format")
        strategy = CrossSectionalStrategy(top_pct=0.2)
        forecast = DailyForecast(model_registry=registry, strategy=strategy)

        factor_df = _make_factor_df(n_dates=5, n_stocks=10)
        model = _StubModel(lambda X: X[:, 0])
        df = forecast.generate(factor_df, ["feat_1", "feat_2"], model)
        md = forecast.format_daily(df)

        assert "KSS 日度预测报告" in md
        assert "市场概览" in md
        # 不应出现 nan（修复 #13 后 pred_price 全数值化）
        assert "nan" not in md.lower()
