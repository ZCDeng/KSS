#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""开盘前 divergence 快讯 → Telegram (每个交易日 9:00).

时效性补位: 17:30 板块复盘的雷达数据是 T-1, 用户最早 t+2 执行;
晨间用同一份 T-1 数据在开盘前提示, 执行点提前到 t+1 开盘
(一年 106 事件: t+1 执行后5日 -0.59%/下跌胜率 62%).

行为:
  - 构建雷达 (份额+NAV, ~20 个 API 调用) + R3 regime
  - 有 divergence 且该 data_date 未推送过 → 推送快讯
  - 无 divergence → 只 log 不推送 (快讯定位, 不制造噪声)
  - 同一 data_date 不重复推送 (state 文件 storage/etf_radar/.morning_alert_state)
  - 雷达构建失败 → 退出码 1 (fail loud, cron 日志可查)

用法:
  python3 scripts/morning_divergence_alert.py            # console
  python3 scripts/morning_divergence_alert.py --channel all
  python3 scripts/morning_divergence_alert.py --dry-run  # 不推送不写 state
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from kss.data.tushare_client import TushareClient  # noqa: E402
from kss.notifications.manager import CHANNEL_CHOICES, send_to_channels  # noqa: E402
from kss.sector.etf_radar import EtfRadar, build_etf_radar  # noqa: E402
from kss.sector.momentum_regime import build_regime_status  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

STATE_FILE = PROJECT_ROOT / "storage" / "etf_radar" / ".morning_alert_state"


def build_alert_message(radar: EtfRadar) -> str | None:
    """divergence 快讯正文; 无预警返回 None."""
    div = [(t, m) for t, m in radar.themes.items() if m.get("divergence")]
    if not div:
        return None
    lines = [f"⚠️ *见顶预警* (份额数据 {radar.data_date[4:6]}-{radar.data_date[6:]})", ""]
    for theme, m in div:
        lines.append(
            f"  · *{theme}*: 5日 {m['past5_ret']:+.1f}% 上涨中, "
            f"申赎转正 {m['flow_5d']:+.2f}%")
    lines += ["", "历史此形态后 5 日均值 -0.6%, 上涨胜率仅 38%"]
    reg = radar.regime
    if reg is not None and reg.get("in_regime"):
        lines.append("当前处于动量期 — 申购档主题历史胜率仅 28-41%, 风险加重")
    lines += ["", "_开盘前快讯 · 详情见今日 17:30 板块复盘_"]
    return "\n".join(lines)


def already_alerted(data_date: str) -> bool:
    try:
        return STATE_FILE.read_text(encoding="utf-8").strip() == data_date
    except OSError:
        return False


def mark_alerted(data_date: str) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(data_date, encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", default=None, help="目标日 YYYYMMDD (默认今日)")
    ap.add_argument("--channel", choices=CHANNEL_CHOICES, default="console")
    ap.add_argument("--dry-run", action="store_true", help="不推送不写 state")
    args = ap.parse_args()
    trade_date = args.date or datetime.now().strftime("%Y%m%d")

    client = TushareClient()
    regime = build_regime_status(trade_date, client=client)
    radar = build_etf_radar(trade_date, client=client, regime=regime)
    if radar is None:
        logger.error("雷达构建失败 (全部 ETF 缺数), 无法判断 divergence")
        return 1
    if radar.stale:
        logger.warning("份额数据滞后 (data_date=%s), 跳过快讯", radar.data_date)
        return 0

    msg = build_alert_message(radar)
    if msg is None:
        logger.info("无 divergence (data_date=%s), 不推送", radar.data_date)
        return 0
    if already_alerted(radar.data_date):
        logger.info("data_date=%s 已推送过, 跳过", radar.data_date)
        return 0

    print(msg)
    if args.dry_run:
        logger.info("[dry-run] 跳过推送与 state 写入")
        return 0
    results = send_to_channels(
        msg, args.channel,
        title=f"⚠️ 见顶预警 {trade_date[4:6]}-{trade_date[6:]}",
        parse_mode="Markdown",
    )
    ok = results.get("telegram", True) if args.channel in ("telegram", "all") else True
    if not ok:
        logger.error("Telegram 推送失败")
        return 2
    mark_alerted(radar.data_date)
    return 0


if __name__ == "__main__":
    sys.exit(main())
