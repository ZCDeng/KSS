#!/usr/bin/env python3
"""macd_hist 横截面跨股票验证 —— 在 51 只科创板上做 Top-Pct 选股回测.

接续 v3 结论："Top K 因子高度不稳定" + "下一步应做横截面池化".
本脚本验证：``macd_hist`` 在 688017 上 Sharpe 1.18，是否真的是该因子的内在 alpha，
还是只是这一只股票的过拟合？

实验设计：

- **股票池**：所有 ``cs_data_688*.csv`` 文件（51 只科创板）
- **因子**：``macd_hist`` 单因子，截面 rank，选 Top 20% 等权
- **执行**：T 日 close 后产生 rank，T+1 开盘建仓，T+2 开盘换仓
- **对比基准**：科创50 指数（``cs_index_000688_SH.csv``）
- **诊断**：截面 IC / IR / 分位 spread / 多空价差 / Sharpe 显著性

输出::

    storage/reports/macd_cross_section_report.md
    storage/reports/macd_cross_section_panels.png
"""

from __future__ import annotations

import glob
import logging
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from kss.backtest.benchmark import Benchmark  # noqa: E402
from kss.backtest.cost_model import CostModel  # noqa: E402
from kss.backtest.cross_section import factor_cross_section_backtest  # noqa: E402
from kss.backtest.diagnostics import SignalDiagnostics  # noqa: E402
from kss.backtest.metrics import Metrics  # noqa: E402
from kss.backtest.significance import Significance  # noqa: E402
from kss.features.pipeline import FactorPipeline  # noqa: E402

# ---------------------------------------------------------------------- #
# 常量
# ---------------------------------------------------------------------- #

DATA_GLOB = str(PROJECT_ROOT / "cs_data_688*.csv")
BENCHMARK_PATH = PROJECT_ROOT / "cs_index_000688_SH.csv"
FACTOR_COL = "macd_hist"
TOP_PCT = 0.2
REPORT_DIR = PROJECT_ROOT / "storage" / "reports"
MD_PATH = REPORT_DIR / "macd_cross_section_report.md"
PNG_PATH = REPORT_DIR / "macd_cross_section_panels.png"

plt.rcParams["font.sans-serif"] = ["Arial Unicode MS", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------- #
# 数据加载
# ---------------------------------------------------------------------- #


def load_universe_panel() -> tuple[pd.DataFrame, list[str]]:
    """加载所有科创板股票 → 拼成 panel."""
    files = sorted(glob.glob(DATA_GLOB))
    if not files:
        raise FileNotFoundError(f"未找到任何文件匹配 {DATA_GLOB}")
    logger.info("加载 %d 只股票", len(files))

    all_factors: list[pd.DataFrame] = []
    feature_cols: list[str] = []

    for fp_path in files:
        try:
            df = pd.read_csv(fp_path)
            df["trade_date"] = pd.to_datetime(df["trade_date"])
            df = df.sort_values("trade_date").reset_index(drop=True)
            if len(df) < 100:
                logger.debug("跳过 %s（仅 %d 行）", fp_path, len(df))
                continue
            fp = FactorPipeline(df)
            factors = fp.generate()
            factors = fp.add_targets(factors, df["close"])
            factors["symbol"] = df["ts_code"].iloc[0]
            all_factors.append(factors)
            if not feature_cols:
                feature_cols = fp.get_feature_cols(factors)
        except Exception as exc:  # noqa: BLE001
            logger.warning("加载 %s 失败: %s", fp_path, exc)

    panel = pd.concat(all_factors, ignore_index=True)
    panel = panel.dropna(subset=[FACTOR_COL, "next_day_return"])
    logger.info(
        "Panel: %d 行 × %d 股票 × %d 因子, 日期 %s ~ %s",
        len(panel), panel["symbol"].nunique(), len(feature_cols),
        panel["trade_date"].min().date(),
        panel["trade_date"].max().date(),
    )
    return panel, feature_cols


# ---------------------------------------------------------------------- #
# 绘图
# ---------------------------------------------------------------------- #


def plot_panels(
    res: pd.DataFrame,
    quant_df: pd.DataFrame,
    ic_series: pd.Series,
    bench_returns: pd.Series,
    ic_decay: pd.DataFrame,
) -> None:
    fig, axes = plt.subplots(2, 2, figsize=(16, 12))
    dates = res["trade_date"]

    # Panel 1: 净值曲线 vs 基准
    ax = axes[0, 0]
    cum_gross = (1 + res["gross_return"]).cumprod() - 1
    cum_net = (1 + res["net_return"]).cumprod() - 1
    ax.plot(dates, cum_gross, label="毛收益", lw=1.5, alpha=0.7)
    ax.plot(dates, cum_net, label="净收益（扣成本）", lw=2)
    if bench_returns is not None and not bench_returns.empty:
        cum_b = (1 + bench_returns).cumprod() - 1
        ax.plot(dates, cum_b.values, label="科创50 指数", lw=1.5, color="gray", alpha=0.7)
    ax.set_title(f"macd_hist Top {TOP_PCT:.0%} 截面选股 vs 基准")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)

    # Panel 2: 5 分位累计收益
    ax = axes[0, 1]
    if not quant_df.empty:
        for col in quant_df.columns:
            cum_q = (1 + quant_df[col].fillna(0)).cumprod() - 1
            ax.plot(quant_df.index, cum_q, label=col, alpha=0.8)
        ax.set_title("macd_hist 5 分位等权累计收益")
        ax.legend(fontsize=8, ncol=2)
        ax.grid(alpha=0.3)

    # Panel 3: 滚动 IC + 滚动 Sharpe
    ax = axes[1, 0]
    window = 60
    if not ic_series.empty:
        rolling_ic = ic_series.rolling(window).mean()
        ax.plot(rolling_ic.index, rolling_ic.values, label=f"{window}d 滚动 IC", color="C1")
        ax.axhline(y=0, color="red", ls="--", alpha=0.4)
        ax.axhline(y=ic_series.mean(), color="green", ls=":",
                   label=f"全期均值 {ic_series.mean():.4f}")
    ax.set_title(f"{window} 日滚动截面 IC")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)

    # Panel 4: IC 衰减 + 直方图
    ax = axes[1, 1]
    if not ic_series.empty:
        ic_clean = ic_series.dropna()
        ax.hist(ic_clean.values, bins=40, alpha=0.6, color="C1", label="IC 分布")
        ax.axvline(x=ic_clean.mean(), color="red", ls="--",
                   label=f"均值: {ic_clean.mean():.4f}")
        ax.axvline(x=0, color="black", ls=":", alpha=0.4)
        ax.set_title(f"截面 IC 直方图 (n={len(ic_clean)} 日)")
        ax.legend(fontsize=8)
        ax.grid(alpha=0.3)
        # IC 衰减子表（horizon → IC）
        if not ic_decay.empty:
            ax2 = ax.twinx()
            ax2.bar(range(len(ic_decay)), ic_decay["ic_mean"].values,
                    alpha=0.3, color="C2", width=0.6)
            ax2.set_xticks(range(len(ic_decay)))
            ax2.set_xticklabels(ic_decay["horizon"], rotation=30, fontsize=7)
            ax2.set_ylabel("IC by horizon", color="C2")

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


def render_markdown(
    res: pd.DataFrame,
    panel: pd.DataFrame,
    metrics_gross: dict,
    metrics_net: dict,
    bench_block: dict | None,
    sig_block: dict,
    ic_summary: dict,
    quant_df: pd.DataFrame,
    ic_decay: pd.DataFrame,
    n_stocks: int,
) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []

    lines.append("# macd_hist 横截面跨股票验证")
    lines.append("")
    lines.append(
        f"- **股票池**: 51 只科创板（``cs_data_688*.csv``）"
    )
    lines.append(
        f"- **样本**: {len(panel):,} 行 × {panel['symbol'].nunique()} 股票 "
        f"× {len(res)} 个交易日"
    )
    lines.append(
        f"- **区间**: {res['trade_date'].min().date()} ~ {res['trade_date'].max().date()}"
    )
    lines.append(
        f"- **策略**: 每日按 ``macd_hist`` 截面 rank 选 Top **{TOP_PCT:.0%}** 等权持有，"
        "T+1 开盘建仓 / T+2 开盘换仓"
    )
    lines.append(
        f"- **成本**: 买入 0.10% / 卖出 0.20%"
    )
    lines.append("")

    # ============== 1. 核心绩效 ============== #
    lines.append("## 1. 核心绩效")
    lines.append("")
    lines.append("| 指标 | 毛收益 | 净收益 |")
    lines.append("|------|--------|--------|")
    for k, label, suffix in [
        ("total", "总收益", "%"),
        ("annual", "年化", "%"),
        ("sharpe", "Sharpe", ""),
        ("max_dd", "最大回撤", "%"),
        ("calmar", "Calmar", ""),
        ("win", "胜率", "%"),
        ("sortino", "Sortino", ""),
        ("downside_dev", "下行波动率", "%"),
        ("n", "交易日", ""),
    ]:
        vg = metrics_gross.get(k, float("nan"))
        vn = metrics_net.get(k, float("nan"))
        if suffix == "%":
            lines.append(f"| {label} | {vg*100:+.1f}% | {vn*100:+.1f}% |")
        elif k == "n":
            lines.append(f"| {label} | {int(vg)} | {int(vn)} |")
        else:
            lines.append(f"| {label} | {vg:.2f} | {vn:.2f} |")
    lines.append("")
    lines.append(
        f"- 平均换手率: {res['turnover'].mean():.1%}  "
        f"平均持仓数: {res['n_stocks'].mean():.1f} 只  "
        f"累计成本: {res['cost'].sum()*100:.1f}%"
    )
    lines.append("")

    # ============== 2. 基准对比 ============== #
    if bench_block:
        ab = bench_block["alpha_beta"]
        lines.append("## 2. vs 科创50 基准")
        lines.append("")
        lines.append("| 指标 | 值 |")
        lines.append("|------|----|")
        lines.append(f"| Alpha（年化） | {ab['alpha_annual']*100:+.2f}% |")
        lines.append(f"| Beta | {ab['beta']:.3f} |")
        if pd.notna(ab.get("alpha_t_stat", float("nan"))):
            lines.append(f"| Alpha t-stat | {ab['alpha_t_stat']:.2f} |")
            lines.append(f"| Alpha p-value | {ab['alpha_p_value']:.4f} |")
        lines.append(f"| R² | {ab['r_squared']:.3f} |")
        lines.append(f"| Information Ratio | {ab['info_ratio']:.2f} |")
        lines.append(f"| 超额收益最大回撤 | {bench_block['excess_max_dd']*100:.1f}% |")
        lines.append("")

    # ============== 3. 统计显著性 ============== #
    lines.append("## 3. 统计显著性")
    lines.append("")
    lines.append("| 指标 | 值 |")
    lines.append("|------|----|")
    lines.append(f"| Sharpe (净收益) | {sig_block['sharpe']:.2f} |")
    lines.append(f"| 日均收益 t-stat | {sig_block['t_stat']:.2f} |")
    lines.append(f"| p-value | {sig_block['p_value']:.4f} |")
    if pd.notna(sig_block.get("nw_se", float("nan"))):
        lines.append(f"| Newey-West SE | {sig_block['nw_se']:.6f} |")
    if pd.notna(sig_block.get("deflated_sharpe", float("nan"))):
        lines.append(f"| Deflated Sharpe (PSR) | {sig_block['deflated_sharpe']:.3f} |")
    if "annual_ci" in sig_block:
        lo, hi = sig_block["annual_ci"]
        lines.append(f"| 年化收益 95% CI | [{lo*100:+.1f}%, {hi*100:+.1f}%] |")
    lines.append(f"| 样本数 | {sig_block['n']} |")
    lines.append("")

    # ============== 4. 信号质量诊断 ============== #
    lines.append("## 4. 信号质量诊断")
    lines.append("")
    lines.append("### 4.1 截面 IC（每日全市场 Spearman rank correlation）")
    lines.append("")
    lines.append("| 指标 | 值 |")
    lines.append("|------|----|")
    lines.append(f"| IC 均值 | {ic_summary['ic_mean']:.4f} |")
    lines.append(f"| IC 标准差 | {ic_summary['ic_std']:.4f} |")
    lines.append(f"| IR (年化) | {ic_summary['ir']:.2f} |")
    lines.append(f"| IC t-stat | {ic_summary['ic_t_stat']:.2f} |")
    lines.append(f"| IC > 0 占比 | {ic_summary['ic_positive_rate']:.1%} |")
    lines.append(f"| IC 最长连负天数 | {int(ic_summary['ic_max_consec_neg'])} |")
    lines.append(f"| 有效日数 | {int(ic_summary['n'])} |")
    lines.append("")

    if not quant_df.empty:
        lines.append("### 4.2 5 分位组合累计收益")
        lines.append("")
        lines.append("| 分位 | 日均收益 | 累计收益 |")
        lines.append("|------|----------|----------|")
        cum = (1 + quant_df.fillna(0)).prod() - 1
        for col in quant_df.columns:
            lines.append(
                f"| {col} | {quant_df[col].mean()*100:+.3f}% | "
                f"{cum[col]*100:+.1f}% |"
            )
        spread_mean = (quant_df.iloc[:, -1] - quant_df.iloc[:, 0]).mean()
        lines.append("")
        lines.append(
            f"**多空价差 (Q5 - Q1) 日均**: {spread_mean*100:+.3f}%  "
        )
        lines.append("")

    if not ic_decay.empty:
        lines.append("### 4.3 不同 horizon 的截面 IC（衰减）")
        lines.append("")
        lines.append("| Horizon | IC 均值 | IR | IC > 0 占比 |")
        lines.append("|---------|---------|----|-----------|")
        for _, row in ic_decay.iterrows():
            lines.append(
                f"| `{row['horizon']}` | {row['ic_mean']:.4f} | "
                f"{row['ir']:.2f} | {row['ic_positive_rate']:.1%} |"
            )
        lines.append("")

    # ============== 5. 与 688017 单股结果对比 ============== #
    lines.append("## 5. vs 688017 单股票结果")
    lines.append("")
    lines.append("| 维度 | 688017 单股票 (v3 WF) | 51 只科创板截面（本报告） |")
    lines.append("|------|---------------------|---------------------|")
    lines.append(f"| 因子使用 | 时序 rolling z-score ±1 阈值 | 截面 rank Top 20% |")
    lines.append(f"| 样本量 | 800 天 × 1 只 | {len(res)} 天 × 51 只 |")
    lines.append(
        f"| 净 Sharpe | macd_hist 默认 ±1: **1.18**（事后 in-sample）|"
        f" **{metrics_net['sharpe']:.2f}** |"
    )
    lines.append(
        f"| 年化净收益 | **+57.7%** | **{metrics_net['annual']*100:+.1f}%** |"
    )
    lines.append(
        f"| 最大回撤 | **-35.2%** | **{metrics_net['max_dd']*100:.1f}%** |"
    )
    lines.append("")

    # ============== 6. 结论 ============== #
    lines.append("## 6. 结论")
    lines.append("")
    # 判断截面 alpha 是否显著
    is_alpha_significant = (
        sig_block["p_value"] < 0.05 and sig_block["sharpe"] > 0
    )
    is_ic_significant = (
        abs(ic_summary["ic_t_stat"]) > 2 and ic_summary["ic_mean"] > 0
    )
    has_beta_close_to_1 = bench_block and 0.7 < bench_block["alpha_beta"]["beta"] < 1.3
    has_positive_alpha = (
        bench_block and bench_block["alpha_beta"]["alpha_annual"] > 0
    )

    lines.append("**核心问题**：macd_hist 在 688017 上的高 Sharpe 是因子本身的 alpha，还是单股噪声？")
    lines.append("")
    if is_ic_significant:
        lines.append(
            f"1. **截面 IC 显著**：IC 均值 = {ic_summary['ic_mean']:.4f}，t-stat = "
            f"{ic_summary['ic_t_stat']:.2f}（|t| > 2），"
            f"IC > 0 占 {ic_summary['ic_positive_rate']:.0%} 的日子。"
            "**macd_hist 在 51 只科创板上有横截面预测力**——不是 688017 的过拟合."
        )
    else:
        lines.append(
            f"1. **截面 IC 不显著**：IC 均值 = {ic_summary['ic_mean']:.4f}，"
            f"t-stat = {ic_summary['ic_t_stat']:.2f}。"
            "**macd_hist 的横截面预测力不明显**——688017 的 1.18 Sharpe 可能是单股噪声."
        )
    lines.append("")
    if is_alpha_significant:
        lines.append(
            f"2. **策略净收益显著**：日均收益 t-stat = {sig_block['t_stat']:.2f} "
            f"(p = {sig_block['p_value']:.4f})，Sharpe = {sig_block['sharpe']:.2f}。"
            "Top 20% 选股相对基准有可观察的正向 alpha."
        )
    else:
        lines.append(
            f"2. **策略净收益不显著**：t-stat = {sig_block['t_stat']:.2f} "
            f"(p = {sig_block['p_value']:.4f})。"
            "Top 20% 选股相对基准的超额收益在统计上无法拒绝零假设."
        )
    lines.append("")
    if bench_block:
        ab = bench_block["alpha_beta"]
        if has_positive_alpha:
            lines.append(
                f"3. **vs 基准**: 年化 Alpha = {ab['alpha_annual']*100:+.2f}%，"
                f"β = {ab['beta']:.2f}，IR = {ab['info_ratio']:.2f}。"
            )
        else:
            lines.append(
                f"3. **vs 基准**: 年化 Alpha = {ab['alpha_annual']*100:+.2f}%（负），"
                f"β = {ab['beta']:.2f}。**横截面 Top 20% 不能稳定跑赢科创50**."
            )
        lines.append("")

    # 与 688017 对比的关键判断
    if metrics_net["sharpe"] > 0.5:
        lines.append(
            f"4. **横截面 Sharpe ({metrics_net['sharpe']:.2f}) 远低于 688017 单股 (1.18)** —— "
            "印证了 v3 的结论：单股票时序回测的 Sharpe 严重夸大；"
            "横截面框架虽然更稳健但 alpha 也更小，是更可信的实盘性能估计."
        )
    else:
        lines.append(
            f"4. **横截面 Sharpe 仅 {metrics_net['sharpe']:.2f}** —— "
            "macd_hist 作为截面排序因子在科创板上预测力有限. "
            "单股票回测的「高 Sharpe」很大程度上来自该股票自身的趋势性，并非因子的横截面 alpha."
        )
    lines.append("")

    # ============== 7. 注意事项 ============== #
    lines.append("## 7. 注意事项")
    lines.append("")
    lines.append(
        "1. **股票池幸存者偏差**：``cs_data_688*.csv`` 是当前科创板成分股，"
        "未含上市 < 100 天的新股与历史已退市股票；"
    )
    lines.append(
        f"2. **样本期偏短**：{len(res)} 天 ≈ {len(res)/252:.1f} 年，"
        "不足以覆盖完整牛熊周期；"
    )
    lines.append(
        "3. **未做行业中性化**：科创板内部存在半导体、新能源等子板块的共振，"
        "未对此剥离；"
    )
    lines.append(
        "4. **未做市值中性**：``top_pct=20%`` 等权可能系统性偏向小市值；"
    )
    lines.append(
        "5. **横截面 vs 时序的本质区别**：本回测每天换仓 Top 20%，"
        "688017 时序回测是固定一只股票的 0/1 仓位——两者不可直接比较绝对收益，"
        "应比较 Sharpe / IR / Alpha 等风险调整指标."
    )
    lines.append("")
    lines.append("---")
    lines.append("_Generated by `scripts/analyze_macd_cross_section.py`_")

    MD_PATH.write_text("\n".join(lines), encoding="utf-8")
    logger.info("Markdown: %s", MD_PATH)


# ---------------------------------------------------------------------- #
# Main
# ---------------------------------------------------------------------- #


def main() -> None:
    panel, feature_cols = load_universe_panel()

    # 横截面回测
    cost = CostModel(buy_cost=0.001, sell_cost=0.002, slippage_bps=0)
    res = factor_cross_section_backtest(
        panel, FACTOR_COL, cost,
        top_pct=TOP_PCT, direction="long_high", min_stocks=10,
    )
    if res is None or res.empty:
        logger.error("回测无结果")
        return

    m_gross = Metrics.calc(res["gross_return"])
    m_net = Metrics.calc(res["net_return"])
    logger.info(
        "[净] 年化 %.1f%%, Sharpe %.2f, 回撤 %.1f%%",
        m_net["annual"]*100, m_net["sharpe"], m_net["max_dd"]*100,
    )

    # 诊断
    diag_panel = res.attrs["panel"]
    ic_series = SignalDiagnostics.ic_series(
        diag_panel, "pred_score", "next_day_return", method="spearman",
    )
    ic_summary = SignalDiagnostics.ic_summary(ic_series)
    logger.info(
        "[诊断] 截面 IC: 均值 %.4f, IR %.2f, t-stat %.2f, > 0 占 %.0f%%",
        ic_summary["ic_mean"], ic_summary["ir"],
        ic_summary["ic_t_stat"], ic_summary["ic_positive_rate"]*100,
    )

    quant_df = SignalDiagnostics.quantile_returns(
        diag_panel, "pred_score", "next_day_return", n_quantiles=5,
    )

    # IC 衰减
    ret_cols = ["next_day_return"]
    for h in ["future_return_5d", "future_return_10d", "future_return_20d"]:
        if h in panel.columns:
            ret_cols.append(h)
    # 把 future_return_Nd 也拼进面板（每日 + symbol）做 IC decay
    decay_panel = panel[["trade_date", "symbol", FACTOR_COL] + ret_cols].copy()
    decay_panel = decay_panel.rename(columns={FACTOR_COL: "pred_score"})
    ic_decay = SignalDiagnostics.ic_decay(
        decay_panel, "pred_score", ret_cols, method="spearman",
    )

    # 基准
    bench_block: dict | None = None
    bench_returns_aligned: pd.Series | None = None
    if BENCHMARK_PATH.exists():
        bm = Benchmark(BENCHMARK_PATH, rf_annual=0.02)
        strat_returns = res.set_index("trade_date")["net_return"]
        ab = bm.alpha_beta(strat_returns)
        bench_returns_aligned = bm.aligned_returns(strat_returns)
        bench_block = {
            "alpha_beta": ab,
            "excess_max_dd": bm.excess_max_dd(strat_returns),
        }
        logger.info(
            "[基准] α(年化) %.2f%%, β %.3f, R² %.2f, IR %.2f",
            ab["alpha_annual"]*100, ab["beta"], ab["r_squared"], ab["info_ratio"],
        )

    # 显著性
    sig_block = Significance.sharpe_significance(
        res["net_return"], n_trials=5,
        skew=m_net.get("skew", 0.0), kurt=m_net.get("kurt", 0.0),
    )
    ci = Significance.bootstrap_ci(
        res["net_return"],
        metric_fn=lambda s: float(((1 + s).prod()) ** (252 / len(s)) - 1),
        n_boot=500, seed=42,
    )
    sig_block["annual_ci"] = ci
    logger.info(
        "[显著性] t=%.2f, p=%.4f, DSR=%.3f, 年化 CI [%.1f%%, %.1f%%]",
        sig_block["t_stat"], sig_block["p_value"],
        sig_block["deflated_sharpe"], ci[0]*100, ci[1]*100,
    )

    # 绘图
    plot_panels(
        res, quant_df, ic_series,
        bench_returns_aligned if bench_returns_aligned is not None else pd.Series(dtype=float),
        ic_decay,
    )

    # Markdown
    render_markdown(
        res, panel, m_gross, m_net,
        bench_block, sig_block, ic_summary,
        quant_df, ic_decay, panel["symbol"].nunique(),
    )

    # 终端摘要
    print("\n" + "=" * 78)
    print(f"macd_hist 横截面跨股票验证 ({panel['symbol'].nunique()} 只科创板)")
    print("=" * 78)
    print(f"\n净收益 (毛 → 净):")
    print(f"  Sharpe:   {m_gross['sharpe']:+.2f} → {m_net['sharpe']:+.2f}")
    print(f"  年化:     {m_gross['annual']*100:+.1f}% → {m_net['annual']*100:+.1f}%")
    print(f"  最大回撤: {m_gross['max_dd']*100:.1f}% → {m_net['max_dd']*100:.1f}%")
    print(f"  平均换手: {res['turnover'].mean():.1%}  累计成本: {res['cost'].sum()*100:.1f}%")
    print(f"\n截面 IC:")
    print(f"  均值: {ic_summary['ic_mean']:.4f}  t-stat: {ic_summary['ic_t_stat']:.2f}  "
          f"IR: {ic_summary['ir']:.2f}  IC > 0: {ic_summary['ic_positive_rate']:.0%}")
    if bench_block:
        ab = bench_block["alpha_beta"]
        print(f"\nvs 科创50 基准:")
        print(f"  α (年化): {ab['alpha_annual']*100:+.2f}%  "
              f"β: {ab['beta']:.2f}  R²: {ab['r_squared']:.2f}  IR: {ab['info_ratio']:.2f}")
    print(f"\n显著性:")
    print(f"  t-stat: {sig_block['t_stat']:.2f}  p-value: {sig_block['p_value']:.4f}  "
          f"DSR: {sig_block['deflated_sharpe']:.3f}")
    print(f"\n与 688017 单股结果对比:")
    print(f"  Sharpe:   688017 单股 1.18 (in-sample) → 横截面 {m_net['sharpe']:.2f}")
    print(f"  年化:     688017 单股 +57.7%       → 横截面 {m_net['annual']*100:+.1f}%")
    print(f"\n报告: {MD_PATH}")
    print(f"图表: {PNG_PATH}")


if __name__ == "__main__":
    main()
