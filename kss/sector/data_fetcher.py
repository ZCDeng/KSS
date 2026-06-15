"""板块数据获取层 —— 单日 snapshot + 失败降级.

从 Tushare 拉取行业 / 概念资金流 + 申万指数 + 北向资金，组装成
:class:`SectorSnapshot`。任一单 API 失败时该字段为 ``None`` ，
其他字段不受影响（符合 :mod:`kss.data` 数据层契约）。

设计取舍（U2 实施期决定）：

- 东财行业（``moneyflow_ind_dc``）与申万行业（``sw_daily``）命名空间不一致
  （东财示例：电网设备 / 半导体；申万示例：申万50 / 农林牧渔），不做按名 join.
- 复盘的「行业」字段以东财为准 —— 它在单表内已含 ``pct_change`` +
  ``net_amount_rate`` + ``buy_elg_amount_rate`` 三维资金流指标，正好对应
  热度评分的三因素权重.
- ``sw_daily`` 保留为可选 ``industry_index`` 字段，供后续显示「申万综指今日」
  之类的市场基准用，不参与热度评分.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import pandas as pd

from kss.data.tushare_client import TushareClient
from kss.data.ths_client import fetch_ths_hot
from kss.data.dragon_tiger_client import fetch_dragon_tiger

logger = logging.getLogger(__name__)


# 大盘指数代码 → 中文别名（沪深主板/创业板/科创板，复盘叙述用）.
# 上证综指代表沪市主板（含科创板个股流通），深证成指代表深市主板；创业板指
# 与科创 50 各自代表创业板 / 科创板 单板块表现.
MARKET_INDEX_TS_CODES: dict[str, str] = {
    "000001.SH": "上证主板",
    "399001.SZ": "深证主板",
    "399006.SZ": "创业板",
    "000688.SH": "科创板",
}


@dataclass
class SectorSnapshot:
    """单日板块数据快照.

    Attributes:
        trade_date: 目标交易日，``YYYYMMDD`` 格式.
        industry: 东财行业资金流（已过滤 ``content_type == '行业'``）；
            含 ``name`` / ``pct_change`` / ``net_amount_rate`` / ``buy_elg_amount_rate``.
            失败时为 ``None``.
        concept: 同花顺概念板块资金流；含 ``name`` / ``pct_change`` / ``net_amount``.
            失败时为 ``None``.
        industry_index: 申万指数日线（含 L1/L2/L3 全量，由调用方按 ts_code 过滤）.
            失败时为 ``None`` —— 主复盘流不依赖此字段.
        northbound: 北向资金单日汇总，含 ``north_money`` / ``south_money``（万元）.
            失败时为 ``None``.
        ths_hot: 同花顺当日强势股 + 题材归因（``reason`` 字段），含 ``code`` /
            ``name`` / ``reason`` / ``pct_change``. 用于在板块复盘文中织入
            「今天为什么涨」的题材关键词. 失败时为 ``None``.
        dragon_tiger: 东财当日全市场龙虎榜明细，含 ``code`` / ``name`` /
            ``net_amount``(元) / ``reason``. 聚合后给复盘 prompt 一份席位级
            资金动向. 外部 HTTP 源（非 Tushare），失败时为 ``None``.
        missing: 拉取失败的字段名列表，供报告生成时显示「⚠️ 缺失」提示.
    """

    trade_date: str
    industry: pd.DataFrame | None = None
    concept: pd.DataFrame | None = None
    industry_index: pd.DataFrame | None = None
    northbound: dict[str, float] | None = None
    ths_hot: pd.DataFrame | None = None
    dragon_tiger: pd.DataFrame | None = None
    missing: list[str] = field(default_factory=list)


def _filter_industry_only(raw: pd.DataFrame | None) -> pd.DataFrame | None:
    """从 ``moneyflow_ind_dc`` 原始返回中过滤出 ``content_type == '行业'`` 行.

    Tushare 单日返回 ~1000 行，混合「行业 / 概念 / 地域」三类；复盘报告
    只用「行业」子集（~85 行）.

    Returns:
        过滤后的 DataFrame（重置 index）；输入为 ``None`` / 缺列 / 空时返回 ``None``.
    """
    if raw is None or raw.empty:
        return None
    if "content_type" not in raw.columns:
        logger.warning("moneyflow_ind_dc 缺少 content_type 列，跳过过滤")
        return raw.reset_index(drop=True)
    filtered = raw[raw["content_type"] == "行业"].reset_index(drop=True)
    if filtered.empty:
        logger.warning("moneyflow_ind_dc 过滤后无行业行")
        return None
    return filtered


def _hsgt_row_to_dict(raw: pd.DataFrame | None) -> dict[str, float] | None:
    """北向 API 单日返回 1 行 DataFrame → 转 ``{字段: float}`` 字典.

    Tushare 该接口字段为字符串数字（"405543.48"）需要 cast 为 float.
    """
    if raw is None or raw.empty:
        return None
    row = raw.iloc[0]
    out: dict[str, float] = {}
    for col in ("north_money", "south_money", "hgt", "sgt", "ggt_ss", "ggt_sz"):
        if col in row.index:
            try:
                out[col] = float(row[col])
            except (TypeError, ValueError):
                logger.warning("北向资金字段 %s 无法 cast 为 float: %r", col, row[col])
    return out or None


def load_sector_snapshot(
    trade_date: str,
    client: TushareClient | None = None,
) -> SectorSnapshot:
    """加载单日板块数据快照（4 个 API 并行调用 + 失败降级）.

    Args:
        trade_date: 目标交易日，``YYYYMMDD`` 格式（例如 ``"20260512"``）.
        client: ``TushareClient`` 实例；``None`` 时用默认单例.

    Returns:
        :class:`SectorSnapshot`. 单 API 失败 → 对应字段为 ``None``、
        加入 ``missing`` 列表；其他字段不受影响.
    """
    if client is None:
        client = TushareClient()

    snap = SectorSnapshot(trade_date=trade_date)

    raw_ind = client.fetch_moneyflow_ind_dc(trade_date)
    snap.industry = _filter_industry_only(raw_ind)
    if snap.industry is None:
        snap.missing.append("industry")

    raw_cnt = client.fetch_moneyflow_cnt_ths(trade_date)
    if raw_cnt is None or raw_cnt.empty:
        snap.concept = None
        snap.missing.append("concept")
    else:
        snap.concept = raw_cnt.reset_index(drop=True)

    raw_sw = client.fetch_sw_daily(trade_date)
    if raw_sw is None or raw_sw.empty:
        snap.industry_index = None
        snap.missing.append("industry_index")
    else:
        snap.industry_index = raw_sw.reset_index(drop=True)

    raw_hs = client.fetch_moneyflow_hsgt(trade_date)
    snap.northbound = _hsgt_row_to_dict(raw_hs)
    if snap.northbound is None:
        snap.missing.append("northbound")

    # 同花顺热点是无鉴权 HTTP（非 Tushare），fetcher 自己处理失败 → 返回 None
    snap.ths_hot = fetch_ths_hot(trade_date)
    if snap.ths_hot is None:
        snap.missing.append("ths_hot")

    # 东财龙虎榜同为无鉴权 HTTP；紧挨 ths_hot 串行调用，避免对东财并发触发限流.
    snap.dragon_tiger = fetch_dragon_tiger(trade_date)
    if snap.dragon_tiger is None:
        snap.missing.append("dragon_tiger")

    if snap.missing:
        logger.warning(
            "load_sector_snapshot(%s) 部分数据缺失: %s",
            trade_date, snap.missing,
        )

    return snap


def fetch_market_indices(
    trade_date: str,
    client: TushareClient | None = None,
    *,
    volume_ratio_window: int = 5,
) -> dict[str, dict[str, float]]:
    """获取沪深主板 / 创业板 / 科创板等大盘指数当日量价 + N 日量比.

    用于 17:30 复盘投顾文字「大盘总览」段落。每个指数返回 dict：

    - ``close``: 收盘点位
    - ``pct_chg``: 当日涨跌幅（百分比）
    - ``amount``: 成交额（Tushare 单位：千元）
    - ``volume_ratio``: 量比 = 当日 amount / 过去 N 个交易日均 amount.
      历史窗口数据不足时该字段不出现.

    Args:
        trade_date: 目标交易日，``YYYYMMDD`` 格式.
        client: ``TushareClient`` 实例；``None`` 时用默认单例.
        volume_ratio_window: 量比计算的回看窗口（默认 5 个交易日）.

    Returns:
        指数中文别名 → 量价字典；单个指数拉取失败 → 该 key 缺失，
        其他 key 不受影响. 全部失败返回 ``{}`` 并 log warning.
    """
    if client is None:
        client = TushareClient()

    # 拉 trade_date 前 N+5 个自然日的窗口，覆盖周末 + 节假日.
    end_dt = datetime.strptime(trade_date, "%Y%m%d")
    start_dt = end_dt - timedelta(days=volume_ratio_window * 3 + 7)
    start_str = start_dt.strftime("%Y%m%d")

    out: dict[str, dict[str, float]] = {}
    for ts_code, alias in MARKET_INDEX_TS_CODES.items():
        df = client.fetch_index_daily(ts_code, start_str, trade_date)
        if df is None or df.empty:
            logger.warning("fetch_index_daily %s (%s) 无数据", ts_code, trade_date)
            continue
        # Tushare index_daily 默认按 trade_date 倒序返回；取当日行.
        df = df.sort_values("trade_date", ascending=False).reset_index(drop=True)
        today_rows = df[df["trade_date"] == trade_date]
        if today_rows.empty:
            logger.warning(
                "fetch_index_daily %s 窗口内无 %s 当日数据", ts_code, trade_date,
            )
            continue
        today = today_rows.iloc[0]
        metrics: dict[str, float] = {
            "close": float(today["close"]),
            "pct_chg": float(today["pct_chg"]),
            "amount": float(today["amount"]),
        }
        # 过去 N 日 amount 均值（不含今日）→ 量比.
        prior = df[df["trade_date"] < trade_date].head(volume_ratio_window)
        if len(prior) >= 1:
            avg_amount = float(prior["amount"].mean())
            if avg_amount > 0:
                metrics["volume_ratio"] = metrics["amount"] / avg_amount
        out[alias] = metrics

    if not out:
        logger.warning("fetch_market_indices(%s) 全部指数拉取失败", trade_date)

    return out
