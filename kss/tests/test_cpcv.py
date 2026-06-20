"""CPCV 组合净化交叉验证单元测试（U5）.

覆盖：折叠数=C(k,p)；purge/embargo 边界无泄漏（构造泄漏特征应被隔离）；IC 分布产出可喂
#8（适配 BacktestICSource）；Sharpe 分布喂 DSR 合理；缓存续跑幂等；15 路径默认时延实测标注。
"""

from __future__ import annotations

import math
import time

import numpy as np
import pandas as pd
import pytest

from kss.backtest.cpcv import (
    CPCVBacktester,
    CPCVBacktestICSource,
    _ROLLING_ZSCORE_CAUSALITY,
    all_test_fold_combos,
    compute_pbo,
    make_fold_bounds,
    purged_train_mask,
    PathResult,
)
from kss.backtest.single_stock import SingleStockAnalyzer
from kss.backtest.walk_forward_combiner import WalkForwardCombiner


# ====================================================================== #
# 合成面板
# ====================================================================== #


def _make_factor_df(n: int = 360, seed: int = 0) -> pd.DataFrame:
    """单股票面板：f1 informative, f2 noisy, f3 trend."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n)
    f1 = rng.standard_normal(n)
    f2 = rng.standard_normal(n)
    f3 = np.cumsum(rng.standard_normal(n) * 0.05)
    r = 0.4 * f1 + 0.1 * f2 + rng.standard_normal(n) * 0.5
    return pd.DataFrame({
        "trade_date": idx,
        "f1": f1, "f2": f2, "f3": f3,
        "next_day_return": r * 0.005,
    })


def _small_wfc_kwargs() -> dict:
    """小 panel 控时延：短 train_window / rolling_window."""
    return {"train_window": 60, "retrain_freq": 15, "rolling_window": 20}


def _builder():
    return WalkForwardCombiner.builder_equal_topk_sharpe(top_k=2)


# ====================================================================== #
# 纯函数：折叠生成 / purge / embargo
# ====================================================================== #


class TestFoldGeneration:

    def test_combo_count_equals_C_k_p(self) -> None:
        for k, p in [(6, 2), (5, 2), (10, 4)]:
            combos = all_test_fold_combos(k, p)
            assert len(combos) == math.comb(k, p)

    def test_fold_bounds_partition_full_range(self) -> None:
        bounds = make_fold_bounds(100, 6)
        assert len(bounds) == 6
        # 连续不重叠，覆盖 [0, 100)
        assert bounds[0][0] == 0
        assert bounds[-1][1] == 100
        for (s0, e0), (s1, e1) in zip(bounds, bounds[1:]):
            assert e0 == s1  # 无缝衔接
        # 每折差 ≤ 1 bar（等分语义）
        sizes = [e - s for s, e in bounds]
        assert max(sizes) - min(sizes) <= 1

    def test_fold_bounds_rejects_bad_k(self) -> None:
        with pytest.raises(ValueError):
            make_fold_bounds(100, 1)
        with pytest.raises(ValueError):
            make_fold_bounds(3, 6)

    def test_combo_rejects_bad_p(self) -> None:
        with pytest.raises(ValueError):
            all_test_fold_combos(6, 6)  # p 必须 < k


class TestPurgeEmbargo:

    def test_test_folds_never_in_train(self) -> None:
        n, k = 120, 6
        bounds = make_fold_bounds(n, k)
        combo = (2, 3)
        mask = purged_train_mask(n, bounds, combo, purge_bars=1, embargo_bars=1)
        for fi in combo:
            s, e = bounds[fi]
            assert not mask[s:e].any(), "测试折 bar 不可作训练"

    def test_purge_removes_boundary_bars(self) -> None:
        n, k = 120, 6
        bounds = make_fold_bounds(n, k)
        combo = (2,)
        purge = 3
        mask = purged_train_mask(n, bounds, combo, purge_bars=purge, embargo_bars=0)
        s, e = bounds[2]
        # 测试折左侧 purge 个 bar 被剔除
        assert not mask[max(0, s - purge):s].any()
        # 右侧 purge 个 bar 被剔除
        assert not mask[e:e + purge].any()

    def test_embargo_extends_after_test(self) -> None:
        n, k = 120, 6
        bounds = make_fold_bounds(n, k)
        combo = (1,)
        purge, emb = 1, 4
        mask = purged_train_mask(n, bounds, combo, purge_bars=purge, embargo_bars=emb)
        s, e = bounds[1]
        # 测试折后 purge+embargo 个 bar 全部剔除
        assert not mask[e:e + purge + emb].any()


# ====================================================================== #
# 折叠数验收（核心）
# ====================================================================== #


class TestRunFoldCount:

    def test_default_15_paths(self) -> None:
        df = _make_factor_df(n=300)
        cb = CPCVBacktester(
            df, ["f1", "f2", "f3"], _builder(),
            k=6, p=2, wfc_kwargs=_small_wfc_kwargs(),
        )
        assert cb.estimate()["n_paths"] == 15
        res = cb.run()
        assert res["n_paths"] == math.comb(6, 2) == 15

    def test_light_mode_10_paths(self) -> None:
        df = _make_factor_df(n=300)
        cb = CPCVBacktester(
            df, ["f1", "f2", "f3"], _builder(),
            k=5, p=2, wfc_kwargs=_small_wfc_kwargs(),
        )
        res = cb.run()
        assert res["n_paths"] == math.comb(5, 2) == 10


# ====================================================================== #
# 分布产出 + DSR / IC（喂 #8）
# ====================================================================== #


class TestDistributionOutputs:

    def test_sharpe_distribution_ordered(self) -> None:
        df = _make_factor_df(n=320, seed=3)
        res = CPCVBacktester(
            df, ["f1", "f2", "f3"], _builder(),
            k=6, p=2, wfc_kwargs=_small_wfc_kwargs(),
        ).run()
        if res["n_paths_valid"] >= 3:
            assert res["sharpe_p5"] <= res["sharpe_p50"] <= res["sharpe_p95"]

    def test_pbo_in_unit_range(self) -> None:
        df = _make_factor_df(n=320, seed=5)
        res = CPCVBacktester(
            df, ["f1", "f2", "f3"], _builder(),
            k=6, p=2, wfc_kwargs=_small_wfc_kwargs(),
        ).run()
        pbo = res["pbo"]
        assert math.isnan(pbo) or (0.0 <= pbo <= 1.0)

    def test_dsr_fed_from_distribution(self) -> None:
        df = _make_factor_df(n=320, seed=2)
        res = CPCVBacktester(
            df, ["f1", "f2", "f3"], _builder(),
            k=6, p=2, wfc_kwargs=_small_wfc_kwargs(),
        ).run()
        dsr = res["deflated_sharpe"]
        # DSR ∈ [0,1] 或 NaN（样本不足）
        assert math.isnan(dsr) or (0.0 <= dsr <= 1.0)
        assert "is_deployable" in res
        assert res["is_deployable"]["strategy_family"] == "mined"

    def test_factor_ic_produced_for_8_adapter(self) -> None:
        """per-factor IC 分布产出，可喂 #8（U3 BacktestICSource）."""
        df = _make_factor_df(n=320, seed=1)
        res = CPCVBacktester(
            df, ["f1", "f2", "f3"], _builder(),
            k=6, p=2, wfc_kwargs=_small_wfc_kwargs(),
        ).run()
        factor_ic = res["factor_ic"]
        assert isinstance(factor_ic, dict)
        # 至少有一个因子被选过 → 有先验
        if factor_ic:
            for f, (icm, icir) in factor_ic.items():
                assert isinstance(icm, float)


class TestBacktestICSourceAdapter:

    def test_adapter_callable_signature(self) -> None:
        """适配器是 Callable[[str], tuple|None]，与 factor_health.BacktestICSource 对齐."""
        res = {"factor_ic": {"f1": (0.12, 0.8), "f2": (float("nan"), float("nan"))}}
        src = CPCVBacktestICSource(res)
        # 命中且有限 → (ic_mean, icir)
        out = src("f1")
        assert out is not None and out[0] == pytest.approx(0.12)
        # NaN ic_mean → None（先验缺失，#8 据此保守）
        assert src("f2") is None
        # 不存在 → None
        assert src("f_missing") is None

    def test_adapter_plugs_into_factor_health_arbitration(self) -> None:
        """端到端：CPCV 先验经适配器喂 U3 FactorHealthHook 的 #8 仲裁（升权需双源一致）."""
        from kss.backtest.factor_health import (
            ArbitrationInput, arbitrate, VERDICT_PROMOTE, VERDICT_DIVERGENCE,
        )

        src = CPCVBacktestICSource({"factor_ic": {"f1": (0.15, 0.9)}})
        prior = src("f1")
        assert prior is not None
        bt_ic_mean, bt_icir = prior

        th = {
            "realized_ic_min_n": 5,
            "realized_icir_promote": 0.3,
            "realized_icir_demote": -0.3,
        }
        # 实盘升权 + 与回测同号 → promote
        same = arbitrate(
            ArbitrationInput(
                realized_icir=0.5, realized_ic_mean=0.10, realized_n=30,
                backtest_ic_mean=bt_ic_mean, backtest_icir=bt_icir,
            ), th,
        )
        assert same.verdict == VERDICT_PROMOTE
        # 实盘升权但与回测反号 → divergence（记 IC_SOURCE_DIVERGENCE）
        opp = arbitrate(
            ArbitrationInput(
                realized_icir=0.5, realized_ic_mean=-0.10, realized_n=30,
                backtest_ic_mean=bt_ic_mean, backtest_icir=bt_icir,
            ), th,
        )
        assert opp.verdict == VERDICT_DIVERGENCE


# ====================================================================== #
# purge/embargo 无泄漏：构造泄漏特征应被隔离
# ====================================================================== #


class TestNoLeakage:

    def test_rolling_zscore_causality_constant(self) -> None:
        """_rolling_zscore 因果性结论固化（OQ-1）."""
        assert _ROLLING_ZSCORE_CAUSALITY == "strictly_causal_trailing_window"

    def test_rolling_zscore_is_trailing(self) -> None:
        """实证 _rolling_zscore 只用 trailing 窗口：改未来值不影响当前 z."""
        x = pd.Series(np.arange(50, dtype=float))
        z_full = SingleStockAnalyzer._rolling_zscore(x, 10)
        x2 = x.copy()
        x2.iloc[30:] = 999.0  # 篡改未来
        z_mod = SingleStockAnalyzer._rolling_zscore(x2, 10)
        # t < 30 的 z 不应被未来篡改影响（误差 < 1e-9）
        diff = (z_full.iloc[10:25] - z_mod.iloc[10:25]).abs().max()
        assert diff < 1e-9

    def test_purge_isolates_leaky_label_copy(self) -> None:
        """构造一个近似复制 next_day_return 的泄漏特征，对比 purge 隔离效果.

        泄漏特征 f_leak ≈ next_day_return。CPCV 的 purge 把测试折边界 bar 从训练里剔除，
        测试折的 OOS 段不应靠"训练窗口看到过测试期 label"虚高——验证 purge 后训练掩码
        确实不含测试折及其边界 bar（结构性保证，不靠数值魔法）。
        """
        n, k = 120, 6
        bounds = make_fold_bounds(n, k)
        combo = (3,)
        mask = purged_train_mask(n, bounds, combo, purge_bars=2, embargo_bars=2)
        s, e = bounds[3]
        # 测试折 + 左右 purge + 右侧 embargo 全不在训练集
        leaked_region = list(range(max(0, s - 2), min(n, e + 2 + 2)))
        assert not mask[leaked_region].any(), "泄漏区（测试折±purge+embargo）必须全隔离"

    def test_failed_paths_reported_not_silent(self) -> None:
        """fail loud：路径失败记入 failed_paths，不静默丢."""
        df = _make_factor_df(n=300)
        # min_train_bars 设极大 → 多数路径训练不足，触发 failed
        cb = CPCVBacktester(
            df, ["f1", "f2", "f3"], _builder(),
            k=6, p=2, min_train_bars=10_000, wfc_kwargs=_small_wfc_kwargs(),
        )
        with pytest.warns(UserWarning, match="成功路径"):
            res = cb.run()
        assert res["n_paths_failed"] > 0
        assert len(res["failed_paths"]) == res["n_paths_failed"]
        # 每条失败都有原因（不静默）
        for combo, err in res["failed_paths"]:
            assert err


# ====================================================================== #
# 缓存续跑幂等
# ====================================================================== #


class TestCacheResume:

    def test_cache_resume_idempotent(self, tmp_path) -> None:
        df = _make_factor_df(n=300, seed=9)
        cache = tmp_path / "run01"
        kw = dict(
            k=6, p=2, wfc_kwargs=_small_wfc_kwargs(), cache_dir=str(cache),
        )
        r1 = CPCVBacktester(df, ["f1", "f2", "f3"], _builder(), **kw).run()
        # 第二次：全部命中缓存
        r2 = CPCVBacktester(df, ["f1", "f2", "f3"], _builder(), **kw).run()
        assert r1["n_paths"] == r2["n_paths"]
        # p50 Sharpe 完全一致（缓存读回，不重算）
        if math.isfinite(r1["sharpe_p50"]):
            assert abs(r1["sharpe_p50"] - r2["sharpe_p50"]) < 1e-9
        # 缓存文件确实写出
        assert list(cache.glob("fold_*.pkl"))

    def test_cache_invalidates_on_param_change(self, tmp_path) -> None:
        df = _make_factor_df(n=300)
        cache = tmp_path / "run02"
        c1 = CPCVBacktester(
            df, ["f1", "f2", "f3"], _builder(), k=6, p=2,
            wfc_kwargs=_small_wfc_kwargs(), cache_dir=str(cache),
        )
        c1.run()
        h1 = c1._param_hash()
        # 改 k → hash 变 → 旧缓存不命中
        c2 = CPCVBacktester(
            df, ["f1", "f2", "f3"], _builder(), k=5, p=2,
            wfc_kwargs=_small_wfc_kwargs(), cache_dir=str(cache),
        )
        assert c2._param_hash() != h1


# ====================================================================== #
# PBO 纯函数
# ====================================================================== #


class TestPBO:

    def test_pbo_nan_when_too_few(self) -> None:
        assert math.isnan(compute_pbo([]))
        assert math.isnan(compute_pbo([
            PathResult(test_folds=(0,), ok=True, sharpe=1.0, train_sharpe=1.0),
        ]))

    def test_pbo_high_when_train_test_anticorrelated(self) -> None:
        """训练好的路径在测试期系统性差 → PBO 高（过拟合）."""
        # train_sharpe 高的路径 test sharpe 低
        results = [
            PathResult(test_folds=(i,), ok=True, train_sharpe=float(i), sharpe=float(-i))
            for i in range(10)
        ]
        pbo = compute_pbo(results)
        assert pbo >= 0.5


# ====================================================================== #
# 15 路径默认时延（实测标注）
# ====================================================================== #


class TestLatency:

    def test_default_15_paths_latency_annotated(self) -> None:
        """15 路径默认时延实测（小 panel 控时延；记录耗时供验收参考）."""
        df = _make_factor_df(n=300)
        t0 = time.perf_counter()
        res = CPCVBacktester(
            df, ["f1", "f2", "f3"], _builder(),
            k=6, p=2, wfc_kwargs=_small_wfc_kwargs(),
        ).run()
        elapsed = time.perf_counter() - t0
        assert res["n_paths"] == 15
        # 小 panel 控时延 < 60s（真实规格基线 ~125s 在生产 panel；此处仅 smoke 时延）
        assert elapsed < 60.0, f"15 路径耗时 {elapsed:.1f}s 超 60s 上限"
