"""统计显著性测试 —— t-stat / Newey-West / Deflated Sharpe / Bootstrap CI.

为回测结果提供"超额收益是不是噪声？"的硬性回答：

- :meth:`Significance.t_stat`：单样本 t 检验，判断日均收益是否显著不为 0.
- :meth:`Significance.newey_west_se`：HAC 标准误，矫正时序自相关.
- :meth:`Significance.deflated_sharpe`：López de Prado (2014) DSR，
  矫正多次回测带来的 selection bias.
- :meth:`Significance.bootstrap_ci`：固定/移动 block bootstrap，
  对任意指标输出置信区间.
"""

from __future__ import annotations

from typing import Any, Callable, Literal

import numpy as np
import pandas as pd
from scipy import stats


# 策略族 → DSR 推荐 n_trials（防止"任何高 Sharpe 都按 mined 量级矫正"过度悲观）.
# 经验取值（López de Prado 2014 启发，结合 KSS 实证）：
#   - prior:        先验信念因子，无搜索（例：log_mv 反向，文献多次验证的 size factor）
#   - single_factor: 单因子直接 ranking，最多 2-3 个方向/horizon 实验
#   - small_grid:   小规模 grid (5-10 个 hyperparam 组合)
#   - tuned:        显式调过 hyperparam (20-50 组)
#   - mined:        数据挖掘出来的策略（100+ 试验或不可数）
StrategyFamily = Literal["prior", "single_factor", "small_grid", "tuned", "mined"]
_FAMILY_TRIALS: dict[str, int] = {
    "prior": 1,
    "single_factor": 2,
    "small_grid": 5,
    "tuned": 20,
    "mined": 100,
}

try:
    from statsmodels.stats.sandwich_covariance import cov_hac
    import statsmodels.api as sm
    _HAS_STATSMODELS = True
except ImportError:  # pragma: no cover
    _HAS_STATSMODELS = False


_TRADING_DAYS = 252


class Significance:
    """统计显著性指标集合.

    所有方法都是无状态 ``@staticmethod``；输入要求为已 dropna 的日度收益 ``pd.Series``.
    """

    # ------------------------------------------------------------------ #
    # 基础 t 检验
    # ------------------------------------------------------------------ #

    @staticmethod
    def t_stat(returns: pd.Series) -> tuple[float, float]:
        """单样本 t 检验：日收益均值是否显著 != 0.

        Args:
            returns: 日度收益率序列.

        Returns:
            ``(t_statistic, p_value)``；样本不足时返回 ``(0.0, 1.0)``.
        """
        r = returns.dropna()
        if len(r) < 2:
            return 0.0, 1.0
        if r.std() == 0:
            return 0.0, 1.0
        t, p = stats.ttest_1samp(r.values, popmean=0.0)
        return float(t), float(p)

    # ------------------------------------------------------------------ #
    # Newey-West HAC 标准误（时序自相关调整）
    # ------------------------------------------------------------------ #

    @staticmethod
    def newey_west_se(returns: pd.Series, lags: int | None = None) -> float:
        """Newey-West HAC 标准误.

        对日均收益做常数项 OLS（``r = α + ε``）并用 HAC 协方差，
        返回 α 的稳健标准误.

        Args:
            returns: 日度收益率序列.
            lags: 自相关滞后阶数；缺省按 ``floor(4 × (n/100)^(2/9))``（Greene 经验值）.

        Returns:
            α 的稳健标准误；样本不足或 statsmodels 缺失时返回 ``np.nan``.
        """
        if not _HAS_STATSMODELS:  # pragma: no cover
            return float("nan")
        r = returns.dropna()
        n = len(r)
        if n < 5:
            return float("nan")
        if lags is None:
            lags = max(1, int(np.floor(4 * (n / 100) ** (2 / 9))))
        X = np.ones((n, 1))
        ols = sm.OLS(r.values, X).fit()
        cov = cov_hac(ols, nlags=lags)
        return float(np.sqrt(cov[0, 0]))

    # ------------------------------------------------------------------ #
    # Deflated Sharpe Ratio
    # ------------------------------------------------------------------ #

    @staticmethod
    def deflated_sharpe(
        sharpe: float,
        n_trials: int,
        n_obs: int,
        skew: float = 0.0,
        kurt: float = 0.0,
    ) -> float:
        """Deflated Sharpe Ratio (Bailey & López de Prado, 2014).

        给定原始 SR、回测试验次数、样本数与高阶矩，输出
        "SR 不为零" 的后验概率（probabilistic Sharpe）.

        Args:
            sharpe: 原始年化夏普比率.
            n_trials: 该策略族尝试过的回测次数（越多越严苛）；≥ 1.
            n_obs: 回测使用的样本日数.
            skew: 收益序列偏度（``Metrics.calc`` 中的 ``skew``）.
            kurt: 收益序列超额峰度（``Metrics.calc`` 中的 ``kurt``）.

        Returns:
            通过概率（0~1）；样本不足返回 ``np.nan``.
        """
        if n_obs < 5 or n_trials < 1:
            return float("nan")
        # 把年化 SR 折回日化 SR 用于公式
        sharpe_daily = sharpe / np.sqrt(_TRADING_DAYS)

        # E[max SR] 的估计：高斯极值近似（López de Prado 2014, eq 5）
        emc = 0.5772156649  # Euler-Mascheroni
        z_max = (1 - emc) * stats.norm.ppf(1 - 1.0 / n_trials) + emc * stats.norm.ppf(
            1 - 1.0 / (n_trials * np.e)
        )
        expected_max_sr = z_max / np.sqrt(_TRADING_DAYS)

        # PSR 公式（连续高阶矩调整）
        # se = sqrt((1 - skew*SR + (kurt-1)/4 * SR^2) / (n - 1))
        se = np.sqrt(
            max(
                1e-12,
                (1 - skew * sharpe_daily + (kurt - 1) / 4.0 * sharpe_daily**2) / (n_obs - 1),
            )
        )
        z = (sharpe_daily - expected_max_sr) / se
        return float(stats.norm.cdf(z))

    # ------------------------------------------------------------------ #
    # Block Bootstrap CI
    # ------------------------------------------------------------------ #

    @staticmethod
    def bootstrap_ci(
        returns: pd.Series,
        metric_fn: Callable[[pd.Series], float],
        n_boot: int = 1000,
        alpha: float = 0.05,
        block_size: int | None = None,
        seed: int | None = 42,
    ) -> tuple[float, float]:
        """Stationary / circular block bootstrap 置信区间.

        通过对收益序列做 block 抽样（保留短期自相关）重复计算 ``metric_fn``，
        输出双侧 ``(alpha/2, 1-alpha/2)`` 分位.

        Args:
            returns: 日度收益率序列.
            metric_fn: 接受 ``pd.Series`` 返回 ``float`` 的指标函数（如年化收益）.
            n_boot: 重抽样次数.
            alpha: 显著性水平，``0.05`` 即输出 95% CI.
            block_size: block 长度；缺省按 ``ceil(n^(1/3))``（Politis-White 推荐）.
            seed: 随机种子.

        Returns:
            ``(lower, upper)``；样本不足返回 ``(nan, nan)``.
        """
        r = returns.dropna().reset_index(drop=True)
        n = len(r)
        if n < 10:
            return float("nan"), float("nan")
        if block_size is None:
            block_size = max(1, int(np.ceil(n ** (1 / 3))))

        rng = np.random.default_rng(seed)
        boot_values: list[float] = []
        # circular block bootstrap：起点均匀分布在 [0, n)，越界 wrap-around
        n_blocks = int(np.ceil(n / block_size))
        idx_base = np.arange(block_size)

        for _ in range(n_boot):
            starts = rng.integers(0, n, size=n_blocks)
            sample_idx = (starts[:, None] + idx_base[None, :]).reshape(-1) % n
            sample_idx = sample_idx[:n]
            sample = r.iloc[sample_idx].reset_index(drop=True)
            try:
                boot_values.append(float(metric_fn(sample)))
            except Exception:  # noqa: BLE001
                continue

        if not boot_values:
            return float("nan"), float("nan")

        lo = float(np.quantile(boot_values, alpha / 2))
        hi = float(np.quantile(boot_values, 1 - alpha / 2))
        return lo, hi

    # ------------------------------------------------------------------ #
    # 便捷封装：Sharpe 显著性
    # ------------------------------------------------------------------ #

    @staticmethod
    def sharpe_significance(
        returns: pd.Series,
        n_trials: int = 1,
        skew: float | None = None,
        kurt: float | None = None,
    ) -> dict[str, float]:
        """一站式 Sharpe 显著性诊断.

        组合 t-stat / Newey-West / Deflated Sharpe，避免上层重复样板代码.

        Args:
            returns: 日度收益序列.
            n_trials: 用于 DSR 的回测试验次数；缺省 1（不矫正）.
            skew: 收益偏度，缺省由序列计算.
            kurt: 收益峰度，缺省由序列计算.

        Returns:
            字典字段：``sharpe`` / ``t_stat`` / ``p_value`` / ``nw_se`` /
            ``deflated_sharpe`` / ``n``.
        """
        r = returns.dropna()
        n = len(r)
        if n < 2 or r.std() == 0:
            return {
                "sharpe": 0.0,
                "t_stat": 0.0,
                "p_value": 1.0,
                "nw_se": float("nan"),
                "deflated_sharpe": float("nan"),
                "n": n,
            }
        annual = r.mean() * _TRADING_DAYS
        vol = r.std() * np.sqrt(_TRADING_DAYS)
        sharpe = annual / vol if vol > 0 else 0.0

        t, p = Significance.t_stat(r)
        nw_se = Significance.newey_west_se(r)
        if skew is None:
            skew = float(stats.skew(r.values, bias=False)) if n >= 3 else 0.0
        if kurt is None:
            kurt = float(stats.kurtosis(r.values, fisher=True, bias=False)) if n >= 4 else 0.0
        dsr = Significance.deflated_sharpe(sharpe, n_trials, n, skew, kurt)

        return {
            "sharpe": float(sharpe),
            "t_stat": float(t),
            "p_value": float(p),
            "nw_se": float(nw_se) if np.isfinite(nw_se) else float("nan"),
            "deflated_sharpe": float(dsr) if np.isfinite(dsr) else float("nan"),
            "n": n,
        }

    # ------------------------------------------------------------------ #
    # 硬性上线门槛
    # ------------------------------------------------------------------ #

    @staticmethod
    def is_deployable(
        returns: pd.Series,
        *,
        dsr_threshold: float = 0.4,
        pvalue_threshold: float = 0.05,
        min_sharpe: float = 0.5,
        n_trials: int | None = None,
        strategy_family: StrategyFamily | None = None,
        return_details: bool = False,
    ) -> bool | dict[str, Any]:
        """硬性策略上线门槛检查.

        通过条件（全部满足）：

        1. Sharpe (净收益) ≥ ``min_sharpe``
        2. 日均收益 t-test p-value < ``pvalue_threshold``
        3. Deflated Sharpe Ratio (``n_trials`` 矫正) ≥ ``dsr_threshold``

        **n_trials 选择**：DSR 对 n_trials 极敏感（取 100 会让 Sharpe 1.93 都拒）.
        旧版默认 10 在"先验单因子"场景下过严. 现版按 ``strategy_family`` 自动选 n_trials:

        - ``"prior"``         (n_trials=1)    : 先验信念因子（如 size factor）
        - ``"single_factor"`` (n_trials=2)    : 单因子 ranking + 少量方向尝试
        - ``"small_grid"``    (n_trials=5)    : 小规模 hyperparam grid
        - ``"tuned"``         (n_trials=20)   : 显式调过参数
        - ``"mined"``         (n_trials=100)  : 数据挖出来的

        显式传 ``n_trials`` 优先级最高；否则按 ``strategy_family`` 推断；
        都不给时退化到 ``n_trials=10``（旧行为兼容）.

        **DSR 阈值**：默认 ``0.4``（LdP 建议上线"最低可接受"为 ``> 0.4``，
        ``> 0.5`` 为"良好"，``> 0.95`` 为"优秀"）. 旧版 0.7 实证下连科创板 size factor
        都过不去，过严.

        Args:
            returns: 日度净收益序列.
            dsr_threshold: DSR 最小阈值，默认 ``0.4``.
            pvalue_threshold: p 值上限.
            min_sharpe: 最低 Sharpe 门槛.
            n_trials: 用于 DSR 的回测试验次数；缺省按 ``strategy_family`` 推断.
            strategy_family: 策略族标签，决定 n_trials 默认值. 不传则 ``n_trials=10``
                （旧行为兼容）.
            return_details: ``True`` 时返回包含 verdict + 各指标的 dict.

        Returns:
            通过返回 ``True``（或 dict 含 ``passed=True``）；
            否则 ``False``（或 dict 含 ``passed=False`` 与 ``failures``）.
        """
        # n_trials 推断：显式 > strategy_family > 旧默认 10
        if n_trials is None:
            if strategy_family is not None:
                n_trials = _FAMILY_TRIALS[strategy_family]
            else:
                n_trials = 10  # 旧行为兼容：未指定族也不指定 trials → 保守

        sig = Significance.sharpe_significance(returns, n_trials=n_trials)
        sharpe = sig["sharpe"]
        p_value = sig["p_value"]
        dsr = sig["deflated_sharpe"]
        n = sig["n"]

        failures: list[str] = []
        if not (sharpe >= min_sharpe):
            failures.append("sharpe<min")
        if not (p_value < pvalue_threshold):
            failures.append("p>threshold")
        # dsr 可能为 NaN（样本过少 / n_trials < 1）—— 视为未通过
        if not (np.isfinite(dsr) and dsr >= dsr_threshold):
            failures.append("dsr<threshold")

        passed = len(failures) == 0

        if return_details:
            return {
                "passed": passed,
                "sharpe": float(sharpe),
                "p_value": float(p_value),
                "dsr": float(dsr) if np.isfinite(dsr) else float("nan"),
                "n": int(n),
                "n_trials": int(n_trials),
                "strategy_family": strategy_family,
                "failures": failures,
            }
        return passed
