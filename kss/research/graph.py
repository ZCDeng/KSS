"""Deterministic research profile DAGs.

Profiles live in code for the first native release so the model cannot invent
protected nodes. Report-specific templates remain in the compiler layer.
"""

from __future__ import annotations

from .models import ProfileSpec, ResearchAgentSpec, TaskSpec

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

DAILY_ANCHORS = [
    "overview",
    "temperature",
    "theme-consensus",
    "risk-radar",
    "precision-cards",
    "methodology",
    "audit",
]


def investment_weekly_v3() -> ProfileSpec:
    agents = [
        ResearchAgentSpec(
            agent_id="source_collector",
            role="source_collector",
            instructions="只采集与规范化受控来源，不提出无来源的市场结论。",
            tool_whitelist=["research_bundle", "research_search", "run_recipe"],
            skill_whitelist=["research-discipline", "financial-statement", "corporate-events"],
            can_submit_claims=False,
        ),
        ResearchAgentSpec(
            agent_id="market_structure_analyst",
            role="market_structure_analyst",
            instructions="基于固化证据计算市场结构、主题共识和分析卡，不生成交易建议。",
            tool_whitelist=["run_recipe"],
            skill_whitelist=["macro-analysis", "correlation-analysis", "sentiment-analysis"],
        ),
        ResearchAgentSpec(
            agent_id="risk_contradiction_critic",
            role="risk_contradiction_critic",
            instructions="优先寻找反例、口径冲突、过期证据和未绑定金融数字。",
            tool_whitelist=["run_recipe"],
            skill_whitelist=["risk-analysis", "thesis-review"],
            can_verify_evidence=False,
        ),
        ResearchAgentSpec(
            agent_id="report_synthesizer",
            role="report_synthesizer",
            instructions="只使用已固化的 Claim、artifact summary 与 evidence ID 生成叙事。",
            skill_whitelist=["report-generate"],
        ),
    ]
    agent_by_kind = {
        "collect_sources": "source_collector",
        "normalize_fields": "source_collector",
        "compute_temperature": "market_structure_analyst",
        "theme_consensus": "market_structure_analyst",
        "analyst_cards": "market_structure_analyst",
        "risk_radar": "risk_contradiction_critic",
        "verify_data": "risk_contradiction_critic",
        "narrative": "report_synthesizer",
    }
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
            "freshness_days": 14,
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
        agents=agents,
        tasks=[
            TaskSpec(
                kind=kind,
                title=title,
                depends_on=deps,
                agent_id=agent_by_kind.get(kind),
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
                    "skill_whitelist": [],
                    "max_steps": 8,
                    "timeout_seconds": 240,
                    "max_provider_tokens": 25_000,
                    "read_only_agent": kind in agent_by_kind,
                },
            )
            for kind, title, deps in task_kinds
        ],
    )


def investment_daily_v1() -> ProfileSpec:
    """Daily report keeps the weekly evidence discipline but removes the
    analyst section and tightens all market evidence to one trading day."""
    weekly = investment_weekly_v3()
    criteria = []
    for item in weekly.criteria:
        copied = dict(item)
        if copied.get("validator") not in {"snapshot", "delivery_audit"}:
            copied["freshness_days"] = 1
        if copied.get("validator") == "source_coverage":
            copied["min_verified_evidence"] = 3
        criteria.append(copied)
    tasks = [
        TaskSpec(
            kind=task.kind,
            title=("生成精判卡结构" if task.kind == "analyst_cards" else task.title),
            required=task.required,
            depends_on=list(task.depends_on),
            agent_id=task.agent_id,
            payload=dict(task.payload),
        )
        for task in weekly.tasks
    ]
    return ProfileSpec(
        profile_id="investment-daily-v1",
        title="投资分析日报 V1",
        anchors=list(DAILY_ANCHORS),
        criteria=criteria,
        agents=list(weekly.agents),
        tasks=tasks,
        budget=dict(weekly.budget),
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
            TaskSpec(
                "collect_sources",
                "采集受控证据",
                depends_on=["freeze_snapshot"],
                payload={
                    "tool_whitelist": ["research_bundle", "research_search"],
                    "skill_whitelist": [],
                    "max_steps": 8,
                    "timeout_seconds": 240,
                    "max_provider_tokens": 25_000,
                },
            ),
            TaskSpec(
                "synthesize_claims",
                "提出结构化主张",
                depends_on=["collect_sources"],
                payload={
                    "tool_whitelist": [],
                    "skill_whitelist": [],
                    "max_steps": 2,
                    "timeout_seconds": 120,
                    "max_provider_tokens": 12_000,
                },
            ),
            TaskSpec("delivery_audit", "执行完成审计", depends_on=["synthesize_claims"]),
        ],
    )


def get_profile(profile_id: str) -> ProfileSpec:
    profiles = {
        "investment-weekly-v3": investment_weekly_v3(),
        "investment-daily-v1": investment_daily_v1(),
        "generic-research-v1": generic_research_v1(),
    }
    try:
        return profiles[profile_id]
    except KeyError as exc:
        raise ValueError(f"unknown research profile: {profile_id}") from exc


def list_profiles() -> list[dict]:
    return [
        investment_weekly_v3().to_wire(),
        investment_daily_v1().to_wire(),
        generic_research_v1().to_wire(),
    ]
