"""Typed intermediate representation for KSS research deliveries."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

BlockType = Literal[
    "paragraph",
    "metric_group",
    "theme_table",
    "risk_radar",
    "analyst_section",
    "precision_cards",
    "table",
    "svg_chart",
    "methodology",
    "audit",
]


@dataclass(frozen=True)
class EvidenceReference:
    evidence_id: str
    source_tier: str
    title: str
    uri: str | None = None
    data_as_of: str | None = None
    hash: str | None = None
    caveat: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MetricEntry:
    metric_id: str
    label: str
    value: float | int | str
    unit: str
    precision: int
    formula_id: str
    formula_version: str
    input_refs: list[str]
    as_of: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MetricLedger:
    metrics: list[MetricEntry] = field(default_factory=list)

    def by_id(self) -> dict[str, MetricEntry]:
        return {m.metric_id: m for m in self.metrics}

    def to_dict(self) -> dict[str, Any]:
        return {"metrics": [m.to_dict() for m in self.metrics]}


@dataclass(frozen=True)
class NarrativeClaim:
    claim_id: str
    text: str
    evidence_refs: list[str]
    confidence: float | None = None
    review_required: bool = False
    rubric_id: str | None = None
    rubric_version: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ChartSpec:
    chart_id: str
    title: str
    metric_refs: list[str]
    kind: str = "bar"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReportBlock:
    block_id: str
    type: BlockType
    title: str | None = None
    text: str | None = None
    metric_refs: list[str] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    rows: list[dict[str, Any]] = field(default_factory=list)
    chart: ChartSpec | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if self.chart:
            data["chart"] = self.chart.to_dict()
        return data


@dataclass(frozen=True)
class ReportSection:
    section_id: str
    title: str
    anchor: str
    blocks: list[ReportBlock] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "section_id": self.section_id,
            "title": self.title,
            "anchor": self.anchor,
            "blocks": [b.to_dict() for b in self.blocks],
        }


@dataclass(frozen=True)
class ReportDocument:
    document_id: str
    profile_id: str
    title: str
    subtitle: str
    date_range: str
    as_of: str
    sections: list[ReportSection]
    metric_ledger: MetricLedger
    claims: list[NarrativeClaim]
    evidence: list[EvidenceReference]
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "profile_id": self.profile_id,
            "title": self.title,
            "subtitle": self.subtitle,
            "date_range": self.date_range,
            "as_of": self.as_of,
            "sections": [s.to_dict() for s in self.sections],
            "metric_ledger": self.metric_ledger.to_dict(),
            "claims": [c.to_dict() for c in self.claims],
            "evidence": [e.to_dict() for e in self.evidence],
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class ReportManifest:
    document_id: str
    profile_id: str
    audit_status: str
    object_hashes: dict[str, str]
    anchors: list[str]
    generated_at: str
    draft: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
