"""Partial-period coverage for weekly / monthly / yearly roll-ups.

A 3-day range inside a week still yields a bucket labelled with the whole week. The
engine's average is honest; without this annotation the label is not.
"""
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services.processing.app.v2.analytics import coverage as cov  # noqa: E402


def test_three_days_inside_one_week_is_one_partial_bucket():
    c = cov.period_coverage(date(2026, 8, 26), date(2026, 8, 28), "WEEK_WISE")
    assert list(c) == ["2026-W35"]
    assert c["2026-W35"] == {"days_covered": 3, "days_in_period": 7, "partial": True,
                             "from": "2026-08-26", "to": "2026-08-28"}


def test_nine_days_across_two_weeks_are_both_partial():
    """Wed 26 Aug → Thu 3 Sep: W35 gets 5 of 7, W36 gets 4 of 7."""
    c = cov.period_coverage(date(2026, 8, 26), date(2026, 9, 3), "WEEK_WISE")
    assert [(k, v["days_covered"], v["partial"]) for k, v in c.items()] == [
        ("2026-W35", 5, True), ("2026-W36", 4, True)]


def test_a_full_monday_to_sunday_week_is_not_partial():
    c = cov.period_coverage(date(2026, 8, 24), date(2026, 8, 30), "WEEK_WISE")
    assert c == {"2026-W35": {"days_covered": 7, "days_in_period": 7, "partial": False,
                              "from": "2026-08-24", "to": "2026-08-30"}}


def test_full_weeks_in_the_middle_of_a_range_are_complete_and_edges_partial():
    """Wed 19 Aug → Tue 8 Sep: W34 partial, W35 + W36 full, W37 partial."""
    c = cov.period_coverage(date(2026, 8, 19), date(2026, 9, 8), "WEEK_WISE")
    assert cov.partial_keys(c) == ["2026-W34", "2026-W37"]
    assert c["2026-W35"]["partial"] is False and c["2026-W36"]["partial"] is False


def test_keys_match_the_engines_iso_week_format():
    """29 Dec 2025 is ISO 2026-W01 — the engine keys on %G-W%V, so must this."""
    c = cov.period_coverage(date(2025, 12, 29), date(2025, 12, 29), "WEEK_WISE")
    assert list(c) == ["2026-W01"]


def test_month_coverage_uses_real_month_lengths():
    c = cov.period_coverage(date(2026, 2, 10), date(2026, 3, 5), "MONTH_WISE")
    assert c["2026-02"] == {"days_covered": 19, "days_in_period": 28, "partial": True,
                            "from": "2026-02-10", "to": "2026-02-28"}
    assert c["2026-03"]["days_covered"] == 5 and c["2026-03"]["days_in_period"] == 31


def test_a_whole_month_is_not_partial():
    c = cov.period_coverage(date(2026, 4, 1), date(2026, 4, 30), "MONTH_WISE")
    assert c == {"2026-04": {"days_covered": 30, "days_in_period": 30, "partial": False,
                             "from": "2026-04-01", "to": "2026-04-30"}}


def test_year_coverage():
    c = cov.period_coverage(date(2026, 11, 1), date(2027, 1, 15), "YEAR_WISE")
    assert c["2026"]["days_covered"] == 61 and c["2026"]["partial"] is True
    assert c["2027"]["days_covered"] == 15


@pytest.mark.parametrize("g", ["BLOCK_WISE", "DATE_WISE", "HOURLY_WISE"])
def test_non_period_groupings_have_no_coverage(g):
    assert cov.period_coverage(date(2026, 8, 1), date(2026, 8, 31), g) is None
    assert cov.partial_keys(None) == []


def test_inverted_range_yields_none():
    assert cov.period_coverage(date(2026, 8, 31), date(2026, 8, 1), "WEEK_WISE") is None
