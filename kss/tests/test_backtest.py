"""Backtest 模块单元测试."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from kss.backtest.metrics import Metrics
from kss.backtest.cost_model import CostModel
from kss.backtest.engine import BacktestEngine


class TestMetrics:
    """Metrics 功能测试."""

    def test_calc_basic(self) -> None:
        """测试基础指标计算."""
        r = pd.Series([0.01, -0.005, 0.02, 0.015, -0.01])
        m = Metrics.calc(r)

        assert m["total"] > 0
        assert m["n"] == 5
        assert m["win"] == pytest.approx(0.6, abs=0.01)
        assert m["avg_daily"] == pytest.approx(r.mean())

    def test_calc_empty(self) -> None:
        """测试空序列返回空字典."""
        m = Metrics.calc(pd.Series([], dtype=float))
        assert m == {}

    def test_calc_all_negative(self) -> None:
        """测试全负收益场景."""
        r = pd.Series([-0.01, -0.02, -0.01])
        m = Metrics.calc(r)
        assert m["total"] < 0
        assert m["win"] == 0.0

    def test_format(self) -> None:
        """测试格式化输出非空."""
        r = pd.Series([0.01] * 10)
        m = Metrics.calc(r)
        text = Metrics.format(m)
        assert "总:" in text
        assert "年化:" in text
        assert "夏普:" in text

    def test_format_empty(self) -> None:
        """测试空字典格式化."""
        text = Metrics.format({})
        assert text == "无有效数据"


class TestCostModel:
    """CostModel 功能测试."""

    def test_default_costs(self) -> None:
        """测试默认费率."""
        cm = CostModel()
        assert cm.buy_cost == 0.001
        assert cm.sell_cost == 0.002

    def test_lateral_cost(self) -> None:
        """测试双边成本计算."""
        cm = CostModel(buy_cost=0.001, sell_cost=0.002)
        assert cm.lateral_cost() == pytest.approx(0.003)

    def test_apply(self) -> None:
        """测试成本扣除逻辑."""
        cm = CostModel(buy_cost=0.001, sell_cost=0.002)
        # 收益 1%，换手率 50%
        net = cm.apply(portfolio_return=0.01, turnover=0.5)
        expected = 0.01 - 0.5 * 0.003
        assert net == pytest.approx(expected)

    def test_apply_zero_turnover(self) -> None:
        """测试零换手率时净收益等于毛收益."""
        cm = CostModel()
        net = cm.apply(portfolio_return=0.02, turnover=0.0)
        assert net == pytest.approx(0.02)


class TestBacktestEngine:
    """BacktestEngine 功能测试."""

    def _make_factor_df(
        self, n_dates: int = 30, n_stocks: int = 5, seed: int = 42
    ) -> pd.DataFrame:
        """构造满足 walk_forward 要求的 dummy factor_df.

        Args:
            n_dates: 交易日数量.
            n_stocks: 股票数量.
            seed: 随机种子，保证 dummy 数据可复现.
        """
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
                    "future_return_5d": rng.standard_normal() * 0.02,
                    "next_day_return": rng.standard_normal() * 0.01,
                })
        return pd.DataFrame(records)

    def test_walk_forward_basic(self) -> None:
        """测试 Walk-forward 回测基本流程."""
        df = self._make_factor_df(n_dates=40, n_stocks=10)
        engine = BacktestEngine(cost_model=CostModel())
        result = engine.walk_forward(
            factor_df=df,
            feature_cols=["feat_1", "feat_2"],
            label_col="future_return_5d",
            train_window=10,
            retrain_freq=5,
            top_pct=0.3,
            min_train=20,
            min_test=5,
            min_stocks=3,
        )

        assert result is not None
        assert not result.empty
        assert "trade_date" in result.columns
        assert "gross_return" in result.columns
        assert "net_return" in result.columns
        assert "turnover" in result.columns
        assert "cost" in result.columns

    def test_walk_forward_insufficient_data(self) -> None:
        """测试数据不足时返回 None."""
        df = self._make_factor_df(n_dates=5, n_stocks=2)
        engine = BacktestEngine(cost_model=CostModel())
        result = engine.walk_forward(
            factor_df=df,
            feature_cols=["feat_1", "feat_2"],
            label_col="future_return_5d",
            train_window=10,
            retrain_freq=5,
            min_train=50,
        )
        assert result is None

    def test_walk_forward_returns_dataframe(self) -> None:
        """测试返回类型为 pd.DataFrame."""
        df = self._make_factor_df(n_dates=50, n_stocks=10)
        engine = BacktestEngine(cost_model=CostModel())
        result = engine.walk_forward(
            factor_df=df,
            feature_cols=["feat_1", "feat_2"],
            label_col="future_return_5d",
            train_window=15,
            retrain_freq=5,
            top_pct=0.3,
            min_train=30,
            min_test=5,
            min_stocks=3,
        )
        assert isinstance(result, pd.DataFrame)

    # ------------------------------------------------------------------ #
    # P0 #3 修复：_split_train_valid 按时间切验证集
    # ------------------------------------------------------------------ #

    def test_split_train_valid_by_time(self) -> None:
        """`_split_train_valid` 必须按 trade_date 切，而不是按行号切.

        修复 P0 #3：旧实现对 symbol-major 拼接的 train_df 做 X[:split] 行号切，
        实际验证集是按股票分组，与时间无关 —— 早停验证失效。
        """
        # 故意模拟 symbol-major 拼接：股票优先，日期次之
        dates = pd.date_range("2024-01-01", periods=10)
        rng = np.random.default_rng(0)
        records: list[dict] = []
        for s in range(5):
            for d in dates:
                records.append({
                    "trade_date": d,
                    "symbol": f"S{s}",
                    "feat_1": rng.standard_normal(),
                    "future_return_5d": rng.standard_normal(),
                })
        train_df = pd.DataFrame(records)

        train_part, valid_part = BacktestEngine._split_train_valid(
            train_df, valid_pct=0.2
        )

        # 验证集起始日必须严格晚于训练集结束日（不可有任何重叠）
        assert valid_part["trade_date"].min() > train_part["trade_date"].max(), (
            "时间切分失败：验证集与训练集存在日期重叠"
        )
        # 10 个唯一日 × 0.2 = 2 个验证日
        assert len(valid_part["trade_date"].unique()) == 2
        # 所有 symbol 应同时出现在 train 与 valid（按时间切，而非按 symbol 切）
        assert set(train_part["symbol"].unique()) == set(valid_part["symbol"].unique())

    def test_split_train_valid_degenerate_single_date(self) -> None:
        """单一交易日时退化：valid 为空，train 含全部行."""
        df = pd.DataFrame({
            "trade_date": pd.to_datetime(["2024-01-01", "2024-01-01"]),
            "symbol": ["A", "B"],
            "feat_1": [1.0, 2.0],
            "future_return_5d": [0.01, -0.01],
        })
        train_part, valid_part = BacktestEngine._split_train_valid(df, valid_pct=0.2)
        assert len(valid_part) == 0
        assert len(train_part) == len(df)

    # ------------------------------------------------------------------ #
    # P0 #1 修复：walk_forward purge gap
    # ------------------------------------------------------------------ #

    def test_walk_forward_purge_gap(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """walk_forward 的训练区间必须排除尾部 period_n 天.

        修复 P0 #1：``future_return_Nd = close.shift(-N)`` 意味着训练区间最后 N 天
        的 label 实际是测试期的收益。spy 拦截 _train_model 看每次实际拿到的 train_df。
        """
        df = self._make_factor_df(n_dates=40, n_stocks=10)
        engine = BacktestEngine(cost_model=CostModel())

        captured_train_max: list[pd.Timestamp] = []
        real_train = BacktestEngine._train_model

        def spy_train(
            train_df: pd.DataFrame,
            feature_cols: list[str],
            label_col: str,
            random_state: int = 42,
        ) -> Any:
            captured_train_max.append(train_df["trade_date"].max())
            return real_train(train_df, feature_cols, label_col, random_state)

        monkeypatch.setattr(BacktestEngine, "_train_model", staticmethod(spy_train))

        train_window = 15
        retrain_freq = 5
        period_n = 5  # label "future_return_5d" 蕴含的 N
        result = engine.walk_forward(
            factor_df=df,
            feature_cols=["feat_1", "feat_2"],
            label_col=f"future_return_{period_n}d",
            train_window=train_window,
            retrain_freq=retrain_freq,
            top_pct=0.3,
            min_train=10,
            min_test=5,
            min_stocks=3,
        )

        assert result is not None
        assert len(captured_train_max) > 0, "_train_model 至少应被调用一次"

        dates_sorted = sorted(df["trade_date"].unique())
        # 每次迭代 i = train_window + iter * retrain_freq；测试期始 = dates_sorted[i]
        for iter_idx, train_max in enumerate(captured_train_max):
            i = train_window + iter_idx * retrain_freq
            if i >= len(dates_sorted):
                break
            test_start = dates_sorted[i]
            train_max_pos = dates_sorted.index(train_max)
            gap = i - train_max_pos
            assert gap >= period_n, (
                f"第 {iter_idx} 次训练，train 末日距 test 始日仅 {gap} 天，"
                f"小于 period_n={period_n}：标签会穿透到测试期"
                f"（train_max={train_max}, test_start={test_start}）"
            )

    def test_walk_forward_invalid_label_col(self) -> None:
        """label_col 格式不符合 future_return_Nd 应抛 ValueError（防误传）."""
        df = self._make_factor_df(n_dates=40, n_stocks=10)
        engine = BacktestEngine(cost_model=CostModel())
        with pytest.raises(ValueError, match="future_return_Nd"):
            engine.walk_forward(
                factor_df=df,
                feature_cols=["feat_1", "feat_2"],
                label_col="my_custom_label",
                train_window=15,
                retrain_freq=5,
                min_train=10,
                min_test=5,
                min_stocks=3,
            )

    # ------------------------------------------------------------------ #
    # P0 #11（关联）：walk_forward 数值不变量
    # ------------------------------------------------------------------ #

    def test_walk_forward_net_return_invariant(self) -> None:
        """``net_return == gross_return - cost`` 与核心金融不变量.

        关联 P0 #11：旧测试只断言列名存在，无任何数值断言。本测试固化金融正确性
        的硬约束。
        """
        df = self._make_factor_df(n_dates=40, n_stocks=10)
        engine = BacktestEngine(cost_model=CostModel(buy_cost=0.001, sell_cost=0.002))
        result = engine.walk_forward(
            factor_df=df,
            feature_cols=["feat_1", "feat_2"],
            label_col="future_return_5d",
            train_window=15,
            retrain_freq=5,
            top_pct=0.3,
            min_train=10,
            min_test=5,
            min_stocks=3,
        )

        assert result is not None and not result.empty

        # 核心 P&L 公式
        diff = (result["net_return"] - (result["gross_return"] - result["cost"])).abs()
        assert (diff < 1e-12).all(), "net_return 必须严格等于 gross_return - cost"

        # 成本非负
        assert (result["cost"] >= 0).all(), "cost 必须 >= 0"

        # 双边换手率 ∈ [0, 1]
        assert ((result["turnover"] >= 0) & (result["turnover"] <= 1)).all()
        assert ((result["buy_turnover"] >= 0) & (result["buy_turnover"] <= 1)).all()

    def test_walk_forward_two_sided_cost_invariant(self) -> None:
        """双边成本不变量（修复 P1 #14）.

        旧实现：``cost = turnover × (buy + sell)``，``turnover = 1 - kept/|prev|`` 只算
        卖出端比例。当 ``|prev| == |top|`` 时碰巧等于双边成本，但 universe 大小变化
        即偏差。新实现明确双边：

            cost = sell_turnover × sell_cost + buy_turnover × buy_cost
        """
        df = self._make_factor_df(n_dates=40, n_stocks=10)
        engine = BacktestEngine(cost_model=CostModel(buy_cost=0.001, sell_cost=0.002))
        result = engine.walk_forward(
            factor_df=df,
            feature_cols=["feat_1", "feat_2"],
            label_col="future_return_5d",
            train_window=15,
            retrain_freq=5,
            top_pct=0.3,
            min_train=10,
            min_test=5,
            min_stocks=3,
        )

        assert result is not None and not result.empty
        assert "buy_turnover" in result.columns

        expected_cost = result["turnover"] * 0.002 + result["buy_turnover"] * 0.001
        diff = (result["cost"] - expected_cost).abs()
        assert (diff < 1e-12).all(), (
            "cost 必须严格等于 sell_turnover × sell_cost + buy_turnover × buy_cost"
        )

    def test_walk_forward_exposes_panel_attrs(self) -> None:
        """walk_forward 必须通过 result_df.attrs['panel'] 暴露全市场打分面板.

        修复 P1：诊断模块（IC / 分位数）需要每日全市场打分，不只 Top.
        attrs 是 pandas 1.x+ 标准的非破坏性元数据机制，零回归.
        """
        df = self._make_factor_df(n_dates=40, n_stocks=10)
        engine = BacktestEngine(cost_model=CostModel())
        result = engine.walk_forward(
            factor_df=df,
            feature_cols=["feat_1", "feat_2"],
            label_col="future_return_5d",
            train_window=15,
            retrain_freq=5,
            top_pct=0.3,
            min_train=10,
            min_test=5,
            min_stocks=3,
        )
        assert result is not None
        panel = result.attrs.get("panel")
        assert panel is not None, "result.attrs['panel'] 必须存在"
        assert not panel.empty
        # panel 必须含诊断所需的最小列
        required = {"trade_date", "symbol", "pred_score", "next_day_return"}
        assert required.issubset(set(panel.columns))
        # label_col / period_n 也要透传给消费方
        assert result.attrs.get("label_col") == "future_return_5d"
        assert result.attrs.get("period_n") == 5

    def test_walk_forward_winsorize_does_not_crash(self) -> None:
        """walk_forward(winsorize=3.0) 在正常 dummy 数据上不应崩."""
        df = self._make_factor_df(n_dates=40, n_stocks=10)
        engine = BacktestEngine(cost_model=CostModel())
        result = engine.walk_forward(
            factor_df=df,
            feature_cols=["feat_1", "feat_2"],
            label_col="future_return_5d",
            train_window=15,
            retrain_freq=5,
            top_pct=0.3,
            min_train=10,
            min_test=5,
            min_stocks=3,
            winsorize=3.0,
        )
        assert result is not None and not result.empty

    def test_walk_forward_first_day_no_sell_cost(self) -> None:
        """首日建仓只有买入端成本，不能被错收 sell_cost（修复 P1 #14 的 adversarial 子项）.

        旧实现 ``turnover = 1.0`` 让首日承担了 ``1.0 × (buy + sell)`` 的成本——
        但首日没有任何持仓可卖。新实现 ``sell_turnover=0, buy_turnover=1``。
        """
        df = self._make_factor_df(n_dates=40, n_stocks=10)
        engine = BacktestEngine(cost_model=CostModel(buy_cost=0.001, sell_cost=0.002))
        result = engine.walk_forward(
            factor_df=df,
            feature_cols=["feat_1", "feat_2"],
            label_col="future_return_5d",
            train_window=15,
            retrain_freq=5,
            top_pct=0.3,
            min_train=10,
            min_test=5,
            min_stocks=3,
        )

        assert result is not None and not result.empty

        # 首行：无任何前期持仓
        assert result["turnover"].iloc[0] == pytest.approx(0.0), \
            "首日 sell_turnover 必须为 0（没有持仓可卖）"
        assert result["buy_turnover"].iloc[0] == pytest.approx(1.0), \
            "首日 buy_turnover 必须为 1.0（全新建仓）"
        # 成本只含 buy_cost = 0.001，不再带 sell_cost = 0.002
        assert result["cost"].iloc[0] == pytest.approx(0.001), \
            "首日 cost 应只含 buy_cost，旧实现错收 sell_cost"


# ====================================================================== #
# Task #4.4 sample_weight 概念漂移加权（DDG-DA 轻量版）
# ====================================================================== #


class TestSampleWeight:
    """sample_weight 入口 + walk_forward(sample_weight_decay=...) 行为."""

    @staticmethod
    def _make_panel(n_dates: int = 40, n_stocks: int = 10, seed: int = 42) -> pd.DataFrame:
        rng = np.random.default_rng(seed)
        dates = pd.date_range("2024-01-01", periods=n_dates)
        records = []
        for d in dates:
            for s in range(n_stocks):
                records.append({
                    "trade_date": d,
                    "symbol": f"688{s:03d}.SH",
                    "feat_1": rng.standard_normal(),
                    "feat_2": rng.standard_normal(),
                    "future_return_5d": rng.standard_normal() * 0.02,
                    "next_day_return": rng.standard_normal() * 0.01,
                })
        return pd.DataFrame(records)

    def test_train_model_accepts_sample_weight(self) -> None:
        """``_train_model(sample_weight=...)`` 不抛错，并返回可 predict 的 model."""
        df = self._make_panel(n_dates=20, n_stocks=8)
        # 把 trade_date 集中起来构造一窗 train_df
        train_df = df.copy()
        n = len(train_df)
        rng = np.random.default_rng(0)
        sw = rng.uniform(0.1, 1.0, size=n)

        model = BacktestEngine._train_model(
            train_df, ["feat_1", "feat_2"], "future_return_5d",
            sample_weight=sw,
        )
        # 等同接口：predict(X) -> ndarray
        pred = model.predict(train_df[["feat_1", "feat_2"]].values)
        assert len(pred) == n

    def test_train_model_default_sample_weight_is_none_backward_compat(self) -> None:
        """``_train_model(...)`` 默认不传 sample_weight 时行为完全等价旧版（确定性）."""
        df = self._make_panel(n_dates=20, n_stocks=8)
        m1 = BacktestEngine._train_model(
            df.copy(), ["feat_1", "feat_2"], "future_return_5d", random_state=42,
        )
        m2 = BacktestEngine._train_model(
            df.copy(), ["feat_1", "feat_2"], "future_return_5d", random_state=42,
            sample_weight=None,
        )
        X = df[["feat_1", "feat_2"]].values
        # 同 seed 下 LightGBM 训练结果对 sample_weight=None 与 不传 应严格一致
        np.testing.assert_allclose(m1.predict(X), m2.predict(X), atol=1e-12)

    def test_train_model_extreme_weight_concentrates_on_one_row(self) -> None:
        """极端 sample_weight（仅 1 个非零）= 等价于只用那 1 个样本训练.

        原理：LGB 在 weight 矩阵 W 上做 ``\\sum_i w_i × L(y_i, f(x_i))``，
        其他权重为 0 → 完全等价从 X 中删掉对应行（早停在 valid 上也只看那 1 个样本）.
        弱断言：预测结果应非 NaN 且模型未崩.
        """
        df = self._make_panel(n_dates=20, n_stocks=8)
        n = len(df)
        sw = np.zeros(n, dtype=float)
        # 最后一行权重 1.0；其他全 0
        sw[-1] = 1.0
        # 给一点很小的非零给最后几行，让 valid 不全 0（避免 LGB 退化）
        sw[-5:] = 1.0

        model = BacktestEngine._train_model(
            df.copy(), ["feat_1", "feat_2"], "future_return_5d",
            sample_weight=sw,
        )
        pred = model.predict(df[["feat_1", "feat_2"]].values)
        assert pred.shape == (n,)
        assert np.isfinite(pred).all()

    def test_train_model_length_mismatch_raises(self) -> None:
        df = self._make_panel(n_dates=10, n_stocks=5)
        bad_sw = np.ones(len(df) - 3)
        with pytest.raises(ValueError, match="sample_weight"):
            BacktestEngine._train_model(
                df, ["feat_1", "feat_2"], "future_return_5d",
                sample_weight=bad_sw,
            )

    def test_train_ranker_accepts_sample_weight(self) -> None:
        df = self._make_panel(n_dates=20, n_stocks=10)
        n = len(df)
        rng = np.random.default_rng(0)
        sw = rng.uniform(0.1, 1.0, size=n)
        ranker = BacktestEngine._train_model(
            df, ["feat_1", "feat_2"], "future_return_5d",
            model_type="ranker", sample_weight=sw,
        )
        pred = ranker.predict(df[["feat_1", "feat_2"]].values)
        assert pred.shape == (n,)

    def test_walk_forward_decay_zero_equals_baseline(self) -> None:
        """``sample_weight_decay=0`` 与不传必须完全等价（向后兼容关键路径）."""
        df = self._make_panel(n_dates=40, n_stocks=10)
        engine = BacktestEngine(cost_model=CostModel())
        common = dict(
            factor_df=df,
            feature_cols=["feat_1", "feat_2"],
            label_col="future_return_5d",
            train_window=15, retrain_freq=5, top_pct=0.3,
            min_train=10, min_test=5, min_stocks=3,
        )
        res_default = engine.walk_forward(**common)
        res_decay0 = engine.walk_forward(**common, sample_weight_decay=0.0)
        assert res_default is not None and res_decay0 is not None
        # 同 seed + 同输入 + decay=0 → 数值严格一致
        pd.testing.assert_frame_equal(
            res_default.reset_index(drop=True),
            res_decay0.reset_index(drop=True),
        )

    def test_walk_forward_decay_positive_runs_and_panel_attrs_ok(self) -> None:
        """``sample_weight_decay > 0`` 跑通 + panel attrs 正常 + 输出 schema 不变."""
        df = self._make_panel(n_dates=40, n_stocks=10)
        engine = BacktestEngine(cost_model=CostModel())
        res = engine.walk_forward(
            factor_df=df, feature_cols=["feat_1", "feat_2"],
            label_col="future_return_5d",
            train_window=15, retrain_freq=5, top_pct=0.3,
            min_train=10, min_test=5, min_stocks=3,
            sample_weight_decay=0.005,
        )
        assert res is not None and not res.empty
        for col in ["trade_date", "gross_return", "net_return", "turnover",
                    "cost", "n_stocks", "n_kept"]:
            assert col in res.columns
        panel = res.attrs.get("panel")
        assert panel is not None and not panel.empty
        assert res.attrs.get("period_n") == 5

    def test_walk_forward_decay_with_ranker(self) -> None:
        """``sample_weight_decay`` 与 ``model_type='ranker'`` 联用不崩."""
        df = self._make_panel(n_dates=50, n_stocks=12)
        engine = BacktestEngine(cost_model=CostModel())
        res = engine.walk_forward(
            factor_df=df, feature_cols=["feat_1", "feat_2"],
            label_col="future_return_5d",
            train_window=20, retrain_freq=5, top_pct=0.3,
            min_train=15, min_test=5, min_stocks=3,
            model_type="ranker",
            sample_weight_decay=0.005,
        )
        assert res is not None and not res.empty
