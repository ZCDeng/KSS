"""Deterministic research profile DAGs.

Profiles live in code for the first native release so the model cannot invent
protected nodes. Report-specific templates remain in the compiler layer.
"""

from __future__ import annotations

from .models import ProfileSpec, TaskSpec

WEEKLY_ANCHORS = [
    "overview",
    "temperature",
    "theme-consensus",
    "risk-radar",
    "analyst-sections",
    "precision-cards",
    "methodology",
    "audit",
]


def investment_weekly_v3() -> ProfileSpec:
    task_kinds = [
        ("freeze_snapshot", "冻结输入和数据时点", []),
        ("collect_sources", "采集分析源、市场数据和卡片原始数据", ["freeze_snapshot"]),
        ("normalize_fields", "规范化字段与来源", ["collect_sources"]),
        ("compute_temperature", "计算市场温度指标", ["normalize_fields"]),
        ("theme_consensus", "汇总主题共识", ["normalize_fields"]),
        ("risk_radar", "汇总共同风险", ["normalize_fields"]),
        ("analyst_cards", "生成分析师及精判卡结构", ["compute_temperature", "theme_consensus", "risk_radar"]),
        ("verify_data", "验证数据、指标与证据引用", ["analyst_cards"]),
        ("narrative", "生成结构化叙事", ["verify_data"]),
        ("compile_report", "编译报告", ["narrative"]),
        ("delivery_audit", "执行交付审计", ["compile_report"]),
        ("preview_publish_gate", "生成预览，等待发布确认", ["delivery_audit"]),
    ]
    criteria = [
        {
            "label": "冻结输入快照",
            "min_verified_evidence": 1,
            "validator": "snapshot",
            "allowed_tiers": ["deterministic_calculation"],
        },
        {
            "label": "分析源覆盖",
            "min_verified_evidence": 6,
            "validator": "source_coverage",
            "allowed_tiers": ["official_or_primary", "reputable_secondary"],
            "freshness_days": 14,
        },
        {
            "label": "市场温度指标",
            "min_verified_evidence": 1,
            "validator": "metric_ledger",
            "allowed_tiers": ["deterministic_calculation"],
        },
        {
            "label": "主题共识",
            "min_verified_evidence": 1,
            "validator": "theme_consensus",
            "allowed_tiers": ["reputable_secondary", "deterministic_calculation"],
            "freshness_days": 14,
        },
        {
            "label": "共同风险",
            "min_verified_evidence": 1,
            "validator": "risk_radar",
            "allowed_tiers": ["reputable_secondary", "deterministic_calculation"],
            "freshness_days": 14,
        },
        {
            "label": "精判卡结构",
            "min_verified_evidence": 1,
            "validator": "precision_cards",
            "allowed_tiers": ["reputable_secondary", "deterministic_calculation"],
            "freshness_days": 14,
        },
        {
            "label": "交付审计",
            "min_verified_evidence": 1,
            "validator": "delivery_audit",
            "allowed_tiers": ["deterministic_calculation"],
        },
    ]
    return ProfileSpec(
        profile_id="investment-weekly-v3",
        title="投资分析周报 V3",
        anchors=list(WEEKLY_ANCHORS),
        criteria=criteria,
        tasks=[
            TaskSpec(
                kind=kind,
                title=title,
                depends_on=deps,
                payload={
                    "protected": kind in {
                        "freeze_snapshot",
                        "compile_report",
                        "delivery_audit",
                        "preview_publish_gate",
                    },
                    "tool_whitelist": (
                        ["research_bundle", "research_search", "run_recipe"]
                        if kind == "collect_sources"
                        else ["run_recipe"]
                    ),
                },
            )
            for kind, title, deps in task_kinds
        ],
    )


def generic_research_v1() -> ProfileSpec:
    return ProfileSpec(
        profile_id="generic-research-v1",
        title="通用深度研究骨架",
        criteria=[
            {
                "label": "问题定义",
                "min_verified_evidence": 1,
                "validator": "scope",
                "allowed_tiers": ["official_or_primary", "reputable_secondary", "deterministic_calculation"],
            },
            {
                "label": "证据覆盖",
                "min_verified_evidence": 2,
                "validator": "evidence",
                "allowed_tiers": ["official_or_primary", "reputable_secondary", "deterministic_calculation"],
            },
            {
                "label": "结论审计",
                "min_verified_evidence": 1,
                "validator": "audit",
                "allowed_tiers": ["deterministic_calculation"],
            },
        ],
        tasks=[
            TaskSpec("freeze_snapshot", "冻结输入和范围", depends_on=[]),
            TaskSpec("collect_sources", "采集受控证据", depends_on=["freeze_snapshot"]),
            TaskSpec("synthesize_claims", "提出结构化主张", depends_on=["collect_sources"]),
            TaskSpec("delivery_audit", "执行完成审计", depends_on=["synthesize_claims"]),
        ],
    )


def get_profile(profile_id: str) -> ProfileSpec:
    profiles = {
        "investment-weekly-v3": investment_weekly_v3(),
        "generic-research-v1": generic_research_v1(),
    }
    try:
        return profiles[profile_id]
    except KeyError as exc:
        raise ValueError(f"unknown research profile: {profile_id}") from exc


def list_profiles() -> list[dict]:
    return [investment_weekly_v3().to_wire(), generic_research_v1().to_wire()]
