"""Safety and calendar tests for the launchd investment-report runner."""

from __future__ import annotations

import csv
import importlib.util
from pathlib import Path


_REPO = Path(__file__).resolve().parents[2]
_RUNNER_PATH = _REPO / "scripts" / "run_scheduled_research.py"
_SPEC = importlib.util.spec_from_file_location("scheduled_research_runner", _RUNNER_PATH)
assert _SPEC and _SPEC.loader
runner = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(runner)

_CALENDAR_WRITER_PATH = _REPO / "scripts" / "persist_trading_calendar.py"
_CALENDAR_SPEC = importlib.util.spec_from_file_location("calendar_writer", _CALENDAR_WRITER_PATH)
assert _CALENDAR_SPEC and _CALENDAR_SPEC.loader
calendar_writer = importlib.util.module_from_spec(_CALENDAR_SPEC)
_CALENDAR_SPEC.loader.exec_module(calendar_writer)


def _write_csv(path: Path, day: str) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        csv.writer(stream).writerows([["symbol", "date"], [path.stem, day]])


def test_latest_target_day_requires_all_eod_sentinels(tmp_path: Path) -> None:
    for symbol in ("688017", "688008", "300059", "159915"):
        _write_csv(tmp_path / f"cs_data_{symbol}.csv", "2026-07-17")

    assert runner._latest_target_day(tmp_path) == "2026-07-17"
    _write_csv(tmp_path / "cs_data_300059.csv", "2026-07-16")
    assert runner._latest_target_day(tmp_path) is None


def test_weekly_window_requires_an_explicit_last_open_day() -> None:
    calendar = ["2026-07-13", "2026-07-14", "2026-07-15", "2026-07-16"]
    assert runner._weekly_window("2026-07-16", calendar) == ("2026-07-13", "2026-07-16")
    assert runner._weekly_window("2026-07-15", calendar) is None
    assert runner._weekly_window("2026-07-17", calendar) is None


def test_scheduled_payloads_have_stable_idempotency_keys() -> None:
    daily = runner._build_payload("daily", "2026-07-17", None)
    weekly = runner._build_payload(
        "weekly",
        "2026-07-17",
        ("2026-07-13", "2026-07-17"),
        ["2026-07-13", "2026-07-14", "2026-07-15", "2026-07-16", "2026-07-17"],
    )

    assert daily["client_request_id"] == "scheduled:investment-daily-v1:2026-07-17"
    assert daily["origin"] == "scheduled"
    assert daily["cadence"] == "daily"
    assert weekly["client_request_id"] == "scheduled:investment-weekly-v3:2026-07-13_2026-07-17"
    assert weekly["inputs"]["date_range"] == "2026-07-13_to_2026-07-17"
    assert weekly["inputs"]["trading_calendar"] == [
        "2026-07-13",
        "2026-07-14",
        "2026-07-15",
        "2026-07-16",
        "2026-07-17",
    ]


def test_runner_contains_no_legacy_key_environment_fallback() -> None:
    source = _RUNNER_PATH.read_text(encoding="utf-8")
    assert "OPENAI_API_KEY" not in source
    assert "DEEPSEEK_API_KEY" not in source
    assert "KSS_LLM_PRIMARY_KEY" not in source
    assert "KSS_PI_AI_CREDENTIAL_SOCKET" in source
    assert "KSS_PI_AI_CREDENTIAL_NONCE" in source


def test_calendar_writer_normalizes_only_confirmed_open_days() -> None:
    class Frame:
        def to_dict(self, orient: str):
            assert orient == "records"
            return [
                {"cal_date": "20260716", "is_open": 1},
                {"cal_date": "20260717", "is_open": "0"},
                {"cal_date": "bad-date", "is_open": 1},
                {"cal_date": "20260720", "is_open": 1},
            ]

    assert calendar_writer._iso_open_dates(Frame()) == ["2026-07-16", "2026-07-20"]
