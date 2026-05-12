#!/usr/bin/env python3
"""科创板 51 只 sample_weight A/B 测试 —— 概念漂移加权 vs 等权.

Task #4.4 DDG-DA 轻量版：在 :class:`BacktestEngine.walk_forward` 上对训练样本
按 ``exp(-decay × age_in_days)`` 加权（近期权重高），看 LGB Ranker 的 Sharpe 是否
进一步提升、是否能压过 KSS baseline `log_mv 反向`.

实验设计:

* **A**: ``walk_forward(model_type="ranker", neutralize=False, sample_weight_decay=0)``
  → 等权（旧版行为）
* **B**: ``walk_forward(model_type="ranker", neutralize=False, sample_weight_decay=0.005)``
  → 半衰期 ~140 天
* **C**: ``factor_cross_section_backtest("log_mv", direction="long_low")``
  → KSS 当前最佳 single factor baseline，做为对照下界

输出::

    storage/reports/sample_weight_ab.md

风险提示（写进 markdown 报告）:

* ``decay=0.005`` 是 prior（半衰期 ~140 天），**未做网格选优**.
* 本次是 in-sample 测试，DSR n_trials=2（``strategy_family="single_factor"``）做轻矫正.
* 若想 production 上线，必须再做一次 walk-forward 选 decay（嵌套 CV）.
"""

from __future__ import annotations

import glob
import logging
import sys
from pathlib import Path

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
MD_PATH = REPORT_DIR / "sample_weight_ab.md"

# 因子选择：复用上一份 LGB 报告的 Top 10
IC_HORIZON = "future_return_5d"
IC_TOP_K = 10

# walk-forward 通用参数（与 KSS 默认一致）
TRAIN_WINDOW = 120
RETRAIN_FREQ = 5
TOP_PCT = 0.2
MIN_TRAIN = 1000
MIN_TEST = 30
MIN_STOCKS = 10

# decay prior：exp(-0.005 × age_days) → 半衰期 ≈ ln(2) / 0.005 ≈ 138.6 天
DECAY_PRIOR = 0.005

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
# 评估
# ---------------------------------------------------------------------- #


def evaluate(res: pd.DataFrame, bench: Benchmark, name: str) -> dict:
    """计算单策略 metrics + 显著性 + 基准 alpha/beta."""
    m_net = Metrics.calc(res["net_return"])
    sig = Significance.sharpe_significance(
        res["net_return"], n_trials=2,
        skew=m_net.get("skew", 0.0), kurt=m_net.get("kurt", 0.0),
    )
    strat_returns = res.set_index("trade_date")["net_return"]
    ab = bench.alpha_beta(strat_returns)
    deployable = Significance.is_deployable(
        res["net_return"],
        strategy_family="single_factor",
        return_details=True,
    )
    return {
        "name": name,
        "metrics_net": m_net,
        "significance": sig,
        "alpha_beta": ab,
        "deployable": deployable,
        "turnover_avg": float(res["turnover"].mean()) if "turnover" in res.columns else 0.0,
        "result_df": res,
    }


def panel_ic(res: pd.DataFrame, label_col: str = "future_return_5d") -> float | None:
    """计算 walk_forward 残留 panel 的截面 IC 均值（Spearman）.

    优先用 ``next_day_return``（与策略实际持仓收益一致）；fallback 到 label_col.
    """
    panel = res.attrs.get("panel")
    if panel is None or panel.empty:
        return None
    ret_col = "next_day_return" if "next_day_return" in panel.columns else label_col
    if ret_col not in panel.columns:
        return None
    try:
        ic = SignalDiagnostics.ic_series(
            panel, pred_col="pred_score", ret_col=ret_col,
            date_col="trade_date", method="spearman",
        )
    except Exception:  # noqa: BLE001
        return None
    if ic.empty:
        return None
    val = ic.dropna().mean()
    return float(val) if pd.notna(val) else None


# ---------------------------------------------------------------------- #
# Markdown
# ---------------------------------------------------------------------- #


def fmt_pct(v: float) -> str:
    return "—" if pd.isna(v) else f"{v*100:+.1f}%"


def render_markdown(
    panel: pd.DataFrame,
    top_factors: list[str],
    results: dict[str, dict],
    decay: float,
) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []

    half_life = float(np.log(2) / decay) if decay > 0 else float("inf")

    lines.append("# Sample Weight A/B —— 概念漂移加权（DDG-DA 轻量版）")
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
        f"- **入选因子**: Top {IC_TOP_K} 截面 IC（与 `kcb50_lgb_cross_section` 同源）"
    )
    lines.append(
        f"- **decay**: ``{decay}``（半衰期 ≈ {half_life:.1f} 天）—— **prior，未做网格选优**"
    )
    lines.append(
        f"- **walk-forward**: train_window={TRAIN_WINDOW}, retrain={RETRAIN_FREQ}, "
        f"top_pct={TOP_PCT}, label=``{IC_HORIZON}``"
    )
    lines.append("")

    # ============== 总览表 ============== #
    lines.append("## 1. A/B 总览（Sharpe / 年化 / 回撤 / Alpha / IR）")
    lines.append("")
    lines.append("| 策略 | Sharpe | 年化 | 最大回撤 | Calmar | 胜率 | 换手 | Alpha | IR | p | IC |")
    lines.append("|------|--------|------|----------|--------|------|------|-------|----|---|----|")
    for name, info in results.items():
        m = info["metrics_net"]
        sig = info["significance"]
        ab = info["alpha_beta"]
        ic = info.get("panel_ic")
        ic_str = "—" if ic is None else f"{ic:+.4f}"
        lines.append(
            f"| {name} | {m['sharpe']:+.2f} | "
            f"{fmt_pct(m['annual'])} | {fmt_pct(m['max_dd'])} | "
            f"{m['calmar']:+.2f} | {fmt_pct(m['win'])} | "
            f"{info['turnover_avg']*100:.1f}% | "
            f"{fmt_pct(ab['alpha_annual'])} | {ab['info_ratio']:+.2f} | "
            f"{sig['p_value']:.3f} | {ic_str} |"
        )
    lines.append("")

    # ============== A vs B 关键判断 ============== #
    lines.append("## 2. A vs B：加权是否进一步提升 LGB？")
    lines.append("")
    res_a = results.get(f"LGB Ranker 等权 (decay=0)")
    res_b = results.get(f"LGB Ranker 加权 (decay={decay})")
    if res_a and res_b:
        sa = res_a["metrics_net"]["sharpe"]
        sb = res_b["metrics_net"]["sharpe"]
        diff = sb - sa
        verdict = (
            "✓ **加权显著抬升**" if diff > 0.1
            else ("～ **基本持平**" if abs(diff) <= 0.1 else "✗ **加权反而变差**")
        )
        lines.append(f"- Sharpe: 等权 **{sa:+.2f}** → 加权 **{sb:+.2f}**（Δ {diff:+.2f}）{verdict}")
        ann_a = res_a["metrics_net"]["annual"]
        ann_b = res_b["metrics_net"]["annual"]
        lines.append(f"- 年化: {fmt_pct(ann_a)} → {fmt_pct(ann_b)}")
        dd_a = res_a["metrics_net"]["max_dd"]
        dd_b = res_b["metrics_net"]["max_dd"]
        lines.append(f"- 最大回撤: {fmt_pct(dd_a)} → {fmt_pct(dd_b)}")

        if diff > 0.1:
            lines.append("")
            lines.append(
                "**解读**：近期样本加权后 LGB 似乎更好地适应了"
                "2024 末小盘崩盘段／2025 切风格段，"
                "训练分布与测试分布的距离被有效缩小."
            )
        elif diff < -0.1:
            lines.append("")
            lines.append(
                "**解读**：加权反而变差 —— 说明本数据上"
                "「概念漂移」不是主要瓶颈，远样本对当前预测仍有效（"
                "或 51 只股票样本本身就太小，加权又削去了有效数据量）. "
                "瓶颈更可能在「特征本身的弱 IC」或「市场状态切换的非平稳性」."
            )
        else:
            lines.append("")
            lines.append(
                "**解读**：加权基本不影响 —— 本时段内分布漂移较小，"
                "或 decay=0.005 半衰期 ~140 天对一个 ~2 年的样本来说几乎等价于等权."
            )
    else:
        lines.append("- A 或 B 缺失，无法对比")
    lines.append("")

    # ============== 与 log_mv 反向对比 ============== #
    lines.append("## 3. 加权 LGB vs `log_mv` 反向（KSS baseline）")
    lines.append("")
    res_c = results.get("log_mv 反向 (KSS baseline)")
    if res_b and res_c:
        sb = res_b["metrics_net"]["sharpe"]
        sc = res_c["metrics_net"]["sharpe"]
        diff = sb - sc
        verdict = (
            "✓ **LGB 加权终于超越 baseline**" if diff > 0.1
            else ("～ **基本打平**" if abs(diff) <= 0.1 else "✗ **仍打不过 log_mv 反向**")
        )
        lines.append(f"- Sharpe: log_mv {sc:+.2f} vs LGB 加权 {sb:+.2f}（Δ {diff:+.2f}）{verdict}")
        lines.append("")
        if diff < -0.1:
            lines.append(
                "**结论**：LGB 加权这一档 hyper-param 调整 **没能改变** 「单因子 size effect 仍是科创板 51 只池中最强信号」"
                "这一事实. 短期内 LGB 多因子组合的瓶颈不是「样本权重」，"
                "更可能是 (1) 因子集本身的弱 IC、(2) 行业 / 市值未中性化让 log_mv "
                "在多因子组合里"
                "被稀释、(3) 训练样本量过小（51 股 × 训练窗 ≈ 6000 行）."
            )
        elif diff > 0.1:
            lines.append(
                "**结论**：加权 LGB 终于超越 single factor baseline —— "
                "**漂移加权对 LGB 是有效的能力补丁**，可作为下一轮 ablation 中的一个变量保留."
            )
    else:
        lines.append("- B 或 baseline 缺失，无法对比")
    lines.append("")

    # ============== DSR 检验 ============== #
    lines.append("## 4. DSR 部署门槛检查（strategy_family=\"single_factor\", n_trials=2）")
    lines.append("")
    lines.append("| 策略 | Sharpe | DSR | 通过部署门槛 |")
    lines.append("|------|--------|-----|--------------|")
    for name, info in results.items():
        dep = info["deployable"]
        passed = dep.get("passed", False) if isinstance(dep, dict) else bool(dep)
        sharpe = info["metrics_net"]["sharpe"]
        # `is_deployable(return_details=True)` 用键名 `dsr`；
        # 而 `sharpe_significance` 返回的是 `deflated_sharpe` —— 优先 dep，回退到 sig.
        if isinstance(dep, dict) and "dsr" in dep:
            dsr = dep["dsr"]
        else:
            dsr = info["significance"].get("deflated_sharpe", float("nan"))
        dsr_str = "—" if not np.isfinite(dsr) else f"{dsr:+.3f}"
        lines.append(
            f"| {name} | {sharpe:+.2f} | {dsr_str} | "
            f"{'✓' if passed else '✗'} |"
        )
    lines.append("")
    lines.append(
        "**关于 n_trials=2**：本实验只比较 2 个 decay 档（0 / 0.005），"
        "属于 `single_factor` 级别的小尝试. 若 production 要做 decay grid（如 5+ 档），"
        "应升到 `small_grid` (n_trials=5) 重新计算 DSR."
    )
    lines.append("")

    # ============== 风险与下一步 ============== #
    lines.append("## 5. 风险防御 / 下一步")
    lines.append("")
    lines.append(
        f"- **decay 是 prior，未做选优**：本次只跑 `decay=0` vs `decay={decay}` 两档. "
        "production 必须按 walk-forward 在每个训练窗口内嵌套选 decay（避免 in-sample bias）."
    )
    lines.append(
        "- **n_trials=2 只覆盖本次 A/B**：未来加更多 decay 档（如 0.001/0.003/0.005/0.01/0.02）必须升至 small_grid (n_trials=5)."
    )
    lines.append(
        "- **51 只股票池太小**：6000 行训练样本对 LGB 而言信噪比已经吃紧，"
        "加权又削去远样本的等效信息量. 在全市场 ~5000 只池上重做才有结论意义."
    )
    lines.append(
        "- **本次未做行业 / 市值中性化**：log_mv 反向之所以强，是因为科创板 size effect "
        "未被剥离. 下一步应叠加 `neutralize=True` 再做 A/B."
    )
    lines.append("")
    lines.append("---")
    lines.append("_Generated by `scripts/sample_weight_ab.py`_")

    MD_PATH.write_text("\n".join(lines), encoding="utf-8")
    logger.info("Markdown: %s", MD_PATH)


# ---------------------------------------------------------------------- #
# Main
# ---------------------------------------------------------------------- #


def main() -> None:
    panel, feature_cols = load_universe_panel()

    # ===== 1. 用 cross_section_ic_scan 选 Top K 因子（与 LGB 主报告同源）===== #
    logger.info("【1】跑 cross_section_ic_scan...")
    ic_scan = SignalDiagnostics.cross_section_ic_scan(
        panel, feature_cols,
        horizons=[IC_HORIZON],
        method="spearman",
    )
    top_factors = (
        ic_scan[ic_scan["horizon"] == IC_HORIZON]
        .head(IC_TOP_K)["factor"]
        .tolist()
    )
    logger.info("Top %d 截面 IC 因子: %s", IC_TOP_K, top_factors)

    bench = Benchmark(BENCHMARK_PATH, rf_annual=0.02)
    cost = CostModel(buy_cost=0.001, sell_cost=0.002, slippage_bps=0)

    results: dict[str, dict] = {}

    common = dict(
        factor_df=panel,
        feature_cols=top_factors,
        label_col=IC_HORIZON,
        train_window=TRAIN_WINDOW,
        retrain_freq=RETRAIN_FREQ,
        top_pct=TOP_PCT,
        min_train=MIN_TRAIN,
        min_test=MIN_TEST,
        min_stocks=MIN_STOCKS,
        model_type="ranker",
    )

    # ===== 2. A: 等权（decay=0）===== #
    logger.info("【2】A: LGB Ranker 等权 (decay=0)...")
    engine = BacktestEngine(cost)
    res_a = engine.walk_forward(**common, sample_weight_decay=0.0)
    if res_a is not None:
        info = evaluate(res_a, bench, "LGB Ranker 等权 (decay=0)")
        info["panel_ic"] = panel_ic(res_a, IC_HORIZON)
        results[info["name"]] = info
        logger.info(
            "  Sharpe=%.2f  年化=%.1f%%  回撤=%.1f%%",
            info["metrics_net"]["sharpe"],
            info["metrics_net"]["annual"] * 100,
            info["metrics_net"]["max_dd"] * 100,
        )

    # ===== 3. B: 加权（decay=DECAY_PRIOR）===== #
    logger.info("【3】B: LGB Ranker 加权 (decay=%s)...", DECAY_PRIOR)
    engine_b = BacktestEngine(cost)
    res_b = engine_b.walk_forward(**common, sample_weight_decay=DECAY_PRIOR)
    if res_b is not None:
        info = evaluate(res_b, bench, f"LGB Ranker 加权 (decay={DECAY_PRIOR})")
        info["panel_ic"] = panel_ic(res_b, IC_HORIZON)
        results[info["name"]] = info
        logger.info(
            "  Sharpe=%.2f  年化=%.1f%%  回撤=%.1f%%",
            info["metrics_net"]["sharpe"],
            info["metrics_net"]["annual"] * 100,
            info["metrics_net"]["max_dd"] * 100,
        )

    # ===== 4. C: log_mv 反向 baseline ===== #
    logger.info("【4】C: log_mv 反向 (KSS baseline)...")
    res_c = factor_cross_section_backtest(
        panel, "log_mv", cost,
        top_pct=TOP_PCT, direction="long_low",
        min_stocks=MIN_STOCKS,
    )
    if res_c is not None:
        info = evaluate(res_c, bench, "log_mv 反向 (KSS baseline)")
        results[info["name"]] = info
        logger.info(
            "  Sharpe=%.2f  年化=%.1f%%  回撤=%.1f%%",
            info["metrics_net"]["sharpe"],
            info["metrics_net"]["annual"] * 100,
            info["metrics_net"]["max_dd"] * 100,
        )

    # ===== 5. 输出 ===== #
    render_markdown(panel, top_factors, results, DECAY_PRIOR)

    # 终端摘要
    print("\n" + "=" * 86)
    print(f"Sample Weight A/B  (decay={DECAY_PRIOR}, 半衰期 ≈ {np.log(2)/DECAY_PRIOR:.0f} 天)")
    print("=" * 86)
    print(f"\n  {'策略':40s}  {'Sharpe':>8s}  {'年化':>8s}  {'回撤':>8s}  {'Alpha':>8s}  {'IR':>6s}  {'p':>6s}")
    print("  " + "-" * 92)
    for name, info in results.items():
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


if __name__ == "__main__":
    main()
