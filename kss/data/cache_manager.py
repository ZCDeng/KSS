"""A 股股票数据 CSV 缓存管理器."""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable

import pandas as pd

logger = logging.getLogger(__name__)


# A 股工作日里休市的节假日。用于 :meth:`CacheManager.is_stale` 把"今天"回滚到
# 最近一个真实交易日；缺失的年份自动退化到"仅识别周末"的旧行为，无回归风险。
# 维护方式：随每年的上交所/国务院假期公告更新；不在此列表内的日期默认按工作日对待。
A_SHARE_HOLIDAYS_2024_2025: frozenset[date] = frozenset({
    # ---- 2024 ----
    date(2024, 1, 1),                                                    # 元旦
    date(2024, 2, 9),
    date(2024, 2, 12), date(2024, 2, 13), date(2024, 2, 14),
    date(2024, 2, 15), date(2024, 2, 16),                                # 春节
    date(2024, 4, 4), date(2024, 4, 5),                                  # 清明
    date(2024, 5, 1), date(2024, 5, 2), date(2024, 5, 3),                # 五一
    date(2024, 6, 10),                                                   # 端午
    date(2024, 9, 16), date(2024, 9, 17),                                # 中秋
    date(2024, 10, 1), date(2024, 10, 2), date(2024, 10, 3),
    date(2024, 10, 4), date(2024, 10, 7),                                # 国庆
    # ---- 2025 ----
    date(2025, 1, 1),                                                    # 元旦
    date(2025, 1, 28), date(2025, 1, 29), date(2025, 1, 30),
    date(2025, 1, 31), date(2025, 2, 3), date(2025, 2, 4),               # 春节
    date(2025, 4, 4),                                                    # 清明
    date(2025, 5, 1), date(2025, 5, 2), date(2025, 5, 5),                # 五一
    date(2025, 6, 2),                                                    # 端午
    date(2025, 10, 1), date(2025, 10, 2), date(2025, 10, 3),
    date(2025, 10, 6), date(2025, 10, 7), date(2025, 10, 8),             # 国庆+中秋
    # ---- 2026 ----（仅元旦，其余待官方公布后补齐）
    date(2026, 1, 1),
})


class CacheManager:
    """单只股票 CSV 缓存管理.

    每只股票存为 ``{cache_dir}/cs_data_{symbol}.csv``。

    安全保证：

    - **原子写入**：:meth:`save` 先写 ``.tmp`` 同目录文件再 ``os.replace`` 原子替换，
      Ctrl-C / 磁盘满不会留下截断的 CSV 被后续 :meth:`exists` + :meth:`is_stale`
      当作鲜数据派发。
    - **节假日感知**：:meth:`is_stale` 用提供的 ``holidays`` 把"今天"回滚到最近的
      真实交易日，不会因长假期间 cache 不更新而误判过期。
    - **内部 gap 检测**：:meth:`has_internal_gaps` 检测 Tushare 部分响应导致的
      缓存中间断档，DataLoader 在 cache-hit 路径会调用以决定是否强制重抓。
    """

    # 相邻 trade_date 自然日间隔上限。春节最长约 11 天（含两侧周末），14 天留 3 天余量；
    # 超过此值视为 Tushare 部分响应。
    DEFAULT_MAX_GAP_DAYS: int = 14

    def __init__(
        self,
        cache_dir: str = "./cs_data",
        holidays: Iterable[date] | None = None,
    ) -> None:
        """初始化缓存管理器.

        Args:
            cache_dir: 本地 CSV 缓存目录.
            holidays: A 股节假日集合（用于 ``is_stale`` 把今天回滚到最近交易日）.
                传 ``None`` 使用内置 ``A_SHARE_HOLIDAYS_2024_2025``；
                传 ``frozenset()`` 关闭节假日识别，仅按周末计算.
        """
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.holidays: frozenset[date] = (
            frozenset(holidays) if holidays is not None else A_SHARE_HOLIDAYS_2024_2025
        )

    def get_path(self, symbol: str) -> Path:
        """返回 *symbol* 缓存文件的完整路径."""
        return self.cache_dir / f"cs_data_{symbol}.csv"

    def exists(self, symbol: str) -> bool:
        """*symbol* 是否存在非空的缓存文件."""
        path = self.get_path(symbol)
        return path.exists() and path.stat().st_size > 0

    def load(self, symbol: str) -> pd.DataFrame | None:
        """读取 *symbol* 缓存 CSV，将 ``trade_date`` 解析为 ``datetime``.

        Returns:
            DataFrame；缓存不存在或读取失败返回 ``None``.
        """
        path = self.get_path(symbol)
        if not path.exists():
            return None
        try:
            df = pd.read_csv(path)
            if "trade_date" in df.columns:
                df["trade_date"] = pd.to_datetime(df["trade_date"])
            return df
        except Exception as exc:  # noqa: BLE001
            logger.warning("缓存读取失败 %s: %s", symbol, exc)
            return None

    def save(self, symbol: str, df: pd.DataFrame) -> None:
        """原子写入 *symbol* 的缓存 CSV.

        先写到 ``.tmp`` 同目录文件，再用 ``os.replace`` 原子替换目标路径。
        ``os.replace`` 在 POSIX/Windows 均保证原子性：要么旧文件完整保留，要么
        新文件完整就位，不会出现截断中间态。

        Args:
            symbol: 股票代码.
            df: 待写入 DataFrame.
        """
        path = self.get_path(symbol)
        tmp = path.with_suffix(path.suffix + ".tmp")
        try:
            df.to_csv(tmp, index=False)
            os.replace(tmp, path)
            logger.debug("缓存写入: %s (%s 行)", path, len(df))
        except Exception as exc:  # noqa: BLE001
            logger.warning("缓存写入失败 %s: %s", symbol, exc)
            # 清理可能残留的 .tmp（写入中断或 os.replace 失败）
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass

    def is_stale(self, symbol: str, threshold_days: int = 1) -> bool:
        """*symbol* 缓存是否过期.

        年龄按"今天回滚到最近交易日"与 cache 最后 ``trade_date`` 的自然日差计算，
        ``today`` 落在周末或节假日会被回滚到上一个交易日，避免长假期间误判过期。

        Args:
            symbol: 股票代码.
            threshold_days: 允许的最大自然日滞后.

        Returns:
            ``True`` 表示需要刷新.
        """
        latest = self.get_latest_date(symbol)
        if latest is None:
            return True
        today = datetime.now().date()
        most_recent_trading = self._roll_back_to_trading_day(today)
        delta = (most_recent_trading - latest.date()).days
        return delta > threshold_days

    def get_latest_date(self, symbol: str) -> datetime | None:
        """返回缓存中最新的 ``trade_date``；缓存为空或缺列返回 ``None``."""
        df = self.load(symbol)
        if df is None or df.empty or "trade_date" not in df.columns:
            return None
        latest = df["trade_date"].max()
        return pd.to_datetime(latest).to_pydatetime()

    def has_internal_gaps(
        self,
        symbol: str,
        max_gap_days: int | None = None,
    ) -> bool:
        """*symbol* 缓存内部是否存在异常的 trade_date gap.

        Tushare 偶发的"部分响应"会让缓存中间段缺失而 :meth:`is_stale` 看不到
        （它只看 ``max(trade_date)``）。本方法扫描相邻 ``trade_date`` 之间的
        自然日间隔，超过 ``max_gap_days`` 视为部分响应特征。

        Args:
            symbol: 股票代码.
            max_gap_days: 相邻 trade_date 允许的自然日间隔上限.
                默认 :attr:`DEFAULT_MAX_GAP_DAYS` (14，覆盖含两侧周末的春节长假).

        Returns:
            ``True`` 表示存在 gap > ``max_gap_days``；空/单行缓存返回 ``False``.
        """
        if max_gap_days is None:
            max_gap_days = self.DEFAULT_MAX_GAP_DAYS
        df = self.load(symbol)
        if df is None or len(df) < 2 or "trade_date" not in df.columns:
            return False
        dates = pd.to_datetime(df["trade_date"]).sort_values()
        diffs_days = dates.diff().dropna().dt.days
        return bool((diffs_days > max_gap_days).any())

    def _roll_back_to_trading_day(self, d: date) -> date:
        """将 *d* 回滚到最近一个交易日（向前跳过周末与已知节假日）.

        最多回退 30 自然日作为安全网（防极端 ``holidays`` 配置导致死循环）.
        """
        for _ in range(30):
            if d.weekday() < 5 and d not in self.holidays:
                return d
            d -= timedelta(days=1)
        return d
