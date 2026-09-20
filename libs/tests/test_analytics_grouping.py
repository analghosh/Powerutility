"""Date and block grouping expressions.

Grouping is pushed into Mongo's `$group`; nothing here buckets rows in Python. The
tests assert on the expression built, which is the house convention for pipeline
code (see `test_forecast_serve_repository.py`), and lock the two details that are
silently wrong otherwise: ISO week keys, and the IST timezone on `$dateToString`.
"""
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services.processing.app.v2.analytics import block_bands as bb  # noqa: E402
from services.processing.app.v2.analytics import grouping as g  # noqa: E402
from services.processing.app.v2.analytics import registry  # noqa: E402


# ---------------------------------------------------------------- date keys

def test_date_groupings_are_the_five_the_ui_offers():
    assert g.DATE_GROUPINGS == ("BLOCK_DATE_WISE", "HOURLY_WISE",
                                "WEEK_WISE", "MONTH_WISE", "YEAR_WISE")


@pytest.mark.parametrize("retired", ["BLOCK_WISE", "DATE_WISE", "block_wise"])
def test_the_retired_codes_map_forward(retired):
    """They produced identical files, so they became one pill.

    Stored export rows and saved preferences still hold them and are replayed
    straight back into the request model, so they have to keep working.
    """
    assert g.canonical(retired) == g.BLOCK_DATE_WISE


def test_an_unknown_grouping_is_refused():
    with pytest.raises(ValueError):
        g.canonical("SOMETHING_ELSE")


def test_the_day_key_is_what_every_dashboard_query_groups_on():
    assert g.day_key_expr() == {"$dateToString": {
        "format": "%Y-%m-%d", "date": "$scheduled_at", "timezone": g.TIMEZONE}}


def test_the_day_key_is_the_only_key_a_pipeline_groups_on():
    """`date_key_expr` is gone with the roll-ups (2026-09-20).

    It existed so a weekly or monthly dataset could bucket by its own period. Those
    four datasets were removed — they read the same stored fields as the daily
    Forecast and MAPE and differed only in period — so nothing aggregates over a
    period any more and one date key remains.
    """
    assert not hasattr(g, "date_key_expr")
    assert not hasattr(g, "DATE_FORMATS")
    assert g.day_key_expr()["$dateToString"]["format"] == g.DAY_FORMAT == "%Y-%m-%d"


def test_the_day_field_is_overridable():
    """The supply group dates on a different field."""
    expr = g.day_key_expr("$delivery_date")
    assert expr["$dateToString"]["date"] == "$delivery_date"


# ---------------------------------------------------------------- timezone

def test_timezone_defaults_to_ist():
    assert g.TIMEZONE == "Asia/Kolkata"


def test_the_day_key_carries_the_timezone():
    assert g.day_key_expr()["$dateToString"]["timezone"] == g.TIMEZONE


def test_a_block_at_2345_ist_belongs_to_the_ist_date_not_the_utc_one():
    """Block 96 of 1 Aug starts 23:45 IST = 18:15Z on 1 Aug — same date either way.

    Block 1 is the trap: 00:00 IST on 2 Aug is 18:30Z on 1 Aug, so a naive UTC
    `$dateToString` files the first four blocks of every day under the previous
    date. This asserts the shift the `timezone` key exists to undo.
    """
    block_1_of_2_aug = datetime(2025, 8, 1, 18, 30, tzinfo=timezone.utc)
    assert block_1_of_2_aug.strftime("%Y-%m-%d") == "2025-08-01"

    from zoneinfo import ZoneInfo
    ist = block_1_of_2_aug.astimezone(ZoneInfo(g.TIMEZONE))
    assert ist.strftime("%Y-%m-%d") == "2025-08-02"


# ---------------------------------------------------------------- block keys

def test_individual_band_keys_on_the_block_itself():
    assert g.block_key_expr(bb.DEFAULT_BANDS["INDIVIDUAL"]) == {"$toString": "$time_block"}


def test_named_band_switches_every_range_onto_the_band_code():
    band = bb.Band("EVENING_PEAK", "Evening Peak", ((67, 78), (83, 88)), True, None)
    expr = g.block_key_expr(band)
    assert expr == {
        "$switch": {
            "branches": [
                {"case": {"$and": [{"$gte": ["$time_block", 67]},
                                   {"$lte": ["$time_block", 78]}]},
                 "then": "EVENING_PEAK"},
                {"case": {"$and": [{"$gte": ["$time_block", 83]},
                                   {"$lte": ["$time_block", 88]}]},
                 "then": "EVENING_PEAK"},
            ],
            "default": None,
        }
    }


def test_a_block_outside_every_range_defaults_to_none_not_zero():
    band = bb.Band("DAY_PEAK", "Day Peak", ((40, 50),), True, None)
    assert g.block_key_expr(band)["$switch"]["default"] is None


def test_named_band_with_no_ranges_raises_rather_than_emitting_an_empty_switch():
    """The five named bands ship inactive with no ranges; $switch needs a branch."""
    with pytest.raises(ValueError) as exc:
        g.block_key_expr(bb.DEFAULT_BANDS["PEAK"])
    # The message must tell the user WHERE to define it, not just that it is missing.
    assert "analytics_band_peak" in str(exc.value)
    assert "Masters" in str(exc.value)


def test_a_named_band_with_parameter_ranges_collapses_them_onto_its_code():
    band = bb.band_with_ranges(bb.DEFAULT_BANDS["PEAK"], bb.parse_ranges("65-72, 81-88"))
    expr = g.block_key_expr(band)
    branches = expr["$switch"]["branches"]
    assert len(branches) == 2
    assert {b["then"] for b in branches} == {"PEAK"}


def test_custom_band_gives_each_supplied_range_its_own_bucket():
    expr = g.block_key_expr(bb.DEFAULT_BANDS["CUSTOM"], custom=[(1, 8), (9, 20)])
    branches = expr["$switch"]["branches"]
    assert [b["then"] for b in branches] == ["1-8", "9-20"]
    assert branches[0]["case"] == {"$and": [{"$gte": ["$time_block", 1]},
                                            {"$lte": ["$time_block", 8]}]}
    assert expr["$switch"]["default"] is None


def test_custom_band_without_ranges_raises():
    with pytest.raises(ValueError):
        g.block_key_expr(bb.DEFAULT_BANDS["CUSTOM"])


def test_custom_ranges_are_validated_against_the_96_block_day():
    with pytest.raises(ValueError):
        g.block_key_expr(bb.DEFAULT_BANDS["CUSTOM"], custom=[(0, 8)])
    with pytest.raises(ValueError):
        g.block_key_expr(bb.DEFAULT_BANDS["CUSTOM"], custom=[(20, 9)])


def test_block_bucket_count_matches_the_branches_built():
    """The budget is computed from bucket_count; it must agree with the pipeline."""
    custom = [(1, 8), (9, 20), (21, 96)]
    expr = g.block_key_expr(bb.DEFAULT_BANDS["CUSTOM"], custom=custom)
    assert len(expr["$switch"]["branches"]) == bb.bucket_count(
        bb.DEFAULT_BANDS["CUSTOM"], 1, 96, custom)


# ---------------------------------------------------------------- accumulators

@pytest.mark.parametrize("agg", ["avg", "sum", "max", "min", "last"])
def test_accumulator_wraps_the_value_expression(agg):
    assert g.accumulator(agg, "$acv") == {f"${agg}": "$acv"}


def test_unknown_aggregation_rule_raises():
    with pytest.raises(ValueError) as exc:
        g.accumulator("median", "$acv")
    assert "median" in str(exc.value)


def test_every_aggregation_rule_in_the_registry_is_supported():
    for dataset in registry.DATASETS.values():
        assert g.accumulator(dataset.agg, "$v") == {f"${dataset.agg}": "$v"}


# ----------------------------------------------------------------- the hour
# `scheduled_at` is a trading date stamped at midnight; the time of day lives in
# `time_block` (4 blocks per hour). Formatting the date field with %H therefore
# labels every row of a day with the same hour. The hour is a COLUMN now
# (2026-09-20), derived from the block, so the equivalence these tests pin is
# between the block number and the hour a reader sees beside it.

@pytest.mark.parametrize("block,hour", [
    (1, 0), (4, 0), (5, 1), (8, 1), (41, 10), (89, 22), (93, 23), (96, 23),
])
def test_block_to_hour_mapping(block, hour):
    assert g.hour_of_block(block) == hour


def test_the_hour_column_is_one_based_beside_the_blocks():
    """`hour_of_block` is 0-23 (the clock); the column reads 1-24 (beside block 1-96).

    A spreadsheet that starts blocks at 1 and hours at 0 invites an off-by-one in
    every formula written against it.
    """
    for block in (1, 21, 96):
        assert g.hour_label(block) == f"Hour {g.hour_of_block(block) + 1}"
    assert g.hour_label(1) == "Hour 1"
    assert g.hour_label(96) == "Hour 24"
