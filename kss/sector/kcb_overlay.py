r"""科创板池子持仓叠加 —— 板块名 → 在池中的 ts_code 列表.

给定 ``stock_names.csv`` 和活跃池股票代码集合，按行业 / 概念两个维度
建立反向索引，让板块复盘报告能标注「⭐N 只在池」.

设计纪律（守护 S620 教训）：

- 活跃池从 ``cs_data_688*.csv`` 文件名提取代码时**必须**使用精确正则
  ``r'cs_data_(688\d+)\.csv'``，禁止用宽松的 ``r'688\d+'`` —— 后者
  会把 ``cs_data_688688008.csv`` 误抽成 ``688688008`` 而非 ``688008``.
- 概念字段按 ``" / "`` 切分（与 stock_names.csv PR #2 的格式一致），
  ``strip()`` 后入索引；空字符串不入.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

# 项目根目录约定：cs_data_*.csv 落在工作目录下
DEFAULT_STOCK_NAMES_PATH = Path("storage") / "stock_names.csv"
DEFAULT_DATA_GLOB = "cs_data_688*.csv"

# 严格正则：完整匹配文件名，捕获 6 位 688 开头代码.
# 拒绝宽松匹配 r'688\d+' —— 见 S620 教训.
_CODE_REGEX = re.compile(r"cs_data_(688\d{3})\.csv")


@dataclass
class KcbOverlay:
    """KCB 池子持仓的双维索引.

    Attributes:
        industry_to_codes: ``{申万行业名: [ts_code, ...]}``.
        concept_to_codes: ``{同花顺概念名: [ts_code, ...]}``.
        active_pool: 索引来源的活跃池 ts_code 集合（用于统计 / 测试）.
    """

    industry_to_codes: dict[str, list[str]] = field(default_factory=dict)
    concept_to_codes: dict[str, list[str]] = field(default_factory=dict)
    active_pool: set[str] = field(default_factory=set)

    def count_for_industry(self, name: str) -> int:
        """板块名是否在池中有持仓 —— 行业维度.

        Returns:
            命中 ts_code 数；不在索引中返回 0.
        """
        return len(self.industry_to_codes.get(name, []))

    def count_for_concept(self, name: str) -> int:
        """板块名是否在池中有持仓 —— 概念维度."""
        return len(self.concept_to_codes.get(name, []))

    def codes_for_industry(self, name: str) -> list[str]:
        return list(self.industry_to_codes.get(name, []))

    def codes_for_concept(self, name: str) -> list[str]:
        return list(self.concept_to_codes.get(name, []))


def discover_active_pool(
    data_dir: Path | str | None = None,
    glob_pattern: str = DEFAULT_DATA_GLOB,
) -> set[str]:
    """从 ``cs_data_688*.csv`` 文件名扫出活跃池 ts_code 集合.

    使用严格正则保护避免 S620 类 bug —— 文件名不完全匹配格式时跳过.

    Args:
        data_dir: 数据目录；``None`` 时用 cwd（与 ``paper_trade_log_mv.py`` 一致）.
        glob_pattern: 文件名 glob.

    Returns:
        ``{"688008.SH", "688322.SH", ...}`` 的集合.
    """
    base = Path(data_dir) if data_dir else Path.cwd()
    codes: set[str] = set()
    for path in base.glob(glob_pattern):
        m = _CODE_REGEX.match(path.name)
        if m:
            codes.add(f"{m.group(1)}.SH")
        else:
            logger.debug("跳过不规则文件名 %s", path.name)
    return codes


def build_kcb_overlay(
    stock_names_path: Path | str = DEFAULT_STOCK_NAMES_PATH,
    active_pool: set[str] | None = None,
    data_dir: Path | str | None = None,
) -> KcbOverlay:
    """从 ``stock_names.csv`` 构建 KCB 池持仓双维索引.

    Args:
        stock_names_path: ``stock_names.csv`` 路径.
        active_pool: 显式活跃池 ts_code 集合；``None`` 时自动从 ``cs_data_688*.csv``
            扫出.
        data_dir: 当 ``active_pool`` 为 ``None`` 时扫文件名的目录.

    Returns:
        :class:`KcbOverlay` —— 文件缺失 / 列缺失时返回空索引 + warning，不外抛.
    """
    p = Path(stock_names_path)
    if not p.exists():
        logger.warning("stock_names.csv 不存在 %s，返回空 overlay", p)
        return KcbOverlay()

    try:
        df = pd.read_csv(p, dtype=str).fillna("")
    except (OSError, pd.errors.ParserError) as exc:
        logger.warning("读取 %s 失败: %s，返回空 overlay", p, exc)
        return KcbOverlay()

    if "ts_code" not in df.columns:
        logger.warning("stock_names.csv 缺 ts_code 列，返回空 overlay")
        return KcbOverlay()

    if active_pool is None:
        active_pool = discover_active_pool(data_dir=data_dir)

    # 过滤：只保留活跃池内的股票
    df = df[df["ts_code"].isin(active_pool)].reset_index(drop=True)
    if df.empty:
        logger.warning(
            "stock_names.csv 与活跃池 (%d 只) 交集为空，返回空 overlay",
            len(active_pool),
        )
        return KcbOverlay(active_pool=set(active_pool))

    industry_to_codes: dict[str, list[str]] = {}
    concept_to_codes: dict[str, list[str]] = {}

    for _, row in df.iterrows():
        code = row["ts_code"]

        # 行业：单值，原样入索引（空字符串跳过）
        if "industry" in df.columns:
            ind = (row["industry"] or "").strip()
            if ind:
                industry_to_codes.setdefault(ind, []).append(code)

        # 概念：" / " 切分后多值入索引（空段跳过）
        if "concept" in df.columns:
            raw = (row["concept"] or "").strip()
            if raw:
                for token in raw.split("/"):
                    name = token.strip()
                    if name:
                        concept_to_codes.setdefault(name, []).append(code)

    if "concept" not in df.columns:
        logger.warning(
            "stock_names.csv 缺 concept 列（PR #2 前的格式），概念维度 KCB 标注全 0",
        )

    return KcbOverlay(
        industry_to_codes=industry_to_codes,
        concept_to_codes=concept_to_codes,
        active_pool=set(active_pool),
    )
