"""kss backtest —— 运行 walk-forward 回测 + 可选诊断/基准/稳健性扫描."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import click
import pandas as pd

from kss.backtest.benchmark import Benchmark
from kss.backtest.cost_model import CostModel
from kss.backtest.diagnostics import SignalDiagnostics
from kss.backtest.engine import BacktestEngine
from kss.backtest.metrics import Metrics
from kss.backtest.reporter import BacktestReporter
from kss.backtest.robustness import Robustness
from kss.backtest.significance import Significance
from kss.data.cache_manager import CacheManager
from kss.data.data_loader import DataLoader
from kss.data.tushare_client import TushareClient
from kss.features.pipeline import FactorPipeline
from kss.features.selection import FeatureSelector

logger = logging.getLogger(__name__)


@click.command()
@click.option("--pool", "-p", default="kcb50", help="股票池名称")
@click.option(
    "--period",
    "-t",
    type=click.Choice(["5", "10", "20"]),
    default="10",
    help="预测周期（日）",
)
@click.option(
    "--output",
    "-o",
    default="./storage/reports/backtest_result.png",
    help="回测结果图表输出路径",
    type=click.Path(),
)
@click.option(
    "--diagnostics",
    is_flag=True,
    default=False,
    help="启用信号质量诊断（IC / 分位数 / 多空价差）",
)
@click.option(
    "--benchmark",
    default=None,
    help="基准指数 CSV 路径；提供后启用 alpha/beta 归因",
    type=click.Path(),
)
@click.option(
    "--robustness",
    type=click.Choice(["off", "fast", "full"]),
    default="off",
    help="稳健性扫描：fast=快速 2x2 grid + 牛熊熊三分段；full=完整 grid",
)
@click.option(
    "--feature-selection",
    is_flag=True,
    default=False,
    help="启用 LightGBM gain 累计特征筛选（开 walk_forward 前一次性选取）",
)
@click.option(
    "--winsorize",
    type=float,
    default=None,
    help="截面 Z-Score 后的离群截断 σ 阈值（典型 3.0）；缺省关闭",
)
@click.option(
    "--md-output",
    default=None,
    help="Markdown 诊断报告输出路径；提供后写盘",
    type=click.Path(),
)
@click.pass_context
def cmd(
    ctx: click.Context,
    pool: str,
    period: str,
    output: str,
    diagnostics: bool,
    benchmark: str | None,
    robustness: str,
    feature_selection: bool,
    winsorize: float | None,
    md_output: str | None,
) -> None:
    """运行 Walk-Forward 回测.

    使用历史数据滚动训练与验证，生成净值曲线 PNG.
    可选启用 ``--diagnostics`` / ``--benchmark`` / ``--robustness`` 输出诊断报告.
    """
    config: dict[str, Any] = ctx.obj.get("config", {})
    bt_cfg = config.get("backtest", {})
    cost_cfg = config.get("costs", {})
    bench_cfg = bt_cfg.get("benchmark", {})
    diag_cfg = bt_cfg.get("diagnostics", {})
    fs_cfg = bt_cfg.get("feature_selection", {})
    rob_cfg = bt_cfg.get("robustness", {})

    click.echo(f"📊 回测 | 股票池: {pool} | 周期: {period}d")

    # ------------------------------------------------------------------ #
    # 1. 加载数据
    # ------------------------------------------------------------------ #
    client = TushareClient()
    cache = CacheManager()
    loader = DataLoader(client, cache)
    df = loader.load_stock_pool(pool_name=pool)
    if df is None or len(df) == 0:
        click.secho("  ❌ 数据加载失败", fg="red")
        return
    click.echo(f"  ✅ 数据加载完成: {len(df)}行, {df['symbol'].nunique()}只股票")

    # ------------------------------------------------------------------ #
    # 2. 生成因子
    # ------------------------------------------------------------------ #
    symbols = list(df["symbol"].unique())
    if not symbols:
        click.secho("  ❌ 股票池为空，无法生成因子", fg="red")
        return

    flist = []
    fp: FactorPipeline | None = None
    for sym in symbols:
        sub = (
            df[df["symbol"] == sym]
            .sort_values("trade_date")
            .reset_index(drop=True)
        )
        fp = FactorPipeline(sub)
        f = fp.generate()
        f = fp.add_targets(f, sub["close"])
        f["symbol"] = sym
        flist.append(f)

    assert fp is not None
    factor_df = pd.concat(flist, ignore_index=True)
    feature_cols = fp.get_feature_cols(factor_df)
    click.echo(f"  ✅ 因子生成完成: {len(feature_cols)}个因子, {len(factor_df)}样本")

    label_col = f"future_return_{period}d"

    # ------------------------------------------------------------------ #
    # 3. （可选）特征筛选
    # ------------------------------------------------------------------ #
    if feature_selection:
        gain_pct = float(fs_cfg.get("gain_pct", 0.85))
        n_folds = int(fs_cfg.get("n_folds", 4))
        original_n = len(feature_cols)
        feature_cols = FeatureSelector.stable_features(
            factor_df, feature_cols, label_col,
            n_folds=n_folds, gain_pct=gain_pct,
        )
        click.echo(
            f"  ✅ 特征筛选完成: {original_n} → {len(feature_cols)} 个（gain_pct={gain_pct}）"
        )

    # ------------------------------------------------------------------ #
    # 4. 回测引擎
    # ------------------------------------------------------------------ #
    cost_model = CostModel(
        buy_cost=cost_cfg.get("buy", 0.001),
        sell_cost=cost_cfg.get("sell", 0.002),
        slippage_bps=float(bt_cfg.get("slippage_bps", 0.0)),
    )
    engine = BacktestEngine(cost_model)

    res = engine.walk_forward(
        factor_df,
        feature_cols,
        label_col,
        train_window=bt_cfg.get("train_window", 120),
        retrain_freq=bt_cfg.get("retrain_freq", 5),
        top_pct=bt_cfg.get("top_pct", 0.2),
        min_train=bt_cfg.get("min_train_samples", 300),
        min_test=bt_cfg.get("min_test_samples", 30),
        min_stocks=bt_cfg.get("min_stocks_per_day", 10),
        winsorize=winsorize,
    )

    if res is None or len(res) == 0:
        click.secho("  ❌ 回测结果为空", fg="red")
        return

    # ------------------------------------------------------------------ #
    # 5. 核心绩效
    # ------------------------------------------------------------------ #
    m_gross = Metrics.calc(res["gross_return"])
    m_net = Metrics.calc(res["net_return"])

    click.echo(f"\n  [毛收益] {Metrics.format(m_gross)}")
    click.echo(f"  [净收益] {Metrics.format(m_net)}")
    click.echo(
        f"  平均换手: {res['turnover'].mean():.1%}  总成本: {res['cost'].sum()*100:.1f}%"
    )

    # ------------------------------------------------------------------ #
    # 6. 诊断子模块（可选）
    # ------------------------------------------------------------------ #
    diag_block: dict[str, Any] | None = None
    sig_block: dict[str, Any] | None = None
    bench_block: dict[str, Any] | None = None
    rob_block: dict[str, Any] | None = None

    panel = res.attrs.get("panel") if hasattr(res, "attrs") else None

    if diagnostics and panel is not None and not panel.empty:
        n_q = int(diag_cfg.get("n_quantiles", 5))
        ic_method = str(diag_cfg.get("ic_method", "spearman"))
        ic_s = SignalDiagnostics.ic_series(
            panel, "pred_score", "next_day_return", method=ic_method
        )
        ic_sum = SignalDiagnostics.ic_summary(ic_s)
        quant = SignalDiagnostics.quantile_returns(
            panel, "pred_score", "next_day_return", n_quantiles=n_q,
        )
        spread = SignalDiagnostics.long_short_spread(quant)
        mono = SignalDiagnostics.monotonicity_score(quant)
        # IC decay：若 panel 含 label_col
        ret_cols = [c for c in [label_col, "next_day_return"] if c in panel.columns]
        ic_decay = SignalDiagnostics.ic_decay(
            panel, "pred_score", ret_cols, method=ic_method
        )
        diag_block = {
            "ic_series": ic_s,
            "ic_summary": ic_sum,
            "quantile_df": quant,
            "long_short": spread,
            "monotonicity": mono,
            "ic_decay": ic_decay,
        }
        click.echo(
            f"\n  [诊断] IC: {ic_sum['ic_mean']:.4f}  IR: {ic_sum['ir']:.2f}  "
            f"IC t-stat: {ic_sum['ic_t_stat']:.2f}  单调性: {mono:.3f}"
        )

        # 同步生成显著性指标（基于 net_return）
        sig_block = Significance.sharpe_significance(
            res["net_return"], n_trials=5,
            skew=m_net.get("skew", 0.0), kurt=m_net.get("kurt", 0.0),
        )
        # 年化收益 bootstrap CI
        ci = Significance.bootstrap_ci(
            res["net_return"],
            metric_fn=lambda s: float(((1 + s).prod()) ** (252 / len(s)) - 1),
            n_boot=500,
        )
        sig_block["annual_return_ci"] = ci
        click.echo(
            f"  [显著性] t={sig_block['t_stat']:.2f} p={sig_block['p_value']:.4f}  "
            f"Deflated Sharpe: {sig_block['deflated_sharpe']:.3f}  "
            f"年化 CI: [{ci[0]*100:.1f}%, {ci[1]*100:.1f}%]"
        )

    # ------------------------------------------------------------------ #
    # 7. 基准对比（可选）
    # ------------------------------------------------------------------ #
    bench_returns_aligned: pd.Series | None = None
    bench_path = benchmark or (
        bench_cfg.get("path") if bench_cfg.get("enabled", False) else None
    )
    if bench_path:
        try:
            bm = Benchmark(bench_path, rf_annual=float(bench_cfg.get("rf_annual", 0.0)))
            strat_returns = res.set_index("trade_date")["net_return"]
            ab = bm.alpha_beta(strat_returns)
            bench_returns_aligned = bm.aligned_returns(strat_returns)
            excess_dd = bm.excess_max_dd(strat_returns)
            bench_block = {
                "alpha_beta": ab,
                "excess_max_dd": excess_dd,
            }
            click.echo(
                f"\n  [基准 {Path(bench_path).name}] "
                f"α(年化)={ab['alpha_annual']*100:.2f}% β={ab['beta']:.3f}  "
                f"R²={ab['r_squared']:.2f}  IR={ab['info_ratio']:.2f}"
            )
        except Exception as exc:  # noqa: BLE001
            click.secho(f"  ⚠️  基准加载失败: {exc}", fg="yellow")

    # ------------------------------------------------------------------ #
    # 8. 稳健性扫描（可选）
    # ------------------------------------------------------------------ #
    if robustness != "off":
        if robustness == "fast":
            grid = {
                "buy_cost": [0.001, 0.002],
                "sell_cost": [0.002, 0.003],
                "slippage_bps": [0, 10],
            }
        else:
            grid = rob_cfg.get("cost_grid") or {
                "buy_cost": [0.0005, 0.001, 0.002],
                "sell_cost": [0.0015, 0.002, 0.003],
                "slippage_bps": [0, 5, 10],
            }
        click.echo(f"\n  [稳健性] 成本敏感度扫描 ({robustness}, "
                   f"{len(grid['buy_cost'])}×{len(grid['sell_cost'])}×{len(grid['slippage_bps'])} grid)")
        cost_sens = Robustness.cost_sensitivity(
            factor_df, feature_cols, label_col,
            grid=grid,
            engine_kwargs={
                "train_window": bt_cfg.get("train_window", 120),
                "retrain_freq": bt_cfg.get("retrain_freq", 5),
                "top_pct": bt_cfg.get("top_pct", 0.2),
                "min_train": bt_cfg.get("min_train_samples", 300),
                "min_test": bt_cfg.get("min_test_samples", 30),
                "min_stocks": bt_cfg.get("min_stocks_per_day", 10),
                "winsorize": winsorize,
            },
        )
        rob_block = {"cost_sensitivity": cost_sens}
        if bench_returns_aligned is not None:
            regime_split = Robustness.regime_split(
                res, bench_returns_aligned,
                n_regimes=3 if robustness == "full" else 2,
            )
            rob_block["regime_split"] = regime_split

    # ------------------------------------------------------------------ #
    # 9. 绘图
    # ------------------------------------------------------------------ #
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    engine.plot_results(
        res, int(period), str(output_path),
        ic_series=diag_block.get("ic_series") if diag_block else None,
        quantile_df=diag_block.get("quantile_df") if diag_block else None,
        benchmark_returns=bench_returns_aligned,
        ic_decay_df=diag_block.get("ic_decay") if diag_block else None,
    )

    # ------------------------------------------------------------------ #
    # 10. Markdown 报告（可选）
    # ------------------------------------------------------------------ #
    if md_output:
        reporter = BacktestReporter(
            result_df=res,
            diagnostics=diag_block,
            significance=sig_block,
            benchmark=bench_block,
            robustness=rob_block,
            meta={
                "股票池": pool,
                "周期": f"{period}d",
                "特征数": len(feature_cols),
                "训练窗口": bt_cfg.get("train_window", 120),
                "重训频率": bt_cfg.get("retrain_freq", 5),
                "选股比例": f"{bt_cfg.get('top_pct', 0.2):.0%}",
                "Winsorize": winsorize if winsorize else "off",
            },
        )
        reporter.to_markdown(md_output)
        click.echo(f"  📝 Markdown 报告: {md_output}")

    click.secho(f"\n  ✅ 回测完成，图表: {output_path}", fg="green")
    logger.info(
        "Backtest pool=%s period=%s diag=%s bench=%s robust=%s",
        pool, period, diagnostics, bool(bench_path), robustness,
    )
