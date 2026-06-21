"""Feature 泄漏防护 —— 粗检（复制 label）+ 时序检查（label 平移敏感度）.

`walk_forward` 的 ``purge_gap`` 只防 **label leak**（``future_return_Nd =
close.shift(-N)`` 穿透训练区间尾部）。它**防不住** feature-level look-ahead：
调用方把含未来信息的列（如 ``next_day_return`` 衍生、或用当日收盘价构造的
当期信号）塞进 ``feature_cols`` 时，模型在 test 上仍能作弊。

本模块提供两道互补检查（U1，见
``docs/brainstorms/2026-06-21-feature-lookahead-guard-requirements.md``）：

1. **粗检** —— feature 与 ``label_col`` 全 panel 绝对 Spearman IC > 阈值（默认
   0.95）时判为近似复制 label，抛 :class:`LookaheadFeatureError`。只能抓 IC≈1
   的粗泄漏。

2. **时序检查（关键）** —— label 平移敏感度。``future_return_Nd`` 在 row t 持
   有 t→t+N 的未来收益；一个**合法预测特征**应与此「未来」label 相关，而**不**
   与「向当期对齐一格」的 label（symbol 内 label 前移一格、更贴近特征当期）相关
   更强。若特征对**更贴近当期**的 label 的 IC 量级显著超过它对预测 label 的 IC，
   且该当期 IC 本身非平凡，说明特征在吃同期/已实现信息（即便绝对预测 IC 中等，如
   close-at-open IC≈-0.01 也能虚高 Sharpe）—— 判时序泄漏。

「可信回测」= 本模块（粗检 + 时序）+ purge/embargo + CPCV OOS 三者合力，**单靠
本模块通过不构成完全信任**。

白名单为逃生口：命中只 ``logger.warning`` 不抛错，由调用方维护并自负其责。
"""

from __future__ import annotations

import logging
import warnings
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

logger = logging.getLogger(__name__)

# 默认阈值（可经 settings.yaml ``backtest.lookahead_guard`` 覆盖；见 _load_defaults）.
_DEFAULT_IC_THRESHOLD: float = 0.95
# 时序检查：当期对齐 IC 量级须 ≥ 此值才算「非平凡」，否则不判泄漏（避免噪声误杀）.
_DEFAULT_TEMPORAL_MIN_IC: float = 0.10
# 时序检查：当期对齐 IC 量级须超过预测 IC 量级 ``× (1 + margin)`` 才算「显著更贴近当期」.
_DEFAULT_TEMPORAL_MARGIN: float = 0.50
# 时序检查：配对有效样本数下限. 小样本 Spearman 是纯噪声（n=10 的随机特征也能
# 偶然让当期 IC 超预测 IC），低于此值跳过时序检查避免误杀（粗检不受影响）.
_DEFAULT_TEMPORAL_MIN_SAMPLES: int = 100


class LookaheadFeatureError(ValueError):
    """检测到 feature-level look-ahead（复制 label 或时序不可得）时抛出.

    继承 :class:`ValueError`，与现有 ``walk_forward`` 参数校验风格一致；
    调用方可 catch 本具体类型。
    """


def _load_defaults() -> dict[str, Any]:
    """从 ``kss/config/settings.yaml`` 的 ``backtest.lookahead_guard`` 读默认值.

    与 :mod:`kss.models.lightgbm_model` 同款懒加载；缺配置/读失败时回退硬编码默认.
    """
    try:
        import yaml  # 局部导入，避免无配置场景强依赖
        from pathlib import Path

        cfg_path = Path(__file__).resolve().parents[2] / "config" / "settings.yaml"
        if cfg_path.exists():
            with cfg_path.open("r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            guard_cfg = cfg.get("backtest", {}).get("lookahead_guard", {})
            return dict(guard_cfg)
    except Exception as exc:  # pragma: no cover - 配置缺失走默认
        logger.debug("加载 lookahead_guard settings 失败: %s", exc)
    return {}


_SETTINGS = _load_defaults()


def _abs_spearman_ic_n(x: pd.Series, y: pd.Series) -> tuple[float, int]:
    """``(x, y)`` 去 NaN 后的 ``(|Spearman IC|, 配对样本数)``；不足/退化 IC 返回 ``nan``.

    与 :meth:`SignalDiagnostics.ic` 同口径（dropna + std==0 守卫），保证度量一致.
    """
    s = pd.concat([x, y], axis=1).dropna()
    n = len(s)
    if n < 3:
        return float("nan"), n
    a = s.iloc[:, 0].values
    b = s.iloc[:, 1].values
    if np.std(a) == 0 or np.std(b) == 0:
        return float("nan"), n
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        rho, _ = stats.spearmanr(a, b)
    return (float(abs(rho)) if np.isfinite(rho) else float("nan")), n


def _abs_spearman_ic(x: pd.Series, y: pd.Series) -> float:
    """``(x, y)`` 去 NaN 后的 |Spearman IC|；样本不足/退化返回 ``nan``."""
    return _abs_spearman_ic_n(x, y)[0]


class FeatureLookaheadGuard:
    """两道互补的 feature 泄漏检查（无状态 ``@staticmethod``）."""

    @staticmethod
    def check(
        factor_df: pd.DataFrame,
        feature_cols: list[str],
        label_col: str,
        *,
        ic_threshold: float | None = None,
        whitelist: list[str] | None = None,
        temporal_check: bool = True,
        temporal_min_ic: float | None = None,
        temporal_margin: float | None = None,
        temporal_min_samples: int | None = None,
        symbol_col: str = "symbol",
        date_col: str = "trade_date",
    ) -> dict[str, list[str]]:
        """对 ``feature_cols`` 跑粗检 + 时序检查；命中泄漏抛 :class:`LookaheadFeatureError`.

        Args:
            factor_df: 含特征、``label_col``、``symbol_col``、``date_col`` 的全 panel.
            feature_cols: 待检特征列名.
            label_col: 预测 label 列（通常 ``future_return_Nd``）.
            ic_threshold: 粗检拒绝线（严格 ``>``，等于不拒）；``None`` 用配置/默认 0.95.
                传 ``float("inf")`` / ``None``-via-disable 见下：``ic_threshold=float("inf")``
                时粗检恒不触发（研究/调试用途）.
            whitelist: 命中泄漏时只 ``logger.warning`` 不抛错的特征名单（人工覆盖）.
            temporal_check: 是否跑时序检查（label 平移敏感度），默认 True.
            temporal_min_ic: 当期对齐 IC 量级「非平凡」下限；``None`` 用配置/默认 0.10.
            temporal_margin: 当期 IC 须超预测 IC 的相对裕度；``None`` 用配置/默认 0.50.
            temporal_min_samples: 时序检查配对样本数下限，低于此跳过（小样本 Spearman
                是噪声，避免误杀）；``None`` 用配置/默认 100.
            symbol_col / date_col: 时序检查按 symbol 排序、date 内平移所需列名.

        Returns:
            ``{"temporal_check_skipped": [被跳过时序检查的特征列名]}``。时序检查因
            样本不足（``n_now < temporal_min_samples``）跳过时，该列入列表 + ``logger.warning``
            —— 别让薄面板被读成"干净通过"（粗检过 + 时序未真正跑）。

        Raises:
            LookaheadFeatureError: 非白名单特征命中粗检或时序检查时.
        """
        if ic_threshold is None:
            ic_threshold = float(_SETTINGS.get("ic_threshold", _DEFAULT_IC_THRESHOLD))
        if temporal_min_ic is None:
            temporal_min_ic = float(
                _SETTINGS.get("temporal_min_ic", _DEFAULT_TEMPORAL_MIN_IC)
            )
        if temporal_margin is None:
            temporal_margin = float(
                _SETTINGS.get("temporal_margin", _DEFAULT_TEMPORAL_MARGIN)
            )
        if temporal_min_samples is None:
            temporal_min_samples = int(
                _SETTINGS.get("temporal_min_samples", _DEFAULT_TEMPORAL_MIN_SAMPLES)
            )
        wl = set(whitelist or [])
        temporal_skipped: list[str] = []

        if label_col not in factor_df.columns:
            # label 列缺失：walk_forward 后续会自行报错；Guard 不越权，静默放行.
            return {"temporal_check_skipped": temporal_skipped}

        label = factor_df[label_col]

        # 时序检查预备：symbol 内把 label 前移一格 → 更贴近特征当期的「准当期」label.
        # future_return_Nd[t] 是 t→t+N 未来收益；shift(+1) 后 row t 拿到 t-1 行的
        # label（在 symbol 内按 date 排序），即「更靠近已实现/当期」的对照标的.
        shifted_label: pd.Series | None = None
        if temporal_check and symbol_col in factor_df.columns:
            ordered = factor_df.sort_values([symbol_col, date_col])
            shifted = ordered.groupby(symbol_col, sort=False)[label_col].shift(1)
            # 还原回 factor_df 原始行顺序，保证与 feature 列按 index 对齐.
            shifted_label = shifted.reindex(factor_df.index)

        for col in feature_cols:
            if col not in factor_df.columns:
                continue
            feat = factor_df[col]

            # ---- 1) 粗检：|IC| > 阈值 → 近似复制 label ---- #
            ic = _abs_spearman_ic(feat, label)
            if np.isfinite(ic) and ic > ic_threshold:
                FeatureLookaheadGuard._flag(
                    col,
                    wl,
                    f"特征 {col} 与 {label_col} 的 |IC|={ic:.4f} > 阈值 {ic_threshold} "
                    f"（疑似复制 label / 直接泄漏）",
                )
                continue  # 已粗检命中，无需再跑时序检查

            # ---- 2) 时序检查：label 平移敏感度 ---- #
            if shifted_label is None:
                continue
            ic_now, n_now = _abs_spearman_ic_n(feat, shifted_label)  # 当期对照 IC
            if not (np.isfinite(ic_now) and np.isfinite(ic)):
                continue
            if n_now < temporal_min_samples:
                # 小样本 Spearman 不可信，跳过时序检查（粗检已先行）。但不能静默——
                # 否则薄面板被读成"干净通过"。warning + 标记，让调用方/审计可见。
                temporal_skipped.append(col)
                logger.warning(
                    "[LookaheadGuard] 特征 %s 时序检查跳过：配对样本 n=%d < "
                    "temporal_min_samples=%d（薄面板，时序泄漏未被真正检验，"
                    "不等于干净通过）",
                    col, n_now, temporal_min_samples,
                )
                continue
            # 当期 IC 非平凡 且 显著超过预测 IC → 特征在吃同期/已实现信息.
            if (
                ic_now >= temporal_min_ic
                and ic_now > ic * (1.0 + temporal_margin)
            ):
                FeatureLookaheadGuard._flag(
                    col,
                    wl,
                    f"特征 {col} 时序泄漏：对当期对齐 label 的 |IC|={ic_now:.4f} "
                    f"显著超过对预测 label {label_col} 的 |IC|={ic:.4f} "
                    f"（吃同期/已实现信息，预测 IC 中等也会虚高 Sharpe）",
                )

        return {"temporal_check_skipped": temporal_skipped}

    @staticmethod
    def _flag(col: str, whitelist: set[str], message: str) -> None:
        """白名单内只 warning，否则抛 :class:`LookaheadFeatureError`."""
        if col in whitelist:
            logger.warning(
                "[LookaheadGuard] 豁免特征 %s（白名单）：%s", col, message
            )
            return
        raise LookaheadFeatureError(message)
