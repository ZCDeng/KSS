"""派生指标：从原始利率/通胀面板算 Δr、利差、收益率曲线斜率.

供 P1 周期阶段分类器（``kss/macro/regime.py``，下一期）和 combo_scan 的
"分母端 tag"消费。本模块只做纯计算，无 I/O，便于测试.
"""

from __future__ import annotations

from typing import Iterable

import numpy as np
import pandas as pd


def compute_rate_changes(
    panel: pd.DataFrame,
    rate_cols: Iterable[str],
    windows: Iterable[int] = (5, 20),
    date_col: str = "trade_date",
) -> pd.DataFrame:
    """对利率面板按窗口算绝对变化（百分点）.

    Args:
        panel: 日频面板，含 ``date_col`` 和 ``rate_cols``.
        rate_cols: 要算变化的利率列名（单位百分点，如 ``yld_10y`` / ``shibor_3m``）.
        windows: 回看窗口数（交易日）；为每个 ``(col, w)`` 生成 ``{col}_d{w}`` 列.
        date_col: 日期列名；用于 sort 保证窗口对齐.

    Returns:
        原 panel + 新增 Δr 列。新增列单位与原列一致（百分点差，不是相对变化率）.

    Notes:
        - 用绝对差而非相对变化率：利率低位时绝对 +50bp 比 5% 相对意义大得多，
          这是书里强调的"小绝对值变化产生大相对变化率"陷阱的反向修正。
        - 单位是百分点（pp），10Y 国债从 2.50% 升到 2.70% → ``yld_10y_d5 = 0.20``.
    """
    if panel is None or panel.empty:
        return panel
    if date_col not in panel.columns:
        raise KeyError(f"panel 缺少日期列: {date_col}")

    df = panel.sort_values(date_col).reset_index(drop=True).copy()
    for col in rate_cols:
        if col not in df.columns:
            continue
        for w in windows:
            df[f"{col}_d{w}"] = df[col].diff(w)
    return df


def compute_e_trend(
    monthly_panel: pd.DataFrame,
    pmi_col: str = "pmi_mfg",
    vai_col: str = "vai_yoy",
    smooth: int = 3,
) -> pd.Series | None:
    """E 趋势复合指标：制造业 PMI 偏离 50 + 工业增加值 yoy 的滚动均值.

    分子端 E 的近端代理。两个指标都按 ``smooth`` 月做滚动均值后线性合成：
    ``e_score = (pmi - 50) / 5 + vai_yoy / 5``。任一缺失则只用另一个。

    Args:
        monthly_panel: 含 ``month`` (YYYYMM) + ``pmi_col`` + ``vai_col`` 的月频表.
        pmi_col: 制造业 PMI 列名（含义：>50 扩张，<50 收缩）.
        vai_col: 工业增加值同比列名（百分点）.
        smooth: 滚动均值窗口（月），降噪.

    Returns:
        Series（index 为 ``month``，单位无量纲，>0 扩张倾向 / <0 收缩倾向）；
        两个列都缺时返回 ``None``.
    """
    if monthly_panel is None or monthly_panel.empty:
        return None
    df = monthly_panel.sort_values("month").reset_index(drop=True).copy()
    have_pmi = pmi_col in df.columns and df[pmi_col].notna().any()
    have_vai = vai_col in df.columns and df[vai_col].notna().any()
    if not (have_pmi or have_vai):
        return None

    parts: list[pd.Series] = []
    if have_pmi:
        pmi_centered = (df[pmi_col] - 50.0) / 5.0
        parts.append(pmi_centered.rolling(smooth, min_periods=1).mean())
    if have_vai:
        parts.append((df[vai_col] / 5.0).rolling(smooth, min_periods=1).mean())

    composite = sum(parts) / len(parts)
    composite.index = df["month"]
    composite.name = "e_trend"
    return composite


def compute_liquidity_index(
    monthly_panel: pd.DataFrame | None = None,
    hsgt_panel: pd.DataFrame | None = None,
    margin_panel: pd.DataFrame | None = None,
) -> pd.Series | None:
    """流动性复合指标：M2 同比 + 北向 20 日累计 + 两融余额 yoy.

    三个分量分别 z-score 后取均值。任一分量缺失就只用其余的，全缺返回 ``None``.

    Args:
        monthly_panel: 月频，含 ``month`` + ``m2_yoy``；可 ``None``.
        hsgt_panel: 日频，含 ``trade_date`` + ``net_amount``（北向单日净流入，亿元）;
            会先做 20 日滚动求和.
        margin_panel: 日频，含 ``trade_date`` + ``rzye``（两融余额）；会取
            ``rzye`` 同比 yoy（与 365 日前的值比，缺早期数据时降级 None）.

    Returns:
        Series（index 为 ``trade_date``，日频）；全部数据缺返回 ``None``.
        单位无量纲（每分量先 z-score 标准化）.

    Notes:
        - 输出按 ``trade_date`` 日频；月频 M2 ffill 到日.
        - 设计为"相对流动性松紧"信号；正值=偏松，负值=偏紧.
    """
    parts: list[pd.Series] = []

    if hsgt_panel is not None and not hsgt_panel.empty and "net_amount" in hsgt_panel.columns:
        h = hsgt_panel.sort_values("trade_date").reset_index(drop=True).copy()
        h["trade_date"] = h["trade_date"].astype(str)
        rolling = h["net_amount"].rolling(20, min_periods=5).sum()
        parts.append(_zscore(rolling).set_axis(h["trade_date"]))

    if margin_panel is not None and not margin_panel.empty and "rzye" in margin_panel.columns:
        m = margin_panel.sort_values("trade_date").reset_index(drop=True).copy()
        m["trade_date"] = m["trade_date"].astype(str)
        # 取沪深合计（exchange_id == 'SSE'+'SZSE' 合计行常以 'SUM' / 空表示）；
        # 实操按 trade_date 求和兜底，保留单一序列
        agg = m.groupby("trade_date", as_index=False)["rzye"].sum().sort_values("trade_date")
        yoy = agg["rzye"].pct_change(252) * 100.0  # 约 1 年
        parts.append(_zscore(yoy).set_axis(agg["trade_date"]))

    if not parts:
        return None

    base_idx = parts[0].index
    for p in parts[1:]:
        base_idx = base_idx.union(p.index)
    aligned = [p.reindex(base_idx) for p in parts]

    if monthly_panel is not None and not monthly_panel.empty and "m2_yoy" in monthly_panel.columns:
        m2 = monthly_panel.sort_values("month").reset_index(drop=True).copy()
        m2["month"] = m2["month"].astype(str)
        # 把月 ffill 到日：构造 month-prefix 索引匹配
        m2_series = pd.Series(m2["m2_yoy"].values, index=m2["month"])
        # 用 np.nan（不是 pd.NA）保证后续 .astype(float) 安全
        daily_m2_vals = [m2_series.get(d[:6], np.nan) for d in base_idx]
        daily_m2 = pd.to_numeric(
            pd.Series(daily_m2_vals, index=base_idx), errors="coerce",
        ).ffill()
        aligned.append(_zscore(daily_m2))

    composite = pd.concat(aligned, axis=1).mean(axis=1, skipna=True)
    composite.name = "liquidity_index"
    return composite


def yc_slope_change(
    wide_yc: pd.DataFrame,
    short_col: str = "yld_1y",
    long_col: str = "yld_10y",
    window: int = 20,
) -> pd.Series | None:
    """收益率曲线斜率的 ``window`` 日变化（百分点差）.

    >0 = 陡峭化（凹），<0 = 平坦化（凸）。配合 :func:`yield_curve_slope` 的
    水平值一起判断阶段切换信号.
    """
    slope = yield_curve_slope(wide_yc, short_col=short_col, long_col=long_col)
    if slope is None:
        return None
    change = slope.diff(window)
    change.name = f"yc_slope_d{window}"
    return change


def _zscore(s: pd.Series) -> pd.Series:
    """容错 z-score；标准差为 0 / 全 NaN 时返回原序列."""
    if s is None or s.empty:
        return s
    mu = s.mean(skipna=True)
    sigma = s.std(skipna=True)
    if sigma is None or sigma == 0 or sigma != sigma:  # NaN check
        return s - mu if mu == mu else s
    return (s - mu) / sigma


def yield_curve_slope(
    wide_yc: pd.DataFrame,
    short_col: str = "yld_1y",
    long_col: str = "yld_10y",
) -> pd.Series | None:
    """收益率曲线斜率 = 长端 - 短端（百分点）.

    凹曲线（正斜率）= 扩张预期；凸曲线（负斜率）= 衰退预期；
    平曲线 ≈ 0 = 顶部 / 谷底过渡。书第 3 章附录的标准定义.

    Args:
        wide_yc: :func:`kss.data.macro_client.pivot_yield_curve` 的返回值.
        short_col: 短端期限列名，默认 1Y.
        long_col: 长端期限列名，默认 10Y.

    Returns:
        Series（index 为 ``trade_date``）；缺列返回 ``None``.
    """
    if wide_yc is None or wide_yc.empty:
        return None
    if short_col not in wide_yc.columns or long_col not in wide_yc.columns:
        return None
    s = wide_yc[long_col] - wide_yc[short_col]
    s.index = wide_yc["trade_date"]
    s.name = "yc_slope"
    return s
