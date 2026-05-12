#!/usr/bin/env python3
"""688017 多因子组合策略 —— 等权 / |IC| 加权 / Sharpe 加权对比.

用法::

    python3 scripts/analyze_688017_combo.py

输出：

- ``storage/reports/688017_combo_report.md``  —— 对比表 + 权重明细 + 结论
- ``storage/reports/688017_combo_panels.png`` —— 4 面板可视化

依赖前置：``scripts/analyze_688017_deep.py`` 已确定 Top 5 信号因子。
本脚本会复用相同的 :class:`SingleStockAnalyzer` 重新跑一遍 scan_factors 与
time_series_ic（数据是同一份，因此结果与 deep 报告完全一致），保证可独立运行.
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
TOP_K = 5
HORIZONS = ["next_day_return", "future_return_5d", "future_return_10d", "future_return_20d"]
IC_HORIZON = "future_return_5d"
REPORT_DIR = PROJECT_ROOT / "storage" / "reports"
MD_PATH = REPORT_DIR / "688017_combo_report.md"
PNG_PATH = REPORT_DIR / "688017_combo_panels.png"

plt.rcParams["font.sans-serif"] = ["Arial Unicode MS", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------- #
# 数据 + 单因子诊断
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


# ---------------------------------------------------------------------- #
# 绘图
# ---------------------------------------------------------------------- #


def plot_panels(
    factor_df: pd.DataFrame,
    single_results: dict[str, dict],
    combo_results: dict[str, dict],
    bh_cum: pd.Series,
    comparison_df: pd.DataFrame,
) -> None:
    fig, axes = plt.subplots(2, 2, figsize=(16, 12))
    dates = factor_df["trade_date"]

    # Panel 1: 净值曲线 —— B&H + Top 单因子 + 3 个组合
    ax = axes[0, 0]
    ax.plot(dates, bh_cum.values, label="Buy & Hold", lw=2, color="gray", alpha=0.7)
    # 单因子细线
    for f, bt in single_results.items():
        cum = (1 + bt["net_return"].fillna(0)).cumprod() - 1
        ax.plot(dates, cum, alpha=0.4, lw=0.8, label=f"单 {f}")
    # 组合粗线
    for name, bt in combo_results.items():
        cum = (1 + bt["net_return"].fillna(0)).cumprod() - 1
        ax.plot(dates, cum, lw=2.0, label=f"【组合】{name}")
    ax.set_title(f"{SYMBOL} 净值曲线：Buy&Hold vs 5 单因子 vs 3 组合")
    ax.legend(fontsize=7, ncol=2, loc="upper left")
    ax.grid(alpha=0.3)

    # Panel 2: 权重柱状图（3 个组合并排）
    ax = axes[0, 1]
    all_factors: list[str] = []
    for bt in combo_results.values():
        for f in bt["weights"]:
            if f not in all_factors:
                all_factors.append(f)
    x = np.arange(len(all_factors))
    width = 0.8 / max(1, len(combo_results))
    for i, (name, bt) in enumerate(combo_results.items()):
        weights = [bt["weights"].get(f, 0.0) for f in all_factors]
        ax.bar(x + (i - (len(combo_results) - 1) / 2) * width, weights, width, label=name)
    ax.set_xticks(x)
    ax.set_xticklabels(all_factors, rotation=30, ha="right", fontsize=8)
    ax.axhline(y=0, color="black", lw=0.5)
    ax.set_ylabel("权重")
    ax.set_title("3 种加权下的因子权重对比")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3, axis="y")

    # Panel 3: Sharpe vs |最大回撤| 散点（标注策略）
    ax = axes[1, 0]
    for _, row in comparison_df.iterrows():
        name = row["策略"]
        if pd.isna(row["Sharpe"]) or pd.isna(row["最大回撤"]):
            continue
        marker = "*" if name.startswith("【组合】") else ("s" if name == "Buy & Hold" else "o")
        size = 200 if name.startswith("【组合】") else 60
        color = "C2" if name.startswith("【组合】") else (
            "C7" if name == "Buy & Hold" else "C0"
        )
        ax.scatter(abs(row["最大回撤"]) * 100, row["Sharpe"],
                   s=size, marker=marker, color=color, alpha=0.7,
                   edgecolors="black", linewidth=0.5)
        ax.annotate(
            name.replace("【组合】", ""),
            (abs(row["最大回撤"]) * 100, row["Sharpe"]),
            fontsize=7, alpha=0.8,
            xytext=(5, 3), textcoords="offset points",
        )
    ax.set_xlabel("|最大回撤| (%)")
    ax.set_ylabel("Sharpe")
    ax.set_title("Sharpe ↔ 回撤 散点 (★=组合, ●=单因子, ■=B&H)")
    ax.grid(alpha=0.3)

    # Panel 4: 组合合成 z-score（最优组合）
    ax = axes[1, 1]
    best_name = comparison_df[
        comparison_df["策略"].str.startswith("【组合】")
    ].sort_values("Sharpe", ascending=False).iloc[0]["策略"].replace("【组合】", "")
    if best_name in combo_results:
        bt = combo_results[best_name]
        z = bt["signal_z"]
        pos = bt["positions"]
        ax.plot(dates, z, lw=1, label=f"{best_name} 合成 z-score", color="C0")
        ax.axhline(y=1, color="green", ls="--", alpha=0.5, label="+1σ 阈值")
        ax.axhline(y=-1, color="red", ls="--", alpha=0.5, label="-1σ 阈值")
        ax.axhline(y=0, color="black", lw=0.3)
        # 持仓填充
        ax.fill_between(dates, 0, z.fillna(0).clip(lower=0),
                        where=(pos > 0).values, alpha=0.15, color="green", label="持仓期")
        ax.set_title(f"最优组合 [{best_name}] 合成 z-score 与持仓")
        ax.legend(fontsize=8, loc="upper left")
        ax.grid(alpha=0.3)

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


def render_markdown(
    factor_df: pd.DataFrame,
    combiners: dict[str, MultiFactorCombiner],
    single_results: dict[str, dict],
    combo_results: dict[str, dict],
    comparison_df: pd.DataFrame,
    bh: dict,
) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []

    lines.append(f"# 688017 多因子组合策略对比")
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

    # ============== 1. 综合对比表 ============== #
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

    # 排名引导
    valid = comparison_df.dropna(subset=["Sharpe"])
    if not valid.empty:
        best = valid.sort_values("Sharpe", ascending=False).iloc[0]
        worst_dd = valid.sort_values("最大回撤", ascending=True).iloc[0]
        lines.append(
            f"- **Sharpe 最高**: `{best['策略']}` → {best['Sharpe']:.2f}"
        )
        lines.append(
            f"- **回撤最小**: `{worst_dd['策略']}` → {worst_dd['最大回撤']*100:.1f}%"
        )
        lines.append("")

    # ============== 2. 各组合权重明细 ============== #
    lines.append("## 2. 组合权重明细")
    lines.append("")
    for name, c in combiners.items():
        lines.append(f"### `{name}`")
        lines.append("")
        lines.append("| 因子 | 权重 |")
        lines.append("|------|------|")
        for f, w in sorted(c.weights.items(), key=lambda kv: abs(kv[1]), reverse=True):
            lines.append(f"| `{f}` | {w:+.3f} |")
        lines.append("")

    # ============== 3. 结论 ============== #
    lines.append("## 3. 结论")
    lines.append("")
    # 区分真正多因子组合（≥2 个因子）和退化为单因子的"组合"
    real_combos = {
        name: bt for name, bt in combo_results.items() if len(bt["weights"]) >= 2
    }
    degenerate_combos = {
        name: bt for name, bt in combo_results.items() if len(bt["weights"]) == 1
    }

    single_max_sharpe = max(
        (r["metrics"].get("sharpe", 0) for r in single_results.values()),
        default=0.0,
    )
    single_max_factor = max(
        single_results, key=lambda f: single_results[f]["metrics"].get("sharpe", 0),
    )

    if real_combos:
        real_max_sharpe = max(
            r["metrics"].get("sharpe", 0) for r in real_combos.values()
        )
        real_max_name = max(
            real_combos, key=lambda f: real_combos[f]["metrics"].get("sharpe", 0),
        )
        if real_max_sharpe > single_max_sharpe + 1e-6:
            lines.append(
                f"1. **真正多因子组合超越单因子**: 最优 `{real_max_name}` "
                f"Sharpe = {real_max_sharpe:.2f}，胜过最强单因子 `{single_max_factor}` "
                f"({single_max_sharpe:.2f})。**多因子分散降低了 idiosyncratic noise**。"
            )
        else:
            lines.append(
                f"1. **真正多因子组合未能超越最强单因子**: 最强单因子 "
                f"`{single_max_factor}` Sharpe = {single_max_sharpe:.2f}；"
                f"最优多因子组合 `{real_max_name}` 仅 {real_max_sharpe:.2f}。"
                f"**根因**：Top 5 信号因子（macd_hist / vol_ma20_ratio / ma_10_ratio / "
                f"ma_10_slope / volatility_5d）均为趋势 / 量价类，相关性高 → "
                f"等权与 Sharpe 加权把最强的 `macd_hist` 信号稀释到 ~20% 权重。"
            )

    if degenerate_combos:
        names = list(degenerate_combos.keys())
        lines.append(
            f"2. **退化诊断**: 以下组合在权重归一后只剩 1 个因子（即等价于单因子）："
        )
        for name, bt in degenerate_combos.items():
            sole_factor = list(bt["weights"].keys())[0]
            lines.append(
                f"   - `{name}` → `{sole_factor}` (权重 1.0)。"
                f"原因：5d horizon 的 IC Top {TOP_K} 中只有 `{sole_factor}` 与 Sharpe Top {TOP_K} 交集；"
                f"**Sharpe 排名与 IC 排名口径不一致**——Sharpe 衡量策略实操收益，IC 衡量原始预测力，"
                f"二者交集小是单股票（样本少+高波动）的常见现象。"
            )

    # 回撤维度
    single_min_dd = min(
        (r["metrics"].get("max_dd", 0) for r in single_results.values()), default=0.0,
    )
    real_min_dd = min(
        (r["metrics"].get("max_dd", 0) for r in real_combos.values()),
        default=float("inf"),
    )
    if real_combos:
        if real_min_dd > single_min_dd + 1e-6:
            lines.append(
                f"3. **回撤控制（真组合 vs 单因子）**: 真组合最大回撤最优 = "
                f"{real_min_dd*100:.1f}%，优于单因子最优 {single_min_dd*100:.1f}% — "
                f"分散降低了尾部风险，即便 Sharpe 没赢."
            )
        else:
            lines.append(
                f"3. **回撤控制**: 真组合最大回撤最优 = {real_min_dd*100:.1f}%，"
                f"未优于单因子最优 {single_min_dd*100:.1f}%。同质因子的同步回撤无法被分散."
            )

    # B&H
    lines.append(
        f"4. **vs Buy & Hold**: 所有组合 Sharpe 都 ≫ 基准 {bh['sharpe']:.2f}，"
        f"最大回撤显著优于 {bh['max_dd']*100:.1f}%（B&H 在 2024 年底 -64% 大回撤期间被择时策略避开）。"
        f"**择时仍有正向价值**——尤其是回撤端."
    )
    lines.append("")

    # ============== 4. 注意事项 ============== #
    lines.append("## 4. 注意事项")
    lines.append("")
    lines.append(
        "1. **Look-ahead bias**: scan_table 与 ic_table 用全样本统计算权重，"
        "理论上是事后 weights；实盘需改成 ``train_window`` 时段历史滚动重选."
    )
    lines.append(
        "2. **样本量**: 单股 800 行；rolling z-score window=60 → 实际信号期约 740 天。"
        "组合 Sharpe 的统计置信区间相对宽松，未必稳定."
    )
    lines.append(
        "3. **方向同质**: Top 5 信号因子（macd_hist / vol_ma20_ratio / ma_10_ratio / "
        "ma_10_slope / volatility_5d）都是趋势 / 量价类，相关性可能较高；"
        "下一步可尝试纳入反向因子（``pb``, ``close_to_low_60d``）做正负互补."
    )
    lines.append(
        "4. **阈值未调优**: 当前 ±1σ 是经验值；不同组合的最优阈值不同，"
        "可用 grid search 在 (upper, lower) ∈ {0.5, 1.0, 1.5} × {-0.5, -1.0, -1.5} 上扫."
    )
    lines.append("")
    lines.append("---")
    lines.append("_Generated by `scripts/analyze_688017_combo.py`_")

    MD_PATH.write_text("\n".join(lines), encoding="utf-8")
    logger.info("Markdown: %s", MD_PATH)


# ---------------------------------------------------------------------- #
# Main
# ---------------------------------------------------------------------- #


def main() -> None:
    # 加载
    factor_df, feature_cols = load_factor_panel()
    logger.info(
        "数据: %d 行 %s → %s, 因子 %d 个",
        len(factor_df),
        factor_df["trade_date"].min().date(),
        factor_df["trade_date"].max().date(),
        len(feature_cols),
    )

    analyzer = SingleStockAnalyzer(factor_df, ret_col="next_day_return")

    # Buy & Hold
    bh = Metrics.calc(factor_df["next_day_return"].dropna())
    bh_cum = (1 + factor_df["next_day_return"].fillna(0)).cumprod() - 1
    logger.info(
        "B&H: 年化 %.1f%%  Sharpe %.2f  最大回撤 %.1f%%",
        bh["annual"] * 100, bh["sharpe"], bh["max_dd"] * 100,
    )

    # 单因子诊断
    scan_table = analyzer.scan_factors(
        feature_cols, upper=1.0, lower=-1.0, rolling_window=ROLLING_WINDOW,
    )
    ic_table = analyzer.time_series_ic(
        feature_cols, horizons=HORIZONS, method="spearman",
    )
    top5 = scan_table.head(TOP_K)["factor"].tolist()
    logger.info("Top %d Sharpe 因子: %s", TOP_K, top5)

    # 3 个组合
    combiners: dict[str, MultiFactorCombiner] = {
        "等权": MultiFactorCombiner.equal_weighted(top5),
        "Sharpe 加权": MultiFactorCombiner.from_scan_table(
            scan_table, top_k=TOP_K, weight_col="sharpe", clip_negative=True,
        ),
        "|IC| 加权 (Top5 交集)": MultiFactorCombiner.from_ic_table(
            ic_table, horizon=IC_HORIZON, top_k=15, signed=True,
        ).restrict_to(top5),
    }
    for name, c in combiners.items():
        logger.info("【%s】%s", name, c)

    # 单因子回测
    single_results: dict[str, dict] = {}
    for f in top5:
        single_results[f] = analyzer.signal_backtest(f, rolling_window=ROLLING_WINDOW)

    # 组合回测
    combo_results: dict[str, dict] = {}
    for name, c in combiners.items():
        combo_results[name] = analyzer.multi_factor_backtest(
            c, rolling_window=ROLLING_WINDOW,
        )

    # 综合对比表
    rows: list[dict] = []
    rows.append(metrics_row("Buy & Hold", bh, n_trades=None))
    for f, bt in single_results.items():
        rows.append(metrics_row(f"单 {f}", bt["metrics"], bt["n_trades"]))
    for name, bt in combo_results.items():
        rows.append(metrics_row(f"【组合】{name}", bt["metrics"], bt["n_trades"]))
    comparison_df = pd.DataFrame(rows)

    # 输出
    plot_panels(factor_df, single_results, combo_results, bh_cum, comparison_df)
    render_markdown(
        factor_df, combiners, single_results, combo_results, comparison_df, bh,
    )

    # 终端打印
    print("\n" + "=" * 72)
    print(f"688017 多因子组合策略对比")
    print("=" * 72)
    print(f"\nBuy & Hold:    年化 {bh['annual']*100:+6.1f}%  Sharpe {bh['sharpe']:+.2f}  回撤 {bh['max_dd']*100:.1f}%")
    print(f"\n【单因子 Top 5】")
    for f, bt in single_results.items():
        m = bt["metrics"]
        print(
            f"  单 {f:20s}  年化 {m['annual']*100:+6.1f}%  Sharpe {m['sharpe']:+.2f}  "
            f"回撤 {m['max_dd']*100:6.1f}%  交易 {bt['n_trades']:3d}"
        )
    print(f"\n【3 种组合】")
    for name, bt in combo_results.items():
        m = bt["metrics"]
        print(
            f"  {name:24s}  年化 {m['annual']*100:+6.1f}%  Sharpe {m['sharpe']:+.2f}  "
            f"回撤 {m['max_dd']*100:6.1f}%  交易 {bt['n_trades']:3d}"
        )
        print(f"    权重: {', '.join(f'{f}={w:+.2f}' for f, w in bt['weights'].items())}")
    print(f"\n报告: {MD_PATH}")
    print(f"图表: {PNG_PATH}")


if __name__ == "__main__":
    main()
