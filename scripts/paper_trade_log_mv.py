#!/usr/bin/env python3
"""log_mv 反向纸交易日志 —— 每天预测 Top 20% 选股名单 + 累计真实成交差异.

经过 8 轮实验 + Wave 1-3 三波改造，KSS 体系内唯一通过 ``is_deployable`` 门槛
（``strategy_family="prior"``）的策略是 **`log_mv` 反向选股**：
小市值股票每天截面 rank 最低的 20% 等权持有 → Sharpe 1.74（含 execution）/ p=0.025.

本脚本作为该策略的实盘前置：每个交易日开盘前打印买入名单 + 推送，
收盘后记录真实成交结果到 ``storage/paper_trade/YYYY-MM-DD.json``，
累计 30 个交易日后产出"预测 vs 实际"对比.

用法::

    python3 scripts/paper_trade_log_mv.py                      # 单日预测（最新可用日）
    python3 scripts/paper_trade_log_mv.py --date 2026-05-08    # 指定日期
    python3 scripts/paper_trade_log_mv.py --summary            # 累计统计
    python3 scripts/paper_trade_log_mv.py --notify             # 推送（=--channel console）
    python3 scripts/paper_trade_log_mv.py --channel telegram   # 推送至自建 Telegram bot
    python3 scripts/paper_trade_log_mv.py --channel all        # 推送至所有可用通道
    python3 scripts/paper_trade_log_mv.py --query 688322.SH    # 单股查询

部署建议（每个交易日 9:00 cron，需在环境/启动脚本中导出 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID）::

    0 9 * * 1-5 cd /path/to/KSS && python3 scripts/paper_trade_log_mv.py --channel all
"""

from __future__ import annotations

import argparse
import glob
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from kss.backtest.cost_model import ExecutionModel  # noqa: E402
from kss.backtest.metrics import Metrics  # noqa: E402
from kss.features.pipeline import FactorPipeline  # noqa: E402
from kss.notifications import TelegramBot  # noqa: E402
from kss.notifications.console import ConsoleNotifier  # noqa: E402
from kss.prediction.cross_sectional_forecast import CrossSectionalForecast  # noqa: E402

# ---------------------------------------------------------------------- #
# 常量
# ---------------------------------------------------------------------- #

DATA_GLOB = str(PROJECT_ROOT / "cs_data_688*.csv")
LOG_DIR = PROJECT_ROOT / "storage" / "paper_trade"
TOP_PCT = 0.2
MIN_STOCKS = 10
FRESHNESS_DAYS = 7

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------- #
# 数据加载
# ---------------------------------------------------------------------- #


def load_long_panel(min_days: int = 60) -> pd.DataFrame:
    """加载所有科创板股票的多日历史 panel（含 log_mv 因子）.

    返回的 long panel 喂给 :class:`CrossSectionalForecast` 让它自己负责
    取快照 / freshness 过滤.

    Args:
        min_days: 每只股票最少需要多少天数据才纳入；用于过滤新股.

    Returns:
        含 ``trade_date / symbol / log_mv / pre_close / open / close / amount`` 的 DataFrame.
    """
    files = sorted(glob.glob(DATA_GLOB))
    if not files:
        raise FileNotFoundError(f"未找到 {DATA_GLOB}")

    panels: list[pd.DataFrame] = []
    for f in files:
        df = pd.read_csv(f)
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        df = df.sort_values("trade_date").reset_index(drop=True)
        if len(df) < min_days:
            continue
        # 用 FactorPipeline 生成所有因子，只取 log_mv（ValuationFactors 一员）
        fp = FactorPipeline(df)
        factors = fp.generate()
        if "log_mv" not in factors.columns:
            continue
        df["log_mv"] = factors["log_mv"].values
        df["symbol"] = df["ts_code"]
        panels.append(df[["trade_date", "symbol", "log_mv",
                          "pre_close", "open", "close", "amount"]])

    if not panels:
        return pd.DataFrame()
    return pd.concat(panels, ignore_index=True)


# ---------------------------------------------------------------------- #
# 持久化
# ---------------------------------------------------------------------- #


def save_log_entry(
    picks: pd.DataFrame,
    target_date: pd.Timestamp,
    use_execution: bool,
) -> Path:
    """保存当日预测到 JSON 日志（供事后对比）."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    in_top = picks[picks["in_top"]] if "in_top" in picks.columns else picks
    entry = {
        "prediction_date": str(target_date.date()),
        "generated_at": datetime.now().isoformat(),
        "strategy": "log_mv_reverse",
        "use_execution": use_execution,
        "top_pct": TOP_PCT,
        "picks": [
            {
                "symbol": row["symbol"],
                "factor_value": float(row["factor_value"]),
                "rank_pct": float(row["rank_pct"]),
                "rank_position": int(row["rank_position"]),
                "planned_weight": float(row["planned_weight"]),
            }
            for _, row in in_top.iterrows()
        ],
    }
    out = LOG_DIR / f"{target_date.date()}.json"
    out.write_text(json.dumps(entry, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


# ---------------------------------------------------------------------- #
# Summary：累计真实收益
# ---------------------------------------------------------------------- #


def summarize_log_dir(lookback_days: int | None = None) -> dict:
    """读取 LOG_DIR 下所有日志 → 算每日预测组合的 T+1→T+2 实际开盘价收益.

    Args:
        lookback_days: 仅统计最近 N 天日志；``None`` 用全部历史.
    """
    files = sorted(LOG_DIR.glob("*.json"))
    if lookback_days is not None and lookback_days > 0:
        files = files[-lookback_days:]
    if not files:
        return {"n_days": 0, "message": "尚未积累任何预测日志"}

    entries = []
    for f in files:
        try:
            entries.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception as exc:  # noqa: BLE001
            logger.warning("解析 %s 失败: %s", f, exc)

    # 加载所需股票的全样本（用于查 T+1 / T+2 开盘价）
    all_symbols = {p["symbol"] for e in entries for p in e["picks"]}
    all_data: dict[str, pd.DataFrame] = {}
    for sym in all_symbols:
        code = sym.split(".")[0]
        path = PROJECT_ROOT / f"cs_data_{code}.csv"
        if not path.exists():
            continue
        df = pd.read_csv(path)
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        all_data[sym] = df.sort_values("trade_date").reset_index(drop=True)

    real_returns: list[dict] = []
    for e in entries:
        t_date = pd.to_datetime(e["prediction_date"])
        weights_sum = 0.0
        ret_sum = 0.0
        n_filled = 0
        for p in e["picks"]:
            df = all_data.get(p["symbol"])
            if df is None:
                continue
            future = df[df["trade_date"] > t_date].head(2)
            if len(future) < 2:
                continue
            t1_open = future.iloc[0]["open"]
            t2_open = future.iloc[1]["open"]
            r = t2_open / t1_open - 1
            ret_sum += p["planned_weight"] * r
            weights_sum += p["planned_weight"]
            n_filled += 1
        if weights_sum > 0:
            real_returns.append({
                "date": e["prediction_date"],
                "portfolio_return": ret_sum / weights_sum,
                "n_filled": n_filled,
            })

    if not real_returns:
        return {
            "n_days_logged": len(entries),
            "n_days_with_returns": 0,
            "message": "预测已记录但尚无 T+2 数据可计算实际收益",
        }

    rdf = pd.DataFrame(real_returns)
    returns_series = pd.Series(rdf["portfolio_return"].values)
    metrics = Metrics.calc(returns_series)
    return {
        "n_days_logged": len(entries),
        "n_days_with_returns": len(real_returns),
        "sample_period": [rdf["date"].min(), rdf["date"].max()],
        "annualized": metrics.get("annual", float("nan")),
        "sharpe": metrics.get("sharpe", float("nan")),
        "max_dd": metrics.get("max_dd", float("nan")),
        "win_rate": metrics.get("win", float("nan")),
        "avg_daily_return": metrics.get("avg_daily", float("nan")),
    }


# ---------------------------------------------------------------------- #
# 通知通道
# ---------------------------------------------------------------------- #

CHANNEL_CHOICES = ("console", "telegram", "all")


def _build_channels(channel: str) -> list[str]:
    """展开 ``channel`` 参数 → 通道名列表 (``console`` / ``telegram``).

    Args:
        channel: ``console`` / ``telegram`` / ``all`` 之一.

    Returns:
        要发送的通道名列表（保留顺序便于测试断言）.
    """
    if channel == "console":
        return ["console"]
    if channel == "telegram":
        return ["telegram"]
    if channel == "all":
        return ["console", "telegram"]
    return ["console"]  # 兜底（argparse 已限制 choices）


def _send_notification(
    message: str, channel: str, title: str | None = None,
) -> dict[str, bool]:
    """按 ``channel`` 分发推送，cron 友好——任何单一通道异常都不打断后续通道.

    各通道接口差异：

    - ``console`` 走 :class:`ConsoleNotifier` （需要 title），
      title 缺省时用 message 第一行（截断）当 title.
    - ``telegram`` 走 :class:`TelegramBot` ，title 以 ``*title*\\n\\n`` 前置进 message
      正文（Telegram 无单独 title 概念）.

    Args:
        message: 消息正文（推荐 Markdown）.
        channel: ``console`` / ``telegram`` / ``all`` 之一.
        title: 可选标题，console 通道必需；telegram 通道作为 message 前缀.

    Returns:
        ``{channel_name: success_bool}``.
    """
    results: dict[str, bool] = {}
    for ch in _build_channels(channel):
        try:
            if ch == "console":
                n = ConsoleNotifier()
                results[ch] = n.send(
                    title=title or message.splitlines()[0][:60],
                    message=message,
                )
            elif ch == "telegram":
                bot = TelegramBot()
                full = f"*{title}*\n\n{message}" if title else message
                ok = bot.send(full)
                if not ok:
                    logger.error("通道 telegram 发送失败（返回 False）")
                results[ch] = ok
        except Exception as exc:  # noqa: BLE001 —— cron 友好：永不外抛
            logger.error("通道 %s 发送异常：%s", ch, exc)
            results[ch] = False
    return results


# ---------------------------------------------------------------------- #
# Main
# ---------------------------------------------------------------------- #


def main() -> None:
    parser = argparse.ArgumentParser(description="log_mv 反向纸交易日志")
    parser.add_argument("--date", type=str, default=None, help="目标日期 (YYYY-MM-DD)")
    parser.add_argument("--no-execution", action="store_true", help="禁用 ExecutionModel")
    parser.add_argument("--summary", action="store_true", help="累计统计模式")
    parser.add_argument(
        "--lookback", type=int, default=None,
        help="summary 模式下仅统计最近 N 天日志（默认全部历史）",
    )
    parser.add_argument(
        "--alert", action="store_true",
        help="summary 模式下启用衰减告警：Sharpe < BASELINE_SHARPE * 50%% 时退出码 != 0",
    )
    parser.add_argument(
        "--baseline-sharpe", type=float, default=1.74,
        help="历史基线 Sharpe（默认 1.74，log_mv 反向 + execution 全样本）",
    )
    parser.add_argument(
        "--notify", action="store_true",
        help="启用推送（向后兼容，等价于 --channel console；与 --channel 同时给出则后者优先）",
    )
    parser.add_argument(
        "--channel", type=str, default=None, choices=CHANNEL_CHOICES,
        help="推送通道：console（默认）/ telegram / all。给出此参数即视为启用推送。",
    )
    parser.add_argument(
        "--query", type=str, default=None,
        help="单股查询模式（例：688322.SH）",
    )
    args = parser.parse_args()

    # ===== 决定推送通道 ===== #
    # 兼容旧 --notify：未指定 --channel 时默认 console；显式 --channel 优先。
    if args.channel is not None:
        notify_channel: str | None = args.channel
    elif args.notify:
        notify_channel = "console"
    else:
        notify_channel = None

    # ===== summary 模式 ===== #
    if args.summary:
        summary = summarize_log_dir(lookback_days=args.lookback)

        # 健康检查：与基线 Sharpe 对比，衰减 ≥ 50% → 返回 alert
        if args.alert and "sharpe" in summary:
            recent_sharpe = float(summary.get("sharpe", 0.0))
            threshold = args.baseline_sharpe * 0.5
            summary["alert"] = {
                "baseline_sharpe": args.baseline_sharpe,
                "recent_sharpe": recent_sharpe,
                "threshold": threshold,
                "triggered": recent_sharpe < threshold,
            }
            print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
            if summary["alert"]["triggered"]:
                logger.warning(
                    "ALERT: 近期 Sharpe %.2f < 基线 %.2f × 50%% = %.2f，策略可能衰减",
                    recent_sharpe, args.baseline_sharpe, threshold,
                )
                sys.exit(2)  # 非零退出码让 cron 能识别
            return
        print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
        return

    # ===== 加载数据 ===== #
    panel = load_long_panel()
    if panel.empty:
        logger.error("无可用数据")
        sys.exit(1)

    target_date = pd.to_datetime(args.date) if args.date else None
    execution = (
        None if args.no_execution else
        ExecutionModel(kcb_limit_pct=0.20, max_volume_pct=0.05, open_slippage_bps=10)
    )
    forecast = CrossSectionalForecast(
        factor_col="log_mv",
        direction="long_low",
        top_pct=TOP_PCT,
        execution=execution,
        freshness_days=FRESHNESS_DAYS,
        min_stocks=MIN_STOCKS,
    )

    # ===== 单股查询模式 ===== #
    if args.query:
        info = forecast.predict_single(panel, args.query, target_date=target_date)
        md = forecast.format_single_markdown(info)
        print(md)
        if notify_channel:
            _send_notification(
                message=md,
                channel=notify_channel,
                title=f"log_mv 截面查询 {args.query}",
            )
        return

    # ===== 整池预测 + 落盘 ===== #
    pool = forecast.predict_pool(panel, target_date=target_date)
    if pool.empty:
        logger.error("无可选股票（min_stocks 不足或全被涨停过滤）")
        sys.exit(1)
    actual_date = pd.Timestamp(pool.attrs["target_date"])

    md = forecast.format_pool_markdown(pool)
    log_path = save_log_entry(pool, actual_date, use_execution=not args.no_execution)
    print(md)
    print(f"\n📝 日志已保存: {log_path}")

    if notify_channel:
        _send_notification(
            message=md,
            channel=notify_channel,
            title=f"log_mv 选股 {actual_date.date()}",
        )


if __name__ == "__main__":
    main()
