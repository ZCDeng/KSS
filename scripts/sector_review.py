#!/usr/bin/env python3
"""收盘后板块复盘命令行入口.

每个交易日 17:30 cron 调用，产出一份 markdown 推送：

- 🔥 行业 Top 强势（涨幅 + 主力净流入 + 大单买入率）
- 💰 行业资金涌入（N 日累计 + 连续净流入天数）
- 🎯 概念 Top 强势（同花顺）
- 🔄 轮动信号（排名跃升 + 今日净流入）—— 可选
- 🌍 北向资金单日汇总
- ⚠️ 缺失数据源（如有）

每个板块自动标注 ``⭐N`` 表示在科创板活跃池子中的持仓数（基于
``storage/stock_names.csv`` 的行业 / 概念双维度索引）.

用法::

    python3 scripts/sector_review.py                          # 今日复盘，console 通道
    python3 scripts/sector_review.py --date 2026-05-12        # 指定日期
    python3 scripts/sector_review.py --channel telegram       # 推 Telegram
    python3 scripts/sector_review.py --channel all            # console + telegram
    python3 scripts/sector_review.py --dry-run                # 仅 print，不推送
    python3 scripts/sector_review.py --lookback-days 5        # 持续性回看 5 个交易日

cron 部署（每个交易日 17:30 收盘后）::

    30 17 * * 1-5 /path/to/KSS/scripts/run_sector_review_daily.sh
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from kss.data.tushare_client import TushareClient  # noqa: E402
from kss.notifications.manager import (  # noqa: E402
    CHANNEL_CHOICES,
    send_to_channels,
)
from kss.sector.data_fetcher import (  # noqa: E402
    _filter_industry_only,
    load_sector_snapshot,
)
from kss.sector.formatter import format_review_markdown  # noqa: E402
from kss.sector.kcb_overlay import build_kcb_overlay  # noqa: E402
from kss.sector.scorer import (  # noqa: E402
    compute_flow_persistence,
    compute_heat_score,
    compute_rotation_signal,
    load_config,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _today_yyyymmdd() -> str:
    """返回今日日期，``YYYYMMDD`` 格式（中国时区下的 ``datetime.now()``）."""
    return datetime.now().strftime("%Y%m%d")


def _walk_back_weekdays(end_yyyymmdd: str, n_days: int) -> list[str]:
    """从 ``end_yyyymmdd`` 往回数 ``n_days`` 个工作日（不含 end 本身），返回
    ``YYYYMMDD`` 字符串列表，**最近日在前**.

    简单跳过周末；不识别法定节假日（节假日 Tushare 返回空，自动被
    :func:`_filter_industry_only` 视作 None 过滤）.
    """
    end_date = datetime.strptime(end_yyyymmdd, "%Y%m%d").date()
    out: list[str] = []
    cur = end_date
    while len(out) < n_days:
        cur = cur - timedelta(days=1)
        if cur.weekday() < 5:  # 0=周一 ... 4=周五
            out.append(cur.strftime("%Y%m%d"))
    return out


def _fmt_display_date(yyyymmdd: str) -> str:
    """``20260512`` → ``2026-05-12``."""
    try:
        return datetime.strptime(yyyymmdd, "%Y%m%d").strftime("%Y-%m-%d")
    except ValueError:
        return yyyymmdd


def run_review(
    trade_date: str,
    lookback_days: int = 3,
    config_path: Path | None = None,
    client: TushareClient | None = None,
) -> tuple[str, list[str]]:
    """核心复盘流程：拉数据 → 评分 → 组装 markdown.

    Args:
        trade_date: 目标交易日 ``YYYYMMDD``.
        lookback_days: 资金持续性回看的交易日数.
        config_path: 配置文件路径；``None`` 走默认.
        client: TushareClient 实例；``None`` 走单例.

    Returns:
        ``(markdown, missing_sources)`` —— ``missing_sources`` 含今日缺失的数据源.
    """
    if client is None:
        client = TushareClient()

    config = load_config() if config_path is None else load_config(config_path)

    today_snap = load_sector_snapshot(trade_date, client=client)
    missing = list(today_snap.missing)

    # ---- 历史 industry（仅这一种数据，节省 API 配额）----
    history_industry: list = []
    if today_snap.industry is not None:
        history_industry.append(today_snap.industry)
    past_industry_for_rotation = None

    for idx, past_date in enumerate(
        _walk_back_weekdays(trade_date, lookback_days)
    ):
        raw = client.fetch_moneyflow_ind_dc(past_date)
        past_df = _filter_industry_only(raw)
        if past_df is None:
            logger.info("历史日 %s 无数据，跳过", past_date)
            continue
        history_industry.append(past_df)
        # rotation_signal 用最远的那一天作为对比
        past_industry_for_rotation = past_df

    # history 列表此时是 [today, day-1, day-2, ...]；
    # compute_flow_persistence 期望「旧→新」顺序 → 反转
    history_chrono = list(reversed(history_industry))

    # ---- 评分 ----
    industry_heat = compute_heat_score(
        today_snap.industry, config["industry_heat_weights"],
    ).head(config["top_n_industry"]) if today_snap.industry is not None else None

    concept_heat = compute_heat_score(
        today_snap.concept, config["concept_heat_weights"],
    ).head(config["top_n_concept"]) if today_snap.concept is not None else None

    flow_persist = compute_flow_persistence(history_chrono).head(
        config["top_n_flow"]
    ) if history_chrono else None

    rotation = compute_rotation_signal(
        today_snap.industry,
        past_industry_for_rotation,
        rank_jump_threshold=config["rotation_rank_jump_threshold"],
    ).head(config.get("top_n_rotation", 5)) if (
        today_snap.industry is not None and past_industry_for_rotation is not None
    ) else None

    # ---- KCB overlay ----
    overlay = build_kcb_overlay()

    # ---- 组装 markdown ----
    md = format_review_markdown(
        trade_date=_fmt_display_date(trade_date),
        industry_heat=industry_heat,
        flow_persistence=flow_persist,
        concept_heat=concept_heat,
        northbound=today_snap.northbound,
        overlay=overlay,
        rotation_signal=rotation,
        missing=missing or None,
    )
    return md, missing


def main() -> None:
    parser = argparse.ArgumentParser(description="收盘后板块热度 + 资金轮动复盘")
    parser.add_argument(
        "--date", type=str, default=None,
        help="目标交易日 YYYY-MM-DD（默认今日）",
    )
    parser.add_argument(
        "--channel", type=str, default="console", choices=CHANNEL_CHOICES,
        help="推送通道：console（默认）/ telegram / all",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="只打印 markdown，跳过推送（即便 --channel 指定 telegram）",
    )
    parser.add_argument(
        "--lookback-days", type=int, default=3,
        help="资金持续性回看的交易日数（默认 3）",
    )
    args = parser.parse_args()

    # 日期统一为 YYYYMMDD
    if args.date:
        try:
            trade_date = datetime.strptime(args.date, "%Y-%m-%d").strftime("%Y%m%d")
        except ValueError:
            # 兼容 YYYYMMDD 直接输入
            trade_date = args.date
    else:
        trade_date = _today_yyyymmdd()

    md, missing = run_review(trade_date=trade_date, lookback_days=args.lookback_days)
    print(md)

    if args.dry_run:
        logger.info("[dry-run] 跳过推送")
        return

    if args.channel == "console" or args.channel == "all" or args.channel == "telegram":
        results = send_to_channels(
            message=md,
            channel=args.channel,
            title=f"板块复盘 {_fmt_display_date(trade_date)}",
        )
        logger.info("推送结果: %s", results)
        # 任一通道失败 → 退出码非 0 让 cron 监控可感知
        if not all(results.values()):
            sys.exit(2)

    # 数据全部缺失 → 退出码也告警
    if len(missing) >= 4:
        logger.warning("所有数据源都缺失，可能 Tushare 当日未准备好")
        sys.exit(3)


if __name__ == "__main__":
    main()
