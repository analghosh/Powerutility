"""The cell budget is the admission control for the whole feature.

cells = date_buckets x block_buckets x sum(series cardinality). The pathological case
is a long range at individual-block granularity with a per-plant dataset: five months
of Plant-wise DC across 34 plants is 489,600 cells.

Since 2026-09-20 there is exactly one date bucket per trading day, whatever Group
Date By says — the pill adds a column to the file instead of collapsing the date
axis — so the only levers left on the date side are the range itself. The week and
month arithmetic these tests used to pin now lives in `coverage`, where it labels
partial roll-up periods, and is exercised in `test_analytics_coverage.py`.
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
                        datasets=["DC_PLANT", "SG_PLANT", "ACTUAL_DEMAND"], ctx=CTX)
    assert e.band == budget.BAND_REFUSED


def test_exchange_datasets_count_three_series_each():
    e = budget.estimate(date_buckets=1, block_buckets=96,
                        datasets=["ACP_DAM"], ctx=CTX)
    assert e.series == 3
    assert e.cells == 288


def test_collapsing_the_blocks_collapses_the_problem():
    """One block bucket is trivially small, whatever is ticked.

    This is now the only coarsening lever: a named block band aggregates 96 blocks
    into one row. The date axis is always one bucket per day.
    """
    e = budget.estimate(date_buckets=5, block_buckets=1,
                        datasets=["DC_PLANT", "ACTUAL_DEMAND"], ctx=CTX)
    assert e.cells == 175
    assert e.band == budget.BAND_INLINE


def test_over_budget_estimate_carries_actionable_suggestions():
    e = budget.estimate(date_buckets=153, block_buckets=96,
                        datasets=["DC_PLANT", "ACTUAL_DEMAND"], ctx=CTX)
    kinds = {s["kind"] for s in e.suggestions}
    assert "coarser_blocks" in kinds
    assert "shorter_range" in kinds
    assert "fewer_datasets" in kinds
    for s in e.suggestions:
        assert s["cells"] < e.cells
        assert s["message"]


def test_no_suggestion_offers_a_coarser_date_grouping():
    """It would be advice that changes nothing.

    Group Date By stopped collapsing the date axis on 2026-09-20, so "switch to
    Week-wise" — which this list used to carry — leaves the cell count exactly where
    it was. A suggestion that does not shrink the request is worse than none: the
    user follows it and hits the same 413.
    """
    e = budget.estimate(date_buckets=153, block_buckets=96,
                        datasets=["DC_PLANT", "ACTUAL_DEMAND"], ctx=CTX)
    assert "coarser_dates" not in {s["kind"] for s in e.suggestions}


def test_inline_request_needs_no_suggestions():
    e = budget.estimate(date_buckets=1, block_buckets=96,
                        datasets=["ACTUAL_DEMAND"], ctx=CTX)
    assert e.suggestions == []


# 1 Apr -> 1 May is 31 days. Every grouping now counts the same buckets: the pill
# names a column in the file, not a bucket in Mongo.
@pytest.mark.parametrize("grouping", [
    "BLOCK_DATE_WISE", "HOURLY_WISE", "WEEK_WISE", "MONTH_WISE", "YEAR_WISE",
    # The two retired spellings still arrive from stored jobs and saved preferences.
    "BLOCK_WISE", "DATE_WISE",
])
def test_every_grouping_counts_one_bucket_per_day(grouping):
    assert budget.date_bucket_count(date(2026, 4, 1), date(2026, 5, 1), grouping) == 31


def test_the_grouping_argument_is_optional():
    """Callers that no longer have a grouping to pass must still be able to price."""
    assert budget.date_bucket_count(date(2026, 4, 1), date(2026, 4, 30)) == 30


def test_hourly_no_longer_multiplies_by_24():
    """It used to, and the multiplication was the defect.

    `HOURLY_WISE` keyed the group on the hour while the block axis still keyed on the
    block, so 96 values were spread across 24 columns and 96 rows — the cells were
    counted 24 times over and the file was 23/24 empty. The hour is a column now.
    """
    assert budget.date_bucket_count(date(2026, 4, 1), date(2026, 4, 1), "HOURLY_WISE") == 1


def test_empty_dataset_selection_is_zero_cells():
    e = budget.estimate(date_buckets=30, block_buckets=96, datasets=[], ctx=CTX)
    assert e.cells == 0


def test_an_inverted_range_is_zero_not_negative():
    assert budget.date_bucket_count(date(2026, 5, 1), date(2026, 4, 1)) == 0
