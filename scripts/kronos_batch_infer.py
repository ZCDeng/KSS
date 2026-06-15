"""U4 离线批处理：冻结模型对 universe 前向推理，落 storage/kronos/predictions.sqlite.

只读、与 log_mv 实盘零连接；不进每日实盘 cron 关键路径。

    python scripts/kronos_batch_infer.py --artifact storage/kronos/artifacts/freeze_2026-02-13 \
        --glob "cs_data_*.csv" --max-symbols 50

exit code：有产出 → 0；零产出 → 1（沿用 paper_trade 契约，便于 cron 监控）。
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from kss.kronos.batch_infer import KronosPredictionStore, run_batch  # noqa: E402
from kss.kronos.config import INFER_LOOKBACK_BARS, PREDICT_LEN, SAMPLE_COUNT  # noqa: E402
from kss.kronos.finetune import load_frozen_model  # noqa: E402


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
    ap.add_argument("--artifact", required=True, help="冻结 artifact 目录")
    ap.add_argument("--glob", default="cs_data_*.csv")
    ap.add_argument("--max-symbols", type=int, default=None)
    ap.add_argument("--lookback", type=int, default=INFER_LOOKBACK_BARS)
    ap.add_argument("--predict", type=int, default=PREDICT_LEN)
    ap.add_argument("--mc-samples", type=int, default=SAMPLE_COUNT)
    ap.add_argument("--base-date", default=None, help="推理基准日 YYYY-MM-DD（缺省最新）")
    ap.add_argument("--device", default=None)
    args = ap.parse_args()

    frames = _load_frames(args.glob, args.max_symbols)
    if not frames:
        print(f"没有匹配 {args.glob} 的 cs_data")
        return 1

    predictor, meta = load_frozen_model(args.artifact, device=args.device)
    print(f"加载冻结模型 D={meta.cutoff_date}，universe={len(frames)} 只")

    store = KronosPredictionStore()
    res = run_batch(
        frames, predictor, cutoff=meta.cutoff_date, model_id=Path(args.artifact).name,
        store=store, base_date=args.base_date, lookback_bars=args.lookback,
        pred_len=args.predict, mc_samples=args.mc_samples,
    )
    print(f"产出 {res.n_predictions} 条预测（{res.n_symbols_ok} 票），跳过 {len(res.skipped)} 票")
    return 0 if res.n_predictions > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
