#!/usr/bin/env python3
"""688017 多因子组合 v3 —— 阈值网格搜索 + walk-forward 去 look-ahead.

接续 v1 / v2 的"下一步建议"中剩余两条：

1. **阈值网格搜索**：当前 ±1σ 是经验值，遍历 (upper, lower) 看最优 vs 最稳健阈值；
2. **Walk-forward 重选权重**：去掉 v1/v2 用全样本算 scan_table 的 look-ahead bias，
   定量给出"事后偏差吃掉了多少 Sharpe".

输出::

    storage/reports/688017_combo_v3_report.md
    storage/reports/688017_combo_v3_panels.png

依赖前置：``cs_data_688017.csv``.
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
from kss.backtest.walk_forward_combiner import WalkForwardCombiner  # noqa: E402
from kss.features.pipeline import FactorPipeline  # noqa: E402
from kss.strategies.multi_factor import MultiFactorCombiner  # noqa: E402


# ---------------------------------------------------------------------- #
# 常量
# ---------------------------------------------------------------------- #

DATA_PATH = PROJECT_ROOT / "cs_data_688017.csv"
SYMBOL = "688017.SH"
ROLLING_WINDOW = 60
HORIZONS = ["next_day_return", "future_return_5d", "future_return_10d", "future_return_20d"]
REPORT_DIR = PROJECT_ROOT / "storage" / "reports"
MD_PATH = REPORT_DIR / "688017_combo_v3_report.md"
PNG_PATH = REPORT_DIR / "688017_combo_v3_panels.png"

# 网格搜索参数
UPPER_GRID = [0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0]
LOWER_GRID = [-2.0, -1.75, -1.5, -1.25, -1.0, -0.75, -0.5, -0.25]

# Walk-forward 参数
TRAIN_WINDOW = 200
RETRAIN_FREQ = 20
TOP_K = 5

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
# 阈值网格热力图
# ---------------------------------------------------------------------- #


def plot_threshold_heatmap(
    ax: plt.Axes,
    grid: pd.DataFrame,
    metric: str = "sharpe",
    title: str = "",
) -> None:
    if grid.empty:
        ax.set_visible(False)
        return
    pivot = grid.pivot_table(index="lower", columns="upper", values=metric)
    pivot = pivot.sort_index(ascending=False)  # lower 从上到下递增
    im = ax.imshow(pivot.values, cmap="RdYlGn", aspect="auto")
    ax.set_xticks(range(len(pivot.columns)))
    ax.set_xticklabels([f"{v:.2f}" for v in pivot.columns], fontsize=8)
    ax.set_yticks(range(len(pivot.index)))
    ax.set_yticklabels([f"{v:.2f}" for v in pivot.index], fontsize=8)
    ax.set_xlabel("upper")
    ax.set_ylabel("lower")
    ax.set_title(title)
    # 单元格数值
    for i in range(pivot.shape[0]):
        for j in range(pivot.shape[1]):
            v = pivot.values[i, j]
            if pd.notna(v):
                ax.text(j, i, f"{v:.2f}",
                        ha="center", va="center", fontsize=6,
                        color="white" if abs(v) > 0.8 else "black")
    plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04)


# ---------------------------------------------------------------------- #
# Walk-forward 权重历史可视化
# ---------------------------------------------------------------------- #


def plot_weight_history(
    ax: plt.Axes,
    weights_history: list[dict],
    top_n_factors: int = 12,
) -> None:
    """权重历史堆叠柱（每个 retrain 一根柱，展示选了哪些因子）."""
    if not weights_history:
        ax.set_visible(False)
        return
    # 收集所有出现过的因子
    all_factors: list[str] = []
    for h in weights_history:
        for f in h["weights"]:
            if f not in all_factors:
                all_factors.append(f)
    # 限制显示 top_n_factors（按出现次数）
    counts = {f: sum(1 for h in weights_history if f in h["weights"]) for f in all_factors}
    top_factors = [f for f, _ in sorted(counts.items(), key=lambda x: x[1], reverse=True)[:top_n_factors]]

    dates = [h["trade_date"] for h in weights_history]
    bottom = np.zeros(len(dates))
    colors = plt.cm.tab20.colors
    for i, f in enumerate(top_factors):
        vals = np.array([h["weights"].get(f, 0.0) for h in weights_history])
        ax.bar(dates, vals, bottom=bottom, label=f, width=18,
               color=colors[i % len(colors)], edgecolor="white", linewidth=0.2)
        bottom = bottom + vals
    ax.set_title(f"Walk-forward 权重历史（每个 retrain 选 Top {TOP_K} 因子）")
    ax.legend(fontsize=6, ncol=3, loc="upper right")
    ax.set_ylabel("权重")
    ax.tick_params(axis="x", rotation=30)
    ax.grid(alpha=0.3, axis="y")


# ---------------------------------------------------------------------- #
# 4 面板可视化
# ---------------------------------------------------------------------- #


def plot_panels(
    factor_df: pd.DataFrame,
    grid_macd: pd.DataFrame,
    grid_combo: pd.DataFrame,
    wf_results: dict[str, dict],
    baseline_results: dict[str, dict],
    bh_cum: pd.Series,
) -> None:
    fig, axes = plt.subplots(2, 2, figsize=(16, 13))

    # Panel 1: macd_hist 阈值网格热力图
    plot_threshold_heatmap(
        axes[0, 0], grid_macd, metric="sharpe",
        title="macd_hist 阈值网格 Sharpe 热力图",
    )

    # Panel 2: v1 等权组合阈值网格热力图
    plot_threshold_heatmap(
        axes[0, 1], grid_combo, metric="sharpe",
        title="v1 等权 Top5 阈值网格 Sharpe 热力图",
    )

    # Panel 3: walk-forward vs 全样本 baseline 净值
    ax = axes[1, 0]
    dates = factor_df["trade_date"]
    ax.plot(dates, bh_cum, label="Buy & Hold", color="gray", lw=1.5, alpha=0.7)
    for name, bt in baseline_results.items():
        cum = (1 + bt["net_return"].fillna(0)).cumprod() - 1
        ax.plot(dates, cum, label=f"事后【{name}】", lw=1.2, alpha=0.7, ls="--")
    for name, bt in wf_results.items():
        cum = (1 + bt["net_return"].fillna(0)).cumprod() - 1
        ax.plot(dates, cum, label=f"WF【{name}】", lw=2, alpha=0.9)
    ax.set_title("事后偏差对比：实线=Walk-forward 实盘可达，虚线=全样本事后")
    ax.legend(fontsize=7, loc="upper left")
    ax.grid(alpha=0.3)

    # Panel 4: walk-forward 权重历史（取一个代表性 WF 结果）
    if wf_results:
        rep_name = list(wf_results.keys())[0]
        plot_weight_history(axes[1, 1], wf_results[rep_name]["weights_history"])

    plt.tight_layout()
    PNG_PATH.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(PNG_PATH, dpi=150, bbox_inches="tight")
    plt.close()
    logger.info("PNG: %s", PNG_PATH)


# ---------------------------------------------------------------------- #
# Markdown
# ---------------------------------------------------------------------- #


def fmt_pct(v: float) -> str:
    return "—" if pd.isna(v) else f"{v*100:+.1f}%"


def fmt_num(v: float, d: int = 2) -> str:
    return "—" if pd.isna(v) else f"{v:.{d}f}"


def metrics_row(name: str, metrics: dict, n_trades: int | None = None) -> dict:
    return {
        "策略": name,
        "年化": metrics.get("annual", float("nan")),
        "Sharpe": metrics.get("sharpe", float("nan")),
        "最大回撤": metrics.get("max_dd", float("nan")),
        "胜率": metrics.get("win", float("nan")),
        "交易次数": n_trades if n_trades is not None else float("nan"),
        "总收益": metrics.get("total", float("nan")),
    }


def render_markdown(
    factor_df: pd.DataFrame,
    grid_macd: pd.DataFrame,
    grid_combo: pd.DataFrame,
    wf_results: dict[str, dict],
    baseline_results: dict[str, dict],
    comparison_df: pd.DataFrame,
    bh: dict,
) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []

    lines.append("# 688017 多因子组合 v3 —— 阈值网格搜索 + 去 look-ahead")
    lines.append("")
    lines.append(
        f"- **股票**: {SYMBOL}  "
        f"  **样本**: {len(factor_df)} 个交易日  "
        f"  **区间**: {factor_df['trade_date'].min().date()} ~ {factor_df['trade_date'].max().date()}"
    )
    lines.append(
        f"- **Walk-forward**: train_window={TRAIN_WINDOW}, "
        f"retrain_freq={RETRAIN_FREQ}, top_k={TOP_K}, rolling_window={ROLLING_WINDOW}"
    )
    lines.append("")
    lines.append(
        "**两个核心实验**："
    )
    lines.append(
        "1. **阈值网格**：当前默认 ±1σ 是经验值，遍历 (upper, lower) ∈ "
        f"{UPPER_GRID} × {LOWER_GRID} 看最优 vs 邻域稳健阈值；"
    )
    lines.append(
        "2. **Walk-forward 重选权重**：去掉 v1/v2 用全样本算 scan_table 的 look-ahead bias，"
        "定量给出事后偏差吃掉了多少 Sharpe."
    )
    lines.append("")

    # ============== 1. 阈值网格搜索 ============== #
    lines.append("## 1. 阈值网格搜索")
    lines.append("")

    if not grid_macd.empty:
        best_macd = grid_macd.iloc[0]
        best_robust_macd = grid_macd.sort_values("robust_sharpe", ascending=False).iloc[0]
        lines.append("### 1.1 `macd_hist` 单因子")
        lines.append("")
        lines.append("**Top 5 阈值组合（按 Sharpe 降序）**:")
        lines.append("")
        lines.append("| upper | lower | Sharpe | 年化 | 最大回撤 | 交易次数 | 邻域 Sharpe（稳健） |")
        lines.append("|-------|-------|--------|------|----------|----------|------|")
        for _, row in grid_macd.head(5).iterrows():
            lines.append(
                f"| {row['upper']:.2f} | {row['lower']:.2f} | "
                f"{row['sharpe']:.2f} | {fmt_pct(row['annual'])} | "
                f"{fmt_pct(row['max_dd'])} | {int(row['n_trades'])} | "
                f"{row['robust_sharpe']:.2f} |"
            )
        lines.append("")
        lines.append(
            f"- **最优单点**: (u={best_macd['upper']}, l={best_macd['lower']}) → "
            f"Sharpe **{best_macd['sharpe']:.2f}** / 年化 {fmt_pct(best_macd['annual'])}"
        )
        lines.append(
            f"- **邻域最稳健**: (u={best_robust_macd['upper']}, l={best_robust_macd['lower']}) → "
            f"Sharpe {best_robust_macd['sharpe']:.2f}（邻域中位数 {best_robust_macd['robust_sharpe']:.2f}）"
        )
        lines.append(
            f"- **vs 默认 (±1)**: Sharpe ~1.18 → {best_macd['sharpe']:.2f}，"
            f"提升 **{(best_macd['sharpe'] / 1.18 - 1) * 100:+.0f}%**"
        )
        lines.append("")

    if not grid_combo.empty:
        best_combo = grid_combo.iloc[0]
        lines.append("### 1.2 v1 等权 Top5 组合")
        lines.append("")
        lines.append("**Top 5 阈值组合**:")
        lines.append("")
        lines.append("| upper | lower | Sharpe | 年化 | 最大回撤 | 交易次数 |")
        lines.append("|-------|-------|--------|------|----------|----------|")
        for _, row in grid_combo.head(5).iterrows():
            lines.append(
                f"| {row['upper']:.2f} | {row['lower']:.2f} | "
                f"{row['sharpe']:.2f} | {fmt_pct(row['annual'])} | "
                f"{fmt_pct(row['max_dd'])} | {int(row['n_trades'])} |"
            )
        lines.append("")
        lines.append(
            f"- **最优**: (u={best_combo['upper']}, l={best_combo['lower']}) → "
            f"Sharpe **{best_combo['sharpe']:.2f}** / 年化 {fmt_pct(best_combo['annual'])}"
        )
        lines.append("")

    lines.append(
        "**重要警示**：网格搜索结果是 **in-sample**——这里的「最优 Sharpe」用了全样本数据，"
        "实盘上选哪组阈值同样需要 walk-forward 验证；当前最佳实践是看「邻域稳健」而非单点最优."
    )
    lines.append("")

    # ============== 2. Walk-forward 去 look-ahead ============== #
    lines.append("## 2. Walk-forward 去 look-ahead")
    lines.append("")
    lines.append(
        f"在每个 retrain 点（每 {RETRAIN_FREQ} 个交易日），只用过去 {TRAIN_WINDOW} 天历史数据"
        "重跑 ``scan_factors`` / ``time_series_ic`` 选出 Top K 因子并构造 ``MultiFactorCombiner``，"
        f"再用该 combiner 在接下来 {RETRAIN_FREQ} 个交易日产生信号."
    )
    lines.append("")
    lines.append("### 2.1 综合对比")
    lines.append("")
    lines.append("| 策略 | 年化 | Sharpe | 最大回撤 | 胜率 | 交易次数 | 总收益 |")
    lines.append("|------|------|--------|----------|------|----------|--------|")
    for _, row in comparison_df.iterrows():
        win = fmt_pct(row['胜率']) if not pd.isna(row['胜率']) else '—'
        n_t = "—" if pd.isna(row['交易次数']) else str(int(row['交易次数']))
        lines.append(
            f"| {row['策略']} | {fmt_pct(row['年化'])} | "
            f"{fmt_num(row['Sharpe'])} | {fmt_pct(row['最大回撤'])} | "
            f"{win} | {n_t} | {fmt_pct(row['总收益'])} |"
        )
    lines.append("")

    # 关键 delta
    lines.append("### 2.2 事后偏差量化")
    lines.append("")
    for builder_name in baseline_results:
        if builder_name not in wf_results:
            continue
        b = baseline_results[builder_name]["metrics"]
        w = wf_results[builder_name]["metrics"]
        d_sharpe = w["sharpe"] - b["sharpe"]
        d_ann = w["annual"] - b["annual"]
        lines.append(
            f"- **{builder_name}**：事后 Sharpe **{b['sharpe']:.2f}** → "
            f"WF Sharpe **{w['sharpe']:.2f}**（Δ {d_sharpe:+.2f}）；"
            f"年化 {b['annual']*100:+.1f}% → {w['annual']*100:+.1f}%（Δ {d_ann*100:+.1f}pp）"
        )
    lines.append("")

    # ============== 3. 权重稳定性诊断 ============== #
    if wf_results:
        lines.append("### 2.3 权重稳定性")
        lines.append("")
        for name, res in wf_results.items():
            hist = res["weights_history"]
            if not hist:
                continue
            # 统计每个因子在多少次 retrain 出现
            all_factors = {}
            for h in hist:
                for f in h["weights"]:
                    all_factors[f] = all_factors.get(f, 0) + 1
            n_retrains = len(hist)
            stable = sorted(all_factors.items(), key=lambda x: x[1], reverse=True)[:8]
            lines.append(f"**{name}**（共 {n_retrains} 次 retrain）— 因子出现次数 Top 8:")
            lines.append("")
            lines.append("| 因子 | 出现次数 | 比例 |")
            lines.append("|------|----------|------|")
            for f, cnt in stable:
                lines.append(f"| `{f}` | {cnt} | {cnt/n_retrains*100:.0f}% |")
            lines.append("")

    # ============== 4. 结论 ============== #
    lines.append("## 3. 结论")
    lines.append("")
    # 网格优化
    if not grid_macd.empty:
        best = grid_macd.iloc[0]
        lines.append(
            f"1. **阈值网格可优化但要警惕过拟合**：`macd_hist` 在 "
            f"(u={best['upper']}, l={best['lower']}) 下 Sharpe **{best['sharpe']:.2f}**，"
            f"比默认 ±1 的 1.18 高 {(best['sharpe'] / 1.18 - 1) * 100:+.0f}%。"
            f"但这是 in-sample 最优；建议用 walk-forward 验证或选邻域稳健点."
        )
    # 事后偏差量化
    if wf_results and baseline_results:
        first_b = list(baseline_results.keys())[0]
        if first_b in wf_results:
            b_sharpe = baseline_results[first_b]["metrics"]["sharpe"]
            w_sharpe = wf_results[first_b]["metrics"]["sharpe"]
            lines.append(
                f"2. **事后偏差吃掉了大部分 Sharpe**：以 `{first_b}` 为例，"
                f"全样本事后 Sharpe **{b_sharpe:.2f}** → walk-forward **{w_sharpe:.2f}**，"
                f"实盘可达 Sharpe 只有事后的 **{w_sharpe/max(b_sharpe, 1e-9)*100:.0f}%**。"
                f"前几轮报告里的 1.00/1.18 都严重高估了 Sharpe 实盘性能."
            )
    # 权重不稳定性洞察
    if wf_results:
        rep = list(wf_results.values())[0]
        hist = rep["weights_history"]
        if hist:
            counter = {}
            for h in hist:
                for f in h["weights"]:
                    counter[f] = counter.get(f, 0) + 1
            most_common = max(counter.items(), key=lambda x: x[1])
            persist_rate = most_common[1] / len(hist) * 100
            lines.append(
                f"3. **Top K 因子高度不稳定**：最常被选中的因子 `{most_common[0]}` 也只在 "
                f"**{persist_rate:.0f}%** 的 retrain 点出现。"
                "这意味着「Sharpe 排名前 K」这套规则本身就在过拟合最近 200 天的局部噪声，"
                "并不能稳定捕获该股票的预测信号."
            )
    lines.append(
        "4. **下一步真正可行的方向**："
    )
    lines.append(
        "   - 不要按 retrain 选 Top K，而是用**固定一组先验信念因子**（如 `macd_hist` 单因子，已证明跨时段稳健）;"
    )
    lines.append(
        "   - 把多股票联合训练（横截面），让单股噪声被池化平均掉；"
    )
    lines.append(
        "   - 用 ``BacktestEngine.walk_forward`` 的 LightGBM 模式做非线性组合，"
        "避免线性 z-score 加权对慢变因子的失效."
    )
    lines.append("")

    # ============== 5. 注意事项 ============== #
    lines.append("## 4. 注意事项")
    lines.append("")
    lines.append(
        f"- Walk-forward 在最早 {TRAIN_WINDOW} + {ROLLING_WINDOW} ≈ 260 天之前**无信号**，"
        f"导致总样本被 ~1/3 截短，Sharpe 估计置信区间变宽；"
    )
    lines.append(
        f"- ``retrain_freq={RETRAIN_FREQ}`` 经验值，不同频率会改变权重稳定性的观察结论；"
    )
    lines.append(
        "- 网格热力图是 in-sample，未做 walk-forward 阈值搜索（避免计算量爆炸），下一步可叠加；"
    )
    lines.append(
        "- 所有结论仅针对 688017 单只股票，不能推广到整个股票池。"
    )
    lines.append("")
    lines.append("---")
    lines.append("_Generated by `scripts/analyze_688017_combo_v3.py`_")

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

    # ===== 1. 阈值网格搜索 ===== #
    logger.info("【1】阈值网格搜索：macd_hist 单因子（%d 个点）",
                len(UPPER_GRID) * len(LOWER_GRID))
    grid_macd = analyzer.threshold_grid_search(
        "macd_hist", upper_grid=UPPER_GRID, lower_grid=LOWER_GRID,
        rolling_window=ROLLING_WINDOW,
    )
    logger.info("  最优单点: %s", grid_macd.iloc[0][["upper", "lower", "sharpe"]].to_dict())

    logger.info("【1】阈值网格搜索：v1 等权 Top5 组合")
    scan_table = analyzer.scan_factors(feature_cols, rolling_window=ROLLING_WINDOW)
    top5 = scan_table.head(TOP_K)["factor"].tolist()
    combiner_v1 = MultiFactorCombiner.equal_weighted(top5)
    grid_combo = analyzer.threshold_grid_search(
        combiner_v1, upper_grid=UPPER_GRID, lower_grid=LOWER_GRID,
        rolling_window=ROLLING_WINDOW,
    )
    logger.info("  最优单点: %s", grid_combo.iloc[0][["upper", "lower", "sharpe"]].to_dict())

    # ===== 2. Walk-forward 实验 ===== #
    logger.info("【2】Walk-forward 三种 builder 对比（train_window=%d, retrain=%d）",
                TRAIN_WINDOW, RETRAIN_FREQ)
    wf_builders: dict = {
        "等权 Top5 (Sharpe 排名)": WalkForwardCombiner.builder_equal_topk_sharpe(top_k=TOP_K),
        "Sharpe 加权 Top5": WalkForwardCombiner.builder_sharpe_topk(top_k=TOP_K),
        "IC 加权 Top5 (signed)": WalkForwardCombiner.builder_ic_topk(top_k=TOP_K, signed=True),
    }
    wf_results: dict[str, dict] = {}
    for name, builder in wf_builders.items():
        wfc = WalkForwardCombiner(
            factor_df=factor_df, feature_cols=feature_cols,
            combiner_builder=builder,
            train_window=TRAIN_WINDOW, retrain_freq=RETRAIN_FREQ,
            rolling_window=ROLLING_WINDOW,
        )
        wf_results[name] = wfc.run()
        m = wf_results[name]["metrics"]
        logger.info(
            "  WF [%s]: Sharpe=%.2f, 年化=%.1f%%, 回撤=%.1f%%, retrain=%d",
            name, m["sharpe"], m["annual"]*100, m["max_dd"]*100,
            wf_results[name]["n_retrains"],
        )

    # ===== 3. 全样本 baseline 对照 ===== #
    logger.info("【3】全样本 baseline（有 look-ahead）对照")
    baseline_results: dict[str, dict] = {}
    # 全样本等权 Top 5
    baseline_results["等权 Top5 (Sharpe 排名)"] = analyzer.multi_factor_backtest(
        MultiFactorCombiner.equal_weighted(top5),
        rolling_window=ROLLING_WINDOW,
    )
    # 全样本 Sharpe 加权
    baseline_results["Sharpe 加权 Top5"] = analyzer.multi_factor_backtest(
        MultiFactorCombiner.from_scan_table(scan_table, top_k=TOP_K, weight_col="sharpe"),
        rolling_window=ROLLING_WINDOW,
    )
    # 全样本 IC 加权 (signed) Top 5
    ic_table = analyzer.time_series_ic(feature_cols, horizons=HORIZONS, method="spearman")
    baseline_results["IC 加权 Top5 (signed)"] = analyzer.multi_factor_backtest(
        MultiFactorCombiner.from_ic_table(
            ic_table, horizon="future_return_5d", top_k=TOP_K, signed=True,
        ),
        rolling_window=ROLLING_WINDOW,
    )

    # 综合对比表
    rows = [metrics_row("Buy & Hold", bh)]
    rows.append(metrics_row("单 macd_hist (默认 ±1)",
                            analyzer.signal_backtest("macd_hist", rolling_window=ROLLING_WINDOW)["metrics"],
                            analyzer.signal_backtest("macd_hist", rolling_window=ROLLING_WINDOW)["n_trades"]))
    # 网格最优 macd_hist
    if not grid_macd.empty:
        best = grid_macd.iloc[0]
        bt = analyzer.signal_backtest(
            "macd_hist", upper=float(best["upper"]), lower=float(best["lower"]),
            rolling_window=ROLLING_WINDOW,
        )
        rows.append(metrics_row(
            f"单 macd_hist (网格最优 u={best['upper']}, l={best['lower']})",
            bt["metrics"], bt["n_trades"],
        ))
    # baseline
    for name, bt in baseline_results.items():
        rows.append(metrics_row(f"事后【{name}】", bt["metrics"], bt["n_trades"]))
    # walk-forward
    for name, bt in wf_results.items():
        rows.append(metrics_row(f"WF【{name}】", bt["metrics"], bt["n_trades"]))
    comparison_df = pd.DataFrame(rows)

    # 输出
    plot_panels(factor_df, grid_macd, grid_combo, wf_results, baseline_results, bh_cum)
    render_markdown(
        factor_df, grid_macd, grid_combo, wf_results, baseline_results, comparison_df, bh,
    )

    # 终端打印
    print("\n" + "=" * 78)
    print(f"688017 多因子组合 v3 —— 阈值网格 + 去 look-ahead")
    print("=" * 78)
    print(f"\nBuy & Hold:  Sharpe {bh['sharpe']:+.2f}  年化 {bh['annual']*100:+.1f}%  回撤 {bh['max_dd']*100:.1f}%")
    print(f"\n【1】阈值网格搜索 (macd_hist)")
    if not grid_macd.empty:
        best = grid_macd.iloc[0]
        print(f"  最优: u={best['upper']:.2f}, l={best['lower']:.2f}  → Sharpe {best['sharpe']:+.2f}  年化 {best['annual']*100:+.1f}%")
        print(f"  邻域稳健: 邻域 Sharpe 中位 {best['robust_sharpe']:.2f}")
    print(f"\n【2】Walk-forward 去 look-ahead 对比")
    for name in wf_builders:
        b = baseline_results[name]["metrics"]
        w = wf_results[name]["metrics"]
        print(
            f"  {name:25s}  事后 Sharpe {b['sharpe']:+.2f} → WF Sharpe {w['sharpe']:+.2f}  "
            f"（Δ {w['sharpe'] - b['sharpe']:+.2f}）  retrain {wf_results[name]['n_retrains']} 次"
        )
    print(f"\n报告: {MD_PATH}")
    print(f"图表: {PNG_PATH}")


if __name__ == "__main__":
    main()
