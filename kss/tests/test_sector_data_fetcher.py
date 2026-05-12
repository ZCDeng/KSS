"""kss/sector/data_fetcher.py 单元测试.

覆盖：
- 全部成功路径
- 单个 API 失败的降级（其他字段不受影响）
- moneyflow_ind_dc 的 content_type 过滤
- 北向 API 字符串 → float 转换
"""

from __future__ import annotations

import pandas as pd
import pytest

from kss.data.tushare_client import TushareClient
from kss.sector.data_fetcher import (
    SectorSnapshot,
    _filter_industry_only,
    _hsgt_row_to_dict,
    load_sector_snapshot,
)


class _FakeClient:
    """TushareClient 子类替身：4 个 fetch_* 方法返回可注入数据."""

    def __init__(
        self,
        ind: pd.DataFrame | None = None,
        cnt: pd.DataFrame | None = None,
        sw: pd.DataFrame | None = None,
        hs: pd.DataFrame | None = None,
    ) -> None:
        self._ind = ind
        self._cnt = cnt
        self._sw = sw
        self._hs = hs

    def fetch_moneyflow_ind_dc(self, trade_date: str) -> pd.DataFrame | None:
        return self._ind

    def fetch_moneyflow_cnt_ths(self, trade_date: str) -> pd.DataFrame | None:
        return self._cnt

    def fetch_sw_daily(self, trade_date: str) -> pd.DataFrame | None:
        return self._sw

    def fetch_moneyflow_hsgt(self, trade_date: str) -> pd.DataFrame | None:
        return self._hs


def _make_ind_dc() -> pd.DataFrame:
    """模拟 moneyflow_ind_dc 返回：含行业 + 概念 + 地域三类."""
    return pd.DataFrame({
        "trade_date": ["20260512"] * 5,
        "content_type": ["行业", "行业", "概念", "概念", "地域"],
        "ts_code": ["BK0457.DC", "BK0475.DC", "BK0820.DC", "BK0815.DC", "BK0915.DC"],
        "name": ["电网设备", "半导体", "AIGC", "新能源汽车", "深圳本地股"],
        "pct_change": [1.48, 2.3, 3.1, 0.5, 0.8],
        "net_amount_rate": [4.13, 2.1, 5.2, 1.0, 0.3],
        "buy_elg_amount_rate": [4.19, 2.5, 5.5, 1.2, 0.5],
    })


def _make_cnt_ths() -> pd.DataFrame:
    return pd.DataFrame({
        "trade_date": ["20260512"] * 2,
        "ts_code": ["886101.TI", "886102.TI"],
        "name": ["集成电路概念", "量子科技"],
        "pct_change": [2.01, 1.5],
        "net_amount": [-1.0, 3.2],
    })


def _make_sw_daily() -> pd.DataFrame:
    return pd.DataFrame({
        "ts_code": ["801001.SI", "801010.SI"],
        "trade_date": ["20260512", "20260512"],
        "name": ["申万50", "农林牧渔"],
        "pct_change": [0.23, 1.05],
        "amount": [44103509.0, 12345678.0],
    })


def _make_hsgt() -> pd.DataFrame:
    return pd.DataFrame({
        "trade_date": ["20260512"],
        "north_money": ["405543.48"],
        "south_money": ["53807.88"],
        "hgt": ["190836.94"],
        "sgt": ["214706.54"],
    })


# ====================================================================== #
# 辅助函数测试
# ====================================================================== #


class TestFilterIndustryOnly:
    """_filter_industry_only 辅助函数."""

    def test_filters_to_industry_rows(self) -> None:
        """混合 content_type 应只保留 '行业' 行."""
        raw = _make_ind_dc()
        out = _filter_industry_only(raw)
        assert out is not None
        assert len(out) == 2
        assert set(out["name"]) == {"电网设备", "半导体"}
        assert (out["content_type"] == "行业").all()

    def test_returns_none_for_none_input(self) -> None:
        assert _filter_industry_only(None) is None

    def test_returns_none_for_empty(self) -> None:
        assert _filter_industry_only(pd.DataFrame()) is None

    def test_returns_none_when_no_industry_rows(self) -> None:
        """全是概念 / 地域时返回 None."""
        raw = pd.DataFrame({
            "content_type": ["概念", "地域"],
            "name": ["A", "B"],
        })
        assert _filter_industry_only(raw) is None

    def test_missing_content_type_column_passes_through(self) -> None:
        """缺列时直接返回原表 + warning（兼容性容错）."""
        raw = pd.DataFrame({"name": ["X"], "pct_change": [1.0]})
        out = _filter_industry_only(raw)
        assert out is not None
        assert len(out) == 1


class TestHsgtRowToDict:
    """_hsgt_row_to_dict 辅助函数."""

    def test_converts_string_fields_to_float(self) -> None:
        """Tushare 返回字符串字段应被 cast 为 float."""
        raw = _make_hsgt()
        out = _hsgt_row_to_dict(raw)
        assert out is not None
        assert out["north_money"] == pytest.approx(405543.48)
        assert out["south_money"] == pytest.approx(53807.88)
        assert "hgt" in out
        assert "sgt" in out

    def test_returns_none_for_none(self) -> None:
        assert _hsgt_row_to_dict(None) is None

    def test_returns_none_for_empty(self) -> None:
        assert _hsgt_row_to_dict(pd.DataFrame()) is None

    def test_skips_unconvertible_fields(self) -> None:
        """字段值无法 cast 时跳过该字段，不外抛."""
        raw = pd.DataFrame({
            "trade_date": ["20260512"],
            "north_money": ["100.0"],
            "south_money": ["NULL"],  # cast 会失败
        })
        out = _hsgt_row_to_dict(raw)
        assert out is not None
        assert out["north_money"] == pytest.approx(100.0)
        assert "south_money" not in out


# ====================================================================== #
# load_sector_snapshot 主入口
# ====================================================================== #


class TestLoadSectorSnapshot:
    """load_sector_snapshot —— 单日 snapshot 主入口."""

    def test_all_apis_success(self) -> None:
        """4 个 API 全部成功 → 4 个字段都有值，missing 为空."""
        client = _FakeClient(
            ind=_make_ind_dc(),
            cnt=_make_cnt_ths(),
            sw=_make_sw_daily(),
            hs=_make_hsgt(),
        )
        snap = load_sector_snapshot("20260512", client=client)  # type: ignore[arg-type]

        assert snap.trade_date == "20260512"
        assert snap.industry is not None and len(snap.industry) == 2
        assert snap.concept is not None and len(snap.concept) == 2
        assert snap.industry_index is not None and len(snap.industry_index) == 2
        assert snap.northbound is not None
        assert snap.northbound["north_money"] == pytest.approx(405543.48)
        assert snap.missing == []

    def test_industry_api_failure_isolated(self) -> None:
        """industry API 失败时其他字段不受影响."""
        client = _FakeClient(
            ind=None,
            cnt=_make_cnt_ths(),
            sw=_make_sw_daily(),
            hs=_make_hsgt(),
        )
        snap = load_sector_snapshot("20260512", client=client)  # type: ignore[arg-type]
        assert snap.industry is None
        assert "industry" in snap.missing
        assert snap.concept is not None
        assert snap.northbound is not None

    def test_concept_api_failure_isolated(self) -> None:
        client = _FakeClient(
            ind=_make_ind_dc(),
            cnt=None,
            sw=_make_sw_daily(),
            hs=_make_hsgt(),
        )
        snap = load_sector_snapshot("20260512", client=client)  # type: ignore[arg-type]
        assert snap.concept is None
        assert "concept" in snap.missing
        assert snap.industry is not None

    def test_northbound_api_failure_isolated(self) -> None:
        client = _FakeClient(
            ind=_make_ind_dc(),
            cnt=_make_cnt_ths(),
            sw=_make_sw_daily(),
            hs=None,
        )
        snap = load_sector_snapshot("20260512", client=client)  # type: ignore[arg-type]
        assert snap.northbound is None
        assert "northbound" in snap.missing

    def test_all_apis_failed(self) -> None:
        """全部失败时 4 个字段都 None，missing 含全部 4 项."""
        client = _FakeClient(ind=None, cnt=None, sw=None, hs=None)
        snap = load_sector_snapshot("20260512", client=client)  # type: ignore[arg-type]
        assert snap.industry is None
        assert snap.concept is None
        assert snap.industry_index is None
        assert snap.northbound is None
        assert set(snap.missing) == {
            "industry", "concept", "industry_index", "northbound",
        }

    def test_industry_filtering_applied(self) -> None:
        """industry 字段应只含 content_type == '行业' 的行."""
        client = _FakeClient(
            ind=_make_ind_dc(),  # 5 行：2 行业 + 2 概念 + 1 地域
            cnt=_make_cnt_ths(),
            sw=_make_sw_daily(),
            hs=_make_hsgt(),
        )
        snap = load_sector_snapshot("20260512", client=client)  # type: ignore[arg-type]
        assert snap.industry is not None
        assert len(snap.industry) == 2
        assert set(snap.industry["name"]) == {"电网设备", "半导体"}

    def test_uses_default_client_when_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """client=None 时回退到 TushareClient 单例（不真实调 API）."""
        TushareClient._instance = None
        TushareClient._pro = None

        # 让 4 个 fetch_* 都返回 None，避免真实网络调用
        def _none(*args: object, **kwargs: object) -> None:
            return None

        monkeypatch.setattr(TushareClient, "fetch_moneyflow_ind_dc", _none)
        monkeypatch.setattr(TushareClient, "fetch_moneyflow_cnt_ths", _none)
        monkeypatch.setattr(TushareClient, "fetch_sw_daily", _none)
        monkeypatch.setattr(TushareClient, "fetch_moneyflow_hsgt", _none)

        snap = load_sector_snapshot("20260512")
        assert isinstance(snap, SectorSnapshot)
        assert snap.trade_date == "20260512"
        assert len(snap.missing) == 4
