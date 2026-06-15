"""KSS 日线 → Kronos 输入契约适配 + 可交易性过滤（U2）.

两件事：

1. **输入契约**：把 KSS ``cs_data`` 格式的单票 OHLCV 转成 Kronos
   :meth:`KronosPredictor.predict` 要求的 DataFrame（列
   ``open/high/low/close/volume/amount``）+ ``x_timestamp`` / ``y_timestamp``
   两条 datetime ``pd.Series``（Kronos 内部 ``calc_time_stamps`` 取 ``.dt``
   日历特征，故必须是 Series 而非 DatetimeIndex）。

2. **可交易性过滤**（``Covers R17``）：按 ``log_mv`` 同口径剔除停牌 / ST /
   零成交标的。停牌 / ST 复用 :class:`kss.data.suspension_data.SuspensionData`；
   零成交（``vol<=0`` 或 ``amount<=0``）单独剔——Kronos 归一化会被 0 列污染，
   且零成交即实质不可交易。退市由 survivorship 上游处理（退市股不在 cs_data），
   不在本模块覆盖。

契约违例（缺必需价格列）→ ``ValueError`` fail loud；数据不足（历史短于
lookback）→ 返回 ``None`` + 记录原因（与 :mod:`kss.data` 降级契约一致）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import pandas as pd

logger = logging.getLogger(__name__)

# Kronos predict() 要求的列（注意 KSS 用 vol，Kronos 用 volume）。
KRONOS_PRICE_COLS: list[str] = ["open", "high", "low", "close"]
KRONOS_VOL_COL: str = "volume"
KRONOS_AMT_COL: str = "amount"
KRONOS_INPUT_COLS: list[str] = KRONOS_PRICE_COLS + [KRONOS_VOL_COL, KRONOS_AMT_COL]

# KSS cs_data 必需列（vol 会被改名为 volume；normalize 幂等接受 volume 别名）。
_REQUIRED_PRICE_COLS: tuple[str, ...] = ("trade_date", "open", "high", "low", "close", "amount")


@dataclass(frozen=True)
class KronosInput:
    """单票一次推理的 Kronos 输入三元组.

    Attributes:
        df: 列为 :data:`KRONOS_INPUT_COLS`、长度 = lookback 的历史窗口.
        x_timestamp: 与 ``df`` 对齐的历史时间戳 ``pd.Series``（datetime64）.
        y_timestamp: 被预测 bar 的未来时间戳 ``pd.Series``（datetime64，长度 = pred_len）.
    """

    df: pd.DataFrame
    x_timestamp: pd.Series
    y_timestamp: pd.Series


def normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """把 KSS cs_data 格式规范化为 Kronos 输入列 + 单调递增时间戳.

    Args:
        df: cs_data 单票 DataFrame（至少含 :data:`_REQUIRED_RAW_COLS`）.

    幂等：已规范化（含 ``volume`` 而非 ``vol``）的输入可再次传入而不报错.

    Returns:
        含 ``trade_date``（datetime64，升序）+ :data:`KRONOS_INPUT_COLS` 的副本.

    Raises:
        ValueError: 缺必需价格列或缺成交量列（契约违例，fail loud）.
    """
    missing = [c for c in _REQUIRED_PRICE_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"normalize_ohlcv: 缺必需列 {missing}")
    if "vol" not in df.columns and KRONOS_VOL_COL not in df.columns:
        raise ValueError("normalize_ohlcv: 缺成交量列 vol/volume")

    out = df.copy()
    out["trade_date"] = pd.to_datetime(out["trade_date"])
    out = out.sort_values("trade_date").reset_index(drop=True)
    if "vol" in out.columns:
        out = out.rename(columns={"vol": KRONOS_VOL_COL})
    return out[["trade_date", *KRONOS_INPUT_COLS]]


def next_trading_days(last_date: pd.Timestamp, n: int) -> pd.Series:
    """从 ``last_date`` 之后取 ``n`` 个交易日（工作日近似，含放假误差）.

    用 :func:`pd.bdate_range` 近似交易日，与 :class:`SuspensionData` 一致；
    仅用于喂 Kronos 日历特征（month/day/weekday），不要求精确到节假日。

    Args:
        last_date: 历史窗口最后一个交易日.
        n: 需要的未来交易日数.

    Returns:
        长度 ``n`` 的 datetime ``pd.Series``（不含 ``last_date``）.
    """
    # 多取一段冗余再切片，规避 bdate_range 端点。
    rng = pd.bdate_range(start=pd.Timestamp(last_date) + pd.Timedelta(days=1), periods=n)
    return pd.Series(rng, name="y_timestamp").reset_index(drop=True)


def build_inference_input(
    df: pd.DataFrame,
    *,
    lookback_bars: int,
    pred_len: int,
    y_timestamp: pd.Series | None = None,
    symbol: str | None = None,
) -> KronosInput | None:
    """构造单票一次推理的 Kronos 输入；历史不足或含 NaN 时返回 ``None``.

    Args:
        df: cs_data 单票 DataFrame.
        lookback_bars: 喂模型的历史 bar 数（取末尾 ``lookback_bars`` 行）.
        pred_len: 预测步长.
        y_timestamp: 显式未来时间戳；缺省用 :func:`next_trading_days` 外推.
        symbol: 仅用于日志.

    Returns:
        :class:`KronosInput`；历史不足 / 含 NaN 时返回 ``None`` 并 ``logger.info``.

    Raises:
        ValueError: 缺必需列（经 :func:`normalize_ohlcv`）.
    """
    tag = symbol or "?"
    norm = normalize_ohlcv(df)
    if len(norm) < lookback_bars:
        logger.info("跳过 %s：历史 %d < lookback %d", tag, len(norm), lookback_bars)
        return None

    window = norm.iloc[-lookback_bars:].reset_index(drop=True)
    kdf = window[KRONOS_INPUT_COLS].copy()
    if kdf.isnull().values.any():
        logger.info("跳过 %s：窗口含 NaN", tag)
        return None

    x_timestamp = window["trade_date"].reset_index(drop=True)
    x_timestamp.name = "x_timestamp"

    if y_timestamp is None:
        y_ts = next_trading_days(x_timestamp.iloc[-1], pred_len)
    else:
        y_ts = pd.Series(pd.to_datetime(y_timestamp)).reset_index(drop=True)
        if len(y_ts) != pred_len:
            logger.info("跳过 %s：y_timestamp 长度 %d != pred_len %d", tag, len(y_ts), pred_len)
            return None

    return KronosInput(df=kdf, x_timestamp=x_timestamp, y_timestamp=y_ts)


def filter_tradable(
    panel: pd.DataFrame,
    suspension=None,
    *,
    date_col: str = "trade_date",
    symbol_col: str = "ts_code",
    vol_col: str = "vol",
    amount_col: str = "amount",
    min_amount: float | None = None,
) -> pd.DataFrame:
    """按 ``log_mv`` 同口径剔除停牌 / ST / 零成交行（``Covers R17``）.

    停牌 / ST 经 :meth:`SuspensionData.filter_panel`；零成交（``vol<=0`` 或
    ``amount<=0``）与可选 ``min_amount`` 流动性门槛单独剔。

    Args:
        panel: 含 ``date_col`` / ``symbol_col`` / ``vol_col`` / ``amount_col`` 的面板.
        suspension: :class:`SuspensionData` 实例；``None`` 则跳过停牌 / ST 过滤.
        date_col / symbol_col / vol_col / amount_col: 列名.
        min_amount: 可选最低成交额门槛（与 ``amount`` 同单位，cs_data 为千元）.

    Returns:
        过滤后的 DataFrame（保留原列）；缺成交列 → 仅做停牌 / ST 过滤 + warn.
    """
    if panel is None or len(panel) == 0:
        return panel

    out = panel
    if suspension is not None:
        out = suspension.filter_panel(
            out, date_col=date_col, symbol_col=symbol_col,
            exclude_st=True, exclude_suspended=True,
        )

    if vol_col not in out.columns or amount_col not in out.columns:
        logger.warning("filter_tradable: 缺 %r/%r，跳过零成交过滤", vol_col, amount_col)
        return out

    keep = (out[vol_col] > 0) & (out[amount_col] > 0)
    if min_amount is not None:
        keep &= out[amount_col] >= min_amount
    return out.loc[keep]
