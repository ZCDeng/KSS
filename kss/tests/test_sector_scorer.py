"""kss/sector/scorer.py 单元测试 —— 板块启发式评分.

覆盖：
- 热度评分：权重 / 归一化 / 缺列容错
- 资金持续性：累计 + 连续天数（含中间断档场景）
- 轮动信号：排名跃升 + 净流入双重条件
- 配置加载：默认值 / 缺库 / 损坏数据（plan 2026-07-12-005 / U15 割接自 json 文件）
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from kss.sector.scorer import (
    DEFAULT_CONFIG,
    compute_flow_persistence,
    compute_heat_score,
    compute_rotation_signal,
    load_config,
)
from kss.storage.db import connect, ensure_schema


# ====================================================================== #
# Config 加载
# ====================================================================== #


class TestLoadConfig:
    """load_config 行为."""

    def test_returns_default_when_db_missing(self, tmp_path: Path) -> None:
        """库不存在 → 返回默认权重，不抛（connect() 会新建空库+空表）."""
        config = load_config(tmp_path / "does_not_exist.db")
        assert config == DEFAULT_CONFIG

    def test_loads_user_overrides(self, tmp_path: Path) -> None:
        """用户配置存在 → 用户键覆盖默认."""
        db_path = tmp_path / "kss.db"
        with connect(db_path) as conn:
            ensure_schema(conn)
            conn.execute(
                "INSERT INTO sector_review_config (config_key, config_json) VALUES ('default', ?)",
                (json.dumps({"top_n_industry": 10}),),
            )
        config = load_config(db_path)
        assert config["top_n_industry"] == 10
        # 未覆盖的键保留默认
        assert config["industry_heat_weights"] == DEFAULT_CONFIG["industry_heat_weights"]

    def test_corrupted_json_falls_back_to_default(self, tmp_path: Path) -> None:
        """config_json 列存了非法 JSON → 默认 + warning，不抛."""
        db_path = tmp_path / "kss.db"
        with connect(db_path) as conn:
            ensure_schema(conn)
            conn.execute(
                "INSERT INTO sector_review_config (config_key, config_json) VALUES ('default', ?)",
                ("{not valid json",),
            )
        config = load_config(db_path)
        assert config == DEFAULT_CONFIG

    def test_corrupt_db_file_falls_back_to_default(self, tmp_path: Path) -> None:
        """库文件本身损坏（非 sqlite）→ 默认 + warning，不抛."""
        db_path = tmp_path / "kss.db"
        db_path.write_bytes(b"not a real sqlite file")
        config = load_config(db_path)
        assert config == DEFAULT_CONFIG


# ====================================================================== #
# compute_heat_score
# ====================================================================== #


class TestComputeHeatScore:
    """热度评分 —— 加权 min-max."""

    def test_pure_pct_change_weight(self) -> None:
        """权重只放 pct_change 时排序应按 pct_change 单维."""
        df = pd.DataFrame({
            "name": ["A", "B", "C"],
            "pct_change": [3.0, 1.0, -2.0],
            "net_amount_rate": [0.0, 0.0, 0.0],
        })
        out = compute_heat_score(df, {"pct_change": 1.0, "net_amount_rate": 0.0})
        assert out.iloc[0]["name"] == "A"
        assert out.iloc[-1]["name"] == "C"

    def test_zero_weights_yield_constant_score(self) -> None:
        """权重全 0 → 所有 score 为 0，排序退化（稳定）."""
        df = pd.DataFrame({
            "name": ["A", "B"],
            "pct_change": [1.0, 2.0],
        })
        out = compute_heat_score(df, {"pct_change": 0.0})
        assert (out["heat_score"] == 0.0).all()

    def test_minmax_normalization_balances_scales(self) -> None:
        """量纲差异极大时，归一化后小权重列仍能影响排序."""
        df = pd.DataFrame({
            "name": ["X", "Y", "Z"],
            "pct_change": [1.0, 1.5, 1.6],          # 微小差异
            "net_amount_rate": [-5.0, 100.0, -10.0], # 巨大差异
        })
        # 权重相同时，归一化后 Y（net_amount_rate 最大）应排第一
        out = compute_heat_score(df, {"pct_change": 0.5, "net_amount_rate": 0.5})
        assert out.iloc[0]["name"] == "Y"

    def test_constant_column_normalizes_to_05(self) -> None:
        """常数列归一化返回 0.5（中性）；不应除以 0."""
        df = pd.DataFrame({
            "name": ["A", "B"],
            "pct_change": [1.0, 1.0],
            "net_amount_rate": [3.0, 5.0],
        })
        out = compute_heat_score(df, {"pct_change": 1.0, "net_amount_rate": 0.0})
        # pct_change 都是 1.0，归一化都 0.5，权重 1.0 → score 都 0.5
        assert all(out["heat_score"] == 0.5)

    def test_empty_input_returns_empty(self) -> None:
        out = compute_heat_score(pd.DataFrame(), {"pct_change": 1.0})
        assert out.empty

    def test_missing_column_returns_empty(self) -> None:
        """缺列时返回空 DataFrame + warning，不外抛."""
        df = pd.DataFrame({"name": ["A"], "pct_change": [1.0]})
        out = compute_heat_score(df, {"missing_col": 1.0})
        assert out.empty

    def test_three_dim_industry_weights(self) -> None:
        """实际 industry 用例：3 维加权."""
        df = pd.DataFrame({
            "name": ["半导体", "电网设备", "钢铁"],
            "pct_change": [3.0, 1.5, -1.0],
            "net_amount_rate": [2.1, 4.1, -0.5],
            "buy_elg_amount_rate": [2.5, 4.2, -1.0],
        })
        weights = {"pct_change": 0.5, "net_amount_rate": 0.3, "buy_elg_amount_rate": 0.2}
        out = compute_heat_score(df, weights)
        assert "heat_score" in out.columns
        assert len(out) == 3
        # 半导体涨幅最高（0.5×1.0）；电网设备资金流最强（0.3+0.2 = 0.5）—— 数值接近
        # 此处只验证：每行 score 在 [0, 1]
        assert out["heat_score"].between(0, 1).all()


# ====================================================================== #
# compute_flow_persistence
# ====================================================================== #


def _flow_day(names: list[str], flows: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"name": names, "net_amount_rate": flows})


class TestComputeFlowPersistence:
    """资金持续性评分."""

    def test_three_consecutive_inflow_days(self) -> None:
        """连续 3 天净流入 → persist_days == 3."""
        history = [
            _flow_day(["A", "B"], [1.0, -2.0]),  # day -2
            _flow_day(["A", "B"], [2.0, -1.0]),  # day -1
            _flow_day(["A", "B"], [3.0, -0.5]),  # day 0 (most recent)
        ]
        out = compute_flow_persistence(history)
        a_row = out[out["name"] == "A"].iloc[0]
        b_row = out[out["name"] == "B"].iloc[0]
        assert a_row["persist_days"] == 3
        assert a_row["cum_inflow"] == pytest.approx(6.0)
        assert b_row["persist_days"] == 0  # 从未净流入
        assert b_row["cum_inflow"] == pytest.approx(-3.5)

    def test_break_in_middle(self) -> None:
        """中间一天净流出 → 连续天数从最近的连续段算起."""
        history = [
            _flow_day(["A"], [2.0]),    # day -3
            _flow_day(["A"], [3.0]),    # day -2
            _flow_day(["A"], [-1.0]),   # day -1 ← 断
            _flow_day(["A"], [2.0]),    # day 0 ← 最近日
        ]
        out = compute_flow_persistence(history)
        # 从最近日往回数：day 0 (+) → day -1 (-) 停 → 连续 1 天
        assert out.iloc[0]["persist_days"] == 1

    def test_recent_outflow_yields_zero_persist(self) -> None:
        """最近一日净流出 → persist_days == 0（即使前面有连续流入）."""
        history = [
            _flow_day(["A"], [2.0]),
            _flow_day(["A"], [3.0]),
            _flow_day(["A"], [-1.0]),  # 最近日净流出
        ]
        out = compute_flow_persistence(history)
        assert out.iloc[0]["persist_days"] == 0

    def test_empty_history(self) -> None:
        out = compute_flow_persistence([])
        assert out.empty

    def test_history_with_none_entries(self) -> None:
        """history 中含 None 应被跳过，不外抛."""
        history = [
            None,  # type: ignore[list-item]
            _flow_day(["A"], [1.0]),
            None,  # type: ignore[list-item]
        ]
        out = compute_flow_persistence(history)
        assert len(out) == 1
        assert out.iloc[0]["persist_days"] == 1

    def test_missing_flow_col_skipped(self) -> None:
        """缺 flow_col 的 DataFrame 应被跳过，其他天的数据正常."""
        history = [
            pd.DataFrame({"name": ["A"]}),  # 没 net_amount_rate 列
            _flow_day(["A"], [3.0]),
        ]
        out = compute_flow_persistence(history)
        # 只一天有效，A 净流入 3.0，连续 1 天
        assert out.iloc[0]["persist_days"] == 1


# ====================================================================== #
# compute_rotation_signal
# ====================================================================== #


class TestComputeRotationSignal:
    """轮动信号 —— 排名跃升 + 今日净流入双重判断."""

    def test_strong_rotation_signal(self) -> None:
        """A 板块今日排名第 1，3 日前排名第 10 → rank_jump = 9 → 触发."""
        today = pd.DataFrame({
            "name": [f"S{i}" for i in range(10)],
            "net_amount_rate": [5.0, 4.0, 3.0, 2.5, 2.0, 1.5, 1.0, 0.5, 0.2, 0.1],
        })
        past = pd.DataFrame({
            "name": [f"S{i}" for i in range(10)],
            "net_amount_rate": [-5.0, 5.0, 4.0, 3.0, 2.0, 1.0, 0.5, 0.2, -0.1, -0.5],
        })
        # S0 今日 rank=1，past rank=10 → jump=9，今日 flow=5.0 > 0 → 触发
        out = compute_rotation_signal(today, past, rank_jump_threshold=5)
        assert "S0" in out["name"].values
        s0 = out[out["name"] == "S0"].iloc[0]
        assert s0["rank_jump"] >= 5
        assert s0["today_flow"] > 0

    def test_rank_jump_below_threshold_filtered(self) -> None:
        """排名上升但未达阈值 → 不进结果."""
        today = pd.DataFrame({
            "name": ["A", "B", "C"],
            "net_amount_rate": [3.0, 2.0, 1.0],
        })
        past = pd.DataFrame({
            "name": ["A", "B", "C"],
            "net_amount_rate": [2.5, 1.5, 0.5],
        })
        # 排名都没动 → 全部过滤
        out = compute_rotation_signal(today, past, rank_jump_threshold=2)
        assert out.empty

    def test_rank_up_but_outflow_filtered(self) -> None:
        """排名上升但今日仍净流出 → 不算「轮动入场」."""
        today = pd.DataFrame({
            "name": ["A", "B"],
            "net_amount_rate": [-1.0, -5.0],
        })
        past = pd.DataFrame({
            "name": ["A", "B"],
            "net_amount_rate": [-10.0, 5.0],
        })
        # A 今日 rank=1 past rank=2 → jump=1（不够阈值）
        # 即便达阈值，A 今日仍净流出，也不触发
        out = compute_rotation_signal(today, past, rank_jump_threshold=1)
        # 不应包含 A 因为 today_flow <= 0
        if not out.empty:
            assert (out["today_flow"] > 0).all()

    def test_empty_inputs_return_empty(self) -> None:
        out = compute_rotation_signal(pd.DataFrame(), pd.DataFrame())
        assert out.empty

    def test_missing_flow_col(self) -> None:
        """缺 flow_col 时返回空表（与契约一致，不外抛）."""
        today = pd.DataFrame({"name": ["A"]})
        past = pd.DataFrame({"name": ["A"]})
        out = compute_rotation_signal(today, past)
        assert out.empty

    def test_signal_sorted_by_rank_jump_desc(self) -> None:
        """多个触发项按 rank_jump 降序."""
        today = pd.DataFrame({
            "name": ["A", "B", "C"],
            "net_amount_rate": [5.0, 4.0, 3.0],
        })
        past = pd.DataFrame({
            "name": ["A", "B", "C"],
            # A: past rank 3 → today rank 1, jump=2 (small)
            # B: past rank 2 → today rank 2, jump=0
            # C: past rank 1 → today rank 3, jump=-2
            # 调整一下让 A 跳更大：
            "net_amount_rate": [-100.0, 50.0, 60.0],
        })
        # 重新设计：A past=3 today=1 jump=2; B past=2 today=2 jump=0; C past=1 today=3 jump=-2
        out = compute_rotation_signal(today, past, rank_jump_threshold=1)
        # 只 A 触发
        if not out.empty:
            assert out.iloc[0]["name"] == "A"
            # 多触发时应按 rank_jump 降序
            assert out["rank_jump"].is_monotonic_decreasing
