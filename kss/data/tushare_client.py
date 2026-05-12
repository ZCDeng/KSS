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
