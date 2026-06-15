"""U3 微调 runbook：在 cs_data 上微调 base，产出冻结 artifact（带 D 标记）.

    # 冒烟（少量票 + 小样本，CPU 可跑）
    python scripts/kronos_finetune.py --glob "cs_data_3000*.csv" --max-symbols 5 \
        --max-samples 64 --epochs 1 --lookback 60 --predict 5

    # 全量（有卡环境）
    python scripts/kronos_finetune.py --max-samples 20000 --epochs 3

冻结截断日 D 缺省按 forward_window(60 交易日) 自动推导；--cutoff 显式覆盖。
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from kss.kronos.finetune import finetune  # noqa: E402


def _load_frames(glob: str, max_symbols: int | None) -> dict[str, pd.DataFrame]:
    frames: dict[str, pd.DataFrame] = {}
    for csv in sorted(PROJECT_ROOT.glob(glob)):
        df = pd.read_csv(csv)
        if "trade_date" not in df.columns:
            continue
        symbol = df["ts_code"].iloc[0] if "ts_code" in df.columns else csv.stem
        frames[str(symbol)] = df
        if max_symbols is not None and len(frames) >= max_symbols:
            break
    return frames


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--glob", default="cs_data_*.csv")
    ap.add_argument("--max-symbols", type=int, default=None)
    ap.add_argument("--cutoff", default=None, help="显式 D, YYYY-MM-DD")
    ap.add_argument("--lookback", type=int, default=90)
    ap.add_argument("--predict", type=int, default=10)
    ap.add_argument("--epochs", type=int, default=1)
    ap.add_argument("--batch-size", type=int, default=16)
    ap.add_argument("--lr", type=float, default=2e-5)
    ap.add_argument("--max-samples", type=int, default=512)
    ap.add_argument("--device", default=None)
    args = ap.parse_args()

    frames = _load_frames(args.glob, args.max_symbols)
    if not frames:
        print(f"没有匹配 {args.glob} 的 cs_data")
        return 1
    print(f"加载 {len(frames)} 只票")

    meta = finetune(
        frames,
        explicit_cutoff=args.cutoff,
        lookback_window=args.lookback,
        predict_window=args.predict,
        epochs=args.epochs,
        batch_size=args.batch_size,
        lr=args.lr,
        max_samples=args.max_samples,
        device=args.device,
    )
    print("冻结完成：", meta)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
