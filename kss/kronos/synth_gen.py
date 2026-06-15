"""合成 K 线生成（U6）—— 冻结模型条件化真实历史造对抗 regime.

用冻结模型在真实历史末尾续写，通过高温度 / 宽 top_p 采样逼出对抗路径
（涨跌停连锁、流动性枯竭、板块急跌）。生成结果必须经
:mod:`kss.kronos.microstructure_check` 校验带 A 股微结构后，方可进 U7 诊断。

合成数据严格隔离：只压测、不训练、不当实盘信号（隔离断言见 U7）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

from kss.kronos.adapter import build_inference_input
from kss.kronos.config import INFER_LOOKBACK_BARS, PREDICT_LEN

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SynthScenario:
    """对抗 regime 采样配置（temperature / top_p 越高越极端）."""

    name: str
    temperature: float
    top_p: float


# 三类对抗 regime（计划 U6）。
ADVERSARIAL_SCENARIOS: list[SynthScenario] = [
    SynthScenario("涨跌停连锁", temperature=1.5, top_p=0.99),
    SynthScenario("流动性枯竭", temperature=1.2, top_p=0.95),
    SynthScenario("板块急跌", temperature=1.4, top_p=0.98),
]


def _assemble_frame(seed_last_close: float, pred_df: pd.DataFrame, y_timestamp: pd.Series) -> pd.DataFrame:
    """把 predict() 输出装配成带 pre_close / pct_chg / trade_date 的合成 K 线."""
    close = pred_df["close"].to_numpy(dtype=float)
    pre_close = np.concatenate([[seed_last_close], close[:-1]])
    out = pred_df.reset_index(drop=True).copy()
    out["trade_date"] = pd.to_datetime(pd.Series(y_timestamp).reset_index(drop=True))
    out["pre_close"] = pre_close
    with np.errstate(divide="ignore", invalid="ignore"):
        out["pct_chg"] = np.where(pre_close > 0, close / pre_close - 1.0, 0.0)
    return out


def generate_paths(
    predictor,
    seed_frame: pd.DataFrame,
    symbol: str,
    *,
    scenario: SynthScenario,
    n_paths: int = 5,
    lookback_bars: int = INFER_LOOKBACK_BARS,
    pred_len: int = PREDICT_LEN,
) -> list[pd.DataFrame]:
    """对单只票按某 regime 采样 ``n_paths`` 条合成路径.

    Args:
        predictor: 冻结 KronosPredictor.
        seed_frame: 真实历史 cs_data（条件化用）.
        symbol: ts_code.
        scenario: 采样配置.
        n_paths: 生成路径数.
        lookback_bars / pred_len: 输入窗口与生成步长.

    Returns:
        合成 OHLCV 帧列表（含 pre_close / pct_chg / trade_date）；输入不足 → 空列表.
    """
    ki = build_inference_input(seed_frame, lookback_bars=lookback_bars, pred_len=pred_len, symbol=symbol)
    if ki is None:
        logger.info("跳过 %s：seed 历史不足", symbol)
        return []
    seed_last_close = float(ki.df["close"].iloc[-1])

    paths: list[pd.DataFrame] = []
    for _ in range(n_paths):
        pred = predictor.predict(
            df=ki.df, x_timestamp=ki.x_timestamp, y_timestamp=ki.y_timestamp,
            pred_len=pred_len, T=scenario.temperature, top_p=scenario.top_p,
            sample_count=1, verbose=False,
        )
        paths.append(_assemble_frame(seed_last_close, pred, ki.y_timestamp))
    return paths


def generate_batch(
    predictor,
    seed_frames: dict[str, pd.DataFrame],
    *,
    scenario: SynthScenario,
    n_paths: int = 5,
    lookback_bars: int = INFER_LOOKBACK_BARS,
    pred_len: int = PREDICT_LEN,
) -> list[tuple[str, pd.DataFrame]]:
    """对全 universe 生成一批合成路径，返回 ``[(symbol, frame), ...]``.

    供 :func:`kss.kronos.microstructure_check.validate_batch` 校验后进 U7。
    """
    out: list[tuple[str, pd.DataFrame]] = []
    for symbol, seed in seed_frames.items():
        for i, frame in enumerate(
            generate_paths(
                predictor, seed, symbol, scenario=scenario,
                n_paths=n_paths, lookback_bars=lookback_bars, pred_len=pred_len,
            )
        ):
            out.append((f"{symbol}#{scenario.name}#{i}", frame))
    logger.info("regime=%s 生成 %d 条合成路径", scenario.name, len(out))
    return out
