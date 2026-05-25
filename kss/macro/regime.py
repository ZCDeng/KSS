"""Bolton 周期阶段分类器（P1 of macro framework）.

把"分子 E / 分母 r"四阶段周期（参见 docs/plans/2026-05-25-002-feat-macro-regime-
classifier-plan.md）落成每日 :class:`MacroRegime` 标签：

- 阶段 I 谷底前：E↑ 加速 / r↓ / 流动性↑ / 曲线凹陡峭
- 阶段 II 扩张：E↑↑ / r↑ 缓 / 流动性平 / 曲线凹
- 阶段 III 顶部：E↑ 减速 / r↑↑ 加速 / 流动性↓ / 曲线趋平
- 阶段 IV 衰退：E↓↓ / r↓ / 流动性↑（救助）/ 曲线凸/水平

实现要点：

- 阈值用**历史分位数**动态算（默认 33/66 分位），不写死
- 跨阶段切换有 ``min_consecutive_days`` 滞后保护，避免单日噪声 flip
- 任一维度数据缺失走降级，confidence 按可用维度数缩放
- 配置在 ``kss/config/macro_regime.yaml``，运行时读取
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
import yaml

logger = logging.getLogger(__name__)


_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "macro_regime.yaml"

# 四阶段标签
STAGES: tuple[str, ...] = ("I", "II", "III", "IV")
UNKNOWN = "Unknown"

# 各阶段在 (E_dir, r_dir, liq_dir, yc_dir) 上的 expected pattern.
# 每维度取值：+1 (high) / 0 (mid) / -1 (low).
# yc_dir = +1 表 steepening (凹), 0 = neutral, -1 = flattening/inverted (凸)
_STAGE_PATTERN: dict[str, tuple[int, int, int, int]] = {
    "I":   (+1, -1, +1, +1),    # E rising, r falling, liquidity easing, curve steepening
    "II":  (+1, +1,  0, +1),    # E rising, r rising slow, liquidity neutral, curve still concave
    "III": ( 0, +1, -1, -1),    # E flat/decelerating, r rising fast, liquidity tightening, curve flattening
    "IV":  (-1, -1, +1, -1),    # E falling, r falling, liquidity easing (rescue), curve inverted/flat
}


@dataclass
class RegimeThresholds:
    """各维度的历史分位数阈值（low / high 切分点）.

    Attributes:
        e_trend: (low_q, high_q) 对应历史 33/66 分位.
        r_trend: 同上，针对 r 变化率（Δyld_10y_20d）.
        liquidity: 同上，针对流动性复合指数.
        yc_slope: 收益率曲线 long-short 水平.
        yc_slope_change: 曲线斜率的 20 日变化.
        n_history: 用于估算分位数的样本量；用于诊断.
    """

    e_trend: tuple[float, float] | None = None
    r_trend: tuple[float, float] | None = None
    liquidity: tuple[float, float] | None = None
    yc_slope: tuple[float, float] | None = None
    yc_slope_change: tuple[float, float] | None = None
    n_history: int = 0


@dataclass(frozen=True)
class MacroRegime:
    """单日宏观阶段标签.

    Attributes:
        trade_date: 交易日，``YYYYMMDD``.
        stage: ``'I'`` / ``'II'`` / ``'III'`` / ``'IV'`` / ``'Unknown'``.
        confidence: 0-1，匹配维度占有效维度的比例（带 weights）.
        evidence: 每个维度的方向值（+1/0/-1）和原始值，调试用.
        n_signals: 有效维度数（非 NaN）.
    """

    trade_date: str
    stage: str
    confidence: float
    evidence: dict[str, Any] = field(default_factory=dict)
    n_signals: int = 0


def load_config(path: Path | str | None = None) -> dict[str, Any]:
    """读 ``macro_regime.yaml``，缺失时返回 sane defaults."""
    p = Path(path) if path else _CONFIG_PATH
    if not p.exists():
        logger.warning("macro_regime.yaml 不存在，使用内置 defaults: %s", p)
        return {
            "quantiles": {"low": 0.33, "high": 0.66},
            "hysteresis": {"min_consecutive_days": 3, "flip_protection": True},
            "history": {"min_days_for_quantile": 252, "use_rolling": False},
            "weights": {"e_trend": 1.0, "r_trend": 1.0, "liquidity": 0.7, "yc_shape": 0.6},
            "min_signals_required": 2,
        }
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def compute_thresholds(
    history: pd.DataFrame,
    config: dict[str, Any] | None = None,
) -> RegimeThresholds:
    """按历史 panel 算各维度 low/high 分位数.

    Args:
        history: 含以下列的日频 panel（缺列会跳过对应维度）：
            ``e_trend`` / ``r_trend`` / ``liquidity`` / ``yc_slope`` / ``yc_slope_change``.
        config: 配置 dict（``load_config()`` 输出），缺省加载默认.

    Returns:
        :class:`RegimeThresholds`；样本不足 ``min_days_for_quantile`` 时返回的
        实例各字段 ``None``，调用方应回退到非分位数策略.
    """
    cfg = config or load_config()
    q_low = cfg.get("quantiles", {}).get("low", 0.33)
    q_high = cfg.get("quantiles", {}).get("high", 0.66)
    min_n = cfg.get("history", {}).get("min_days_for_quantile", 252)

    if history is None or history.empty or len(history) < min_n:
        logger.warning(
            "compute_thresholds: 历史样本 %s < min_days_for_quantile %s",
            0 if history is None else len(history),
            min_n,
        )
        return RegimeThresholds(n_history=0 if history is None else len(history))

    th = RegimeThresholds(n_history=len(history))
    for attr, col in (
        ("e_trend", "e_trend"),
        ("r_trend", "r_trend"),
        ("liquidity", "liquidity"),
        ("yc_slope", "yc_slope"),
        ("yc_slope_change", "yc_slope_change"),
    ):
        if col not in history.columns:
            continue
        s = history[col].dropna()
        if len(s) < min_n // 4:    # 至少 1/4 样本非空
            continue
        setattr(th, attr, (float(s.quantile(q_low)), float(s.quantile(q_high))))
    return th


def classify_day(
    row: pd.Series,
    thresholds: RegimeThresholds,
    config: dict[str, Any] | None = None,
) -> MacroRegime:
    """对单日指标行打标签.

    Args:
        row: pd.Series 含 ``trade_date`` + 上述五个指标（缺列按 NaN 处理）.
        thresholds: :class:`RegimeThresholds`，由 :func:`compute_thresholds` 算出.
        config: 配置 dict；若 ``None`` 自动加载.

    Returns:
        :class:`MacroRegime`；阶段无法判定时 stage='Unknown', confidence=0.
    """
    cfg = config or load_config()
    weights = cfg.get(
        "weights", {"e_trend": 1.0, "r_trend": 1.0, "liquidity": 0.7, "yc_shape": 0.6}
    )
    min_signals = cfg.get("min_signals_required", 2)

    trade_date = str(row.get("trade_date", ""))
    evidence: dict[str, Any] = {}

    e_dir = _direction(row.get("e_trend"), thresholds.e_trend)
    r_dir = _direction(row.get("r_trend"), thresholds.r_trend)
    liq_dir = _direction(row.get("liquidity"), thresholds.liquidity)

    # 曲线维度：用 (yc_slope 水平 + yc_slope_change 变化方向) 合成
    slope_lvl_dir = _direction(row.get("yc_slope"), thresholds.yc_slope)
    slope_chg_dir = _direction(row.get("yc_slope_change"), thresholds.yc_slope_change)
    yc_dir = _combine_yc(slope_lvl_dir, slope_chg_dir)

    dims = {
        "e_trend": (e_dir, row.get("e_trend"), weights.get("e_trend", 1.0)),
        "r_trend": (r_dir, row.get("r_trend"), weights.get("r_trend", 1.0)),
        "liquidity": (liq_dir, row.get("liquidity"), weights.get("liquidity", 0.7)),
        "yc_shape": (yc_dir, row.get("yc_slope"), weights.get("yc_shape", 0.6)),
    }

    n_signals = sum(1 for (d, _, _) in dims.values() if d is not None)
    if n_signals < min_signals:
        return MacroRegime(
            trade_date=trade_date,
            stage=UNKNOWN,
            confidence=0.0,
            evidence={k: {"dir": d, "value": _safe_num(v)} for k, (d, v, _) in dims.items()},
            n_signals=n_signals,
        )

    # 计算每个阶段的加权匹配度
    actual = (e_dir, r_dir, liq_dir, yc_dir)
    weight_tuple = (
        weights.get("e_trend", 1.0),
        weights.get("r_trend", 1.0),
        weights.get("liquidity", 0.7),
        weights.get("yc_shape", 0.6),
    )
    scores: dict[str, float] = {}
    for stage, pattern in _STAGE_PATTERN.items():
        total_w = 0.0
        match_w = 0.0
        for a, p, w in zip(actual, pattern, weight_tuple):
            if a is None:
                continue
            total_w += w
            if a == p:
                match_w += w
            elif a == 0 and p in (-1, +1):
                match_w += w * 0.5    # 中位与极性方向半匹配
        scores[stage] = match_w / total_w if total_w > 0 else 0.0

    best_stage = max(scores, key=scores.get)
    confidence = scores[best_stage]

    # 如果最高分 == 0，仍降级 Unknown（没有任何 stage match）
    if confidence <= 0:
        best_stage = UNKNOWN

    return MacroRegime(
        trade_date=trade_date,
        stage=best_stage,
        confidence=round(confidence, 3),
        evidence={
            k: {"dir": d, "value": _safe_num(v), "stage_scores": scores}
            if k == "e_trend"
            else {"dir": d, "value": _safe_num(v)}
            for k, (d, v, _) in dims.items()
        },
        n_signals=n_signals,
    )


def classify_today(
    panel: pd.DataFrame,
    today: str | None = None,
    config: dict[str, Any] | None = None,
) -> MacroRegime:
    """对 panel 最后一日（或指定日）分类，自动从历史算阈值.

    Args:
        panel: 完整历史 + 当日的日频 panel；至少含 ``trade_date`` 列.
        today: 目标日，``YYYYMMDD``；``None`` 取 panel 中最大的 trade_date.
        config: 配置 dict.

    Returns:
        :class:`MacroRegime`. 当 ``today`` 不在 panel 中或 panel 空 → Unknown.
    """
    if panel is None or panel.empty:
        return MacroRegime(trade_date=str(today or ""), stage=UNKNOWN, confidence=0.0)

    df = panel.copy()
    df["trade_date"] = df["trade_date"].astype(str)
    if today is None:
        today = str(df["trade_date"].max())

    history = df[df["trade_date"] < today]
    today_rows = df[df["trade_date"] == today]
    if today_rows.empty:
        return MacroRegime(trade_date=today, stage=UNKNOWN, confidence=0.0)

    cfg = config or load_config()
    thresholds = compute_thresholds(history, cfg)
    return classify_day(today_rows.iloc[-1], thresholds, cfg)


def classify_history(
    panel: pd.DataFrame,
    config: dict[str, Any] | None = None,
) -> pd.DataFrame:
    """对 panel 全量历史每日分类（用于 backfill 与回测）.

    使用 expanding window：第 N 日的阈值用 ``[0, N)`` 的数据算，确保无未来信息.
    在 ``min_days_for_quantile`` 之前的天数 stage='Unknown'.

    Args:
        panel: 含 ``trade_date`` + 各指标的日频 panel.
        config: 配置.

    Returns:
        含 ``trade_date`` / ``stage`` / ``confidence`` / ``n_signals`` /
        ``stage_raw``（未做滞后处理）/ ``stage_smoothed``（应用 hysteresis 后）
        / 各 evidence_* 字段的 DataFrame.
    """
    if panel is None or panel.empty:
        return pd.DataFrame()

    cfg = config or load_config()
    df = panel.copy().sort_values("trade_date").reset_index(drop=True)
    df["trade_date"] = df["trade_date"].astype(str)
    min_n = cfg.get("history", {}).get("min_days_for_quantile", 252)
    use_rolling = cfg.get("history", {}).get("use_rolling", False)

    out_rows: list[dict[str, Any]] = []
    for i in range(len(df)):
        today = df.iloc[i]
        if i < min_n:
            out_rows.append({
                "trade_date": today["trade_date"],
                "stage_raw": UNKNOWN,
                "confidence": 0.0,
                "n_signals": 0,
                "evidence_e": None,
                "evidence_r": None,
                "evidence_liq": None,
                "evidence_yc": None,
            })
            continue
        if use_rolling:
            hist = df.iloc[max(0, i - min_n):i]
        else:
            hist = df.iloc[:i]
        thresholds = compute_thresholds(hist, cfg)
        regime = classify_day(today, thresholds, cfg)
        out_rows.append({
            "trade_date": today["trade_date"],
            "stage_raw": regime.stage,
            "confidence": regime.confidence,
            "n_signals": regime.n_signals,
            "evidence_e": regime.evidence.get("e_trend"),
            "evidence_r": regime.evidence.get("r_trend"),
            "evidence_liq": regime.evidence.get("liquidity"),
            "evidence_yc": regime.evidence.get("yc_shape"),
        })

    result = pd.DataFrame(out_rows)
    result["stage"] = _apply_hysteresis(
        result["stage_raw"],
        min_days=cfg.get("hysteresis", {}).get("min_consecutive_days", 3),
    )
    return result


# ---------------------------------------------------------------------------- #
# Helpers
# ---------------------------------------------------------------------------- #


def _direction(value: Any, thresholds: tuple[float, float] | None) -> int | None:
    """把单值落入 (low, mid, high) 桶 → -1/0/+1；缺数据 → None."""
    if value is None or thresholds is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v != v:    # NaN
        return None
    low, high = thresholds
    if v <= low:
        return -1
    if v >= high:
        return +1
    return 0


def _combine_yc(level_dir: int | None, change_dir: int | None) -> int | None:
    """收益率曲线方向合成.

    Bolton: 凹陡峭（正斜率高 + 上行变陡）= 扩张; 凸/平坦 = 衰退/顶部.
    简化为：两维度都非空时取平均后量化；只有一维度时直接用.
    """
    if level_dir is None and change_dir is None:
        return None
    if level_dir is None:
        return change_dir
    if change_dir is None:
        return level_dir
    avg = (level_dir + change_dir) / 2.0
    if avg >= 0.5:
        return +1
    if avg <= -0.5:
        return -1
    return 0


def _apply_hysteresis(
    raw: pd.Series, min_days: int = 3
) -> pd.Series:
    """切换 stage 需连续 ``min_days`` 同标签，否则保持前值.

    Unknown 不触发保护（直接透传），其他 stage 之间需累计达到阈值才切换.
    """
    out: list[str] = []
    current = UNKNOWN
    pending: str | None = None
    pending_count = 0
    for val in raw:
        if val == UNKNOWN:
            out.append(current)
            pending = None
            pending_count = 0
            continue
        if val == current:
            out.append(current)
            pending = None
            pending_count = 0
            continue
        # 不同 stage：候选
        if val == pending:
            pending_count += 1
        else:
            pending = val
            pending_count = 1
        if pending_count >= min_days:
            current = pending
            pending = None
            pending_count = 0
        out.append(current)
    return pd.Series(out, index=raw.index, name="stage")


def _safe_num(v: Any) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:
        return None
    return round(f, 4)
