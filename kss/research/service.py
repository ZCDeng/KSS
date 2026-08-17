"""Research overlay service for KSSDesktop sidecar protocol."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from kss.storage.db import connect, ensure_schema

from .artifacts import ArtifactStore
from .compiler import ReportCompiler, make_investment_weekly_fixture, stable_json
from .corpus import ANALYST_CORPUS_VERSION, AnalystCorpusError, load_analyst_corpus
from .execution_slot import ResearchExecutionSlot
from .graph import get_profile as get_graph_profile
from .investment_analysis import (
    KSS_EQUIVALENT_VERSION,
    PrecisionCard,
    PrecisionCardError,
    aggregate_kss_equivalent,
    check_precision_cards,
)
from .models import Claim, Evidence
from .profiles import get_profile as get_packaged_profile
from .profiles import list_profiles as list_packaged_profiles
from .report_models import (
    EvidenceReference,
    MetricEntry,
    MetricLedger,
    NarrativeClaim,
    ReportBlock,
    ReportDocument,
    ReportSection,
)
from .repository import ResearchRepository, dumps, loads, new_id, utc_now
from .allowlist import is_write_capable_research_node
from .runner import AgentResearchTaskRunner

TERMINAL_GOAL = {"completed", "cancelled", "failed", "blocked", "budget_limited", "insufficient_evidence", "needs_refresh"}
TERMINAL_TASK = {"succeeded", "incomplete", "failed", "interrupted", "cancelled", "blocked"}
ALLOWED_EXPORT_ROOTS = ("Downloads", "Desktop", "Documents", "projects")
DATE_RANGE_RE = re.compile(
    r"^(?P<start>\d{4}-\d{2}-\d{2})_to_(?P<end>\d{4}-\d{2}-\d{2})$"
)


class ResearchService:
    """Synchronous facade used by `agent-research` and `agent-artifacts`.

    Single-agent goals stay sequential. The opt-in multi-agent pilot may run at
    most two independent empty-allowlist nodes from the same goal concurrently.
    Write-capable nodes in a layer stay serial. execution_slot remains the
    cross-process mutex and is not the R11 classifier.
    Protected compile/audit nodes always remain deterministic and sequential.
    """

    def __init__(
        self,
        *,
        state_root: Path,
        project_root: Path | None = None,
        task_runner: AgentResearchTaskRunner | None = None,
        allow_synthetic_fixture: bool = False,
    ) -> None:
        self.state_root = Path(state_root).resolve()
        self.project_root = Path(project_root or Path(__file__).resolve().parents[2]).resolve()
        self.db_path = self.state_root / "storage" / "kss.db"
        self.research_root = self.state_root / "storage" / "agent" / "research"
        self.repo = ResearchRepository(db_path=self.db_path, research_root=self.research_root)
        self.artifacts = ArtifactStore(root=self.research_root, db_path=self.db_path)
        self.compiler = ReportCompiler()
        self.task_runner = task_runner or AgentResearchTaskRunner(
            state_root=self.state_root,
            project_root=self.project_root,
        )
        self.allow_synthetic_fixture = allow_synthetic_fixture
        self._worker_lock = threading.Lock()
        self._workers: dict[str, threading.Thread] = {}
        self.artifacts.clean_staging_and_isolate_orphans()
        self.repo.mark_stale_running_attempts()
        self.repo.mirror_unmirrored()

    # ------------------------------------------------------------------
    # Protocol actions
    # ------------------------------------------------------------------

    def create_goal(self, payload: dict[str, Any] | None = None, goal: str | None = None, **_: Any) -> dict[str, Any]:
        payload = payload or {}
        profile_id = str(payload.get("profile_id") or "investment-weekly-v3")
        try:
            profile = get_graph_profile(profile_id)
        except ValueError:
            return {
                "protocol_version": 1,
                "ok": False,
                "error": "profile_not_found",
                "profile_id": profile_id,
            }
        execution_mode = str(payload.get("execution_mode") or "single")
        if execution_mode not in {"single", "multi_agent_pilot"}:
            return {
                "protocol_version": 1,
                "ok": False,
                "error": "invalid_execution_mode",
                "execution_mode": execution_mode,
            }
        if execution_mode == "multi_agent_pilot" and not profile.agents:
            return {
                "protocol_version": 1,
                "ok": False,
                "error": "profile_does_not_support_multi_agent",
                "profile_id": profile_id,
            }
        objective = str(payload.get("objective") or goal or payload.get("goal") or profile.title)
        inputs = dict(payload.get("inputs") or {})
        input_errors = self._validate_inputs(profile_id, inputs)
        if input_errors:
            return {
                "protocol_version": 1,
                "ok": False,
                "error": "invalid_research_inputs",
                "details": input_errors,
                "profile_id": profile_id,
            }
        budget = dict(profile.budget)
        budget.update(payload.get("budget_overrides") or {})
        goal_id = new_id("goal")
        task_ids = {task.kind: f"{goal_id}_{task.kind}" for task in profile.tasks}
        criteria = []
        for index, item in enumerate(profile.criteria, start=1):
            criteria.append({
                "criterion_id": f"{goal_id}_criterion_{index:02d}",
                "label": item["label"],
                "required": item.get("required", True),
                "min_verified_evidence": item.get("min_verified_evidence", 1),
                "allowed_tiers": item.get("allowed_tiers", ["official_or_primary", "reputable_secondary"]),
                "freshness_days": item.get("freshness_days"),
                "validator": item.get("validator"),
            })
        tasks = []
        dependencies: list[tuple[str, str, bool]] = []
        agent_specs = {agent.agent_id: agent for agent in profile.agents}
        for index, task in enumerate(profile.tasks, start=1):
            task_id = task_ids[task.kind]
            task_payload = dict(task.payload)
            agent = agent_specs.get(str(task.agent_id or ""))
            write_allowlist = list(task_payload.get("write_allowlist") or [])
            if agent is not None:
                write_allowlist = list(agent.write_allowlist or write_allowlist)
            task_payload["write_allowlist"] = write_allowlist
            if execution_mode == "multi_agent_pilot" and agent is not None:
                task_payload.update(
                    {
                        "agent_role": agent.role,
                        "agent_instructions": agent.instructions,
                        "provider_route": agent.provider_route,
                        "model_override": agent.model_override,
                        "tool_whitelist": list(agent.tool_whitelist),
                        "skill_whitelist": list(agent.skill_whitelist),
                        "max_steps": agent.max_steps,
                        "timeout_seconds": agent.timeout_seconds,
                        "max_provider_tokens": agent.max_tokens,
                        "can_submit_claims": agent.can_submit_claims,
                        "can_verify_evidence": agent.can_verify_evidence,
                        "write_allowlist": write_allowlist,
                    }
                )
            tasks.append({
                "task_id": task_id,
                "kind": task.kind,
                "title": task.title,
                "status": "pending" if task.depends_on else "ready",
                "required": task.required,
                "sequence_index": index,
                "agent_id": task.agent_id,
                "payload": task_payload,
            })
            for dep_kind in task.depends_on:
                dependencies.append((task_id, task_ids[dep_kind], True))
        snapshot = self._snapshot(profile_id=profile_id, inputs=inputs)
        origin = str(payload.get("origin") or "manual")
        cadence = payload.get("cadence")
        if origin not in {"manual", "scheduled"}:
            return {"protocol_version": 1, "ok": False, "error": "invalid_origin"}
        if cadence not in {None, "daily", "weekly"}:
            return {"protocol_version": 1, "ok": False, "error": "invalid_cadence"}
        created = self.repo.create_goal(
            goal_id=goal_id,
            session_id=payload.get("session_id"),
            profile_id=profile_id,
            objective=objective,
            inputs=inputs,
            snapshot=snapshot,
            budget=budget,
            criteria=criteria,
            tasks=tasks,
            dependencies=dependencies,
            client_request_id=payload.get("client_request_id"),
            execution_mode=execution_mode,
            origin=origin,
            cadence=str(cadence) if cadence else None,
        )
        resolved_goal_id = str(created.get("goal_id") or goal_id)
        return {
            "protocol_version": 1,
            "ok": True,
            "event": "created",
            "goal": self._wire_goal(created),
            "detail": self._wire_goal(created),
            "goal_id": resolved_goal_id,
            "profile": get_packaged_profile(profile_id, self.project_root),
        }

    def list_goals(
        self,
        *,
        origin: str | None = None,
        cadence: str | None = None,
        profile_ids: list[str] | None = None,
        limit: int | None = None,
        cursor: str | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        if origin or cadence or profile_ids:
            reports, next_cursor = self.repo.list_report_summaries(
                origin=origin, cadence=cadence, profile_ids=profile_ids,
                limit=limit or 100, cursor=cursor,
            )
            return {
                "protocol_version": 1,
                "ok": True,
                "event": "listed",
                "profiles": list_packaged_profiles(self.project_root),
                # Report archives deliberately avoid hydrating every historical goal
                # (and, in particular, never read report HTML at list time).
                "goals": [],
                "reports": reports,
                "next_cursor": next_cursor,
            }
        return {
            "protocol_version": 1,
            "ok": True,
            "event": "listed",
            "profiles": list_packaged_profiles(self.project_root),
            "goals": [self._summary(g) for g in self.repo.list_goals()],
        }

    def open_goal(self, goal_id: str | None = None, **_: Any) -> dict[str, Any]:
        if not goal_id:
            return {"protocol_version": 1, "ok": False, "error": "goal_id_required"}
        goal = self.repo.get_goal(goal_id)
        if not goal:
            return {"protocol_version": 1, "ok": False, "error": "goal_not_found", "goal": goal_id, "goal_id": goal_id}
        return {
            "protocol_version": 1,
            "ok": True,
            "event": "opened",
            "goal": self._wire_goal(goal),
            "detail": self._wire_goal(goal),
            "goal_id": goal_id,
        }

    def import_analyst_corpus(
        self,
        goal_id: str | None = None,
        payload: dict[str, Any] | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        """导入用户明确选择的语料，并登记通过 checker 的精判卡。

        导入动作不会启动网络抓取，也不会执行语料或 Skill 中的脚本。原文只写
        内容寻址对象库；SQLite 仅保存哈希、来源、引用区间和 checker 结果。
        """

        payload = payload or {}
        if not goal_id:
            return {"protocol_version": 1, "ok": False, "error": "goal_id_required"}
        goal = self.repo.get_goal(goal_id)
        if not goal:
            return {
                "protocol_version": 1,
                "ok": False,
                "error": "goal_not_found",
                "goal_id": goal_id,
            }
        if str(goal.get("profile_id") or "") not in {
            "investment-daily-v1",
            "investment-weekly-v3",
        }:
            return {
                "protocol_version": 1,
                "ok": False,
                "error": "profile_does_not_accept_analyst_corpus",
                "goal_id": goal_id,
            }
        path_value = payload.get("path")
        if not isinstance(path_value, str) or not path_value.strip():
            return {
                "protocol_version": 1,
                "ok": False,
                "error": "corpus_path_required",
                "goal_id": goal_id,
            }
        try:
            records = load_analyst_corpus(path_value)
            raw_cards = payload.get("precision_cards") or []
            if not isinstance(raw_cards, list) or any(
                not isinstance(item, dict) for item in raw_cards
            ):
                raise PrecisionCardError("precision_cards 必须是 JSON object 数组")
            cards = check_precision_cards(raw_cards, records)
            if cards:
                aggregate_kss_equivalent(
                    cards,
                    period_end=self._investment_period_end(goal),
                    snapshot_hash=self._snapshot_id(goal_id),
                    config={
                        "analyst_weights": payload.get("analyst_weights"),
                    },
                )
        except (AnalystCorpusError, PrecisionCardError) as exc:
            return {
                "protocol_version": 1,
                "ok": False,
                "error": "analyst_corpus_invalid",
                "detail": str(exc),
                "goal_id": goal_id,
            }
        if not records:
            return {
                "protocol_version": 1,
                "ok": False,
                "error": "analyst_corpus_empty",
                "goal_id": goal_id,
            }
        with connect(self.db_path) as conn:
            ensure_schema(conn)
            existing_rows = conn.execute(
                """
                SELECT source_id, source_message_id, content_hash,
                       corpus_artifact_id
                FROM research_source_records
                WHERE goal_id=?
                ORDER BY line_number
                """,
                (goal_id,),
            ).fetchall()
            existing_count = len(existing_rows)
        if existing_count:
            if cards:
                existing_by_message = {
                    str(row["source_message_id"]): row for row in existing_rows
                }
                if len(existing_by_message) != len(records) or any(
                    record.source_message_id not in existing_by_message
                    or str(
                        existing_by_message[record.source_message_id][
                            "content_hash"
                        ]
                    )
                    != record.content_hash
                    for record in records
                ):
                    return {
                        "protocol_version": 1,
                        "ok": False,
                        "error": "analyst_corpus_does_not_match_imported_sources",
                        "goal_id": goal_id,
                    }
                source_ids = {
                    message_id: str(row["source_id"])
                    for message_id, row in existing_by_message.items()
                }
                source_evidence_ids = self._source_evidence_ids(goal_id)
                if set(source_evidence_ids) != set(source_ids):
                    return {
                        "protocol_version": 1,
                        "ok": False,
                        "error": "analyst_corpus_evidence_incomplete",
                        "goal_id": goal_id,
                    }
                try:
                    formula_artifact = self._persist_precision_cards_and_formula(
                        goal=goal,
                        cards=cards,
                        source_ids=source_ids,
                        source_evidence_ids=source_evidence_ids,
                        analyst_weights=payload.get("analyst_weights"),
                    )
                except (sqlite3.IntegrityError, PrecisionCardError) as exc:
                    return {
                        "protocol_version": 1,
                        "ok": False,
                        "error": "precision_cards_invalid_or_already_imported",
                        "detail": str(exc),
                        "goal_id": goal_id,
                    }
                self._emit(
                    goal_id,
                    "precision_cards_imported",
                    {
                        "record_count": existing_count,
                        "verified_card_count": len(cards),
                        "formula_artifact_id": formula_artifact["artifact_id"],
                    },
                )
                return {
                    "protocol_version": 1,
                    "ok": True,
                    "event": "precision_cards_imported",
                    "goal_id": goal_id,
                    "record_count": existing_count,
                    "verified_card_count": len(cards),
                    "requires_card_extraction": False,
                    "formula_artifact": self._wire_artifact(formula_artifact),
                }
            return {
                "protocol_version": 1,
                "ok": False,
                "error": "analyst_corpus_already_imported",
                "goal_id": goal_id,
                "record_count": existing_count,
            }
        canonical_lines = [
            stable_json(
                {
                    "protocol_version": ANALYST_CORPUS_VERSION,
                    **record.to_dict(),
                }
            )
            for record in records
        ]
        canonical_bytes = ("\n".join(canonical_lines) + "\n").encode("utf-8")
        corpus_artifact = self.artifacts.put_bytes(
            goal_id=goal_id,
            kind="analyst_corpus",
            name="analyst-corpus-v1.jsonl",
            data=canonical_bytes,
            media_type="application/x-ndjson; charset=utf-8",
            metadata={
                "protocol_version": ANALYST_CORPUS_VERSION,
                "record_count": len(records),
                "snapshot_id": self._snapshot_id(goal_id),
            },
        )
        source_ids: dict[str, str] = {}
        source_evidence_ids: dict[str, str] = {}
        now = utc_now()
        try:
            with connect(self.db_path) as conn:
                ensure_schema(conn)
                conn.execute("BEGIN IMMEDIATE")
                for line_number, record in enumerate(records, start=1):
                    source_id = new_id("source")
                    source_ids[record.source_message_id] = source_id
                    conn.execute(
                        """
                        INSERT INTO research_source_records (
                            source_id, goal_id, source_message_id, analyst_id,
                            published_at, source_uri, content_hash,
                            corpus_artifact_id, line_number, provenance_json,
                            attachment_refs_json, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            source_id,
                            goal_id,
                            record.source_message_id,
                            record.analyst_id,
                            record.published_at,
                            record.source_uri,
                            record.content_hash,
                            corpus_artifact["artifact_id"],
                            line_number,
                            dumps(record.provenance),
                            dumps([item.to_dict() for item in record.attachments]),
                            now,
                        ),
                    )
        except sqlite3.IntegrityError:
            return {
                "protocol_version": 1,
                "ok": False,
                "error": "analyst_corpus_already_imported",
                "goal_id": goal_id,
            }

        source_criterion = self._criterion_for_validator(
            goal_id, "source_coverage"
        )
        for record in records:
            source_tier = str(
                record.provenance.get("source_tier") or "reputable_secondary"
            )
            if source_tier not in {
                "official_or_primary",
                "reputable_secondary",
            }:
                source_tier = "reputable_secondary"
            evidence = Evidence(
                evidence_id=new_id("ev"),
                goal_id=goal_id,
                criterion_id=(
                    source_criterion["criterion_id"] if source_criterion else None
                ),
                source_tool="analyst_corpus_import",
                source_tier=source_tier,
                provider=str(record.provenance.get("provider") or "local_import"),
                uri=record.source_uri,
                artifact_id=corpus_artifact["artifact_id"],
                data_as_of=record.published_at,
                method="analyst-corpus-v1",
                scope="analyst_source",
                hash=record.content_hash,
                metadata={
                    "snapshot_id": self._snapshot_id(goal_id),
                    "source_message_id": record.source_message_id,
                    "analyst_id": record.analyst_id,
                },
            )
            self.repo.register_evidence(evidence)
            self.repo.verify_evidence(
                evidence.evidence_id,
                checker="analyst_corpus_hash_checker",
                detail={
                    "content_hash": record.content_hash,
                    "protocol_version": ANALYST_CORPUS_VERSION,
                },
            )
            source_evidence_ids[record.source_message_id] = evidence.evidence_id

        formula_artifact: dict[str, Any] | None = None
        if cards:
            formula_artifact = self._persist_precision_cards_and_formula(
                goal=goal,
                cards=cards,
                source_ids=source_ids,
                source_evidence_ids=source_evidence_ids,
                analyst_weights=payload.get("analyst_weights"),
            )

        self._emit(
            goal_id,
            "analyst_corpus_imported",
            {
                "record_count": len(records),
                "verified_card_count": len(cards),
                "corpus_artifact_id": corpus_artifact["artifact_id"],
                "formula_artifact_id": (
                    formula_artifact["artifact_id"] if formula_artifact else None
                ),
            },
        )
        return {
            "protocol_version": 1,
            "ok": True,
            "event": "analyst_corpus_imported",
            "goal_id": goal_id,
            "record_count": len(records),
            "verified_card_count": len(cards),
            "requires_card_extraction": not bool(cards),
            "corpus_artifact": self._wire_artifact(corpus_artifact),
            "formula_artifact": (
                self._wire_artifact(formula_artifact) if formula_artifact else None
            ),
        }

    def _source_evidence_ids(self, goal_id: str) -> dict[str, str]:
        """按真实 source_message_id 恢复已登记的来源证据映射。"""

        result: dict[str, str] = {}
        for evidence in self.repo.evidence_for_goal(goal_id):
            if evidence.get("source_tool") != "analyst_corpus_import":
                continue
            source_message_id = (evidence.get("metadata") or {}).get(
                "source_message_id"
            )
            if isinstance(source_message_id, str) and source_message_id:
                result[source_message_id] = str(evidence["evidence_id"])
        return result

    def _persist_precision_cards_and_formula(
        self,
        *,
        goal: dict[str, Any],
        cards: list[PrecisionCard],
        source_ids: dict[str, str],
        source_evidence_ids: dict[str, str],
        analyst_weights: Any,
    ) -> dict[str, Any]:
        """原子登记通过 checker 的卡片，再固化版本化公式结果。"""

        goal_id = str(goal["goal_id"])
        now = utc_now()
        result = aggregate_kss_equivalent(
            cards,
            period_end=self._investment_period_end(goal),
            snapshot_hash=self._snapshot_id(goal_id),
            config={
                "analyst_weights": analyst_weights,
            },
        )
        with connect(self.db_path) as conn:
            ensure_schema(conn)
            conn.execute("BEGIN IMMEDIATE")
            for card in cards:
                conn.execute(
                    """
                    INSERT INTO research_precision_cards (
                        card_id, goal_id, source_id, evidence_id, analyst_id,
                        trading_date, symbols_json, themes_json, stance_label,
                        stance_score, conviction_label, conviction_weight,
                        risks_json, catalysts_json, date_anchor, evidence_grade,
                        quote_start, quote_end, quote_hash, sell_side_forward,
                        extractor_json, checker_json, verified, exclusion_reason,
                        created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, NULL, ?)
                    """,
                    (
                        card.card_id,
                        goal_id,
                        source_ids[card.source_message_id],
                        source_evidence_ids[card.source_message_id],
                        card.analyst_id,
                        card.trade_date,
                        dumps([card.instrument]),
                        dumps([card.theme]),
                        str(card.stance_label),
                        card.stance_label,
                        card.conviction,
                        card.conviction_weight,
                        dumps([card.risk] if card.risk else []),
                        dumps([card.catalyst] if card.catalyst else []),
                        card.date_anchor,
                        card.evidence_grade,
                        card.quote_span.start,
                        card.quote_span.end,
                        hashlib.sha256(
                            card.quote_span.text.encode("utf-8")
                        ).hexdigest(),
                        1 if card.is_sellside_forward else 0,
                        dumps(card.extractor),
                        dumps(card.checker),
                        now,
                    ),
                )

        formula_artifact = self.artifacts.put_bytes(
            goal_id=goal_id,
            kind="investment_formula_result",
            name="investment-formulas-kss-equivalent-v1.json",
            data=stable_json(result).encode("utf-8"),
            media_type="application/json",
            metadata={
                "formula_version": KSS_EQUIVALENT_VERSION,
                "snapshot_id": self._snapshot_id(goal_id),
                "card_count": len(cards),
                "input_hash": result["hashes"]["input_hash"],
                "config_hash": result["hashes"]["config_hash"],
            },
        )
        hashes = result["hashes"]
        with connect(self.db_path) as conn:
            ensure_schema(conn)
            conn.execute(
                """
                INSERT INTO research_formula_runs (
                    formula_run_id, goal_id, snapshot_id, formula_version,
                    config_hash, input_hash, result_artifact_id,
                    model_versions_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id("formula"),
                    goal_id,
                    self._snapshot_id(goal_id),
                    KSS_EQUIVALENT_VERSION,
                    hashes["config_hash"],
                    hashes["input_hash"],
                    formula_artifact["artifact_id"],
                    dumps(
                        {
                            "extractors": sorted(
                                {stable_json(card.extractor) for card in cards}
                            ),
                            "checkers": sorted(
                                {stable_json(card.checker) for card in cards}
                            ),
                        }
                    ),
                    now,
                ),
            )
        self._register_formula_evidence(
            goal_id=goal_id,
            artifact=formula_artifact,
            result=result,
        )
        return formula_artifact

    def _investment_period_end(self, goal: dict[str, Any]) -> str:
        inputs = goal.get("inputs") or {}
        if str(goal.get("profile_id") or "") == "investment-daily-v1":
            return str(inputs.get("trade_date") or inputs.get("as_of") or "")
        date_range = str(inputs.get("date_range") or "")
        match = DATE_RANGE_RE.fullmatch(date_range)
        return (
            match.group("end")
            if match
            else str(inputs.get("as_of") or self._goal_as_of(str(goal["goal_id"])))
        )

    def _register_formula_evidence(
        self,
        *,
        goal_id: str,
        artifact: dict[str, Any],
        result: dict[str, Any],
    ) -> None:
        """把同一确定性公式产物映射到各 Criterion，避免模型正文充当数字证据。"""

        mapping = {
            "metric_ledger": "market_temperature",
            "theme_consensus": "theme_strength",
            "risk_radar": "risk_severity",
            "precision_cards": "verified_precision_cards",
        }
        for validator, scope in mapping.items():
            criterion = self._criterion_for_validator(goal_id, validator)
            if not criterion:
                continue
            evidence = Evidence(
                evidence_id=new_id("ev"),
                goal_id=goal_id,
                criterion_id=criterion["criterion_id"],
                source_tool="investment_formula_engine",
                source_tier="deterministic_calculation",
                artifact_id=artifact["artifact_id"],
                data_as_of=self._goal_as_of(goal_id),
                method=KSS_EQUIVALENT_VERSION,
                scope=scope,
                hash=artifact["object_hash"],
                metadata={
                    "snapshot_id": self._snapshot_id(goal_id),
                    "formula_version": KSS_EQUIVALENT_VERSION,
                    "formula_classification": result.get(
                        "formula_classification"
                    ),
                },
            )
            self.repo.register_evidence(evidence)
            self.repo.verify_evidence(
                evidence.evidence_id,
                checker="investment_formula_engine",
                detail={
                    "artifact_hash": artifact["object_hash"],
                    "formula_version": KSS_EQUIVALENT_VERSION,
                },
            )

    def start_goal(self, goal_id: str | None = None, **_: Any) -> dict[str, Any]:
        if not goal_id:
            return {"protocol_version": 1, "ok": False, "error": "goal_id_required"}
        goal = self.repo.get_goal(goal_id)
        if not goal:
            return {"protocol_version": 1, "ok": False, "error": "goal_not_found", "goal": goal_id, "goal_id": goal_id}
        if goal["status"] in TERMINAL_GOAL:
            return {"protocol_version": 1, "ok": False, "error": "goal_terminal", "goal": goal_id, "goal_id": goal_id, "status": goal["status"]}
        active_worker_goal = self._active_worker_goal()
        if active_worker_goal:
            if active_worker_goal == goal_id:
                return {
                    "protocol_version": 1,
                    "ok": True,
                    "event": "already_running",
                    "goal": self._wire_goal(goal),
                    "detail": self._wire_goal(goal),
                    "goal_id": goal_id,
                }
            self.repo.update_goal_status(
                goal_id,
                "queued",
                termination_reason="global_research_slot_busy",
            )
            queued_goal = self.repo.get_goal(goal_id) or {}
            return {
                "protocol_version": 1,
                "ok": True,
                "event": "queued",
                "goal": self._wire_goal(queued_goal),
                "detail": self._wire_goal(queued_goal),
                "goal_id": goal_id,
                "existing_goal_id": active_worker_goal,
            }
        active_goal_id, active_attempt_id = self._active_attempt()
        if active_attempt_id:
            if active_goal_id == goal_id:
                return {
                    "protocol_version": 1,
                    "ok": True,
                    "event": "already_running",
                    "goal": self._wire_goal(goal),
                    "detail": self._wire_goal(goal),
                    "goal_id": goal_id,
                    "attempt_id": active_attempt_id,
                }
            self.repo.update_goal_status(
                goal_id,
                "queued",
                termination_reason="global_research_slot_busy",
            )
            return {
                "protocol_version": 1,
                "ok": True,
                "event": "queued",
                "goal": self._wire_goal(self.repo.get_goal(goal_id) or {}),
                "detail": self._wire_goal(self.repo.get_goal(goal_id) or {}),
                "goal_id": goal_id,
                    "existing_goal_id": active_goal_id,
                }
        self.repo.update_goal_status(goal_id, "running")
        self._emit(goal_id, "research_start", {"status": "running"})
        self._launch_worker(goal_id)
        return {
            "protocol_version": 1,
            "ok": True,
            "event": "started",
            "goal": self._wire_goal(self.repo.get_goal(goal_id) or {}),
            "detail": self._wire_goal(self.repo.get_goal(goal_id) or {}),
            "goal_id": goal_id,
        }

    def wait_for_idle(self, goal_id: str | None = None, timeout: float | None = None) -> dict[str, Any]:
        """Testing/control helper: wait for a background research worker to settle."""

        if not goal_id:
            return {"protocol_version": 1, "ok": False, "error": "goal_id_required"}
        with self._worker_lock:
            worker = self._workers.get(goal_id)
        if worker is not None:
            worker.join(timeout=timeout)
            if worker.is_alive():
                return {
                    "protocol_version": 1,
                    "ok": False,
                    "error": "still_running",
                    "goal_id": goal_id,
                    "goal": self._wire_goal(self.repo.get_goal(goal_id) or {}),
                }
        return {
            "protocol_version": 1,
            "ok": True,
            "event": "idle",
            "goal_id": goal_id,
            "goal": self._wire_goal(self.repo.get_goal(goal_id) or {}),
            "detail": self._wire_goal(self.repo.get_goal(goal_id) or {}),
        }

    def _launch_worker(self, goal_id: str) -> None:
        with self._worker_lock:
            existing = self._workers.get(goal_id)
            if existing and existing.is_alive():
                return
            worker = threading.Thread(
                target=self._run_goal_worker,
                args=(goal_id,),
                name=f"kss-research-{goal_id[:12]}",
                daemon=True,
            )
            self._workers[goal_id] = worker
            worker.start()

    def _active_worker_goal(self) -> str | None:
        with self._worker_lock:
            stale = [goal_id for goal_id, worker in self._workers.items() if not worker.is_alive()]
            for goal_id in stale:
                self._workers.pop(goal_id, None)
            for goal_id, worker in self._workers.items():
                if worker.is_alive():
                    return goal_id
        return None

    def _run_goal_worker(self, goal_id: str) -> None:
        slot = ResearchExecutionSlot(self.state_root)
        try:
            if not slot.acquire():
                self.repo.update_goal_status(
                    goal_id,
                    "queued",
                    termination_reason="global_research_slot_busy",
                )
                self._emit(
                    goal_id,
                    "goal_status",
                    {"status": "queued", "reason": "global_research_slot_busy"},
                )
                return
            exhausted = self._run_ready_loop(goal_id)
            settled = self.repo.get_goal(goal_id) or {}
            may_settle = exhausted and settled.get("status") == "running"
            audit: dict[str, Any] | None = None
            if exhausted:
                audit = self.audit_goal(
                    goal_id=goal_id,
                    complete_if_pass=may_settle,
                )
                self._emit(
                    goal_id,
                    "research_end",
                    {
                        "status": (self.repo.get_goal(goal_id) or {}).get(
                            "status"
                        ),
                        "audit_status": audit.get("status"),
                    },
                )
        except Exception as exc:
            self.repo.update_goal_status(goal_id, "failed", termination_reason=str(exc))
            self._emit(goal_id, "research_error", {"error": str(exc)})
        finally:
            slot.release()
            with self._worker_lock:
                self._workers.pop(goal_id, None)

    def pause_goal(self, goal_id: str | None = None, **_: Any) -> dict[str, Any]:
        return self._set_goal(goal_id, "paused", "paused")

    def resume_goal(self, goal_id: str | None = None, **_: Any) -> dict[str, Any]:
        if goal_id:
            with connect(self.db_path) as conn:
                ensure_schema(conn)
                conn.execute(
                    """
                    UPDATE research_tasks
                    SET status='ready', current_attempt_id=NULL,
                        lease_owner=NULL, lease_expires_at=NULL,
                        updated_at=?, finished_at=NULL
                    WHERE goal_id=? AND status='interrupted'
                    """,
                    (utc_now(), goal_id),
                )
            self.repo.update_goal_status(goal_id, "running")
            return self.start_goal(goal_id=goal_id)
        return {"protocol_version": 1, "ok": False, "error": "goal_id_required"}

    def cancel_goal(self, goal_id: str | None = None, **_: Any) -> dict[str, Any]:
        return self._set_goal(goal_id, "cancelled", "cancelled")

    def retry_task(self, goal_id: str | None = None, task_id: str | None = None, **_: Any) -> dict[str, Any]:
        if not goal_id or not task_id:
            return {"protocol_version": 1, "ok": False, "error": "goal_and_task_required"}
        with connect(self.db_path) as conn:
            ensure_schema(conn)
            task = conn.execute(
                "SELECT status FROM research_tasks WHERE goal_id=? AND task_id=?",
                (goal_id, task_id),
            ).fetchone()
            if not task:
                return {
                    "protocol_version": 1,
                    "ok": False,
                    "error": "task_not_found",
                    "goal_id": goal_id,
                }
            if str(task["status"]) not in {"failed", "incomplete", "interrupted"}:
                return {
                    "protocol_version": 1,
                    "ok": False,
                    "error": "task_not_retryable",
                    "status": str(task["status"]),
                    "goal_id": goal_id,
                }
            missing_dependencies = conn.execute(
                """
                SELECT d.depends_on_task_id, t.status
                FROM research_task_dependencies d
                JOIN research_tasks t ON t.task_id=d.depends_on_task_id
                WHERE d.goal_id=? AND d.task_id=? AND d.required=1
                  AND t.status!='succeeded'
                ORDER BY t.sequence_index
                """,
                (goal_id, task_id),
            ).fetchall()
            if missing_dependencies:
                return {
                    "protocol_version": 1,
                    "ok": False,
                    "error": "dependencies_not_satisfied",
                    "dependencies": [dict(row) for row in missing_dependencies],
                    "goal_id": goal_id,
                }
            now = utc_now()
            conn.execute("UPDATE research_tasks SET status='ready', current_attempt_id=NULL, updated_at=?, finished_at=NULL WHERE goal_id=? AND task_id=?", (now, goal_id, task_id))
            conn.execute(
                """
                UPDATE research_goals
                SET status='running', termination_reason=NULL, finished_at=NULL,
                    updated_at=?
                WHERE goal_id=? AND status IN (
                    'insufficient_evidence', 'blocked', 'failed',
                    'budget_limited', 'needs_refresh'
                )
                """,
                (now, goal_id),
            )
        self._emit(goal_id, "task_ready", {"task_id": task_id, "retry": True}, task_id=task_id)
        return self.start_goal(goal_id=goal_id)

    def refresh_snapshot(
        self,
        goal_id: str | None = None,
        payload: dict[str, Any] | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        if not goal_id:
            return {"protocol_version": 1, "ok": False, "error": "goal_id_required"}
        goal = self.repo.get_goal(goal_id)
        if not goal:
            return {"protocol_version": 1, "ok": False, "error": "goal_not_found", "goal": goal_id, "goal_id": goal_id}
        refreshed_inputs = dict(goal.get("inputs") or {})
        requested_inputs = (payload or {}).get("inputs")
        if isinstance(requested_inputs, dict):
            refreshed_inputs.update(requested_inputs)
        input_errors = self._validate_inputs(goal["profile_id"], refreshed_inputs)
        if input_errors:
            return {
                "protocol_version": 1,
                "ok": False,
                "error": "invalid_research_inputs",
                "details": input_errors,
                "goal_id": goal_id,
            }
        snapshot = self._snapshot(
            profile_id=goal["profile_id"],
            inputs=refreshed_inputs,
            refresh_of=goal.get("snapshot", {}).get("snapshot_id"),
        )
        with connect(self.db_path) as conn:
            ensure_schema(conn)
            now = utc_now()
            conn.execute(
                """
                UPDATE research_goals
                SET inputs_json=?, snapshot_json=?, status='draft',
                    termination_reason=NULL, finished_at=NULL, updated_at=?
                WHERE goal_id=?
                """,
                (dumps(refreshed_inputs), dumps(snapshot), now, goal_id),
            )
            conn.execute(
                """
                UPDATE research_criteria
                SET status='pending', updated_at=?
                WHERE goal_id=?
                """,
                (now, goal_id),
            )
            conn.execute(
                """
                UPDATE research_tasks
                SET status=CASE WHEN sequence_index=1 THEN 'ready' ELSE 'pending' END,
                    current_attempt_id=NULL, lease_owner=NULL,
                    lease_expires_at=NULL, started_at=NULL, finished_at=NULL,
                    updated_at=?
                WHERE goal_id=?
                """,
                (now, goal_id),
            )
            self.repo.append_event(
                conn,
                goal_id=goal_id,
                event_type="goal_status",
                payload={"status": "draft", "snapshot": snapshot, "refreshed": True},
            )
        self.repo.mirror_unmirrored(goal_id)
        refreshed = self.repo.get_goal(goal_id) or {}
        return {
            "protocol_version": 1,
            "ok": True,
            "event": "snapshot_refreshed",
            "goal": self._wire_goal(refreshed),
            "detail": self._wire_goal(refreshed),
            "goal_id": goal_id,
            "snapshot": snapshot,
        }

    def audit_goal(self, goal_id: str | None = None, complete_if_pass: bool = False, **_: Any) -> dict[str, Any]:
        if not goal_id:
            return {"protocol_version": 1, "ok": False, "error": "goal_id_required"}
        goal = self.repo.get_goal(goal_id)
        if not goal:
            return {"protocol_version": 1, "ok": False, "error": "goal_not_found", "goal": goal_id, "goal_id": goal_id}
        evidence = self.repo.evidence_for_goal(goal_id)
        claims = self.repo.claims_for_goal(goal_id)
        artifacts = self.artifacts.list_goal(goal_id)
        findings: list[dict[str, Any]] = []
        criterion_statuses: dict[str, str] = {}
        snapshot_id = str((goal.get("snapshot") or {}).get("snapshot_id") or "")
        coverage: dict[str, Any] = {"criteria": {}, "tasks": {}, "artifact_count": len(artifacts)}
        if str(goal.get("profile_id") or "") in {
            "investment-daily-v1",
            "investment-weekly-v3",
        }:
            investment_coverage = self._investment_input_coverage(
                goal_id=goal_id,
                evidence=evidence,
            )
            if str(goal.get("profile_id") or "") == "investment-daily-v1":
                from kss.research.recipe_metrics import (
                    augment_daily_investment_coverage,
                    daily_investment_gates_satisfied,
                )

                investment_coverage = augment_daily_investment_coverage(
                    investment_coverage,
                    evidence=evidence,
                    task_results=self._current_task_results(goal_id),
                    goal_as_of=self._goal_as_of(goal_id),
                )
            coverage["investment_inputs"] = investment_coverage
            daily_gates = None
            if str(goal.get("profile_id") or "") == "investment-daily-v1":
                from kss.research.recipe_metrics import daily_investment_gates_satisfied

                daily_gates = daily_investment_gates_satisfied(investment_coverage)
            if investment_coverage["source_records"] == 0 and not (
                daily_gates and daily_gates["corpus"]
            ):
                findings.append(
                    {
                        "severity": "block",
                        "code": "missing_analyst_corpus",
                        "detail": "正式投资分析必须导入受控 analyst-corpus-v1 语料",
                    }
                )
            if investment_coverage["verified_precision_cards"] == 0 and not (
                daily_gates and daily_gates["cards"]
            ):
                findings.append(
                    {
                        "severity": "block",
                        "code": "missing_verified_precision_cards",
                        "detail": "正式投资分析必须至少包含一张通过独立 checker 的精判卡",
                    }
                )
            if investment_coverage["formula_runs"] == 0 and not (
                daily_gates and daily_gates["formula"]
            ):
                findings.append(
                    {
                        "severity": "block",
                        "code": "missing_investment_formula_run",
                        "detail": "正式投资分析缺少可复算的版本化公式结果",
                    }
                )
            if investment_coverage["synthetic_evidence"] > 0:
                findings.append(
                    {
                        "severity": "block",
                        "code": "synthetic_evidence_forbidden",
                        "detail": "合成 fixture 只能用于测试，不能满足正式报告完成门",
                    }
                )
        for criterion in goal.get("criteria", []):
            allowed_tiers = set(criterion.get("allowed_tiers") or [])
            verified = [
                item for item in evidence
                if item.get("criterion_id") == criterion["criterion_id"]
                and item.get("verified")
                and str((item.get("metadata") or {}).get("snapshot_id") or "")
                == snapshot_id
            ]
            allowed = [item for item in verified if not allowed_tiers or item.get("source_tier") in allowed_tiers]
            fresh = [
                item
                for item in allowed
                if self._is_fresh(
                    item,
                    criterion,
                    reference_as_of=str(
                        (goal.get("snapshot") or {}).get("as_of") or ""
                    ),
                )
            ]
            coverage["criteria"][criterion["criterion_id"]] = {
                "verified": len(verified),
                "allowed_tier": len(allowed),
                "fresh": len(fresh),
                "required": bool(criterion["required"]),
                "allowed_tiers": sorted(allowed_tiers),
            }
            criterion_statuses[criterion["criterion_id"]] = (
                "met"
                if len(fresh) >= int(criterion["min_verified_evidence"])
                else "unmet"
            )
            if (
                criterion.get("validator") != "delivery_audit"
                and criterion["required"]
                and len(fresh) < int(criterion["min_verified_evidence"])
            ):
                findings.append({"severity": "block", "code": "criterion_insufficient_evidence", "criterion_id": criterion["criterion_id"], "label": criterion["label"]})
        for task in goal.get("tasks", []):
            coverage["tasks"][task["task_id"]] = task["status"]
            if task["required"] and task["status"] != "succeeded":
                findings.append({"severity": "block", "code": "required_task_not_succeeded", "task_id": task["task_id"], "status": task["status"]})
        for claim in claims:
            if claim.get("status") == "contradicted" and claim.get(
                "contradiction_ids"
            ):
                findings.append({"severity": "block", "code": "unresolved_contradiction", "claim_id": claim["claim_id"]})
        report_artifacts = [a for a in artifacts if a.get("kind") == "report_html"]
        if not report_artifacts:
            findings.append({"severity": "block", "code": "missing_report_html"})
        compiler_audits = [a for a in artifacts if a.get("kind") == "audit_json"]
        manifests = [a for a in artifacts if a.get("kind") == "manifest"]
        if report_artifacts and not compiler_audits:
            findings.append({"severity": "block", "code": "missing_compiler_audit"})
        if report_artifacts and not manifests:
            findings.append({"severity": "block", "code": "missing_report_manifest"})
        if compiler_audits:
            try:
                compiler_audit = json.loads(
                    self.artifacts.read_bytes(
                        str(compiler_audits[-1]["object_hash"])
                    )
                )
                if compiler_audit.get("status") != "pass":
                    findings.append(
                        {"severity": "block", "code": "compiler_audit_failed"}
                    )
            except (OSError, ValueError, json.JSONDecodeError):
                findings.append(
                    {"severity": "block", "code": "compiler_audit_invalid"}
                )
        for artifact in artifacts:
            try:
                actual = hashlib.sha256(
                    self.artifacts.read_bytes(str(artifact["object_hash"]))
                ).hexdigest()
            except OSError:
                actual = ""
            if actual != artifact["object_hash"]:
                findings.append(
                    {
                        "severity": "block",
                        "code": "artifact_hash_mismatch",
                        "artifact_id": artifact["artifact_id"],
                    }
                )
        audit_criterion = next(
            (
                criterion
                for criterion in goal.get("criteria", [])
                if criterion.get("validator") == "delivery_audit"
            ),
            None,
        )
        if not findings and audit_criterion:
            existing = next(
                (
                    item
                    for item in evidence
                    if item.get("criterion_id")
                    == audit_criterion["criterion_id"]
                    and item.get("method") == "research_audit"
                    and item.get("verified")
                    and str(
                        (item.get("metadata") or {}).get("snapshot_id") or ""
                    )
                    == snapshot_id
                ),
                None,
            )
            if not existing:
                manifest_hash = (
                    str(manifests[-1]["object_hash"]) if manifests else None
                )
                audit_evidence = Evidence(
                    evidence_id=new_id("ev"),
                    goal_id=goal_id,
                    criterion_id=audit_criterion["criterion_id"],
                    source_tool="research_audit_service",
                    source_tier="deterministic_calculation",
                    artifact_id=manifests[-1]["artifact_id"]
                    if manifests
                    else None,
                    data_as_of=self._goal_as_of(goal_id),
                    method="research_audit",
                    scope="delivery_gate",
                    hash=manifest_hash,
                    metadata={"snapshot_id": snapshot_id},
                )
                self.repo.register_evidence(audit_evidence)
                self.repo.verify_evidence(
                    audit_evidence.evidence_id,
                    checker="research_audit_service",
                    detail={"manifest_hash": manifest_hash},
                )
            coverage["criteria"][audit_criterion["criterion_id"]] = {
                "verified": 1,
                "allowed_tier": 1,
                "fresh": 1,
                "required": True,
                "allowed_tiers": audit_criterion.get("allowed_tiers") or [],
            }
            criterion_statuses[audit_criterion["criterion_id"]] = "met"
        elif audit_criterion:
            criterion_statuses[audit_criterion["criterion_id"]] = "unmet"
        status = "fail" if any(f["severity"] == "block" for f in findings) else "pass"
        audit = {"status": status, "coverage": coverage, "findings": findings, "generated_at": utc_now()}
        audit_id = new_id("audit")
        with connect(self.db_path) as conn:
            ensure_schema(conn)
            now = utc_now()
            conn.execute(
                "INSERT INTO research_audits (audit_id, goal_id, status, coverage_json, findings_json, artifact_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (audit_id, goal_id, status, dumps(coverage), dumps(findings), report_artifacts[-1]["artifact_id"] if report_artifacts else None, now),
            )
            for criterion_id, criterion_status in criterion_statuses.items():
                conn.execute(
                    """
                    UPDATE research_criteria
                    SET status=?, updated_at=?
                    WHERE criterion_id=? AND goal_id=?
                    """,
                    (criterion_status, now, criterion_id, goal_id),
                )
        self._emit(goal_id, "audit_result", {"audit_id": audit_id, "status": status, "coverage": coverage, "findings": findings})
        if complete_if_pass:
            self.repo.update_goal_status(goal_id, "completed" if status == "pass" else "insufficient_evidence", termination_reason=None if status == "pass" else "audit_failed")
        refreshed_goal = self.repo.get_goal(goal_id) or goal
        return {
            "protocol_version": 1,
            "ok": True,
            "event": "audited",
            "goal": self._wire_goal(refreshed_goal),
            "detail": self._wire_goal(refreshed_goal),
            "goal_id": goal_id,
            **audit,
        }

    def _investment_input_coverage(
        self,
        *,
        goal_id: str,
        evidence: list[dict[str, Any]],
    ) -> dict[str, int]:
        """返回正式投资分析完成门所需的受控输入覆盖。

        SQLite 只保存来源、哈希和 checker 结果；私密原文仍位于内容寻址
        对象库。这里不读取原文，也不把模型正文或 Skill 当成有效输入。
        """

        with connect(self.db_path) as conn:
            ensure_schema(conn)
            source_records = int(
                conn.execute(
                    "SELECT COUNT(*) AS count FROM research_source_records WHERE goal_id=?",
                    (goal_id,),
                ).fetchone()["count"]
            )
            verified_cards = int(
                conn.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM research_precision_cards
                    WHERE goal_id=? AND verified=1
                    """,
                    (goal_id,),
                ).fetchone()["count"]
            )
            eligible_cards = int(
                conn.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM research_precision_cards
                    WHERE goal_id=? AND verified=1 AND sell_side_forward=0
                    """,
                    (goal_id,),
                ).fetchone()["count"]
            )
            formula_runs = int(
                conn.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM research_formula_runs
                    WHERE goal_id=? AND formula_version='kss-equivalent-v1'
                    """,
                    (goal_id,),
                ).fetchone()["count"]
            )
        synthetic_evidence = sum(
            1
            for item in evidence
            if str(item.get("uri") or "").startswith("kss-fixture://")
            or bool((item.get("metadata") or {}).get("synthetic"))
        )
        return {
            "source_records": source_records,
            "verified_precision_cards": verified_cards,
            "eligible_precision_cards": eligible_cards,
            "formula_runs": formula_runs,
            "synthetic_evidence": synthetic_evidence,
        }

    def list_events(self, goal_id: str | None = None, after_sequence: int = 0, **_: Any) -> list[dict[str, Any]]:
        return self.repo.list_events(str(goal_id), int(after_sequence or 0)) if goal_id else []

    events = list_events
    replay_events = list_events

    def list_artifacts(self, goal_id: str | None = None, **_: Any) -> dict[str, Any]:
        if not goal_id:
            return {"protocol_version": 1, "ok": False, "error": "goal_id_required"}
        goal = self.repo.get_goal(goal_id) or {}
        return {
            "protocol_version": 1,
            "ok": True,
            "event": "artifacts_listed",
            "goal": self._wire_goal(goal),
            "detail": self._wire_goal(goal),
            "goal_id": goal_id,
            "artifacts": [self._wire_artifact(a) for a in self.artifacts.list_goal(goal_id)],
        }

    def export_draft(self, goal_id: str | None = None, artifact_id: str | None = None, payload: dict[str, Any] | None = None, **_: Any) -> dict[str, Any]:
        return self._export(goal_id=goal_id, artifact_id=artifact_id, payload=payload or {}, require_pass=False, event="draft_exported")

    def publish_artifact(self, goal_id: str | None = None, artifact_id: str | None = None, payload: dict[str, Any] | None = None, **_: Any) -> dict[str, Any]:
        return self._export(goal_id=goal_id, artifact_id=artifact_id, payload=payload or {}, require_pass=True, event="published")

    # ------------------------------------------------------------------
    # Execution internals
    # ------------------------------------------------------------------

    def _run_ready_loop(self, goal_id: str) -> bool:
        while True:
            goal = self.repo.get_goal(goal_id) or {}
            if goal.get("status") != "running":
                return False
            if self._budget_exceeded(goal):
                self.repo.update_goal_status(
                    goal_id,
                    "budget_limited",
                    termination_reason="research_budget_exhausted",
                )
                return False
            execution_mode = str(goal.get("execution_mode") or "single")
            ready_tasks = self._next_ready_tasks(
                goal_id,
                limit=2 if execution_mode == "multi_agent_pilot" else 1,
            )
            if not ready_tasks:
                return True
            if execution_mode != "multi_agent_pilot" or not all(
                self._parallel_research_task(task) for task in ready_tasks
            ):
                ready_tasks = ready_tasks[:1]
            runnable: list[tuple[dict[str, Any], str]] = []
            for task in ready_tasks:
                attempt_id = self._start_attempt(goal_id, task)
                if attempt_id is None:
                    break
                runnable.append((task, attempt_id))
            if not runnable:
                self.repo.update_goal_status(
                    goal_id,
                    "queued",
                    termination_reason="global_research_slot_busy",
                )
                return False
            if len(runnable) == 1:
                task, attempt_id = runnable[0]
                self._run_task(goal_id, task, attempt_id)
            else:
                with ThreadPoolExecutor(
                    max_workers=min(2, len(runnable)),
                    thread_name_prefix="kss-research-agent",
                ) as pool:
                    futures = [
                        pool.submit(self._run_task, goal_id, task, attempt_id)
                        for task, attempt_id in runnable
                    ]
                    for future in as_completed(futures):
                        future.result()
            self._promote_ready_tasks(goal_id)

    @staticmethod
    def _parallel_research_task(task: dict[str, Any]) -> bool:
        payload = task.get("payload") or {}
        return bool(
            task.get("agent_id")
            and not payload.get("protected")
            and not is_write_capable_research_node(task)
        )

    def _run_task(
        self,
        goal_id: str,
        task: dict[str, Any],
        attempt_id: str,
    ) -> None:
        try:
            self._emit(
                goal_id,
                "task_start",
                {"task": task, "agent_id": task.get("agent_id")},
                task_id=task["task_id"],
                attempt_id=attempt_id,
            )
            kind = task["kind"]
            if kind == "freeze_snapshot":
                self._register_task_evidence(goal_id, task, attempt_id, "snapshot", "冻结快照证据")
            elif kind == "compile_report":
                compiled = self._compile_report(goal_id, task, attempt_id)
                compile_status = (
                    "succeeded"
                    if compiled["audit"]["status"] == "pass"
                    else "incomplete"
                )
                compile_result = {
                    "status": compile_status,
                    "claims": [],
                    "evidence_refs": [],
                    "artifact_refs": compiled["artifact_refs"],
                    "open_questions": [],
                    "warnings": [
                        str(finding.get("code") or "compiler_audit_failed")
                        for finding in compiled["audit"].get("findings") or []
                    ],
                }
                self._finish_attempt(
                    goal_id,
                    task["task_id"],
                    attempt_id,
                    compile_status,
                    compile_result,
                )
                self._record_usage(goal_id, {})
                self._emit(
                    goal_id,
                    "task_end",
                    {
                        "status": compile_status,
                        "task_id": task["task_id"],
                        "audit_status": compiled["audit"]["status"],
                    },
                    task_id=task["task_id"],
                    attempt_id=attempt_id,
                )
                return
            elif kind == "delivery_audit":
                # A task marker or assistant statement is never itself audit
                # evidence. This node only checks the compiler's durable
                # audit sidecar; ResearchAuditService creates the final
                # completion evidence after every required task has settled.
                compiler_audit = self._latest_compiler_audit(goal_id)
                if not compiler_audit or compiler_audit.get("status") != "pass":
                    result = {
                        "status": "incomplete",
                        "claims": [],
                        "evidence_refs": [],
                        "artifact_refs": [],
                        "open_questions": ["编译器审计尚未通过"],
                        "warnings": ["compiler_audit_not_passed"],
                    }
                    self._finish_attempt(
                        goal_id,
                        task["task_id"],
                        attempt_id,
                        "incomplete",
                        result,
                    )
                    self._record_usage(goal_id, {})
                    self._emit(
                        goal_id,
                        "task_end",
                        {
                            "status": "incomplete",
                            "task_id": task["task_id"],
                            "reason": "compiler_audit_not_passed",
                        },
                        task_id=task["task_id"],
                        attempt_id=attempt_id,
                    )
                    return
            elif kind == "preview_publish_gate":
                self._emit(goal_id, "artifact_ready", {"preview": True}, task_id=task["task_id"], attempt_id=attempt_id)
            elif self.allow_synthetic_fixture:
                self._run_synthetic_task(goal_id, task, attempt_id)
            else:
                result = self.task_runner.run(
                    goal=self.repo.get_goal(goal_id) or {},
                    task=task,
                    attempt_id=attempt_id,
                    dependency_summaries=self._dependency_summaries(
                        task["task_id"]
                    ),
                )
                self._capture_task_result(goal_id, task, attempt_id, result)
                if task["kind"] == "analyst_cards":
                    self._ingest_extracted_precision_cards(goal_id, result)
                status = str(result.get("status") or "incomplete")
                current_goal_status = (self.repo.get_goal(goal_id) or {}).get(
                    "status"
                )
                if str(result.get("harness_status") or "") == "interrupted":
                    status = "interrupted"
                elif current_goal_status == "paused":
                    status = "interrupted"
                elif current_goal_status == "cancelled":
                    status = "cancelled"
                self._finish_attempt(
                    goal_id,
                    task["task_id"],
                    attempt_id,
                    status,
                    {
                        key: value
                        for key, value in result.items()
                        if not key.startswith("_")
                    },
                )
                self._record_usage(goal_id, result.get("usage") or {})
                retry_scheduled = self._schedule_transient_retry(
                    goal_id,
                    task["task_id"],
                    attempt_id,
                    status,
                    result,
                )
                self._emit(
                    goal_id,
                    "task_end",
                    {
                        "status": status,
                        "task_id": task["task_id"],
                        "retry_scheduled": retry_scheduled,
                    },
                    task_id=task["task_id"],
                    attempt_id=attempt_id,
                )
                return
            succeeded = {
                "status": "succeeded",
                "claims": [],
                "evidence_refs": [],
                "artifact_refs": [],
                "open_questions": [],
                "warnings": [],
            }
            self._finish_attempt(
                goal_id,
                task["task_id"],
                attempt_id,
                "succeeded",
                succeeded,
            )
            self._record_usage(goal_id, {})
            self._emit(
                goal_id,
                "task_end",
                {"status": "succeeded", "task_id": task["task_id"]},
                task_id=task["task_id"],
                attempt_id=attempt_id,
            )
        except Exception as exc:
            self._finish_attempt(goal_id, task["task_id"], attempt_id, "failed", {"status": "incomplete", "warnings": [str(exc)]}, error=str(exc))
            self._emit(goal_id, "task_end", {"status": "failed", "error": str(exc), "task_id": task["task_id"]}, task_id=task["task_id"], attempt_id=attempt_id)
            raise

    def _run_synthetic_task(self, goal_id: str, task: dict[str, Any], attempt_id: str) -> None:
        """Produce deterministic fixture evidence for packaged weekly-profile smoke runs.

        This is intentionally separate from model-backed task execution. It
        keeps the first desktop research workflow runnable without BYOK while
        preserving ledger, tier and audit gates.
        """

        kind = task["kind"]
        validator_by_kind = {
            "collect_sources": "source_coverage",
            "normalize_fields": "evidence",
            "compute_temperature": "metric_ledger",
            "theme_consensus": "theme_consensus",
            "risk_radar": "risk_radar",
            "analyst_cards": "precision_cards",
            "verify_data": "evidence",
            "narrative": "evidence",
        }
        validator = validator_by_kind.get(kind, "evidence")
        if kind == "collect_sources":
            for index in range(1, 7):
                self._register_task_evidence(
                    goal_id,
                    task,
                    attempt_id,
                    validator,
                    f"合成分析源 {index}",
                    uri=f"kss-fixture://investment-weekly-v3/source-{index}",
                )
            return
        self._register_task_evidence(
            goal_id,
            task,
            attempt_id,
            validator,
            f"{task['title']}证据",
        )


    def _backfill_collect_source_evidence(self, goal_id: str) -> None:
        """Register URL evidence_refs that harness put on collect_sources."""
        goal = self.repo.get_goal(goal_id) or {}
        task = next(
            (
                item
                for item in goal.get("tasks") or []
                if item.get("kind") == "collect_sources"
            ),
            None,
        )
        if not task:
            return
        result = (self._current_task_results(goal_id).get("collect_sources") or {})
        if not result:
            return
        self._capture_task_result(
            goal_id,
            task,
            str(task.get("current_attempt_id") or task.get("task_id")),
            result,
        )

    def _compile_report(
        self,
        goal_id: str,
        task: dict[str, Any],
        attempt_id: str,
    ) -> dict[str, Any]:
        self._emit(goal_id, "compile_start", {"task_id": task["task_id"]}, task_id=task["task_id"], attempt_id=attempt_id)
        self._backfill_collect_source_evidence(goal_id)
        document = (
            make_investment_weekly_fixture()
            if self.allow_synthetic_fixture
            and (self.repo.get_goal(goal_id) or {}).get("profile_id") == "investment-weekly-v3"
            else self._build_report_document(goal_id)
        )
        compiled = self.compiler.compile(document)
        artifact_refs = []
        kind_map = {
            "report.html": "report_html",
            "report_ir.json": "report_ir",
            "metrics.json": "metrics",
            "claims.json": "claims",
            "evidence_manifest.json": "evidence_manifest",
            "audit.json": "audit_json",
            "manifest.json": "manifest",
            "preview.png": "preview_png",
        }
        for name, data in compiled["outputs"].items():
            artifact = self.artifacts.put_bytes(
                goal_id=goal_id,
                task_id=task["task_id"],
                attempt_id=attempt_id,
                kind=kind_map.get(name, "report_sidecar"),
                name=name,
                data=data,
                media_type="text/html; charset=utf-8" if name.endswith(".html") else ("image/png" if name.endswith(".png") else "application/json"),
                metadata={
                    "audit_status": compiled["audit"]["status"],
                    "draft": compiled["draft"],
                    "logical_name": name,
                    "snapshot_id": self._snapshot_id(goal_id),
                },
            )
            artifact_refs.append(artifact["artifact_id"])
        if compiled["audit"]["status"] == "pass":
            compiled_evidence_ids: list[str] = []
            for validator in (
                "metric_ledger",
                "theme_consensus",
                "risk_radar",
                "precision_cards",
                "delivery_audit",
            ):
                criterion = self._criterion_for_validator(goal_id, validator)
                if not criterion:
                    continue
                evidence = Evidence(
                    evidence_id=new_id("ev"),
                    goal_id=goal_id,
                    criterion_id=criterion["criterion_id"],
                    task_id=task["task_id"],
                    attempt_id=attempt_id,
                    source_tool="delivery_compiler",
                    source_tier="deterministic_calculation",
                    artifact_id=artifact_refs[0] if artifact_refs else None,
                    data_as_of=self._goal_as_of(goal_id),
                    method="ReportCompiler.compile",
                    scope=validator,
                    hash=compiled["manifest"]["object_hashes"].get(
                        "report.html"
                    ),
                    metadata={
                        "audit_status": compiled["audit"]["status"],
                        "artifact_refs": artifact_refs,
                        "snapshot_id": self._snapshot_id(goal_id),
                    },
                )
                self.repo.register_evidence(evidence)
                self.repo.verify_evidence(
                    evidence.evidence_id, checker="delivery_compiler"
                )
                compiled_evidence_ids.append(evidence.evidence_id)
            self.repo.register_claim(
                Claim(
                    claim_id=new_id("claim"),
                    goal_id=goal_id,
                    content="投资分析周报 V3 已由结构化 IR 编译并通过确定性审计。",
                    status="supported",
                    task_id=task["task_id"],
                    evidence_ids=compiled_evidence_ids,
                )
            )
        self.repo.mirror_unmirrored(goal_id)
        self._emit(goal_id, "compile_end", {"status": compiled["audit"]["status"], "artifact_refs": artifact_refs}, task_id=task["task_id"], attempt_id=attempt_id)
        compiled["artifact_refs"] = artifact_refs
        return compiled

    def _build_report_document(self, goal_id: str) -> ReportDocument:
        """Build an honest typed draft from the current snapshot ledger.

        Missing market metrics remain explicit ``N/A`` values. The compiler
        turns those into blocking audit findings and a watermarked draft
        instead of inventing numbers or stopping before a preview exists.
        """

        goal = self.repo.get_goal(goal_id) or {}
        snapshot = goal.get("snapshot") or {}
        snapshot_id = str(snapshot.get("snapshot_id") or "")
        raw_evidence = [
            item
            for item in self.repo.evidence_for_goal(goal_id)
            if item.get("verified")
            and str((item.get("metadata") or {}).get("snapshot_id") or "")
            == snapshot_id
            and item.get("hash")
            and item.get("data_as_of")
        ]
        evidence = [
            EvidenceReference(
                evidence_id=str(item["evidence_id"]),
                source_tier=str(item.get("source_tier") or "unknown"),
                title=str(
                    (item.get("metadata") or {}).get("title")
                    or item.get("scope")
                    or item.get("source_tool")
                    or "研究证据"
                ),
                uri=item.get("uri"),
                data_as_of=str(item.get("data_as_of") or ""),
                hash=str(item.get("hash") or ""),
                caveat=item.get("caveat"),
            )
            for item in raw_evidence
        ]
        evidence_ids = {item.evidence_id for item in evidence}
        evidence_numeric_values = {
            str(item["evidence_id"]): [
                float(value)
                for value in (item.get("metadata") or {}).get(
                    "numeric_values", []
                )
                if isinstance(value, (int, float)) and not isinstance(value, bool)
            ]
            for item in raw_evidence
        }
        task_results = self._current_task_results(goal_id)
        claims = [
            NarrativeClaim(
                claim_id=str(item["claim_id"]),
                text=str(item.get("content") or ""),
                evidence_refs=[
                    str(ref)
                    for ref in item.get("evidence_ids") or []
                    if str(ref) in evidence_ids
                ],
                review_required=True,
            )
            for item in self.repo.claims_for_goal(goal_id)
            if item.get("status") == "supported"
            and any(str(ref) in evidence_ids for ref in item.get("evidence_ids") or [])
        ]
        card_rows = [
            {
                "card_id": f"claim_{index:04d}",
                "title": f"证据卡 {index:04d}",
                "summary": claim.text,
                "metric_refs": ["m_card_count"],
                "evidence_refs": list(claim.evidence_refs),
                "source_group": claim.evidence_refs[0],
            }
            for index, claim in enumerate(claims, start=1)
        ]
        if not card_rows:
            card_rows = [
                {
                    "card_id": f"evidence_{index:04d}",
                    "title": item.title,
                    "summary": item.caveat or "已验证证据条目，尚待形成研究主张。",
                    "metric_refs": ["m_card_count"],
                    "evidence_refs": [item.evidence_id],
                    "source_group": item.source_tier,
                }
                for index, item in enumerate(evidence, start=1)
            ]
        metric_specs = {
            "compute_temperature": {
                "metric_id": "m_temperature",
                "label": "市场温度",
                "formula_id": "temperature_index",
            },
            "theme_consensus": {
                "metric_id": "m_consensus",
                "label": "主题共识强度",
                "formula_id": "theme_consensus",
            },
            "risk_radar": {
                "metric_id": "m_risk",
                "label": "风险雷达均值",
                "formula_id": "risk_radar",
            },
        }
        derived_metrics: dict[str, MetricEntry] = {}
        formula_inputs: dict[str, Any] = {}
        investment_formula = self._investment_formula_document_data(
            goal_id,
            as_of=self._goal_as_of(goal_id),
        )
        if investment_formula:
            derived_metrics.update(investment_formula["metrics"])
            formula_inputs.update(investment_formula["formula_inputs"])
            if investment_formula["card_rows"]:
                card_rows = investment_formula["card_rows"]
        if str(goal.get("profile_id") or "") == "investment-daily-v1" and not (
            investment_formula and investment_formula.get("card_rows")
        ):
            from kss.research.recipe_metrics import build_daily_card_rows

            card_rows = build_daily_card_rows(
                task_results=task_results,
                evidence=evidence,
            )
        for task_kind, expected in metric_specs.items():
            if expected["metric_id"] in derived_metrics:
                continue
            task_result = task_results.get(task_kind) or {}
            for raw_claim in task_result.get("claims") or []:
                if not isinstance(raw_claim, dict):
                    continue
                metric = raw_claim.get("metric")
                if not isinstance(metric, dict):
                    continue
                metric_id = str(metric.get("metric_id") or "")
                input_refs = [
                    str(value) for value in metric.get("input_refs") or []
                ]
                values = metric.get("formula_inputs")
                if (
                    metric_id != expected["metric_id"]
                    or str(metric.get("formula_id") or "")
                    != expected["formula_id"]
                    or metric.get("formula_version") != "v1"
                    or not input_refs
                    or any(value not in evidence_ids for value in input_refs)
                    or not isinstance(values, list)
                    or not values
                    or any(
                        isinstance(value, bool)
                        or not isinstance(value, (int, float))
                        or not math.isfinite(float(value))
                        for value in values
                    )
                ):
                    continue
                source_numbers = [
                    number
                    for evidence_id in input_refs
                    for number in evidence_numeric_values.get(evidence_id, [])
                ]
                normalized_values = [float(value) for value in values]
                if not source_numbers or any(
                    not any(
                        abs(value - source) <= max(1e-9, abs(source) * 1e-9)
                        for source in source_numbers
                    )
                    for value in normalized_values
                ):
                    continue
                value = metric.get("value")
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(float(value))
                ):
                    continue
                try:
                    precision = int(metric.get("precision") or 1)
                except (TypeError, ValueError):
                    continue
                if not 0 <= precision <= 8:
                    continue
                metric_as_of = str(metric.get("as_of") or "")
                if metric_as_of != self._goal_as_of(goal_id):
                    continue
                derived_metrics[metric_id] = MetricEntry(
                    metric_id,
                    str(expected["label"]),
                    float(value),
                    str(metric.get("unit") or "%"),
                    precision,
                    str(expected["formula_id"]),
                    "v1",
                    input_refs,
                    metric_as_of,
                )
                formula_inputs[metric_id] = normalized_values
                break
        all_refs = [item.evidence_id for item in evidence]
        from kss.research.recipe_metrics import fill_missing_recipe_metrics

        fill_missing_recipe_metrics(
            derived_metrics,
            formula_inputs,
            task_results,
            evidence_ids=evidence_ids,
            fallback_refs=all_refs,
            goal_as_of=self._goal_as_of(goal_id),
        )
        metrics = MetricLedger(
            [
                derived_metrics.get("m_temperature")
                or MetricEntry(
                    "m_temperature",
                    "市场温度",
                    "N/A",
                    "",
                    1,
                    "temperature_index",
                    "v1",
                    all_refs,
                    self._goal_as_of(goal_id),
                ),
                derived_metrics.get("m_consensus")
                or MetricEntry(
                    "m_consensus",
                    "主题共识强度",
                    "N/A",
                    "",
                    1,
                    "theme_consensus",
                    "v1",
                    all_refs,
                    self._goal_as_of(goal_id),
                ),
                derived_metrics.get("m_risk")
                or MetricEntry(
                    "m_risk",
                    "风险雷达均值",
                    "N/A",
                    "",
                    1,
                    "risk_radar",
                    "v1",
                    all_refs,
                    self._goal_as_of(goal_id),
                ),
                MetricEntry(
                    "m_card_count",
                    "精判卡数量",
                    int(
                        investment_formula["verified_card_count"]
                        if investment_formula
                        else len(card_rows)
                    ),
                    "张",
                    0,
                    "card_count",
                    (
                        KSS_EQUIVALENT_VERSION
                        if investment_formula
                        else "v1"
                    ),
                    all_refs,
                    self._goal_as_of(goal_id),
                ),
            ]
        )
        objective = str(goal.get("objective") or "深度研究")
        profile_id = str(goal.get("profile_id") or "investment-weekly-v3")
        narrative_paragraphs: list[str] = []
        if profile_id == "investment-daily-v1":
            from kss.research.recipe_metrics import extract_daily_narrative_paragraphs

            narrative_paragraphs = extract_daily_narrative_paragraphs(task_results)
        overview_blocks = [
            ReportBlock(
                f"b_overview_{index}",
                "paragraph",
                text=text,
                evidence_refs=all_refs,
            )
            for index, text in enumerate(narrative_paragraphs, start=1)
        ] or [
            ReportBlock(
                "b_overview",
                "paragraph",
                text=objective,
                evidence_refs=all_refs,
            )
        ]
        sections = [
            ReportSection(
                "sec_overview",
                "盘后综述" if narrative_paragraphs else "总览",
                "overview",
                overview_blocks,
            ),
            ReportSection(
                "sec_temperature",
                "市场温度",
                "temperature",
                [
                    ReportBlock(
                        "b_temperature",
                        "metric_group",
                        metric_refs=["m_temperature", "m_card_count"],
                        evidence_refs=all_refs,
                    )
                ],
            ),
            ReportSection(
                "sec_theme",
                "主题共识",
                "theme-consensus",
                [
                    ReportBlock(
                        "b_theme",
                        "metric_group",
                        metric_refs=["m_consensus"],
                        evidence_refs=all_refs,
                    )
                ],
            ),
            ReportSection(
                "sec_risk",
                "风险雷达",
                "risk-radar",
                [
                    ReportBlock(
                        "b_risk",
                        "metric_group",
                        metric_refs=["m_risk"],
                        evidence_refs=all_refs,
                    )
                ],
            ),
            ReportSection(
                "sec_analyst",
                "分析师分区",
                "analyst-sections",
                [
                    ReportBlock(
                        "b_analysts",
                        "table",
                        rows=[
                            {
                                "来源": item.title,
                                "等级": item.source_tier,
                                "evidence_refs": [item.evidence_id],
                            }
                            for item in evidence
                        ],
                        evidence_refs=all_refs,
                    )
                ],
            ),
            ReportSection(
                "sec_cards",
                "精判卡",
                "precision-cards",
                [
                    ReportBlock(
                        "b_cards",
                        "precision_cards",
                        rows=card_rows,
                        metric_refs=["m_card_count"],
                        evidence_refs=all_refs,
                    )
                ],
            ),
            ReportSection(
                "sec_method",
                "方法论",
                "methodology",
                [
                    ReportBlock(
                        "b_method",
                        "methodology",
                        text="仅工具结果和确定性计算可进入证据账本；缺失指标保持空缺。",
                        evidence_refs=all_refs,
                    )
                ],
            ),
            ReportSection(
                "sec_audit",
                "审计",
                "audit",
                [
                    ReportBlock(
                        "b_audit",
                        "audit",
                        text="正式发布需通过证据、数字、矛盾、锚点和对象哈希门禁。",
                        metric_refs=["m_card_count"],
                        evidence_refs=all_refs,
                    )
                ],
            ),
        ]
        if profile_id == "investment-daily-v1":
            sections = [
                section for section in sections
                if section.anchor != "analyst-sections"
            ]
        date_range = str((goal.get("inputs") or {}).get("date_range") or "")
        if profile_id == "investment-daily-v1":
            trade_date = str((goal.get("inputs") or {}).get("trade_date") or "未指定")
            date_range = f"{trade_date}_to_{trade_date}"
        return ReportDocument(
            document_id=f"{goal_id}-{snapshot_id or 'snapshot'}",
            profile_id=profile_id,
            title=("投资分析日报 V1" if profile_id == "investment-daily-v1" else "投资分析周报 V3"),
            subtitle=("盘后投资分析" if narrative_paragraphs else "结构化证据草稿"),
            date_range=date_range or "未指定",
            as_of=self._goal_as_of(goal_id),
            sections=sections,
            metric_ledger=metrics,
            claims=claims,
            evidence=evidence,
            metadata={
                "snapshot_id": snapshot_id,
                "card_count": len(card_rows),
                "formula_inputs": formula_inputs,
            },
        )

    def _investment_formula_document_data(
        self,
        goal_id: str,
        *,
        as_of: str,
    ) -> dict[str, Any] | None:
        """从最新公式产物和精判卡索引构建可独立复算的报告指标。"""

        with connect(self.db_path) as conn:
            ensure_schema(conn)
            formula_row = conn.execute(
                """
                SELECT a.object_hash
                FROM research_formula_runs f
                JOIN research_artifacts a
                  ON a.artifact_id=f.result_artifact_id
                WHERE f.goal_id=? AND f.formula_version=?
                ORDER BY f.created_at DESC
                LIMIT 1
                """,
                (goal_id, KSS_EQUIVALENT_VERSION),
            ).fetchone()
            cards = conn.execute(
                """
                SELECT card_id, evidence_id, analyst_id, symbols_json,
                       themes_json, stance_label, conviction_label,
                       evidence_grade, sell_side_forward
                FROM research_precision_cards
                WHERE goal_id=? AND verified=1
                ORDER BY trading_date, card_id
                """,
                (goal_id,),
            ).fetchall()
        if formula_row is None or not cards:
            return None
        try:
            result = json.loads(
                self.artifacts.read_bytes(
                    str(formula_row["object_hash"])
                ).decode("utf-8")
            )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
            return None
        equivalent = result.get("kss_equivalent")
        audit_inputs = result.get("audit_inputs")
        if not isinstance(equivalent, dict) or not isinstance(audit_inputs, list):
            return None
        theme_rows = equivalent.get("theme_strength")
        risk_rows = equivalent.get("risk_severity")
        temperature = equivalent.get("temperature")
        if (
            isinstance(temperature, bool)
            or not isinstance(temperature, (int, float))
            or not isinstance(theme_rows, list)
            or not isinstance(risk_rows, list)
        ):
            return None
        source_refs = sorted(
            {
                str(row["evidence_id"])
                for row in cards
                if row["evidence_id"]
            }
        )
        theme_contributions = [
            float(row["contribution_sum"])
            for row in theme_rows
            if isinstance(row, dict)
            and isinstance(row.get("contribution_sum"), (int, float))
            and not isinstance(row.get("contribution_sum"), bool)
        ]
        risk_inputs = [
            {
                "mention_card_count": row["mention_card_count"],
                "distinct_analyst_count": row["distinct_analyst_count"],
            }
            for row in risk_rows
            if isinstance(row, dict)
            and isinstance(row.get("mention_card_count"), (int, float))
            and isinstance(row.get("distinct_analyst_count"), (int, float))
        ]
        strongest_theme = (
            max(theme_contributions, key=abs) if theme_contributions else 0.0
        )
        strongest_risk = max(
            (
                float(row["mention_card_count"])
                + 0.5 * float(row["distinct_analyst_count"])
                for row in risk_inputs
            ),
            default=0.0,
        )
        metrics = {
            "m_temperature": MetricEntry(
                "m_temperature",
                "市场温度",
                float(temperature),
                "",
                3,
                "investment_temperature",
                KSS_EQUIVALENT_VERSION,
                source_refs,
                as_of,
            ),
            "m_consensus": MetricEntry(
                "m_consensus",
                "最强主题贡献",
                strongest_theme,
                "",
                3,
                "investment_theme_strength",
                KSS_EQUIVALENT_VERSION,
                source_refs,
                as_of,
            ),
            "m_risk": MetricEntry(
                "m_risk",
                "最高风险严重度",
                strongest_risk,
                "",
                1,
                "investment_risk_severity",
                KSS_EQUIVALENT_VERSION,
                source_refs,
                as_of,
            ),
        }
        card_rows = []
        stance_names = {
            "-2": "强烈看空",
            "-1": "看空",
            "0": "中性",
            "1": "看多",
            "2": "强烈看多",
        }
        for row in cards:
            symbols = loads(row["symbols_json"], [])
            themes = loads(row["themes_json"], [])
            card_rows.append(
                {
                    "card_id": str(row["card_id"]),
                    "title": " · ".join(
                        [
                            str(symbols[0] if symbols else "未指定标的"),
                            str(themes[0] if themes else "未指定主题"),
                        ]
                    ),
                    "summary": (
                        f"立场 {stance_names.get(str(row['stance_label']), '未分类')} · "
                        f"置信度 {row['conviction_label']} · "
                        f"证据等级 {row['evidence_grade']}"
                    ),
                    "metric_refs": ["m_card_count"],
                    "evidence_refs": [str(row["evidence_id"])],
                    "source_group": str(row["analyst_id"]),
                }
            )
        return {
            "metrics": metrics,
            "formula_inputs": {
                "m_temperature": audit_inputs,
                "m_consensus": theme_contributions,
                "m_risk": risk_inputs,
            },
            "card_rows": card_rows,
            "verified_card_count": len(cards),
        }

    def _current_task_results(self, goal_id: str) -> dict[str, dict[str, Any]]:
        with connect(self.db_path) as conn:
            ensure_schema(conn)
            rows = conn.execute(
                """
                SELECT t.kind, a.result_json
                FROM research_tasks t
                JOIN research_attempts a ON a.attempt_id=t.current_attempt_id
                WHERE t.goal_id=? AND t.status='succeeded'
                  AND a.status='succeeded'
                """,
                (goal_id,),
            ).fetchall()
        return {
            str(row["kind"]): loads(row["result_json"], {})
            for row in rows
        }

    def _start_attempt(
        self,
        goal_id: str,
        task: dict[str, Any],
    ) -> str | None:
        attempt_id = new_id("attempt")
        now = utc_now()
        conn = self.repo.transaction()
        try:
            current = conn.execute(
                "SELECT status FROM research_tasks WHERE task_id=?",
                (task["task_id"],),
            ).fetchone()
            running = conn.execute(
                """
                SELECT attempt_id, goal_id
                FROM research_attempts
                WHERE status='running'
                ORDER BY started_at
                """
            ).fetchall()
            goal_row = conn.execute(
                "SELECT execution_mode FROM research_goals WHERE goal_id=?",
                (goal_id,),
            ).fetchone()
            pilot = bool(
                goal_row
                and str(goal_row["execution_mode"]) == "multi_agent_pilot"
            )
            another_goal_running = any(
                str(row["goal_id"]) != goal_id for row in running
            )
            capacity_exhausted = len(running) >= (2 if pilot else 1)
            if (
                not current
                or current["status"] != "ready"
                or another_goal_running
                or capacity_exhausted
                or (running and not self._parallel_research_task(task))
            ):
                self.repo.commit_close(conn)
                return None
            row = conn.execute("SELECT COALESCE(MAX(attempt_no), 0) + 1 AS n FROM research_attempts WHERE task_id=?", (task["task_id"],)).fetchone()
            attempt_no = int(row["n"])
            conn.execute(
                "INSERT INTO research_attempts (attempt_id, goal_id, task_id, status, attempt_no, trigger, usage_json, lease_owner, lease_expires_at, created_at, started_at, agent_id) VALUES (?, ?, ?, 'running', ?, 'scheduler', ?, ?, ?, ?, ?, ?)",
                (
                    attempt_id,
                    goal_id,
                    task["task_id"],
                    attempt_no,
                    dumps({}),
                    os.uname().nodename,
                    self._lease_expiry(),
                    now,
                    now,
                    task.get("agent_id"),
                ),
            )
            conn.execute(
                "UPDATE research_tasks SET status='running', current_attempt_id=?, lease_owner=?, lease_expires_at=?, started_at=COALESCE(started_at, ?), updated_at=? WHERE task_id=?",
                (attempt_id, os.uname().nodename, self._lease_expiry(), now, now, task["task_id"]),
            )
            self.repo.commit_close(conn)
        except Exception:
            conn.rollback()
            self.repo.commit_close(conn)
            raise
        self._emit(
            goal_id,
            "attempt_start",
            {
                "attempt_id": attempt_id,
                "task_id": task["task_id"],
                "agent_id": task.get("agent_id"),
            },
            task_id=task["task_id"],
            attempt_id=attempt_id,
        )
        return attempt_id

    def _finish_attempt(self, goal_id: str, task_id: str, attempt_id: str, status: str, result: dict[str, Any], error: str | None = None) -> None:
        now = utc_now()
        with connect(self.db_path) as conn:
            ensure_schema(conn)
            conn.execute(
                "UPDATE research_attempts SET status=?, result_json=?, error=?, finished_at=?, lease_owner=NULL, lease_expires_at=NULL WHERE attempt_id=?",
                (status, dumps(result), error, now, attempt_id),
            )
            conn.execute(
                "UPDATE research_tasks SET status=?, updated_at=?, finished_at=?, lease_owner=NULL, lease_expires_at=NULL WHERE task_id=?",
                (status, now, now, task_id),
            )
        with connect(self.db_path) as conn:
            ensure_schema(conn)
            task = conn.execute(
                "SELECT agent_id FROM research_tasks WHERE task_id=?",
                (task_id,),
            ).fetchone()
        self._emit(
            goal_id,
            "attempt_end",
            {
                "attempt_id": attempt_id,
                "task_id": task_id,
                "status": status,
                "error": error,
                "agent_id": task["agent_id"] if task else None,
            },
            task_id=task_id,
            attempt_id=attempt_id,
        )

    def _schedule_transient_retry(
        self,
        goal_id: str,
        task_id: str,
        attempt_id: str,
        status: str,
        result: dict[str, Any],
    ) -> bool:
        if status != "incomplete" or not self._is_transient_result(result):
            return False
        with connect(self.db_path) as conn:
            ensure_schema(conn)
            attempt = conn.execute(
                "SELECT attempt_no FROM research_attempts WHERE attempt_id=?",
                (attempt_id,),
            ).fetchone()
            goal = conn.execute(
                "SELECT status FROM research_goals WHERE goal_id=?",
                (goal_id,),
            ).fetchone()
            if (
                not attempt
                or int(attempt["attempt_no"]) >= 3
                or not goal
                or goal["status"] != "running"
            ):
                return False
            now = utc_now()
            conn.execute(
                """
                UPDATE research_tasks
                SET status='ready', finished_at=NULL, updated_at=?
                WHERE task_id=? AND current_attempt_id=?
                """,
                (now, task_id, attempt_id),
            )
        self._emit(
            goal_id,
            "task_update",
            {
                "task_id": task_id,
                "status": "ready",
                "operation": "retry_scheduled",
                "previous_attempt_id": attempt_id,
            },
            task_id=task_id,
            attempt_id=attempt_id,
        )
        return True

    @staticmethod
    def _is_transient_result(result: dict[str, Any]) -> bool:
        text = " ".join(
            str(item) for item in (result.get("warnings") or [])
        ).lower()
        non_retryable = (
            "api key",
            "credential",
            "unauthorized",
            "forbidden",
            "401",
            "403",
            "schema",
            "path",
            "security",
        )
        if any(marker in text for marker in non_retryable):
            return False
        return any(
            marker in text
            for marker in (
                "timeout",
                "temporar",
                "connection",
                "network",
                "rate limit",
                "provider stream",
            )
        )

    def _next_ready_tasks(
        self,
        goal_id: str,
        *,
        limit: int,
    ) -> list[dict[str, Any]]:
        with connect(self.db_path) as conn:
            ensure_schema(conn)
            rows = conn.execute(
                """
                SELECT *
                FROM research_tasks
                WHERE goal_id=? AND status='ready'
                ORDER BY sequence_index
                LIMIT ?
                """,
                (goal_id, max(1, min(limit, 2))),
            ).fetchall()
        return [self._row_task(row) for row in rows]

    def _next_ready_task(self, goal_id: str) -> dict[str, Any] | None:
        """Compatibility helper retained for tests and extensions."""
        tasks = self._next_ready_tasks(goal_id, limit=1)
        return tasks[0] if tasks else None

    def _promote_ready_tasks(self, goal_id: str) -> None:
        with connect(self.db_path) as conn:
            ensure_schema(conn)
            pending = conn.execute("SELECT * FROM research_tasks WHERE goal_id=? AND status='pending' ORDER BY sequence_index", (goal_id,)).fetchall()
            now = utc_now()
            for row in pending:
                deps = conn.execute("SELECT d.required, t.status FROM research_task_dependencies d JOIN research_tasks t ON t.task_id=d.depends_on_task_id WHERE d.goal_id=? AND d.task_id=?", (goal_id, row["task_id"])).fetchall()
                if all(str(dep["status"]) == "succeeded" or not bool(dep["required"]) for dep in deps):
                    conn.execute("UPDATE research_tasks SET status='ready', updated_at=? WHERE task_id=?", (now, row["task_id"]))
                    self.repo.append_event(conn, goal_id=goal_id, event_type="task_ready", task_id=str(row["task_id"]), payload={"task_id": row["task_id"]})
                elif any(
                    bool(dep["required"])
                    and str(dep["status"]) in TERMINAL_TASK - {"succeeded"}
                    for dep in deps
                ):
                    conn.execute(
                        "UPDATE research_tasks SET status='blocked', updated_at=?, finished_at=? WHERE task_id=?",
                        (now, now, row["task_id"]),
                    )
                    self.repo.append_event(
                        conn,
                        goal_id=goal_id,
                        event_type="task_end",
                        task_id=str(row["task_id"]),
                        payload={
                            "task_id": row["task_id"],
                            "status": "blocked",
                            "reason": "required_dependency_not_succeeded",
                        },
                    )
        self.repo.mirror_unmirrored(goal_id)

    def _register_task_evidence(self, goal_id: str, task: dict[str, Any], attempt_id: str, validator: str, title: str, uri: str | None = None) -> None:
        criterion = self._criterion_for_validator(goal_id, validator) or self._first_criterion(goal_id)
        if not criterion:
            return
        evidence = Evidence(
            evidence_id=new_id("ev"),
            goal_id=goal_id,
            criterion_id=criterion["criterion_id"],
            task_id=task["task_id"],
            attempt_id=attempt_id,
            source_tool="research_deterministic_runner",
            source_tier="deterministic_calculation" if validator in {"snapshot", "metric_ledger", "delivery_audit"} else "reputable_secondary",
            uri=uri,
            data_as_of=self._goal_as_of(goal_id),
            method=validator,
            scope=title,
            hash=hashlib.sha256(f"{goal_id}:{task['task_id']}:{attempt_id}:{title}".encode()).hexdigest(),
            metadata={
                "title": title,
                "validator": validator,
                "snapshot_id": self._snapshot_id(goal_id),
            },
        )
        self.repo.register_evidence(evidence)
        self.repo.verify_evidence(evidence.evidence_id, checker="research_deterministic_runner")

    def _ingest_extracted_precision_cards(
        self,
        goal_id: str,
        result: dict[str, Any],
    ) -> None:
        """Persist precision-card-v1 rows extracted by analyst_cards when corpus exists."""
        from kss.research.recipe_metrics import extract_precision_card_payloads

        cards = extract_precision_card_payloads(result)
        if not cards:
            return
        goal = self.repo.get_goal(goal_id) or {}
        path_value = str((goal.get("inputs") or {}).get("analyst_corpus_path") or "").strip()
        if not path_value:
            path_value = os.environ.get("KSS_ANALYST_CORPUS_PATH", "").strip()
        if not path_value:
            return
        imported = self.import_analyst_corpus(
            goal_id=goal_id,
            payload={"path": path_value, "precision_cards": cards},
        )
        if not imported.get("ok"):
            self._emit(
                goal_id,
                "precision_cards_ingest_skipped",
                {"error": imported.get("error"), "detail": imported.get("detail")},
            )

    def _capture_task_result(

        self,
        goal_id: str,
        task: dict[str, Any],
        attempt_id: str,
        result: dict[str, Any],
    ) -> None:
        """Persist structured task output and only tool-backed evidence."""
        artifact = self.artifacts.put_bytes(
            goal_id=goal_id,
            task_id=task["task_id"],
            attempt_id=attempt_id,
            kind="task_result",
            name=f"{task['kind']}-{attempt_id}.json",
            data=dumps(
                {
                    key: value
                    for key, value in result.items()
                    if not key.startswith("_")
                }
            ).encode("utf-8"),
            media_type="application/json",
            metadata={
                "task_kind": task["kind"],
                "status": result.get("status"),
                "snapshot_id": self._snapshot_id(goal_id),
            },
        )
        captured_ids: list[str] = []
        tool_artifact_ids: list[str] = []
        criterion = self._criterion_for_validator(
            goal_id,
            "source_coverage" if task["kind"] == "collect_sources" else "evidence",
        )
        from kss.research.recipe_metrics import iter_url_evidence_refs

        existing_uris = {
            str(item.get("uri") or "")
            for item in self.repo.evidence_for_goal(goal_id)
            if str((item.get("metadata") or {}).get("snapshot_id") or "")
            == self._snapshot_id(goal_id)
            and item.get("uri")
        }
        for source in iter_url_evidence_refs(result):
            if not isinstance(source, dict) or not source.get("url"):
                continue
            url = str(source.get("url") or "").strip()
            if not url or url in existing_uris:
                continue
            existing_uris.add(url)
            evidence_id = new_id("ev")
            source_payload = stable_json(source)
            evidence = Evidence(
                evidence_id=evidence_id,
                goal_id=goal_id,
                criterion_id=criterion["criterion_id"] if criterion else None,
                task_id=task["task_id"],
                attempt_id=attempt_id,
                run_id=result.get("run_id"),
                tool_call_id=source.get("tool_event_id"),
                source_tool=str(source.get("tool_name") or "research_tool"),
                provider=str(source.get("provider") or "") or None,
                uri=str(source["url"]),
                artifact_id=artifact["artifact_id"],
                data_as_of=str(
                    self._goal_as_of(goal_id)
                    if task.get("kind") == "collect_sources"
                    else source.get("retrievedAt")
                    or source.get("data_as_of")
                    or self._goal_as_of(goal_id)
                ),
                method="successful_tool_result",
                scope=str(source.get("title") or task["title"]),
                hash=hashlib.sha256(source_payload.encode("utf-8")).hexdigest(),
                source_tier=str(
                    source.get("sourceTier")
                    or source.get("source_tier")
                    or "unknown"
                ),
                caveat=source.get("caveat"),
                metadata={
                    "title": source.get("title"),
                    "used_for": source.get("usedFor"),
                    "snapshot_id": self._snapshot_id(goal_id),
                },
            )
            self.repo.register_evidence(evidence)
            self.repo.verify_evidence(
                evidence_id,
                checker="tool_result_integrity",
                detail={
                    "tool_event_id": source.get("tool_event_id"),
                    "object_hash": artifact["object_hash"],
                },
            )
            captured_ids.append(evidence_id)

        validator_by_task = {
            "compute_temperature": "metric_ledger",
            "theme_consensus": "theme_consensus",
            "risk_radar": "risk_radar",
            "analyst_cards": "precision_cards",
        }
        local_criterion = self._criterion_for_validator(
            goal_id,
            validator_by_task.get(task["kind"], "evidence"),
        )
        for tool_result in result.get("_tool_results") or []:
            if not isinstance(tool_result, dict):
                continue
            tool_name = str(tool_result.get("tool_name") or "research_tool")
            raw_payload = tool_result.get("result")
            if (
                tool_name in {
                    "research_search",
                    "research_fetch",
                    "research_bundle",
                }
                or not isinstance(raw_payload, dict)
                or raw_payload.get("error")
                or raw_payload.get("is_error")
            ):
                continue
            encoded = stable_json(raw_payload).encode("utf-8")
            tool_artifact = self.artifacts.put_bytes(
                goal_id=goal_id,
                task_id=task["task_id"],
                attempt_id=attempt_id,
                kind="tool_result",
                name=f"{tool_name}-{tool_result.get('tool_call_id') or new_id('call')}.json",
                data=encoded,
                media_type="application/json",
                metadata={
                    "tool_name": tool_name,
                    "snapshot_id": self._snapshot_id(goal_id),
                },
            )
            tool_artifact_ids.append(tool_artifact["artifact_id"])
            evidence_id = new_id("ev")
            numeric_values = self._numeric_values(raw_payload)
            evidence = Evidence(
                evidence_id=evidence_id,
                goal_id=goal_id,
                criterion_id=(
                    local_criterion["criterion_id"] if local_criterion else None
                ),
                task_id=task["task_id"],
                attempt_id=attempt_id,
                run_id=result.get("run_id"),
                tool_call_id=str(
                    tool_result.get("tool_call_id") or ""
                )
                or None,
                source_tool=tool_name,
                uri=(
                    f"kss-tool://{tool_name}/"
                    f"{tool_result.get('tool_call_id') or evidence_id}"
                ),
                artifact_id=tool_artifact["artifact_id"],
                data_as_of=self._goal_as_of(goal_id),
                method="successful_tool_result",
                scope=task["title"],
                hash=hashlib.sha256(encoded).hexdigest(),
                source_tier="deterministic_calculation",
                metadata={
                    "title": f"{task['title']} · {tool_name}",
                    "numeric_values": numeric_values,
                    "snapshot_id": self._snapshot_id(goal_id),
                },
            )
            self.repo.register_evidence(evidence)
            self.repo.verify_evidence(
                evidence_id,
                checker="tool_result_integrity",
                detail={"object_hash": tool_artifact["object_hash"]},
            )
            captured_ids.append(evidence_id)

        ledger_evidence = self.repo.evidence_for_goal(goal_id)
        existing_ids = {item["evidence_id"] for item in ledger_evidence}
        reference_map: dict[str, str] = {}
        for item in ledger_evidence:
            evidence_id = str(item["evidence_id"])
            for reference in (
                evidence_id,
                item.get("uri"),
                item.get("tool_call_id"),
                item.get("source_tool"),
            ):
                if reference:
                    reference_map[str(reference)] = evidence_id

        normalized_claims: list[Any] = []
        can_submit_claims = bool(
            (task.get("payload") or {}).get("can_submit_claims", True)
        )
        raw_claims = result.get("claims") or []
        if raw_claims and not can_submit_claims:
            result.setdefault("warnings", []).append(
                "agent_role_cannot_submit_claims"
            )
            raw_claims = []
        for raw_claim in raw_claims:
            if isinstance(raw_claim, str):
                content = raw_claim
                requested_refs: list[str] = []
                confidence = None
                normalized_claim: Any = raw_claim
            elif isinstance(raw_claim, dict):
                content = str(raw_claim.get("content") or raw_claim.get("text") or "")
                requested_refs = [
                    str(value) for value in raw_claim.get("evidence_refs") or []
                ]
                confidence = raw_claim.get("confidence")
                normalized_claim = dict(raw_claim)
            else:
                continue
            resolved = list(
                dict.fromkeys(
                    reference_map.get(value, value)
                    for value in requested_refs
                    if reference_map.get(value, value) in existing_ids
                )
            )
            if isinstance(normalized_claim, dict):
                normalized_claim["evidence_refs"] = resolved
                metric = normalized_claim.get("metric")
                if isinstance(metric, dict):
                    normalized_metric = dict(metric)
                    normalized_metric["input_refs"] = list(
                        dict.fromkeys(
                            reference_map.get(str(value), str(value))
                            for value in metric.get("input_refs") or []
                            if reference_map.get(str(value), str(value))
                            in existing_ids
                        )
                    )
                    normalized_claim["metric"] = normalized_metric
            normalized_claims.append(normalized_claim)
            if not content.strip():
                continue
            self.repo.register_claim(
                Claim(
                    claim_id=new_id("claim"),
                    goal_id=goal_id,
                    content=content,
                    status="supported" if resolved else "proposed",
                    task_id=task["task_id"],
                    confidence=confidence,
                    evidence_ids=resolved,
                )
            )

        result["claims"] = normalized_claims
        result.setdefault("artifact_refs", []).extend(
            [artifact["artifact_id"], *tool_artifact_ids]
        )
        result.setdefault("evidence_refs", []).extend(captured_ids)

    @staticmethod
    def _numeric_values(value: Any, *, limit: int = 200) -> list[float]:
        values: list[float] = []

        def visit(item: Any) -> None:
            if len(values) >= limit:
                return
            if isinstance(item, bool) or item is None:
                return
            if isinstance(item, (int, float)):
                number = float(item)
                if math.isfinite(number):
                    values.append(number)
            elif isinstance(item, list):
                for child in item:
                    visit(child)
            elif isinstance(item, dict):
                for child in item.values():
                    visit(child)

        visit(value)
        return values

    def _dependency_summaries(self, task_id: str) -> list[dict[str, Any]]:
        with connect(self.db_path) as conn:
            ensure_schema(conn)
            rows = conn.execute(
                """
                SELECT t.task_id, t.kind, t.status, a.result_json
                FROM research_task_dependencies d
                JOIN research_tasks t ON t.task_id=d.depends_on_task_id
                LEFT JOIN research_attempts a ON a.attempt_id=t.current_attempt_id
                WHERE d.task_id=?
                ORDER BY t.sequence_index
                """,
                (task_id,),
            ).fetchall()
        return [
            {
                "task_id": row["task_id"],
                "kind": row["kind"],
                "status": row["status"],
                "result": loads(row["result_json"], {}),
            }
            for row in rows
        ]

    def _record_usage(self, goal_id: str, usage: dict[str, Any]) -> None:
        conn = self.repo.transaction()
        try:
            row = conn.execute(
                "SELECT usage_json, started_at FROM research_goals WHERE goal_id=?",
                (goal_id,),
            ).fetchone()
            if not row:
                self.repo.commit_close(conn)
                return
            current = loads(row["usage_json"], {})
            tokens = int(
                usage.get("total_tokens")
                or usage.get("provider_tokens")
                or (
                    int(usage.get("input_tokens") or 0)
                    + int(usage.get("output_tokens") or 0)
                )
            )
            current["provider_tokens"] = int(
                current.get("provider_tokens") or 0
            ) + tokens
            current["nodes"] = int(current.get("nodes") or 0) + 1
            if row["started_at"]:
                try:
                    started = datetime.fromisoformat(str(row["started_at"]))
                    current["seconds"] = max(
                        0,
                        int(
                            (
                                datetime.now(timezone.utc)
                                - started.astimezone(timezone.utc)
                            ).total_seconds()
                        ),
                    )
                except ValueError:
                    pass
            conn.execute(
                "UPDATE research_goals SET usage_json=?, updated_at=? WHERE goal_id=?",
                (dumps(current), utc_now(), goal_id),
            )
            self.repo.commit_close(conn)
        except Exception:
            conn.rollback()
            self.repo.commit_close(conn)
            raise

    def _budget_exceeded(self, goal: dict[str, Any]) -> bool:
        budget = goal.get("budget") or {}
        usage = goal.get("usage") or {}
        checks = (
            ("max_nodes", "nodes"),
            ("max_seconds", "seconds"),
            ("max_provider_tokens", "provider_tokens"),
        )
        return any(
            int(budget.get(limit) or 0) > 0
            and int(usage.get(used) or 0) >= int(budget[limit])
            for limit, used in checks
        )

    def _active_attempt(self) -> tuple[str | None, str | None]:
        with connect(self.db_path) as conn:
            ensure_schema(conn)
            row = conn.execute(
                """
                SELECT goal_id, attempt_id
                FROM research_attempts
                WHERE status='running'
                ORDER BY started_at
                LIMIT 1
                """
            ).fetchone()
        if not row:
            return None, None
        return str(row["goal_id"]), str(row["attempt_id"])

    def _criterion_for_validator(self, goal_id: str, validator: str) -> dict[str, Any] | None:
        goal = self.repo.get_goal(goal_id)
        for criterion in (goal or {}).get("criteria", []):
            if criterion.get("validator") == validator:
                return criterion
        return None

    def _first_criterion(self, goal_id: str) -> dict[str, Any] | None:
        criteria = (self.repo.get_goal(goal_id) or {}).get("criteria", [])
        return criteria[0] if criteria else None

    def _goal_as_of(self, goal_id: str) -> str:
        goal = self.repo.get_goal(goal_id) or {}
        snapshot = goal.get("snapshot") or {}
        return str(snapshot.get("as_of") or date.today().isoformat())

    def _snapshot_id(self, goal_id: str) -> str:
        goal = self.repo.get_goal(goal_id) or {}
        return str((goal.get("snapshot") or {}).get("snapshot_id") or "")

    # ------------------------------------------------------------------
    # Export/publication
    # ------------------------------------------------------------------

    def _export(self, *, goal_id: str | None, artifact_id: str | None, payload: dict[str, Any], require_pass: bool, event: str) -> dict[str, Any]:
        if not goal_id:
            return {"protocol_version": 1, "ok": False, "error": "goal_id_required"}
        artifact = self._resolve_artifact(goal_id, artifact_id)
        if not artifact:
            return {"protocol_version": 1, "ok": False, "error": "artifact_not_found", "goal": goal_id, "goal_id": goal_id}
        if require_pass and not self._latest_audit_pass(goal_id):
            return {"protocol_version": 1, "ok": False, "error": "audit_not_passed", "goal": goal_id, "goal_id": goal_id}
        if require_pass:
            goal = self.repo.get_goal(goal_id) or {}
            artifact_metadata = artifact.get("metadata") or {}
            artifact_snapshot = str(
                artifact_metadata.get("snapshot_id") or ""
            )
            current_snapshot = str(
                (goal.get("snapshot") or {}).get("snapshot_id") or ""
            )
            if (
                goal.get("status") != "completed"
                or not artifact_snapshot
                or artifact_snapshot != current_snapshot
                or bool(artifact_metadata.get("draft"))
                or artifact_metadata.get("audit_status") != "pass"
            ):
                return {
                    "protocol_version": 1,
                    "ok": False,
                    "error": "artifact_not_current_completed_snapshot",
                    "goal": goal_id,
                    "goal_id": goal_id,
                }
        destination = payload.get("destination")
        if not destination:
            destination = str(self.state_root / "storage" / "agent" / "research" / "exports" / goal_id / artifact["name"])
        dest = self._safe_destination(destination)
        result = self.artifacts.export_object(
            object_hash=artifact["object_hash"],
            destination=dest,
            allow_overwrite=bool(payload.get("overwrite", False)),
        )
        if require_pass:
            pub_id = new_id("pub")
            with connect(self.db_path) as conn:
                ensure_schema(conn)
                conn.execute(
                    "INSERT INTO research_publications (publication_id, goal_id, artifact_id, destination, object_hash, status, created_at) VALUES (?, ?, ?, ?, ?, 'published', ?)",
                    (pub_id, goal_id, artifact["artifact_id"], result["destination"], result["sha256"], utc_now()),
                )
            self._emit(goal_id, "artifact_ready", {"publication_id": pub_id, "destination": result["destination"], "sha256": result["sha256"]})
        goal = self.repo.get_goal(goal_id) or {}
        return {
            "protocol_version": 1,
            "ok": True,
            "event": event,
            "goal": self._wire_goal(goal),
            "detail": self._wire_goal(goal),
            "goal_id": goal_id,
            "artifact": self._wire_artifact(artifact),
            **result,
        }

    def _resolve_artifact(self, goal_id: str, artifact_id: str | None) -> dict[str, Any] | None:
        artifacts = self.artifacts.list_goal(goal_id)
        if artifact_id:
            return next((a for a in artifacts if a["artifact_id"] == artifact_id or a.get("id") == artifact_id), None)
        html_artifacts = [a for a in artifacts if a.get("kind") == "report_html"]
        return html_artifacts[-1] if html_artifacts else (artifacts[-1] if artifacts else None)

    def _safe_destination(self, raw: str) -> Path:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = self.state_root / path
        resolved = path.resolve()
        allowed = [self.state_root.resolve()]
        home = Path.home().resolve()
        allowed.extend(home / name for name in ALLOWED_EXPORT_ROOTS)
        if not any(resolved == root or root in resolved.parents for root in allowed):
            raise ValueError("destination_outside_allowed_roots")
        if resolved.exists() and resolved.is_dir():
            raise IsADirectoryError(str(resolved))
        return resolved

    def _latest_audit_pass(self, goal_id: str) -> bool:
        with connect(self.db_path) as conn:
            ensure_schema(conn)
            row = conn.execute(
                """
                SELECT status
                FROM research_audits
                WHERE goal_id=?
                ORDER BY created_at DESC, rowid DESC
                LIMIT 1
                """,
                (goal_id,),
            ).fetchone()
        return bool(row and row["status"] == "pass")

    def _latest_compiler_audit(self, goal_id: str) -> dict[str, Any] | None:
        current_snapshot = self._snapshot_id(goal_id)
        artifacts = [
            artifact
            for artifact in self.artifacts.list_goal(goal_id)
            if artifact.get("kind") == "audit_json"
            and str((artifact.get("metadata") or {}).get("snapshot_id") or "")
            == current_snapshot
        ]
        if not artifacts:
            return None
        try:
            value = json.loads(
                self.artifacts.read_bytes(str(artifacts[-1]["object_hash"]))
            )
        except (OSError, ValueError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    # ------------------------------------------------------------------
    # Wire helpers
    # ------------------------------------------------------------------

    def _emit(self, goal_id: str, event_type: str, payload: dict[str, Any], task_id: str | None = None, attempt_id: str | None = None, run_id: str | None = None) -> dict[str, Any]:
        conn = self.repo.transaction()
        try:
            frame = self.repo.append_event(conn, goal_id=goal_id, event_type=event_type, task_id=task_id, attempt_id=attempt_id, run_id=run_id, payload=payload)
            self.repo.commit_close(conn)
            self.repo.mirror_unmirrored(goal_id)
            return frame
        except Exception:
            conn.rollback()
            self.repo.commit_close(conn)
            raise

    def _set_goal(self, goal_id: str | None, status: str, event: str) -> dict[str, Any]:
        if not goal_id:
            return {"protocol_version": 1, "ok": False, "error": "goal_id_required"}
        if status in {"paused", "cancelled"}:
            abort = getattr(self.task_runner, "abort", None)
            if callable(abort):
                abort(
                    goal_id,
                    "research_cancelled"
                    if status == "cancelled"
                    else "research_paused",
                )
        self.repo.update_goal_status(goal_id, status)
        return {
            "protocol_version": 1,
            "ok": True,
            "event": event,
            "goal": self._wire_goal(self.repo.get_goal(goal_id) or {}),
            "goal_id": goal_id,
        }

    def _summary(self, goal: dict[str, Any]) -> dict[str, Any]:
        tasks = goal.get("tasks") or []
        done = sum(1 for task in tasks if task.get("status") == "succeeded")
        return {
            "goal_id": goal["goal_id"],
            "id": goal["goal_id"],
            "session_id": goal.get("session_id"),
            "profile_id": goal["profile_id"],
            "execution_mode": goal.get("execution_mode") or "single",
            "objective": goal["objective"],
            "status": goal["status"],
            "progress": (done / len(tasks)) if tasks else 0.0,
            "terminal_reason": goal.get("termination_reason"),
            "created_at": goal.get("created_at"),
            "updated_at": goal.get("updated_at"),
            "origin": goal.get("origin") or "manual",
            "cadence": goal.get("cadence"),
        }

    def _wire_goal(self, goal: dict[str, Any]) -> dict[str, Any]:
        if not goal:
            return {}
        evidence = self.repo.evidence_for_goal(goal["goal_id"])
        claims = self.repo.claims_for_goal(goal["goal_id"])
        return {
            **self._summary(goal),
            "inputs": goal.get("inputs") or {},
            "snapshot": goal.get("snapshot") or {},
            "budget": goal.get("budget") or {},
            "usage": goal.get("usage") or {},
            "criteria": goal.get("criteria") or [],
            "tasks": goal.get("tasks") or [],
            "research_agents": self._research_agents(goal),
            "artifacts": [self._wire_artifact(a) for a in goal.get("artifacts") or []],
            "evidence": [self._wire_evidence(item) for item in evidence],
            "claims": claims,
            "audit": self._wire_audits(goal["goal_id"]),
            "events": self.repo.list_events(goal["goal_id"], 0)[-200:],
        }

    def _research_agents(self, goal: dict[str, Any]) -> list[dict[str, Any]]:
        if (goal.get("execution_mode") or "single") != "multi_agent_pilot":
            return []
        try:
            profile = get_graph_profile(str(goal.get("profile_id") or ""))
        except ValueError:
            return []
        task_stats: dict[str, dict[str, int]] = {}
        for task in goal.get("tasks") or []:
            agent_id = task.get("agent_id")
            if not agent_id:
                continue
            stats = task_stats.setdefault(
                str(agent_id),
                {"tasks": 0, "succeeded": 0},
            )
            stats["tasks"] += 1
            if task.get("status") == "succeeded":
                stats["succeeded"] += 1
        return [
            {
                **agent.to_wire(),
                **task_stats.get(agent.agent_id, {"tasks": 0, "succeeded": 0}),
            }
            for agent in profile.agents
        ]

    def _wire_evidence(self, evidence: dict[str, Any]) -> dict[str, Any]:
        metadata = evidence.get("metadata") or {}
        return {
            "evidence_id": evidence["evidence_id"],
            "id": evidence["evidence_id"],
            "criterion_id": evidence.get("criterion_id"),
            "title": metadata.get("title")
            or evidence.get("scope")
            or evidence.get("source_tool")
            or "研究证据",
            "source": evidence.get("source_tool"),
            "source_tier": evidence.get("source_tier"),
            "url": evidence.get("uri"),
            "uri": evidence.get("uri"),
            "status": "verified" if evidence.get("verified") else "unverified",
            "verified": bool(evidence.get("verified")),
            "data_as_of": evidence.get("data_as_of"),
            "method": evidence.get("method"),
            "hash": evidence.get("hash"),
            "caveat": evidence.get("caveat"),
            "created_at": evidence.get("created_at"),
        }

    def _wire_audits(self, goal_id: str) -> list[dict[str, Any]]:
        with connect(self.db_path) as conn:
            ensure_schema(conn)
            rows = conn.execute(
                """
                SELECT audit_id, status, coverage_json, findings_json, created_at
                FROM research_audits
                WHERE goal_id=?
                ORDER BY created_at
                """,
                (goal_id,),
            ).fetchall()
        return [
            {
                "event_id": row["audit_id"],
                "id": row["audit_id"],
                "type": "research_audit",
                "status": row["status"],
                "timestamp": row["created_at"],
                "message": (
                    "审计通过"
                    if row["status"] == "pass"
                    else f"审计阻断：{len(loads(row['findings_json'], []))} 项"
                ),
                "coverage": loads(row["coverage_json"], {}),
                "findings": loads(row["findings_json"], []),
            }
            for row in rows
        ]

    def _wire_artifact(self, artifact: dict[str, Any]) -> dict[str, Any]:
        metadata = artifact.get("metadata") or {}
        wire = {
            "artifact_id": artifact["artifact_id"],
            "id": artifact["artifact_id"],
            "kind": artifact["kind"],
            "logical_name": metadata.get("logical_name") or artifact.get("name"),
            "name": artifact.get("name"),
            "media_type": artifact.get("media_type"),
            "size_bytes": artifact.get("size_bytes"),
            "sha256": artifact.get("object_hash"),
            "object_hash": artifact.get("object_hash"),
            "created_at": artifact.get("created_at"),
            "audit_status": metadata.get("audit_status"),
            "draft": metadata.get("draft"),
        }
        if artifact.get("media_type") == "text/html; charset=utf-8" and int(artifact.get("size_bytes") or 0) <= 2_000_000:
            try:
                wire["content"] = self.artifacts.read_bytes(str(artifact["object_hash"])).decode("utf-8")
            except Exception:
                pass
        return wire

    def _row_task(self, row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["required"] = bool(item["required"])
        item["payload"] = loads(item.pop("payload_json"), {})
        return item

    def _snapshot(self, *, profile_id: str, inputs: dict[str, Any], refresh_of: str | None = None) -> dict[str, Any]:
        as_of = str(inputs.get("as_of") or date.today().isoformat())
        frozen_inputs = dict(inputs)
        if profile_id == "investment-weekly-v3":
            match = DATE_RANGE_RE.fullmatch(str(inputs.get("date_range") or ""))
            if match and not frozen_inputs.get("trading_calendar"):
                start = date.fromisoformat(match.group("start"))
                end = date.fromisoformat(match.group("end"))
                frozen_inputs["trading_calendar"] = [
                    (start + timedelta(days=offset)).isoformat()
                    for offset in range((end - start).days + 1)
                    if (start + timedelta(days=offset)).weekday() < 5
                ]
        elif profile_id == "investment-daily-v1":
            trade_date = str(inputs.get("trade_date") or "")
            if trade_date and not frozen_inputs.get("trading_calendar"):
                frozen_inputs["trading_calendar"] = [trade_date]
        raw = stable_json({"profile_id": profile_id, "inputs": frozen_inputs, "as_of": as_of, "refresh_of": refresh_of})
        return {
            "snapshot_id": "snap_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24],
            "profile_id": profile_id,
            "inputs": frozen_inputs,
            "as_of": as_of,
            "created_at": utc_now(),
            "refresh_of": refresh_of,
        }

    def _is_fresh(
        self,
        evidence: dict[str, Any],
        criterion: dict[str, Any],
        *,
        reference_as_of: str | None = None,
    ) -> bool:
        freshness_days = criterion.get("freshness_days")
        if not freshness_days:
            return True
        raw = evidence.get("data_as_of")
        if not raw:
            return False
        try:
            data_date = datetime.fromisoformat(
                str(raw).replace("Z", "+00:00")
            ).date()
            reference_date = date.fromisoformat(
                str(reference_as_of or date.today().isoformat())[:10]
            )
        except ValueError:
            return False
        age = (reference_date - data_date).days
        return 0 <= age <= int(freshness_days)

    def _validate_inputs(
        self,
        profile_id: str,
        inputs: dict[str, Any],
    ) -> list[dict[str, str]]:
        if profile_id == "investment-daily-v1":
            findings: list[dict[str, str]] = []
            trade_date = str(inputs.get("trade_date") or "")
            try:
                trade = date.fromisoformat(trade_date)
            except ValueError:
                findings.append({"field": "trade_date", "reason": "invalid_iso_date"})
                trade = None
            try:
                as_of = date.fromisoformat(str(inputs.get("as_of") or ""))
            except ValueError:
                findings.append({"field": "as_of", "reason": "invalid_iso_date"})
                as_of = None
            if trade and as_of and as_of < trade:
                findings.append({"field": "as_of", "reason": "before_trade_date"})
            return findings
        if profile_id != "investment-weekly-v3":
            return []
        findings: list[dict[str, str]] = []
        date_range = str(inputs.get("date_range") or "")
        match = DATE_RANGE_RE.fullmatch(date_range)
        if not match:
            findings.append(
                {
                    "field": "date_range",
                    "reason": "expected_YYYY-MM-DD_to_YYYY-MM-DD",
                }
            )
        else:
            try:
                start = date.fromisoformat(match.group("start"))
                end = date.fromisoformat(match.group("end"))
                if start > end:
                    findings.append(
                        {"field": "date_range", "reason": "start_after_end"}
                    )
            except ValueError:
                findings.append(
                    {"field": "date_range", "reason": "invalid_calendar_date"}
                )
        raw_as_of = str(inputs.get("as_of") or "")
        try:
            as_of = date.fromisoformat(raw_as_of)
        except ValueError:
            findings.append({"field": "as_of", "reason": "invalid_iso_date"})
        else:
            if match:
                try:
                    end = date.fromisoformat(match.group("end"))
                    if as_of < end:
                        findings.append(
                            {
                                "field": "as_of",
                                "reason": "before_date_range_end",
                            }
                        )
                except ValueError:
                    pass
        return findings

    def _lease_expiry(self) -> str:
        return datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() + 900, timezone.utc).isoformat(timespec="seconds")
