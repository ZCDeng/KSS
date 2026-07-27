from __future__ import annotations

import json
from pathlib import Path

from kss.research.corpus import content_sha256
from kss.research.service import ResearchService
from kss.storage.db import connect, ensure_schema


def _source_row(content: str) -> dict[str, object]:
    return {
        "protocol_version": "analyst-corpus-v1",
        "source_message_id": "source-1",
        "analyst_id": "analyst-1",
        "published_at": "2026-07-27T09:30:00+08:00",
        "content": content,
        "source_uri": "kss://analyst/source-1",
        "content_hash": content_sha256(content),
        "provenance": {
            "provider": "local_fixture",
            "source_tier": "official_or_primary",
            "source_relation": "direct",
        },
    }


def _card(content: str) -> dict[str, object]:
    quote = "机器人订单继续放量"
    start = content.index(quote)
    return {
        "protocol_version": "precision-card-v1",
        "card_id": "card-1",
        "evidence": {
            "source_message_id": "source-1",
            "content_hash": content_sha256(content),
        },
        "source": {
            "source_message_id": "source-1",
            "content_hash": content_sha256(content),
        },
        "analyst": {"analyst_id": "analyst-1"},
        "trade_date": "2026-07-27",
        "instrument": "300000.SZ",
        "theme": "机器人",
        "stance_label": 2,
        "conviction": "high",
        "original_expression": quote,
        "risk": "估值偏高",
        "catalyst": "订单兑现",
        "date_anchor": "2026-07-27",
        "evidence_grade": "A",
        "quote_span": {
            "start": start,
            "end": start + len(quote),
            "text": quote,
        },
        "is_sellside_forward": False,
        "extractor": {
            "model": "extractor-model",
            "version": "1",
            "run_id": "extract-run",
        },
        "checker": {
            "model": "checker-model",
            "version": "1",
            "run_id": "check-run",
            "result": "passed",
        },
    }


def test_corpus_then_checked_cards_can_be_imported_in_two_phases(
    tmp_path: Path,
) -> None:
    project_root = Path(__file__).resolve().parents[2]
    service = ResearchService(state_root=tmp_path, project_root=project_root)
    created = service.create_goal(
        payload={
            "client_request_id": "corpus-two-phase",
            "profile_id": "investment-daily-v1",
            "objective": "测试日报",
            "inputs": {
                "trade_date": "2026-07-27",
                "as_of": "2026-07-27",
            },
        }
    )
    content = "机器人订单继续放量，景气度改善。"
    corpus_path = tmp_path / "analyst-corpus-v1.jsonl"
    corpus_path.write_text(
        json.dumps(_source_row(content), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    first = service.import_analyst_corpus(
        goal_id=created["goal_id"],
        payload={"path": str(corpus_path)},
    )
    second = service.import_analyst_corpus(
        goal_id=created["goal_id"],
        payload={
            "path": str(corpus_path),
            "precision_cards": [_card(content)],
        },
    )

    assert first["ok"] is True
    assert first["requires_card_extraction"] is True
    assert second["ok"] is True
    assert second["event"] == "precision_cards_imported"
    assert second["verified_card_count"] == 1
    with connect(service.db_path) as conn:
        ensure_schema(conn)
        counts = {
            "sources": conn.execute(
                "SELECT COUNT(*) FROM research_source_records"
            ).fetchone()[0],
            "cards": conn.execute(
                "SELECT COUNT(*) FROM research_precision_cards WHERE verified=1"
            ).fetchone()[0],
            "formulas": conn.execute(
                "SELECT COUNT(*) FROM research_formula_runs"
            ).fetchone()[0],
        }
    assert counts == {"sources": 1, "cards": 1, "formulas": 1}


def test_unapproved_analyst_weights_are_rejected_before_card_persistence(
    tmp_path: Path,
) -> None:
    project_root = Path(__file__).resolve().parents[2]
    service = ResearchService(state_root=tmp_path, project_root=project_root)
    created = service.create_goal(
        payload={
            "client_request_id": "weights-approval",
            "profile_id": "investment-daily-v1",
            "objective": "测试权重批准门",
            "inputs": {
                "trade_date": "2026-07-27",
                "as_of": "2026-07-27",
            },
        }
    )
    content = "机器人订单继续放量，景气度改善。"
    corpus_path = tmp_path / "corpus.jsonl"
    corpus_path.write_text(
        json.dumps(_source_row(content), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    result = service.import_analyst_corpus(
        goal_id=created["goal_id"],
        payload={
            "path": str(corpus_path),
            "precision_cards": [_card(content)],
            "analyst_weights": {
                "version": "user-v1",
                "approved": False,
                "weights": {"analyst-1": 1.5},
            },
        },
    )

    assert result["ok"] is False
    assert result["error"] == "analyst_corpus_invalid"
    with connect(service.db_path) as conn:
        ensure_schema(conn)
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM research_precision_cards"
            ).fetchone()[0]
            == 0
        )
