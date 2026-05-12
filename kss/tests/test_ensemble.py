"""MultiPeriodEnsemble / BackendEnsemble 单元测试."""

from __future__ import annotations

import pandas as pd
import pytest

from kss.strategies.ensemble import BackendEnsemble, MultiPeriodEnsemble


class TestMultiPeriodEnsemble:

    def test_weights_normalized(self) -> None:
        ens = MultiPeriodEnsemble({5: 1, 10: 2, 20: 1})
        # sum=4 → 0.25 / 0.5 / 0.25
        assert ens.weights[5] == pytest.approx(0.25)
        assert ens.weights[10] == pytest.approx(0.5)
        assert ens.weights[20] == pytest.approx(0.25)

    def test_zero_weights_raises(self) -> None:
        with pytest.raises(ValueError, match="总和必须"):
            MultiPeriodEnsemble({5: 0, 10: 0})

    def test_empty_raises(self) -> None:
        with pytest.raises(ValueError, match="不能为空"):
            MultiPeriodEnsemble({})

    def test_combine_preserves_index(self) -> None:
        s5 = pd.Series([1.0, 2.0, 3.0], index=["A", "B", "C"])
        s10 = pd.Series([0.5, 0.7, 0.3], index=["A", "B", "C"])
        ens = MultiPeriodEnsemble({5: 0.5, 10: 0.5})
        combined = ens.combine_scores({5: s5, 10: s10})
        assert list(combined.index) == ["A", "B", "C"]

    def test_combine_with_missing_period_renormalizes(self) -> None:
        """只提供部分周期时，权重应在所提供周期内重新归一化."""
        s5 = pd.Series([1.0, 2.0, 3.0], index=["A", "B", "C"])
        ens = MultiPeriodEnsemble({5: 0.5, 10: 0.5})
        # 只提供 5d
        combined = ens.combine_scores({5: s5})
        # 单周期相当于直接 rank-pct
        expected = s5.rank(pct=True)
        assert (combined - expected).abs().max() < 1e-12

    def test_no_intersection_raises(self) -> None:
        s = pd.Series([1.0, 2.0], index=["A", "B"])
        ens = MultiPeriodEnsemble({5: 1.0})
        with pytest.raises(ValueError, match="不交集"):
            ens.combine_scores({99: s})

    def test_combined_rank_for_monotonic_inputs(self) -> None:
        """各模型都把 A 排最高 → 融合分数也是 A 最高."""
        idx = ["A", "B", "C", "D"]
        s5 = pd.Series([4, 3, 2, 1], index=idx)
        s10 = pd.Series([40, 30, 20, 10], index=idx)
        ens = MultiPeriodEnsemble({5: 0.5, 10: 0.5})
        combined = ens.combine_scores({5: s5, 10: s10})
        assert combined.idxmax() == "A"
        assert combined.idxmin() == "D"


class TestBackendEnsemble:

    def test_dl_none_falls_back_to_lgb_rank(self) -> None:
        be = BackendEnsemble()
        lgb = pd.Series([3.0, 1.0, 2.0], index=["A", "B", "C"])
        out = be.combine(lgb, None)
        expected = lgb.rank(pct=True)
        assert (out - expected).abs().max() < 1e-12

    def test_weights_normalized(self) -> None:
        be = BackendEnsemble(lgb_weight=0.7, dl_weight=0.3)
        # 0.7 / 1.0 / 0.3 / 1.0
        assert abs(be.lgb_weight - 0.7) < 1e-12
        assert abs(be.dl_weight - 0.3) < 1e-12

        be2 = BackendEnsemble(lgb_weight=2, dl_weight=2)
        assert abs(be2.lgb_weight - 0.5) < 1e-12

    def test_dl_missing_symbols_filled_neutral(self) -> None:
        """DL 漏掉的 symbol 用 0.5（中性）填充."""
        be = BackendEnsemble(lgb_weight=0.5, dl_weight=0.5)
        lgb = pd.Series([3.0, 1.0, 2.0], index=["A", "B", "C"])
        dl = pd.Series([0.1, 0.9], index=["A", "B"])
        out = be.combine(lgb, dl)
        # C 在 DL 中缺，DL 端记 0.5；LGB rank(C) = 2/3
        # final(C) = 0.5 * 2/3 + 0.5 * 0.5 = 0.5833
        assert abs(out["C"] - (0.5 * 2 / 3 + 0.5 * 0.5)) < 1e-12

    def test_zero_weights_raises(self) -> None:
        with pytest.raises(ValueError, match="必须 > 0"):
            BackendEnsemble(lgb_weight=0, dl_weight=0)
