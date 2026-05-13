"""Data 模块单元测试."""

from __future__ import annotations

import os
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from kss.data.cache_manager import CacheManager
from kss.data.tushare_client import TushareClient


class TestCacheManager:
    """CacheManager 功能测试."""

    def test_load_save_roundtrip(self) -> None:
        """测试 CSV 缓存的写入与读取一致性."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cm = CacheManager(cache_dir=tmpdir)
            df = pd.DataFrame({
                "trade_date": pd.date_range("2024-01-01", periods=5),
                "close": [100.0, 101.0, 102.0, 103.0, 104.0],
            })
            cm.save("688001.SH", df)

            loaded = cm.load("688001.SH")
            assert loaded is not None
            assert len(loaded) == 5
            assert "trade_date" in loaded.columns
            # trade_date 应被解析为 datetime
            assert pd.api.types.is_datetime64_any_dtype(loaded["trade_date"])

    def test_load_nonexistent(self) -> None:
        """测试读取不存在的缓存返回 None."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cm = CacheManager(cache_dir=tmpdir)
            result = cm.load("NOT_EXIST")
            assert result is None

    def test_is_stale_fresh(self) -> None:
        """测试新鲜缓存不应被判定为过期."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cm = CacheManager(cache_dir=tmpdir)
            today = datetime.now().date()
            df = pd.DataFrame({
                "trade_date": [pd.Timestamp(today)],
                "close": [100.0],
            })
            cm.save("688001.SH", df)
            assert cm.is_stale("688001.SH", threshold_days=1) is False

    def test_is_stale_old(self) -> None:
        """测试旧缓存应被判定为过期."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cm = CacheManager(cache_dir=tmpdir)
            old_date = datetime.now().date() - timedelta(days=5)
            df = pd.DataFrame({
                "trade_date": [pd.Timestamp(old_date)],
                "close": [100.0],
            })
            cm.save("688001.SH", df)
            assert cm.is_stale("688001.SH", threshold_days=1) is True

    def test_exists(self) -> None:
        """测试 exists 方法."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cm = CacheManager(cache_dir=tmpdir)
            assert cm.exists("688001.SH") is False
            df = pd.DataFrame({"trade_date": [pd.Timestamp.now()], "close": [1.0]})
            cm.save("688001.SH", df)
            assert cm.exists("688001.SH") is True


class TestTushareClient:
    """TushareClient 功能测试."""

    def test_singleton(self) -> None:
        """测试单例模式."""
        c1 = TushareClient()
        c2 = TushareClient()
        assert c1 is c2

    def test_resolve_token_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """测试从环境变量解析 token."""
        monkeypatch.setenv("TUSHARE_TOKEN", "test_token_12345")
        # 清除已有实例以强制重新初始化
        TushareClient._instance = None
        TushareClient._pro = None

        client = TushareClient()
        token = client._resolve_token()
        assert token == "test_token_12345"

    def test_resolve_token_from_file(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """测试从文件解析 token."""
        monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
        token_file = tmp_path / "tushare_token.txt"
        token_file.write_text("file_token_67890\n")

        # 临时切换工作目录
        monkeypatch.chdir(tmp_path)
        TushareClient._instance = None
        TushareClient._pro = None

        client = TushareClient()
        token = client._resolve_token()
        assert token == "file_token_67890"

    def test_resolve_token_not_found(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """测试无 token 时返回空字符串."""
        monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
        monkeypatch.chdir(tmp_path)
        TushareClient._instance = None
        TushareClient._pro = None

        client = TushareClient()
        token = client._resolve_token()
        assert token == ""


# ====================================================================== #
# P0 #6: CacheManager.save 原子写入
# ====================================================================== #


class TestCacheManagerAtomicWrite:
    """CacheManager.save 原子性测试（P0 #6 修复）."""

    def test_save_leaves_no_tmp_on_success(self) -> None:
        """正常写入完成后不应残留 .tmp 文件."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cm = CacheManager(cache_dir=tmpdir)
            df = pd.DataFrame({
                "trade_date": [pd.Timestamp("2024-01-01")],
                "close": [100.0],
            })
            cm.save("A", df)

            target = cm.get_path("A")
            tmp = target.with_suffix(target.suffix + ".tmp")
            assert target.exists()
            assert not tmp.exists(), "原子写入完成后 .tmp 应被 os.replace 移除"

    def test_save_preserves_old_cache_on_crash(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """模拟 os.replace 崩溃：旧缓存内容必须保留，.tmp 应被清理."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cm = CacheManager(cache_dir=tmpdir)
            df_old = pd.DataFrame({
                "trade_date": [pd.Timestamp("2024-01-01")],
                "close": [100.0],
            })
            cm.save("A", df_old)
            old_bytes = cm.get_path("A").read_bytes()

            # 让 os.replace 抛错模拟"写到一半被 kill"
            def crashing_replace(*args: object, **kwargs: object) -> None:
                raise OSError("simulated crash")

            monkeypatch.setattr("kss.data.cache_manager.os.replace", crashing_replace)

            df_new = pd.DataFrame({
                "trade_date": [pd.Timestamp("2024-01-02")],
                "close": [200.0],
            })
            cm.save("A", df_new)  # 数据层契约：log warning 不 raise

            # 旧缓存字节完全保留
            assert cm.get_path("A").read_bytes() == old_bytes, \
                "崩溃时旧缓存不应被覆盖（原子写入的关键不变量）"
            # .tmp 已被清理
            tmp = cm.get_path("A").with_suffix(cm.get_path("A").suffix + ".tmp")
            assert not tmp.exists(), "崩溃时残留的 .tmp 应被清理"


# ====================================================================== #
# P0 #8: CacheManager.is_stale 节假日感知
# ====================================================================== #


class TestCacheManagerHolidays:
    """is_stale 节假日回滚测试（P0 #8 修复）."""

    def test_roll_back_through_holiday(self) -> None:
        """today 落在节假日中段 → 应回滚穿过整段假期到节前最后交易日."""
        holidays = frozenset({
            date(2024, 5, 1), date(2024, 5, 2), date(2024, 5, 3),
        })
        with tempfile.TemporaryDirectory() as tmpdir:
            cm = CacheManager(cache_dir=tmpdir, holidays=holidays)
            # today = 2024-05-02 周四（在 5/1-5/3 假期中段）→ 回滚穿过 5/2、5/1 到 4/30
            rolled = cm._roll_back_to_trading_day(date(2024, 5, 2))
            assert rolled == date(2024, 4, 30), \
                f"应回滚到 2024-04-30 周二，实际: {rolled}"

    def test_roll_back_first_trading_day_after_holiday(self) -> None:
        """today = 节后首个真实交易日 → 不应回滚（它本身就是交易日）."""
        holidays = frozenset({
            date(2024, 5, 1), date(2024, 5, 2), date(2024, 5, 3),
        })
        with tempfile.TemporaryDirectory() as tmpdir:
            cm = CacheManager(cache_dir=tmpdir, holidays=holidays)
            # 5/6 周一是 5/1-5/5 长假后首个交易日 → 不回滚
            rolled = cm._roll_back_to_trading_day(date(2024, 5, 6))
            assert rolled == date(2024, 5, 6)

    def test_roll_back_weekend_inside_long_holiday(self) -> None:
        """周六处于长假中 → 应回滚穿过整段节假日 + 周末 到节前最后交易日."""
        holidays = frozenset({
            date(2024, 5, 1), date(2024, 5, 2), date(2024, 5, 3),
        })
        with tempfile.TemporaryDirectory() as tmpdir:
            cm = CacheManager(cache_dir=tmpdir, holidays=holidays)
            # 5/4 周六 → 5/3 假期 → 5/2 假期 → 5/1 假期 → 4/30 周二 ✓
            rolled = cm._roll_back_to_trading_day(date(2024, 5, 4))
            assert rolled == date(2024, 4, 30)

    def test_roll_back_on_weekday_no_holiday(self) -> None:
        """today 本就是普通交易日 → 不变."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cm = CacheManager(cache_dir=tmpdir, holidays=frozenset())
            # 2024-04-30 是周二
            rolled = cm._roll_back_to_trading_day(date(2024, 4, 30))
            assert rolled == date(2024, 4, 30)

    def test_roll_back_through_weekend(self) -> None:
        """无节假日识别下，周六应回滚到周五（保留旧行为）."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cm = CacheManager(cache_dir=tmpdir, holidays=frozenset())
            # 2024-05-04 周六 → 2024-05-03 周五
            rolled = cm._roll_back_to_trading_day(date(2024, 5, 4))
            assert rolled == date(2024, 5, 3)

    def test_is_stale_uses_holidays(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """today 处于节假日中段时，is_stale 不应误判过期（这是 P0 #8 的核心 case）.

        场景：today=5/2（五一假期第 2 天），cache 最后 trade_date=4/30（节前最后交易日）。
        Tushare 在 5/1-5/3 三天无新数据可拿，is_stale 应识别这点不触发无谓重抓。
        """
        holidays = frozenset({
            date(2024, 5, 1), date(2024, 5, 2), date(2024, 5, 3),
        })
        with tempfile.TemporaryDirectory() as tmpdir:
            cm = CacheManager(cache_dir=tmpdir, holidays=holidays)
            df = pd.DataFrame({
                "trade_date": [pd.Timestamp("2024-04-30")],
                "close": [100.0],
            })
            cm.save("A", df)

            class FakeDatetime(datetime):
                @classmethod
                def now(cls, tz: object = None) -> "FakeDatetime":  # type: ignore[override]
                    return cls(2024, 5, 2, 14, 0)

            monkeypatch.setattr("kss.data.cache_manager.datetime", FakeDatetime)

            # 旧逻辑（无节假日识别）：delta = 2 days > 1 → STALE → 触发无效重抓
            # 新逻辑：today=5/2 回滚到 4/30，delta = 0 → NOT STALE
            assert cm.is_stale("A", threshold_days=1) is False, \
                "节假日感知失败：5/2 假期中段的缓存被误判过期"

    def test_is_stale_post_holiday_still_detects_freshness(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """节后首个交易日，缓存仍是节前的最后日：应识别为过期（确实有新数据可拿）."""
        holidays = frozenset({
            date(2024, 5, 1), date(2024, 5, 2), date(2024, 5, 3),
        })
        with tempfile.TemporaryDirectory() as tmpdir:
            cm = CacheManager(cache_dir=tmpdir, holidays=holidays)
            df = pd.DataFrame({
                "trade_date": [pd.Timestamp("2024-04-30")],
                "close": [100.0],
            })
            cm.save("A", df)

            class FakeDatetime(datetime):
                @classmethod
                def now(cls, tz: object = None) -> "FakeDatetime":  # type: ignore[override]
                    return cls(2024, 5, 6, 14, 0)  # 节后首个交易日

            monkeypatch.setattr("kss.data.cache_manager.datetime", FakeDatetime)

            # today=5/6 是真实交易日不回滚；delta=6 > 1 → STALE（正确：Tushare 已有 5/6 数据可抓）
            assert cm.is_stale("A", threshold_days=1) is True


# ====================================================================== #
# P0 #9: CacheManager.has_internal_gaps 部分响应检测
# ====================================================================== #


class TestCacheManagerGapDetection:
    """has_internal_gaps 检测 Tushare 部分响应（P0 #9 修复）."""

    def test_normal_weekend_not_flagged(self) -> None:
        """Fri→Mon 的 3 天间隔是正常周末，不应被判为 gap."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cm = CacheManager(cache_dir=tmpdir)
            df = pd.DataFrame({
                "trade_date": pd.to_datetime([
                    "2024-04-26", "2024-04-29", "2024-04-30",  # Fri, Mon, Tue
                ]),
                "close": [100.0, 101.0, 102.0],
            })
            cm.save("A", df)
            assert cm.has_internal_gaps("A") is False

    def test_long_holiday_not_flagged(self) -> None:
        """春节长假 ~11 天间隔在默认 14 天阈值内，不应被误报."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cm = CacheManager(cache_dir=tmpdir)
            df = pd.DataFrame({
                "trade_date": pd.to_datetime([
                    "2024-02-08", "2024-02-19",  # 春节前后，相隔 11 天
                ]),
                "close": [100.0, 101.0],
            })
            cm.save("A", df)
            assert cm.has_internal_gaps("A") is False

    def test_partial_response_flagged(self) -> None:
        """缓存中间存在 ~58 天断档（Tushare 部分响应特征）应被检出."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cm = CacheManager(cache_dir=tmpdir)
            df = pd.DataFrame({
                "trade_date": pd.to_datetime([
                    "2024-01-02", "2024-01-03", "2024-03-01",
                ]),
                "close": [100.0, 101.0, 110.0],
            })
            cm.save("A", df)
            assert cm.has_internal_gaps("A") is True

    def test_empty_or_single_row_not_flagged(self) -> None:
        """空/单行缓存不应误报为 gap."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cm = CacheManager(cache_dir=tmpdir)
            df = pd.DataFrame({
                "trade_date": [pd.Timestamp("2024-01-01")],
                "close": [100.0],
            })
            cm.save("A", df)
            assert cm.has_internal_gaps("A") is False

    def test_custom_threshold(self) -> None:
        """支持自定义阈值（极小阈值 → 任何周末都触发）."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cm = CacheManager(cache_dir=tmpdir)
            df = pd.DataFrame({
                "trade_date": pd.to_datetime(["2024-04-26", "2024-04-29"]),  # 3 天
                "close": [100.0, 101.0],
            })
            cm.save("A", df)
            assert cm.has_internal_gaps("A", max_gap_days=2) is True
            assert cm.has_internal_gaps("A", max_gap_days=10) is False


# ====================================================================== #
# P0 #7: TushareClient 指数退避重试
# ====================================================================== #


class _FakePro:
    """Tushare _pro 替身：所有 API 方法都委托给一个统一的可注入函数."""

    def __init__(self, fn) -> None:  # type: ignore[no-untyped-def]
        self.fn = fn

    def daily(self, **kwargs: object) -> pd.DataFrame | None:
        return self.fn(**kwargs)

    def daily_basic(self, **kwargs: object) -> pd.DataFrame | None:
        return self.fn(**kwargs)

    # 板块层面 API（U1 板块复盘扩展）
    def moneyflow_ind_dc(self, **kwargs: object) -> pd.DataFrame | None:
        return self.fn(**kwargs)

    def moneyflow_cnt_ths(self, **kwargs: object) -> pd.DataFrame | None:
        return self.fn(**kwargs)

    def sw_daily(self, **kwargs: object) -> pd.DataFrame | None:
        return self.fn(**kwargs)

    def moneyflow_hsgt(self, **kwargs: object) -> pd.DataFrame | None:
        return self.fn(**kwargs)


class TestTushareRetry:
    """fetch_daily / fetch_daily_basic 指数退避重试（P0 #7 修复）."""

    @staticmethod
    def _reset_singleton() -> None:
        TushareClient._instance = None
        TushareClient._pro = None

    def test_fetch_daily_retries_then_succeeds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """前两次 RuntimeError、第三次成功 → 返回数据；总调用 3 次."""
        self._reset_singleton()
        client = TushareClient()

        sample_df = pd.DataFrame({
            "trade_date": ["20240101"], "close": [100.0],
        })
        call_count = {"n": 0}

        def flaky(**kwargs: object) -> pd.DataFrame:
            call_count["n"] += 1
            if call_count["n"] < 3:
                raise RuntimeError(f"simulated transient error #{call_count['n']}")
            return sample_df

        client._pro = _FakePro(flaky)
        # 屏蔽 sleep 提速测试
        monkeypatch.setattr("kss.data.tushare_client.time.sleep", lambda x: None)

        result = client.fetch_daily("688008.SH", "20240101", "20240131")
        assert result is not None
        assert len(result) == 1
        assert call_count["n"] == 3, \
            f"应重试至第 3 次成功，实际调用 {call_count['n']} 次"

    def test_fetch_daily_returns_none_after_max_retries(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """持续失败应返回 None（数据层契约：log warning 不 raise），尝试 3 次."""
        self._reset_singleton()
        client = TushareClient()

        call_count = {"n": 0}

        def always_fail(**kwargs: object) -> pd.DataFrame:
            call_count["n"] += 1
            raise RuntimeError("simulated permanent outage")

        client._pro = _FakePro(always_fail)
        monkeypatch.setattr("kss.data.tushare_client.time.sleep", lambda x: None)

        result = client.fetch_daily("688008.SH", "20240101", "20240131")
        assert result is None
        assert call_count["n"] == 3, "应尝试 3 次（首次 + 2 次重试）后放弃"

    def test_fetch_daily_empty_response_no_retry(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """空 DataFrame 是业务"无数据"信号，不应触发重试."""
        self._reset_singleton()
        client = TushareClient()

        call_count = {"n": 0}

        def empty_response(**kwargs: object) -> pd.DataFrame:
            call_count["n"] += 1
            return pd.DataFrame()

        client._pro = _FakePro(empty_response)
        monkeypatch.setattr("kss.data.tushare_client.time.sleep", lambda x: None)

        result = client.fetch_daily("688008.SH", "20240101", "20240131")
        assert result is None
        assert call_count["n"] == 1, "空响应不应重试，调用应只有 1 次"

    def test_fetch_daily_basic_also_retries(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """fetch_daily_basic 也走同一条重试路径."""
        self._reset_singleton()
        client = TushareClient()

        call_count = {"n": 0}
        sample = pd.DataFrame({"trade_date": ["20240101"], "pe": [15.0]})

        def flaky(**kwargs: object) -> pd.DataFrame:
            call_count["n"] += 1
            if call_count["n"] < 2:
                raise RuntimeError("retry me")
            return sample

        client._pro = _FakePro(flaky)
        monkeypatch.setattr("kss.data.tushare_client.time.sleep", lambda x: None)

        result = client.fetch_daily_basic("688008.SH", "20240101", "20240131")
        assert result is not None
        assert call_count["n"] == 2


# ====================================================================== #
# U1: 板块层面 API（行业/概念资金流 + 申万行业 + 北向）
# ====================================================================== #


class TestTushareBoardAPIs:
    """板块级 fetch 方法（U1 收盘后板块复盘）.

    全部走 _fetch_with_retry，验证重试 / 空响应 / kwargs 透传行为.
    """

    @staticmethod
    def _reset_singleton() -> None:
        TushareClient._instance = None
        TushareClient._pro = None

    def test_fetch_moneyflow_ind_dc_returns_dataframe(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """成功路径：返回含 content_type / ts_code / pct_change / net_amount_rate 列的 DataFrame."""
        self._reset_singleton()
        client = TushareClient()

        sample = pd.DataFrame({
            "trade_date": ["20260512"],
            "content_type": ["行业"],
            "ts_code": ["BK0457.DC"],
            "name": ["电网设备"],
            "pct_change": [1.48],
            "net_amount": [4060351744.0],
            "net_amount_rate": [4.13],
            "buy_elg_amount_rate": [4.19],
        })
        client._pro = _FakePro(lambda **_: sample)
        monkeypatch.setattr("kss.data.tushare_client.time.sleep", lambda x: None)

        result = client.fetch_moneyflow_ind_dc("20260512")
        assert result is not None
        assert "content_type" in result.columns
        assert "net_amount_rate" in result.columns
        assert result.iloc[0]["name"] == "电网设备"

    def test_fetch_moneyflow_ind_dc_retries_then_succeeds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """暂时性失败后重试成功."""
        self._reset_singleton()
        client = TushareClient()

        call_count = {"n": 0}
        sample = pd.DataFrame({"name": ["半导体"], "pct_change": [2.0]})

        def flaky(**kwargs: object) -> pd.DataFrame:
            call_count["n"] += 1
            if call_count["n"] < 3:
                raise RuntimeError("transient")
            return sample

        client._pro = _FakePro(flaky)
        monkeypatch.setattr("kss.data.tushare_client.time.sleep", lambda x: None)

        result = client.fetch_moneyflow_ind_dc("20260512")
        assert result is not None
        assert call_count["n"] == 3

    def test_fetch_moneyflow_ind_dc_empty_no_retry(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """空响应（业务"无数据"）不触发重试，立即返回 None."""
        self._reset_singleton()
        client = TushareClient()

        call_count = {"n": 0}

        def empty(**kwargs: object) -> pd.DataFrame:
            call_count["n"] += 1
            return pd.DataFrame()

        client._pro = _FakePro(empty)
        monkeypatch.setattr("kss.data.tushare_client.time.sleep", lambda x: None)

        result = client.fetch_moneyflow_ind_dc("20260512")
        assert result is None
        assert call_count["n"] == 1

    def test_fetch_moneyflow_cnt_ths_returns_dataframe(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """同花顺概念资金流：含 ts_code（886xxx.TI 格式）/ name / net_amount 列."""
        self._reset_singleton()
        client = TushareClient()

        sample = pd.DataFrame({
            "trade_date": ["20260512"],
            "ts_code": ["886101.TI"],
            "name": ["兵装重组概念"],
            "pct_change": [2.01],
            "net_amount": [-1.0],
        })
        client._pro = _FakePro(lambda **_: sample)
        monkeypatch.setattr("kss.data.tushare_client.time.sleep", lambda x: None)

        result = client.fetch_moneyflow_cnt_ths("20260512")
        assert result is not None
        assert result.iloc[0]["name"] == "兵装重组概念"

    def test_fetch_moneyflow_cnt_ths_returns_none_on_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """持续失败 3 次后返回 None，不外抛."""
        self._reset_singleton()
        client = TushareClient()

        call_count = {"n": 0}

        def always_fail(**kwargs: object) -> pd.DataFrame:
            call_count["n"] += 1
            raise RuntimeError("outage")

        client._pro = _FakePro(always_fail)
        monkeypatch.setattr("kss.data.tushare_client.time.sleep", lambda x: None)

        result = client.fetch_moneyflow_cnt_ths("20260512")
        assert result is None
        assert call_count["n"] == 3

    def test_fetch_sw_daily_returns_dataframe(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """sw_daily 单日切片：含 ts_code（801xxx.SI）/ name / pct_change / amount 列."""
        self._reset_singleton()
        client = TushareClient()

        sample = pd.DataFrame({
            "ts_code": ["801001.SI", "801010.SI"],
            "trade_date": ["20260512", "20260512"],
            "name": ["申万50", "农林牧渔"],
            "pct_change": [0.23, 1.05],
            "amount": [44103509.0, 12345678.0],
        })
        client._pro = _FakePro(lambda **_: sample)
        monkeypatch.setattr("kss.data.tushare_client.time.sleep", lambda x: None)

        result = client.fetch_sw_daily("20260512")
        assert result is not None
        assert len(result) == 2
        assert "pct_change" in result.columns

    def test_fetch_moneyflow_hsgt_returns_single_row(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """北向资金 API 单日返回 1 行，含 north_money / south_money 字段（万元单位）."""
        self._reset_singleton()
        client = TushareClient()

        sample = pd.DataFrame({
            "trade_date": ["20260512"],
            "north_money": ["405543.48"],
            "south_money": ["53807.88"],
        })
        client._pro = _FakePro(lambda **_: sample)
        monkeypatch.setattr("kss.data.tushare_client.time.sleep", lambda x: None)

        result = client.fetch_moneyflow_hsgt("20260512")
        assert result is not None
        assert len(result) == 1
        assert "north_money" in result.columns

    def test_fetch_moneyflow_hsgt_retries_and_eventually_returns_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """北向 API 持续失败 → 3 次后返回 None."""
        self._reset_singleton()
        client = TushareClient()

        call_count = {"n": 0}

        def always_fail(**kwargs: object) -> pd.DataFrame:
            call_count["n"] += 1
            raise RuntimeError("hsgt down")

        client._pro = _FakePro(always_fail)
        monkeypatch.setattr("kss.data.tushare_client.time.sleep", lambda x: None)

        result = client.fetch_moneyflow_hsgt("20260512")
        assert result is None
        assert call_count["n"] == 3
