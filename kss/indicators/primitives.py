"""参数化技术指标基元库：均线交叉 / RSI·动量阈值 / 布林·ATR 波动.

三族基元由声明式 ``{family, params}`` 组合产生候选，特征计算复用
``kss.features.technical.TechnicalFactors``；不做代码生成，候选空间有界。
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from kss.features.technical import TechnicalFactors

FAMILY_MA_CROSS = "ma_cross"
FAMILY_RSI_THRESHOLD = "rsi_threshold"
FAMILY_BOLL_ATR = "boll_atr"
FAMILY_SR_LEVEL = "sr_level"

FAMILIES: tuple[str, ...] = (FAMILY_MA_CROSS, FAMILY_RSI_THRESHOLD, FAMILY_BOLL_ATR, FAMILY_SR_LEVEL)

DEFAULT_PARAMS: dict[str, dict[str, Any]] = {
    FAMILY_MA_CROSS: {"fast": 5, "slow": 20, "kind": "sma"},
    FAMILY_RSI_THRESHOLD: {"period": 14, "entry_level": 30.0, "exit_level": 70.0},
    FAMILY_BOLL_ATR: {"period": 20, "atr_period": 14, "atr_mult": 2.0, "atr_window": 10},
    FAMILY_SR_LEVEL: {"pivot_window": 5, "cluster_atr_mult": 1.0, "rule_variant": "bounce", "multi_timeframe": False},
}

# 参数网格：供 U2 walk-forward 重估消费，与 mi_walk_forward.DEFAULT_N_GRID 同量级。
PARAM_GRID: dict[str, list[dict[str, Any]]] = {
    FAMILY_MA_CROSS: [
        {"fast": f, "slow": s, "kind": k}
        for k in ("sma", "ewm")
        for f, s in ((5, 20), (10, 30), (10, 60), (20, 60))
    ],
    FAMILY_RSI_THRESHOLD: [
        {"period": p, "entry_level": el, "exit_level": xl}
        for p in (9, 14, 21)
        for el, xl in ((20.0, 80.0), (30.0, 70.0))
    ],
    FAMILY_BOLL_ATR: [
        {"period": p, "atr_period": 14, "atr_mult": m, "atr_window": 10}
        for p in (14, 20, 30)
        for m in (1.5, 2.0, 2.5)
    ],
    # 2(pivot_window) × 2(cluster_atr_mult) × 2(rule_variant) × 2(multi_timeframe) = 16 组合
    # （plan 2026-07-20-001 KTD3：与现有三族网格同量级，约束夜间批跑与门禁全网格重算成本）。
    FAMILY_SR_LEVEL: [
        {"pivot_window": pw, "cluster_atr_mult": cm, "rule_variant": rv, "multi_timeframe": mtf}
        for pw in (3, 5)
        for cm in (0.5, 1.0)
        for rv in ("bounce", "breakout")
        for mtf in (False, True)
    ],
}


def _check_family(family: str) -> None:
    if family not in FAMILIES:
        raise ValueError(f"未知基元族: {family!r}；允许 {FAMILIES}")


def default_params(family: str) -> dict[str, Any]:
    """族默认参数（可回测的最小可行组合）."""
    _check_family(family)
    return dict(DEFAULT_PARAMS[family])


def param_grid(family: str) -> list[dict[str, Any]]:
    """族参数网格（walk-forward 重估候选池）."""
    _check_family(family)
    return [dict(p) for p in PARAM_GRID[family]]


def build_features(
    df: pd.DataFrame, family: str, params: dict[str, Any]
) -> pd.DataFrame:
    """按基元族计算特征列，附加到 ``df`` 副本；不含 entry/exit 布尔信号（见 ``rules.py``）.

    所有特征列只使用截至当前 bar 的历史数据（rolling/ewm/shift 均非负移位）；
    ``ret`` 列例外——它是 T+1 开盘买入、T+2 开盘卖出的前瞻收益，仅供 U2 打分消费，
    不进入任何 entry/exit 判定。
    """
    _check_family(family)
    out = df.copy()
    close = out["close"]

    if family == FAMILY_MA_CROSS:
        fast, slow = int(params["fast"]), int(params["slow"])
        kind = params.get("kind", "sma")
        if fast >= slow:
            raise ValueError(f"ma_cross 要求 fast < slow，收到 fast={fast} slow={slow}")
        if kind == "sma":
            out["ma_fast"] = close.rolling(fast).mean()
            out["ma_slow"] = close.rolling(slow).mean()
        elif kind == "ewm":
            out["ma_fast"] = close.ewm(span=fast, adjust=False).mean()
            out["ma_slow"] = close.ewm(span=slow, adjust=False).mean()
        else:
            raise ValueError(f"未知 ma_cross kind: {kind!r}；允许 sma/ewm")

    elif family == FAMILY_RSI_THRESHOLD:
        period = int(params["period"])
        rsi = TechnicalFactors.rsi(close, period=period)
        out["rsi"] = rsi[f"rsi_{period}"]

    elif family == FAMILY_BOLL_ATR:
        boll_period = int(params["period"])
        boll = TechnicalFactors.bollinger(close, period=boll_period)
        # TechnicalFactors.bollinger 按 close 归一化返回比值；还原绝对价方便判穿越。
        out["boll_upper"] = boll["boll_upper"] * close
        out["boll_lower"] = boll["boll_lower"] * close
        out["boll_mid"] = close.rolling(boll_period).mean()
        out["atr"] = TechnicalFactors.atr(
            out["high"], out["low"], out["close"], period=int(params["atr_period"])
        )
        window = int(params["atr_window"])
        out["rolling_high"] = close.rolling(window, min_periods=1).max()

    elif family == FAMILY_SR_LEVEL:
        from kss.indicators.sr_levels import causal_features

        level_feat = causal_features(out, params)
        out["sr_support"] = level_feat["nearest_support"]
        out["sr_resistance"] = level_feat["nearest_resistance"]
        out["sr_support_strength"] = level_feat["support_strength"]
        out["sr_resistance_strength"] = level_feat["resistance_strength"]

    out["ret"] = out["open"].shift(-2) / out["open"].shift(-1) - 1.0
    return out
