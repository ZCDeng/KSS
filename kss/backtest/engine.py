"""Walk-forward 回测引擎."""

from __future__ import annotations

import logging
import os
import re
import warnings
from typing import TYPE_CHECKING, Any, Callable, Literal

import lightgbm as lgb
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from kss.backtest.cost_model import CostModel
from kss.backtest.metrics import Metrics
from kss.features.pipeline import FactorPipeline

if TYPE_CHECKING:
    from kss.backtest.cost_model import ExecutionModel

logger = logging.getLogger(__name__)

warnings.filterwarnings("ignore")

# 匹配 future_return_<N>d 形式的 label 列，提取预测周期 N（用于 purge gap）
_LABEL_PATTERN = re.compile(r"^future_return_(\d+)d$")

# Matplotlib 中文字体配置
plt.rcParams["font.sans-serif"] = ["Arial Unicode MS", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False


class BacktestEngine:
    """Walk-forward 滚动训练回测引擎.

    支持：
    - LightGBM 滚动训练 / 预测
    - 纯多头 Top-Pct 选股
    - 换手率与交易成本建模
    - 绩效可视化
    """

    def __init__(self, cost_model: CostModel) -> None:
        """初始化回测引擎.

        Args:
            cost_model: 交易成本模型实例.
        """
        self.cost_model = cost_model

    # ------------------------------------------------------------------ #
    # 模型训练
    # ------------------------------------------------------------------ #

    @staticmethod
    def _split_train_valid(
        train_df: pd.DataFrame,
        valid_pct: float = 0.2,
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        """按时间将训练集切分为 train / valid 两部分.

        取尾部 ``valid_pct`` 个 unique ``trade_date`` 作为验证集，避免在 symbol-major
        拼接的 ``train_df`` 上做 80/20 行号切分时实际是按股票（而非按时间）切。
        若 unique trade_date < 2 则验证集为空（退化到无早停训练）。

        Args:
            train_df: 训练数据 DataFrame（需含 ``trade_date`` 列）.
            valid_pct: 验证集占用的交易日比例（按交易日数算，非按行数算）.

        Returns:
            ``(train_part, valid_part)`` 元组.
        """
        sorted_df = train_df.sort_values(["trade_date", "symbol"])
        unique_dates = sorted(sorted_df["trade_date"].unique())
        if len(unique_dates) < 2:
            return sorted_df, sorted_df.iloc[0:0]
        n_valid_dates = max(1, int(len(unique_dates) * valid_pct))
        valid_dates = set(unique_dates[-n_valid_dates:])
        valid_mask = sorted_df["trade_date"].isin(valid_dates)
        return sorted_df.loc[~valid_mask].copy(), sorted_df.loc[valid_mask].copy()

    @staticmethod
    def _train_model(
        train_df: pd.DataFrame,
        feature_cols: list[str],
        label_col: str,
        random_state: int = 42,
        model_type: Literal["regression", "ranker"] = "regression",
        *,
        sample_weight: np.ndarray | pd.Series | None = None,
    ) -> Any:
        """使用 LightGBM 训练模型（按时间切验证集进行早停）.

        验证集由 :meth:`_split_train_valid` 按 ``trade_date`` 尾部切出，保证时间
        顺序，不会因 symbol-major 拼接把验证集退化成按股票分组。

        Args:
            train_df: 训练数据（必须含 ``trade_date`` 列；ranker 模式下用作 group key）.
            feature_cols: 特征列名列表.
            label_col: 目标变量列名.
            random_state: 随机种子.
            model_type: ``"regression"``（默认，返回 ``lgb.Booster``）或
                ``"ranker"``（返回 :class:`LightGBMRanker` 实例，predict 接口同构）.
            sample_weight: 与 ``train_df`` **同长度同顺序**的样本权重；``None`` = 等权
                （默认，向后兼容）. 内部会按 :meth:`_split_train_valid` 切出 train_part /
                valid_part 后，用对应行号子集（``train_part.index`` / ``valid_part.index``）
                取权重 —— 因此 ``train_df`` 的 index 必须能直接索引到 ``sample_weight``.
                调用方（``walk_forward``）保证 ``sample_weight`` 是 numpy 数组且与
                ``train_df.reset_index(drop=True)`` 的 0..N-1 顺序对齐.

        Returns:
            训练好的模型对象（Booster 或 LightGBMRanker）；两者都实现 ``predict(X) -> ndarray``.
        """
        if model_type == "ranker":
            return BacktestEngine._train_ranker(
                train_df, feature_cols, label_col, random_state,
                sample_weight=sample_weight,
            )

        # 把 sample_weight 投到 train_df 的 positional index 上，
        # 这样 _split_train_valid 切完后能用 positional 取对应权重.
        sw_arr: np.ndarray | None = None
        if sample_weight is not None:
            sw_arr = np.asarray(
                sample_weight.values
                if isinstance(sample_weight, pd.Series)
                else sample_weight
            ).reshape(-1).astype(float)
            if len(sw_arr) != len(train_df):
                raise ValueError(
                    "sample_weight 长度必须与 train_df 一致："
                    f"sample_weight={len(sw_arr)}, train_df={len(train_df)}"
                )

        # _split_train_valid 内部会做 sort_values，所以这里给 train_df 加一个
        # positional 列 ``_sw_pos`` 来保留权重映射；切完后用它取 sw_arr.
        if sw_arr is not None:
            train_df = train_df.copy()
            train_df["__sw_pos__"] = np.arange(len(train_df))

        train_part, valid_part = BacktestEngine._split_train_valid(
            train_df, valid_pct=0.2
        )

        X_train = train_part[feature_cols].values
        y_train = train_part[label_col].values
        sw_train: np.ndarray | None = None
        if sw_arr is not None:
            sw_train = sw_arr[train_part["__sw_pos__"].values]

        params: dict[str, Any] = {
            "objective": "regression",
            "metric": "rmse",
            "boosting_type": "gbdt",
            "num_leaves": 31,
            "learning_rate": 0.05,
            "feature_fraction": 0.8,
            "bagging_fraction": 0.8,
            "bagging_freq": 5,
            "verbose": -1,
            "random_state": random_state,
        }

        train_data = lgb.Dataset(X_train, y_train, weight=sw_train)
        valid_sets: list[lgb.Dataset] = [train_data]
        valid_names: list[str] = ["train"]
        callbacks: list[Any] = []

        if len(valid_part) > 0:
            X_valid = valid_part[feature_cols].values
            y_valid = valid_part[label_col].values
            sw_valid: np.ndarray | None = None
            if sw_arr is not None:
                sw_valid = sw_arr[valid_part["__sw_pos__"].values]
            valid_data = lgb.Dataset(
                X_valid, y_valid, reference=train_data, weight=sw_valid,
            )
            valid_sets.append(valid_data)
            valid_names.append("valid")
            callbacks.append(lgb.early_stopping(stopping_rounds=50, verbose=False))

        model = lgb.train(
            params,
            train_data,
            num_boost_round=500,
            valid_sets=valid_sets,
            valid_names=valid_names,
            callbacks=callbacks,
        )
        return model

    @staticmethod
    def _train_ranker(
        train_df: pd.DataFrame,
        feature_cols: list[str],
        label_col: str,
        random_state: int = 42,
        *,
        sample_weight: np.ndarray | pd.Series | None = None,
    ) -> Any:
        """使用 LightGBMRanker (lambdarank) 训练排序模型.

        与 :meth:`_train_model` 的 MSE 路径并列；调用方通过 ``model_type="ranker"`` 选择.
        延迟导入 :class:`LightGBMRanker` 避免循环依赖.

        Args:
            train_df: 训练数据（必须含 ``trade_date``，用于构造 group）.
            feature_cols: 特征列名.
            label_col: 目标列名.
            random_state: 随机种子.
            sample_weight: 与 ``train_df`` 同长度同顺序的样本权重；``None`` = 等权（默认）.
                经由 :meth:`_split_train_valid` 切分后按 positional 索引透传给
                :class:`LightGBMRanker.fit` 的 ``sample_weight`` / 验证集省略.

        Returns:
            训练好的 :class:`LightGBMRanker` 实例（已 fit）.
        """
        from kss.models.lightgbm_ranker import LightGBMRanker  # 延迟导入

        # 同 _train_model：用 __sw_pos__ 把权重的 positional 映射保留过 _split_train_valid
        sw_arr: np.ndarray | None = None
        if sample_weight is not None:
            sw_arr = np.asarray(
                sample_weight.values
                if isinstance(sample_weight, pd.Series)
                else sample_weight
            ).reshape(-1).astype(float)
            if len(sw_arr) != len(train_df):
                raise ValueError(
                    "sample_weight 长度必须与 train_df 一致："
                    f"sample_weight={len(sw_arr)}, train_df={len(train_df)}"
                )
            train_df = train_df.copy()
            train_df["__sw_pos__"] = np.arange(len(train_df))

        train_part, valid_part = BacktestEngine._split_train_valid(
            train_df, valid_pct=0.2
        )
        ranker = LightGBMRanker(
            params={"random_state": random_state, "verbose": -1},
            num_boost_round=500,
            early_stopping_rounds=50,
        )

        sw_train = (
            sw_arr[train_part["__sw_pos__"].values] if sw_arr is not None else None
        )
        if len(valid_part) > 0:
            ranker.fit(
                train_part[feature_cols].values,
                train_part[label_col].values,
                trade_dates=train_part["trade_date"].values,
                valid_X=valid_part[feature_cols].values,
                valid_y=valid_part[label_col].values,
                valid_dates=valid_part["trade_date"].values,
                sample_weight=sw_train,
            )
        else:
            ranker.fit(
                train_part[feature_cols].values,
                train_part[label_col].values,
                trade_dates=train_part["trade_date"].values,
                sample_weight=sw_train,
            )
        return ranker

    # ------------------------------------------------------------------ #
    # Walk-forward 回测
    # ------------------------------------------------------------------ #

    def walk_forward(
        self,
        factor_df: pd.DataFrame,
        feature_cols: list[str],
        label_col: str,
        train_window: int = 120,
        retrain_freq: int = 5,
        top_pct: float = 0.2,
        min_train: int = 300,
        min_test: int = 30,
        min_stocks: int = 10,
        normalize: bool = True,
        winsorize: float | None = None,
        feature_selector: Callable[[pd.DataFrame], list[str]] | None = None,
        *,
        neutralize: bool = False,
        neutralize_by_industry: bool = True,
        neutralize_by_log_mv: bool = True,
        industry_col: str = "industry",
        log_mv_col: str = "log_mv",
        model_type: Literal["regression", "ranker"] = "regression",
        execution: "ExecutionModel | None" = None,
        sample_weight_decay: float = 0.0,
    ) -> pd.DataFrame | None:
        """执行 Walk-forward 纯多头回测.

        逻辑：
        1. 按交易日排序，训练窗口 ``train_window`` 日，**尾部 ``period_n`` 天 purge**
           （``period_n`` 从 ``label_col`` 解析），避免 ``future_return_Nd`` 标签穿透到测试期；
        2. 在接下来 ``retrain_freq`` 个交易日中，每日用模型打分，
           选出 Top ``top_pct`` 股票构建等权组合；
        3. 计算组合次日收益，并根据换手率扣除交易成本；
        4. 滑动窗口，重复步骤 1~3.

        Args:
            factor_df: 包含因子、目标变量、``trade_date``、``symbol`` 的 DataFrame.
            feature_cols: 模型使用的特征列名列表.
            label_col: 训练标签列名，必须形如 ``future_return_Nd``（如 ``future_return_5d``），
                N 用于计算 purge gap.
            train_window: 训练窗口长度（交易日数），默认 120.
            retrain_freq: 重训练频率（每 N 个交易日重训一次），默认 5.
            top_pct: 选股比例（只做多前 top_pct），默认 0.2（20%）.
            min_train: 训练集最小样本数，默认 300.
            min_test: 测试集最小样本数，默认 30.
            min_stocks: 单日最小股票数，低于此值跳过该日，默认 10.
            normalize: 是否在每个 train/test 窗口内独立做截面 Z-Score 标准化，
                默认 True. 关闭时由调用方自行确保特征已标准化.
            winsorize: 截面 Z-Score 后的离群截断阈值（σ）；``None`` = 关闭（默认，保留
                旧行为）；典型值 ``3.0``. 仅在 ``normalize=True`` 时生效.
            feature_selector: 可选回调，签名 ``(train_df) -> list[str]``，每次 retrain 时
                调用，仅用**当窗历史训练集**返回的因子子集训练 LightGBM. 防 look-ahead 选因子；
                返回的因子必须是 ``feature_cols`` 的子集. ``None``（默认）保持向后兼容.
            neutralize: 是否对每个 train/test 窗口做行业 + 市值中性化（默认 False）.
                启用时 panel 必须含 ``industry_col`` / ``log_mv_col``. 中性化在 z-score 之前.
            neutralize_by_industry / neutralize_by_log_mv: 控制中性化两个维度的开关.
            industry_col / log_mv_col: 中性化用的列名（与 :meth:`FactorPipeline.neutralize` 一致）.
            model_type: ``"regression"``（默认，MSE）或 ``"ranker"``（lambdarank）.
                Ranker 在弱 IC 信号下显著优于 MSE（见 :class:`LightGBMRanker` 实证测试）.
            execution: 可选 :class:`~kss.backtest.cost_model.ExecutionModel`. 启用后：
                1. 选 Top Pct 后剔除买入侧涨停股；
                2. 单股权重按 ``max_tradable_ratio`` 缩减（部分成交）；
                3. 成本叠加开盘滑点 ``slippage_cost``.
                启用要求 panel 含 ``pre_close``/``open``/``amount`` 列。
            sample_weight_decay: exp-decay 半衰期参数. ``0.0``（默认）= 等权（行为不变，
                与旧版完全等价；测试用 monkeypatch spy 拦截 ``_train_model`` 也不受影响）.
                ``> 0`` 时每个 retrain 窗口按 ``exp(-decay × age_in_days)`` 加权训练样本，
                ``age`` 为相对当窗 train_df 最末一日的天数（远样本权重低）.
                典型值 ``0.005``（半衰期 ~140 天）；DDG-DA 轻量版概念漂移加权.
                必须用 walk-forward 选 decay，否则触发 hyperparam in-sample bias.

        Returns:
            回测结果 DataFrame，列为：
            ``trade_date``, ``gross_return``, ``net_return``,
            ``turnover``, ``cost``, ``n_stocks``, ``n_kept``.
            若无效结果过多则返回 ``None``.

        Raises:
            ValueError: ``label_col`` 不符合 ``future_return_Nd`` 格式时抛出.
        """
        # 从 label_col 中解析预测周期 N，用于 purge gap
        # 训练区间尾部 N 天的 future_return_Nd = close.shift(-N) 会窥见测试期收益
        match = _LABEL_PATTERN.match(label_col)
        if match is None:
            raise ValueError(
                f"label_col 必须形如 'future_return_Nd'（例：future_return_5d），收到: {label_col}"
            )
        period_n = int(match.group(1))

        dates = sorted(factor_df["trade_date"].unique())
        results: list[dict[str, Any]] = []
        # 全市场每日打分面板（不只 Top），供 SignalDiagnostics 消费.
        # 注意：仅含测试期日，与 results 共用一套 retrain 循环，O(1) 额外内存压力.
        panel_records: list[pd.DataFrame] = []
        prev_holdings: set[str] = set()

        logger.info(
            "Walk-forward: 总交易日 %d, 训练窗口 %d, 重训频率 %d, purge gap %d",
            len(dates),
            train_window,
            retrain_freq,
            period_n,
        )

        for i in range(train_window, len(dates), retrain_freq):
            # Purge gap：训练区间排除尾部 period_n 天
            train_dates_full = dates[i - train_window : i]
            purge_end = max(0, len(train_dates_full) - period_n)
            train_dates = train_dates_full[:purge_end]
            test_dates = dates[i : min(i + retrain_freq, len(dates))]

            if not train_dates:
                continue

            train_df = factor_df[factor_df["trade_date"].isin(train_dates)]
            test_df = factor_df[factor_df["trade_date"].isin(test_dates)]

            # ---- 动态特征选择（防 look-ahead 选因子）---- #
            # feature_selector 接收当窗 train_df，返回因子子集.
            # 默认 None 时使用 feature_cols 全集，行为与旧版一致.
            if feature_selector is not None:
                # selector 只能访问 train_df，未来数据不可见.
                # 为防止 selector 误用 ``next_day_return`` / 当窗 future_return_Nd
                # 之外的 leakage，约定 selector 自己负责防 look-ahead.
                selected = list(feature_selector(train_df))
                if not selected:
                    continue  # selector 给空集，本窗跳过
                # 必须是候选 superset 的子集
                bad = set(selected) - set(feature_cols)
                if bad:
                    raise ValueError(
                        f"feature_selector 返回非候选列: {bad}; "
                        f"必须是 feature_cols 子集"
                    )
                window_features = selected
            else:
                window_features = list(feature_cols)

            # 仅对必要列 dropna —— train 需 feature+label，test 仅需 feature+next_day_return.
            # 旧实现的 .dropna() 不指定 subset 会因任何列（包括其它周期的 future_return_Nd）
            # 的 NaN 把测试期尾部 N 天整段悄悄丢掉。
            train_df = train_df.dropna(subset=window_features + [label_col])
            test_df = test_df.dropna(subset=window_features + ["next_day_return"])

            if len(train_df) < min_train or len(test_df) < min_test:
                continue

            # 行业 + 市值中性化（在 cs_normalize 之前；按窗独立做，防 look-ahead）.
            # 注意：neutralize 替换 feature_cols 列为残差，仍保持原列名，所以下游
            # cs_normalize / 模型训练代码无感知.
            if neutralize:
                train_df = FactorPipeline.neutralize(
                    train_df, window_features,
                    by_industry=neutralize_by_industry,
                    by_log_mv=neutralize_by_log_mv,
                    industry_col=industry_col,
                    log_mv_col=log_mv_col,
                )
                test_df = FactorPipeline.neutralize(
                    test_df, window_features,
                    by_industry=neutralize_by_industry,
                    by_log_mv=neutralize_by_log_mv,
                    industry_col=industry_col,
                    log_mv_col=log_mv_col,
                )

            # 截面标准化按窗口独立计算，不依赖外层对整张面板的预处理（结构性防 look-ahead）
            if normalize:
                train_df = FactorPipeline.cs_normalize(
                    train_df, window_features, winsorize=winsorize
                )
                test_df = FactorPipeline.cs_normalize(
                    test_df, window_features, winsorize=winsorize
                )

            # 每窗 sample_weight：默认 None（等权，向后兼容）；> 0 时按 exp-decay 算.
            # age = 当窗最末日 - 该样本日；exp(-decay × age) → 远样本权重小.
            sample_weight: np.ndarray | None = None
            if sample_weight_decay > 0:
                # train_df 已 dropna 完，索引可能不连续；用 positional ndarray 避免后续切错位.
                anchor = train_df["trade_date"].max()
                ages = (anchor - train_df["trade_date"]).dt.days.values.astype(float)
                sample_weight = np.exp(-sample_weight_decay * ages)

            # 默认 regression + 等权路径与旧调用签名严格一致（不传任何额外 kwarg），
            # 保持外部 monkeypatch / spy 测试兼容性.
            if model_type == "regression" and sample_weight is None:
                model = self._train_model(train_df, window_features, label_col)
            elif sample_weight is None:
                model = self._train_model(
                    train_df, window_features, label_col, model_type=model_type,
                )
            else:
                model = self._train_model(
                    train_df, window_features, label_col,
                    model_type=model_type, sample_weight=sample_weight,
                )

            for test_date in test_dates:
                day_df = test_df[test_df["trade_date"] == test_date].copy()
                if len(day_df) < min_stocks:
                    continue

                # 预测打分 & 排名（用当窗实际训练特征，而非候选 superset）
                day_df["pred_score"] = model.predict(day_df[window_features].values)
                day_df["daily_rank"] = day_df["pred_score"].rank(pct=True)

                # 收集全市场打分面板（不限 Top），供后续 IC / 分位数诊断使用.
                # 列保持最小集合：trade_date / symbol / pred_score / next_day_return /
                # label，避免攒下整张 factor 表造成内存爆.
                panel_cols = ["trade_date", "symbol", "pred_score", "next_day_return"]
                if label_col in day_df.columns:
                    panel_cols.append(label_col)
                panel_records.append(day_df[panel_cols].copy())

                # 选出 Top 组合
                top_mask = day_df["daily_rank"] >= (1 - top_pct)

                # ---- execution：选 Top 后剔除买入侧涨停股 ---- #
                # 不足 min_stocks 时降级到"扩到当日全可成交股票再取等比例 Top"，
                # 与 factor_cross_section_backtest 同口径.
                n_tradable_pre: int | None = None
                n_tradable_post: int | None = None
                if execution is not None:
                    n_tradable_pre = int(top_mask.sum())
                    top_df = day_df.loc[top_mask]
                    tradable_top = execution.filter_tradable(
                        top_df, side="buy", symbol_col="symbol",
                    )
                    n_tradable_post = int(len(tradable_top))
                    if n_tradable_post < min_stocks:
                        # 降级路径
                        tradable_all = execution.filter_tradable(
                            day_df, side="buy", symbol_col="symbol",
                        )
                        if len(tradable_all) >= min_stocks:
                            n_take = max(
                                min_stocks, int(round(len(tradable_all) * top_pct))
                            )
                            tradable_top = tradable_all.nlargest(n_take, "pred_score")
                            n_tradable_post = int(len(tradable_top))
                        else:
                            # 当日无足够可成交股票 → 跳过该日
                            continue
                    top_mask = pd.Series(
                        day_df.index.isin(tradable_top.index), index=day_df.index,
                    )

                top_stocks = set(day_df.loc[top_mask, "symbol"].tolist())

                # 双边换手率：sells 对应 prev_holdings 分母，buys 对应 top_stocks 分母.
                # 旧实现 ``turnover = 1 - kept/|prev|`` 只算卖出端，配合 ``cost = turnover ×
                # (buy+sell)`` 在 |prev| == |top| 时碰巧对，但 universe 大小变化（新股上市/
                # 退市/特征过滤）会让买入端被错误计费。
                if prev_holdings:
                    kept = len(prev_holdings & top_stocks)
                    sell_turnover = (
                        (len(prev_holdings) - kept) / len(prev_holdings)
                    )
                    buy_turnover = (
                        (len(top_stocks) - kept) / len(top_stocks)
                        if top_stocks
                        else 0.0
                    )
                else:
                    # 首日建仓：只买不卖，避免老实现 turnover=1.0 时被错算了一笔 sell_cost
                    kept = 0
                    sell_turnover = 0.0
                    buy_turnover = 1.0 if top_stocks else 0.0

                # 组合毛收益：execution 启用时按 max_tradable_ratio 调权重（部分成交）.
                top_rows = day_df.loc[top_mask]
                if execution is not None and len(top_rows) > 0:
                    n_top = len(top_rows)
                    per_position = 1.0 / n_top
                    ratios = top_rows.apply(
                        lambda r: execution.max_tradable_ratio(r, per_position),
                        axis=1,
                    ).astype(float)
                    weights = (
                        ratios / ratios.sum() if ratios.sum() > 0 else ratios
                    )
                    portfolio_return = float(
                        (top_rows["next_day_return"] * weights).sum()
                    )
                else:
                    # 等权
                    portfolio_return = day_df.loc[top_mask, "next_day_return"].mean()

                # 双边成本：sell 与 buy 各自乘自家"总单边成本"（含 CostModel 滑点）.
                # execution 启用时再叠加开盘冲击成本（slippage_cost）.
                cost = (
                    sell_turnover * self.cost_model.sell_total
                    + buy_turnover * self.cost_model.buy_total
                )
                if execution is not None:
                    cost += execution.slippage_cost(buy_turnover + sell_turnover)
                net_return = portfolio_return - cost

                row_record: dict[str, Any] = {
                    "trade_date": test_date,
                    "gross_return": portfolio_return,
                    "net_return": net_return,
                    "turnover": sell_turnover,       # 兼容旧列：卖出端换手率（plot_results 读取）
                    "buy_turnover": buy_turnover,    # 新列：买入端换手率
                    "cost": cost,
                    "n_stocks": int(top_mask.sum()),
                    "n_kept": int(kept),
                }
                if execution is not None:
                    row_record["n_tradable_pre"] = n_tradable_pre
                    row_record["n_tradable_post"] = n_tradable_post
                results.append(row_record)

                prev_holdings = top_stocks

            if results and len(results) % 50 == 0:
                logger.info(
                    "  已处理 %d 个交易日, 当前: %s",
                    len(results),
                    test_dates[-1],
                )

        if not results:
            return None
        result_df = pd.DataFrame(results)
        # 通过 attrs 暴露面板（pandas 1.x+），不改主返回结构、不影响旧调用方.
        # 注意：attrs 在某些 pandas 操作下不会保留（如 merge / concat），消费方
        # 应在 walk_forward 返回后立即读取或显式拷贝.
        if panel_records:
            result_df.attrs["panel"] = pd.concat(panel_records, ignore_index=True)
        result_df.attrs["label_col"] = label_col
        result_df.attrs["period_n"] = period_n
        return result_df

    # ------------------------------------------------------------------ #
    # 可视化
    # ------------------------------------------------------------------ #

    def plot_results(
        self,
        results_df: pd.DataFrame,
        period: int,
        output_path: str,
        *,
        ic_series: pd.Series | None = None,
        quantile_df: pd.DataFrame | None = None,
        benchmark_returns: pd.Series | None = None,
        ic_decay_df: pd.DataFrame | None = None,
    ) -> None:
        """绘制回测结果图（4 或 8 面板）.

        基础四面板（兼容旧调用）：

        1. 累计收益曲线（毛收益 vs 净收益）
        2. 累积交易成本
        3. 日收益分布直方图
        4. 换手率序列

        诊断扩展四面板（任一关键字参数提供时启用）：

        5. 策略 vs 基准累计净值（含超额阴影）
        6. 60 日滚动夏普 + 60 日滚动 IC
        7. 5 分位累计收益 + 多空价差
        8. IC 直方图 + IC 衰减条形图

        Args:
            results_df: :meth:`walk_forward` 返回的结果 DataFrame.
            period: 模型预测周期（用于图表标题）.
            output_path: 图片保存路径.
            ic_series: 日度 IC 序列；提供则启用 panel 5-8 中的 IC 相关绘图.
            quantile_df: 分位数日收益 DataFrame；提供则启用分位累计曲线.
            benchmark_returns: 与 ``results_df`` 日历对齐的基准日收益；
                提供则启用基准对比面板.
            ic_decay_df: ``SignalDiagnostics.ic_decay`` 返回的 horizon DataFrame.
        """
        extended = any(
            x is not None
            for x in (ic_series, quantile_df, benchmark_returns, ic_decay_df)
        )

        if extended:
            fig, axes = plt.subplots(4, 2, figsize=(16, 22))
        else:
            fig, axes = plt.subplots(2, 2, figsize=(16, 12))

        dates = results_df["trade_date"]

        # 1. 累计收益
        cum_gross = (1 + results_df["gross_return"]).cumprod() - 1
        cum_net = (1 + results_df["net_return"]).cumprod() - 1
        axes[0, 0].plot(dates, cum_gross, label="毛收益（未扣成本）", lw=2)
        axes[0, 0].plot(dates, cum_net, label="净收益（扣成本）", lw=2)
        axes[0, 0].set_title(f"累计收益 ({period}日模型, 纯多头, 每周调仓)")
        axes[0, 0].legend()
        axes[0, 0].grid(True, alpha=0.3)

        # 2. 累积交易成本
        axes[0, 1].plot(dates, results_df["cost"].cumsum(), label="累积交易成本", color="red")
        axes[0, 1].set_title("累积交易成本")
        axes[0, 1].legend()
        axes[0, 1].grid(True, alpha=0.3)

        # 3. 日收益分布
        axes[1, 0].hist(results_df["gross_return"].dropna(), bins=30, alpha=0.6, label="毛收益")
        axes[1, 0].hist(results_df["net_return"].dropna(), bins=30, alpha=0.6, label="净收益")
        axes[1, 0].set_title("日收益分布")
        axes[1, 0].legend()

        # 4. 换手率
        avg_turnover = results_df["turnover"].mean()
        axes[1, 1].plot(dates, results_df["turnover"], label="换手率", color="orange")
        axes[1, 1].axhline(
            y=avg_turnover,
            color="red",
            ls="--",
            label=f"平均:{avg_turnover:.1%}",
        )
        axes[1, 1].set_title("换手率")
        axes[1, 1].legend()
        axes[1, 1].grid(True, alpha=0.3)

        if extended:
            # 5. 策略 vs 基准累计净值
            ax5 = axes[2, 0]
            if benchmark_returns is not None and not benchmark_returns.empty:
                cum_b = (1 + benchmark_returns.values).cumprod() - 1
                ax5.plot(dates, cum_net, label="策略净收益", lw=2)
                ax5.plot(dates, cum_b, label="基准", lw=2, color="gray")
                excess = cum_net.values - cum_b
                ax5.fill_between(
                    dates, cum_b, cum_b + excess,
                    where=excess >= 0, alpha=0.2, color="green", label="超额（正）"
                )
                ax5.fill_between(
                    dates, cum_b, cum_b + excess,
                    where=excess < 0, alpha=0.2, color="red", label="超额（负）"
                )
                ax5.set_title("策略 vs 基准 累计净值")
                ax5.legend()
                ax5.grid(True, alpha=0.3)
            else:
                ax5.set_visible(False)

            # 6. 滚动 Sharpe + 滚动 IC
            ax6 = axes[2, 1]
            window = 60
            rolling_sharpe = (
                results_df["net_return"].rolling(window).mean()
                / results_df["net_return"].rolling(window).std()
                * (252 ** 0.5)
            )
            ax6.plot(dates, rolling_sharpe, label=f"{window}日滚动夏普", color="C0")
            ax6.set_ylabel("Sharpe", color="C0")
            ax6.grid(True, alpha=0.3)
            ax6.set_title(f"{window}日滚动稳定性")
            if ic_series is not None and not ic_series.empty:
                ax6b = ax6.twinx()
                rolling_ic = ic_series.rolling(window).mean()
                ax6b.plot(
                    rolling_ic.index, rolling_ic.values,
                    label=f"{window}日滚动 IC", color="C1", alpha=0.7,
                )
                ax6b.axhline(y=0, color="red", ls="--", alpha=0.3)
                ax6b.set_ylabel("IC", color="C1")
            ax6.legend(loc="upper left")

            # 7. 分位数累计收益 + 多空价差
            ax7 = axes[3, 0]
            if quantile_df is not None and not quantile_df.empty:
                for col in quantile_df.columns:
                    cum_q = (1 + quantile_df[col].fillna(0)).cumprod() - 1
                    ax7.plot(quantile_df.index, cum_q, label=col, alpha=0.7)
                ax7.set_title("分位数累计收益")
                ax7.legend(ncol=2, fontsize=8)
                ax7.grid(True, alpha=0.3)
            else:
                ax7.set_visible(False)

            # 8. IC 直方图 + IC 衰减
            ax8 = axes[3, 1]
            if ic_series is not None and not ic_series.empty:
                ic_clean = ic_series.dropna()
                ax8.hist(ic_clean.values, bins=30, alpha=0.6, color="C1", label="IC 分布")
                ax8.axvline(x=ic_clean.mean(), color="red", ls="--",
                            label=f"均值: {ic_clean.mean():.3f}")
                ax8.axvline(x=0, color="black", ls=":", alpha=0.4)
                ax8.set_title("IC 直方图 + IC 衰减")
                ax8.legend(loc="upper left")
                ax8.grid(True, alpha=0.3)
                if ic_decay_df is not None and not ic_decay_df.empty:
                    # 副坐标：绘制 horizon → IC 均值条形图
                    ax8b = ax8.twinx()
                    ax8b.bar(
                        range(len(ic_decay_df)),
                        ic_decay_df["ic_mean"].values,
                        alpha=0.4, color="C2", width=0.6,
                    )
                    ax8b.set_xticks(range(len(ic_decay_df)))
                    ax8b.set_xticklabels(ic_decay_df["horizon"], rotation=45, fontsize=7)
                    ax8b.set_ylabel("IC by horizon", color="C2")
            else:
                ax8.set_visible(False)

        plt.tight_layout()
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        plt.savefig(output_path, dpi=150, bbox_inches="tight")
        plt.close()
        logger.info("图: %s", output_path)
