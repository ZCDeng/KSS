from kss.research.report_html import display_period


def test_display_period_collapses_same_day_range() -> None:
    assert display_period("2026-08-14_to_2026-08-14", "2026-08-14") == "2026-08-14"


def test_display_period_keeps_distinct_range() -> None:
    assert display_period("2026-08-10_to_2026-08-14", "2026-08-14") == "2026-08-10 至 2026-08-14"
