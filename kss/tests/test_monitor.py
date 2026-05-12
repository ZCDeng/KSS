"""monitor.py 单元测试.

monitor.py 是项目根目录的脚本（非 kss 包内模块），用 importlib 加载.
集中验证 P1 #32-#37 修复后的关键不变量：

- 模块加载不依赖 best_params.json 存在（旧实现模块顶层 ``with open(...)`` 缺文件即 ImportError）；
- ``load_best_params`` 在文件缺失/损坏时返回空字典而非抛错；
- ``calc_indicators`` 不修改入参 DataFrame（旧实现直接写 20+ 列回入参）；
- ``calc_indicators`` 对极端输入（常数序列）不产生 NaN/inf（RSI/CCI 0/0 防御）。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


def _load_monitor_module():
    """从项目根目录动态加载 monitor.py 模块."""
    root = Path(__file__).resolve().parents[2]
    monitor_path = root / "monitor.py"
    spec = importlib.util.spec_from_file_location("monitor_under_test", monitor_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["monitor_under_test"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def monitor():
    return _load_monitor_module()


def _make_df(n: int = 60, constant: bool = False) -> pd.DataFrame:
    """构造 monitor 指标计算所需的 OHLCV DataFrame（含中文列名）."""
    rng = np.random.default_rng(0)
    if constant:
        # 全常数序列：用于验证 RSI/CCI 在 0/0 退化场景不产 NaN
        close = pd.Series([100.0] * n)
    else:
        close = pd.Series(100.0 + rng.standard_normal(n).cumsum() * 0.5)
    high = close + 0.5
    low = close - 0.5
    return pd.DataFrame({
        "日期": pd.date_range("2024-01-01", periods=n),
        "开盘": close,
        "最高": high,
        "最低": low,
        "收盘": close,
        "昨收": close.shift(1).fillna(close.iloc[0]),
        "成交量": pd.Series(10000.0 + rng.standard_normal(n) * 1000),
        "成交额": pd.Series(1e7 + rng.standard_normal(n) * 1e6),
        "涨跌幅": close.pct_change().fillna(0) * 100,
        "volume_ratio": pd.Series(1.0 + rng.standard_normal(n) * 0.1),
        "turnover_rate": pd.Series(2.0 + rng.standard_normal(n) * 0.5),
        "turnover_rate_f": pd.Series(2.0 + rng.standard_normal(n) * 0.5),
        "振幅": (high - low) / close * 100,
    })


# ====================================================================== #
# P1 #32: 模块加载不依赖外部文件
# ====================================================================== #


class TestModuleLoadSafety:
    """monitor 模块加载阶段不应做任何 file IO / 网络调用（修复 P1 #32）."""

    def test_module_loads_without_best_params_file(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """切换到空目录后 import monitor 必须成功（旧实现会 FileNotFoundError）."""
        monkeypatch.chdir(tmp_path)
        # 清缓存，确保 spec.loader.exec_module 真正再跑一次模块顶层
        sys.modules.pop("monitor_under_test", None)
        module = _load_monitor_module()
        assert hasattr(module, "main")
        assert hasattr(module, "load_best_params")


class TestLoadBestParams:
    """load_best_params 健壮性（修复 P1 #32 主路径）."""

    def test_missing_file_returns_empty_dict(
        self, monitor, tmp_path: Path
    ) -> None:
        assert monitor.load_best_params(tmp_path / "absent.json") == {}

    def test_malformed_json_returns_empty_dict(
        self, monitor, tmp_path: Path
    ) -> None:
        bad = tmp_path / "best_params.json"
        bad.write_text("{not json", encoding="utf-8")
        assert monitor.load_best_params(bad) == {}

    def test_valid_file_round_trip(self, monitor, tmp_path: Path) -> None:
        good = tmp_path / "best_params.json"
        good.write_text(
            '{"688322": {"threshold": 5, "hold_days": 10}}', encoding="utf-8",
        )
        params = monitor.load_best_params(good)
        assert params == {"688322": {"threshold": 5, "hold_days": 10}}


# ====================================================================== #
# P1 #35: calc_indicators 不污染入参 DataFrame
# ====================================================================== #


class TestCalcIndicatorsImmutability:
    """calc_indicators 必须对入参做 .copy()（修复 P1 #35）.

    旧实现直接写 20+ 列回入参；与 fetch_stock_data 内的 rename_map 副作用一起，
    让上游 DataFrame 被未声明地修改。
    """

    def test_does_not_mutate_input(self, monitor) -> None:
        df = _make_df(n=80)
        original_cols = list(df.columns)
        original_close = df["收盘"].copy()

        result = monitor.calc_indicators(df)

        # 入参列不应被新增 DIF/DEA/MACD_BAR/BBI/RSI6/... 等指标列
        assert list(df.columns) == original_cols, \
            "calc_indicators 不允许给入参 DataFrame 新增列"
        # 入参原有列也不应被修改
        pd.testing.assert_series_equal(df["收盘"], original_close)

        # 返回的副本含全套指标
        for col in ("DIF", "DEA", "RSI6", "CCI14", "BOLL_MID", "MA20"):
            assert col in result.columns, f"返回 DataFrame 应包含 {col} 列"


# ====================================================================== #
# P2 #48: RSI / CCI / 各除法防 0/0 退化
# ====================================================================== #


class TestCalcIndicatorsEdgeCases:
    """RSI/CCI/WR 在常数序列等退化场景不应产生 NaN/inf（修复 #48 子项）."""

    def test_constant_close_no_inf_in_indicators(self, monitor) -> None:
        df = _make_df(n=80, constant=True)
        result = monitor.calc_indicators(df)

        # 排除 NaN 来源：rolling.std/mean 在 warm-up 期天然产 NaN（前 N 行），
        # 这里只验证 warm-up 之后没有 inf 串入（旧实现在分母为 0 时产 inf）
        for col in ("RSI6", "RSI12", "CCI14", "WR10", "K", "D"):
            tail = result[col].iloc[60:]  # 跳过 warm-up
            assert np.isfinite(tail).all(), (
                f"{col} warm-up 之后不应出现 inf/-inf；"
                f"前 5 个非法值: {tail[~np.isfinite(tail)].head().tolist()}"
            )

    def test_vol_ratio_5_handles_zero_volume(self, monitor) -> None:
        """成交量全 0 时 VOL_RATIO_5 也应是有限数（+1e-8 兜底）."""
        df = _make_df(n=80)
        df["成交量"] = 0.0
        result = monitor.calc_indicators(df)
        tail = result["VOL_RATIO_5"].iloc[10:]
        assert np.isfinite(tail).all()
