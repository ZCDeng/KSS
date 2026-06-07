"""ETF 份额调仓雷达 —— 配置盘主题间相对申赎观察.

数据源: Tushare ``fund_share`` (日度 ETF 份额). 经 2026-06-07 校验
(docs/solutions/daily_review_prediction_validation.md 同期工作):

- ETF 申赎与当日涨跌同期相关 -0.81 (跌买涨卖, 结构性逆向),
  对次日方向无预测力 (lead-1 corr≈-0.19) —— **绝对申赎不做方向信号**.
- 有信息量的是两件事: ① 主题间相对流差 (谁被持续赎回 / 谁获净配置);
  ② 单日申赎加速 (兑现盘加速 / 底部接货).

口径注意:

- ``fd_share`` T 日数据 T+1 早间可得; 17:30 复盘当天最新行通常是 T-1,
  输出里显式带 ``data_date``, 不假装是当日数据.
- 份额缩 ≠ 资金看空: 上涨段持续赎回是获利了结常态. 解读语义由
  commentary prompt 约束, 本模块只产出确定性数字 (判断类任务不进代码).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import pandas as pd

from kss.data.tushare_client import TushareClient

logger = logging.getLogger(__name__)

# 主题 → 旗舰 ETF (上市早 / 份额大 / 流动性好; 2026-06-07 验证过的 10 只).
# 份额加权聚合, 单只缺数不影响主题内其他只.
ETF_BASKET: dict[str, list[tuple[str, str]]] = {
    "科创50": [("588000.SH", "科创50ETF华夏"), ("588080.SH", "科创50ETF易方达")],
    "科创芯片": [("588200.SH", "科创芯片ETF嘉实")],
    "芯片": [("159995.SZ", "芯片ETF华夏"), ("512760.SH", "芯片ETF国泰")],
    "半导体": [("512480.SH", "半导体ETF国联安")],
    "机器人": [("562500.SH", "机器人ETF华夏"), ("159770.SZ", "机器人ETF天弘")],
    "人工智能": [("515070.SH", "人工智能ETF华夏"), ("159819.SZ", "人工智能ETF易方达")],
}

# 拉取窗口: 5 日流需要 ≥6 个交易日, 放 20 个自然日覆盖节假日.
_WINDOW_CALENDAR_DAYS = 20

# 单日主题加权申赎超过该阈值视为「加速」(验证期日常波动 ±1% 内).
_ACCEL_THRESHOLD_PCT = 1.5


@dataclass
class EtfRadar:
    """单日 ETF 调仓雷达.

    Attributes:
        data_date: 份额数据实际日期 (``YYYYMMDD``); 通常为 T-1.
        stale: 数据日期早于上一交易日 (落后超过正常 T+1 披露节奏).
        themes: 主题 → 指标 dict:
            ``flow_1d`` / ``flow_5d``: 份额加权申赎 % (正=净申购);
            ``rank_5d``: 5 日流主题间排名 (1=最获配置);
            ``accel``: ``|flow_1d|`` 超过加速阈值;
            ``n_funds``: 实际取到数据的 ETF 只数.
        missing_funds: 拉取失败的 ETF 代码列表.
    """

    data_date: str
    stale: bool = False
    themes: dict[str, dict[str, Any]] = field(default_factory=dict)
    missing_funds: list[str] = field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        """序列化为 LLM prompt payload (紧凑、只含确定性数字)."""
        return {
            "data_date": self.data_date,
            "stale": self.stale,
            "note": "正=净申购/负=净赎回; 申赎与当日涨跌天然逆向(跌买涨卖), 不构成方向信号; 只解读主题间相对差与加速",
            "themes": self.themes,
        }


def _theme_flows(df: pd.DataFrame) -> tuple[float | None, float | None]:
    """单主题份额加权 1 日 / 5 日申赎 %.

    Args:
        df: 该主题全部 ETF 的 ``trade_date`` × ``fd_share`` 长表 (已按日期升序).

    Returns:
        ``(flow_1d, flow_5d)``; 数据不足时对应值为 ``None``.
    """
    total = df.groupby("trade_date")["fd_share"].sum().sort_index()
    if len(total) < 2:
        return None, None
    flow_1d = (total.iloc[-1] / total.iloc[-2] - 1) * 100
    flow_5d = (total.iloc[-1] / total.iloc[-6] - 1) * 100 if len(total) >= 6 else None
    return float(flow_1d), (float(flow_5d) if flow_5d is not None else None)


def build_etf_radar(
    trade_date: str,
    client: TushareClient | None = None,
) -> EtfRadar | None:
    """构建单日 ETF 调仓雷达.

    Args:
        trade_date: 目标交易日, ``YYYYMMDD``. 实际使用 ≤ trade_date 的最新
            份额数据 (通常 T-1), ``data_date`` 字段如实标注.
        client: ``TushareClient`` 实例; ``None`` 走单例.

    Returns:
        :class:`EtfRadar`; 全部主题都拉不到数据时返回 ``None`` (fail loud,
        调用方应把 ``etf_radar`` 加入 missing 维度).
    """
    if client is None:
        client = TushareClient()

    start = (
        datetime.strptime(trade_date, "%Y%m%d") - timedelta(days=_WINDOW_CALENDAR_DAYS)
    ).strftime("%Y%m%d")

    radar = EtfRadar(data_date="")
    theme_raw: dict[str, pd.DataFrame] = {}
    all_dates: set[str] = set()

    for theme, funds in ETF_BASKET.items():
        frames: list[pd.DataFrame] = []
        for ts_code, _name in funds:
            df = client.fetch_fund_share(ts_code, start, trade_date)
            if df is None or df.empty or "fd_share" not in df.columns:
                radar.missing_funds.append(ts_code)
                continue
            frames.append(df[["trade_date", "fd_share"]])
        if not frames:
            continue
        merged = pd.concat(frames).sort_values("trade_date")
        theme_raw[theme] = merged
        all_dates.update(merged["trade_date"].astype(str))

    if not theme_raw:
        logger.warning("build_etf_radar(%s): 全部 ETF 份额拉取失败", trade_date)
        return None

    radar.data_date = max(all_dates)

    # staleness: 数据日期落后目标日超过 4 个自然日 (跨周末的 T-1 容忍上限)
    lag_days = (
        datetime.strptime(trade_date, "%Y%m%d")
        - datetime.strptime(radar.data_date, "%Y%m%d")
    ).days
    radar.stale = lag_days > 4
    if radar.stale:
        logger.warning(
            "build_etf_radar(%s): 份额数据滞后 %d 天 (data_date=%s)",
            trade_date, lag_days, radar.data_date,
        )

    for theme, merged in theme_raw.items():
        # 截到统一数据日, 避免不同主题末日不齐
        sub = merged[merged["trade_date"].astype(str) <= radar.data_date]
        flow_1d, flow_5d = _theme_flows(sub)
        radar.themes[theme] = {
            "flow_1d": round(flow_1d, 2) if flow_1d is not None else None,
            "flow_5d": round(flow_5d, 2) if flow_5d is not None else None,
            "accel": flow_1d is not None and abs(flow_1d) > _ACCEL_THRESHOLD_PCT,
            "n_funds": int(sub["trade_date"].value_counts().max()),
        }

    # 主题间 5 日流排名 (1 = 最获配置)
    ranked = sorted(
        (t for t, m in radar.themes.items() if m["flow_5d"] is not None),
        key=lambda t: radar.themes[t]["flow_5d"],
        reverse=True,
    )
    for i, theme in enumerate(ranked, start=1):
        radar.themes[theme]["rank_5d"] = i

    if radar.missing_funds:
        logger.warning(
            "build_etf_radar(%s): 部分 ETF 缺数: %s",
            trade_date, radar.missing_funds,
        )
    return radar
