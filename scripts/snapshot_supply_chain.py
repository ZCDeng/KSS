#!/usr/bin/env python3
"""写入紫苏叶 point-in-time 历史快照."""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from kss.supply_chain.history import snapshot_supply_chain_history


def main() -> int:
    """命令行入口."""
    parser = argparse.ArgumentParser(description="写入紫苏叶 point-in-time 历史快照")
    parser.add_argument("--config", default="kss/config/supply_chain.yaml", help="supply_chain.yaml 路径")
    parser.add_argument(
        "--output-root",
        default="storage/research/perilla/point_in_time",
        help="快照输出目录",
    )
    parser.add_argument("--as-of", required=True, help="审计基准日, YYYY-MM-DD")
    parser.add_argument(
        "--observed-at",
        default=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        help="观察时间, 带时区 ISO 日期时间",
    )
    parser.add_argument(
        "--source-ref",
        default=None,
        help="来源引用, 例如 git:<commit>:kss/config/supply_chain.yaml 或 working-tree",
    )
    parser.add_argument(
        "--source-observed-at",
        default=None,
        help="来源自身的观察/提交时间, 带时区 ISO 日期时间",
    )
    args = parser.parse_args()

    path = snapshot_supply_chain_history(
        config=Path(args.config),
        output_root=Path(args.output_root),
        as_of=args.as_of,
        observed_at=args.observed_at,
        source_ref=args.source_ref,
        source_observed_at=args.source_observed_at,
    )
    print(f"已写入紫苏叶 PIT 快照: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
