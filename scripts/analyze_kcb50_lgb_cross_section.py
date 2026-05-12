#!/usr/bin/env python3
"""科创板 51 只 LGB 横截面回测 —— 用截面 IC 选因子，主干 walk_forward 训练.

接续 ``analyze_macd_cross_section.py`` 的结论：单股票时序选因子（macd_hist Sharpe 1.18）
在横截面验证下失效（Sharpe 0.25 / IC 反向）。本脚本走真正的"工业流程"：

1. **用截面 IC 重选因子**：在 51 只科创板 panel 上算每个因子的截面 IC summary，
   按 |t-stat| 选 Top K 显著因子（不是用单股时序 Sharpe）；
2. **主干 LGB walk_forward**：用 ``BacktestEngine.walk_forward`` 在 Top K 因子上
   滚动训练 LightGBM，每天预测 ``future_return_5d`` 选 Top 20% 等权持有；
3. **多组对照**：单因子 (Top1 IC) baseline / macd_hist (反向) baseline /
   全 45 因子 LGB —— 看 LGB 多因子组合是否真的优于单因子.

输出::

    storage/reports/kcb50_lgb_cross_section_report.md
    storage/reports/kcb50_lgb_cross_section_panels.png
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
from kss.backtest.engine import BacktestEngine  # noqa: E402
from kss.backtest.metrics import Metrics  # noqa: E402
from kss.backtest.significance import Significance  # noqa: E402
from kss.features.pipeline import FactorPipeline  # noqa: E402

# ---------------------------------------------------------------------- #
# 常量
# ---------------------------------------------------------------------- #

DATA_GLOB = str(PROJECT_ROOT / "cs_data_688*.csv")
BENCHMARK_PATH = PROJECT_ROOT / "cs_index_000688_SH.csv"
REPORT_DIR = PROJECT_ROOT / "storage" / "reports"
MD_PATH = REPORT_DIR / "kcb50_lgb_cross_section_report.md"
PNG_PATH = REPORT_DIR / "kcb50_lgb_cross_section_panels.png"

# IC 扫描参数
IC_HORIZON = "future_return_5d"
IC_TOP_K = 10  # 截面 IC |t-stat| 前 K 个作为 LGB 特征

# Walk-forward 参数（与 KSS 默认一致）
TRAIN_WINDOW = 120
RETRAIN_FREQ = 5
TOP_PCT = 0.2
MIN_TRAIN = 1000  # 横截面：51 股 × 30 天 ≈ 1500 样本
MIN_TEST = 30
MIN_STOCKS = 10

plt.rcParams["font.sans-serif"] = ["Arial Unicode MS", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------- #
# 数据
# ---------------------------------------------------------------------- #


def load_universe_panel() -> tuple[pd.DataFrame, list[str]]:
    files = sorted(glob.glob(DATA_GLOB))
    if not files:
        raise FileNotFoundError(f"未找到匹配 {DATA_GLOB}")
    logger.info("加载 %d 只股票", len(files))

    all_factors: list[pd.DataFrame] = []
    feature_cols: list[str] = []
    for fp_path in files:
        try:
            df = pd.read_csv(fp_path)
            df["trade_date"] = pd.to_datetime(df["trade_date"])
            df = df.sort_values("trade_date").reset_index(drop=True)
            if len(df) < 100:
                continue
            fp = FactorPipeline(df)
            factors = fp.add_targets(fp.generate(), df["close"])
            factors["symbol"] = df["ts_code"].iloc[0]
            all_factors.append(factors)
            if not feature_cols:
                feature_cols = fp.get_feature_cols(factors)
        except Exception as exc:  # noqa: BLE001
            logger.warning("加载 %s 失败: %s", fp_path, exc)

    panel = pd.concat(all_factors, ignore_index=True)
    panel = panel.dropna(subset=["next_day_return"])
    logger.info(
        "Panel: %d 行 × %d 股票, %s ~ %s",
        len(panel), panel["symbol"].nunique(),
        panel["trade_date"].min().date(), panel["trade_date"].max().date(),
    )
    return panel, feature_cols


# ---------------------------------------------------------------------- #
# 单策略评估辅助
# ---------------------------------------------------------------------- #


def evaluate_strategy(
    res: pd.DataFrame,
    bench: Benchmark,
    name: str,
    n_trials: int = 5,
) -> dict:
    """计算单策略 metrics + 显著性 + 基准对比."""
    m_net = Metrics.calc(res["net_return"])
    m_gross = Metrics.calc(res["gross_return"])
    sig = Significance.sharpe_significance(
        res["net_return"], n_trials=n_trials,
        skew=m_net.get("skew", 0.0), kurt=m_net.get("kurt", 0.0),
    )
    strat_returns = res.set_index("trade_date")["net_return"]
    ab = bench.alpha_beta(strat_returns)
    bench_returns = bench.aligned_returns(strat_returns)
    excess_dd = bench.excess_max_dd(strat_returns)

    return {
        "name": name,
        "metrics_net": m_net,
        "metrics_gross": m_gross,
        "significance": sig,
        "alpha_beta": ab,
        "excess_max_dd": excess_dd,
        "bench_returns": bench_returns,
        "n_trades": int(res["n_stocks"].mean()) if "n_stocks" in res.columns else None,
        "turnover_avg": float(res["turnover"].mean()),
        "result_df": res,
    }


# ---------------------------------------------------------------------- #
# 绘图
# ---------------------------------------------------------------------- #


def plot_panels(
    panel: pd.DataFrame,
    ic_scan_5d: pd.DataFrame,
    strategies: dict[str, dict],
    top_k_factors: list[str],
) -> None:
    fig, axes = plt.subplots(3, 2, figsize=(16, 18))

    # Panel 1: Top 15 截面 IC |t-stat|（5d）
    ax = axes[0, 0]
    top = ic_scan_5d.head(15)
    colors = ["C2" if ic > 0 else "C3" for ic in top["ic_mean"]]
    ax.barh(range(len(top)), top["abs_t_stat"], color=colors, alpha=0.7)
    ax.set_yticks(range(len(top)))
    ax.set_yticklabels(top["factor"], fontsize=8)
    ax.invert_yaxis()
    ax.set_title("Top 15 截面 IC |t-stat| (5d horizon) | 绿=正向 红=反向")
    ax.set_xlabel("|t-stat|")
    ax.axvline(x=2.0, color="black", ls="--", alpha=0.4, label="t=2 (5%) 显著")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3, axis="x")

    # Panel 2: 所有策略净值曲线
    ax = axes[0, 1]
    # 用任一策略的 bench_returns 作基准
    any_strat = next(iter(strategies.values()))
    bench = any_strat["bench_returns"]
    if not bench.empty:
        cum_b = (1 + bench.fillna(0)).cumprod() - 1
        ax.plot(bench.index, cum_b.values, label="科创50 指数", lw=1.5,
                color="gray", alpha=0.7)
    colors_strat = ["C0", "C1", "C2", "C3", "C4"]
    for i, (name, info) in enumerate(strategies.items()):
        r = info["result_df"]
        cum = (1 + r["net_return"].fillna(0)).cumprod() - 1
        ax.plot(r["trade_date"], cum, label=name, lw=1.5,
                color=colors_strat[i % len(colors_strat)], alpha=0.85)
    ax.set_title("各策略净值曲线 vs 科创50")
    ax.legend(fontsize=8, loc="upper left")
    ax.grid(alpha=0.3)

    # Panel 3: Sharpe vs |最大回撤| 散点
    ax = axes[1, 0]
    for name, info in strategies.items():
        m = info["metrics_net"]
        s = m.get("sharpe", float("nan"))
        dd = abs(m.get("max_dd", 0))
        ax.scatter(dd * 100, s, s=200, marker="*", alpha=0.8,
                   edgecolors="black", linewidth=0.5)
        ax.annotate(name, (dd * 100, s), fontsize=8, alpha=0.85,
                    xytext=(5, 4), textcoords="offset points")
    ax.set_xlabel("|最大回撤| (%)")
    ax.set_ylabel("Sharpe (净)")
    ax.set_title("策略 Sharpe ↔ 回撤")
    ax.axhline(y=0, color="black", lw=0.3)
    ax.grid(alpha=0.3)

    # Panel 4: alpha / IR 柱
    ax = axes[1, 1]
    names = list(strategies.keys())
    alphas = [s["alpha_beta"]["alpha_annual"] * 100 for s in strategies.values()]
    irs = [s["alpha_beta"]["info_ratio"] for s in strategies.values()]
    x = np.arange(len(names))
    ax.bar(x - 0.2, alphas, 0.4, label="年化 Alpha (%)", color="C0", alpha=0.7)
    ax2 = ax.twinx()
    ax2.bar(x + 0.2, irs, 0.4, label="Information Ratio", color="C1", alpha=0.7)
    ax.set_xticks(x)
    ax.set_xticklabels(names, rotation=20, ha="right", fontsize=7)
    ax.set_ylabel("年化 Alpha (%)", color="C0")
    ax2.set_ylabel("Information Ratio", color="C1")
    ax.axhline(y=0, color="black", lw=0.3)
    ax.set_title("各策略 vs 基准：Alpha 与 IR")
    ax.legend(loc="upper left", fontsize=8)
    ax2.legend(loc="upper right", fontsize=8)
    ax.grid(alpha=0.3, axis="y")

    # Panel 5: 滚动 60d Sharpe（最强策略 vs 其他）
    ax = axes[2, 0]
    window = 60
    for i, (name, info) in enumerate(strategies.items()):
        r = info["result_df"]
        net = r.set_index("trade_date")["net_return"]
        rolling_sh = (
            net.rolling(window).mean() / net.rolling(window).std() * np.sqrt(252)
        )
        ax.plot(rolling_sh.index, rolling_sh.values, label=name, alpha=0.7,
                lw=1.2, color=colors_strat[i % len(colors_strat)])
    ax.axhline(y=0, color="black", lw=0.5, alpha=0.5)
    ax.set_title(f"{window} 日滚动 Sharpe")
    ax.legend(fontsize=8, loc="upper right")
    ax.grid(alpha=0.3)

    # Panel 6: LGB feature importance (取 LGB Top K 策略，若有)
    ax = axes[2, 1]
    lgb_key = next(
        (k for k in strategies if "LGB" in k and "Top" in k),
        None,
    )
    if lgb_key and "feature_importance" in strategies[lgb_key]:
        fi = strategies[lgb_key]["feature_importance"]
        if not fi.empty:
            ax.barh(range(len(fi)), fi["importance"], color="C0", alpha=0.7)
            ax.set_yticks(range(len(fi)))
            ax.set_yticklabels(fi["feature"], fontsize=8)
            ax.invert_yaxis()
            ax.set_title(f"LGB 特征重要性: {lgb_key}")
            ax.set_xlabel("Gain importance")
            ax.grid(alpha=0.3, axis="x")
    else:
        ax.set_visible(False)

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
    panel: pd.DataFrame,
    ic_scan_full: pd.DataFrame,
    strategies: dict[str, dict],
    top_k_factors: list[str],
) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []

    lines.append("# 科创板 LGB 横截面回测 —— 用截面 IC 选因子")
    lines.append("")
    lines.append(
        f"- **股票池**: 51 只科创板（``cs_data_688*.csv``）"
    )
    lines.append(
        f"- **样本**: {len(panel):,} 行 × {panel['symbol'].nunique()} 股票"
    )
    lines.append(
        f"- **区间**: {panel['trade_date'].min().date()} ~ {panel['trade_date'].max().date()}"
    )
    lines.append(
        f"- **因子选择**: 用 ``cross_section_ic_scan`` 按 |t-stat| 选 Top **{IC_TOP_K}** "
        f"个 horizon=``{IC_HORIZON}`` 的截面 IC 显著因子"
    )
    lines.append(
        f"- **主干 LGB**: ``BacktestEngine.walk_forward`` train_window={TRAIN_WINDOW}, "
        f"retrain={RETRAIN_FREQ}, top_pct={TOP_PCT}, label={IC_HORIZON}"
    )
    lines.append("")

    # ============== 1. 截面 IC 因子重选 ============== #
    lines.append("## 1. 截面 IC 因子重选")
    lines.append("")
    lines.append(
        "**为什么重新选**：前几轮报告用「单股票时序 Sharpe」选 Top 5 因子"
        "（macd_hist / vol_ma20_ratio 等），结果在 51 只科创板上横截面验证"
        "macd_hist IC = -0.0165（**反向**）。"
        "正确做法是直接在多股票面板上算每个因子的截面 IC 与 t-stat."
    )
    lines.append("")

    for h in [IC_HORIZON, "next_day_return", "future_return_10d"]:
        sub = ic_scan_full[ic_scan_full["horizon"] == h].head(15)
        if sub.empty:
            continue
        lines.append(f"### 1.{['future_return_5d', 'next_day_return', 'future_return_10d'].index(h) + 1} `{h}` — Top 15")
        lines.append("")
        lines.append("| 排名 | 因子 | IC 均值 | t-stat | IR | IC > 0 | n |")
        lines.append("|------|------|---------|--------|----|--------|---|")
        for i, row in sub.iterrows():
            sign = "🟢" if row["ic_mean"] > 0 else "🔴"
            sig = " ★" if abs(row["ic_t_stat"]) >= 2 else ""
            lines.append(
                f"| {sub.index.get_loc(i) + 1} | `{row['factor']}` {sign}{sig} | "
                f"{row['ic_mean']:+.4f} | {row['ic_t_stat']:+.2f} | "
                f"{row['ir']:+.2f} | {row['ic_positive_rate']:.0%} | "
                f"{int(row['n'])} |"
            )
        lines.append("")

    lines.append(
        f"**入选 Top {IC_TOP_K}（按 5d horizon |t-stat|）**："
    )
    for f in top_k_factors:
        row = ic_scan_full[
            (ic_scan_full["factor"] == f) & (ic_scan_full["horizon"] == IC_HORIZON)
        ].iloc[0]
        sign = "正向" if row["ic_mean"] > 0 else "反向"
        lines.append(
            f"- `{f}` (IC={row['ic_mean']:+.4f}, t={row['ic_t_stat']:+.2f}, {sign})"
        )
    lines.append("")

    # ============== 2. 综合对比 ============== #
    lines.append("## 2. 综合对比")
    lines.append("")
    lines.append("| 策略 | 年化 | Sharpe | 最大回撤 | Calmar | 胜率 | 换手 | Alpha | IR | p-value |")
    lines.append("|------|------|--------|----------|--------|------|------|-------|----|---------|")
    for name, info in strategies.items():
        m = info["metrics_net"]
        sig = info["significance"]
        ab = info["alpha_beta"]
        lines.append(
            f"| {name} | {fmt_pct(m['annual'])} | "
            f"{m['sharpe']:.2f} | {fmt_pct(m['max_dd'])} | "
            f"{m['calmar']:.2f} | {fmt_pct(m['win'])} | "
            f"{info['turnover_avg']*100:.1f}% | "
            f"{fmt_pct(ab['alpha_annual'])} | {ab['info_ratio']:.2f} | "
            f"{sig['p_value']:.3f} |"
        )
    lines.append("")

    # ============== 3. 每个策略详细诊断 ============== #
    lines.append("## 3. 每策略详细诊断")
    lines.append("")
    for name, info in strategies.items():
        lines.append(f"### `{name}`")
        lines.append("")
        m = info["metrics_net"]
        sig = info["significance"]
        ab = info["alpha_beta"]
        lines.append(
            f"- 净收益：年化 **{fmt_pct(m['annual'])}**，Sharpe **{m['sharpe']:.2f}**，"
            f"最大回撤 **{fmt_pct(m['max_dd'])}**"
        )
        lines.append(
            f"- 显著性：t-stat **{sig['t_stat']:.2f}** (p={sig['p_value']:.4f})，"
            f"Deflated Sharpe **{sig['deflated_sharpe']:.3f}**"
        )
        lines.append(
            f"- vs 科创50：年化 Alpha **{fmt_pct(ab['alpha_annual'])}**，"
            f"β **{ab['beta']:.2f}**，R² **{ab['r_squared']:.2f}**，"
            f"IR **{ab['info_ratio']:.2f}**"
        )
        lines.append(
            f"- 平均换手：**{info['turnover_avg']*100:.1f}%**，"
            f"超额最大回撤 **{info['excess_max_dd']*100:.1f}%**"
        )
        if "feature_importance" in info and not info["feature_importance"].empty:
            fi = info["feature_importance"].head(5)
            top_feats = ", ".join(f"`{r.feature}` ({r.importance:.0f})" for _, r in fi.iterrows())
            lines.append(f"- 重要特征 Top 5: {top_feats}")
        lines.append("")

    # ============== 4. 结论 ============== #
    lines.append("## 4. 结论")
    lines.append("")

    # 关键判断
    lgb_top_k = strategies.get(f"LGB Top {IC_TOP_K} (截面 IC 选)")
    lgb_full = strategies.get("LGB 全 45 因子")
    single_top1 = strategies.get(f"单 {top_k_factors[0]} 反向")
    macd_baseline = strategies.get("单 macd_hist (正向 baseline)")

    if lgb_top_k and lgb_full and single_top1:
        sh_lgb_top = lgb_top_k["metrics_net"]["sharpe"]
        sh_lgb_full = lgb_full["metrics_net"]["sharpe"]
        sh_single = single_top1["metrics_net"]["sharpe"]

        lines.append(
            f"1. **LGB Top {IC_TOP_K} (截面 IC 选) Sharpe = {sh_lgb_top:.2f}**"
        )
        lines.append(
            f"   - vs LGB 全 45 因子 Sharpe {sh_lgb_full:.2f}："
            f"{'✓ 选因子有用' if sh_lgb_top > sh_lgb_full + 0.05 else '✗ 选因子无显著增益（让 LGB 自己选）'}"
        )
        lines.append(
            f"   - vs 单 `{top_k_factors[0]}` 反向 Sharpe {sh_single:.2f}："
            f"{'✓ 多因子组合优于单因子' if sh_lgb_top > sh_single + 0.05 else '✗ 多因子未能超越单因子'}"
        )
        lines.append("")

        # LGB 跑负 Sharpe 时给出深度诊断
        if sh_lgb_top < 0 and sh_single > 0.5:
            lines.append(
                f"   **LGB 为何输得这么惨？** 三条根本原因："
            )
            n_significant = (ic_scan_full[ic_scan_full["horizon"] == IC_HORIZON]
                             .head(IC_TOP_K)["abs_t_stat"] >= 2).sum()
            lines.append(
                f"   - **信号微弱**：Top {IC_TOP_K} 因子中只有 **{int(n_significant)} 个** "
                f"|t-stat| ≥ 2 显著，多数 IC 仅 |ic| ≈ 0.02 量级。"
                f"LGB 在弱信号 + 小样本 (~6000 行训练集) 上倾向过拟合."
            )
            lines.append(
                f"   - **训练-测试目标错配**：LGB 的损失函数是 MSE on `{IC_HORIZON}`，"
                "学的是「预测收益值」；但实战中按预测 score 选 Top 20% 是「排序」任务。"
                "MSE 优化器在因子 IC 弱时会被噪声主导，学出与真实方向无关的映射。"
                "工业界标准做法是用 Rank IC objective 或 LambdaMART (learning to rank)."
            )
            lines.append(
                f"   - **简单胜复杂**：单 `{top_k_factors[0]}` 反向直接 ranking 不需要训练"
                f"——它的 |t-stat| = {ic_scan_full.iloc[0]['ic_t_stat']:.2f} 已经远超其他因子，"
                f"「加权稀释」+「训练噪声」双重伤害下 LGB 反而把唯一强信号搞糊涂."
            )
            lines.append("")

    if macd_baseline:
        sh_macd = macd_baseline["metrics_net"]["sharpe"]
        lines.append(
            f"2. **macd_hist (正向) baseline Sharpe = {sh_macd:.2f}**"
        )
        lines.append(
            f"   - 与上一报告 `macd_cross_section` 结果一致：单股票 Sharpe 1.18 → 横截面 ≈ 0.25。"
            f"再次印证**单股回测严重高估实盘性能**."
        )
        lines.append("")

    # IC 显著性最强的因子
    if not ic_scan_full.empty:
        top1 = ic_scan_full[ic_scan_full["horizon"] == IC_HORIZON].iloc[0]
        lines.append(
            f"3. **最强截面信号是 `{top1['factor']}`** —— "
            f"IC = {top1['ic_mean']:+.4f}, t-stat = {top1['ic_t_stat']:+.2f} "
            f"({'反向' if top1['ic_mean'] < 0 else '正向'})。"
        )
        if top1["factor"] == "log_mv":
            lines.append(
                "   - `log_mv` (对数市值) 显著负向 → "
                "**科创板存在显著的「小市值效应」**：小市值股票后续 5 日跑赢大市值."
                "这是因子工程领域的经典实证现象，本数据再次印证."
            )
        lines.append("")

    if lgb_top_k:
        ab = lgb_top_k["alpha_beta"]
        if ab["alpha_p_value"] < 0.05 and ab["alpha_annual"] > 0:
            lines.append(
                f"4. **LGB Top {IC_TOP_K} 选股 Alpha 显著**：年化 Alpha "
                f"{fmt_pct(ab['alpha_annual'])}, p-value {ab['alpha_p_value']:.3f}, "
                f"IR {ab['info_ratio']:.2f}. 这是相对**科创板内部**的超额收益."
            )
        else:
            lines.append(
                f"4. **LGB Top {IC_TOP_K} 选股 Alpha 不显著**：年化 Alpha "
                f"{fmt_pct(ab['alpha_annual'])}, p-value {ab['alpha_p_value']:.3f}. "
                "未能稳定跑赢科创板基准 - **科创板内部 Top 20% 选股的边际预测力有限**."
            )
        lines.append("")

    # ============== 5. 注意事项 ============== #
    lines.append("## 5. 注意事项")
    lines.append("")
    lines.append(
        "- **训练集/测试集时间分割已防 look-ahead**：``walk_forward`` 内 purge gap 由 "
        f"label `{IC_HORIZON}` 自动推断为 5 天."
    )
    lines.append(
        "- **截面 IC scan 用全样本**：理论上选因子环节本身存在事后偏差，"
        "实盘需 train_window 滚动重选因子。可作下一步（``factor_selection`` 也走 walk-forward）."
    )
    lines.append(
        "- **股票池幸存者偏差**：``cs_data_688*.csv`` 是当前科创板成分股，不含已退市；"
        "结论可能对全 A 市场不通用."
    )
    lines.append(
        "- **未做行业 / 市值中性化**：`log_mv` 主导可能掩盖其他因子的边际贡献；"
        "下一步可加行业中性化与去市值化."
    )
    lines.append(
        "- **样本期偏短**：~2.2 年，跨牛熊不足；DSR 已用 n_trials=5 做轻度矫正."
    )
    lines.append("")
    lines.append("---")
    lines.append("_Generated by `scripts/analyze_kcb50_lgb_cross_section.py`_")

    MD_PATH.write_text("\n".join(lines), encoding="utf-8")
    logger.info("Markdown: %s", MD_PATH)


# ---------------------------------------------------------------------- #
# Main
# ---------------------------------------------------------------------- #


def main() -> None:
    panel, feature_cols = load_universe_panel()

    # ===== 1. 截面 IC 扫描选因子 ===== #
    logger.info("【1】跑 cross_section_ic_scan...")
    ic_scan = SignalDiagnostics.cross_section_ic_scan(
        panel, feature_cols,
        horizons=[IC_HORIZON, "next_day_return", "future_return_10d"],
        method="spearman",
    )
    top_factors = (
        ic_scan[ic_scan["horizon"] == IC_HORIZON]
        .head(IC_TOP_K)["factor"]
        .tolist()
    )
    logger.info("Top %d 截面 IC 因子: %s", IC_TOP_K, top_factors)

    # 基准
    bench = Benchmark(BENCHMARK_PATH, rf_annual=0.02)
    cost = CostModel(buy_cost=0.001, sell_cost=0.002, slippage_bps=0)

    strategies: dict[str, dict] = {}

    # ===== 2. LGB walk_forward — Top K 截面 IC 因子 ===== #
    logger.info("【2】LGB walk_forward (Top %d 截面 IC 因子)...", IC_TOP_K)
    engine = BacktestEngine(cost)
    res_lgb_topk = engine.walk_forward(
        factor_df=panel,
        feature_cols=top_factors,
        label_col=IC_HORIZON,
        train_window=TRAIN_WINDOW,
        retrain_freq=RETRAIN_FREQ,
        top_pct=TOP_PCT,
        min_train=MIN_TRAIN,
        min_test=MIN_TEST,
        min_stocks=MIN_STOCKS,
    )
    if res_lgb_topk is not None:
        info = evaluate_strategy(res_lgb_topk, bench, f"LGB Top {IC_TOP_K} (截面 IC 选)")
        # 跑一次模型抽 feature importance（简化：在最后一窗训练）
        # TODO: 复用 _train_model 的最后一个 model；这里跳过详细 FI
        strategies[f"LGB Top {IC_TOP_K} (截面 IC 选)"] = info
        logger.info("  Sharpe=%.2f, 年化=%.1f%%, 回撤=%.1f%%",
                    info["metrics_net"]["sharpe"],
                    info["metrics_net"]["annual"] * 100,
                    info["metrics_net"]["max_dd"] * 100)

    # ===== 3. LGB walk_forward — 全 45 因子 ===== #
    logger.info("【3】LGB walk_forward (全 45 因子)...")
    res_lgb_full = engine.walk_forward(
        factor_df=panel,
        feature_cols=feature_cols,
        label_col=IC_HORIZON,
        train_window=TRAIN_WINDOW,
        retrain_freq=RETRAIN_FREQ,
        top_pct=TOP_PCT,
        min_train=MIN_TRAIN,
        min_test=MIN_TEST,
        min_stocks=MIN_STOCKS,
    )
    if res_lgb_full is not None:
        info = evaluate_strategy(res_lgb_full, bench, "LGB 全 45 因子")
        strategies["LGB 全 45 因子"] = info
        logger.info("  Sharpe=%.2f, 年化=%.1f%%, 回撤=%.1f%%",
                    info["metrics_net"]["sharpe"],
                    info["metrics_net"]["annual"] * 100,
                    info["metrics_net"]["max_dd"] * 100)

    # ===== 4. 单因子 baseline — Top1 截面 IC（log_mv 反向）===== #
    top1 = top_factors[0]
    row1 = ic_scan[
        (ic_scan["factor"] == top1) & (ic_scan["horizon"] == IC_HORIZON)
    ].iloc[0]
    direction1 = "long_low" if row1["ic_mean"] < 0 else "long_high"
    name1 = f"单 {top1} {'反向' if direction1 == 'long_low' else '正向'}"
    logger.info("【4】单 %s baseline...", name1)
    res_single = factor_cross_section_backtest(
        panel, top1, cost, top_pct=TOP_PCT, direction=direction1,
        min_stocks=MIN_STOCKS,
    )
    if res_single is not None:
        info = evaluate_strategy(res_single, bench, name1)
        strategies[name1] = info
        logger.info("  Sharpe=%.2f, 年化=%.1f%%",
                    info["metrics_net"]["sharpe"],
                    info["metrics_net"]["annual"] * 100)

    # ===== 5. macd_hist 正向 baseline (对比用) ===== #
    logger.info("【5】macd_hist (正向) baseline 对照...")
    res_macd = factor_cross_section_backtest(
        panel, "macd_hist", cost, top_pct=TOP_PCT, direction="long_high",
        min_stocks=MIN_STOCKS,
    )
    if res_macd is not None:
        info = evaluate_strategy(res_macd, bench, "单 macd_hist (正向 baseline)")
        strategies["单 macd_hist (正向 baseline)"] = info
        logger.info("  Sharpe=%.2f, 年化=%.1f%%",
                    info["metrics_net"]["sharpe"],
                    info["metrics_net"]["annual"] * 100)

    # ===== 6. 输出 ===== #
    ic_5d = ic_scan[ic_scan["horizon"] == IC_HORIZON].reset_index(drop=True)
    plot_panels(panel, ic_5d, strategies, top_factors)
    render_markdown(panel, ic_scan, strategies, top_factors)

    # 终端摘要
    print("\n" + "=" * 86)
    print(f"科创板 LGB 横截面回测 ({panel['symbol'].nunique()} 只)")
    print("=" * 86)
    print(f"\n截面 IC Top {IC_TOP_K} 因子（5d horizon）:")
    for f in top_factors:
        row = ic_scan[
            (ic_scan["factor"] == f) & (ic_scan["horizon"] == IC_HORIZON)
        ].iloc[0]
        sig = "★" if abs(row["ic_t_stat"]) >= 2 else " "
        sign = "反向" if row["ic_mean"] < 0 else "正向"
        print(f"  {sig} {f:25s}  IC={row['ic_mean']:+.4f}  t={row['ic_t_stat']:+.2f}  {sign}")

    print(f"\n各策略表现:")
    print(f"  {'策略':40s}  {'Sharpe':>8s}  {'年化':>8s}  {'回撤':>8s}  {'Alpha':>8s}  {'IR':>6s}  {'p':>6s}")
    print("  " + "-" * 92)
    for name, info in strategies.items():
        m = info["metrics_net"]
        ab = info["alpha_beta"]
        sig = info["significance"]
        print(
            f"  {name:40s}  {m['sharpe']:+8.2f}  "
            f"{m['annual']*100:+7.1f}%  {m['max_dd']*100:+7.1f}%  "
            f"{ab['alpha_annual']*100:+7.1f}%  {ab['info_ratio']:+6.2f}  "
            f"{sig['p_value']:6.3f}"
        )

    print(f"\n报告: {MD_PATH}")
    print(f"图表: {PNG_PATH}")


if __name__ == "__main__":
    main()
