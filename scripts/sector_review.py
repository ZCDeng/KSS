#!/usr/bin/env python3
"""收盘后板块复盘命令行入口.

每个交易日 17:30 cron 调用，LLM 生成投顾语气复盘文字，产出 6 段结构：

1. <b>大盘总览</b>（沪深 / 创业板 / 科创板 量价）
2. <b>热点行业</b>
3. <b>概念轮动</b>
4. <b>科技板块资金走向</b>
5. <b>「十五五」六大主题</b>
6. <b>加减仓建议</b>

LLM 失败时自动降级为结构化纯文本，不静默失败.

用法::

    python3 scripts/sector_review.py                          # 今日复盘，console 通道
    python3 scripts/sector_review.py --date 2026-05-12        # 指定日期
    python3 scripts/sector_review.py --channel telegram       # 推 Telegram
    python3 scripts/sector_review.py --channel all            # console + telegram
    python3 scripts/sector_review.py --dry-run                # 仅 print，不推送

lauchd / cron 部署（每个交易日 17:30 收盘后）::

    30 17 * * 1-5 /path/to/KSS/scripts/run_sector_review_daily.sh
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

# 雷达存档目录 —— 预测校验周报 (validate_predictions.py) 的审计底稿.
# first-write-wins: 已存在不覆盖 (复刻 daily_review 的 dry-run 改写存档教训)
RADAR_ARCHIVE_DIR = PROJECT_ROOT / "storage" / "etf_radar"

from kss.data.tushare_client import TushareClient  # noqa: E402
from kss.notifications.manager import (  # noqa: E402
    CHANNEL_CHOICES,
    send_to_channels,
)
from kss.sector.commentary import generate_commentary  # noqa: E402
from kss.sector.data_fetcher import (  # noqa: E402
    fetch_market_indices,
    load_sector_snapshot,
)
from kss.sector.etf_radar import build_etf_radar  # noqa: E402
from kss.sector.kcb_overlay import build_kcb_overlay  # noqa: E402
from kss.sector.momentum_regime import build_regime_status  # noqa: E402
from kss.sector.themes import load_themes  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _today_yyyymmdd() -> str:
    """返回今日日期，``YYYYMMDD`` 格式."""
    return datetime.now().strftime("%Y%m%d")


def _fmt_display_date(yyyymmdd: str) -> str:
    """``20260512`` → ``2026-05-12``."""
    try:
        return datetime.strptime(yyyymmdd, "%Y%m%d").strftime("%Y-%m-%d")
    except ValueError:
        return yyyymmdd


def _archive_radar(trade_date: str, payload: dict) -> None:
    """雷达 payload 落盘 ``storage/etf_radar/YYYYMMDD.json``.

    first-write-wins: 存档是校验底稿, 回放旧日期不得改写当时发布的内容.
    """
    RADAR_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    path = RADAR_ARCHIVE_DIR / f"{trade_date}.json"
    if path.exists():
        logger.info("雷达存档已存在, 跳过: %s", path)
        return
    payload = {"trade_date": trade_date, "source": "live", **payload}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                    encoding="utf-8")
    logger.info("雷达存档: %s", path)


def run_review(
    trade_date: str,
    client: TushareClient | None = None,
    llm_client: object | None = None,
) -> tuple[str, list[str]]:
    """核心复盘流程：拉数据 → LLM 投顾文字.

    Args:
        trade_date: 目标交易日 ``YYYYMMDD``.
        client: TushareClient 实例；``None`` 走单例.
        llm_client: LLMClient 实例（或 mock）；``None`` 时 ``generate_commentary``
            内部 lazy 初始化.

    Returns:
        ``(commentary_text, missing_sources)``.
    """
    if client is None:
        client = TushareClient()

    # ---- 数据装载 ----
    snap = load_sector_snapshot(trade_date, client=client)
    indices = fetch_market_indices(trade_date, client=client)
    overlay = build_kcb_overlay()
    themes = load_themes()
    # R3 动量 regime + ETF 份额调仓雷达 (数据日期通常 T-1,
    # 失败 → None, commentary 计入 missing; regime 失败不阻塞雷达)
    regime = build_regime_status(trade_date, client=client)
    etf_radar = build_etf_radar(trade_date, client=client, regime=regime)
    if etf_radar is not None:
        _archive_radar(trade_date, etf_radar.to_payload())

    # ---- LLM commentary ----
    commentary = generate_commentary(
        date_yyyymmdd=trade_date,
        snapshot=snap,
        indices=indices,
        themes=themes,
        overlay=overlay,
        client=llm_client,
        etf_radar=etf_radar,
    )
    _archive_commentary(trade_date, commentary)
    return commentary, snap.missing


def _archive_commentary(trade_date: str, commentary: str) -> None:
    """投顾点评落盘 ``storage/etf_radar/YYYYMMDD.commentary.md``（桌面端复盘读取）。

    first-write-wins：与雷达存档一致，回放旧日期不覆盖当时发布内容。
    """
    if not commentary:
        return
    RADAR_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    path = RADAR_ARCHIVE_DIR / f"{trade_date}.commentary.md"
    if path.exists():
        logger.info("点评存档已存在, 跳过: %s", path)
        return
    path.write_text(commentary, encoding="utf-8")
    logger.info("点评存档: %s", path)


def main() -> None:
    parser = argparse.ArgumentParser(description="收盘后板块复盘 —— LLM 投顾文字")
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
        help="只打印，跳过推送（即便 --channel 指定 telegram）",
    )
    args = parser.parse_args()

    # 日期统一为 YYYYMMDD
    if args.date:
        try:
            trade_date = datetime.strptime(args.date, "%Y-%m-%d").strftime("%Y%m%d")
        except ValueError:
            trade_date = args.date  # 兼容直接输入 YYYYMMDD
    else:
        trade_date = _today_yyyymmdd()

    commentary, missing = run_review(trade_date=trade_date)
    print(commentary)

    if args.dry_run:
        logger.info("[dry-run] 跳过推送")
        return

    if args.channel in ("console", "all", "telegram"):
        results = send_to_channels(
            message=commentary,
            channel=args.channel,
            title=f"板块复盘 {_fmt_display_date(trade_date)}",
            parse_mode="HTML",
        )
        logger.info("推送结果: %s", results)
        if not all(results.values()):
            sys.exit(2)

    # 所有数据源都缺失 → 退出码告警
    if len(missing) >= 4:
        logger.warning("所有数据源都缺失，可能 Tushare 当日未准备好")
        sys.exit(3)


if __name__ == "__main__":
    main()
