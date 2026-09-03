"""vwap 基元族单测：会话累加、左侧规则、T+1 离场、无前瞻。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from kss.data.minute_resample import normalize_stk_mins, resample_session_halves
from kss.indicators.primitives import FAMILY_VWAP, build_features, param_grid
from kss.indicators.rules import IndicatorSpec, compute_positions, replay, rule_sentence, signal_strength


def _session_df(n_days: int = 30, *, dip: bool = True, close_weak: bool = False) -> pd.DataFrame:
    """构造每天 4 根 60m：可选在第 2 根深跌、第 3 根收阳仍低于 VWAP。"""
    rows: list[dict] = []
    dates = pd.bdate_range("2024-01-02", periods=n_days)
    times = ["10:30", "11:30", "14:00", "15:00"]
    for d in dates:
        day = str(d.date())
        # 平坦日：价格贴着 100。dip 日：11:30 砸到 96，14:00 收到 97.2。
        if close_weak:
            closes = [100.0, 96.0, 95.5, 95.0]
            lows = [99.5, 95.5, 95.0, 94.5]
            highs = [100.5, 100.0, 96.0, 95.8]
            opens = [100.0, 99.8, 96.0, 95.4]
        elif dip:
            closes = [100.0, 96.0, 97.2, 98.0]
            lows = [99.5, 95.5, 95.8, 97.0]
            highs = [100.5, 100.0, 97.5, 98.5]
            opens = [100.0, 99.8, 96.1, 97.3]
        else:
            closes = [100.0, 100.2, 100.1, 100.3]
            lows = [99.7, 99.8, 99.9, 100.0]
            highs = [100.3, 100.4, 100.3, 100.5]
            opens = [100.0, 100.0, 100.2, 100.1]
        vols = [100.0, 220.0, 120.0, 110.0]
        for t, o, h, l, c, v in zip(times, opens, highs, lows, closes, vols):
            rows.append(
                {
                    "trade_date": pd.Timestamp(day),
                    "bar_end_ts": pd.Timestamp(f"{day} {t}:00"),
                    "open": o,
                    "high": h,
                    "low": l,
                    "close": c,
                    "volume": v,
                    "amount": c * v,
                }
            )
    return pd.DataFrame(rows)


def _default_spec(**overrides) -> IndicatorSpec:
    params = {
        "rule_variant": "dev_reclaim",
        "entry_dev_bps": 80,
        "stop_dev_bps": 250,
        "max_hold_bars": 8,
        "t1_exit": True,
    }
    params.update(overrides)
    return IndicatorSpec(FAMILY_VWAP, params)


def test_param_grid_has_12_combinations() -> None:
    grid = param_grid(FAMILY_VWAP)
    assert len(grid) == 12
    seen = {tuple(sorted(combo.items())) for combo in grid}
    assert len(seen) == 12


def test_session_vwap_resets_each_day() -> None:
    df = _session_df(3)
    feat = build_features(df, FAMILY_VWAP, _default_spec().params)
    first_of_day = feat.groupby(feat["trade_date"].dt.strftime("%Y-%m-%d")).head(1)
    # 每日首根 VWAP 等于该根成交均价（amount/volume ≈ close）。
    assert np.allclose(first_of_day["vwap"], first_of_day["close"], rtol=1e-6)


def test_dev_reclaim_trades_on_dump_bounce() -> None:
    df = _session_df(20, dip=True)
    spec = _default_spec(entry_dev_bps=50, t1_exit=False, max_hold_bars=8)
    rep = replay(df, spec)
    assert len(rep["trades"]) >= 1
    feat = compute_positions(df, spec)
    assert feat["position"].sum() > 0


def test_close_dip_only_fires_at_1500() -> None:
    df = _session_df(20, close_weak=True)
    spec = _default_spec(rule_variant="close_dip", entry_dev_bps=50, t1_exit=False)
    feat = compute_positions(df, spec)
    # 入场只应发生在 15:00 bar（is_session_close）。
    entries = feat.loc[feat["position"].diff().fillna(0) > 0]
    if not entries.empty:
        assert (pd.to_datetime(entries["bar_end_ts"]).dt.strftime("%H:%M") == "15:00").all()


def test_t1_exit_does_not_flatten_same_day() -> None:
    df = _session_df(16, dip=True)
    spec = _default_spec(entry_dev_bps=50, t1_exit=True, max_hold_bars=8)
    feat = compute_positions(df, spec)
    pos = feat["position"].to_numpy()
    dates = feat["trade_date"].dt.strftime("%Y-%m-%d").to_numpy()
    for i in range(1, len(pos)):
        if pos[i - 1] <= 0 and pos[i] > 0:
            entry_d = dates[i]
            # 同一交易日里仓位不得回到 0。
            same = (dates == entry_d)
            held = pos[same]
            # 入场之后的当日 bar 必须仍持仓
            idx = np.where(same)[0]
            after = idx[idx >= i]
            assert (pos[after] > 0).all()


def test_signal_strength_bounded() -> None:
    df = _session_df(12)
    feat = build_features(df, FAMILY_VWAP, _default_spec().params)
    strength = signal_strength(feat, FAMILY_VWAP)
    assert strength.between(-1.0, 1.0).all()


@pytest.mark.parametrize("variant", ["dev_reclaim", "close_dip"])
def test_rule_sentence_names_variant(variant: str) -> None:
    spec = _default_spec(rule_variant=variant)
    assert variant in rule_sentence(spec)


def test_unknown_rule_variant_rejected() -> None:
    df = _session_df(8)
    spec = _default_spec(rule_variant="nope")
    with pytest.raises(ValueError, match="未知 vwap 规则变体"):
        replay(df, spec)


def test_no_lookahead_on_session_bars() -> None:
    df = _session_df(25)
    spec = _default_spec()
    feat_full = compute_positions(df, spec)
    truncated = df.iloc[:60].copy()
    feat_trunc = compute_positions(truncated, spec)
    pd.testing.assert_series_equal(
        feat_full["position"].iloc[:60].reset_index(drop=True),
        feat_trunc["position"].reset_index(drop=True),
    )


def test_normalize_drops_auction_snapshot() -> None:
    raw = pd.DataFrame(
        {
            "ts_code": ["688017.SH"] * 5,
            "trade_time": [
                "2026-09-02 09:30:00",
                "2026-09-02 10:30:00",
                "2026-09-02 11:30:00",
                "2026-09-02 14:00:00",
                "2026-09-02 15:00:00",
            ],
            "open": [1, 2, 3, 4, 5],
            "high": [1, 2, 3, 4, 5],
            "low": [1, 2, 3, 4, 5],
            "close": [1, 2, 3, 4, 5],
            "vol": [10, 20, 30, 40, 50],
            "amount": [10, 40, 90, 160, 250],
        }
    )
    out = normalize_stk_mins(raw)
    assert len(out) == 4
    assert "09:30" not in out["bar_end_ts"].dt.strftime("%H:%M").tolist()
    assert "volume" in out.columns


def test_resample_session_halves_two_bars_per_day() -> None:
    df = _session_df(2, dip=False)
    out = resample_session_halves(df)
    assert len(out) == 4  # 2 days × AM/PM
    # 上午收盘应对齐 11:30
    am = out.loc[pd.to_datetime(out["bar_end_ts"]).dt.strftime("%H:%M") == "11:30"]
    assert len(am) == 2
    pm = out.loc[pd.to_datetime(out["bar_end_ts"]).dt.strftime("%H:%M") == "15:00"]
    assert len(pm) == 2


def test_resample_without_amount_keeps_sane_vwap() -> None:
    """无成交额时仍按真实 volume 加权，不得退化为等权或伪造 amount。"""
    df = _session_df(6, dip=True).drop(columns=["amount"])
    out = resample_session_halves(df)
    assert "amount" not in out.columns
    feat = build_features(out, FAMILY_VWAP, _default_spec().params)
    first_day = feat.iloc[:2]
    typical = (first_day["high"] + first_day["low"] + first_day["close"]) / 3.0
    expected_close_vwap = float((typical * first_day["volume"]).sum() / first_day["volume"].sum())
    assert float(first_day["vwap"].iloc[-1]) == pytest.approx(expected_close_vwap)
    assert feat["vwap_dev"].abs().max() < 0.2
