#!/usr/bin/env python3
"""舆情热点 digest 生成入口(plan U10)。

cron wrapper 调本脚本。**先探活采集后端**:不可用则告警 + 退出非零(让 cron 系统监控
接管),不空跑。可用 → 跑全链(采集→隔离→去重→集中度→受约束情绪/催化→关联标的→
渲染→归档),写 ``storage/news_digest/{date}_{scene}.md``。

采集后端 2026-08-15 由 seek MCP(已下线)换成本机 ``combosearch`` CLI,见
``kss.research.combosearch_client``。

用法:
  python scripts/run_news_digest.py --scene 盘前
  python scripts/run_news_digest.py --scene 盘后 --date 20260629
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))  # 仓库根 → import kss.*
sys.path.insert(0, str(_HERE))         # scripts → import kss_app_bridge

from kss.research import combosearch_client  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成舆情热点 digest")
    parser.add_argument("--scene", default="盘前", choices=["盘前", "盘后"], help="场次")
    parser.add_argument("--date", default=None, help="YYYYMMDD,缺省=今天")
    parser.add_argument("--no-mapping", action="store_true", help="不挂关联标的(两段形态)")
    args = parser.parse_args(argv)

    # 探活:采集后端不可用 → 告警 + 退出非零,不空跑(R2 运营依赖)。
    if not combosearch_client.is_alive():
        print(
            f"[news-digest] combosearch CLI 不可用({combosearch_client._bin()}),"
            f"该场({args.scene})降级跳过。装/修 CLI 后重试,或用 KSS_COMBOSEARCH_BIN 指定路径。",
            file=sys.stderr,
        )
        return 1

    from kss.news.digest import run_news_digest

    theme_leaders = None
    if not args.no_mapping:
        try:
            import kss_app_bridge as bridge

            theme_leaders = bridge._theme_leaders()
        except Exception as exc:  # noqa: BLE001 - 取龙头失败不应阻塞 digest,退两段
            print(f"[news-digest] theme-leaders 取数失败,转两段形态: {exc}", file=sys.stderr)
            theme_leaders = None

    digest = run_news_digest(
        args.scene,
        date=args.date,
        theme_leaders=theme_leaders,
        with_mapping=not args.no_mapping,
    )
    print(
        f"[news-digest] {digest['date']} {digest['scene']} 完成:"
        f"方向 {len(digest['directions'])} / 催化 {len(digest['catalysts'])} / "
        f"挂标的={digest['with_mapping']} / 归档={digest['archive_path']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
