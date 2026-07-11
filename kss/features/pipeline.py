"""因子管道 ——  orchestrates 多类因子生成、目标变量计算与截面标准化."""

from __future__ import annotations

import logging
from typing import Sequence

import numpy as np
import pandas as pd

from kss.features.technical import TechnicalFactors
from kss.features.volatility import VolatilityFactors
from kss.features.volume import VolumeFactors
from kss.features.valuation import ValuationFactors

try:
    import statsmodels.api as _sm
    _HAS_STATSMODELS = True
except ImportError:  # pragma: no cover - statsmodels 在项目 requirements 内
    _HAS_STATSMODELS = False

_logger = logging.getLogger(__name__)


# 目标变量周期
_TARGET_PERIODS: Sequence[int] = (5, 10, 20)


class FactorPipeline:
    """因子生成管道.

    负责将原始行情 DataFrame 转换为包含全部 49+ 因子的特征矩阵，
    并添加目标变量（未来收益）与进行截面标准化.
    """

    def __init__(self, df: pd.DataFrame) -> None:
        """初始化管道.

        Args:
            df: 原始行情 DataFrame，需包含
                ``trade_date``、``open``、``high``、``low``、``close``、``vol``
                以及可选的 ``amount``、``daily_basic`` 相关列.
        """
        self.df = df.copy()
        self._validate()

    def _validate(self) -> None:
        """检查必需的列是否存在."""
        required = {"trade_date", "open", "high", "low", "close", "vol"}
        missing = required - set(self.df.columns)
        if missing:
            raise ValueError(f"缺少必需列: {missing}")

    # ------------------------------------------------------------------ #
    # 主流程
    # ------------------------------------------------------------------ #

    def generate(self) -> pd.DataFrame:
        """生成完整因子矩阵（含 49+ 因子）.

        Returns:
            包含全部因子列的 DataFrame.
        """
        c = self.df["close"]
        h = self.df["high"]
        l = self.df["low"]
        v = self.df["vol"]
        o = self.df["open"]
        amt = self.df.get("amount", v * c)

        factors: dict[str, pd.Series] = {
            "trade_date": self.df["trade_date"],
            "close": c,
        }

        # 1. 技术因子
        factors.update(TechnicalFactors.momentum(c))
        factors.update(TechnicalFactors.moving_averages(c))
        factors.update(TechnicalFactors.macd(c))
        factors.update(TechnicalFactors.rsi(c))
        factors.update(TechnicalFactors.kdj(h, l, c))
        factors.update(TechnicalFactors.bollinger(c))
        factors.update(TechnicalFactors.candlestick(o, h, l, c))
        factors.update(TechnicalFactors.price_position(c, h, l))
        # MI 动量指标（通达信/tqsdk 口径）；默认 N=12，额外输出 6/20 供研究
        factors.update(TechnicalFactors.mi(c, periods=(6, 12, 20)))

        # 2. 波动率因子
        factors.update(VolatilityFactors.volatility(c))
        factors.update(VolatilityFactors.atr(h, l, c))

        # 3. 成交量因子
        factors.update(VolumeFactors.volume_ratio(v))
        factors.update(VolumeFactors.amount_ratio(amt))
        factors.update(VolumeFactors.price_volume_corr(c, v))

        # 4. 估值因子（透传原表数据）
        factors.update(ValuationFactors.from_daily_basic(self.df))

        # 回缺值填充：vr / turnover 若缺失则给默认值
        if "vr" not in factors:
            factors["vr"] = pd.Series(1.0, index=self.df.index)
        if "turnover" not in factors:
            factors["turnover"] = pd.Series(0.0, index=self.df.index)

        return pd.DataFrame(factors)

    def get_feature_cols(self, factor_df: pd.DataFrame) -> list[str]:
        """获取特征列名列表（排除非特征列）.

        Args:
            factor_df: 因子 DataFrame.

        Returns:
            特征列名字符串列表.
        """
        exclude = {
            "trade_date",
            "symbol",
            "close",
            "next_day_return",
        }
        exclude.update({f"future_return_{p}d" for p in _TARGET_PERIODS})
        return [c for c in factor_df.columns if c not in exclude]

    def add_targets(self, factor_df: pd.DataFrame, close: pd.Series) -> pd.DataFrame:
        """添加目标变量（未来收益与次日收益）.

        - ``future_return_Nd`` 是**训练标签**：基于 close-to-close，``close.shift(-N) / close - 1``。
          模型在 ``close[t]`` 截面信息上学习预测未来 N 天 close 的方向，与执行价无关。
        - ``next_day_return`` 是**回测期实盘可达收益**：``t`` 日 close 后选股，``t+1``
          开盘建仓，``t+2`` 开盘换仓，单日持仓收益 = ``open[t+2] / open[t+1] - 1``。
          因此 ``day-t`` 行的 ``next_day_return`` 表示"以该日选股、下一交易日开盘买入、
          再下一交易日开盘卖出"的可达收益。每只股票最后 2 行因此为 NaN（被 walk_forward
          的 dropna 自然丢弃）。

        Args:
            factor_df: 因子 DataFrame（行序需与 ``self.df`` 一致；
                即由本管道的 :meth:`generate` 产出）.
            close: 收盘价序列（用于计算 ``future_return_Nd`` 训练标签）.

        Returns:
            添加目标列后的 DataFrame 副本.
        """
        df = factor_df.copy()
        # 训练标签：close-to-close N 日收益
        for p in _TARGET_PERIODS:
            df[f"future_return_{p}d"] = close.shift(-p) / close - 1
        # 回测期实盘可达收益：open[t+1] → open[t+2]，避免同日决策同日执行
        open_ = self.df["open"]
        df["next_day_return"] = open_.shift(-2) / open_.shift(-1) - 1
        return df

    @staticmethod
    def cs_normalize(
        factor_df: pd.DataFrame,
        feature_cols: list[str],
        winsorize: float | None = None,
    ) -> pd.DataFrame:
        """截面 Z-Score 标准化 —— 按交易日分组去均值除标准差.

        对单股票截面日做防御性处理：pandas groupby std (ddof=1) 在单元素分组上
        返回 NaN，配合 ``+1e-8`` 仍是 NaN，原实现会把这部分静默写回特征列。
        本实现把 NaN 标准差视为 0，最终输出整体 ``fillna(0.0)`` 保证不再串 NaN/inf
        到下游模型。

        可选 winsorization：在 z-score 之后将极端值截断到 ``±winsorize`` 个 σ
        内。**默认关闭**（``winsorize=None``）以保证旧调用方零回归；上层（如
        ``walk_forward`` / CLI ``--winsorize`` flag）显式开启时才生效.

        Args:
            factor_df: 因子 DataFrame（可包含多只股票）.
            feature_cols: 需要标准化的特征列名列表.
            winsorize: 截断阈值（σ）；``None`` = 不截断；典型值 ``3.0``.

        Returns:
            标准化后的 DataFrame 副本.
        """
        df = factor_df.copy()
        for col in feature_cols:
            grouped = df.groupby("trade_date")[col]
            mean = grouped.transform("mean")
            # 单股票日 std=NaN → 当作 0；+1e-8 epsilon 仍能防止除零
            std = grouped.transform("std").fillna(0.0)
            z = (df[col] - mean) / (std + 1e-8)
            if winsorize is not None and winsorize > 0:
                # clip 到 ±winsorize σ，对离群值无声"软截断"
                z = z.clip(lower=-float(winsorize), upper=float(winsorize))
            # 残留 NaN（原 feature 本身缺失等）填 0，避免把 NaN 喂给模型
            df[col] = z.fillna(0.0)
        return df

    # ------------------------------------------------------------------ #
    # 行业 + 市值中性化
    # ------------------------------------------------------------------ #

    @staticmethod
    def neutralize(
        panel: pd.DataFrame,
        feature_cols: list[str],
        *,
        by_industry: bool = True,
        by_log_mv: bool = True,
        industry_col: str = "industry",
        log_mv_col: str = "log_mv",
        date_col: str = "trade_date",
        min_stocks_per_day: int = 5,
    ) -> pd.DataFrame:
        """因子行业 + 市值中性化.

        每个 ``trade_date`` 截面跑 OLS::

            factor_i = α + β_industry × industry_dummies + β_mv × log_mv + residual

        保留 ``residual`` 作为去中性化后的因子.

        Args:
            panel: 横截面面板（含 ``date_col``、``industry_col``（如启用）、
                ``log_mv_col``（如启用）与 ``feature_cols``）.
            feature_cols: 需要中性化的因子列名.
            by_industry: 是否对行业做 dummy 回归.
            by_log_mv: 是否对 ``log_mv`` 做线性回归.
            industry_col: 行业列名.
            log_mv_col: 对数市值列名.
            date_col: 交易日列名.
            min_stocks_per_day: 单日股票少于此数则跳过中性化（避免 OLS
                自由度不足），当日的 ``feature_cols`` 保留原值.

        Returns:
            DataFrame 副本，``feature_cols`` 列已被 residual 替换；其余列不变.

        Raises:
            ValueError: 必需列缺失.

        实现要点：
          - 优先用 ``statsmodels`` 跑 OLS（项目已装）；缺失时 fallback 到 ``numpy.linalg.lstsq``.
          - ``log_mv`` 在 ``feature_cols`` 中会被自动跳过（不能用 log_mv 解释 log_mv）.
          - 单日股票数 < ``min_stocks_per_day`` → 当日跳过中性化，保留原值.
          - 当日某行的 ``factor`` 或 ``log_mv`` 为 NaN → 该行不进 OLS，原 NaN 保留.
          - 行业列全 1 类（dummies 矩阵会被 ``drop_first`` 干掉）→ 仅对 ``log_mv`` 回归，不抛错.
          - 设计矩阵秩亏 → 退化为 ``residual = factor - factor.mean()``.
        """
        if date_col not in panel.columns:
            raise ValueError(f"panel 缺少 date 列 {date_col!r}")
        if by_industry and industry_col not in panel.columns:
            raise ValueError(
                f"by_industry=True 但 panel 缺少 industry 列 {industry_col!r}"
            )
        if by_log_mv and log_mv_col not in panel.columns:
            raise ValueError(
                f"by_log_mv=True 但 panel 缺少 log_mv 列 {log_mv_col!r}"
            )
        if not by_industry and not by_log_mv:
            # 无操作：直接返回副本
            return panel.copy()

        out = panel.copy()

        # 跳过 log_mv 自身（不能用 log_mv 解释 log_mv）.
        targets: list[str] = []
        for col in feature_cols:
            if col not in out.columns:
                _logger.warning("neutralize: feature %s not in panel; skipped", col)
                continue
            if by_log_mv and col == log_mv_col:
                _logger.debug(
                    "neutralize: skip %s (would regress log_mv on itself)", col,
                )
                continue
            targets.append(col)

        if not targets:
            return out

        # 按日 group，逐组做 OLS。groupby + indices 比 groupby.apply 控制力更强.
        groups = out.groupby(date_col, sort=False).indices
        # 缓存 dummies/log_mv 列以减少重复 .loc 开销.
        log_mv_series = out[log_mv_col] if by_log_mv else None
        industry_series = out[industry_col] if by_industry else None

        # 预分配 residual 容器：复制目标列为 float64 以接受 NaN.
        for col in targets:
            out[col] = out[col].astype(float)

        for _date_val, idx in groups.items():
            if len(idx) < min_stocks_per_day:
                # 不够 OLS：保留原值.
                continue

            # 构造该日设计矩阵 X.
            X_parts: list[pd.DataFrame | pd.Series] = []
            if by_industry:
                ind = industry_series.iloc[idx]  # type: ignore[union-attr]
                # drop_first 避免与常数项共线.
                dummies = pd.get_dummies(ind, prefix="ind", drop_first=True, dtype=float)
                if dummies.shape[1] > 0:
                    dummies.index = ind.index
                    X_parts.append(dummies)
                # 若 dummies 为空（当日仅 1 个行业）则不加；fallback 到仅 log_mv.
            if by_log_mv:
                lm = log_mv_series.iloc[idx].astype(float)  # type: ignore[union-attr]
                X_parts.append(lm.rename(log_mv_col))

            if not X_parts:
                # 既没行业 dummies 又没 log_mv 可用：退化为 demean.
                for col in targets:
                    y = out[col].iloc[idx]
                    if y.notna().sum() < 2:
                        continue
                    out.loc[y.index, col] = y - y.mean()
                continue

            # 拼接设计矩阵 + 常数项.
            X_df = pd.concat(X_parts, axis=1)
            X_df = X_df.astype(float)
            # 添加常数项列 (statsmodels add_constant 等价).
            X_df.insert(0, "const", 1.0)

            for col in targets:
                y = out[col].iloc[idx].astype(float)
                # 排除 y / X 任一为 NaN 的行参与 OLS.
                mask = y.notna() & X_df.notna().all(axis=1)
                if mask.sum() < min_stocks_per_day:
                    # 有效行不足：跳过当日该因子（保留原值）.
                    continue

                y_fit = y[mask].to_numpy()
                X_fit = X_df[mask].to_numpy()

                resid = _ols_residual(y_fit, X_fit)
                if resid is None:
                    # 完全退化为 demean.
                    out.loc[y[mask].index, col] = y_fit - y_fit.mean()
                else:
                    # 仅覆盖参与回归的行；其余 NaN 行保留 NaN.
                    out.loc[y[mask].index, col] = resid

        return out


def _ols_residual(y: np.ndarray, X: np.ndarray) -> np.ndarray | None:
    """跑一次 OLS 并返回残差.

    优先 ``statsmodels``；缺失或拟合失败 → ``numpy.linalg.lstsq`` 兜底；
    仍失败返回 ``None``（调用方走 demean 退化）.
    """
    # 秩亏快速检测：若 X 有 >1 列且某列方差 ≈ 0，statsmodels 会 raise；先检测.
    if X.shape[0] <= X.shape[1]:
        # 自由度不足：返回 None 让调用方退化.
        return None
    if _HAS_STATSMODELS:
        try:
            model = _sm.OLS(y, X, missing="raise").fit()
            return np.asarray(model.resid, dtype=float)
        except Exception as exc:  # pragma: no cover - 退化路径
            _logger.debug("statsmodels OLS failed (%s); falling back to lstsq", exc)
    try:
        beta, *_ = np.linalg.lstsq(X, y, rcond=None)
        y_hat = X @ beta
        return y - y_hat
    except Exception as exc:  # pragma: no cover - 极端退化
        _logger.debug("numpy lstsq failed (%s); returning None", exc)
        return None
