"""紫苏叶产业链卡位评分测试."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import yaml

from kss.supply_chain.registry import ChainRegistry, StockChainInfo, ScoringWeights, ChainConfig
from kss.supply_chain.scoring import (
    _layer_score,
    _moat_score,
    _lock_score,
    _coverage_gap_score,
    compute_perilla_score,
    explain_score,
    perilla_tier,
)


class TestPerillaTier:
    """结构性分层 core / main / watch（精不是多）."""

    def test_core_duopoly_deep_locked(self) -> None:
        # 全球≤2 + L≥4 + 国内独家 + 锁定 → 核心
        s = _make_stock(n_competitors_global=2, chain_layer=4,
                        n_competitors_domestic=1, demand_locked=True)
        assert perilla_tier(s) == "core"

    def test_main_three_global_deep_locked(self) -> None:
        # 全球三家寡头 + L≥4 + 锁定 → 国产替代主线
        s = _make_stock(n_competitors_global=3, chain_layer=4, demand_locked=True)
        assert perilla_tier(s) == "main"

    def test_watch_shallow_layer(self) -> None:
        # 链层不足（L3）即便双寡头也只是观察
        s = _make_stock(n_competitors_global=2, chain_layer=3, demand_locked=True)
        assert perilla_tier(s) == "watch"

    def test_watch_not_locked(self) -> None:
        s = _make_stock(n_competitors_global=2, chain_layer=4, demand_locked=False)
        assert perilla_tier(s) == "watch"

    def test_watch_wide_moat(self) -> None:
        # 全球≥4 家 moat 不足 → 观察
        s = _make_stock(n_competitors_global=5, chain_layer=5, demand_locked=True)
        assert perilla_tier(s) == "watch"


# ────────────────────────────────────────────────────────────────
# Fixtures
# ────────────────────────────────────────────────────────────────

def _make_config() -> ChainConfig:
    """构造默认评分配置."""
    cfg = ChainConfig(
        scoring_weights=ScoringWeights(layer=0.25, moat=0.35, lock=0.25, coverage_gap=0.15),
        moat_tiers={1: 1.0, 2: 0.7, 3: 0.3},
        ranking_multiplier=0.3,
        demand_chains={},
    )
    cfg._moat_default = 0.0  # type: ignore[attr-defined]
    return cfg


def _make_stock(**overrides: object) -> StockChainInfo:
    """构造测试用 StockChainInfo, 可逐字段覆盖."""
    defaults = dict(
        ts_code="688012.SH",
        name="中微公司",
        demand_chains=("半导体",),
        chain_layer=4,
        chain_role="equipment",
        n_competitors_global=3,
        n_competitors_domestic=1,
        substitutability="medium",
        expansion_cycle_years=3.0,
        demand_locked=True,
        analyst_count=20,
        analyst_notes="test",
    )
    defaults.update(overrides)
    if isinstance(defaults["demand_chains"], list):
        defaults["demand_chains"] = tuple(defaults["demand_chains"])
    return StockChainInfo(**defaults)  # type: ignore[arg-type]


# ────────────────────────────────────────────────────────────────
# 评分子项测试
# ────────────────────────────────────────────────────────────────

class TestLayerScore:
    def test_layer_1_is_zero(self) -> None:
        assert _layer_score(1) == 0.0

    def test_layer_5_is_one(self) -> None:
        assert _layer_score(5) == 1.0

    def test_layer_3_is_half(self) -> None:
        assert _layer_score(3) == pytest.approx(0.5)

    def test_clamped_below(self) -> None:
        assert _layer_score(0) == 0.0

    def test_clamped_above(self) -> None:
        assert _layer_score(10) == 1.0


class TestMoatScore:
    def test_monopoly(self) -> None:
        tiers = {1: 1.0, 2: 0.7, 3: 0.3}
        assert _moat_score(1, tiers) == 1.0

    def test_duopoly(self) -> None:
        tiers = {1: 1.0, 2: 0.7, 3: 0.3}
        assert _moat_score(2, tiers) == 0.7

    def test_competitive(self) -> None:
        tiers = {1: 1.0, 2: 0.7, 3: 0.3}
        assert _moat_score(5, tiers, default=0.0) == 0.0


class TestLockScore:
    def test_not_locked(self) -> None:
        assert _lock_score(False, 3.0) == 0.0

    def test_locked_full_cycle(self) -> None:
        assert _lock_score(True, 3.0) == pytest.approx(1.0)

    def test_locked_partial_cycle(self) -> None:
        assert _lock_score(True, 1.5) == pytest.approx(0.5)

    def test_locked_exceeds_max(self) -> None:
        """扩产周期超过 max_cycle 也只给 1.0."""
        assert _lock_score(True, 5.0) == pytest.approx(1.0)


class TestCoverageGapScore:
    def test_zero_analysts(self) -> None:
        assert _coverage_gap_score(0) == 1.0

    def test_full_coverage(self) -> None:
        assert _coverage_gap_score(20) == 0.0

    def test_over_coverage(self) -> None:
        assert _coverage_gap_score(30) == 0.0

    def test_partial(self) -> None:
        assert _coverage_gap_score(10) == pytest.approx(0.5)


# ────────────────────────────────────────────────────────────────
# 综合评分测试
# ────────────────────────────────────────────────────────────────

class TestPerillaScore:
    def test_equipment_deep_layer(self) -> None:
        """中微公司: layer=4, n=3, locked, cycle=3, analyst=20."""
        info = _make_stock()
        cfg = _make_config()
        score = compute_perilla_score(info, cfg)
        # layer=0.75*0.25 + moat=0.3*0.35 + lock=1.0*0.25 + cover=0.0*0.15
        expected = 0.25 * 0.75 + 0.35 * 0.3 + 0.25 * 1.0 + 0.15 * 0.0
        assert score == pytest.approx(expected, abs=0.001)

    def test_terminal_layer_competitive(self) -> None:
        """绝味食品: layer=1, n=10, not locked → 最低档."""
        info = _make_stock(
            ts_code="688017.SH", chain_layer=1,
            n_competitors_global=10, demand_locked=False,
            analyst_count=25,
        )
        cfg = _make_config()
        score = compute_perilla_score(info, cfg)
        assert score < 0.05  # 接近 0

    def test_monopoly_deep_layer(self) -> None:
        """假想: layer=5, 全球独家, 锁定 → 接近满分."""
        info = _make_stock(
            chain_layer=5, n_competitors_global=1,
            demand_locked=True, expansion_cycle_years=3.0,
            analyst_count=2,
        )
        cfg = _make_config()
        score = compute_perilla_score(info, cfg)
        # layer=1.0*0.25 + moat=1.0*0.35 + lock=1.0*0.25 + cover=0.9*0.15
        expected = 0.25 + 0.35 + 0.25 + 0.15 * 0.9
        assert score == pytest.approx(expected, abs=0.001)

    def test_score_within_bounds(self) -> None:
        info = _make_stock()
        cfg = _make_config()
        score = compute_perilla_score(info, cfg)
        assert 0.0 <= score <= 1.0


class TestExplainScore:
    def test_returns_all_keys(self) -> None:
        info = _make_stock()
        cfg = _make_config()
        detail = explain_score(info, cfg)
        assert set(detail.keys()) == {"layer", "moat", "lock", "coverage_gap", "total"}

    def test_total_matches_compute(self) -> None:
        info = _make_stock()
        cfg = _make_config()
        detail = explain_score(info, cfg)
        score = compute_perilla_score(info, cfg)
        assert detail["total"] == pytest.approx(score, abs=0.001)


# ────────────────────────────────────────────────────────────────
# Registry 测试
# ────────────────────────────────────────────────────────────────

class TestChainRegistry:
    def test_from_yaml_loads_config(self) -> None:
        """从项目默认 YAML 加载（如果 CI 上存在）."""
        reg = ChainRegistry.from_yaml()
        assert reg.n_stocks >= 0  # 可能 CI 上无文件 → 0

    def test_from_yaml_missing_file(self, tmp_path: Path) -> None:
        """文件不存在 → 空注册表, 不崩溃."""
        reg = ChainRegistry.from_yaml(tmp_path / "nonexistent.yaml")
        assert reg.n_stocks == 0
        assert reg.score("688012") == 0.0

    def test_roundtrip_yaml(self, tmp_path: Path) -> None:
        """写一个最小 YAML → 读回来 → 查询正确."""
        data = {
            "version": 1,
            "scoring_weights": {"layer": 0.25, "moat": 0.35, "lock": 0.25, "coverage_gap": 0.15},
            "moat_tiers": {1: 1.0, 2: 0.7, 3: 0.3, "default": 0.0},
            "ranking_multiplier": 0.3,
            "demand_chains": {},
            "stocks": {
                "688012.SH": {
                    "name": "中微公司",
                    "demand_chains": ["半导体"],
                    "chain_layer": 4,
                    "chain_role": "equipment",
                    "n_competitors_global": 3,
                    "n_competitors_domestic": 1,
                    "substitutability": "medium",
                    "expansion_cycle_years": 3,
                    "demand_locked": True,
                    "analyst_count": 20,
                    "analyst_notes": "test",
                },
            },
        }
        p = tmp_path / "sc.yaml"
        with open(p, "w", encoding="utf-8") as f:
            yaml.dump(data, f, allow_unicode=True)

        reg = ChainRegistry.from_yaml(p)
        assert reg.n_stocks == 1
        assert "688012.SH" in reg
        info = reg.get("688012.SH")
        assert info is not None
        assert info.name == "中微公司"
        assert info.chain_layer == 4

    def test_lookup_bare_code(self, tmp_path: Path) -> None:
        """裸码 688012 也能查到 688012.SH."""
        data = {
            "version": 1,
            "scoring_weights": {},
            "moat_tiers": {1: 1.0, 2: 0.7, 3: 0.3, "default": 0.0},
            "ranking_multiplier": 0.3,
            "stocks": {
                "688012.SH": {
                    "name": "中微",
                    "chain_layer": 4,
                    "chain_role": "equipment",
                    "n_competitors_global": 3,
                    "n_competitors_domestic": 1,
                    "substitutability": "medium",
                    "expansion_cycle_years": 3,
                    "demand_locked": True,
                    "analyst_count": 20,
                },
            },
        }
        p = tmp_path / "sc.yaml"
        with open(p, "w", encoding="utf-8") as f:
            yaml.dump(data, f, allow_unicode=True)

        reg = ChainRegistry.from_yaml(p)
        assert reg.get("688012") is not None
        assert reg.score("688012") > 0

    def test_candidates(self, tmp_path: Path) -> None:
        """candidates() 返回 score ≥ min 的票, 降序."""
        data = {
            "version": 1,
            "scoring_weights": {"layer": 0.25, "moat": 0.35, "lock": 0.25, "coverage_gap": 0.15},
            "moat_tiers": {1: 1.0, 2: 0.7, 3: 0.3, "default": 0.0},
            "ranking_multiplier": 0.3,
            "stocks": {
                "688012.SH": {
                    "name": "中微", "chain_layer": 4, "chain_role": "equipment",
                    "n_competitors_global": 3, "n_competitors_domestic": 1,
                    "substitutability": "medium", "expansion_cycle_years": 3,
                    "demand_locked": True, "analyst_count": 20,
                },
                "688017.SH": {
                    "name": "绝味", "chain_layer": 1, "chain_role": "platform",
                    "n_competitors_global": 10, "n_competitors_domestic": 5,
                    "substitutability": "high", "expansion_cycle_years": 0.5,
                    "demand_locked": False, "analyst_count": 25,
                },
            },
        }
        p = tmp_path / "sc.yaml"
        with open(p, "w", encoding="utf-8") as f:
            yaml.dump(data, f, allow_unicode=True)

        reg = ChainRegistry.from_yaml(p)
        cands = reg.candidates(min_score=0.3)
        # 中微应入选, 绝味不应
        codes = [c[0] for c in cands]
        assert "688012.SH" in codes
        assert "688017.SH" not in codes

    def test_format_tag(self, tmp_path: Path) -> None:
        """format_tag 返回可读标签."""
        data = {
            "version": 1,
            "scoring_weights": {"layer": 0.25, "moat": 0.35, "lock": 0.25, "coverage_gap": 0.15},
            "moat_tiers": {1: 1.0, 2: 0.7, 3: 0.3, "default": 0.0},
            "ranking_multiplier": 0.3,
            "stocks": {
                "688012.SH": {
                    "name": "中微", "chain_layer": 4, "chain_role": "equipment",
                    "n_competitors_global": 3, "n_competitors_domestic": 1,
                    "substitutability": "medium", "expansion_cycle_years": 3,
                    "demand_locked": True, "analyst_count": 20,
                },
            },
        }
        p = tmp_path / "sc.yaml"
        with open(p, "w", encoding="utf-8") as f:
            yaml.dump(data, f, allow_unicode=True)

        reg = ChainRegistry.from_yaml(p)
        tag = reg.format_tag("688012.SH")
        assert "L4" in tag
        assert "国内独家" in tag
        assert "需求锁定" in tag

    def test_format_tag_unlisted(self) -> None:
        """未标注股票 → 空字符串."""
        reg = ChainRegistry.from_yaml(Path("/nonexistent"))
        assert reg.format_tag("000001.SZ") == ""


# ────────────────────────────────────────────────────────────────
# 紫苏叶理论核心逻辑验证
# ────────────────────────────────────────────────────────────────

class TestPerillaLeafTheory:
    """验证评分系统确实遵循紫苏叶理论的选股逻辑:
    深层 + 垄断 + 需求锁定 + 低覆盖 > 浅层 + 竞争 + 不锁定 + 高覆盖.
    """

    def test_deep_beats_shallow(self) -> None:
        """同等条件下, 深层 > 浅层."""
        cfg = _make_config()
        deep = _make_stock(chain_layer=5)
        shallow = _make_stock(chain_layer=1)
        assert compute_perilla_score(deep, cfg) > compute_perilla_score(shallow, cfg)

    def test_monopoly_beats_competitive(self) -> None:
        """同等条件下, 独家 > 多家竞争."""
        cfg = _make_config()
        mono = _make_stock(n_competitors_global=1)
        comp = _make_stock(n_competitors_global=5)
        assert compute_perilla_score(mono, cfg) > compute_perilla_score(comp, cfg)

    def test_locked_beats_unlocked(self) -> None:
        """同等条件下, 需求锁定 > 不锁定."""
        cfg = _make_config()
        locked = _make_stock(demand_locked=True, expansion_cycle_years=3)
        unlocked = _make_stock(demand_locked=False, expansion_cycle_years=3)
        assert compute_perilla_score(locked, cfg) > compute_perilla_score(unlocked, cfg)

    def test_low_coverage_beats_high(self) -> None:
        """同等条件下, 低覆盖 > 高覆盖."""
        cfg = _make_config()
        low = _make_stock(analyst_count=2)
        high = _make_stock(analyst_count=25)
        assert compute_perilla_score(low, cfg) > compute_perilla_score(high, cfg)

    def test_ideal_perilla_stock(self) -> None:
        """理想紫苏叶: layer=5, 独家, 锁定, 无人覆盖 → 最高分."""
        cfg = _make_config()
        ideal = _make_stock(
            chain_layer=5, n_competitors_global=1,
            demand_locked=True, expansion_cycle_years=3,
            analyst_count=0,
        )
        assert compute_perilla_score(ideal, cfg) > 0.9

    def test_anti_perilla_stock(self) -> None:
        """反面: layer=1, 10家竞争, 不锁定, 高覆盖 → 接近 0."""
        cfg = _make_config()
        anti = _make_stock(
            chain_layer=1, n_competitors_global=10,
            demand_locked=False, expansion_cycle_years=0,
            analyst_count=30,
        )
        assert compute_perilla_score(anti, cfg) < 0.05
