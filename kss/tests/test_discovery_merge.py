"""U6 —— 统一发现管道合并 + 管道级 alpha 测试.

覆盖 plan U6 验收 + brainstorm Acceptance Examples：

截 1（bridge 内 _discovery_merge，stdlib）：
- 多管道命中 → 共识溢价升权（Example B）；
- 跨管道同 ts_code 去重，仅出现一次（SC3）；
- None score 管道不当 0 分、不计入分母（Example C）；
- **相关性预检：高相关（Jaccard>=0.6）时共识溢价被抑制**（Example D / 关键护栏）；
- 板块管道成分股展开（leaderStocks → ts_code）+ 无 leaderStocks 降级；
- pipeline_weights 缺失 → 等权；空输入不抛异常（SC1）。

截 2（compute_pipeline_alpha）：
- 管道 IC + top-quintile alpha 计算可行（小样本）；
- 无对齐样本 → feasible=False（fail loud，不静默假装可信）；
- 合并候选相对单管道在 IC 上不劣化（小样本理性检查）；
- #8 双源：回测先验注入走仲裁。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _load_bridge():
    """以模块形式加载 scripts/kss_app_bridge.py（stdlib，纯函数可单测）。"""
    spec = importlib.util.spec_from_file_location(
        "kss_app_bridge", PROJECT_ROOT / "scripts" / "kss_app_bridge.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bridge = _load_bridge()


def _result(
    pid: str,
    items: list[tuple[str, float | None]],
    date: str = "20260620",
    signal_role: str | None = None,
) -> dict:
    """构造 PipelineResult dict（ts_code, score）。"""
    result = {
        "pipeline_id": pid,
        "date": date,
        "candidates": [
            {"ts_code": c, "name": c, "score": s, "raw_score": s, "metadata": {}}
            for c, s in items
        ],
    }
    if signal_role is not None:
        result["signal_role"] = signal_role
    return result


EQUAL_W = {pid: 0.25 for pid in bridge.PIPELINE_IDS}


# ===================================================================== #
# 截 1：合并 / 去重 / 共识溢价
# ===================================================================== #


def test_single_hit_no_bonus():
    """单管道命中 → 无共识溢价（Example A）。"""
    results = [
        _result("log_mv", [("688008.SH", 0.9), ("688009.SH", 0.8)]),
        _result("sector_hotspot", [("688120.SH", 0.7)]),
    ]
    out = bridge._discovery_merge(results, pipeline_weights=EQUAL_W)
    by_code = {c["ts_code"]: c for c in out["candidates"]}
    assert len(out["candidates"]) == 3
    for c in out["candidates"]:
        assert c["hit_count"] == 1
        assert c["consensus_applied"] is False
        # final == base（无溢价）
        assert c["final_score"] == pytest.approx(c["base_score"])


def test_cross_pipeline_consensus_bonus():
    """跨管道共识 → hit_count=2，综合分 = 加权和 × consensus_multiplier（Example B）。

    两管道候选集低相关（仅 688017 重合，Jaccard<0.6），共识是独立确认 → 享受溢价。
    """
    results = [
        _result("log_mv", [("688017.SH", 0.85), ("688001.SH", 0.5), ("688002.SH", 0.4)]),
        _result("sector_hotspot", [("688017.SH", 0.72), ("688003.SH", 0.5), ("688004.SH", 0.4)]),
    ]
    out = bridge._discovery_merge(results, pipeline_weights=EQUAL_W,
                                  consensus_multiplier=1.2)
    c = [x for x in out["candidates"] if x["ts_code"] == "688017.SH"][0]
    assert c["hit_count"] == 2
    assert sorted(c["sources"]) == ["log_mv", "sector_hotspot"]
    assert c["consensus_applied"] is True
    base = 0.25 * 0.85 + 0.25 * 0.72
    assert c["base_score"] == pytest.approx(base, abs=1e-6)
    assert c["final_score"] == pytest.approx(base * 1.2, abs=1e-6)


def test_dedup_across_pipelines():
    """同 ts_code 跨三管道 → 合并结果仅出现一次（SC3）。"""
    results = [
        _result("log_mv", [("688017.SH", 0.8)]),
        _result("sector_hotspot", [("688017.SH", 0.7)]),
        _result("supply_chain", [("688017.SH", 0.6)]),
    ]
    out = bridge._discovery_merge(results, pipeline_weights=EQUAL_W)
    codes = [c["ts_code"] for c in out["candidates"]]
    assert codes.count("688017.SH") == 1
    assert out["candidates"][0]["hit_count"] == 3


def test_none_score_excluded_from_denominator():
    """None score 管道不当 0 分、不计入命中（Example C）。"""
    results = [
        _result("log_mv", [("688017.SH", 0.8)]),
        _result("supply_chain", [("688017.SH", None)]),  # 未覆盖 → None
    ]
    out = bridge._discovery_merge(results, pipeline_weights=EQUAL_W)
    c = out["candidates"][0]
    assert c["hit_count"] == 1  # None 管道不计入
    assert c["sources"] == ["log_mv"]
    assert "supply_chain" not in c["pipeline_scores"]


def test_research_overlay_does_not_create_alpha_candidate_or_bonus():
    """静态结构研究层不能凭自身进入 alpha 排名，也不能制造共识溢价."""
    overlay = _result(
        "supply_chain",
        [("688017.SH", 0.9)],
        signal_role="research_overlay",
    )
    only_overlay = bridge._discovery_merge([overlay], pipeline_weights=EQUAL_W)
    assert only_overlay["candidates"] == []

    results = [
        _result("log_mv", [("688017.SH", 0.8)]),
        overlay,
    ]
    out = bridge._discovery_merge(results, pipeline_weights=EQUAL_W)
    candidate = out["candidates"][0]
    assert candidate["hit_count"] == 1
    assert candidate["sources"] == ["log_mv"]
    assert candidate["research_overlays"] == ["supply_chain"]
    assert candidate["consensus_applied"] is False
    assert candidate["base_score"] == pytest.approx(0.25 * 0.8)


def test_research_overlay_is_excluded_from_correlation_precheck():
    """研究层与 alpha 集合重合不代表两条独立信号高度相关."""
    results = [
        _result("log_mv", [("688017.SH", 0.8), ("688018.SH", 0.7)]),
        _result(
            "supply_chain",
            [("688017.SH", 0.9), ("688018.SH", 0.8)],
            signal_role="research_overlay",
        ),
    ]
    out = bridge._discovery_merge(results, pipeline_weights=EQUAL_W)
    assert out["warnings"] == []


def test_supply_chain_adapter_uses_structural_date_and_overlay_role(monkeypatch):
    """紫苏叶 adapter 不得用今天伪装静态标注日期，也不得声明为 alpha."""
    monkeypatch.setattr(
        bridge,
        "_perilla_picks",
        lambda **_: [{
            "symbol": "688017.SH",
            "name": "样例",
            "score": 0.8,
            "layer": 4,
            "role": "material",
            "assessmentStatus": "needs_review",
            "reviewFlags": ["待补证据来源"],
            "structuralAsOf": "2026-06-30",
            "evidenceAsOf": None,
        }],
    )

    result = bridge._adapt_supply_chain()

    assert result is not None
    assert result["signal_role"] == bridge.RESEARCH_OVERLAY_ROLE
    assert result["validation_status"] == "point_in_time_recording"
    assert result["date"] == "20260630"
    assert result["degraded"] == "evidence_review_required"


# ===================================================================== #
# 截 1：相关性预检（关键护栏）
# ===================================================================== #


def test_correlation_precheck_suppresses_consensus():
    """高相关（Jaccard>=0.6）→ 共识溢价被抑制（Example D / 关键护栏）。

    两管道候选集几乎重合（独立性证伪）→ 它们之间的「共识」是放大共同偏差，
    不该享受溢价。门控在已证独立性上。
    """
    # 两管道命中集合完全相同 → Jaccard=1.0 >= 0.6
    shared = [("688008.SH", 0.8), ("688009.SH", 0.7), ("688017.SH", 0.6)]
    results = [
        _result("sector_hotspot", shared),
        _result("bj50_scan", shared),
    ]
    out = bridge._discovery_merge(results, pipeline_weights=EQUAL_W,
                                  consensus_multiplier=1.2)
    # 高相关对进 warnings
    assert ["bj50_scan", "sector_hotspot"] in [sorted(w) for w in out["warnings"]]
    # 这些票虽 hit_count=2，但溢价被抑制 → final == base
    for c in out["candidates"]:
        assert c["hit_count"] == 2
        assert c["consensus_applied"] is False
        assert c["consensus_suppressed"] is True
        assert c["final_score"] == pytest.approx(c["base_score"], abs=1e-6)


def test_low_correlation_keeps_consensus():
    """低相关（Jaccard<0.6）→ 共识溢价保留（独立确认才升权）。"""
    results = [
        # 仅 688017 重合，其余各异 → Jaccard = 1/5 = 0.2 < 0.6
        _result("log_mv", [("688017.SH", 0.8), ("688001.SH", 0.7), ("688002.SH", 0.6)]),
        _result("sector_hotspot", [("688017.SH", 0.7), ("688003.SH", 0.6), ("688004.SH", 0.5)]),
    ]
    out = bridge._discovery_merge(results, pipeline_weights=EQUAL_W,
                                  consensus_multiplier=1.2)
    assert out["warnings"] == []
    shared = [c for c in out["candidates"] if c["ts_code"] == "688017.SH"][0]
    assert shared["hit_count"] == 2
    assert shared["consensus_applied"] is True
    assert shared["final_score"] > shared["base_score"]


# ===================================================================== #
# 截 1：板块成分股展开 + 降级 + 鲁棒性
# ===================================================================== #


def test_sector_leader_expansion():
    """板块热点 → leaderStocks 展开成 ts_code，heatScore 继承。"""
    snap = {
        "tradeDate": "20260620",
        "leaderBoards": [
            {"name": "AI算力", "heatScore": 0.9,
             "leaderStocks": [{"code": "688008.SH", "name": "A"},
                              {"code": "688009.SH", "name": "B"}]},
        ],
        "industries": [], "concepts": [],
    }
    expanded = bridge._expand_sector_leaders(snap)
    codes = {e["ts_code"] for e in expanded}
    assert codes == {"688008.SH", "688009.SH"}
    assert all(e["heat"] == 0.9 for e in expanded)


def test_sector_no_leaderstocks_degrades():
    """归档无 leaderStocks（实测当前归档即如此）→ 板块管道空候选，不编造 ts_code。"""
    snap = {"tradeDate": "20260620", "leaderBoards": [],
            "industries": [{"name": "X", "heatScore": 0.8, "leaderStocks": []}],
            "concepts": []}
    expanded = bridge._expand_sector_leaders(snap)
    assert expanded == []


def test_empty_input_no_exception():
    """空输入 / 全 None 不抛异常（SC1）。"""
    out = bridge._discovery_merge([], pipeline_weights=EQUAL_W)
    assert out["candidates"] == []
    assert out["warnings"] == []


def test_weights_missing_defaults_equal():
    """pipeline_weights=None 且无文件 → 等权（不抛）。"""
    results = [_result("log_mv", [("688008.SH", 0.5)])]
    # 传 None → 走 _load_pipeline_weights（文件存在则用文件，等权初始）
    out = bridge._discovery_merge(results, pipeline_weights=None)
    assert "log_mv" in out["pipelineWeights"]
    assert out["candidates"][0]["ts_code"] == "688008.SH"


# ===================================================================== #
# 截 2：管道 alpha / IC 计算
# ===================================================================== #


def _load_alpha_mod():
    spec = importlib.util.spec_from_file_location(
        "compute_pipeline_alpha", PROJECT_ROOT / "scripts" / "compute_pipeline_alpha.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_pipeline_alpha_computes_with_synthetic(monkeypatch):
    """合成命中 + 前向收益 → IC/top-quintile alpha 可算（小样本）。

    构造 score 与前向收益正相关的 6 个交易日 → IC 应为正、feasible=True。
    """
    alpha = _load_alpha_mod()
    from kss.backtest.factor_health import load_thresholds

    # 6 日，每日 5 票，score 越高收益越高（正 IC）
    hits = {}
    fwd_table = {}
    for d in range(6):
        date = f"2026-06-0{d+1}"
        day = []
        for k in range(5):
            code = f"{600000 + d*10 + k}.SH"
            score = float(k)            # k 越大 score 越高
            day.append((code, score))
            fwd_table[(code, date)] = 0.01 * k  # 收益随 score 单调 → 正 IC
        hits[date] = day

    monkeypatch.setattr(alpha, "_forward_return",
                        lambda code, date, hz: fwd_table.get((code, date)))
    res = alpha.compute_pipeline_alpha(
        "synthetic", hits, horizon=5, thresholds=load_thresholds(), cpcv_prior=None,
    )
    assert res["feasible"] is True
    assert res["ic_mean"] > 0           # 正相关 → 正 IC
    assert res["sample_n"] == 6         # 去重交易日数
    assert res["top_quintile_alpha"] is not None


def test_pipeline_alpha_infeasible_fail_loud(monkeypatch):
    """无对齐样本 → feasible=False（fail loud，不静默假装可信）。"""
    alpha = _load_alpha_mod()
    from kss.backtest.factor_health import load_thresholds

    hits = {"2026-06-01": [("999999.SH", 0.5)]}
    monkeypatch.setattr(alpha, "_forward_return", lambda c, d, h: None)
    res = alpha.compute_pipeline_alpha(
        "x", hits, horizon=5, thresholds=load_thresholds(), cpcv_prior=None,
    )
    assert res["feasible"] is False
    assert res["sample_n"] == 0


def test_pipeline_alpha_dual_source_injection(monkeypatch):
    """#8 双源：回测先验注入走仲裁（先验在 backtest_prior 字段回显）。"""
    alpha = _load_alpha_mod()
    from kss.backtest.factor_health import load_thresholds

    hits = {}
    fwd_table = {}
    # 25 日过 min_n=20；每日 score 与收益正相关但带轻微扰动 → 日级 IC<1，ICIR 有限非零
    perturb = [0.0, 1.0, 1.5, 3.0, 4.0]  # 单调但非等距 → 多数日 IC 高、个别日略降
    for d in range(25):
        date = f"2026-07-{d+1:02d}"
        day = []
        for k in range(5):
            code = f"{700000 + d*10 + k}.SH"
            day.append((code, float(k)))
            # 每隔几日轻微打乱收益排序，制造 IC 波动（ICIR 有限）
            ret_rank = perturb[k] if d % 4 else perturb[(k + 1) % 5]
            fwd_table[(code, date)] = 0.01 * ret_rank
        hits[date] = day
    monkeypatch.setattr(alpha, "_forward_return",
                        lambda code, date, hz: fwd_table.get((code, date)))

    prior = {"p": (0.05, 0.5)}  # 回测先验同向正 IC
    res = alpha.compute_pipeline_alpha(
        "p", hits, horizon=5, thresholds=load_thresholds(), cpcv_prior=prior,
    )
    assert res["backtest_prior"] == {"ic_mean": 0.05, "icir": 0.5}
    assert res["sample_n"] == 25
    # 实盘正 IC + 先验同向 → promote（双源一致）
    assert res["arbitration"]["verdict"] == "promote"


def test_merged_ic_not_worse_than_single():
    """合并候选相对单管道在 IC 上不劣化（互补噪声场景，合并降噪）。

    两管道覆盖**同一 union**，各自 score = truth + 独立噪声（互补、非冗余）。
    加权平均降噪 → 合并 IC 高于任一单管道的含噪 IC。这是合并层增值的核心情形
    （多源独立确认）。

    设计取舍（文档化，不强行测过）：当一管道是另一管道在某切片上的**无噪超集**、
    或两管道覆盖严重不重叠时，additive 加权 + 共识溢价对重叠票叠权会引入 tilt，
    合并 IC **不保证**严格优于那个干净单管道——合并以覆盖/共识换排序纯度。相关性
    预检（test_correlation_precheck_suppresses_consensus）是对该 tilt 的护栏。
    """
    from scipy.stats import spearmanr

    union = [f"688{i:03d}.SH" for i in range(12)]
    truth = {c: i for i, c in enumerate(union)}

    # 两管道覆盖同一 union；score = 真值秩 + 反向交错噪声（独立、互补）
    a_items, b_items = [], []
    for i, c in enumerate(union):
        a_items.append((c, float(truth[c]) + (2.0 if i % 2 == 0 else -2.0)))
        b_items.append((c, float(truth[c]) + (-2.0 if i % 2 == 0 else 2.0)))
    results = [_result("log_mv", a_items), _result("sector_hotspot", b_items)]

    out = bridge._discovery_merge(results, pipeline_weights=EQUAL_W,
                                  consensus_multiplier=1.0)
    truth_vec = [truth[c["ts_code"]] for c in out["candidates"]]
    merged_ic, _ = spearmanr([c["final_score"] for c in out["candidates"]], truth_vec)

    a_ic, _ = spearmanr([s for _, s in a_items], [truth[c] for c, _ in a_items])
    b_ic, _ = spearmanr([s for _, s in b_items], [truth[c] for c, _ in b_items])
    # 互补噪声相消 → 合并 IC 严格高于任一含噪单管道
    assert merged_ic > max(a_ic, b_ic)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
