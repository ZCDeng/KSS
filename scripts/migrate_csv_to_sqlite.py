"""一次性把根目录所有 cs_data_*.csv + index_*.csv / cs_index_*.csv 迁移到 SQLite.

用法::

    python3 scripts/migrate_csv_to_sqlite.py
    # 默认输出: storage/kss_quotes.db

    # 自定义路径
    python3 scripts/migrate_csv_to_sqlite.py --db storage/foo.db --root .

    # 干跑（只统计，不写入）
    python3 scripts/migrate_csv_to_sqlite.py --dry-run

迁移完不删 CSV，作为 fallback / 灾备保留.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Iterable

import pandas as pd

# 允许从仓库根执行：``python3 scripts/migrate_csv_to_sqlite.py``
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kss.data.sqlite_store import SQLiteStore  # noqa: E402


# ---------------------------------------------------------------------------
# tqdm 兜底：未装也能跑
# ---------------------------------------------------------------------------

try:  # pragma: no cover - 取决于环境
    from tqdm import tqdm

    def _iter(seq: Iterable, desc: str):
        return tqdm(list(seq), desc=desc)

except ImportError:  # pragma: no cover

    def _iter(seq: Iterable, desc: str):
        items = list(seq)
        total = len(items)
        for i, x in enumerate(items, 1):
            if i == 1 or i == total or i % 10 == 0:
                print(f"  [{desc}] {i}/{total}", flush=True)
            yield x


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def _discover_files(root: Path) -> tuple[list[Path], list[Path]]:
    stock_files = sorted(root.glob("cs_data_*.csv"))
    index_files = sorted(set(list(root.glob("index_*.csv")) + list(root.glob("cs_index_*.csv"))))
    return stock_files, index_files


def _load_csv(path: Path) -> pd.DataFrame | None:
    try:
        return pd.read_csv(path)
    except Exception as exc:  # pragma: no cover
        print(f"  [skip] {path.name}: {exc}", file=sys.stderr)
        return None


def migrate(
    root: Path,
    db_path: Path,
    dry_run: bool = False,
) -> dict:
    stock_files, index_files = _discover_files(root)
    print(
        f"发现 {len(stock_files)} 个股票 CSV、{len(index_files)} 个指数 CSV "
        f"(root={root})"
    )

    if dry_run:
        print("[dry-run] 仅扫描，不写入 SQLite")
        return {
            "n_stock_files": len(stock_files),
            "n_index_files": len(index_files),
            "dry_run": True,
        }

    store = SQLiteStore(db_path)

    total_stock_rows = 0
    for path in _iter(stock_files, "stocks"):
        df = _load_csv(path)
        if df is None or df.empty:
            continue
        # 文件名形如 cs_data_688017.csv → 不携带交易所后缀；
        # CSV 内部 ts_code 列已带完整代码，所以不强制传入 ts_code
        total_stock_rows += store.save_stock(df)

    total_index_rows = 0
    for path in _iter(index_files, "indices"):
        df = _load_csv(path)
        if df is None or df.empty:
            continue
        total_index_rows += store.save_index(df)

    stats = store.stats()
    stats["total_stock_rows_written"] = total_stock_rows
    stats["total_index_rows_written"] = total_index_rows
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help="CSV 所在目录（默认仓库根）",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=ROOT / "storage" / "kss_quotes.db",
        help="目标 SQLite 路径（默认 storage/kss_quotes.db）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只扫描文件并打印计数，不写入",
    )
    args = parser.parse_args()

    t0 = time.time()
    stats = migrate(args.root, args.db, dry_run=args.dry_run)
    elapsed = time.time() - t0

    print("\n=== 迁移结果 ===")
    if args.dry_run:
        print(f"  股票 CSV: {stats['n_stock_files']}")
        print(f"  指数 CSV: {stats['n_index_files']}")
    else:
        print(f"  db 路径     : {args.db}")
        print(f"  db 大小     : {stats['db_size_mb']} MB")
        print(f"  股票数      : {stats['n_stocks']}")
        print(f"  指数数      : {stats['n_indices']}")
        print(f"  股票行数    : {stats['n_stock_rows']}")
        print(f"  指数行数    : {stats['n_index_rows']}")
        print(f"  日期范围    : {stats['date_range'][0]} → {stats['date_range'][1]}")
    print(f"  耗时        : {elapsed:.2f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
