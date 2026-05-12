"""行业映射数据 —— ts_code → 申万一级行业.

目前 Tushare 拉 sw_l1 板块需要积分；本模块提供两条路径：

1. **优先**：从 ``storage/industry_map.csv`` 读（持久化文件，可手工维护）；
2. **fallback**：返回每只股 ``industry="UNKNOWN"``，配合 ``neutralize`` 时的容错.

未来扩展：可加 ``fetch_from_tushare()`` 方法自动同步.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable

import pandas as pd

logger = logging.getLogger(__name__)

DEFAULT_INDUSTRY_MAP_PATH = Path("storage") / "industry_map.csv"


class IndustryMapping:
    """``ts_code`` → ``industry`` 映射.

    内部就是一个 ``dict[str, str]``。提供多种构造方式（CSV / dict /
    fallback by ts_code 前缀），以及把映射广播到 panel 的工具方法.
    """

    def __init__(self, mapping: dict[str, str] | None = None) -> None:
        """初始化映射.

        Args:
            mapping: ``{ts_code: industry}`` 字典；``None`` 则空映射.
        """
        self._map: dict[str, str] = dict(mapping) if mapping else {}

    # ------------------------------------------------------------------ #
    # 构造方法
    # ------------------------------------------------------------------ #

    @classmethod
    def from_csv(cls, path: str | Path = DEFAULT_INDUSTRY_MAP_PATH) -> "IndustryMapping":
        """从 CSV 加载.

        CSV 列要求：``ts_code``、``industry``。文件不存在时返回空映射 +
        ``logger.warning``，不抛异常（让 ``neutralize`` 走容错路径）.

        Args:
            path: CSV 路径，默认 ``storage/industry_map.csv``.

        Returns:
            ``IndustryMapping`` 实例.
        """
        p = Path(path)
        if not p.exists():
            logger.warning(
                "industry_map csv not found at %s; returning empty mapping", p,
            )
            return cls({})
        try:
            df = pd.read_csv(p, comment="#")
        except Exception as exc:  # pragma: no cover - read failure
            logger.warning("failed to read %s (%s); returning empty mapping", p, exc)
            return cls({})

        if "ts_code" not in df.columns or "industry" not in df.columns:
            logger.warning(
                "%s missing required columns ts_code/industry; got %s",
                p, list(df.columns),
            )
            return cls({})

        mapping = {
            str(row["ts_code"]).strip(): str(row["industry"]).strip()
            for _, row in df.iterrows()
            if pd.notna(row["ts_code"]) and pd.notna(row["industry"])
        }
        return cls(mapping)

    @classmethod
    def from_dict(cls, mapping: dict[str, str]) -> "IndustryMapping":
        """从字典构造."""
        return cls(mapping)

    @classmethod
    def fallback_kcb(cls, ts_codes: Iterable[str]) -> "IndustryMapping":
        """科创板专用 fallback —— 根据 ``ts_code`` 前缀粗分类（同板视为同行业）.

        这是行业映射数据完全缺失时的兜底；让 ``neutralize`` 在 ``by_industry=True``
        时不至于因为只有 1 个 industry 而无法构造 dummy 矩阵。具体规则：

        =======================  =================
        ``ts_code`` 模式         分配的 industry
        =======================  =================
        ``688[0-3]xx``           ``STAR_TECH``  （早期科创板技术股）
        ``688[4-6]xx``           ``STAR_MFG``   （中段制造）
        ``688[7-9]xx``           ``STAR_BIO``   （较晚的生物医药/新兴）
        其他 ``688xxx``          ``STAR_BOARD`` （兜底）
        非 ``688`` 前缀          ``UNKNOWN``
        =======================  =================

        实际生产建议从 Tushare/同花顺拉真实 SW L1 数据替换之.

        Args:
            ts_codes: 一组 ``ts_code``（``688xxx.SH`` 风格或裸数字均可）.

        Returns:
            ``IndustryMapping`` 实例.
        """
        mapping: dict[str, str] = {}
        for ts_code in ts_codes:
            if ts_code is None:
                continue
            code = str(ts_code).strip()
            # 取数字头
            digits = code.split(".")[0]
            if not digits.startswith("688"):
                mapping[code] = "UNKNOWN"
                continue
            try:
                tail = int(digits[3:6]) if len(digits) >= 6 else 0
            except ValueError:
                mapping[code] = "STAR_BOARD"
                continue
            if tail < 400:
                mapping[code] = "STAR_TECH"
            elif tail < 700:
                mapping[code] = "STAR_MFG"
            elif tail < 1000:
                mapping[code] = "STAR_BIO"
            else:  # pragma: no cover - 不可达
                mapping[code] = "STAR_BOARD"
        return cls(mapping)

    # ------------------------------------------------------------------ #
    # 查询 / 应用
    # ------------------------------------------------------------------ #

    def get(self, ts_code: str, default: str = "UNKNOWN") -> str:
        """单只股票查询，缺失返回 ``default``."""
        if ts_code is None:
            return default
        return self._map.get(str(ts_code).strip(), default)

    def add_to_panel(
        self,
        panel: pd.DataFrame,
        symbol_col: str = "symbol",
        industry_col: str = "industry",
        default: str = "UNKNOWN",
    ) -> pd.DataFrame:
        """把 ``industry`` 列加到 panel（按 ``symbol_col`` 查映射）.

        Args:
            panel: 输入 DataFrame.
            symbol_col: panel 中存放 ``ts_code`` 的列名.
            industry_col: 输出列名.
            default: 未命中映射时填的默认值.

        Returns:
            DataFrame 副本，新增 ``industry_col`` 列.

        Raises:
            ValueError: ``symbol_col`` 不存在.
        """
        if symbol_col not in panel.columns:
            raise ValueError(
                f"panel missing symbol column {symbol_col!r}; "
                f"got {list(panel.columns)}"
            )
        out = panel.copy()
        out[industry_col] = (
            out[symbol_col]
            .astype(str)
            .map(lambda c: self._map.get(c.strip(), default))
        )
        return out

    # ------------------------------------------------------------------ #
    # 标准方法
    # ------------------------------------------------------------------ #

    def __len__(self) -> int:
        return len(self._map)

    def __contains__(self, ts_code: object) -> bool:
        return isinstance(ts_code, str) and ts_code.strip() in self._map

    def __repr__(self) -> str:
        return f"IndustryMapping(n={len(self._map)})"
