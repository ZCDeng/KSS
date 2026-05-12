"""停牌 / ST 名单数据加载.

数据源：

1. **CSV 落盘**（默认 ``storage/suspension_dates.csv``，列：
   ``ts_code, suspend_date, resume_date, reason``；以及
   ``storage/st_dates.csv``，列：``ts_code, st_start_date, st_end_date, reason``）.
2. **可选** Tushare 接口 ``suspend_d`` / ``namechange``（需 5000 积分），
   见 :func:`fetch_suspension_from_tushare` / :func:`fetch_st_from_tushare`.

向后兼容契约：

- CSV 不存在 → 返回空 set；下游 :class:`ExecutionModel` 自动跳过停牌 / ST 过滤；
- 加载失败 → ``logger.warning`` + 空 set，**不抛**（防 paper trade 链路中断）.

与 Qlib 的 :class:`Exchange` 对照：Qlib 通过 ``trade_calendar`` + ``daily_basic``
里的 ``is_st`` / ``suspended`` 字段判定；KSS 用更直接的"停牌区间 + ST 区间"表，
兼顾 Tushare 接口与手工 CSV 维护.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:  # pragma: no cover - 仅类型注解
    from collections.abc import Iterable

logger = logging.getLogger(__name__)

DEFAULT_SUSPENSION_CSV = Path("storage/suspension_dates.csv")
DEFAULT_ST_CSV = Path("storage/st_dates.csv")


class SuspensionData:
    """停牌 / ST 名单查询.

    展开策略：把 ``[suspend_date, resume_date]`` 用 :func:`pd.bdate_range`
    展开为具体交易日 set，按 ``ts_code`` 索引；查询 O(1).

    Notes:
        - ``bdate_range`` 用工作日近似 A 股交易日；对于跨节假日的长停牌，
          会把非交易日（节假日）也算进去，但这不影响过滤——非交易日本身
          就没有 panel 行.
        - ``resume_date`` 取**含端点**（停牌当日通常 amount=0 不会进 panel，
          但若 Tushare 给出 ``suspend_d`` 是首个停牌日且 ``resume_date``
          是末个停牌日，含端点最稳妥）.
    """

    SUSPENSION_REQUIRED_COLS = ("ts_code", "suspend_date", "resume_date")
    ST_REQUIRED_COLS = ("ts_code", "st_start_date", "st_end_date")

    def __init__(
        self,
        suspension_path: str | Path = DEFAULT_SUSPENSION_CSV,
        st_path: str | Path = DEFAULT_ST_CSV,
    ) -> None:
        """加载停牌 / ST 名单. 文件不存在则使用空 set + info log.

        Args:
            suspension_path: 停牌 CSV 路径.
            st_path: ST CSV 路径.
        """
        self.suspension_path = Path(suspension_path)
        self.st_path = Path(st_path)
        self.suspension: dict[str, set[pd.Timestamp]] = {}
        self.st: dict[str, set[pd.Timestamp]] = {}
        self._load_suspension(self.suspension_path)
        self._load_st(self.st_path)

    # ------------------------------------------------------------------ #
    # 加载
    # ------------------------------------------------------------------ #

    def _load_suspension(self, path: Path) -> None:
        if not path.exists():
            logger.info("停牌名单 %s 不存在，使用空 set", path)
            return
        try:
            df = pd.read_csv(path)
            missing = set(self.SUSPENSION_REQUIRED_COLS) - set(df.columns)
            if missing:
                logger.warning(
                    "停牌名单 %s 缺少必需列 %s，跳过", path, sorted(missing)
                )
                return
            df["suspend_date"] = pd.to_datetime(df["suspend_date"])
            df["resume_date"] = pd.to_datetime(df["resume_date"])
            for _, row in df.iterrows():
                ts = str(row["ts_code"])
                start = row["suspend_date"]
                end = row["resume_date"]
                if pd.isna(start) or pd.isna(end):
                    continue
                # 含起含止；bdate_range 自动忽略周末
                dates = pd.bdate_range(start, end)
                if len(dates) == 0:
                    # end 早于 start 的脏数据 → 至少把 start 当成停牌
                    dates = pd.DatetimeIndex([pd.Timestamp(start).normalize()])
                self.suspension.setdefault(ts, set()).update(
                    pd.Timestamp(d).normalize() for d in dates
                )
            logger.info(
                "加载停牌名单 %s：%d 只股票", path, len(self.suspension)
            )
        except Exception as exc:  # noqa: BLE001 - 数据层不抛
            logger.warning("加载停牌名单失败: %s", exc)

    def _load_st(self, path: Path) -> None:
        if not path.exists():
            logger.info("ST 名单 %s 不存在，使用空 set", path)
            return
        try:
            df = pd.read_csv(path)
            missing = set(self.ST_REQUIRED_COLS) - set(df.columns)
            if missing:
                logger.warning(
                    "ST 名单 %s 缺少必需列 %s，跳过", path, sorted(missing)
                )
                return
            df["st_start_date"] = pd.to_datetime(df["st_start_date"])
            df["st_end_date"] = pd.to_datetime(df["st_end_date"])
            for _, row in df.iterrows():
                ts = str(row["ts_code"])
                start = row["st_start_date"]
                end = row["st_end_date"]
                if pd.isna(start):
                    continue
                if pd.isna(end):
                    # 仍处于 ST 状态 → 用 today 兜底
                    end = pd.Timestamp.today().normalize()
                dates = pd.bdate_range(start, end)
                if len(dates) == 0:
                    dates = pd.DatetimeIndex([pd.Timestamp(start).normalize()])
                self.st.setdefault(ts, set()).update(
                    pd.Timestamp(d).normalize() for d in dates
                )
            logger.info("加载 ST 名单 %s：%d 只股票", path, len(self.st))
        except Exception as exc:  # noqa: BLE001
            logger.warning("加载 ST 名单失败: %s", exc)

    # ------------------------------------------------------------------ #
    # 查询
    # ------------------------------------------------------------------ #

    @staticmethod
    def _norm_date(date: object) -> pd.Timestamp | None:
        """把任意日期 / 字符串规范化为 normalize() 的 Timestamp."""
        if date is None:
            return None
        try:
            ts = pd.Timestamp(date)
        except (TypeError, ValueError):
            return None
        if pd.isna(ts):
            return None
        return ts.normalize()

    def is_suspended(self, ts_code: str, date: object) -> bool:
        """判定 ``ts_code`` 在 ``date`` 当日是否停牌."""
        norm = self._norm_date(date)
        if norm is None:
            return False
        return norm in self.suspension.get(str(ts_code), set())

    def is_st(self, ts_code: str, date: object) -> bool:
        """判定 ``ts_code`` 在 ``date`` 当日是否处于 ST 状态."""
        norm = self._norm_date(date)
        if norm is None:
            return False
        return norm in self.st.get(str(ts_code), set())

    def filter_panel(
        self,
        panel: pd.DataFrame,
        date_col: str = "trade_date",
        symbol_col: str = "symbol",
        exclude_st: bool = True,
        exclude_suspended: bool = True,
    ) -> pd.DataFrame:
        """从 panel 中剔除当日停牌 / ST 行.

        Args:
            panel: 含 ``date_col / symbol_col`` 的面板.
            date_col / symbol_col: 列名.
            exclude_st: True 时剔除 ST 行.
            exclude_suspended: True 时剔除停牌行.

        Returns:
            过滤后的 DataFrame（保留原 index 顺序）；缺列 → 原样返回 + warn.
        """
        if panel is None or len(panel) == 0:
            return panel
        if date_col not in panel.columns or symbol_col not in panel.columns:
            logger.warning(
                "SuspensionData.filter_panel: 缺少列 %r/%r，跳过",
                date_col,
                symbol_col,
            )
            return panel
        if not exclude_st and not exclude_suspended:
            return panel
        # 若内部 set 都为空 → 提前返回
        if (not self.suspension or not exclude_suspended) and (
            not self.st or not exclude_st
        ):
            return panel

        # 向量化掩码：对每行查一次 set，避免 apply 调 Python loop
        norm_dates = pd.to_datetime(panel[date_col]).dt.normalize()
        symbols = panel[symbol_col].astype(str)
        keep_mask = pd.Series(True, index=panel.index)

        if exclude_suspended and self.suspension:
            sus_mask = [
                d in self.suspension.get(s, set())
                for s, d in zip(symbols, norm_dates)
            ]
            keep_mask &= ~pd.Series(sus_mask, index=panel.index)
        if exclude_st and self.st:
            st_mask = [
                d in self.st.get(s, set()) for s, d in zip(symbols, norm_dates)
            ]
            keep_mask &= ~pd.Series(st_mask, index=panel.index)
        return panel.loc[keep_mask]

    # ------------------------------------------------------------------ #
    # 统计 / 调试
    # ------------------------------------------------------------------ #

    def __repr__(self) -> str:  # pragma: no cover - 调试便利
        return (
            f"SuspensionData(suspension={len(self.suspension)} symbols, "
            f"st={len(self.st)} symbols)"
        )


# --------------------------------------------------------------------------- #
# 可选：Tushare 拉数据落 CSV
# --------------------------------------------------------------------------- #


def fetch_suspension_from_tushare(
    ts_codes: "Iterable[str] | None" = None,
    start: str = "",
    end: str = "",
    output_path: str | Path = DEFAULT_SUSPENSION_CSV,
) -> bool:
    """调 Tushare ``suspend_d`` 接口拉停牌名单到 CSV.

    需要 **5000 积分**；积分不足 / token 缺失时 ``log.warning`` + 返回 False，
    不抛.

    Args:
        ts_codes: 可选股票代码迭代器（用于过滤；None = 全市场）.
        start: ``YYYYMMDD``.
        end: ``YYYYMMDD``.
        output_path: CSV 落盘路径.

    Returns:
        True 表示成功落盘；False 表示拉取失败 / 跳过.
    """
    try:
        from kss.data.tushare_client import TushareClient
    except Exception as exc:  # noqa: BLE001
        logger.warning("fetch_suspension_from_tushare: 无法导入 TushareClient: %s", exc)
        return False

    try:
        pro = TushareClient().get_pro()
    except Exception as exc:  # noqa: BLE001
        logger.warning("fetch_suspension_from_tushare: 初始化 Tushare 失败: %s", exc)
        return False
    if pro is None:
        logger.warning("fetch_suspension_from_tushare: Tushare pro 未就绪，跳过")
        return False

    try:
        # Tushare suspend_d：suspend_type='S' = 停牌；R = 复牌
        df = pro.suspend_d(start_date=start, end_date=end, suspend_type="S")
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "fetch_suspension_from_tushare: 调用 suspend_d 失败（可能积分不足）: %s",
            exc,
        )
        return False

    if df is None or df.empty:
        logger.warning("fetch_suspension_from_tushare: 返回空数据")
        return False

    if ts_codes is not None:
        codes = set(str(c) for c in ts_codes)
        df = df[df["ts_code"].astype(str).isin(codes)]
        if df.empty:
            logger.warning("fetch_suspension_from_tushare: 过滤后为空")
            return False

    # 把 suspend_d 的"单日记录"聚合为区间（每段连续停牌算一段）
    df = df.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    df["trade_date"] = pd.to_datetime(df["trade_date"].astype(str))
    rows: list[dict[str, object]] = []
    for ts_code, grp in df.groupby("ts_code"):
        dates = sorted(grp["trade_date"].tolist())
        if not dates:
            continue
        seg_start = dates[0]
        prev = dates[0]
        for d in dates[1:]:
            # 连续工作日间隔 ≤ 5 天 → 视作同一段停牌
            if (d - prev).days <= 5:
                prev = d
                continue
            rows.append({
                "ts_code": ts_code,
                "suspend_date": seg_start.strftime("%Y-%m-%d"),
                "resume_date": prev.strftime("%Y-%m-%d"),
                "reason": "tushare:suspend_d",
            })
            seg_start = d
            prev = d
        rows.append({
            "ts_code": ts_code,
            "suspend_date": seg_start.strftime("%Y-%m-%d"),
            "resume_date": prev.strftime("%Y-%m-%d"),
            "reason": "tushare:suspend_d",
        })

    out = pd.DataFrame(rows, columns=["ts_code", "suspend_date", "resume_date", "reason"])
    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(out_path, index=False)
    logger.info(
        "fetch_suspension_from_tushare: 写入 %d 段停牌区间 → %s", len(out), out_path
    )
    return True


def fetch_st_from_tushare(
    ts_codes: "Iterable[str] | None" = None,
    start: str = "",
    end: str = "",
    output_path: str | Path = DEFAULT_ST_CSV,
) -> bool:
    """调 Tushare ``namechange`` 接口拉 ST 区间到 CSV.

    需要 **5000 积分**；积分不足时返回 False + warn.

    Returns:
        True / False.
    """
    try:
        from kss.data.tushare_client import TushareClient
    except Exception as exc:  # noqa: BLE001
        logger.warning("fetch_st_from_tushare: 无法导入 TushareClient: %s", exc)
        return False

    try:
        pro = TushareClient().get_pro()
    except Exception as exc:  # noqa: BLE001
        logger.warning("fetch_st_from_tushare: 初始化 Tushare 失败: %s", exc)
        return False
    if pro is None:
        logger.warning("fetch_st_from_tushare: Tushare pro 未就绪，跳过")
        return False

    try:
        df = pro.namechange(start_date=start, end_date=end)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "fetch_st_from_tushare: 调用 namechange 失败（可能积分不足）: %s",
            exc,
        )
        return False

    if df is None or df.empty:
        logger.warning("fetch_st_from_tushare: 返回空数据")
        return False

    # 过滤名称含 "ST" 或 "*ST" 的记录
    df = df[df["name"].astype(str).str.contains("ST", na=False)].copy()
    if df.empty:
        logger.warning("fetch_st_from_tushare: 过滤 ST 后为空")
        return False
    if ts_codes is not None:
        codes = set(str(c) for c in ts_codes)
        df = df[df["ts_code"].astype(str).isin(codes)]
        if df.empty:
            logger.warning("fetch_st_from_tushare: 代码过滤后为空")
            return False

    df = df.rename(columns={"start_date": "st_start_date", "end_date": "st_end_date"})
    out = df[["ts_code", "st_start_date", "st_end_date", "name"]].rename(
        columns={"name": "reason"}
    )
    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(out_path, index=False)
    logger.info("fetch_st_from_tushare: 写入 %d 行 ST 区间 → %s", len(out), out_path)
    return True
