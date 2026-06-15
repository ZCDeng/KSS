"""U1 冒烟：下载 base checkpoint + tokenizer，在一只真实 A 股 K 线上跑通预测.

非单测（需联网拉 HuggingFace 权重）；CI 不跑，运营/首装时手动验证：

    python scripts/kronos_smoke.py [cs_data_xxxxxx.csv]

成功标准：base checkpoint 能 from_pretrained 加载、KronosPredictor.predict 在
样例 OHLCV 上产出 pred_len 行 open/high/low/close/volume/amount。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from kss.kronos import load_vendor  # noqa: E402
from kss.kronos.config import (  # noqa: E402
    BASE_MODEL_REPO,
    BASE_TOKENIZER_REPO,
    INFER_LOOKBACK_BARS,
    MAX_CONTEXT,
    PREDICT_LEN,
)


def main() -> int:
    Kronos, KronosTokenizer, KronosPredictor = load_vendor()

    csv = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("cs_data_300002.csv")
    if not csv.exists():
        print(f"找不到样例 CSV: {csv}")
        return 1

    print(f"加载 tokenizer: {BASE_TOKENIZER_REPO}")
    tokenizer = KronosTokenizer.from_pretrained(BASE_TOKENIZER_REPO)
    print(f"加载 model: {BASE_MODEL_REPO}")
    model = Kronos.from_pretrained(BASE_MODEL_REPO)
    predictor = KronosPredictor(model, tokenizer, max_context=MAX_CONTEXT)

    df = pd.read_csv(csv)
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df = df.sort_values("trade_date").reset_index(drop=True)
    # KSS cs_data 用 vol；Kronos 用 volume。
    df = df.rename(columns={"vol": "volume"})

    lookback = min(INFER_LOOKBACK_BARS, len(df) - PREDICT_LEN)
    if lookback < 10:
        print(f"样本不足: 只有 {len(df)} 行")
        return 1

    x_df = df.loc[: lookback - 1, ["open", "high", "low", "close", "volume", "amount"]]
    x_ts = df.loc[: lookback - 1, "trade_date"]
    # 用真实未来日期当 y_timestamp（仅供 calc_time_stamps 取日历特征）。
    y_ts = df.loc[lookback : lookback + PREDICT_LEN - 1, "trade_date"]

    pred = predictor.predict(
        df=x_df, x_timestamp=x_ts, y_timestamp=y_ts, pred_len=PREDICT_LEN,
        T=1.0, top_p=0.9, sample_count=1, verbose=True,
    )
    print("预测结果：")
    print(pred)
    ok = list(pred.columns) == ["open", "high", "low", "close", "volume", "amount"] and len(pred) == PREDICT_LEN
    print("SMOKE PASS" if ok else "SMOKE FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
