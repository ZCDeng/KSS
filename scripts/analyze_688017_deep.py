#!/usr/bin/env python3
"""688017 深度因子分析 —— 时序 IC + 分位收益 + 信号回测 + Markdown 报告.

用法::

    python3 scripts/analyze_688017_deep.py

输出：

- ``storage/reports/688017_deep_report.md`` —— 因子排名 + 信号策略对比
- ``storage/reports/688017_deep_panels.png`` —— 8 面板可视化
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

# 项目根目录优先
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from kss.backtest.metrics import Metrics  # noqa: E402
from kss.backtest.single_stock import SingleStockAnalyzer  # noqa: E402
from kss.features.pipeline import FactorPipeline  # noqa: E402


# ---------------------------------------------------------------------- #
# 常量
# ---------------------------------------------------------------------- #

DATA_PATH = PROJECT_ROOT / "cs_data_688017.csv"
SYMBOL = "688017.SH"
ROLLING_WINDOW = 60
HORIZONS = ["next_day_return", "future_return_5d", "future_return_10d", "future_return_20d"]
REPORT_DIR = PROJECT_ROOT / "storage" / "reports"
MD_PATH = REPORT_DIR / "688017_deep_report.md"
PNG_PATH = REPORT_DIR / "688017_deep_panels.png"

plt.rcParams["font.sans-serif"] = ["Arial Unicode MS", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def load_factor_panel() -> tuple[pd.DataFrame, list[str]]:
    """加载行情 → 生成因子 → 添加目标变量."""
    df = pd.read_csv(DATA_PATH)
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df = df.sort_values("trade_date").reset_index(drop=True)

    fp = FactorPipeline(df)
    factor_df = fp.generate()
    factor_df = fp.add_targets(factor_df, df["close"])
    feature_cols = fp.get_feature_cols(factor_df)

    # 把行情列拼回去，便于绘图
    factor_df["open"] = df["open"]
    factor_df["high"] = df["high"]
    factor_df["low"] = df["low"]
    factor_df["pct_chg"] = df.get("pct_chg")

    logger.info(
        "数据: %s 行 %s → %s, 因子 %d 个",
        len(factor_df),
        factor_df["trade_date"].min().date(),
        factor_df["trade_date"].max().date(),
        len(feature_cols),
    )
    return factor_df, feature_cols


def buy_hold_metrics(factor_df: pd.DataFrame) -> dict:
    """Buy-and-Hold 基准（next_day_return 全持仓）."""
    return Metrics.calc(factor_df["next_day_return"].dropna())


def top_factors_by_ic(ic_table: pd.DataFrame, horizon: str, k: int = 10) -> pd.DataFrame:
    """按 |IC| 排前 k."""
    sub = ic_table[ic_table["horizon"] == horizon].copy()
    sub["abs_ic"] = sub["ic"].abs()
    return sub.sort_values("abs_ic", ascending=False).head(k).reset_index(drop=True)


def plot_panels(
    factor_df: pd.DataFrame,
    ic_5d: pd.DataFrame,
    scan_top: pd.DataFrame,
    quant_dfs: dict[str, pd.DataFrame],
    bt_results: dict[str, dict],
) -> None:
    """8 panel：价格 / IC 排名 / 因子信号收益曲线 / 分位累计收益 / 关键因子 z-score."""
    fig, axes = plt.subplots(4, 2, figsize=(16, 22))

    # Panel 1: 收盘价 + 60 日 MA
    ax = axes[0, 0]
    ax.plot(factor_df["trade_date"], factor_df["close"], label="Close", lw=1.5)
    ma60 = factor_df["close"].rolling(60).mean()
    ax.plot(factor_df["trade_date"], ma60, label="MA60", lw=1, alpha=0.7)
    ax.set_title(f"{SYMBOL} 收盘价")
    ax.legend()
    ax.grid(alpha=0.3)

    # Panel 2: Top 因子 |IC|（5d）
    ax = axes[0, 1]
    top = ic_5d.head(15)
    colors = ["C2" if x > 0 else "C3" for x in top["ic"]]
    ax.barh(range(len(top)), top["abs_ic"], color=colors, alpha=0.7)
    ax.set_yticks(range(len(top)))
    ax.set_yticklabels(top["factor"], fontsize=8)
    ax.invert_yaxis()
    ax.set_title("Top 15 因子 |IC| (future_return_5d) | 绿=正向 红=反向")
    ax.set_xlabel("|Spearman IC|")
    ax.grid(alpha=0.3, axis="x")

    # Panel 3: Top 5 信号策略累计净值
    ax = axes[1, 0]
    base_dates = factor_df["trade_date"]
    bh_cum = (1 + factor_df["next_day_return"].fillna(0)).cumprod() - 1
    ax.plot(base_dates, bh_cum, label="Buy & Hold", lw=2, color="gray", alpha=0.7)
    top5 = scan_top.head(5)
    for _, row in top5.iterrows():
        f = row["factor"]
        if f not in bt_results:
            continue
        net = bt_results[f]["net_return"]
        cum = (1 + net.fillna(0)).cumprod() - 1
        ax.plot(base_dates, cum, label=f"{f} (Sh={row['sharpe']:.2f})", alpha=0.8)
    ax.set_title("Top 5 单因子信号策略 vs Buy&Hold")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)

    # Panel 4: 信号策略 Sharpe 分布（所有因子）
    ax = axes[1, 1]
    sh_vals = scan_top["sharpe"].dropna()
    ax.hist(sh_vals, bins=20, alpha=0.7, color="C0")
    ax.axvline(x=0, color="red", ls="--", alpha=0.5)
    ax.axvline(x=sh_vals.mean(), color="green", ls="--",
               label=f"均值 {sh_vals.mean():.2f}")
    ax.set_title(f"全部 {len(sh_vals)} 因子信号 Sharpe 分布")
    ax.set_xlabel("Sharpe")
    ax.legend()
    ax.grid(alpha=0.3)

    # Panel 5-6: Top 2 因子的分位收益柱状
    for i, (factor, q_df) in enumerate(list(quant_dfs.items())[:2]):
        ax = axes[2, i]
        if q_df.empty:
            ax.set_visible(False)
            continue
        x = range(len(q_df))
        ax.bar(x, q_df["mean_return"] * 100, color="C0", alpha=0.7)
        ax.axhline(y=0, color="black", lw=0.5)
        for j, v in enumerate(q_df["mean_return"] * 100):
            ax.text(j, v, f"{v:.2f}%", ha="center", va="bottom" if v >= 0 else "top",
                    fontsize=8)
        ax.set_xticks(x)
        ax.set_xticklabels(q_df["bucket"])
        ax.set_title(f"{factor} 分位日均收益 (%)")
        ax.set_ylabel("Daily Return %")
        ax.grid(alpha=0.3, axis="y")

    # Panel 7: Top 因子 rolling z-score（带仓位阴影）
    ax = axes[3, 0]
    if not scan_top.empty:
        best = scan_top.iloc[0]["factor"]
        if best in bt_results:
            z = bt_results[best]["signal_z"]
            pos = bt_results[best]["positions"]
            ax.plot(factor_df["trade_date"], z, label=f"{best} z-score", lw=1)
            ax.axhline(y=1, color="green", ls="--", alpha=0.5, label="+1σ")
            ax.axhline(y=-1, color="red", ls="--", alpha=0.5, label="-1σ")
            ax.axhline(y=0, color="black", lw=0.3)
            ax.fill_between(
                factor_df["trade_date"], 0, pos * z.max(),
                where=pos > 0, alpha=0.1, color="green", label="持仓"
            )
            ax.set_title(f"最优因子 {best} 滚动 z-score 与仓位")
            ax.legend(fontsize=8)
            ax.grid(alpha=0.3)

    # Panel 8: IC 衰减（每个 horizon 的平均 |IC|）
    ax = axes[3, 1]
    horizon_means = {}
    # 取出 horizon 列各自 top 15 的 |IC| 均值
    for h in HORIZONS:
        sub = ic_5d.copy()  # 已是 5d
        # 我们没有把全部 horizon 都传进来，所以这里要算
    # 改成画 top 5 因子在各 horizon 的 |IC|
    ax = axes[3, 1]
    top_factors = scan_top.head(5)["factor"].tolist()
    if top_factors:
        full_ic = bt_results.get("_full_ic_table")
        if isinstance(full_ic, pd.DataFrame):
            xticks = HORIZONS
            for f in top_factors:
                sub = full_ic[full_ic["factor"] == f].set_index("horizon").reindex(xticks)
                ax.plot(xticks, sub["ic"].abs(), marker="o", label=f, alpha=0.8)
            ax.set_title("Top 5 因子在各 horizon 的 |IC|（衰减）")
            ax.set_xlabel("Horizon")
            ax.set_ylabel("|Spearman IC|")
            ax.legend(fontsize=8)
            ax.grid(alpha=0.3)
            plt.setp(ax.get_xticklabels(), rotation=15, ha="right")

    plt.tight_layout()
    PNG_PATH.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(PNG_PATH, dpi=150, bbox_inches="tight")
    plt.close()
    logger.info("PNG: %s", PNG_PATH)


def render_markdown(
    factor_df: pd.DataFrame,
    ic_table: pd.DataFrame,
    scan_table: pd.DataFrame,
    quant_dfs: dict[str, pd.DataFrame],
    bt_results: dict[str, dict],
    bh: dict,
) -> None:
    """生成 Markdown 报告."""
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []

    lines.append(f"# 688017 深度因子分析报告")
    lines.append("")
    lines.append(
        f"- **股票**: {SYMBOL}  "
        f"  **样本**: {len(factor_df)} 个交易日  "
        f"  **区间**: {factor_df['trade_date'].min().date()} ~ {factor_df['trade_date'].max().date()}"
    )
    lines.append(
        f"- **基准 (Buy & Hold)**: 总收益 {bh.get('total', 0)*100:.1f}%  "
        f"年化 {bh.get('annual', 0)*100:.1f}%  "
        f"夏普 {bh.get('sharpe', 0):.2f}  "
        f"最大回撤 {bh.get('max_dd', 0)*100:.1f}%"
    )
    lines.append("")

    # ============== 1. 时序 IC 排名 ============== #
    lines.append("## 1. 因子时序 IC 排名")
    lines.append("")
    lines.append(
        "**时序 IC** 衡量「因子值随时间变化」与「未来收益随时间变化」的 Spearman 秩相关。"
        "正值表示因子越高未来收益越大；负值反向；|IC| 越大越有效。"
    )
    lines.append("")
    for horizon in HORIZONS:
        top = top_factors_by_ic(ic_table, horizon, k=10)
        if top.empty:
            continue
        lines.append(f"### 1.{HORIZONS.index(horizon) + 1} Horizon = `{horizon}`")
        lines.append("")
        lines.append("| 排名 | 因子 | IC | t-stat | p-value | n |")
        lines.append("|------|------|----|--------|---------|---|")
        for i, row in top.iterrows():
            sign = "🟢" if row["ic"] > 0 else "🔴"
            lines.append(
                f"| {i + 1} | `{row['factor']}` {sign} | "
                f"{row['ic']:.4f} | {row['ic_t_stat']:.2f} | "
                f"{row['ic_p_value']:.4f} | {int(row['n'])} |"
            )
        lines.append("")

    # ============== 2. 信号策略排名 ============== #
    lines.append("## 2. 单因子信号策略排名")
    lines.append("")
    lines.append(
        f"**策略规则**：因子滚动 ({ROLLING_WINDOW} 日) z-score > +1 → 持仓；"
        f"z-score < -1 → 空仓；中间区域沿用上日仓位。"
    )
    lines.append(
        "**成本**：买入 0.10% / 卖出 0.20%。"
        "**执行**：T 日 close 后产生信号，T+1 开盘建仓，T+2 开盘换仓（next_day_return）。"
    )
    lines.append("")
    top_scan = scan_table.head(15)
    lines.append("| 排名 | 因子 | 年化 | Sharpe | 最大回撤 | 胜率 | 交易次数 | 总收益 |")
    lines.append("|------|------|------|--------|----------|------|----------|--------|")
    for i, row in top_scan.iterrows():
        lines.append(
            f"| {i + 1} | `{row['factor']}` | "
            f"{row['annual']*100:.1f}% | {row['sharpe']:.2f} | "
            f"{row['max_dd']*100:.1f}% | {row['win_rate']*100:.1f}% | "
            f"{int(row['n_trades'])} | {row['total']*100:.1f}% |"
        )
    lines.append("")
    lines.append(
        f"> 基准 Buy & Hold: 年化 **{bh.get('annual', 0)*100:.1f}%** / "
        f"Sharpe **{bh.get('sharpe', 0):.2f}** / 最大回撤 **{bh.get('max_dd', 0)*100:.1f}%**"
    )
    lines.append("")

    # ============== 3. 信号策略合格统计 ============== #
    valid = scan_table.dropna(subset=["sharpe"])
    n_pos_sharpe = (valid["sharpe"] > 0).sum()
    n_beat_bh = (valid["sharpe"] > bh.get("sharpe", 0)).sum()
    avg_sharpe = valid["sharpe"].mean()
    lines.append(f"- 全部 {len(valid)} 个因子中：")
    lines.append(f"  - Sharpe > 0：**{n_pos_sharpe}** 个 ({n_pos_sharpe/len(valid)*100:.0f}%)")
    lines.append(f"  - 超越 Buy & Hold Sharpe：**{n_beat_bh}** 个 ({n_beat_bh/len(valid)*100:.0f}%)")
    lines.append(f"  - 平均 Sharpe：**{avg_sharpe:.2f}**")
    lines.append("")

    # ============== 4. 分位数收益（Top 2） ============== #
    lines.append("## 3. 分位数桶日均收益（Top 2 因子）")
    lines.append("")
    lines.append(
        "把因子值按历史 expanding 分位切 5 桶（防 look-ahead），统计每桶下一日实盘收益。"
        "若 Q5 显著高于 Q1，说明因子方向稳定（多空价差正）。"
    )
    lines.append("")
    for factor, q_df in quant_dfs.items():
        if q_df.empty:
            continue
        lines.append(f"### `{factor}`")
        lines.append("")
        lines.append("| 桶 | 样本数 | 日均收益 | 累计收益 | Sharpe | 胜率 |")
        lines.append("|----|--------|----------|----------|--------|------|")
        for _, r in q_df.iterrows():
            lines.append(
                f"| {r['bucket']} | {r['n_days']} | "
                f"{r['mean_return']*100:+.3f}% | {r['total_return']*100:+.1f}% | "
                f"{r['sharpe']:.2f} | {r['win_rate']*100:.1f}% |"
            )
        # 多空价差
        if len(q_df) >= 2:
            spread = q_df.iloc[-1]["mean_return"] - q_df.iloc[0]["mean_return"]
            lines.append("")
            lines.append(f"**多空价差 (Q{len(q_df)} - Q1)**: {spread*100:+.3f}% 日均")
        lines.append("")

    # ============== 5. 综合结论 ============== #
    lines.append("## 4. 综合结论")
    lines.append("")
    if not scan_table.empty:
        top1 = scan_table.iloc[0]
        lines.append(
            f"- **最优单因子信号**: `{top1['factor']}` —— "
            f"年化 {top1['annual']*100:.1f}% / Sharpe {top1['sharpe']:.2f} / "
            f"{int(top1['n_trades'])} 次交易 / 最大回撤 {top1['max_dd']*100:.1f}%"
        )
    # 高 IC 因子
    sig_ic = ic_table[
        (ic_table["horizon"] == "future_return_5d")
        & (ic_table["ic_p_value"] < 0.05)
        & (ic_table["n"] >= 200)
    ].copy()
    if not sig_ic.empty:
        sig_ic["abs_ic"] = sig_ic["ic"].abs()
        sig_ic = sig_ic.sort_values("abs_ic", ascending=False).head(5)
        lines.append("- **5 日 horizon 显著（p < 0.05）IC 最强的因子**：")
        for _, r in sig_ic.iterrows():
            direction = "正向（高分预测涨）" if r["ic"] > 0 else "反向（高分预测跌）"
            lines.append(
                f"  - `{r['factor']}`: IC = {r['ic']:+.4f} ({direction})，p = {r['ic_p_value']:.4f}"
            )
    lines.append("")
    lines.append("**注意事项**:")
    lines.append("1. 单股票样本量小，单因子 IC 与策略 Sharpe 都存在 selection bias，需结合 Deflated Sharpe 修正。")
    lines.append("2. ``z-score > 1`` 阈值未经过参数搜索；不同因子最优阈值不同。")
    lines.append("3. Top 3 因子组合（多因子加权）通常优于单因子，可通过 ``MultiPeriodEnsemble`` 或 LightGBM 训练得到。")
    lines.append("")
    lines.append("---")
    lines.append("_Generated by `scripts/analyze_688017_deep.py`_")

    MD_PATH.write_text("\n".join(lines), encoding="utf-8")
    logger.info("Markdown: %s", MD_PATH)


def main() -> None:
    factor_df, feature_cols = load_factor_panel()
    bh = buy_hold_metrics(factor_df)
    logger.info(
        "Buy & Hold: 总收益 %.1f%%, 年化 %.1f%%, Sharpe %.2f, 最大回撤 %.1f%%",
        bh.get("total", 0) * 100, bh.get("annual", 0) * 100,
        bh.get("sharpe", 0), bh.get("max_dd", 0) * 100,
    )

    analyzer = SingleStockAnalyzer(factor_df, ret_col="next_day_return")

    # 时序 IC 表（全 horizon）
    ic_table = analyzer.time_series_ic(
        feature_cols, horizons=HORIZONS, method="spearman",
    )
    logger.info("时序 IC 表完成: %d 行", len(ic_table))

    # 信号策略批量扫描（基于 next_day_return）
    scan_table = analyzer.scan_factors(
        feature_cols, upper=1.0, lower=-1.0,
        rolling_window=ROLLING_WINDOW,
        buy_cost=0.001, sell_cost=0.002,
    )
    logger.info("信号扫描完成: %d 个因子", len(scan_table))

    # 单因子详细回测（Top 5，用于绘图）
    bt_results: dict[str, dict] = {}
    for _, row in scan_table.head(5).iterrows():
        f = row["factor"]
        bt_results[f] = analyzer.signal_backtest(
            f, upper=1.0, lower=-1.0,
            rolling_window=ROLLING_WINDOW,
        )
    bt_results["_full_ic_table"] = ic_table

    # Top 2 因子的分位收益
    quant_dfs: dict[str, pd.DataFrame] = {}
    for f in scan_table.head(2)["factor"]:
        quant_dfs[f] = analyzer.quantile_returns(f, n_quantiles=5)

    # 输出
    ic_5d = top_factors_by_ic(ic_table, "future_return_5d", k=15)
    plot_panels(factor_df, ic_5d, scan_table, quant_dfs, bt_results)
    render_markdown(factor_df, ic_table, scan_table, quant_dfs, bt_results, bh)

    # 终端摘要
    print("\n" + "=" * 60)
    print(f"688017 深度因子分析完成")
    print("=" * 60)
    print(f"Buy & Hold:  年化 {bh['annual']*100:.1f}%  Sharpe {bh['sharpe']:.2f}")
    print(f"\n【信号策略 Top 5】")
    for _, r in scan_table.head(5).iterrows():
        print(
            f"  {r['factor']:20s}  年化 {r['annual']*100:+6.1f}%  "
            f"Sharpe {r['sharpe']:+.2f}  胜率 {r['win_rate']*100:.1f}%  "
            f"交易 {int(r['n_trades']):3d} 次"
        )
    print(f"\n【时序 IC Top 5 (5d horizon)】")
    for i, r in top_factors_by_ic(ic_table, "future_return_5d", k=5).iterrows():
        direction = "↑" if r["ic"] > 0 else "↓"
        print(
            f"  {r['factor']:20s}  IC = {r['ic']:+.4f} {direction}  "
            f"p = {r['ic_p_value']:.4f}"
        )
    print(f"\n报告: {MD_PATH}")
    print(f"图表: {PNG_PATH}")


if __name__ == "__main__":
    main()
