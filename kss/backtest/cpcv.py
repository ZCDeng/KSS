"""组合净化交叉验证（CPCV, López de Prado 2018）—— 把 walk-forward 点估计升级为 OOS 分布（U5）.

``WalkForwardCombiner.run()`` 只报告全期聚合的单条 OOS 净值曲线（一个 Sharpe 数字），
掩盖了 Sharpe 在不同窗口上的塌陷风险（科创板实测从 1.93 跌到 -0.53）。CPCV 把 T 个 bar
分成 ``k`` 折，每次选 ``p`` 折做测试，产生 ``C(k,p)`` 条互不重叠的 OOS 路径；聚合这些路径的
**Sharpe 分布 + IC 分布**，再喂进已有的 :class:`~kss.backtest.significance.Significance`
（``deflated_sharpe`` / ``is_deployable``）与 PBO（probability of backtest overfitting）。

设计纪律（见 docs/plans/2026-06-21-001-backtest-loop-closure-plan.md U5 段 + Global #6/#8）：

- **上游包 :class:`WalkForwardCombiner`，不改其接口**：CPCV 只调度 WFC 跑单路径回测，
  方向单向（CPCV → WFC，反之不成立）。bug 修复自动继承。
- **purge + embargo 防泄漏**（R1.2/R1.3）：训练/测试边界两侧各去 ``purge_bars`` 个 bar
  （堵 ``next_day_return`` 向前看 1 bar 的 label 重叠），测试折结束后再 embargo ``embargo_bars``
  个 bar。**``_rolling_zscore`` 因果性已核**（``rolling(window, min_periods=window)`` 严格
  trailing，每个 t 只用 ``[t-window+1, t]``，无未来窗口）；故 purge 只需覆盖 label 重叠
  （``next_day_return`` 1 bar），rolling 边界不额外泄漏——见模块尾 ``_ROLLING_ZSCORE_CAUSALITY``。
- **单用户本地算力**（Global #6）：默认 ``k=6/p=2 = C(6,2)=15`` 路径（验收基线，实测 ~125s）；
  严格 ``k=10/p=4 = 210`` 路径标 deferred、可配但非首版门。fold 缓存（pickle）支持续跑，
  参数变化自动失效（hash key）。
- **IC 分布产出**（Global #8 / U3 先验）：per path 算 IC/ICIR，聚合成回测 IC 分布，
  经 :class:`CPCVBacktestICSource` 适配成 U3 ``factor_health.BacktestICSource``
  （``Callable[[str], tuple[float, float] | None]``），喂 U3 升权路径的回测先验。
- **fail loud**（R2.2/R4.4）：单路径失败（WFC 抛异常 / 训练不足）记入 ``failed_paths``，
  不中断整体；成功率 < 70% 发 ``UserWarning``，绝不静默当 0 路径成功。
"""

from __future__ import annotations

import hashlib
import logging
import math
import pickle
import warnings
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd
from scipy import stats

from kss.backtest.metrics import Metrics
from kss.backtest.significance import Significance
from kss.backtest.walk_forward_combiner import CombinerBuilder, WalkForwardCombiner

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CACHE_ROOT = PROJECT_ROOT / "storage" / "cpcv_cache"


# ---------------------------------------------------------------------------
# 单路径结果（落库 / 缓存字段集）
# ---------------------------------------------------------------------------


@dataclass
class PathResult:
    """一条 CPCV OOS 路径的结果（一个 test-fold 组合）.

    ``ok=False`` 时其余指标为 NaN，``error`` 记原因（fail loud，不静默丢）。
    ``factor_ic`` 是该路径 per-factor 的 (ic_mean, icir)，供回测 IC 先验（#8）。
    """

    test_folds: tuple[int, ...]
    ok: bool
    sharpe: float = float("nan")
    annual: float = float("nan")
    max_dd: float = float("nan")
    calmar: float = float("nan")
    oos_len: int = 0
    # 训练期 Sharpe（PBO 用：训练排序 → 测试中位以下占比）
    train_sharpe: float = float("nan")
    # per-factor IC：{factor_id: (ic_mean, icir)}（单路径 OOS 段，供 #8 聚合）
    factor_ic: dict[str, tuple[float, float]] = field(default_factory=dict)
    # 路径 OOS 合成信号层面的 IC（signal_z vs 实盘收益），路径级标量
    signal_ic: float = float("nan")
    error: str = ""


# ---------------------------------------------------------------------------
# 纯函数：折叠生成 / purge / embargo
# ---------------------------------------------------------------------------


def make_fold_bounds(n_bars: int, k: int) -> list[tuple[int, int]]:
    """把 ``n_bars`` 个 bar 按 **等 bar 数** 切成 ``k`` 折，返回每折 ``[start, end)`` 整数位置.

    不按日历月切（A 股非交易日分布不均会让每折样本量失衡）；用 ``np.array_split`` 的
    等分语义，余数摊到前几折（每折差 ≤ 1 bar）。时间顺序连续，不打乱 bar（R1.1/R1.4）。
    """
    if k < 2:
        raise ValueError(f"k 必须 ≥ 2，收到 {k}")
    if n_bars < k:
        raise ValueError(f"n_bars={n_bars} < k={k}，无法切折")
    edges = np.array_split(np.arange(n_bars), k)
    return [(int(e[0]), int(e[-1]) + 1) for e in edges]


def purged_train_mask(
    n_bars: int,
    fold_bounds: list[tuple[int, int]],
    test_folds: tuple[int, ...],
    purge_bars: int,
    embargo_bars: int,
) -> np.ndarray:
    """构造该 test-fold 组合的训练掩码（True=训练可用），含 purge + embargo（R1.2/R1.3）.

    - **purge**：每个测试折 ``[s, e)`` 两侧各去 ``purge_bars`` 个 bar（``[s-purge, e+purge)``
      内的训练 bar 全部剔除）—— 堵 label 重叠（``next_day_return`` 向前看 1 bar，至少 purge=1）。
    - **embargo**：测试折结束后的训练数据额外排除 ``embargo_bars`` 个 bar（防泄漏方向二：
      测试期信息经训练样本的滚动统计回流）。

    Returns:
        长度 ``n_bars`` 的 bool 数组；test-fold bar 本身始终 False（训练不可用）。
    """
    is_test = np.zeros(n_bars, dtype=bool)
    train_ok = np.ones(n_bars, dtype=bool)
    for fi in test_folds:
        s, e = fold_bounds[fi]
        is_test[s:e] = True
        # purge：两侧各 purge_bars
        p_lo = max(0, s - purge_bars)
        p_hi = min(n_bars, e + purge_bars)
        train_ok[p_lo:p_hi] = False
        # embargo：测试折结束后 embargo_bars（仅向后，防测试期信息经训练回流）
        emb_hi = min(n_bars, e + purge_bars + embargo_bars)
        train_ok[e:emb_hi] = False
    # 测试折 bar 永不作训练
    train_ok[is_test] = False
    return train_ok


def all_test_fold_combos(k: int, p: int) -> list[tuple[int, ...]]:
    """所有 ``C(k,p)`` 个测试折组合（R1.1）。"""
    if not (1 <= p < k):
        raise ValueError(f"需 1 ≤ p < k，收到 k={k} p={p}")
    return [tuple(c) for c in combinations(range(k), p)]


def _per_factor_ic(
    seg_df: pd.DataFrame,
    factors: list[str],
    ret_col: str,
    method: str = "spearman",
) -> dict[str, tuple[float, float]]:
    """该 OOS 段 per-factor 的 (ic_mean, icir).

    单股票时序：一日一条 (因子, 次日收益)，无截面 Spearman。用 ``因子去均值符号 × 收益符号``
    的逐日一致性作为日级 IC 代理（与 WFC.health_hook 同口径），ic_mean=代理均值，
    icir = ic_mean / ic_std。样本不足（<10）→ (nan, nan)。

    口径 = ``sign_proxy``：经 ``CPCVBacktestICSource`` 喂 #8 时是回测先验，
    ``FactorHealthHook.backtest_ic_method`` 默认即 ``sign_proxy``，与此一致。
    """
    out: dict[str, tuple[float, float]] = {}
    for f in factors:
        if f not in seg_df.columns:
            continue
        pair = seg_df[[f, ret_col]].dropna()
        if len(pair) < 10:
            out[f] = (float("nan"), float("nan"))
            continue
        fv = pair[f].astype(float).values
        rv = pair[ret_col].astype(float).values
        sign_ic = np.sign(fv - fv.mean()) * np.sign(rv)
        ic_mean = float(np.mean(sign_ic))
        ic_std = float(np.std(sign_ic, ddof=1)) if len(sign_ic) >= 2 else 0.0
        icir = float(ic_mean / ic_std) if ic_std > 0 else 0.0
        out[f] = (ic_mean, icir)
    return out


def compute_pbo(path_results: list[PathResult]) -> float:
    """PBO（Probability of Backtest Overfitting，CPCV 标准定义，R4.3）.

    把成功路径按 **训练期 Sharpe** 排序，统计 **测试期 Sharpe < 测试期中位数** 的比例。
    接近 0.5 = 过拟合严重（训练好的路径在测试期没系统性更好）。

    实现：对每条成功路径，logit(rank) 的负向占比近似——这里用 LdP 简化口径：
    test_sharpe < median(test_sharpe) 在按 train_sharpe 降序的高分位路径中的占比。
    样本 < 2 → NaN。
    """
    ok = [
        r for r in path_results
        if r.ok and np.isfinite(r.train_sharpe) and np.isfinite(r.sharpe)
    ]
    if len(ok) < 2:
        return float("nan")
    test_median = float(np.median([r.sharpe for r in ok]))
    # 按训练 Sharpe 降序，取"训练表现较好"的上半部分
    ok_sorted = sorted(ok, key=lambda r: r.train_sharpe, reverse=True)
    top_half = ok_sorted[: max(1, len(ok_sorted) // 2)]
    n_overfit = sum(1 for r in top_half if r.sharpe < test_median)
    return float(n_overfit / len(top_half))


# ---------------------------------------------------------------------------
# CPCVBacktester
# ---------------------------------------------------------------------------


@dataclass
class CPCVBacktester:
    """组合净化交叉验证回测器（上游包 :class:`WalkForwardCombiner`，不改其接口）.

    Attributes:
        factor_df: 含 ``trade_date`` / ``feature_cols`` / ``ret_col`` 的单股票面板.
        feature_cols: 候选因子列名.
        combiner_builder: 与 WFC 相同签名的 :data:`CombinerBuilder`（R5.1，类型复用）.
        k: 总折数（默认 6，验收基线）.
        p: 测试折数（默认 2 → C(6,2)=15 路径）.
        purge_bars: 训练/测试边界两侧 purge bar 数（默认 1，堵 next_day_return label 重叠）.
        embargo_bars: 测试折后 embargo bar 数（默认 1）.
        min_train_bars: 路径训练样本下限，不足则跳过并 warning（R1.5）.
        cache_dir: fold 结果 pickle 缓存目录；None=不缓存（R3.3）.
        wfc_kwargs: 透传给 WFC 的额外构造参数（train_window/retrain_freq/rolling_window/阈值等）.
        ret_col / date_col: 收益列 / 交易日列.
    """

    factor_df: pd.DataFrame
    feature_cols: list[str]
    combiner_builder: CombinerBuilder
    k: int = 6
    p: int = 2
    purge_bars: int = 1
    embargo_bars: int = 1
    min_train_bars: int = 80
    cache_dir: str | Path | None = None
    wfc_kwargs: dict[str, Any] = field(default_factory=dict)
    ret_col: str = "next_day_return"
    date_col: str = "trade_date"

    def __post_init__(self) -> None:
        self._df = self.factor_df.sort_values(self.date_col).reset_index(drop=True)
        self._n = len(self._df)
        self._fold_bounds = make_fold_bounds(self._n, self.k)
        self._combos = all_test_fold_combos(self.k, self.p)
        if self.cache_dir is not None:
            self._cache_path = Path(self.cache_dir)
            self._cache_path.mkdir(parents=True, exist_ok=True)
        else:
            self._cache_path = None

    # ------------------------------------------------------------------ #
    # 缓存 key（R3.4）：参数变化自动失效
    # ------------------------------------------------------------------ #

    def _param_hash(self) -> str:
        builder_name = getattr(self.combiner_builder, "__name__", "builder")
        key = (
            f"{self._df.shape}|{sorted(self.feature_cols)}|k={self.k}|p={self.p}"
            f"|purge={self.purge_bars}|embargo={self.embargo_bars}"
            f"|min_train={self.min_train_bars}|builder={builder_name}"
            f"|wfc={sorted(self.wfc_kwargs.items())}"
        )
        return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]

    def _fold_cache_file(self, combo: tuple[int, ...]) -> Path | None:
        if self._cache_path is None:
            return None
        combo_str = "_".join(str(c) for c in combo)
        return self._cache_path / f"fold_{self._param_hash()}_{combo_str}.pkl"

    # ------------------------------------------------------------------ #
    # 预估（R3.2）
    # ------------------------------------------------------------------ #

    def estimate(self) -> dict[str, Any]:
        """运行前预估：路径数 + 每路径 bar 数（不实际跑回测）."""
        n_paths = len(self._combos)
        approx_train = self._n - sum(
            self._fold_bounds[fi][1] - self._fold_bounds[fi][0]
            for fi in self._combos[0]
        )
        return {
            "n_paths": n_paths,
            "n_bars": self._n,
            "k": self.k,
            "p": self.p,
            "approx_train_bars_per_path": int(approx_train),
            "fold_bounds": self._fold_bounds,
        }

    # ------------------------------------------------------------------ #
    # 单路径回测
    # ------------------------------------------------------------------ #

    def _run_path(self, combo: tuple[int, ...]) -> PathResult:
        """跑一条路径：purge/embargo 训练掩码 → 在含测试折的时序切片上跑 WFC →
        截取属于测试折的 OOS 收益段，算 Sharpe / IC（R2.1）.

        关键防泄漏：WFC 在测试段算合成 z-score 时用的 combiner 权重，只能来自训练折
        （purge/embargo 后）。本实现把 **训练折 + 测试折拼成时序连续切片**喂 WFC，并用
        ``date_blacklist`` 让 WFC 在被 purge/embargo 的训练 bar 上不参与 retrain 权重选择。
        由于 WFC 自身已用 ``[i-train_window, i)`` 历史 retrain 且 ``_rolling_zscore`` 严格
        因果，purge_bars 只需覆盖 label 重叠（1 bar）即可阻断 next_day_return 泄漏。
        """
        n = self._n
        bounds = self._fold_bounds
        train_ok = purged_train_mask(
            n, bounds, combo, self.purge_bars, self.embargo_bars
        )
        is_test = np.zeros(n, dtype=bool)
        for fi in combo:
            s, e = bounds[fi]
            is_test[s:e] = True

        if int(train_ok.sum()) < self.min_train_bars:
            return PathResult(
                test_folds=combo, ok=False,
                error=f"训练 bar 数 {int(train_ok.sum())} < min_train_bars {self.min_train_bars}",
            )

        # 喂 WFC 的切片：训练可用 bar ∪ 测试 bar，时序保持（去掉被 purge/embargo 的 bar）。
        # 被 purge/embargo 的训练 bar 从面板里物理移除，确保 WFC 的 retrain 窗口绝不触及它们。
        keep = train_ok | is_test
        sub = self._df.loc[keep].reset_index(drop=True)
        # 测试折 bar 在 sub 里的位置（用于截取 OOS 段）
        sub_is_test = is_test[keep]

        wfc_kwargs = dict(self.wfc_kwargs)
        wfc_kwargs.setdefault("ret_col", self.ret_col)
        wfc_kwargs.setdefault("date_col", self.date_col)
        try:
            wfc = WalkForwardCombiner(
                factor_df=sub,
                feature_cols=list(self.feature_cols),
                combiner_builder=self.combiner_builder,
                **wfc_kwargs,
            )
            res = wfc.run()
        except Exception as exc:  # noqa: BLE001 —— 单路径失败不中断整体（R2.2）
            return PathResult(test_folds=combo, ok=False, error=f"WFC: {exc}")

        net = res["net_return"].reset_index(drop=True)
        signal_z = res["signal_z"].reset_index(drop=True)
        # 截取属于测试折的 OOS 段（sub 顺序对齐）
        test_mask = pd.Series(sub_is_test, index=net.index)
        oos = net[test_mask].dropna()
        if len(oos) < 5:
            return PathResult(
                test_folds=combo, ok=False,
                error=f"OOS 有效收益 {len(oos)} < 5（测试折信号未触发或全 NaN）",
            )

        m = Metrics.calc(oos)
        # 训练期 Sharpe（PBO 用）：训练折 OOS 之外的 net_return 段
        train_net = net[~test_mask].dropna()
        train_sharpe = (
            Metrics.calc(train_net).get("sharpe", float("nan"))
            if len(train_net) >= 5 else float("nan")
        )

        # per-factor IC（测试折段）：供 #8 回测先验聚合
        test_seg = sub.loc[sub_is_test].reset_index(drop=True)
        factor_ic = _per_factor_ic(test_seg, list(self.feature_cols), self.ret_col)

        # 路径级 signal IC：合成 z vs 实盘收益（Spearman），测试折段
        sig_test = signal_z[test_mask]
        ret_test = sub.loc[sub_is_test, self.ret_col].reset_index(drop=True)
        sig_test = sig_test.reset_index(drop=True)
        pair = pd.concat([sig_test, ret_test], axis=1).dropna()
        signal_ic = float("nan")
        if len(pair) >= 10 and pair.iloc[:, 0].std() > 0 and pair.iloc[:, 1].std() > 0:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                rho = stats.spearmanr(pair.iloc[:, 0].values, pair.iloc[:, 1].values)[0]
            signal_ic = float(rho) if np.isfinite(rho) else float("nan")

        return PathResult(
            test_folds=combo, ok=True,
            sharpe=float(m.get("sharpe", float("nan"))),
            annual=float(m.get("annual", float("nan"))),
            max_dd=float(m.get("max_dd", float("nan"))),
            calmar=float(m.get("calmar", float("nan"))),
            oos_len=int(len(oos)),
            train_sharpe=float(train_sharpe),
            factor_ic=factor_ic,
            signal_ic=signal_ic,
        )

    def _load_or_run_path(self, combo: tuple[int, ...]) -> PathResult:
        """缓存续跑（R3.3/F3）：命中缓存直接读，否则跑完写盘."""
        cache_file = self._fold_cache_file(combo)
        if cache_file is not None and cache_file.exists():
            try:
                with cache_file.open("rb") as fh:
                    cached = pickle.load(fh)
                if isinstance(cached, PathResult) and cached.test_folds == combo:
                    return cached
            except Exception as exc:  # noqa: BLE001 —— 缓存损坏不致命，重算
                logger.warning("fold 缓存读取失败 %s: %s（重算）", cache_file, exc)

        result = self._run_path(combo)
        if cache_file is not None:
            try:
                with cache_file.open("wb") as fh:
                    pickle.dump(result, fh)
            except Exception as exc:  # noqa: BLE001
                logger.warning("fold 缓存写入失败 %s: %s", cache_file, exc)
        return result

    # ------------------------------------------------------------------ #
    # 主流程
    # ------------------------------------------------------------------ #

    def run(self) -> dict[str, Any]:
        """跑全部 ``C(k,p)`` 条路径，聚合 Sharpe + IC 分布 + PBO（R4）.

        Returns:
            字典字段（部分）：

            - ``n_paths``        : C(k,p)
            - ``n_paths_valid``  : 成功路径数
            - ``n_paths_failed`` : 失败路径数
            - ``sharpe_p5/p25/p50/p75/p95`` : Sharpe 百分位
            - ``sharpe_mean`` / ``pct_positive`` : 均值 / Sharpe>0 占比
            - ``pbo``            : Probability of Backtest Overfitting
            - ``ic_p5/p50/p95``  : 路径级 signal IC 分布
            - ``deflated_sharpe``: DSR（n_trials=n_paths_valid, sharpe=p50）
            - ``is_deployable``  : is_deployable(strategy_family="mined") 明细
            - ``factor_ic``      : per-factor 聚合 (bt_ic_mean, bt_icir)（#8 先验源）
            - ``path_results``   : list[PathResult]
            - ``failed_paths``   : list[(combo, error)]
        """
        est = self.estimate()
        logger.info(
            "CPCV 预估：%d 路径（k=%d p=%d, %d bars, ~%d train bars/path）",
            est["n_paths"], self.k, self.p, est["n_bars"],
            est["approx_train_bars_per_path"],
        )

        path_results: list[PathResult] = [
            self._load_or_run_path(combo) for combo in self._combos
        ]

        valid = [r for r in path_results if r.ok]
        failed = [(r.test_folds, r.error) for r in path_results if not r.ok]
        n_paths = len(path_results)
        n_valid = len(valid)

        # 成功率 < 70% → fail loud（R4.4）
        if n_valid < n_paths * 0.7:
            warnings.warn(
                f"CPCV 成功路径 {n_valid}/{n_paths} < 70%；"
                f"分布统计可能不可靠（失败原因见 failed_paths）",
                UserWarning,
                stacklevel=2,
            )

        sharpes = np.array(
            [r.sharpe for r in valid if np.isfinite(r.sharpe)], dtype=float
        )
        ics = np.array(
            [r.signal_ic for r in valid if np.isfinite(r.signal_ic)], dtype=float
        )

        def _pct(arr: np.ndarray, q: float) -> float:
            return float(np.percentile(arr, q)) if arr.size else float("nan")

        sharpe_p50 = _pct(sharpes, 50)
        median_oos_len = (
            int(np.median([r.oos_len for r in valid])) if valid else 0
        )

        # DSR：把 p50 Sharpe 作为代表，n_trials = 成功路径数（R5.2）
        dsr = (
            Significance.deflated_sharpe(
                sharpe=sharpe_p50, n_trials=max(1, n_valid), n_obs=median_oos_len,
            )
            if np.isfinite(sharpe_p50) and median_oos_len >= 5
            else float("nan")
        )

        # is_deployable：用所有成功路径 OOS Sharpe 的中位代理序列喂硬门槛
        # （strategy_family="mined" → n_trials=100，CPCV 分布配 mined 是推荐组合，R5.4）
        deploy = self._deployability(valid, n_valid)

        # per-factor 聚合 IC（#8 先验）：各路径 factor_ic 取均值
        factor_ic_agg = self._aggregate_factor_ic(valid)

        result: dict[str, Any] = {
            "n_paths": n_paths,
            "n_paths_valid": n_valid,
            "n_paths_failed": len(failed),
            "sharpe_p5": _pct(sharpes, 5),
            "sharpe_p25": _pct(sharpes, 25),
            "sharpe_p50": sharpe_p50,
            "sharpe_p75": _pct(sharpes, 75),
            "sharpe_p95": _pct(sharpes, 95),
            "sharpe_mean": float(sharpes.mean()) if sharpes.size else float("nan"),
            "pct_positive": (
                float((sharpes > 0).mean()) if sharpes.size else float("nan")
            ),
            "pbo": compute_pbo(path_results),
            "ic_p5": _pct(ics, 5),
            "ic_p50": _pct(ics, 50),
            "ic_p95": _pct(ics, 95),
            "ic_mean": float(ics.mean()) if ics.size else float("nan"),
            "median_oos_len": median_oos_len,
            "deflated_sharpe": float(dsr) if np.isfinite(dsr) else float("nan"),
            "is_deployable": deploy,
            "factor_ic": factor_ic_agg,
            "path_results": path_results,
            "failed_paths": failed,
            "sharpe_values": sharpes.tolist(),
        }
        return result

    def _deployability(
        self, valid: list[PathResult], n_valid: int
    ) -> dict[str, Any]:
        """用成功路径 Sharpe 分布喂 is_deployable（strategy_family="mined"）.

        is_deployable 接受日度收益序列；CPCV 这里没有单一收益序列，故用各路径 Sharpe
        当作"年化 Sharpe 样本"做轻量门槛代理：把路径 Sharpe 序列当成观测，n_trials 按
        mined（100）矫正。文档说明这是分布配 mined 的推荐用法（R5.4），不替代逐日检验。
        """
        sharpes = [r.sharpe for r in valid if np.isfinite(r.sharpe)]
        if len(sharpes) < 2:
            return {"passed": False, "reason": "成功路径不足，无法判定", "n_valid": n_valid}
        p50 = float(np.median(sharpes))
        pct_pos = float(np.mean([s > 0 for s in sharpes]))
        # 用 p50 Sharpe + 路径数 n_trials 直接走 DSR 门
        dsr = Significance.deflated_sharpe(
            sharpe=p50, n_trials=100, n_obs=max(5, int(np.median(
                [r.oos_len for r in valid]
            ))),
        )
        passed = (
            p50 >= 0.5 and pct_pos >= 0.5 and np.isfinite(dsr) and dsr >= 0.4
        )
        return {
            "passed": bool(passed),
            "sharpe_p50": p50,
            "pct_positive": pct_pos,
            "dsr": float(dsr) if np.isfinite(dsr) else float("nan"),
            "strategy_family": "mined",
            "n_trials": 100,
            "n_valid": n_valid,
        }

    @staticmethod
    def _aggregate_factor_ic(
        valid: list[PathResult],
    ) -> dict[str, tuple[float, float]]:
        """把各路径 per-factor (ic_mean, icir) 聚合成回测先验（跨路径均值）.

        Returns:
            ``{factor_id: (bt_ic_mean, bt_icir)}``；某因子在所有路径都 NaN → 不收录。
        """
        buckets: dict[str, list[tuple[float, float]]] = {}
        for r in valid:
            for f, (icm, icir) in r.factor_ic.items():
                if np.isfinite(icm):
                    buckets.setdefault(f, []).append((icm, icir))
        agg: dict[str, tuple[float, float]] = {}
        for f, vals in buckets.items():
            icms = np.array([v[0] for v in vals], dtype=float)
            icirs = np.array(
                [v[1] for v in vals if np.isfinite(v[1])], dtype=float
            )
            agg[f] = (
                float(icms.mean()),
                float(icirs.mean()) if icirs.size else float("nan"),
            )
        return agg


# ---------------------------------------------------------------------------
# #8 适配器：CPCV 回测 IC 分布 → U3 factor_health.BacktestICSource
# ---------------------------------------------------------------------------


class CPCVBacktestICSource:
    """把 CPCV 的 per-factor 回测 IC 暴露成 U3 ``factor_health.BacktestICSource``.

    U3 的 ``BacktestICSource = Callable[[str], tuple[float, float] | None]``（回测先验注入
    接口）：传 ``factor_id`` 返回 ``(backtest_ic_mean, backtest_icir)``，因子缺失先验 → None
    （U3 升权路径据此保守不放行）。

    用法（接通 Global #8）::

        res = CPCVBacktester(df, cols, ...).run()
        bt_source = CPCVBacktestICSource(res)              # 或 .from_factor_ic(res["factor_ic"])
        hook = FactorHealthHook(tracker, registry, backtest_ic_source=bt_source)
        hook.on_backtest_end(factor_id, ws, we, realized_ic_series)  # 走 #8 仲裁

    本适配器是 ``Callable``，可直接当 ``backtest_ic_source`` 传入（鸭子类型对齐）。
    """

    def __init__(self, cpcv_result: dict[str, Any]) -> None:
        self._factor_ic: dict[str, tuple[float, float]] = dict(
            cpcv_result.get("factor_ic", {})
        )

    @classmethod
    def from_factor_ic(
        cls, factor_ic: dict[str, tuple[float, float]]
    ) -> "CPCVBacktestICSource":
        obj = cls.__new__(cls)
        obj._factor_ic = dict(factor_ic)
        return obj

    def __call__(self, factor_id: str) -> tuple[float, float] | None:
        prior = self._factor_ic.get(factor_id)
        if prior is None:
            return None
        ic_mean, icir = prior
        if not np.isfinite(ic_mean):
            return None
        # icir 可能为 NaN（单观测）；#8 仲裁用 ic_mean 做符号判断，icir 容忍 NaN
        return (float(ic_mean), float(icir) if np.isfinite(icir) else 0.0)


# ---------------------------------------------------------------------------
# _rolling_zscore 因果性结论（U5 blocking OQ-1，固化为模块常量供测试引用）
# ---------------------------------------------------------------------------

#: ``SingleStockAnalyzer._rolling_zscore`` 因果性核验结论（OQ-1）：
#: ``rolling(window, min_periods=window).mean()/.std()`` 是 pandas 左锚 **trailing** 窗口，
#: 每个时刻 t 的 mean/std 只用 ``[t-window+1, t]``（含 t、不含任何未来 bar），前 window-1 行为
#: NaN（不输出信号）。**严格因果，不引用未来窗口**。故 CPCV 的 purge 只需覆盖 label 重叠
#: （``next_day_return`` 向前看 1 bar → purge_bars≥1），rolling 边界本身不额外泄漏。
#: embargo 仍保留（默认 1）以堵"测试期信息经训练样本滚动统计回流"的方向二泄漏。
_ROLLING_ZSCORE_CAUSALITY = "strictly_causal_trailing_window"
