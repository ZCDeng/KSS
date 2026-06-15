"""U3 泄漏隔离断言测试（test-first，最高风险失败模式）.

镜像 ``kss/tests/test_adversarial.py::test_lookahead_factor_caught_by_purge_gap``
的写法：构造会泄漏未来的输入，断言隔离守卫抛错。Kronos 吃滑动窗口、无内建
split/泄漏防护，隔离由 KSS 这层保证（见 docs/solutions/known_bias_gaps.md：
特征级 look-ahead 当前无防护）。

R4/R5/R6/R7，Covers AE5。
"""

from __future__ import annotations

import pandas as pd
import pytest

from kss.kronos.isolation import (
    FreezeMetadata,
    LeakageError,
    assert_after_cutoff,
    assert_train_before_cutoff,
    assert_window_precedes_target,
    resolve_cutoff_date,
)


class TestWindowPrecedesTarget:
    def test_covers_ae5_window_end_crosses_target_raises(self) -> None:
        """Covers AE5: 输入窗口末尾跨越（>=）被预测 bar → 隔离断言抛错.

        这是特征级 look-ahead 的核心防线：喂给模型的最后一根历史 bar 必须严格
        早于第一根被预测 bar，否则模型在"预测"时已看到答案。
        """
        x_ts = pd.Series(pd.to_datetime(["2025-01-06", "2025-01-07", "2025-01-08"]))
        # y 的首个被预测 bar 与 x 末尾同日 → 跨越
        y_ts = pd.Series(pd.to_datetime(["2025-01-08", "2025-01-09"]))
        with pytest.raises(LeakageError, match="窗口末尾"):
            assert_window_precedes_target(x_ts, y_ts)

    def test_window_end_after_target_raises(self) -> None:
        """x 末尾晚于 y 首个 bar（更严重的穿透）→ 抛错."""
        x_ts = pd.Series(pd.to_datetime(["2025-01-06", "2025-01-10"]))
        y_ts = pd.Series(pd.to_datetime(["2025-01-08", "2025-01-09"]))
        with pytest.raises(LeakageError):
            assert_window_precedes_target(x_ts, y_ts)

    def test_happy_strictly_earlier_passes(self) -> None:
        """happy: x 末尾严格早于 y 首个 bar → 不抛."""
        x_ts = pd.Series(pd.to_datetime(["2025-01-06", "2025-01-07"]))
        y_ts = pd.Series(pd.to_datetime(["2025-01-08", "2025-01-09"]))
        assert_window_precedes_target(x_ts, y_ts)  # 不抛即通过


class TestTrainBeforeCutoff:
    def test_edge_train_after_cutoff_raises(self) -> None:
        """edge: 训练集含 D 之后样本 → 断言失败."""
        cutoff = pd.Timestamp("2025-03-31")
        ts = pd.Series(pd.to_datetime(["2025-03-30", "2025-03-31", "2025-04-01"]))
        with pytest.raises(LeakageError, match="截断日"):
            assert_train_before_cutoff(ts, cutoff)

    def test_happy_all_on_or_before_cutoff_passes(self) -> None:
        """happy: 全部样本 ≤ D → 不抛（含端点 D 合法）."""
        cutoff = pd.Timestamp("2025-03-31")
        ts = pd.Series(pd.to_datetime(["2025-03-30", "2025-03-31"]))
        assert_train_before_cutoff(ts, cutoff)


class TestAfterCutoff:
    def test_forward_must_be_after_cutoff(self) -> None:
        """影子/压测只跑 D 之后：y ≤ D → 抛错."""
        cutoff = pd.Timestamp("2025-03-31")
        ts = pd.Series(pd.to_datetime(["2025-03-31", "2025-04-01"]))  # 含端点 D
        with pytest.raises(LeakageError, match="截断日之后"):
            assert_after_cutoff(ts, cutoff)

    def test_strictly_after_passes(self) -> None:
        cutoff = pd.Timestamp("2025-03-31")
        ts = pd.Series(pd.to_datetime(["2025-04-01", "2025-04-02"]))
        assert_after_cutoff(ts, cutoff)


class TestResolveCutoffDate:
    def test_auto_leaves_forward_window(self) -> None:
        """自动推导：D 之后恰好留 forward_window 个交易日."""
        dates = pd.bdate_range("2024-01-01", periods=100)
        d = resolve_cutoff_date(dates, forward_window=60)
        after = dates[dates > d]
        assert len(after) == 60

    def test_explicit_wins(self) -> None:
        dates = pd.bdate_range("2024-01-01", periods=100)
        d = resolve_cutoff_date(dates, explicit="2024-02-15", forward_window=60)
        assert d == pd.Timestamp("2024-02-15")

    def test_insufficient_dates_raises(self) -> None:
        dates = pd.bdate_range("2024-01-01", periods=30)
        with pytest.raises(ValueError, match="不足"):
            resolve_cutoff_date(dates, forward_window=60)


class TestFreezeMetadata:
    def test_roundtrip(self, tmp_path) -> None:
        """artifact 元数据带 D 标记，可落盘 + 读回."""
        meta = FreezeMetadata(
            cutoff_date="2025-03-31",
            base_model_repo="NeoQuasar/Kronos-base",
            base_tokenizer_repo="NeoQuasar/Kronos-Tokenizer-base",
            n_train_samples=1000,
            n_symbols=50,
            lookback_window=90,
            predict_window=10,
        )
        p = tmp_path / "freeze_meta.json"
        meta.save(p)
        loaded = FreezeMetadata.load(p)
        assert loaded.cutoff_date == "2025-03-31"
        assert loaded.n_symbols == 50
