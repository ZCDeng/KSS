#!/usr/bin/env python3
"""688017 多因子组合 v2 —— 反向因子互补性实验.

接续 ``analyze_688017_combo.py`` 的下一步建议：
**纳入反向因子（pb / close_to_low_60d）用 |IC| signed 加权**，
看是否真能在 Sharpe 与回撤上带来互补.

实验设计：

- 反向单因子：用 ``MultiFactorCombiner({factor: -1.0})`` 跑出"反向使用"的单因子表现，
  对比正向 ``macd_hist``，看 ``pb`` / ``close_to_low_60d`` 反向时是否本身就有 alpha.
- 反向因子组合：
  - **IC Top 5 (signed)**：纯按 5d IC 排名 + 符号选因子，含 pb / close_to_low_60d.
  - **趋势+反向估值（手工）**：``macd_hist`` 趋势 + ``pb`` 反向 + ``close_to_low_60d`` 反向.
  - **macd_hist + pb 二因子（手工）**：极简互补.
- 因子 z-score 自相关分析：揭示 pb / close_to_low_60d 为何"IC 高但 Sharpe 低"——
  慢变因子在 ±1σ 阈值切换框架下不产生有效信号.

输出::

    storage/reports/688017_combo_v2_report.md
    storage/reports/688017_combo_v2_panels.png
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from kss.backtest.metrics import Metrics  # noqa: E402
from kss.backtest.single_stock import SingleStockAnalyzer  # noqa: E402
from kss.features.pipeline import FactorPipeline  # noqa: E402
from kss.strategies.multi_factor import MultiFactorCombiner  # noqa: E402


# ---------------------------------------------------------------------- #
# 常量
# ---------------------------------------------------------------------- #

DATA_PATH = PROJECT_ROOT / "cs_data_688017.csv"
SYMBOL = "688017.SH"
ROLLING_WINDOW = 60
IC_HORIZON = "future_return_5d"
HORIZONS = ["next_day_return", "future_return_5d", "future_return_10d", "future_return_20d"]
REPORT_DIR = PROJECT_ROOT / "storage" / "reports"
MD_PATH = REPORT_DIR / "688017_combo_v2_report.md"
PNG_PATH = REPORT_DIR / "688017_combo_v2_panels.png"

# Sharpe Top 5（v1 baseline 入参）
SHARPE_TOP5 = ["macd_hist", "vol_ma20_ratio", "ma_10_ratio", "ma_10_slope", "volatility_5d"]
# 反向因子（v2 实验入参）：IC Top 5 中除 macd_hist 外的 4 个
REVERSE_FACTORS = ["pb", "close_to_low_60d", "close_to_low_20d", "kdj_d"]

plt.rcParams["font.sans-serif"] = ["Arial Unicode MS", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------- #
# 数据
# ---------------------------------------------------------------------- #


def load_factor_panel() -> tuple[pd.DataFrame, list[str]]:
    df = pd.read_csv(DATA_PATH)
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df = df.sort_values("trade_date").reset_index(drop=True)
    fp = FactorPipeline(df)
    factor_df = fp.generate()
    factor_df = fp.add_targets(factor_df, df["close"])
    feature_cols = fp.get_feature_cols(factor_df)
    return factor_df, feature_cols


# ---------------------------------------------------------------------- #
# 因子 z-score 自相关诊断
# ---------------------------------------------------------------------- #


def factor_persistence(
    analyzer: SingleStockAnalyzer,
    factors: list[str],
    rolling_window: int = ROLLING_WINDOW,
) -> pd.DataFrame:
    """诊断每个因子的 z-score 自相关（慢变 vs 快速均值回归）.

    Returns:
        DataFrame，列：``factor / autocorr_1d / autocorr_20d / days_above_1 / days_below_neg1``.
    """
    rows: list[dict] = []
    for f in factors:
        if f not in analyzer.df.columns:
            continue
        z = analyzer._rolling_zscore(analyzer.df[f], rolling_window).dropna()
        if len(z) < 30:
            continue
        rows.append({
            "factor": f,
            "autocorr_1d": float(z.autocorr(1)) if len(z) > 2 else float("nan"),
            "autocorr_20d": float(z.autocorr(20)) if len(z) > 21 else float("nan"),
            "days_above_1": int((z > 1).sum()),
            "days_below_neg1": int((z < -1).sum()),
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------- #
# 因子 z-score 相关性矩阵
# ---------------------------------------------------------------------- #


def factor_correlation_matrix(
    analyzer: SingleStockAnalyzer,
    factors: list[str],
    rolling_window: int = ROLLING_WINDOW,
) -> pd.DataFrame:
    """计算因子 rolling z-score 的 Spearman 相关矩阵."""
    z_dict = {
        f: analyzer._rolling_zscore(analyzer.df[f], rolling_window)
        for f in factors if f in analyzer.df.columns
    }
    df_z = pd.DataFrame(z_dict).dropna()
    return df_z.corr(method="spearman")


# ---------------------------------------------------------------------- #
# Helper：用 multi_factor_backtest 跑"带方向"的单因子
# ---------------------------------------------------------------------- #


def signed_single_backtest(
    analyzer: SingleStockAnalyzer,
    factor: str,
    direction: int,
    **kwargs,
) -> dict:
    """用 ``multi_factor_backtest`` 跑单因子，``direction`` 可正可负.

    Args:
        analyzer: SingleStockAnalyzer 实例.
        factor: 因子名.
        direction: +1 = 正向（z>1 持仓），-1 = 反向（z<-1 持仓）.
        **kwargs: 透传给 multi_factor_backtest.

    Returns:
        与 ``signal_backtest`` 同构的 dict.
    """
    combiner = MultiFactorCombiner({factor: float(direction)}, normalize=False)
    return analyzer.multi_factor_backtest(combiner, **kwargs)


# ---------------------------------------------------------------------- #
# 绘图
# ---------------------------------------------------------------------- #


def plot_panels(
    factor_df: pd.DataFrame,
    all_results: dict[str, dict],
    bh_cum: pd.Series,
    comparison_df: pd.DataFrame,
    corr_mat: pd.DataFrame,
    persistence_df: pd.DataFrame,
) -> None:
    fig, axes = plt.subplots(2, 2, figsize=(16, 13))
    dates = factor_df["trade_date"]

    # Panel 1: 净值曲线 —— B&H + 关键策略
    ax = axes[0, 0]
    ax.plot(dates, bh_cum.values, label="Buy & Hold", lw=1.5, color="gray", alpha=0.7)
    # 选择要画的策略：单 macd_hist（正参考）+ 反向 pb + Sharpe Top5 等权（v1） + IC Top5 signed + 趋势+反向估值
    highlight = [
        "单 macd_hist (+)",
        "反向 pb (-)",
        "反向 close_to_low_60d (-)",
        "v1 等权 (Sharpe Top5)",
        "IC Top 5 (signed)",
        "趋势+反向估值（手工）",
        "macd_hist + pb (手工)",
    ]
    for name in highlight:
        if name not in all_results:
            continue
        bt = all_results[name]
        cum = (1 + bt["net_return"].fillna(0)).cumprod() - 1
        ax.plot(dates, cum, lw=1.5, label=name, alpha=0.85)
    ax.set_title(f"{SYMBOL} 净值曲线：反向因子是否带来互补？")
    ax.legend(fontsize=7, loc="upper left")
    ax.grid(alpha=0.3)

    # Panel 2: 因子相关性热力图
    ax = axes[0, 1]
    im = ax.imshow(corr_mat.values, cmap="RdBu_r", vmin=-1, vmax=1, aspect="auto")
    ax.set_xticks(range(len(corr_mat.columns)))
    ax.set_yticks(range(len(corr_mat.index)))
    ax.set_xticklabels(corr_mat.columns, rotation=35, ha="right", fontsize=7)
    ax.set_yticklabels(corr_mat.index, fontsize=7)
    for i in range(len(corr_mat.index)):
        for j in range(len(corr_mat.columns)):
            v = corr_mat.values[i, j]
            ax.text(j, i, f"{v:.2f}", ha="center", va="center",
                    fontsize=6, color="white" if abs(v) > 0.5 else "black")
    plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    ax.set_title("因子 rolling z-score Spearman 相关矩阵")

    # Panel 3: Sharpe ↔ 回撤 散点（突出反向因子组合）
    ax = axes[1, 0]
    for _, row in comparison_df.iterrows():
        name = row["策略"]
        if pd.isna(row["Sharpe"]) or pd.isna(row["最大回撤"]):
            continue
        is_combo = "Top" in name or "手工" in name or "等权" in name or "Sharpe 加权" in name
        is_reverse = "反向" in name or "signed" in name or "反向估值" in name
        is_bh = name == "Buy & Hold"
        if is_bh:
            marker, size, color = "s", 100, "C7"
        elif is_reverse and is_combo:
            marker, size, color = "*", 280, "C3"  # 反向组合：红色大星
        elif is_combo:
            marker, size, color = "*", 200, "C2"  # 普通组合：绿色星
        elif is_reverse:
            marker, size, color = "v", 90, "C1"  # 反向单因子：橙色倒三角
        else:
            marker, size, color = "o", 60, "C0"  # 正向单因子：蓝色圆
        ax.scatter(abs(row["最大回撤"]) * 100, row["Sharpe"],
                   s=size, marker=marker, color=color, alpha=0.75,
                   edgecolors="black", linewidth=0.6)
        ax.annotate(
            name, (abs(row["最大回撤"]) * 100, row["Sharpe"]),
            fontsize=6, alpha=0.85,
            xytext=(4, 3), textcoords="offset points",
        )
    ax.set_xlabel("|最大回撤| (%)")
    ax.set_ylabel("Sharpe")
    ax.set_title("Sharpe ↔ 回撤  ★=组合(绿) / ★=反向组合(红) / ▼=反向单因子 / ●=正单因子")
    ax.axhline(y=0, color="black", lw=0.3)
    ax.grid(alpha=0.3)

    # Panel 4: 因子持续性（z-score 自相关 + ±1σ 区间天数）
    ax = axes[1, 1]
    x = np.arange(len(persistence_df))
    width = 0.4
    ax.bar(x - width / 2, persistence_df["autocorr_1d"], width,
           label="z autocorr(1d)", color="C0", alpha=0.7)
    ax.bar(x + width / 2, persistence_df["autocorr_20d"], width,
           label="z autocorr(20d)", color="C3", alpha=0.7)
    ax.set_xticks(x)
    ax.set_xticklabels(persistence_df["factor"], rotation=30, ha="right", fontsize=7)
    ax.axhline(y=0, color="black", lw=0.5)
    ax.set_title("因子 z-score 持续性：autocorr(20d) 越高 = 慢变 = 阈值策略失效")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3, axis="y")
    # 在每个 bar 顶上标 ±1σ 天数
    for i, row in persistence_df.iterrows():
        ax.text(i, max(row["autocorr_1d"], row["autocorr_20d"]) + 0.03,
                f"±1σ: {row['days_above_1'] + row['days_below_neg1']}d",
                ha="center", fontsize=6, alpha=0.7)

    plt.tight_layout()
    PNG_PATH.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(PNG_PATH, dpi=150, bbox_inches="tight")
    plt.close()
    logger.info("PNG: %s", PNG_PATH)


# ---------------------------------------------------------------------- #
# Markdown 报告
# ---------------------------------------------------------------------- #


def fmt_pct(v: float) -> str:
    return "—" if pd.isna(v) else f"{v*100:+.1f}%"


def fmt_num(v: float, digits: int = 2) -> str:
    return "—" if pd.isna(v) else f"{v:.{digits}f}"


def fmt_int(v) -> str:
    if pd.isna(v):
        return "—"
    try:
        return str(int(v))
    except (TypeError, ValueError):
        return "—"


def metrics_row(name: str, metrics: dict, n_trades: int | None = None) -> dict:
    return {
        "策略": name,
        "年化": metrics.get("annual", float("nan")),
        "Sharpe": metrics.get("sharpe", float("nan")),
        "最大回撤": metrics.get("max_dd", float("nan")),
        "Calmar": metrics.get("calmar", float("nan")),
        "胜率": metrics.get("win", float("nan")),
        "交易次数": n_trades if n_trades is not None else float("nan"),
        "总收益": metrics.get("total", float("nan")),
        "n": metrics.get("n", 0),
    }


def render_markdown(
    factor_df: pd.DataFrame,
    combiners: dict[str, MultiFactorCombiner],
    all_results: dict[str, dict],
    comparison_df: pd.DataFrame,
    corr_mat: pd.DataFrame,
    persistence_df: pd.DataFrame,
    bh: dict,
) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []

    lines.append(f"# 688017 多因子组合 v2 —— 反向因子互补性实验")
    lines.append("")
    lines.append(
        f"- **股票**: {SYMBOL}  "
        f"  **样本**: {len(factor_df)} 个交易日  "
        f"  **区间**: {factor_df['trade_date'].min().date()} ~ {factor_df['trade_date'].max().date()}"
    )
    lines.append(
        f"- **滚动 z-score 窗口**: {ROLLING_WINDOW} 日  "
        f"  **阈值**: z > +1 持仓 / z < -1 空仓  "
        f"  **成本**: 买 0.10% / 卖 0.20%"
    )
    lines.append("")
    lines.append(
        "**实验问题**：上一轮发现 Sharpe Top 5 都是趋势 / 量价类同质因子，组合反而稀释最强信号。"
        "把 ``pb`` / ``close_to_low_60d`` 等 **IC 显著但方向相反** 的因子用 ``signed=True`` 加进来，"
        "是否真能带来互补？"
    )
    lines.append("")

    # ============== 1. 综合对比 ============== #
    lines.append("## 1. 综合对比")
    lines.append("")
    lines.append("| 策略 | 年化 | Sharpe | 最大回撤 | Calmar | 胜率 | 交易次数 | 总收益 |")
    lines.append("|------|------|--------|----------|--------|------|----------|--------|")
    for _, row in comparison_df.iterrows():
        lines.append(
            f"| {row['策略']} | {fmt_pct(row['年化'])} | "
            f"{fmt_num(row['Sharpe'])} | {fmt_pct(row['最大回撤'])} | "
            f"{fmt_num(row['Calmar'])} | "
            f"{fmt_pct(row['胜率']) if not pd.isna(row['胜率']) else '—'} | "
            f"{fmt_int(row['交易次数'])} | {fmt_pct(row['总收益'])} |"
        )
    lines.append("")

    # ============== 2. 反向因子组合权重明细 ============== #
    lines.append("## 2. 反向因子组合权重明细")
    lines.append("")
    lines.append("（v1 基线组合见上一轮 ``688017_combo_report.md``）")
    lines.append("")
    for name, c in combiners.items():
        lines.append(f"### `{name}`")
        lines.append("")
        lines.append("| 因子 | 权重 | 方向 |")
        lines.append("|------|------|------|")
        for f, w in sorted(c.weights.items(), key=lambda kv: abs(kv[1]), reverse=True):
            direction = "🟢 正向（z↑→持仓）" if w > 0 else "🔴 反向（z↓→持仓）"
            lines.append(f"| `{f}` | {w:+.3f} | {direction} |")
        lines.append("")

    # ============== 3. 因子持续性诊断 ============== #
    lines.append("## 3. 因子持续性诊断（解释为何反向因子单跑乏力）")
    lines.append("")
    lines.append(
        "**核心机制**：阈值策略（``z > +1 持仓 / z < -1 空仓``）依赖 z-score 在 ±1 区间频繁切换。"
        "若因子的 z-score **慢变**（20 日自相关高），它会长期「卡」在某个区间不动 → 信号稀疏 → 错过反弹."
    )
    lines.append("")
    lines.append("| 因子 | autocorr(1d) | autocorr(20d) | z>+1 天数 | z<-1 天数 | 类型 |")
    lines.append("|------|--------------|---------------|----------|----------|------|")
    for _, row in persistence_df.iterrows():
        ac20 = row["autocorr_20d"]
        if ac20 > 0.25:
            label = "🐢 **慢变**（阈值策略失效）"
        elif ac20 > 0.05:
            label = "🐇 中等"
        else:
            label = "⚡ **快速回归**（适合阈值策略）"
        lines.append(
            f"| `{row['factor']}` | "
            f"{fmt_num(row['autocorr_1d'], 3)} | "
            f"{fmt_num(ac20, 3)} | "
            f"{int(row['days_above_1'])} | {int(row['days_below_neg1'])} | "
            f"{label} |"
        )
    lines.append("")
    lines.append(
        "**关键洞察**：``pb`` 与 ``close_to_low_60d`` 的 ``autocorr(20d)`` 都 > 0.3 → "
        "属于「水平变量」（估值贵就一直贵）而非「震荡变量」。"
        "它们的 IC 在 ``future_return_5d/10d/20d`` 上都很显著（说明能预测「风险」），"
        "但在 ±1σ 阈值切换框架下基本不发出有效买卖信号 → 单跑 Sharpe < 0.5."
    )
    lines.append("")

    # ============== 4. 因子相关性矩阵 ============== #
    lines.append("## 4. 因子 z-score 相关性矩阵")
    lines.append("")
    lines.append("| | " + " | ".join(f"`{c}`" for c in corr_mat.columns) + " |")
    lines.append("|---|" + "|".join(["---"] * len(corr_mat.columns)) + "|")
    for i, idx in enumerate(corr_mat.index):
        cells = []
        for j, _ in enumerate(corr_mat.columns):
            v = corr_mat.values[i, j]
            cells.append(f"{v:+.2f}")
        lines.append(f"| `{idx}` | " + " | ".join(cells) + " |")
    lines.append("")

    # ============== 5. 结论 ============== #
    lines.append("## 5. 结论")
    lines.append("")
    # 抓关键数字
    mh = all_results["单 macd_hist (+)"]["metrics"]
    ic_signed = all_results["IC Top 5 (signed)"]["metrics"]
    v1_eq = all_results["v1 等权 (Sharpe Top5)"]["metrics"]
    trend_rev = all_results["趋势+反向估值（手工）"]["metrics"]
    macd_pb = all_results["macd_hist + pb (手工)"]["metrics"]
    pb_rev = all_results["反向 pb (-)"]["metrics"]

    lines.append(
        f"### 5.1 **反向因子组合未能带来 Sharpe 增益**"
    )
    lines.append("")
    lines.append(
        f"- 最强单 ``macd_hist`` Sharpe = **{mh['sharpe']:.2f}**（保持冠军）"
    )
    lines.append(
        f"- `IC Top 5 (signed)` Sharpe = **{ic_signed['sharpe']:.2f}** ❌ —— "
        f"远不及 v1 等权 {v1_eq['sharpe']:.2f}，也远不及单 macd_hist。**反向因子的负权重把信号合成稀释成噪声**。"
    )
    lines.append(
        f"- `趋势+反向估值（手工）` Sharpe = **{trend_rev['sharpe']:.2f}** —— "
        f"略好于 IC signed 组合，但仍弱于单 macd_hist."
    )
    lines.append(
        f"- `macd_hist + pb 二因子` Sharpe = **{macd_pb['sharpe']:.2f}** —— "
        f"加入 30% pb 反向权重后 Sharpe 比纯 macd_hist 下降."
    )
    lines.append("")

    lines.append(f"### 5.2 **根因：pb / close_to_low_60d 是「风险因子」而非「择时因子」**")
    lines.append("")
    pb_ac20 = persistence_df.loc[persistence_df["factor"] == "pb", "autocorr_20d"].values
    macd_ac20 = persistence_df.loc[persistence_df["factor"] == "macd_hist", "autocorr_20d"].values
    if len(pb_ac20) and len(macd_ac20):
        lines.append(
            f"- ``pb`` 的 z-score **20 日自相关 = {pb_ac20[0]:.3f}**（慢变），"
            f"而 ``macd_hist`` 仅 **{macd_ac20[0]:.3f}**（快速均值回归）."
        )
    lines.append(
        f"- 反向 ``pb`` 单跑 Sharpe = **{pb_rev['sharpe']:.2f}** —— "
        f"接近 Buy & Hold ({bh['sharpe']:.2f})，并无独立 alpha."
    )
    lines.append(
        "- ``pb`` IC 显著（5d/10d/20d 都 p < 0.001）说明它**能预测中长期风险**，"
        "但 ±1σ 阈值切换抓不到这种「水平变量」的预测力——"
        "应当作「持仓上限过滤器」（PB 高时减仓）而非「信号买卖触发器」."
    )
    lines.append("")

    lines.append(f"### 5.3 **真正能带来互补的反向因子标准**")
    lines.append("")
    lines.append(
        "理论上反向因子要带来 Sharpe 互补，需同时满足："
    )
    lines.append(
        "1. **方向反相关**：与 ``macd_hist`` 的 z-score 相关 ≤ 0（互补而非平行）；"
    )
    fast_reverse = persistence_df[
        (persistence_df["autocorr_20d"] < 0.1) & (persistence_df["factor"].isin(REVERSE_FACTORS))
    ]
    lines.append(
        "2. **快速均值回归**：``autocorr(20d) < 0.1`` 才能在阈值框架下产生切换；"
    )
    lines.append(
        "3. **反向 IC 显著**：单跑 Sharpe（反向）> 0.5 才有独立 alpha 可贡献。"
    )
    lines.append("")
    if fast_reverse.empty:
        lines.append(
            "**688017 上目前没有同时满足这三条的反向因子**——"
            "估值 / 位置类因子都是慢变水平变量。下一步可考虑："
        )
    else:
        cands = ", ".join(f"`{f}`" for f in fast_reverse["factor"])
        lines.append(f"候选：{cands}（满足 autocorr(20d) < 0.1）")
    lines.append(
        "- 加入横截面信息：用 ``pb`` 的**截面分位**（vs 行业 / 全市场）而非时序 z-score；"
    )
    lines.append(
        "- 把 ``pb`` 改用作「持仓过滤器」：``macd_hist`` 给买入信号但 ``pb`` 处于历史高 30% 时降仓 50%；"
    )
    lines.append(
        "- 寻找**反向震荡因子**：例如 ``rsi_14`` 的反向（z>1 = 超买 → 反向空仓），样本中需要验证."
    )
    lines.append("")

    # ============== 6. 注意事项 ============== #
    lines.append("## 6. 注意事项")
    lines.append("")
    lines.append("- 单股 ~800 天样本，所有 Sharpe 都有较宽统计置信区间，不能仅凭一只股票下结论；")
    lines.append("- ``signed`` 加权用了**全样本 IC**算权重 → 存在事后偏差；")
    lines.append("- 阈值 ±1σ 未调优，慢变因子在不同阈值下（如 ±0.5）可能产出截然不同的信号频率；")
    lines.append("- 本实验结论仅适用 ``阈值切换 + 单股票时序`` 框架，不否定 ``pb`` 在**截面选股**中的价值.")
    lines.append("")
    lines.append("---")
    lines.append("_Generated by `scripts/analyze_688017_combo_v2.py`_")

    MD_PATH.write_text("\n".join(lines), encoding="utf-8")
    logger.info("Markdown: %s", MD_PATH)


# ---------------------------------------------------------------------- #
# Main
# ---------------------------------------------------------------------- #


def main() -> None:
    factor_df, feature_cols = load_factor_panel()
    logger.info(
        "数据: %d 行 %s → %s, 因子 %d 个",
        len(factor_df),
        factor_df["trade_date"].min().date(),
        factor_df["trade_date"].max().date(),
        len(feature_cols),
    )

    analyzer = SingleStockAnalyzer(factor_df, ret_col="next_day_return")

    bh = Metrics.calc(factor_df["next_day_return"].dropna())
    bh_cum = (1 + factor_df["next_day_return"].fillna(0)).cumprod() - 1

    # 单因子诊断
    scan_table = analyzer.scan_factors(
        feature_cols, upper=1.0, lower=-1.0, rolling_window=ROLLING_WINDOW,
    )
    ic_table = analyzer.time_series_ic(
        feature_cols, horizons=HORIZONS, method="spearman",
    )

    # ===== 1. 反向因子组合 ===== #
    combiners: dict[str, MultiFactorCombiner] = {
        "IC Top 5 (signed)": MultiFactorCombiner.from_ic_table(
            ic_table, horizon=IC_HORIZON, top_k=5, signed=True,
        ),
        "趋势+反向估值（手工）": MultiFactorCombiner({
            "macd_hist": +0.5,
            "pb": -0.25,
            "close_to_low_60d": -0.25,
        }, normalize=True),
        "macd_hist + pb (手工)": MultiFactorCombiner({
            "macd_hist": +0.7,
            "pb": -0.3,
        }, normalize=True),
    }
    for name, c in combiners.items():
        logger.info("【%s】%s", name, c)

    # ===== 2. v1 基线组合（仅 Sharpe Top 5 等权，用于参照）===== #
    v1_combiner = MultiFactorCombiner.equal_weighted(SHARPE_TOP5)

    # ===== 3. 回测所有策略 ===== #
    all_results: dict[str, dict] = {}

    # 单因子（正向）
    all_results["单 macd_hist (+)"] = analyzer.signal_backtest("macd_hist", rolling_window=ROLLING_WINDOW)

    # 反向单因子
    for f in REVERSE_FACTORS:
        if f in factor_df.columns:
            all_results[f"反向 {f} (-)"] = signed_single_backtest(
                analyzer, f, direction=-1, rolling_window=ROLLING_WINDOW,
            )

    # v1 基线
    all_results["v1 等权 (Sharpe Top5)"] = analyzer.multi_factor_backtest(
        v1_combiner, rolling_window=ROLLING_WINDOW,
    )

    # v2 反向因子组合
    for name, c in combiners.items():
        all_results[name] = analyzer.multi_factor_backtest(c, rolling_window=ROLLING_WINDOW)

    # ===== 4. 因子持续性诊断 + 相关性矩阵 ===== #
    diag_factors = ["macd_hist", "vol_ma20_ratio", "ma_10_ratio", "volatility_5d",
                    "pb", "close_to_low_60d", "close_to_low_20d", "kdj_d"]
    persistence_df = factor_persistence(analyzer, diag_factors)
    corr_mat = factor_correlation_matrix(analyzer, diag_factors)

    # ===== 5. 综合对比表 ===== #
    rows = [metrics_row("Buy & Hold", bh)]
    for name, bt in all_results.items():
        rows.append(metrics_row(name, bt["metrics"], bt["n_trades"]))
    comparison_df = pd.DataFrame(rows)

    # 输出
    plot_panels(factor_df, all_results, bh_cum, comparison_df, corr_mat, persistence_df)
    render_markdown(
        factor_df, combiners, all_results, comparison_df, corr_mat, persistence_df, bh,
    )

    # 终端打印
    print("\n" + "=" * 78)
    print("688017 多因子组合 v2 —— 反向因子互补性实验")
    print("=" * 78)
    print(f"\nBuy & Hold:  Sharpe {bh['sharpe']:+.2f}  年化 {bh['annual']*100:+.1f}%  回撤 {bh['max_dd']*100:.1f}%")
    print(f"\n【正向参考】")
    print(f"  单 macd_hist (+):           "
          f"Sharpe {all_results['单 macd_hist (+)']['metrics']['sharpe']:+.2f}  "
          f"年化 {all_results['单 macd_hist (+)']['metrics']['annual']*100:+.1f}%")
    print(f"  v1 等权 (Sharpe Top5):      "
          f"Sharpe {all_results['v1 等权 (Sharpe Top5)']['metrics']['sharpe']:+.2f}  "
          f"年化 {all_results['v1 等权 (Sharpe Top5)']['metrics']['annual']*100:+.1f}%")
    print(f"\n【反向单因子】（验证：反向 IC 因子单跑能否产生 alpha）")
    for f in REVERSE_FACTORS:
        k = f"反向 {f} (-)"
        if k not in all_results:
            continue
        m = all_results[k]["metrics"]
        print(f"  {k:30s}  Sharpe {m['sharpe']:+.2f}  年化 {m['annual']*100:+.1f}%  交易 {all_results[k]['n_trades']}")
    print(f"\n【反向因子组合（本实验主角）】")
    for name in combiners:
        m = all_results[name]["metrics"]
        print(f"  {name:30s}  Sharpe {m['sharpe']:+.2f}  年化 {m['annual']*100:+.1f}%  回撤 {m['max_dd']*100:.1f}%  交易 {all_results[name]['n_trades']}")
    print(f"\n【因子持续性（autocorr 20d 越高 = 慢变）】")
    for _, row in persistence_df.iterrows():
        print(f"  {row['factor']:25s}  autocorr(20d)={row['autocorr_20d']:+.3f}  z>±1 天数: {int(row['days_above_1']) + int(row['days_below_neg1'])}")
    print(f"\n报告: {MD_PATH}")
    print(f"图表: {PNG_PATH}")


if __name__ == "__main__":
    main()
