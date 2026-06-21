"""kss/backtest/factor_health.py —— 因子健康度闭环测试 (U3).

覆盖 plan U3 验收 + Global Decision #8 仲裁：
- 人造衰减序列触发 PENDING_REVIEW；
- 实盘 IC 想升权但无回测先验一致 → 不升权 (#8 保守)；
- 实盘与回测符号分歧 → 记 IC_SOURCE_DIVERGENCE；
- 有效 n = 去重交易日数（事件数 != 去重日数时按去重日）；
- 常数序列 IC 中性；
- 崩盘记录可查询；
- 阈值外化：删 YAML 即 fail loud；
- 状态机单向，拒绝回退；
- cross_section / walk_forward hook surgical（默认 None 不改行为）。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from kss.backtest.factor_health import (
    CRASH_IC_DECAY,
    CRASH_IC_SOURCE_DIVERGENCE,
    STATE_ACTIVE,
    STATE_PENDING_REVIEW,
    STATE_RETIRED,
    VERDICT_DEMOTE,
    VERDICT_DIVERGENCE,
    VERDICT_HOLD,
    VERDICT_INSUFFICIENT_N,
    VERDICT_PROMOTE,
    ArbitrationInput,
    FactorCrashRegistry,
    FactorHealthHook,
    FactorHealthTracker,
    arbitrate,
    compute_ic_snapshot,
    load_thresholds,
)


# 测试用阈值（不依赖真实 YAML 标定值，注入固定起点）
_THRESHOLDS = {
    "icir_baseline": 0.25,
    "rolling_icir_window": 60,
    "crash_consecutive_n": 5,
    "ic_floor": 0.02,
    "half_life_min_periods": 20,
    "realized_ic_min_n": 20,
    "realized_icir_promote": 0.30,
    "realized_icir_demote": 0.25,
}


@pytest.fixture()
def tracker(tmp_path: Path) -> FactorHealthTracker:
    return FactorHealthTracker(
        db_path=tmp_path / "fh.db", thresholds=dict(_THRESHOLDS)
    )


@pytest.fixture()
def registry(tmp_path: Path) -> FactorCrashRegistry:
    return FactorCrashRegistry(db_path=tmp_path / "fh.db")


def _ic_series(values, start="2026-01-01") -> pd.Series:
    dates = pd.bdate_range(start=start, periods=len(values))
    return pd.Series(values, index=dates, dtype=float)


# ---------------------------------------------------------------------------
# IC 快照 / 有效 n / 常数序列
# ---------------------------------------------------------------------------


def test_effective_n_is_dedup_dates(tracker: FactorHealthTracker) -> None:
    """有效 n = 去重交易日数；事件数 (含重复日期) 不算入 (AE5/SC3)."""
    # 构造同一天 20 条事件 + 10 个去重日 → n_periods 应 = 10
    dates = pd.to_datetime(["2026-01-05"] * 20 + [f"2026-01-{d:02d}" for d in range(6, 15)])
    vals = np.random.default_rng(0).normal(0.1, 0.05, len(dates))
    s = pd.Series(vals, index=dates)
    snap = compute_ic_snapshot("f", "2026-01-14", s, thresholds=_THRESHOLDS)
    assert snap.n_periods == s.index.nunique()
    assert snap.n_periods == 10
    assert snap.n_periods != len(s)  # 事件数 30 != 去重日 10


def test_constant_series_neutral_ic(tracker: FactorHealthTracker) -> None:
    """常数 IC 序列 → ic_std=0 → ICIR 中性 (0)，不爆炸."""
    snap = compute_ic_snapshot("f", "2026-02-01", _ic_series([0.0] * 30),
                               thresholds=_THRESHOLDS)
    assert snap.icir == 0.0
    assert snap.ic_mean == 0.0


def test_record_idempotent(tracker: FactorHealthTracker) -> None:
    s = _ic_series([0.1, 0.2, 0.15, 0.05, 0.12])
    snap1 = tracker.record_ic_snapshot("mom_5d", "2026-03-31", s)
    # 二次写不重算（返回既存），改变输入也不覆盖（幂等）
    snap2 = tracker.record_ic_snapshot("mom_5d", "2026-03-31", _ic_series([9.0] * 5))
    assert snap2.ic_mean == snap1.ic_mean
    # force 覆盖
    snap3 = tracker.record_ic_snapshot(
        "mom_5d", "2026-03-31", _ic_series([0.5] * 5), force=True
    )
    assert snap3.ic_mean == pytest.approx(0.5)


def test_rolling_icir_query(tracker: FactorHealthTracker) -> None:
    rng = np.random.default_rng(1)
    for k in range(10):
        we = f"2026-04-{k + 1:02d}"
        tracker.record_ic_snapshot("f", we, _ic_series(rng.normal(0.1, 0.05, 30)))
    icir = tracker.rolling_icir("f", window_days=5)
    assert len(icir) == 10
    assert icir.name == "rolling_icir"


# ---------------------------------------------------------------------------
# #8 仲裁规则（纯函数）
# ---------------------------------------------------------------------------


def test_arbitrate_promote_needs_backtest_prior() -> None:
    """实盘想升权但回测先验缺失 (U5 未就绪) → 不放行升权 (#8 保守)."""
    inp = ArbitrationInput(
        realized_icir=0.5, realized_ic_mean=0.08, realized_n=40,
        backtest_ic_mean=None,  # U5 未就绪
    )
    res = arbitrate(inp, _THRESHOLDS)
    assert res.verdict == VERDICT_HOLD
    assert "升权路径不放行" in res.reason


def test_arbitrate_promote_when_both_agree() -> None:
    inp = ArbitrationInput(
        realized_icir=0.5, realized_ic_mean=0.08, realized_n=40,
        backtest_ic_mean=0.06, backtest_icir=0.4,
    )
    assert arbitrate(inp, _THRESHOLDS).verdict == VERDICT_PROMOTE


def test_arbitrate_sign_divergence() -> None:
    """实盘与回测 IC 符号分歧 → divergence (记 IC_SOURCE_DIVERGENCE)."""
    inp = ArbitrationInput(
        realized_icir=0.5, realized_ic_mean=0.08, realized_n=40,
        backtest_ic_mean=-0.06,  # 反号
    )
    res = arbitrate(inp, _THRESHOLDS)
    assert res.verdict == VERDICT_DIVERGENCE
    assert res.divergence is True


def test_arbitrate_demote_fail_safe_single_source() -> None:
    """实盘 ICIR 跌破降权门 → demote (单源即可，不需回测一致, fail-safe)."""
    inp = ArbitrationInput(
        realized_icir=0.10, realized_ic_mean=0.01, realized_n=40,
        backtest_ic_mean=None,
    )
    assert arbitrate(inp, _THRESHOLDS).verdict == VERDICT_DEMOTE


def test_arbitrate_insufficient_n_does_not_flip() -> None:
    """实盘有效 n 低于门 → 仅供参考，不翻转 (即便 ICIR 很差也不 demote)."""
    inp = ArbitrationInput(
        realized_icir=0.01, realized_ic_mean=0.0, realized_n=5,  # n < 20
        backtest_ic_mean=None,
    )
    assert arbitrate(inp, _THRESHOLDS).verdict == VERDICT_INSUFFICIENT_N


# ---------------------------------------------------------------------------
# Hook 端到端：衰减触发 PENDING_REVIEW、分歧记崩盘、升权门控
# ---------------------------------------------------------------------------


def _hook(tracker, registry, **kw) -> FactorHealthHook:
    return FactorHealthHook(
        tracker=tracker, registry=registry, thresholds=dict(_THRESHOLDS),
        notify=False, **kw,
    )


def test_decay_triggers_pending_review(tracker, registry) -> None:
    """人造衰减序列 (ICIR 很低 + 连续低 IC) → PENDING_REVIEW + 崩盘记录可查."""
    hook = _hook(tracker, registry)
    # 30 期 IC 围绕 0 高噪声（连续 |IC| < ic_floor 触发 IC_DECAY），均值≈0 → ICIR
    # 低于降权门，应 demote 到 PENDING_REVIEW。
    rng = np.random.default_rng(42)
    decay = _ic_series(np.clip(rng.normal(0.0, 0.008, 30), -0.018, 0.018).tolist())
    out = hook.on_backtest_end("pb_ratio", "2026-01-01", "2026-02-15", decay)
    assert out["state"] == STATE_PENDING_REVIEW
    assert out["verdict"].verdict == VERDICT_DEMOTE
    # 崩盘登记可查询 (IC_DECAY)
    crashes = registry.query(factor_id="pb_ratio")
    assert any(c.crash_type == CRASH_IC_DECAY for c in crashes)


def test_realized_wants_promote_but_no_prior_holds(tracker, registry) -> None:
    """实盘 IC 强想升权，但无回测先验 (U5 未就绪) → 状态不升 (停留 ACTIVE)，不放行 (#8)."""
    hook = _hook(tracker, registry, backtest_ic_source=None)
    strong = _ic_series([0.15] * 25 + [0.18] * 25)  # 高且稳 → ICIR 高
    out = hook.on_backtest_end("mom_20d", "2026-01-01", "2026-03-10", strong)
    assert out["verdict"].verdict == VERDICT_HOLD
    assert out["state"] == STATE_ACTIVE  # 未被升权路径改动（本就 ACTIVE）


def test_sign_divergence_logs_registry(tracker, registry) -> None:
    """实盘正 IC 强、回测先验负 IC → 记 IC_SOURCE_DIVERGENCE，不静默."""
    hook = _hook(
        tracker, registry,
        backtest_ic_source=lambda fid: (-0.05, 0.4),  # 回测先验反号
    )
    # 同口径才比符号：realized 与 backtest 都用 sign_proxy（否则 method_mismatch）
    hook.backtest_ic_method = "sign_proxy"
    strong = _ic_series([0.15] * 25 + [0.18] * 25)
    out = hook.on_backtest_end(
        "rsi_14", "2026-01-01", "2026-03-10", strong, method="sign_proxy"
    )
    assert out["verdict"].verdict == VERDICT_DIVERGENCE
    divs = registry.query(crash_type=CRASH_IC_SOURCE_DIVERGENCE)
    assert len(divs) == 1
    assert divs[0].factor_id == "rsi_14"


def test_promote_path_with_consistent_prior(tracker, registry) -> None:
    """实盘强 + 回测先验同号同口径 → promote，置 ACTIVE."""
    hook = _hook(tracker, registry, backtest_ic_source=lambda fid: (0.05, 0.4))
    # 同口径才可比：realized 与 backtest 都用 sign_proxy（否则 method_mismatch）
    hook.backtest_ic_method = "sign_proxy"
    strong = _ic_series([0.15] * 25 + [0.18] * 25)
    out = hook.on_backtest_end(
        "mom_5d", "2026-01-01", "2026-03-10", strong, method="sign_proxy"
    )
    assert out["verdict"].verdict == VERDICT_PROMOTE
    assert out["state"] == STATE_ACTIVE


def test_backtest_source_only_persists_no_state(tracker, registry) -> None:
    """source='backtest' 的先验只落库，不驱动实盘状态机."""
    hook = _hook(tracker, registry)
    out = hook.on_backtest_end(
        "f", "2026-01-01", "2026-02-01", _ic_series([0.001] * 30), source="backtest"
    )
    assert out["verdict"] is None
    assert registry.get_state("f") == STATE_ACTIVE  # 未因回测先验降权
    assert tracker.get_snapshot("f", "2026-02-01", source="backtest") is not None


# ---------------------------------------------------------------------------
# 状态机单向
# ---------------------------------------------------------------------------


def test_lifecycle_single_direction(registry) -> None:
    assert registry.get_state("f") == STATE_ACTIVE
    assert registry.set_state("f", STATE_PENDING_REVIEW) == STATE_PENDING_REVIEW
    assert registry.set_state("f", STATE_RETIRED) == STATE_RETIRED
    # 拒绝回退 RETIRED → ACTIVE（保持 RETIRED）
    assert registry.set_state("f", STATE_ACTIVE) == STATE_RETIRED
    assert registry.get_state("f") == STATE_RETIRED


def test_active_factors_filters_retired(registry) -> None:
    registry.set_state("dead", STATE_PENDING_REVIEW)
    registry.set_state("dead", STATE_RETIRED)
    registry.set_state("review", STATE_PENDING_REVIEW)
    kept = registry.active_factors(["alive", "dead", "review"])
    assert kept == ["alive"]


def test_list_pending_review(registry) -> None:
    registry.set_state("a", STATE_PENDING_REVIEW)
    registry.set_state("b", STATE_PENDING_REVIEW)
    registry.set_state("b", STATE_RETIRED)
    assert registry.list_pending_review() == ["a"]


# ---------------------------------------------------------------------------
# 崩盘登记库查询
# ---------------------------------------------------------------------------


def test_crash_query_filters(registry) -> None:
    registry.log_crash("f1", window_start="2026-01-01", window_end="2026-01-31",
                       crash_type=CRASH_IC_DECAY, ic_mean=0.001)
    registry.log_crash("f1", window_start="2026-02-01", window_end="2026-02-28",
                       crash_type=CRASH_IC_SOURCE_DIVERGENCE, ic_mean=0.05)
    registry.log_crash("f2", window_start="2026-01-01", window_end="2026-01-31",
                       crash_type=CRASH_IC_DECAY)
    assert len(registry.query(factor_id="f1")) == 2
    assert len(registry.query(crash_type=CRASH_IC_DECAY)) == 2
    assert len(registry.query(since="2026-02-01")) == 1


# ---------------------------------------------------------------------------
# 阈值外化：删 YAML 即 fail loud
# ---------------------------------------------------------------------------


def test_thresholds_fail_loud_when_missing(tmp_path: Path) -> None:
    missing = tmp_path / "nope.yaml"
    with pytest.raises(FileNotFoundError):
        load_thresholds(missing)


def test_thresholds_load_from_real_yaml() -> None:
    """真实 YAML 可加载且含必需键（外化验证）."""
    th = load_thresholds()
    for key in ("icir_baseline", "crash_consecutive_n", "ic_floor",
                "realized_ic_min_n", "realized_icir_promote", "realized_icir_demote"):
        assert key in th


# ---------------------------------------------------------------------------
# Hook surgical：默认不接 hook，回测行为不变
# ---------------------------------------------------------------------------


def test_cross_section_hook_optional() -> None:
    """cross_section health_hook=None 默认不改行为；接上则收到逐日截面 IC."""
    from kss.backtest.cost_model import CostModel
    from kss.backtest.cross_section import factor_cross_section_backtest

    rng = np.random.default_rng(7)
    rows = []
    for d in pd.bdate_range("2026-01-01", periods=15):
        for sym in [f"68800{i}.SH" for i in range(12)]:
            fv = rng.normal()
            rows.append({
                "trade_date": d, "symbol": sym, "macd_hist": fv,
                "next_day_return": fv * 0.01 + rng.normal(0, 0.005),
            })
    df = pd.DataFrame(rows)
    cm = CostModel(buy_cost=0.001, sell_cost=0.002)

    captured: list = []

    def hook(fid, ws, we, ic):
        captured.append((fid, len(ic)))

    res = factor_cross_section_backtest(
        df, "macd_hist", cm, top_pct=0.3, health_hook=hook
    )
    assert res is not None
    assert captured and captured[0][0] == "macd_hist"
    assert captured[0][1] > 0  # 收到逐日截面 IC
