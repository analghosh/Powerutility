"""The cell budget is the admission control for the whole feature.

cells = date_buckets x block_buckets x sum(series cardinality). The pathological case
is a long range at individual-block granularity with a per-plant dataset: five months
of Plant-wise DC across 34 plants is 489,600 cells.

Since 2026-09-20 there is exactly one date bucket per trading day, whatever Group
Date By says — the pill adds a column to the file instead of collapsing the date
axis — so the only levers left on the date side are the range itself.

The thresholds were re-scaled the same day. They had been set for a browser grid and
a sheet-per-dataset workbook; the single-table file buys far more per cell, and the
old ceiling refused a request that builds in 3.5 seconds. The bands now mean:
**inline** = short enough to hold the request open, **async_only** = built in the
background with progress (not a refusal), **refused** = the memory cap only.
"""
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services.processing.app.v2.analytics import budget, registry  # noqa: E402

CTX = {"plant_count": 34, "exchange_count": 3}


def test_one_dataset_one_month_is_inline():
    e = budget.estimate(date_buckets=30, block_buckets=96,
                        datasets=["ACTUAL_DEMAND"], ctx=CTX)
    assert e.cells == 2880
    assert e.band == budget.BAND_INLINE


def test_ten_state_datasets_five_months_is_inline():
    codes = ["ACTUAL_DEMAND", "ROSTERING", "DEMAND_UDM", "ROSTERING", "FREQUENCY",
             "FORECAST_DAF", "FORECAST_IDF", "MAPE_DAF", "MAPE_IDF",
             "FINAL_SCHEDULE_STATE"]
    e = budget.estimate(date_buckets=150, block_buckets=96, datasets=codes, ctx=CTX)
    assert e.cells == 144_000
    assert e.band == budget.BAND_INLINE


def test_plant_wise_five_months_is_served_inline():
    """153 days x 96 blocks x 34 plants = 499,392.

    This used to be `inline_capped` — too big for the request — under thresholds
    calibrated for a browser grid. Measured against the single-table file it is
    about three seconds, so it is simply served.
    """
    e = budget.estimate(date_buckets=153, block_buckets=96,
                        datasets=["DC_PLANT"], ctx=CTX)
    assert e.cells == 499_392
    assert e.band == budget.BAND_INLINE


def test_the_request_ptc_hit_is_now_an_ordinary_download():
    """411,264 cells — refused on 2026-09-20, and it builds in 3.5 seconds."""
    e = budget.estimate(date_buckets=51, block_buckets=96,
                        datasets=["DC_PLANT", "SG_PLANT"], ctx={"plant_count": 42})
    assert e.band == budget.BAND_INLINE


def test_a_year_of_everything_is_built_in_the_background_not_refused():
    """~4.9M cells, about 26 seconds. Too long to hold a connection, not too big
    to produce — so it goes to the background path WITH progress."""
    live = [c for c, d in registry.DATASETS.items() if d.availability == "live"]
    e = budget.estimate(date_buckets=365, block_buckets=96, datasets=live, ctx=CTX)
    assert e.band == budget.BAND_ASYNC_ONLY, f"{e.cells:,} cells landed in {e.band}"


def test_only_the_memory_cap_refuses():
    """`MAX` is not a rendering limit: the pivot holds the whole value matrix in
    RAM, on a box that is also serving the bidding screens."""
    # Ten years at individual blocks across a wide selection: ~27M cells, which at
    # ~24 bytes a float is over half a gigabyte of matrix before list overhead.
    codes = ["DC_PLANT", "SG_PLANT", "ACP_DAM", "ACV_DAM", "ACTUAL_DEMAND",
             "DRAWAL", "FREQUENCY", "DSM_RATE"]
    e = budget.estimate(date_buckets=3650, block_buckets=96, datasets=codes, ctx=CTX)
    assert e.cells > budget.THRESHOLDS["max"], f"{e.cells:,} is under the cap"
    assert e.band == budget.BAND_REFUSED


def test_three_years_of_everything_still_fits():
    """The cap protects memory; it must not refuse a file PTC could plausibly want."""
    live = [c for c, d in registry.DATASETS.items() if d.availability == "live"]
    e = budget.estimate(date_buckets=1095, block_buckets=96, datasets=live, ctx=CTX)
    assert e.band == budget.BAND_ASYNC_ONLY, f"{e.cells:,} cells was refused"


def test_exchange_datasets_count_three_series_each():
    e = budget.estimate(date_buckets=1, block_buckets=96,
                        datasets=["ACP_DAM"], ctx=CTX)
    assert e.series == 3
    assert e.cells == 288


def test_the_thresholds_are_ordered_and_generous():
    """A regression here is a silent return to refusing three-second exports."""
    th = budget.THRESHOLDS
    assert th["inline"] < th["inline_capped"] < th["max"]
    assert th["inline"] >= 1_000_000, "the inline ceiling is back to grid-era values"


def test_collapsing_the_blocks_collapses_the_problem():
    """One block bucket is trivially small, whatever is ticked.

    This is now the only coarsening lever: a named block band aggregates 96 blocks
    into one row. The date axis is always one bucket per day.
    """
    e = budget.estimate(date_buckets=5, block_buckets=1,
                        datasets=["DC_PLANT", "ACTUAL_DEMAND"], ctx=CTX)
    assert e.cells == 175
    assert e.band == budget.BAND_INLINE


def test_a_refusal_carries_actionable_suggestions():
    """Only the memory cap refuses now, but it has to be useful when it does."""
    codes = ["DC_PLANT", "SG_PLANT", "ACP_DAM", "ACV_DAM", "ACTUAL_DEMAND",
             "DRAWAL", "FREQUENCY", "DSM_RATE"]
    e = budget.estimate(date_buckets=3650, block_buckets=96, datasets=codes, ctx=CTX)
    assert e.band == budget.BAND_REFUSED
    kinds = {s["kind"] for s in e.suggestions}
    assert "coarser_blocks" in kinds
    assert "shorter_range" in kinds
    assert "fewer_datasets" in kinds
    for s in e.suggestions:
        assert s["cells"] < e.cells
        assert s["message"]


def test_no_suggestion_offers_a_coarser_date_grouping():
    """(at the memory cap, where suggestions are still produced)"""
    """It would be advice that changes nothing.

    Group Date By stopped collapsing the date axis on 2026-09-20, so "switch to
    Week-wise" — which this list used to carry — leaves the cell count exactly where
    it was. A suggestion that does not shrink the request is worse than none: the
    user follows it and hits the same 413.
    """
    codes = ["DC_PLANT", "SG_PLANT", "ACP_DAM", "ACV_DAM", "ACTUAL_DEMAND",
             "DRAWAL", "FREQUENCY", "DSM_RATE"]
    e = budget.estimate(date_buckets=3650, block_buckets=96, datasets=codes, ctx=CTX)
    assert e.band == budget.BAND_REFUSED
    assert "coarser_dates" not in {s["kind"] for s in e.suggestions}


def test_a_comfortably_inline_request_needs_no_suggestions():
    """Advice on a request that is already instant is noise."""
    e = budget.estimate(date_buckets=1, block_buckets=96,
                        datasets=["ACTUAL_DEMAND"], ctx=CTX)
    assert e.band == budget.BAND_INLINE
    assert e.suggestions == []


def test_a_heavy_but_served_request_still_gets_advice():
    """`inline_capped` is served; the banner offers to narrow it anyway, and an
    `async_only` export is produced but slow enough to be worth shortening."""
    live = [c for c, d in registry.DATASETS.items() if d.availability == "live"]
    e = budget.estimate(date_buckets=365, block_buckets=96, datasets=live, ctx=CTX)
    assert e.band == budget.BAND_ASYNC_ONLY
    assert e.suggestions, "a 26-second export was offered no way to be quicker"


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
