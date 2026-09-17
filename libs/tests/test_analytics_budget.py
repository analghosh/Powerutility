"""The cell budget is the admission control for the whole feature.

cells = date_buckets x block_buckets x sum(series cardinality). The pathological case
is a long range at individual-block granularity with a per-plant dataset: five months
of Plant-wise DC across 34 plants is 489,600 cells, which is why plant datasets need
an explicit selector rather than defaulting to all.
"""
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services.processing.app.v2.analytics import budget  # noqa: E402

CTX = {"plant_count": 34, "exchange_count": 3}


def test_one_dataset_one_month_is_inline():
    e = budget.estimate(date_buckets=30, block_buckets=96,
                        datasets=["ACTUAL_DEMAND"], ctx=CTX)
    assert e.cells == 2880
    assert e.band == budget.BAND_INLINE


def test_ten_state_datasets_five_months_is_inline_capped():
    codes = ["ACTUAL_DEMAND", "ROSTERING", "DEMAND_UDM", "ROSTERING", "FREQUENCY",
             "FORECAST_DAF", "FORECAST_IDF", "MAPE_DAF", "MAPE_IDF",
             "FINAL_SCHEDULE_STATE"]
    e = budget.estimate(date_buckets=150, block_buckets=96, datasets=codes, ctx=CTX)
    assert e.cells == 144_000
    assert e.band == budget.BAND_INLINE


def test_plant_wise_five_months_exceeds_the_inline_cap():
    """153 days x 96 blocks x 34 plants = 499,392 — the case that must not render inline."""
    e = budget.estimate(date_buckets=153, block_buckets=96,
                        datasets=["DC_PLANT"], ctx=CTX)
    assert e.cells == 499_392
    assert e.band == budget.BAND_INLINE_CAPPED


def test_plant_wise_plus_a_second_dataset_goes_async_only():
    e = budget.estimate(date_buckets=153, block_buckets=96,
                        datasets=["DC_PLANT", "ACTUAL_DEMAND"], ctx=CTX)
    assert e.band == budget.BAND_ASYNC_ONLY


def test_absurd_request_is_refused_outright():
    e = budget.estimate(date_buckets=3650, block_buckets=96,
                        datasets=["DC_PLANT", "SG_PLANT", "FINAL_SCHEDULE_PLANT"], ctx=CTX)
    assert e.band == budget.BAND_REFUSED


def test_exchange_datasets_count_three_series_each():
    e = budget.estimate(date_buckets=1, block_buckets=96,
                        datasets=["ACP_DAM"], ctx=CTX)
    assert e.series == 3
    assert e.cells == 288


def test_coarse_grouping_collapses_the_problem():
    """Month-wise over five months is trivially small, whatever is ticked."""
    e = budget.estimate(date_buckets=5, block_buckets=1,
                        datasets=["DC_PLANT", "ACTUAL_DEMAND"], ctx=CTX)
    assert e.cells == 175
    assert e.band == budget.BAND_INLINE


def test_over_budget_estimate_carries_actionable_suggestions():
    e = budget.estimate(date_buckets=153, block_buckets=96,
                        datasets=["DC_PLANT", "ACTUAL_DEMAND"], ctx=CTX)
    kinds = {s["kind"] for s in e.suggestions}
    assert "coarser_blocks" in kinds or "coarser_dates" in kinds
    assert "fewer_plants" in kinds
    for s in e.suggestions:
        assert s["cells"] < e.cells
        assert s["message"]


def test_inline_request_needs_no_suggestions():
    e = budget.estimate(date_buckets=1, block_buckets=96,
                        datasets=["ACTUAL_DEMAND"], ctx=CTX)
    assert e.suggestions == []


# 1 Apr -> 1 May is 31 days. MONTH_WISE is calendar-month keyed (`%Y-%m` in the
# Mongo grouping), so that range yields two buckets, "2026-04" and "2026-05".
@pytest.mark.parametrize("grouping,expected", [
    ("BLOCK_WISE", 31), ("DATE_WISE", 31), ("WEEK_WISE", 5),
    ("MONTH_WISE", 2), ("YEAR_WISE", 1),
])
def test_date_bucket_count_per_grouping(grouping, expected):
    assert budget.date_bucket_count(date(2026, 4, 1), date(2026, 5, 1), grouping) == expected


def test_hourly_grouping_multiplies_by_24():
    assert budget.date_bucket_count(date(2026, 4, 1), date(2026, 4, 1), "HOURLY_WISE") == 24


def test_empty_dataset_selection_is_zero_cells():
    e = budget.estimate(date_buckets=30, block_buckets=96, datasets=[], ctx=CTX)
    assert e.cells == 0


# ------------------------------------------------ partial weeks (user question)

def test_three_days_inside_one_iso_week_is_one_bucket():
    """Wed 26 → Fri 28 Aug 2026 — all W35."""
    assert budget.date_bucket_count(date(2026, 8, 26), date(2026, 8, 28), "WEEK_WISE") == 1


def test_three_days_straddling_a_week_boundary_is_two_buckets():
    """Sat 29 → Mon 31 Aug 2026 spans W35 and W36. ceil(3/7) said 1 — an UNDERestimate,
    which is the wrong direction for admission control."""
    assert budget.date_bucket_count(date(2026, 8, 29), date(2026, 8, 31), "WEEK_WISE") == 2


def test_nine_days_across_two_partial_weeks_is_two_buckets():
    """Wed 26 Aug → Thu 3 Sep: W35 (5 days) + W36 (4 days)."""
    assert budget.date_bucket_count(date(2026, 8, 26), date(2026, 9, 3), "WEEK_WISE") == 2


def test_a_full_iso_week_monday_to_sunday_is_one_bucket():
    assert budget.date_bucket_count(date(2026, 8, 24), date(2026, 8, 30), "WEEK_WISE") == 1


def test_eight_days_monday_to_monday_is_two_buckets():
    assert budget.date_bucket_count(date(2026, 8, 24), date(2026, 8, 31), "WEEK_WISE") == 2


def test_week_count_survives_the_iso_year_boundary():
    """29 Dec 2025 is 2026-W01; 22 Dec 2025 is 2025-W52. Two weeks, one year rollover."""
    assert budget.date_bucket_count(date(2025, 12, 22), date(2025, 12, 29), "WEEK_WISE") == 2
