"""时间贴水 *n* 估值标尺 —— P3 of Bolton 周期框架.

把 Bolton《稳中求胜》第 7 章附录 7B 的"时间贴水"公式落成可日频计算的
代码。核心公式::

    S_NWG = E / r              # No-Growth Value: 当期盈利按无风险利率折现
    n = log(S / S_NWG) / log(1+g)   # 需多少年增长把 NWG 长到 S

对于指数：``S = price``, ``E = price / PE``, 所以::

    n = log(PE * r) / log(1+g)

其中 ``r = risk_free + equity_risk_premium`` 是要求回报率，``g`` 是预期
盈利增长率。``n`` 大 = 隐含极长高增长 = 顶部泡沫；``n < 0`` = 当前价低于
无增长价值 = 极度悲观/反转区.

阈值规则（plan §Implementation）：

==========  ===========================
``n`` 区间   combo_scan 行为
==========  ===========================
n > 10      不发 entry，只推送 avoid
5 < n ≤ 10  仓位上限砍半，候选数减至 3
0 ≤ n ≤ 5   默认
-2 ≤ n < 0  加大候选数至 7
n < -2      reversal mode（防御被错杀）
==========  ===========================
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Iterable

import pandas as pd

logger = logging.getLogger(__name__)


# 中国市场默认 equity risk premium（5 年滚动估值的初始锚定值，可配置）
DEFAULT_EQUITY_RISK_PREMIUM = 0.055    # 5.5%
# 当 PE * r ≤ 1 时 n ≤ 0 直接报；当 g ≤ 0 时几何增长无意义，退化为 NaN
_MIN_GROWTH = 1e-4


@dataclass
class ValuationResult:
    """单日估值标尺.

    Attributes:
        trade_date: ``YYYYMMDD``.
        index_code: 指数代码（如 ``000300.SH``）.
        pe: TTM 市盈率（Tushare ``index_dailybasic.pe_ttm``）.
        risk_free: 用于折现的无风险利率（小数，如 10Y 国债 2.5% → 0.025）.
        equity_premium: 股权风险溢价（小数）.
        growth_g: 预期盈利增长率（小数）.
        n_years: 隐含时间贴水年数；公式不可解时为 ``None``.
        stage_rule: 阈值规则名 ``bubble``/``hot``/``normal``/``cool``/``reversal``.
    """

    trade_date: str
    index_code: str
    pe: float | None
    risk_free: float
    equity_premium: float
    growth_g: float
    n_years: float | None
    stage_rule: str


def compute_time_premium(
    pe: float,
    risk_free_rate: float,
    growth_rate: float,
    equity_premium: float = DEFAULT_EQUITY_RISK_PREMIUM,
) -> float | None:
    """估值时间贴水 n（年数）.

    公式::

        n = log(PE * (risk_free + equity_premium)) / log(1 + growth_rate)

    Args:
        pe: TTM PE.
        risk_free_rate: 无风险利率（小数，如 0.025）.
        growth_rate: 预期盈利增长率（小数）.
        equity_premium: 股权风险溢价（小数）.

    Returns:
        ``n`` 年数；``pe`` 或 ``growth_rate`` 不合法时 ``None``.

        - ``pe <= 0`` / ``r <= 0`` / ``g <= 0`` → ``None``（公式定义域外）
        - ``PE * r >= 1`` → 正常正值
        - ``PE * r < 1`` → 负值（隐含价低于 NWG，反转区）
    """
    if pe is None or pe <= 0:
        return None
    r = float(risk_free_rate) + float(equity_premium)
    if r <= 0:
        return None
    g = float(growth_rate)
    if g <= _MIN_GROWTH:
        return None
    num = pe * r
    if num <= 0:
        return None
    return math.log(num) / math.log(1.0 + g)


def compute_n_percentile(
    n_history: pd.Series | Iterable[float],
    current_n: float,
    window: int = 1260,
) -> float | None:
    """当前 n 在最近 ``window`` 个交易日（≈5 年）历史里的分位.

    Args:
        n_history: 历史 ``n`` 序列（按 trade_date 升序）.
        current_n: 当日 n.
        window: 回看交易日数；默认 1260 ≈ 5 年.

    Returns:
        分位（0-1），``current_n`` 越大分位越高；样本不足或缺值 ``None``.
    """
    if current_n is None:
        return None
    s = pd.Series(n_history).dropna()
    if s.empty:
        return None
    tail = s.tail(window)
    if tail.empty:
        return None
    # 分位 = rank position of current_n within sorted tail
    rank = float((tail <= current_n).sum()) / float(len(tail))
    return rank


def stage_rule_for_n(n: float | None) -> str:
    """把 n 映射到 combo_scan 行为规则名.

    规则名是字符串 token，由调用方自行查表决定行为参数（保持 valuation 模块
    纯计算无副作用）.

    Returns:
        ``"bubble"``  if ``n > 10``
        ``"hot"``     if ``5 < n ≤ 10``
        ``"normal"``  if ``0 ≤ n ≤ 5``  / ``n`` 缺失
        ``"cool"``    if ``-2 ≤ n < 0``
        ``"reversal"``if ``n < -2``
    """
    if n is None:
        return "normal"
    if n > 10:
        return "bubble"
    if n > 5:
        return "hot"
    if n >= 0:
        return "normal"
    if n >= -2:
        return "cool"
    return "reversal"


def modulate_entry_count(rule: str, requested: int) -> int:
    """按估值规则调整 entry 候选数.

    与 :func:`scan_combo_signals._modulate_entry_count`（按 regime stage）独立
    且互补；最终生效值取两者较小者（在 scanner 内显式 ``min(...)``）.
    """
    if rule == "bubble":
        return 0
    if rule == "hot":
        return max(1, requested // 2)
    if rule == "cool":
        return min(7, max(requested, requested + 2))
    if rule == "reversal":
        # reversal 模式不发普通 entry，由调用方走单独 watchlist
        return 0
    return requested


def compute_hs300_n_from_panel(
    pe_panel: pd.DataFrame,
    rates_panel: pd.DataFrame,
    growth_rate: float,
    equity_premium: float = DEFAULT_EQUITY_RISK_PREMIUM,
    pe_col: str = "pe_ttm",
    rf_col: str = "yld_10y",
) -> pd.DataFrame:
    """对 PE / 利率 panel 全量算 n（向量化，回填 / 大量批量用）.

    Args:
        pe_panel: 含 ``trade_date`` + ``pe_col`` 的日频 DataFrame.
        rates_panel: 含 ``trade_date`` + ``rf_col`` 的日频 DataFrame（rf_col
            假定单位是**百分点**，如 2.5 表 2.5%；会先 /100 转小数）.
        growth_rate: 预期盈利增长率（小数）.
        equity_premium: ERP.
        pe_col / rf_col: 列名.

    Returns:
        DataFrame 含 ``trade_date`` / ``pe`` / ``risk_free`` / ``n_years``.
    """
    if pe_panel is None or rates_panel is None or pe_panel.empty or rates_panel.empty:
        return pd.DataFrame(columns=["trade_date", "pe", "risk_free", "n_years"])

    pe = pe_panel[["trade_date", pe_col]].copy()
    pe["trade_date"] = pe["trade_date"].astype(str)
    rf = rates_panel[["trade_date", rf_col]].copy()
    rf["trade_date"] = rf["trade_date"].astype(str)

    merged = pe.merge(rf, on="trade_date", how="inner").sort_values("trade_date")
    if merged.empty:
        return pd.DataFrame(columns=["trade_date", "pe", "risk_free", "n_years"])

    merged["risk_free"] = merged[rf_col] / 100.0     # pp → decimal
    merged["n_years"] = [
        compute_time_premium(
            pe=float(row[pe_col]) if pd.notna(row[pe_col]) else None,
            risk_free_rate=float(row["risk_free"]) if pd.notna(row["risk_free"]) else 0.0,
            growth_rate=growth_rate,
            equity_premium=equity_premium,
        )
        for _, row in merged.iterrows()
    ]
    merged["pe"] = merged[pe_col]
    return merged[["trade_date", "pe", "risk_free", "n_years"]].reset_index(drop=True)
