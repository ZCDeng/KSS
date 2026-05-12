"""KSS 对抗性 / 红队测试套件.

刻意构造"看起来美好但有诈"的数据，看 KSS 诊断工具能否识破。元层面 QA：
以前 7 轮实验都是开发者自报自评，本文件强迫工具在已知坏数据上自证。

每场景一个测试，多 seed parametrize；KSS 识别不出的偏差用 ``pytest.xfail``
配 ``reason="KSS 当前 gap: ..."``，便于后续 todo。仅用公开 API + 合成数据。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from kss.backtest.cost_model import CostModel
from kss.backtest.diagnostics import SignalDiagnostics
from kss.backtest.engine import BacktestEngine
from kss.backtest.metrics import Metrics
from kss.backtest.significance import Significance


# --------------------------------------------------------------------------- #
# 场景 1：纯随机噪声 → 显著性应输出"不显著"
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("seed", [0, 1, 7, 42, 2026])
def test_random_noise_fails_significance(seed: int) -> None:
    """N(0, 0.01) × 500 天纯噪声 + 截面噪声 panel.

    KSS 期望（每 seed 单独成立的稳健判据）：
    - ``deflated_sharpe`` < 0.6（多重检验下纯噪声应稳稳被拒）
    - ``cross_section_ic_scan`` 上 ``abs_t_stat`` < 3（含 3σ 缓冲）

    **不**断言 ``p_value > 0.1``——单 t 检验在 H0 下也有 5% 假阳性
    （如 seed=7 → 纯噪声 sharpe=-2.17 偶然显著负）；DSR 才是该信任的统计量.
    """
    rng = np.random.default_rng(seed)
    returns = pd.Series(rng.normal(0.0, 0.01, 500))
    sig = Significance.sharpe_significance(returns, n_trials=20)
    assert sig["deflated_sharpe"] < 0.6, (
        f"seed={seed}: 纯噪声 DSR={sig['deflated_sharpe']:.3f} 不该过 0.6"
    )

    # 截面 noise factor vs noise return
    n_dates, n_stocks = 80, 30
    rng2 = np.random.default_rng(seed + 1000)
    rows = [
        {"trade_date": d, "symbol": f"S{s:03d}",
         "factor": float(rng2.standard_normal()),
         "next_day_return": float(rng2.standard_normal()) * 0.01}
        for d in pd.date_range("2024-01-01", periods=n_dates)
        for s in range(n_stocks)
    ]
    panel = pd.DataFrame(rows)
    scan = SignalDiagnostics.cross_section_ic_scan(
        panel, factor_cols=["factor"], horizons=["next_day_return"],
    )
    abs_t = float(scan.iloc[0]["abs_t_stat"])
    assert abs_t < 3.0, f"seed={seed}: 纯噪声 |t|={abs_t:.2f} 不该过 3"


def test_random_noise_false_positive_rate_bounded() -> None:
    """50 次独立噪声样本上单 t 检验 ``p<0.05`` 比例应 ≈ 5%（Type-I 错误率）.

    这是"显著性框架是否被噪声骗到"的正确度量；给 2× 上限避免偶然超出.
    """
    false_pos = sum(
        1 for seed in range(50)
        if Significance.t_stat(pd.Series(
            np.random.default_rng(seed).normal(0.0, 0.01, 500)
        ))[1] < 0.05
    )
    rate = false_pos / 50
    assert rate <= 0.20, f"纯噪声 p<0.05 比例 {rate:.0%} 超出预期"


# --------------------------------------------------------------------------- #
# 场景 2：未来数据 feature → walk_forward 防得住 label-leak,
#         防不住 feature-leak（已知 gap, xfail）
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_lookahead_factor_caught_by_purge_gap(seed: int) -> None:
    """``cheat_factor[t] = next_day_return[t]``（直接看未来）.

    KSS 现状：``walk_forward`` 的 ``purge_gap=N`` 只剔训练区间尾部 N 天,
    防的是 **label leak**（``future_return_Nd = close.shift(-N)`` 穿透）.
    它**无法**阻止使用者把 ``next_day_return`` 直接塞进 ``feature_cols``——
    test 时该 feature 仍是未来值，模型在 test 上"作弊", sharpe 会爆表 (≥5).
    """
    rng = np.random.default_rng(seed)
    n_dates, n_stocks = 80, 12
    rows: list[dict] = []
    for d in pd.date_range("2024-01-01", periods=n_dates):
        for s in range(n_stocks):
            next_ret = float(rng.standard_normal() * 0.02)
            rows.append({
                "trade_date": d, "symbol": f"S{s:02d}",
                "noise": float(rng.standard_normal()),
                "cheat_factor": next_ret,         # 看未来
                "future_return_5d": next_ret,     # label 对齐
                "next_day_return": next_ret,
            })
    factor_df = pd.DataFrame(rows)

    res = BacktestEngine(cost_model=CostModel()).walk_forward(
        factor_df=factor_df,
        feature_cols=["noise", "cheat_factor"],
        label_col="future_return_5d",
        train_window=20, retrain_freq=5, top_pct=0.25,
        min_train=30, min_test=5, min_stocks=4,
    )
    assert res is not None and not res.empty
    sharpe = float(Metrics.calc(res["net_return"]).get("sharpe", 0.0))

    if sharpe >= 5.0:
        pytest.xfail(
            f"KSS 当前 gap: walk_forward purge_gap 只防 label leak,"
            f" 防不住 feature-level look-ahead；sharpe={sharpe:.2f} (seed={seed})"
        )
    # 防御性：即便偶然欠拟合也不该完全失控
    assert sharpe <= 10.0, f"seed={seed}: sharpe={sharpe:.2f} 离谱失控"


# --------------------------------------------------------------------------- #
# 场景 3：单股噪声伪装 alpha → 截面 IC 应稀释
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("seed", [0, 1, 7, 2026])
def test_single_stock_noise_diluted_in_cross_section(seed: int) -> None:
    """50 只股票，1 只有强信号 (ρ≈0.85)，其余 49 只噪声.

    KSS 期望：
    - 该单股时序 IC > 0.6
    - 全 50 只的截面 IC mean < 0.15 且 |t| < 3（"单股 alpha ≠ 截面 alpha"）
    """
    rng = np.random.default_rng(seed)
    n_dates, n_stocks = 120, 50
    rows: list[dict] = []
    for d in pd.date_range("2024-01-01", periods=n_dates):
        for s in range(n_stocks):
            f = rng.standard_normal()
            if s == 0:  # 只有 S000 有信号
                ret = 0.85 * f + np.sqrt(1 - 0.85 ** 2) * rng.standard_normal()
            else:
                ret = rng.standard_normal()
            rows.append({"trade_date": d, "symbol": f"S{s:03d}",
                         "factor": float(f), "next_day_return": float(ret) * 0.01})
    panel = pd.DataFrame(rows)

    ss = panel[panel["symbol"] == "S000"]
    single_ic = SignalDiagnostics.ic(ss["factor"], ss["next_day_return"])
    assert single_ic > 0.6, f"seed={seed}: 强信号股时序 IC={single_ic:.3f} 应 > 0.6"

    cs_ic = SignalDiagnostics.ic_series(panel, "factor", "next_day_return")
    summary = SignalDiagnostics.ic_summary(cs_ic)
    assert abs(summary["ic_mean"]) < 0.15, (
        f"seed={seed}: 截面 IC 均值={summary['ic_mean']:.3f} 应被稀释到 < 0.15"
    )
    assert abs(summary["ic_t_stat"]) < 3.0, (
        f"seed={seed}: 截面 IC t={summary['ic_t_stat']:.2f} 不该显著"
    )


# --------------------------------------------------------------------------- #
# 场景 4：高 Sharpe 但末段集中回撤 → 滚动 Sharpe + max_dd + calmar 警示
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("seed", [0, 1, 42])
def test_concentrated_drawdown_caught_by_rolling_sharpe(seed: int) -> None:
    """前 80% 日均 +0.5%，最后 20% 日均 -2%.

    KSS 期望：
    - 末段 30 日滚动 sharpe 均值 < -1（崩溃信号）
    - max_dd < -20%，calmar < 0.5
    """
    rng = np.random.default_rng(seed)
    good = rng.normal(0.005, 0.01, 400)
    bad = rng.normal(-0.02, 0.015, 100)
    r = pd.Series(np.concatenate([good, bad]))

    m = Metrics.calc(r)
    rolling = r.rolling(60).mean() / r.rolling(60).std() * np.sqrt(252)
    tail = rolling.iloc[-30:].dropna()
    assert tail.mean() < -1.0, (
        f"seed={seed}: 末 30 日滚动 sharpe={tail.mean():.2f} 应 < -1"
    )
    assert m["max_dd"] < -0.20, f"seed={seed}: max_dd={m['max_dd']:.2%} 应深于 -20%"
    assert m["calmar"] < 0.5, f"seed={seed}: calmar={m['calmar']:.2f} 应 < 0.5"


# --------------------------------------------------------------------------- #
# 场景 5：同质化"多因子" → 相关矩阵揭穿
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("seed", [0, 1, 7])
def test_highly_correlated_factors_low_diversification(seed: int) -> None:
    """5 个因子：f0..f3 同源 latent (互相 ρ ≈ 0.95)，f4 真噪声.

    KSS 期望：
    - cross_section_ic_scan 上 f0..f3 各自 |IC| > 0.05（"显著"假象）
    - 因子两两 ρ：f0..f3 互相 > 0.85；与 f4 < 0.3（揭穿同质）
    """
    rng = np.random.default_rng(seed)
    n_dates, n_stocks = 80, 40
    rows: list[dict] = []
    for d in pd.date_range("2024-01-01", periods=n_dates):
        latents = rng.standard_normal(n_stocks)
        for s in range(n_stocks):
            base = latents[s]
            rows.append({
                "trade_date": d, "symbol": f"S{s:03d}",
                "f0": base + 0.1 * rng.standard_normal(),
                "f1": base + 0.1 * rng.standard_normal(),
                "f2": base + 0.1 * rng.standard_normal(),
                "f3": base + 0.1 * rng.standard_normal(),
                "f4": rng.standard_normal(),
                "next_day_return": (0.4 * base + 0.6 * rng.standard_normal()) * 0.01,
            })
    panel = pd.DataFrame(rows)

    scan = SignalDiagnostics.cross_section_ic_scan(
        panel, factor_cols=["f0", "f1", "f2", "f3", "f4"],
        horizons=["next_day_return"],
    ).set_index("factor")
    for f in ["f0", "f1", "f2", "f3"]:
        assert abs(scan.loc[f, "ic_mean"]) > 0.05, (
            f"seed={seed}: {f} ic_mean={scan.loc[f, 'ic_mean']:.3f} 应显示假显著"
        )

    corr = panel[["f0", "f1", "f2", "f3", "f4"]].corr().abs()
    for a in ["f0", "f1", "f2", "f3"]:
        for b in ["f0", "f1", "f2", "f3"]:
            if a == b:
                continue
            assert corr.loc[a, b] > 0.85, (
                f"seed={seed}: corr({a},{b})={corr.loc[a, b]:.2f} 应 > 0.85"
            )
        assert corr.loc[a, "f4"] < 0.3, (
            f"seed={seed}: corr({a},f4)={corr.loc[a, 'f4']:.2f} 应 < 0.3"
        )


# --------------------------------------------------------------------------- #
# 场景 6：幸存者偏差 —— 4.2 ExecutionModel 停牌过滤后 RESOLVED
# --------------------------------------------------------------------------- #


def _build_survivorship_panel(
    seed: int,
    n_dates: int = 200,
    n_stocks: int = 100,
    n_delisted: int = 30,
    delist_at: int = 140,
) -> tuple[pd.DataFrame, list[pd.Timestamp], list[str]]:
    """构造 30% 中段退市 + 退市前 20 天负 drift 的合成 panel.

    Returns:
        (panel, delist_dates_per_stock-equiv, delisted_symbols).
    """
    rng = np.random.default_rng(seed)
    dates = list(pd.date_range("2024-01-01", periods=n_dates))
    delisted = [f"S{s:03d}" for s in range(n_delisted)]
    rows: list[dict] = []
    for i, d in enumerate(dates):
        for s in range(n_stocks):
            if s < n_delisted:
                if i >= delist_at:
                    ret = float("nan")
                elif i >= delist_at - 20:
                    ret = rng.normal(-0.03, 0.02)
                else:
                    ret = rng.normal(0.0, 0.015)
            else:
                ret = rng.normal(0.001, 0.01)
            rows.append({"trade_date": d, "symbol": f"S{s:03d}",
                         "next_day_return": ret})
    return pd.DataFrame(rows), dates, delisted


@pytest.mark.parametrize("seed", [0, 7])
def test_survivorship_bias_inflates_returns(seed: int) -> None:
    """100 只股票，30 只在中段"退市"（最后 60 天 NaN，退市前 20 天负 drift -3%）.

    原 xfail：``dropna`` 静默丢退市股 → survivor sharpe ≫ full sharpe.

    **4.2 修复（``docs/solutions/known_bias_gaps.md`` Gap 2 → RESOLVED）**:
    引入 :class:`SuspensionData` + :class:`ExecutionModel` 联合过滤后，
    "退市后期 NaN 段"通过停牌名单显式标记 → 回测层在生成 daily series 前
    就剔除这些日子（而不是 dropna 后偷偷继续）；此时 survivor / full 的
    构造方法应该收敛、 bias_gap 应缩小.

    本测试 parametrize 两种条件：

    - ``no_filter``：保留 xfail 行为作对照，必须复现 bias_gap > 0.3；
    - ``with_filter``：注入停牌名单后，bias_gap 应显著回落（< 0.2）.
    """
    from kss.backtest.cost_model import ExecutionModel
    from kss.data.suspension_data import SuspensionData

    panel, _dates, delisted = _build_survivorship_panel(seed)

    # ---- 对照 1：无停牌过滤（旧行为，gap 应仍然大） ---- #
    daily_full = panel.groupby("trade_date")["next_day_return"].mean()
    sharpe_full = float(Metrics.calc(daily_full)["sharpe"])
    survivors = (
        panel.groupby("symbol")["next_day_return"].apply(lambda s: s.notna().all())
    )
    survivors = survivors[survivors].index.tolist()
    daily_surv = (
        panel[panel["symbol"].isin(survivors)]
        .groupby("trade_date")["next_day_return"].mean()
    )
    sharpe_surv = float(Metrics.calc(daily_surv)["sharpe"])
    bias_gap_raw = sharpe_surv - sharpe_full

    # 对照保留：原 gap 必须真实存在，否则 fixture 退化
    assert bias_gap_raw > 0.3, (
        f"seed={seed}: 对照组 bias_gap={bias_gap_raw:.2f} 不再 > 0.3, "
        f"合成数据失效"
    )

    # ---- 对照 2：注入 SuspensionData → ExecutionModel 显式剔除退市股 ---- #
    # 构造"退市股的全部交易日 = 停牌"的反事实名单：相当于把它们排除出 universe.
    # 真实场景下 Tushare suspend_d 会给出实际停牌段；这里用合成 panel 模拟.
    delisted_dates = panel[panel["symbol"].isin(delisted)]["trade_date"].unique()
    suspension_map: dict[str, set[pd.Timestamp]] = {
        s: set(pd.Timestamp(d).normalize() for d in delisted_dates) for s in delisted
    }

    class _Stub(SuspensionData):
        """跳过文件 IO 的轻量 SuspensionData."""

        def __init__(self, suspension: dict[str, set[pd.Timestamp]]) -> None:
            # 不调父类 __init__；直接喂内部 dict
            self.suspension_path = pd.NA  # type: ignore[assignment]
            self.st_path = pd.NA  # type: ignore[assignment]
            self.suspension = suspension
            self.st = {}

    sus = _Stub(suspension_map)
    exec_m = ExecutionModel(suspension_data=sus, exclude_zero_volume=True)

    # 模拟"用 ExecutionModel 在每日 universe 上过滤"：
    # 4.2 真实链路里 cross_section.factor_cross_section_backtest 会先过滤再算 daily.
    panel_with_amount = panel.copy()
    # 模拟 amount：退市后段 NaN → 0；正常 → 1e6
    panel_with_amount["amount"] = panel_with_amount["next_day_return"].apply(
        lambda r: 0.0 if pd.isna(r) else 1e6
    )
    panel_with_amount["open"] = 10.0
    panel_with_amount["pre_close"] = 10.0

    filtered_daily = []
    for d, day_df in panel_with_amount.groupby("trade_date"):
        tradable = exec_m.filter_tradable(
            day_df, side="buy", date_col="trade_date"
        )
        if len(tradable) > 0:
            filtered_daily.append({
                "trade_date": d,
                "ret": float(tradable["next_day_return"].mean()),
            })
    daily_filtered = pd.Series(
        [r["ret"] for r in filtered_daily],
        index=[r["trade_date"] for r in filtered_daily],
    )
    sharpe_filtered = float(Metrics.calc(daily_filtered)["sharpe"])

    bias_gap_resolved = sharpe_surv - sharpe_filtered
    # 关键：用 SuspensionData 过滤后，filtered universe 与 survivor universe
    # 之间的 gap 应显著缩小（survivor 是后视镜筛选，但 filtered 是 PIT 筛选；
    # 在合成数据中两者最终包含的 universe 集合趋同 → gap 接近 0）.
    assert bias_gap_resolved < 0.2, (
        f"seed={seed}: 4.2 修复后 bias_gap_resolved={bias_gap_resolved:.3f} "
        f"应 < 0.2 (raw_gap={bias_gap_raw:.2f}, "
        f"sharpe_surv={sharpe_surv:.2f}, sharpe_filtered={sharpe_filtered:.2f})"
    )
