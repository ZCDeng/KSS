from __future__ import annotations

import pytest

from kss.research.corpus import AnalystMessage, content_sha256
from kss.research.investment_analysis import (
    PrecisionCardError,
    aggregate_kss_equivalent,
    check_precision_card,
    check_precision_cards,
)


def _message(
    source_message_id: str = "msg-1",
    content: str = "订单持续放量，机器人链景气度继续改善，短期催化来自新品发布。",
    analyst_id: str = "analyst-zhang",
    source_relation: str = "direct",
) -> AnalystMessage:
    return AnalystMessage(
        source_message_id=source_message_id,
        analyst_id=analyst_id,
        published_at="2026-07-27T09:30:00+08:00",
        source_uri=f"kss://analyst-corpus/{source_message_id}",
        content_hash=content_sha256(content),
        provenance={"channel": "analyst-chat", "source_relation": source_relation},
        content=content,
        object_ref=None,
        attachments=[],
        metadata={},
    )


def _card(
    message: AnalystMessage,
    card_id: str = "card-1",
    *,
    trade_date: str = "2026-07-27",
    analyst_id: str | None = None,
    **overrides,
) -> dict[str, object]:
    quote_text = "机器人链景气度继续改善"
    payload: dict[str, object] = {
        "protocol_version": "precision-card-v1",
        "card_id": card_id,
        "evidence": {
            "source_message_id": message.source_message_id,
            "content_hash": message.content_hash,
            "source_uri": message.source_uri,
        },
        "source": {
            "source_message_id": message.source_message_id,
            "content_hash": message.content_hash,
        },
        "analyst": {"analyst_id": analyst_id or message.analyst_id},
        "trade_date": trade_date,
        "instrument": "300000.SZ",
        "theme": "机器人",
        "stance_label": 2,
        "conviction": "high",
        "original_expression": quote_text,
        "risk": "估值偏高",
        "catalyst": "新品发布",
        "date_anchor": "2026-07-27",
        "evidence_grade": "A",
        "quote_span": {
            "start": message.content.index(quote_text) if message.content else 0,
            "end": (message.content.index(quote_text) + len(quote_text)) if message.content else 0,
            "text": quote_text,
        },
        "is_sellside_forward": message.provenance.get("source_relation") == "sell_side_forward",
        "extractor": {
            "model": "fixture-extractor",
            "version": "1",
            "run_id": f"extract-{card_id}",
        },
        "checker": {
            "model": "fixture-checker",
            "version": "1",
            "run_id": f"check-{card_id}",
            "result": "passed",
        },
    }
    payload.update(overrides)
    return payload


def test_check_precision_card_accepts_formal_schema_quote_hash_and_source() -> None:
    message = _message()

    card = check_precision_card(_card(message), [message])

    assert card.card_id == "card-1"
    assert card.analyst_id == "analyst-zhang"
    assert card.quote_span.text == "机器人链景气度继续改善"
    assert card.conviction_weight == 1.0


def test_check_precision_card_rejects_bad_quote_enum_hash_and_analyst() -> None:
    message = _message()

    with pytest.raises(PrecisionCardError, match="quote_span.text"):
        check_precision_card(
            _card(message, quote_span={"start": 0, "end": 2, "text": "不匹配"}),
            [message],
        )
    with pytest.raises(PrecisionCardError, match="stance_label"):
        check_precision_card(_card(message, stance_label=3), [message])
    with pytest.raises(PrecisionCardError, match="content_hash"):
        check_precision_card(_card(message, source={"source_message_id": "msg-1", "content_hash": "1" * 64}), [message])
    with pytest.raises(PrecisionCardError, match="analyst.analyst_id"):
        check_precision_card(_card(message, analyst_id="other-analyst"), [message])


def test_check_precision_card_enforces_sellside_forward_flag() -> None:
    forwarded = _message("msg-forward", source_relation="sell_side_forward")
    direct = _message("msg-direct")

    ok = check_precision_card(_card(forwarded), [forwarded])

    assert ok.is_sellside_forward is True
    with pytest.raises(PrecisionCardError, match="is_sellside_forward"):
        check_precision_card(_card(forwarded, is_sellside_forward=False), [forwarded])
    with pytest.raises(PrecisionCardError, match="is_sellside_forward"):
        check_precision_card(_card(direct, is_sellside_forward=True), [direct])


def test_aggregate_kss_equivalent_matches_approved_formulas() -> None:
    messages = [
        _message("m1", "机器人订单放量，机器人链景气度继续改善，短期催化来自新品发布。"),
        _message("m2", "机器人订单继续兑现，机器人链景气度继续改善，产能扩张值得跟踪。", analyst_id="analyst-li"),
        _message("m3", "机器人需求回升，机器人链景气度继续改善，订单排产维持高位。"),
        _message("m4", "机器人估值偏高，机器人链景气度继续改善，但要关注估值风险。", analyst_id="analyst-li"),
        _message("m5", "机器人链景气度继续改善，卖方转发观点不计入共识。", analyst_id="analyst-forward", source_relation="sell_side_forward"),
    ]
    payloads = [
        _card(messages[0], "c1", trade_date="2026-07-24", conviction="high", date_anchor="2026-07-27"),
        _card(messages[1], "c2", trade_date="2026-07-25", conviction="medium", catalyst="产能扩张", date_anchor="2026-07-30"),
        _card(messages[2], "c3", trade_date="2026-07-26", conviction="low", risk="供应扰动"),
        _card(
            messages[3],
            "c4",
            trade_date="2026-07-27",
            stance_label=-1,
            conviction="medium",
            risk="估值偏高",
            catalyst="估值消化",
            date_anchor="2026-08-01",
        ),
        _card(messages[4], "c5", trade_date="2026-07-27", conviction="high", risk="估值偏高"),
    ]
    cards = check_precision_cards(payloads, messages)

    first = aggregate_kss_equivalent(
        cards,
        period_end="2026-07-27",
        snapshot_hash="2" * 64,
        config={"period": "daily"},
    )
    second = aggregate_kss_equivalent(
        cards,
        period_end="2026-07-27",
        snapshot_hash="2" * 64,
        config={"period": "daily"},
    )
    equivalent = first["kss_equivalent"]

    assert first == second
    assert first["sample_exact"]["calibrated_from_sample"] is True
    assert first["formula_classification"]["temperature"] == "kss_equivalent"
    assert first["formula_classification"]["risk_severity"] == "sample_exact"
    assert set(first["hashes"]) == {"formula_hash", "config_hash", "input_hash", "snapshot_hash"}
    assert equivalent["protocol_version"] == "kss-equivalent-v1"
    assert equivalent["consensus_card_count"] == 4
    assert equivalent["sellside_forward_excluded_count"] == 1
    assert {
        row["analyst_id"] for row in equivalent["analyst_profiles"]
    } == {"analyst-li", "analyst-zhang"}
    assert equivalent["temperature"] == pytest.approx(0.625)
    assert equivalent["theme_strength"][0] == {
        "instrument": "300000.SZ",
        "theme": "机器人",
        "contribution_sum": 1.875,
        "cards": 4,
        "analysts": ["analyst-li", "analyst-zhang"],
    }
    assert equivalent["risk_severity"][0]["risk"] == "估值偏高"
    assert equivalent["risk_severity"][0]["severity"] == pytest.approx(5.5)
    assert equivalent["persistent_themes"][0]["theme"] == "机器人"
    assert equivalent["persistent_themes"][0]["trade_days"] == [
        "2026-07-24",
        "2026-07-25",
        "2026-07-26",
    ]
    catalyst_status = {row["catalyst"]: row["status"] for row in equivalent["catalysts"]}
    assert catalyst_status["新品发布"] == "realized"
    assert catalyst_status["产能扩张"] == "pending"
    assert equivalent["trend"]["daily_theme_strength"] == [
        {"trade_date": "2026-07-24", "theme_strength": 1.0},
        {"trade_date": "2026-07-25", "theme_strength": 0.75},
        {"trade_date": "2026-07-26", "theme_strength": 0.5},
        {"trade_date": "2026-07-27", "theme_strength": -0.375},
    ]


def test_custom_analyst_weights_require_approval_and_affect_temperature() -> None:
    first = _message("m-weight-1")
    second = _message(
        "m-weight-2",
        analyst_id="analyst-li",
    )
    cards = check_precision_cards(
        [
            _card(first, "c-weight-1", stance_label=2),
            _card(second, "c-weight-2", stance_label=-2),
        ],
        [first, second],
    )

    with pytest.raises(PrecisionCardError, match="用户批准"):
        aggregate_kss_equivalent(
            cards,
            period_end="2026-07-27",
            config={
                "analyst_weights": {
                    "version": "user-v1",
                    "approved": False,
                    "weights": {"analyst-zhang": 2.0},
                }
            },
        )

    weighted = aggregate_kss_equivalent(
        cards,
        period_end="2026-07-27",
        config={
            "analyst_weights": {
                "version": "user-v1",
                "approved": True,
                "weights": {"analyst-zhang": 2.0},
            }
        },
    )

    assert weighted["kss_equivalent"]["temperature"] == pytest.approx(1 / 3)
    assert weighted["audit_inputs"][0]["analyst_weight"] == 2.0
