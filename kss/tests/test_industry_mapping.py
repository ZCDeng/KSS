"""IndustryMapping 单元测试."""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd
import pytest

from kss.data.industry_mapping import IndustryMapping


class TestIndustryMappingFromCsv:
    """``from_csv`` 路径."""

    def test_missing_file_returns_empty_with_warning(
        self, caplog: pytest.LogCaptureFixture, tmp_path: Path,
    ) -> None:
        missing = tmp_path / "nope.csv"
        with caplog.at_level(logging.WARNING, logger="kss.data.industry_mapping"):
            im = IndustryMapping.from_csv(missing)
        assert len(im) == 0
        assert any("not found" in rec.message for rec in caplog.records)

    def test_valid_csv_loads(self, tmp_path: Path) -> None:
        p = tmp_path / "industry_map.csv"
        p.write_text(
            "ts_code,industry\n"
            "688008.SH,Semiconductors\n"
            "688017.SH,Software\n",
            encoding="utf-8",
        )
        im = IndustryMapping.from_csv(p)
        assert len(im) == 2
        assert im.get("688008.SH") == "Semiconductors"
        assert im.get("688017.SH") == "Software"

    def test_csv_with_comments_loads(self, tmp_path: Path) -> None:
        """CSV 行首 ``#`` 注释应被 pandas 跳过."""
        p = tmp_path / "industry_map.csv"
        p.write_text(
            "# this is a header comment\n"
            "ts_code,industry\n"
            "688009.SH,Capital_Goods\n",
            encoding="utf-8",
        )
        im = IndustryMapping.from_csv(p)
        assert len(im) == 1
        assert im.get("688009.SH") == "Capital_Goods"

    def test_csv_missing_columns_returns_empty(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture,
    ) -> None:
        p = tmp_path / "bad.csv"
        p.write_text("ts_code,foo\n688008.SH,bar\n", encoding="utf-8")
        with caplog.at_level(logging.WARNING, logger="kss.data.industry_mapping"):
            im = IndustryMapping.from_csv(p)
        assert len(im) == 0


class TestFallbackKcb:
    """``fallback_kcb`` 兜底规则."""

    def test_kcb_codes_get_star_prefix_label(self) -> None:
        im = IndustryMapping.fallback_kcb(
            ["688008.SH", "688500.SH", "688981.SH", "688017.SH"],
        )
        # 688[0-3]xx → STAR_TECH
        assert im.get("688008.SH") == "STAR_TECH"
        assert im.get("688017.SH") == "STAR_TECH"
        # 688[4-6]xx → STAR_MFG
        assert im.get("688500.SH") == "STAR_MFG"
        # 688[7-9]xx → STAR_BIO
        assert im.get("688981.SH") == "STAR_BIO"

    def test_non_kcb_returns_unknown(self) -> None:
        im = IndustryMapping.fallback_kcb(["300033.SZ", "600519.SH"])
        assert im.get("300033.SZ") == "UNKNOWN"
        assert im.get("600519.SH") == "UNKNOWN"

    def test_fallback_handles_bare_digits(self) -> None:
        im = IndustryMapping.fallback_kcb(["688008", "688500"])
        assert im.get("688008") == "STAR_TECH"
        assert im.get("688500") == "STAR_MFG"

    def test_fallback_at_least_two_classes_for_mixed_kcb(self) -> None:
        """fallback_kcb 至少要在科创板内部分出 ≥2 个类，否则 industry dummy 退化."""
        im = IndustryMapping.fallback_kcb(
            ["688008.SH", "688500.SH", "688981.SH"],
        )
        classes = {im.get(c) for c in ["688008.SH", "688500.SH", "688981.SH"]}
        assert len(classes) >= 2


class TestAddToPanel:
    """``add_to_panel`` 把 industry 列广播到 panel."""

    def test_add_to_panel_basic(self) -> None:
        panel = pd.DataFrame({
            "symbol": ["688008.SH", "688017.SH", "688009.SH"],
            "factor": [1.0, 2.0, 3.0],
        })
        im = IndustryMapping.from_dict({
            "688008.SH": "Semi",
            "688017.SH": "SW",
        })
        out = im.add_to_panel(panel)
        assert list(out["industry"]) == ["Semi", "SW", "UNKNOWN"]

    def test_add_to_panel_missing_symbol_col_raises(self) -> None:
        panel = pd.DataFrame({"ts_code": ["688008.SH"]})
        im = IndustryMapping.from_dict({"688008.SH": "Semi"})
        with pytest.raises(ValueError, match="symbol"):
            im.add_to_panel(panel, symbol_col="symbol")

    def test_add_to_panel_custom_columns(self) -> None:
        panel = pd.DataFrame({"ts_code": ["688008.SH", "688999.SH"]})
        im = IndustryMapping.from_dict({"688008.SH": "Semi"})
        out = im.add_to_panel(
            panel, symbol_col="ts_code", industry_col="sw_l1", default="N/A",
        )
        assert list(out["sw_l1"]) == ["Semi", "N/A"]


class TestGetAndContains:
    """常规查询接口."""

    def test_get_default(self) -> None:
        im = IndustryMapping.from_dict({"688008.SH": "Semi"})
        assert im.get("688008.SH") == "Semi"
        assert im.get("000000.SZ") == "UNKNOWN"
        assert im.get("000000.SZ", default="X") == "X"

    def test_contains(self) -> None:
        im = IndustryMapping.from_dict({"688008.SH": "Semi"})
        assert "688008.SH" in im
        assert "999999.SH" not in im
        # 非字符串 → False
        assert 12345 not in im  # type: ignore[operator]

    def test_repr_len(self) -> None:
        im = IndustryMapping.from_dict({"a": "x", "b": "y"})
        assert len(im) == 2
        assert "n=2" in repr(im)
