"""SuspensionData 单测 —— CSV 加载 / 边界 / filter_panel."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from kss.data.suspension_data import (
    DEFAULT_ST_CSV,
    DEFAULT_SUSPENSION_CSV,
    SuspensionData,
)


# --------------------------------------------------------------------------- #
# CSV 加载
# --------------------------------------------------------------------------- #


class TestCSVLoad:

    def test_missing_files_empty_sets(self, tmp_path: Path) -> None:
        """文件不存在 → 空 set，不抛."""
        data = SuspensionData(
            suspension_path=tmp_path / "no_such_sus.csv",
            st_path=tmp_path / "no_such_st.csv",
        )
        assert data.suspension == {}
        assert data.st == {}

    def test_load_suspension_csv(self, tmp_path: Path) -> None:
        sus_csv = tmp_path / "suspension.csv"
        pd.DataFrame([
            {"ts_code": "600000.SH",
             "suspend_date": "2024-03-01",
             "resume_date": "2024-03-05",
             "reason": "重大事项"},
        ]).to_csv(sus_csv, index=False)

        data = SuspensionData(suspension_path=sus_csv, st_path=tmp_path / "_none.csv")
        # 工作日展开：3-01 周五, 3-04 周一, 3-05 周二 → 三个工作日
        sus_dates = data.suspension["600000.SH"]
        assert pd.Timestamp("2024-03-01") in sus_dates
        assert pd.Timestamp("2024-03-04") in sus_dates
        assert pd.Timestamp("2024-03-05") in sus_dates
        # 周六周日不应该被算进去（bdate_range 已排除）
        assert pd.Timestamp("2024-03-02") not in sus_dates
        assert pd.Timestamp("2024-03-03") not in sus_dates

    def test_load_st_csv(self, tmp_path: Path) -> None:
        st_csv = tmp_path / "st.csv"
        pd.DataFrame([
            {"ts_code": "600100.SH",
             "st_start_date": "2024-04-01",
             "st_end_date": "2024-04-03",
             "reason": "*ST 风险警示"},
        ]).to_csv(st_csv, index=False)

        data = SuspensionData(suspension_path=tmp_path / "_none.csv", st_path=st_csv)
        st_dates = data.st["600100.SH"]
        assert pd.Timestamp("2024-04-01") in st_dates
        assert pd.Timestamp("2024-04-02") in st_dates
        assert pd.Timestamp("2024-04-03") in st_dates

    def test_missing_required_columns_warns(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """缺必需列 → 跳过 + warn."""
        bad_csv = tmp_path / "bad.csv"
        pd.DataFrame([{"foo": "bar"}]).to_csv(bad_csv, index=False)
        data = SuspensionData(
            suspension_path=bad_csv, st_path=tmp_path / "none.csv"
        )
        assert data.suspension == {}

    def test_malformed_dates_skipped(self, tmp_path: Path) -> None:
        """脏数据 NaN 起始日 → 该行跳过，其他行正常."""
        sus_csv = tmp_path / "suspension.csv"
        pd.DataFrame([
            {"ts_code": "600000.SH",
             "suspend_date": "2024-03-01",
             "resume_date": "2024-03-01",
             "reason": "ok"},
            {"ts_code": "600100.SH",
             "suspend_date": None,
             "resume_date": "2024-03-05",
             "reason": "skip"},
        ]).to_csv(sus_csv, index=False)
        data = SuspensionData(
            suspension_path=sus_csv, st_path=tmp_path / "none.csv"
        )
        assert "600000.SH" in data.suspension
        assert "600100.SH" not in data.suspension


# --------------------------------------------------------------------------- #
# is_suspended / is_st 边界
# --------------------------------------------------------------------------- #


class TestQuery:

    def _make(self, tmp_path: Path) -> SuspensionData:
        sus_csv = tmp_path / "sus.csv"
        pd.DataFrame([
            {"ts_code": "600000.SH",
             "suspend_date": "2024-03-04",
             "resume_date": "2024-03-04",
             "reason": "x"},
        ]).to_csv(sus_csv, index=False)
        st_csv = tmp_path / "st.csv"
        pd.DataFrame([
            {"ts_code": "600100.SH",
             "st_start_date": "2024-03-04",
             "st_end_date": "2024-03-04",
             "reason": "x"},
        ]).to_csv(st_csv, index=False)
        return SuspensionData(suspension_path=sus_csv, st_path=st_csv)

    def test_is_suspended_positive(self, tmp_path: Path) -> None:
        data = self._make(tmp_path)
        assert data.is_suspended("600000.SH", pd.Timestamp("2024-03-04")) is True
        assert data.is_suspended("600000.SH", "2024-03-04") is True

    def test_is_suspended_other_date(self, tmp_path: Path) -> None:
        data = self._make(tmp_path)
        assert data.is_suspended("600000.SH", "2024-03-05") is False

    def test_is_suspended_unknown_symbol(self, tmp_path: Path) -> None:
        data = self._make(tmp_path)
        assert data.is_suspended("999999.SH", "2024-03-04") is False

    def test_is_suspended_nan_date(self, tmp_path: Path) -> None:
        data = self._make(tmp_path)
        assert data.is_suspended("600000.SH", None) is False

    def test_is_st_buy_block(self, tmp_path: Path) -> None:
        data = self._make(tmp_path)
        assert data.is_st("600100.SH", "2024-03-04") is True
        assert data.is_st("600100.SH", "2024-03-05") is False


# --------------------------------------------------------------------------- #
# filter_panel
# --------------------------------------------------------------------------- #


class TestFilterPanel:

    def _data(self, tmp_path: Path) -> SuspensionData:
        sus_csv = tmp_path / "sus.csv"
        pd.DataFrame([
            {"ts_code": "600000.SH",
             "suspend_date": "2024-03-04",
             "resume_date": "2024-03-04",
             "reason": "x"},
        ]).to_csv(sus_csv, index=False)
        st_csv = tmp_path / "st.csv"
        pd.DataFrame([
            {"ts_code": "600100.SH",
             "st_start_date": "2024-03-04",
             "st_end_date": "2024-03-04",
             "reason": "x"},
        ]).to_csv(st_csv, index=False)
        return SuspensionData(suspension_path=sus_csv, st_path=st_csv)

    def _panel(self) -> pd.DataFrame:
        return pd.DataFrame([
            {"trade_date": pd.Timestamp("2024-03-04"), "symbol": "600000.SH"},
            {"trade_date": pd.Timestamp("2024-03-04"), "symbol": "600100.SH"},
            {"trade_date": pd.Timestamp("2024-03-04"), "symbol": "600200.SH"},
            {"trade_date": pd.Timestamp("2024-03-05"), "symbol": "600000.SH"},
        ])

    def test_filter_drops_suspended_and_st(self, tmp_path: Path) -> None:
        data = self._data(tmp_path)
        out = data.filter_panel(self._panel())
        # 3-04 当日 600000 停牌 + 600100 ST → 都被剔；600200 留下
        keys = list(zip(out["trade_date"], out["symbol"]))
        assert (pd.Timestamp("2024-03-04"), "600000.SH") not in keys
        assert (pd.Timestamp("2024-03-04"), "600100.SH") not in keys
        assert (pd.Timestamp("2024-03-04"), "600200.SH") in keys
        # 3-05 600000 已复牌
        assert (pd.Timestamp("2024-03-05"), "600000.SH") in keys

    def test_filter_only_suspended(self, tmp_path: Path) -> None:
        data = self._data(tmp_path)
        out = data.filter_panel(self._panel(), exclude_st=False)
        symbols = set(
            out[out["trade_date"] == pd.Timestamp("2024-03-04")]["symbol"]
        )
        # 600100 是 ST，但 exclude_st=False → 保留
        assert "600100.SH" in symbols
        assert "600000.SH" not in symbols  # 停牌仍剔除

    def test_filter_missing_columns_passthrough(self, tmp_path: Path) -> None:
        data = self._data(tmp_path)
        df = pd.DataFrame([{"foo": "bar"}])
        out = data.filter_panel(df)
        assert len(out) == 1

    def test_filter_empty_panel(self, tmp_path: Path) -> None:
        data = self._data(tmp_path)
        df = pd.DataFrame(columns=["trade_date", "symbol"])
        out = data.filter_panel(df)
        assert len(out) == 0

    def test_filter_no_lists_loaded_passthrough(self, tmp_path: Path) -> None:
        """SuspensionData 内部 set 都空 → 直接 passthrough."""
        data = SuspensionData(
            suspension_path=tmp_path / "no.csv", st_path=tmp_path / "no2.csv"
        )
        panel = self._panel()
        out = data.filter_panel(panel)
        assert len(out) == len(panel)


# --------------------------------------------------------------------------- #
# Tushare 抓取 stub —— 仅验证 token 缺失时 graceful 跳过
# --------------------------------------------------------------------------- #


class TestTushareFetchGraceful:
    """token 缺失 / 接口异常时不抛."""

    def test_fetch_suspension_returns_false_on_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from kss.data import suspension_data as mod

        class _FakePro:
            def suspend_d(self, **_kw: object) -> pd.DataFrame:
                raise RuntimeError("simulated: 5000 积分不足")

        class _FakeClient:
            def get_pro(self) -> object:
                return _FakePro()

        monkeypatch.setattr(
            "kss.data.tushare_client.TushareClient",
            lambda: _FakeClient(),  # noqa: ARG005
        )
        ok = mod.fetch_suspension_from_tushare(
            ts_codes=["600000.SH"],
            start="20240101",
            end="20240131",
            output_path=tmp_path / "out.csv",
        )
        assert ok is False

    def test_fetch_st_returns_false_on_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from kss.data import suspension_data as mod

        class _FakePro:
            def namechange(self, **_kw: object) -> pd.DataFrame:
                raise RuntimeError("simulated: 接口异常")

        class _FakeClient:
            def get_pro(self) -> object:
                return _FakePro()

        monkeypatch.setattr(
            "kss.data.tushare_client.TushareClient",
            lambda: _FakeClient(),  # noqa: ARG005
        )
        ok = mod.fetch_st_from_tushare(
            ts_codes=["600000.SH"],
            start="20240101",
            end="20240131",
            output_path=tmp_path / "out.csv",
        )
        assert ok is False


# --------------------------------------------------------------------------- #
# Default paths exist as attribute（防 typo 回归）
# --------------------------------------------------------------------------- #


def test_default_paths_exposed() -> None:
    assert "suspension" in str(DEFAULT_SUSPENSION_CSV)
    assert "st" in str(DEFAULT_ST_CSV)
