"""宏观分母端数据客户端 —— 利率 / 货币 / 通胀 / 收益率曲线.

按"分子 E / 分母 r"框架（参见 docs/plans/2026-05-25-001-feat-macro-denominator-feed-plan.md），
本模块拉取经济周期判定所需的分母端原始数据：

- **短端利率**：Shibor 隔夜 / 1W / 1M / 3M（Tushare ``shibor``）
- **长端利率**：中债国债收益率曲线（Tushare ``yc_cb``，含 1Y / 5Y / 10Y）
- **货币供应**：M0 / M1 / M2 月度同比 / 环比（Tushare ``cn_m``）
- **通胀**：CPI / PPI 月度同比（Tushare ``cn_cpi`` / ``cn_ppi``）
- **信用利差**：AA 与 AAA 同期限中债收益率差（AkShare ``bond_china_yield``）

数据层契约（与 :mod:`kss.data` 一致）：

- 所有方法失败返回 ``None`` 而非抛异常
- 重试策略复用 :func:`kss.data.tushare_client._fetch_with_retry`
- Tushare 是主源，AkShare 仅做信用利差的兜底（Tushare 无对应免费接口）
"""

from __future__ import annotations

import logging
from typing import Any, Callable

import pandas as pd

from kss.data.tushare_client import TushareClient, _fetch_with_retry

logger = logging.getLogger(__name__)


# 中债国债收益率曲线 ts_code（Tushare 标准代码）。
_CN_SOVEREIGN_CURVE_TS_CODE: str = "1001.CB"

# curve_type='0' = 即期收益率（spot），'1' = 到期收益率（YTM）。
# 复盘惯例用 YTM（'1'）观察长端走势，与新闻报道口径一致。
_DEFAULT_CURVE_TYPE: str = "1"

# 标准化收益率曲线关键期限（年），P0 阶段固定提取这五点。
_KEY_TERMS: tuple[float, ...] = (0.25, 1.0, 5.0, 10.0, 30.0)


class MacroClient:
    """宏观数据薄封装，复用 :class:`TushareClient` 的 token 解析与重试.

    单例化（同 ``TushareClient`` 的设计）。AkShare 调用按需懒加载，避免对未安装
    场景的 import-time 强依赖。
    """

    _instance: MacroClient | None = None

    def __new__(cls) -> MacroClient:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        # TushareClient 是 singleton，多次实例化无副作用。
        self._tushare = TushareClient()
        self._pro = self._tushare.get_pro()

    # ------------------------------------------------------------------ #
    # Tushare 主源
    # ------------------------------------------------------------------ #

    def fetch_shibor(self, start: str, end: str) -> pd.DataFrame | None:
        """Shibor 利率（``date`` + ``on/1w/2w/1m/3m/6m/9m/1y`` 九列）.

        Args:
            start: 起始日期，``YYYYMMDD``.
            end: 截止日期，``YYYYMMDD``.

        Returns:
            DataFrame 含 ``date`` 列与各期限利率列（百分点）；失败返回 ``None``.
        """
        return _fetch_with_retry(
            lambda: self._pro.query("shibor", start_date=start, end_date=end),
            f"fetch_shibor ({start}~{end})",
        )

    def fetch_cn_yield_curve(
        self,
        start: str,
        end: str,
        curve_type: str = _DEFAULT_CURVE_TYPE,
        chunk_days: int = 1,
        sleep_between: float = 0.3,
    ) -> pd.DataFrame | None:
        """中债国债收益率曲线（长形式，每个交易日 ~1000 行覆盖各期限 × 双 curve_type）.

        **关键限制**：Tushare ``yc_cb`` 单次返回硬上限 2000 行 ≈ 仅 2 个交易日（单日
        约 1010 行包含 type='0'/'1' 双曲线 + 75 期限）。``chunk_days=3`` 横跨周末时
        会被服务端 truncate 到最后 2 个交易日，前面 1 天数据丢失。**默认 chunk_days=1
        是经过验证的安全值**，每片单日历日返回 ≤1010 行远低于 2000 限额。

        Args:
            start: 起始日期，``YYYYMMDD``.
            end: 截止日期，``YYYYMMDD``.
            curve_type: ``'0'`` 即期 / ``'1'`` 到期收益率，默认到期.
            chunk_days: 每片日历天数。默认 1 安全；改大需自行验证不踩 2000 行硬限.
            sleep_between: 片间睡眠秒数，避免触发 Tushare 频率限制.

        Returns:
            DataFrame 含 ``trade_date`` / ``curve_term`` / ``yield``；
            全部片都失败时返回 ``None``；部分片失败保留已成功片.
        """
        from datetime import datetime, timedelta
        import time

        start_dt = datetime.strptime(start, "%Y%m%d")
        end_dt = datetime.strptime(end, "%Y%m%d")
        parts: list[pd.DataFrame] = []
        cur = start_dt
        while cur <= end_dt:
            chunk_end = min(cur + timedelta(days=chunk_days - 1), end_dt)
            cs, ce = cur.strftime("%Y%m%d"), chunk_end.strftime("%Y%m%d")

            def _call(cs=cs, ce=ce) -> pd.DataFrame | None:
                df = self._pro.query(
                    "yc_cb",
                    ts_code=_CN_SOVEREIGN_CURVE_TS_CODE,
                    start_date=cs,
                    end_date=ce,
                )
                if df is None or df.empty:
                    return df
                return df[df["curve_type"] == curve_type].reset_index(drop=True)

            chunk_df = _fetch_with_retry(
                _call, f"fetch_cn_yield_curve chunk ({cs}~{ce}, type={curve_type})"
            )
            if chunk_df is not None and not chunk_df.empty:
                parts.append(chunk_df)
            cur = chunk_end + timedelta(days=1)
            if sleep_between > 0 and cur <= end_dt:
                time.sleep(sleep_between)

        if not parts:
            return None
        return pd.concat(parts, ignore_index=True).drop_duplicates(
            subset=["trade_date", "curve_term"], keep="last"
        )

    def fetch_cn_money_supply(
        self, start_m: str, end_m: str
    ) -> pd.DataFrame | None:
        """M0 / M1 / M2 货币供应（月度，年同比 + 环比）.

        Args:
            start_m: 起始月份，``YYYYMM``.
            end_m: 截止月份，``YYYYMM``.

        Returns:
            DataFrame 含 ``month`` 与 ``m0/m1/m2`` 及对应 ``_yoy/_mom`` 列；
            失败返回 ``None``.
        """
        return _fetch_with_retry(
            lambda: self._pro.query("cn_m", start_m=start_m, end_m=end_m),
            f"fetch_cn_money_supply ({start_m}~{end_m})",
        )

    def fetch_cn_cpi(self, start_m: str, end_m: str) -> pd.DataFrame | None:
        """CPI 月度数据（全国 + 城镇 + 农村，含同比/环比/累计）.

        Args:
            start_m: 起始月份，``YYYYMM``.
            end_m: 截止月份，``YYYYMM``.

        Returns:
            DataFrame；失败返回 ``None``.
        """
        return _fetch_with_retry(
            lambda: self._pro.query("cn_cpi", start_m=start_m, end_m=end_m),
            f"fetch_cn_cpi ({start_m}~{end_m})",
        )

    def fetch_cn_ppi(self, start_m: str, end_m: str) -> pd.DataFrame | None:
        """PPI 月度数据（生产资料 / 生活资料 + 多层子项）.

        Args:
            start_m: 起始月份，``YYYYMM``.
            end_m: 截止月份，``YYYYMM``.

        Returns:
            DataFrame；失败返回 ``None``.
        """
        return _fetch_with_retry(
            lambda: self._pro.query("cn_ppi", start_m=start_m, end_m=end_m),
            f"fetch_cn_ppi ({start_m}~{end_m})",
        )

    def fetch_cn_pmi(self, start_m: str, end_m: str) -> pd.DataFrame | None:
        """PMI 月度数据（制造业 / 非制造业 / 综合 + 各分项）.

        分子端 E 信号的核心月度代理：``pmi010000`` 是制造业 PMI 总指数（>50 扩张）.

        Args:
            start_m: 起始月份，``YYYYMM``.
            end_m: 截止月份，``YYYYMM``.

        Returns:
            DataFrame；失败返回 ``None``.
        """
        return _fetch_with_retry(
            lambda: self._pro.query("cn_pmi", start_m=start_m, end_m=end_m),
            f"fetch_cn_pmi ({start_m}~{end_m})",
        )

    def fetch_cn_vai(self, start_m: str, end_m: str) -> pd.DataFrame | None:
        """工业增加值月度数据（同比 / 累计同比）.

        分子端 E 的近端代理，比季度净利润早 1-2 个月发布.

        Args:
            start_m: 起始月份，``YYYYMM``.
            end_m: 截止月份，``YYYYMM``.

        Returns:
            DataFrame；失败返回 ``None``.
        """
        return _fetch_with_retry(
            lambda: self._pro.query("cn_vai", start_m=start_m, end_m=end_m),
            f"fetch_cn_vai ({start_m}~{end_m})",
        )

    def fetch_margin(self, start: str, end: str) -> pd.DataFrame | None:
        """两融余额日频（沪深合计 + 分市场）.

        流动性维度代理：余额变化反映杠杆资金风险偏好.

        Args:
            start: 起始日期，``YYYYMMDD``.
            end: 截止日期，``YYYYMMDD``.

        Returns:
            DataFrame 含 ``trade_date`` / ``exchange_id`` / ``rzye`` 等列；失败 ``None``.
        """
        return _fetch_with_retry(
            lambda: self._pro.query("margin", start_date=start, end_date=end),
            f"fetch_margin ({start}~{end})",
        )

    def fetch_moneyflow_hsgt(self, trade_date: str) -> pd.DataFrame | None:
        """北向资金单日净流入（沪股通 + 深股通）.

        透传给 :meth:`TushareClient.fetch_moneyflow_hsgt`；MacroClient 暴露
        这个方法是为了让 regime classifier 单点依赖 MacroClient.
        """
        return self._tushare.fetch_moneyflow_hsgt(trade_date)

    def fetch_index_dailybasic(
        self,
        ts_code: str,
        start: str,
        end: str,
    ) -> pd.DataFrame | None:
        """指数估值数据（TTM PE / PB / DV / 总市值 / 流通市值）.

        Tushare ``index_dailybasic`` 接口；常用 ``000300.SH`` (沪深 300)/
        ``000905.SH`` (中证 500)/ ``000016.SH`` (上证 50).
        书第 7 章估值标尺 n = log(PE * r) / log(1+g) 的 PE 来源.

        Args:
            ts_code: 指数代码.
            start: ``YYYYMMDD``.
            end: ``YYYYMMDD``.

        Returns:
            DataFrame 含 ``trade_date`` / ``pe`` (TTM) / ``pe_ttm`` / ``pb`` /
            ``dv_ratio`` / ``total_mv``；失败 ``None``.
        """
        return _fetch_with_retry(
            lambda: self._pro.index_dailybasic(
                ts_code=ts_code, start_date=start, end_date=end,
            ),
            f"fetch_index_dailybasic {ts_code} ({start}~{end})",
        )

    # ------------------------------------------------------------------ #
    # AkShare 兜底（仅信用利差，因 Tushare 无对应免费接口）
    # ------------------------------------------------------------------ #

    def fetch_credit_yield_curve_akshare(self) -> pd.DataFrame | None:
        """AkShare 中债不同评级收益率曲线（含国债 / AAA / AA / AA- 等）.

        ``ak.bond_china_yield()`` 默认返回当前最近一日的全部评级 × 全期限矩阵。
        需要历史序列时调用方应按日循环 + 缓存（``scripts/update_macro_daily.py``
        负责按日增量落地）。

        Returns:
            DataFrame（长形式）；AkShare 未安装或调用失败返回 ``None``.
        """
        try:
            import akshare as ak  # type: ignore[import-not-found]
        except ImportError:
            logger.warning("AkShare 未安装，跳过 credit_yield_curve 拉取")
            return None

        def _call() -> pd.DataFrame | None:
            return ak.bond_china_yield()

        return _fetch_with_retry(
            _call, "fetch_credit_yield_curve_akshare (latest)"
        )


# ---------------------------------------------------------------------- #
# 数据转换辅助
# ---------------------------------------------------------------------- #


def pivot_yield_curve(
    long_df: pd.DataFrame,
    key_terms: tuple[float, ...] = _KEY_TERMS,
) -> pd.DataFrame | None:
    """把 ``fetch_cn_yield_curve`` 长表透视为日频宽表.

    Args:
        long_df: :meth:`MacroClient.fetch_cn_yield_curve` 的返回值.
        key_terms: 要提取的期限（年），缺失项保留 ``NaN``.

    Returns:
        宽表，index 为 ``trade_date``，columns 为 ``yld_<term>y``（如 ``yld_10y``）；
        输入为 ``None`` / 空 / 缺关键列时返回 ``None``.
    """
    if long_df is None or long_df.empty:
        return None
    required = {"trade_date", "curve_term", "yield"}
    if not required.issubset(long_df.columns):
        logger.warning("pivot_yield_curve: 缺少必要列 %s", required - set(long_df.columns))
        return None

    filtered = long_df[long_df["curve_term"].isin(key_terms)].copy()
    if filtered.empty:
        return None

    wide = filtered.pivot_table(
        index="trade_date", columns="curve_term", values="yield", aggfunc="last"
    )
    wide.columns = [_term_to_col(t) for t in wide.columns]
    return wide.sort_index().reset_index()


def _term_to_col(term: float) -> str:
    """``10.0`` -> ``yld_10y``；``0.25`` -> ``yld_3m``."""
    if term < 1.0:
        months = int(round(term * 12))
        return f"yld_{months}m"
    return f"yld_{int(term)}y"
