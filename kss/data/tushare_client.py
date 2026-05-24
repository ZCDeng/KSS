"""Tushare pro API 单例封装，含指数退避重试."""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any, Callable

import pandas as pd
import tushare as ts

logger = logging.getLogger(__name__)

# Tushare API 调用的重试策略：3 次尝试、1s/2s 退避；最终失败 log warning 返回 None，
# 遵循 AGENTS.md 数据层契约（不在数据层抛异常）。
_MAX_ATTEMPTS: int = 3
_BACKOFF_BASE_SECONDS: float = 1.0


def _fetch_with_retry(
    fn: Callable[[], pd.DataFrame | None],
    label: str,
    max_attempts: int = _MAX_ATTEMPTS,
    backoff_base: float = _BACKOFF_BASE_SECONDS,
) -> pd.DataFrame | None:
    """对 Tushare API 调用应用指数退避重试.

    异常路径（network/timeout/429）重试；返回 None / 空 DataFrame 视为业务层
    "无数据"信号不重试（避免对真实空响应做无意义重试）。

    Args:
        fn: 无参回调，调用一次 Tushare API.
        label: 日志标签（含 ts_code 与日期窗口）.
        max_attempts: 最大尝试次数（包含首次）.
        backoff_base: 退避基数（秒）；第 n 次失败后等待 ``backoff_base * 2^(n-1)``.

    Returns:
        成功时返回 DataFrame；最终失败或空响应返回 ``None``.
    """
    for attempt in range(1, max_attempts + 1):
        try:
            df = fn()
        except Exception as exc:  # noqa: BLE001
            if attempt >= max_attempts:
                logger.warning(
                    "%s 最终失败（已重试 %d 次）: %s",
                    label,
                    max_attempts - 1,
                    exc,
                )
                return None
            wait = backoff_base * (2 ** (attempt - 1))
            logger.info(
                "%s 第 %d 次尝试失败: %s；%.1fs 后重试",
                label,
                attempt,
                exc,
                wait,
            )
            time.sleep(wait)
            continue
        if df is None or df.empty:
            logger.warning("%s 无数据返回", label)
            return None
        return df
    return None


class TushareClient:
    """Singleton wrapper for tushare pro API.

    The token is resolved once at initialisation from (in order):
    1. ``TUSHARE_TOKEN`` environment variable
    2. ``~/.tushare/token``
    3. ``.tushare_token`` (cwd)
    4. ``tushare_token.txt`` (cwd)
    """

    _instance: TushareClient | None = None
    _pro: Any | None = None

    def __new__(cls) -> TushareClient:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        if self._pro is not None:
            return

        token = self._resolve_token()
        if not token:
            logger.warning("Tushare token not found; API calls will likely fail.")
        else:
            ts.set_token(token)

        self._pro = ts.pro_api()

    @staticmethod
    def _resolve_token() -> str:
        """Find tushare token from env or known file paths."""
        token = os.environ.get("TUSHARE_TOKEN", "").strip()
        if token:
            return token

        candidates = [
            Path.home() / ".tushare" / "token",
            Path(".tushare_token"),
            Path("tushare_token.txt"),
        ]
        for path in candidates:
            if path.exists():
                try:
                    token = path.read_text(encoding="utf-8").strip()
                    if token:
                        return token
                except OSError:
                    continue
        return ""

    def get_pro(self) -> Any:
        """Return the underlying ``ts.pro_api()`` instance."""
        return self._pro

    def fetch_daily(
        self,
        ts_code: str,
        start: str,
        end: str,
    ) -> pd.DataFrame | None:
        """获取日线 OHLCV 数据（含指数退避重试）.

        Args:
            ts_code: Tushare 代码，例如 ``688008.SH``.
            start: 起始日期，``YYYYMMDD`` 格式.
            end: 截止日期，``YYYYMMDD`` 格式.

        Returns:
            DataFrame；失败或空响应返回 ``None``.
        """
        return _fetch_with_retry(
            lambda: self._pro.daily(
                ts_code=ts_code, start_date=start, end_date=end
            ),
            f"fetch_daily {ts_code} ({start}~{end})",
        )

    def fetch_daily_basic(
        self,
        ts_code: str,
        start: str,
        end: str,
    ) -> pd.DataFrame | None:
        """获取每日基本面指标（换手率、PE、PB 等，含指数退避重试）.

        Args:
            ts_code: Tushare 代码，例如 ``688008.SH``.
            start: 起始日期，``YYYYMMDD`` 格式.
            end: 截止日期，``YYYYMMDD`` 格式.

        Returns:
            DataFrame；失败或空响应返回 ``None``.
        """
        return _fetch_with_retry(
            lambda: self._pro.daily_basic(
                ts_code=ts_code, start_date=start, end_date=end
            ),
            f"fetch_daily_basic {ts_code} ({start}~{end})",
        )

    # ------------------------------------------------------------------ #
    # 板块层面 API（用于收盘后板块复盘 / 资金轮动分析）
    # ------------------------------------------------------------------ #

    def fetch_moneyflow_ind_dc(self, trade_date: str) -> pd.DataFrame | None:
        """东方财富行业资金流（单日切片，含指数退避重试）.

        Tushare ``moneyflow_ind_dc`` 单日返回 1000+ 行，含 ``content_type``
        列区分「行业 / 概念 / 地域」。调用方需要按 ``content_type == '行业'``
        过滤；保留原始 17 列含 ``net_amount_rate`` / ``buy_elg_amount_rate``
        等关键资金流指标.

        Args:
            trade_date: 交易日，``YYYYMMDD`` 格式.

        Returns:
            DataFrame；失败或空响应返回 ``None``.
        """
        return _fetch_with_retry(
            lambda: self._pro.moneyflow_ind_dc(trade_date=trade_date),
            f"fetch_moneyflow_ind_dc ({trade_date})",
        )

    def fetch_moneyflow_cnt_ths(self, trade_date: str) -> pd.DataFrame | None:
        """同花顺概念板块资金流（单日切片，含指数退避重试）.

        与 ``concept_detail`` 共用同花顺概念命名空间，保证概念名能与
        ``storage/stock_names.csv`` 的 ``concept`` 字段一致.

        Args:
            trade_date: 交易日，``YYYYMMDD`` 格式.

        Returns:
            DataFrame；失败或空响应返回 ``None``.
        """
        return _fetch_with_retry(
            lambda: self._pro.moneyflow_cnt_ths(trade_date=trade_date),
            f"fetch_moneyflow_cnt_ths ({trade_date})",
        )

    def fetch_sw_daily(self, trade_date: str) -> pd.DataFrame | None:
        """申万行业指数日线（单日切片，含 L1/L2/L3 全量，含指数退避重试）.

        返回的 DataFrame 含 ``ts_code`` / ``name`` / ``pct_change`` / ``amount``
        等列。**调用方需自行按 ts_code 前缀过滤所需层级**（申万一级为
        ``801001-801999.SI`` 的特定子集）；本方法不做过滤，保持薄封装.

        Args:
            trade_date: 交易日，``YYYYMMDD`` 格式.

        Returns:
            DataFrame；失败或空响应返回 ``None``.
        """
        return _fetch_with_retry(
            lambda: self._pro.sw_daily(trade_date=trade_date),
            f"fetch_sw_daily ({trade_date})",
        )

    def fetch_moneyflow_hsgt(self, trade_date: str) -> pd.DataFrame | None:
        """沪深港通资金流向（含北向 / 南向单日累计，含指数退避重试）.

        单日返回 1 行，含 ``north_money`` / ``south_money`` 等汇总字段
        （金额单位：万元）。用于复盘报告的「外资态度」section.

        Args:
            trade_date: 交易日，``YYYYMMDD`` 格式.

        Returns:
            DataFrame；失败或空响应返回 ``None``.
        """
        return _fetch_with_retry(
            lambda: self._pro.moneyflow_hsgt(trade_date=trade_date),
            f"fetch_moneyflow_hsgt ({trade_date})",
        )

    def fetch_index_daily(
        self,
        ts_code: str,
        start: str,
        end: str,
    ) -> pd.DataFrame | None:
        """指数日线 OHLCV（含 ``pct_chg`` / ``amount``，用于大盘量价复盘）.

        Tushare ``index_daily`` 接口；支持上证 / 深证 / 创业板 / 科创 50 等
        宽基指数代码（如 ``000001.SH`` / ``399006.SZ`` / ``000688.SH``）.

        Args:
            ts_code: 指数代码（如 ``000001.SH``）.
            start: 起始日期，``YYYYMMDD`` 格式.
            end: 截止日期，``YYYYMMDD`` 格式.

        Returns:
            DataFrame；失败或空响应返回 ``None``.
        """
        return _fetch_with_retry(
            lambda: self._pro.index_daily(
                ts_code=ts_code, start_date=start, end_date=end
            ),
            f"fetch_index_daily {ts_code} ({start}~{end})",
        )

    # ------------------------------------------------------------------ #
    # 基本面 / 股票信息（用于风险前过滤）
    # ------------------------------------------------------------------ #

    def fetch_fina_indicator(
        self,
        ts_code: str,
        start: str | None = None,
        end: str | None = None,
        period: str | None = None,
    ) -> pd.DataFrame | None:
        """财务指标（季频，含 ``debt_to_assets`` / ``current_ratio`` 等）.

        Tushare ``fina_indicator`` 单股查询；按 ``period`` 取单季 / ``start``
        ``end`` 取窗口. 财报披露滞后约 30 天，调用方应当用 trade_date - 30d
        作为可见上限.

        Args:
            ts_code: Tushare 代码.
            start: 起始公告日期 ``YYYYMMDD``，可空.
            end: 截止公告日期 ``YYYYMMDD``，可空.
            period: 报告期 ``YYYYMMDD``（如 ``20240930`` = 2024Q3），可空.

        Returns:
            DataFrame 含 ``ts_code`` / ``end_date`` / ``debt_to_assets`` /
            ``current_ratio`` / ``n_income_attr_p`` 等；失败 ``None``.
        """
        kwargs: dict = {"ts_code": ts_code}
        if start:
            kwargs["start_date"] = start
        if end:
            kwargs["end_date"] = end
        if period:
            kwargs["period"] = period
        return _fetch_with_retry(
            lambda: self._pro.fina_indicator(**kwargs),
            f"fetch_fina_indicator {ts_code} ({start or ''}~{end or ''} period={period or ''})",
        )

    def fetch_stock_basic(
        self,
        exchange: str = "",
        list_status: str = "L",
    ) -> pd.DataFrame | None:
        """股票基础信息（``name`` / ``list_date`` / ``delist_date`` / ``market``）.

        Tushare ``stock_basic`` 返回全市场清单. ``list_status='L'`` 仅在
        市股；含 ``'D'``（退市）/ ``'P'``（暂停上市）可换参. ST 检测：
        ``name`` 含 ``'ST'`` / ``'*ST'`` 前缀.

        Args:
            exchange: ``'SSE'`` 上交 / ``'SZSE'`` 深交，空则全市场.
            list_status: ``'L'`` 上市 / ``'D'`` 退市 / ``'P'`` 暂停.

        Returns:
            DataFrame；失败 ``None``.
        """
        return _fetch_with_retry(
            lambda: self._pro.stock_basic(
                exchange=exchange, list_status=list_status,
                fields="ts_code,name,industry,market,list_date,delist_date",
            ),
            f"fetch_stock_basic (exchange={exchange or 'ALL'} status={list_status})",
        )
