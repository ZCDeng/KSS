"""kss/sector/kcb_overlay.py 单元测试.

关键覆盖：
- 行业维度索引正确
- 概念字段按 ' / ' 切分多值入索引
- 库缺失容错
- S620 回归保护：正则不能宽松匹配
"""

from __future__ import annotations

from pathlib import Path

from kss.sector.kcb_overlay import (
    KcbOverlay,
    build_kcb_overlay,
    discover_active_pool,
)
from kss.storage.stock_names import set_stock_names


def _write_names_db(path: Path, rows: list[dict[str, str]]) -> Path:
    """写入测试 stock_names（kss.db），返回 db_path 供 build_kcb_overlay 用。"""
    set_stock_names(rows, db_path=path)
    return path


# ====================================================================== #
# discover_active_pool
# ====================================================================== #


class TestDiscoverActivePool:
    """从 cs_data_688*.csv 文件名提取活跃池代码."""

    def test_correctly_extracts_six_digit_codes(self, tmp_path: Path) -> None:
        """正常文件名 → 提取 6 位代码 + .SH 后缀."""
        for code in ["688008", "688322", "688099"]:
            (tmp_path / f"cs_data_{code}.csv").touch()
        pool = discover_active_pool(data_dir=tmp_path)
        assert pool == {"688008.SH", "688322.SH", "688099.SH"}

    def test_regex_strictness_rejects_malformed_filename(self, tmp_path: Path) -> None:
        """S620 回归：cs_data_688688008.csv（9 位）不应被误抽成 688688008."""
        (tmp_path / "cs_data_688008.csv").touch()
        (tmp_path / "cs_data_688688008.csv").touch()  # 异常格式
        pool = discover_active_pool(data_dir=tmp_path)
        # 严格正则只匹配恰好 6 位的 cs_data_688\d{3}.csv
        assert pool == {"688008.SH"}
        # 不应误抽出 688688.SH 或 688688008
        assert "688688.SH" not in pool

    def test_empty_dir_returns_empty_set(self, tmp_path: Path) -> None:
        assert discover_active_pool(data_dir=tmp_path) == set()


# ====================================================================== #
# build_kcb_overlay
# ====================================================================== #


class TestBuildKcbOverlay:
    """从 stock_names 构建 industry/concept 双维索引."""

    def test_industry_index_built_correctly(self, tmp_path: Path) -> None:
        """3 只股，行业半导体 x2 + 软件服务 x1 → industry_to_codes 命中数正确."""
        db = _write_names_db(tmp_path / "kss.db", [
            {"ts_code": "688008.SH", "name": "澜起科技", "industry": "半导体", "concept": ""},
            {"ts_code": "688012.SH", "name": "中微公司", "industry": "半导体", "concept": ""},
            {"ts_code": "688088.SH", "name": "虹软科技", "industry": "软件服务", "concept": ""},
        ])
        overlay = build_kcb_overlay(
            db_path=db,
            active_pool={"688008.SH", "688012.SH", "688088.SH"},
        )
        assert overlay.count_for_industry("半导体") == 2
        assert overlay.count_for_industry("软件服务") == 1
        assert overlay.count_for_industry("不存在") == 0
        assert set(overlay.codes_for_industry("半导体")) == {"688008.SH", "688012.SH"}

    def test_concept_split_by_slash(self, tmp_path: Path) -> None:
        """concept 字段 '集成电路概念 / 转融券标的' → 两个概念都映射到该 ts_code."""
        db = _write_names_db(tmp_path / "kss.db", [
            {
                "ts_code": "688099.SH", "name": "晶晨股份", "industry": "半导体",
                "concept": "集成电路概念 / 转融券标的",
            },
        ])
        overlay = build_kcb_overlay(
            db_path=db,
            active_pool={"688099.SH"},
        )
        assert overlay.count_for_concept("集成电路概念") == 1
        assert overlay.count_for_concept("转融券标的") == 1
        assert overlay.codes_for_concept("集成电路概念") == ["688099.SH"]

    def test_empty_concept_not_indexed(self, tmp_path: Path) -> None:
        """concept 为空字符串 → 不进 concept 索引，不外抛."""
        db = _write_names_db(tmp_path / "kss.db", [
            {"ts_code": "688008.SH", "name": "澜起", "industry": "半导体", "concept": ""},
        ])
        overlay = build_kcb_overlay(
            db_path=db,
            active_pool={"688008.SH"},
        )
        assert overlay.concept_to_codes == {}

    def test_active_pool_filters_unrelated_stocks(self, tmp_path: Path) -> None:
        """stock_names 含池外股票 → 不进索引."""
        db = _write_names_db(tmp_path / "kss.db", [
            {"ts_code": "688008.SH", "name": "A", "industry": "半导体", "concept": ""},
            {"ts_code": "688999.SH", "name": "B", "industry": "半导体", "concept": ""},
        ])
        # 池中只有 688008
        overlay = build_kcb_overlay(
            db_path=db,
            active_pool={"688008.SH"},
        )
        assert overlay.count_for_industry("半导体") == 1
        assert overlay.codes_for_industry("半导体") == ["688008.SH"]

    def test_concept_column_absent_in_row_treated_as_empty(self, tmp_path: Path) -> None:
        """行未提供 concept 键（.get 落 None）→ concept_to_codes 为空，不外抛。

        （原 CSV 版本测的是"旧格式无 concept 列"；kss.db 表 schema 固定 4 列，
        这个场景现在对应"某行 concept 值缺失/NULL"，行为等价：概念维度该行不计入。）
        """
        db = _write_names_db(tmp_path / "kss.db", [
            {"ts_code": "688008.SH", "name": "A", "industry": "半导体"},
        ])
        overlay = build_kcb_overlay(
            db_path=db,
            active_pool={"688008.SH"},
        )
        assert overlay.count_for_industry("半导体") == 1
        assert overlay.concept_to_codes == {}

    def test_missing_db_returns_empty_overlay(self, tmp_path: Path) -> None:
        """kss.db 不存在 → connect() 新建空库，stock_names 表为空 → 空 overlay，不外抛."""
        overlay = build_kcb_overlay(db_path=tmp_path / "no_such.db", active_pool={"688008.SH"})
        assert isinstance(overlay, KcbOverlay)
        assert overlay.industry_to_codes == {}
        assert overlay.concept_to_codes == {}

    def test_active_pool_no_intersection_returns_empty(self, tmp_path: Path) -> None:
        """DB 与 active_pool 无交集 → 空 overlay，但 active_pool 字段保留."""
        db = _write_names_db(tmp_path / "kss.db", [
            {"ts_code": "688008.SH", "name": "A", "industry": "半导体", "concept": ""},
        ])
        overlay = build_kcb_overlay(
            db_path=db,
            active_pool={"688999.SH"},  # 不存在的代码
        )
        assert overlay.industry_to_codes == {}
        assert overlay.active_pool == {"688999.SH"}

    def test_auto_discover_active_pool_via_data_dir(self, tmp_path: Path) -> None:
        """active_pool=None 时从 data_dir 自动发现."""
        (tmp_path / "cs_data_688008.csv").touch()
        (tmp_path / "cs_data_688012.csv").touch()

        db = _write_names_db(tmp_path / "kss.db", [
            {"ts_code": "688008.SH", "name": "A", "industry": "半导体", "concept": ""},
            {"ts_code": "688012.SH", "name": "B", "industry": "半导体", "concept": ""},
            {"ts_code": "688999.SH", "name": "C", "industry": "软件服务", "concept": ""},
        ])
        overlay = build_kcb_overlay(
            db_path=db,
            active_pool=None,
            data_dir=tmp_path,
        )
        assert overlay.count_for_industry("半导体") == 2  # 688008 + 688012
        assert overlay.count_for_industry("软件服务") == 0  # 688999 不在活跃池

    def test_concept_with_extra_whitespace_normalized(self, tmp_path: Path) -> None:
        """概念字段含额外空格 → strip 后入索引."""
        db = _write_names_db(tmp_path / "kss.db", [
            {
                "ts_code": "688099.SH", "name": "A", "industry": "半导体",
                "concept": "  集成电路概念  /   转融券标的  ",
            },
        ])
        overlay = build_kcb_overlay(
            db_path=db,
            active_pool={"688099.SH"},
        )
        # strip 后的概念名作为 key
        assert overlay.count_for_concept("集成电路概念") == 1
        assert overlay.count_for_concept("转融券标的") == 1
        # 不应有带空格的 key
        assert "  集成电路概念  " not in overlay.concept_to_codes
