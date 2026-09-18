"""Supply adapter — latest revision per key.

`supply_serve` carries real revisions (`system_revision_number` 2144, 2145, 2146…),
and at the roll boundary the *same* `(scheduled_at, time_block, plant_id, dc_sg)`
key exists in both `supply_serve` and `supply_serve_historical` — verified in the
local UAT import on 2026-08-31. Both hazards land on the same stage: without
collapsing to one document per key after the union, `DECLARED_CAPACITY_TOTAL` and
`SG_TOTAL` (both `$sum`) double-count, and a superseded revision can win.

These tests assert on the pipeline built, not on stored state, matching
`test_analytics_adapter_base.py`. Task 18 proves the pipeline is valid MongoDB
against the real database.
"""
import sys
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services.processing.app.v2.analytics import block_bands as bb  # noqa: E402
from services.processing.app.v2.analytics import registry  # noqa: E402
from services.processing.app.v2.analytics.adapters import base  # noqa: E402
from services.processing.app.v2.analytics.adapters import supply as supply_mod  # noqa: E402


FILTERS = {
    "from_date": date(2026, 8, 29),
    "to_date": date(2026, 8, 31),
    "from_block": 1,
    "to_block": 96,
    "date_grouping": "DATE_WISE",
    "block_band": bb.BAND_INDIVIDUAL,
}
CTX = {"plant_count": 34}

DC_PLANT = registry.get("DC_PLANT")
SG_PLANT = registry.get("SG_PLANT")
DC_TOTAL = registry.get("DECLARED_CAPACITY_TOTAL")
SG_TOTAL = registry.get("SG_TOTAL")


@pytest.fixture
def adapter():
    return supply_mod.SupplyAdapter()


def _build(adapter, datasets, **overrides):
    filters = dict(FILTERS)
    filters.update(overrides)
    return adapter.build_pipeline(datasets=datasets, filters=filters, ctx=CTX)


def _indexes(pipeline, name):
    return [i for i, s in enumerate(pipeline) if name in s]


def _value_fields(group_stage):
    g = group_stage["$group"]
    return [k for k in g
            if k != "_id" and not k.startswith(base.NULL_GUARD_PREFIX)]


# --------------------------------------------------------------- identity

def test_adapter_declares_the_supply_source_group(adapter):
    assert adapter.group == "supply"
    assert adapter.serve_collection == "supply_serve"
    assert adapter.historical_collection == "supply_serve_historical"
    assert adapter.entity == "plant_id"
    assert adapter.entity_field() == "plant_id"


def test_adapter_is_registered_under_its_group():
    assert isinstance(base.get_adapter("supply"), supply_mod.SupplyAdapter)


def test_registering_a_second_supply_adapter_raises():
    with pytest.raises(ValueError, match="already registered"):
        base.register(supply_mod.SupplyAdapter())


# ------------------------------------------------- serve ∪ historical

def test_pipeline_starts_with_match_and_unions_the_historical_twin(adapter):
    pipeline = _build(adapter, [DC_PLANT])
    assert "$match" in pipeline[0]
    unions = _indexes(pipeline, "$unionWith")
    assert len(unions) == 1
    union = pipeline[unions[0]]["$unionWith"]
    assert union["coll"] == "supply_serve_historical"
    assert union["pipeline"] == [{"$match": pipeline[0]["$match"]}]


def test_match_carries_the_date_range_and_the_block_range(adapter):
    match = _build(adapter, [DC_PLANT])[0]["$match"]
    assert match["scheduled_at"]["$gte"].date() == date(2026, 8, 29)
    # to_date is inclusive, so the exclusive bound is the next midnight
    assert match["scheduled_at"]["$lt"].date() == date(2026, 9, 1)
    assert match["time_block"] == {"$gte": 1, "$lte": 96}


# ----------------------------------------------- the revision collapse

def test_collapse_sorts_by_revision_and_takes_the_last_document(adapter):
    pipeline = _build(adapter, [DC_PLANT])
    sorts = _indexes(pipeline, "$sort")
    assert len(sorts) == 1, "one sort, and it is the revision sort"
    # The archival stamp is the tiebreak: the rolling window leaves the same key
    # at the same revision in both collections, and the live copy must win.
    assert pipeline[sorts[0]]["$sort"] == {"system_revision_number": 1,
                                           "archived_at": -1}

    collapse = pipeline[sorts[0] + 1]["$group"]
    assert collapse["_id"] == {
        "d": "$scheduled_at",
        "b": "$time_block",
        "p": "$plant_id",
        "t": "$dc_sg",
    }
    assert collapse["doc"] == {"$last": "$$ROOT"}
    assert pipeline[sorts[0] + 2] == {"$replaceRoot": {"newRoot": "$doc"}}


def test_collapse_runs_after_the_union_so_boundary_duplicates_collapse(adapter):
    # The same (date, block, plant, dc_sg) key exists in BOTH collections at the
    # roll boundary. Collapsing before the union would leave both copies alive.
    pipeline = _build(adapter, [DC_TOTAL])
    assert _indexes(pipeline, "$sort")[0] > _indexes(pipeline, "$unionWith")[0]


def test_nothing_filters_between_the_union_and_the_revision_collapse(adapter):
    """No cut-off stage. A filter here could only ever delete a row.

    An 01:00 capture cut-off lived here briefly on 2026-09-18. It was removed: the
    collapse already returns the last revision received, the ingest window shuts at
    midnight, and a later revision reaches the historical collection with
    `system_revision_number = 1` and loses the collapse anyway. See the module
    docstring before reintroducing anything here.
    """
    pipeline = _build(adapter, [DC_TOTAL])
    union = _indexes(pipeline, "$unionWith")[0]
    collapse = _indexes(pipeline, "$sort")[0]
    assert collapse == union + 1, "a stage was inserted between the union and the collapse"


def test_collapse_runs_before_the_analytics_group(adapter):
    pipeline = _build(adapter, [DC_PLANT])
    groups = _indexes(pipeline, "$group")
    assert len(groups) == 2, "exactly the collapse group and the analytics group"
    collapse, analytics = groups
    assert collapse < analytics
    assert "doc" in pipeline[collapse]["$group"]
    assert _value_fields(pipeline[analytics]) == ["DC_PLANT"]


def test_plant_filter_is_applied_in_the_initial_match_not_after_grouping(adapter):
    pipeline = _build(adapter, [DC_PLANT], plants=["NNTPP", "KKNP"])
    match = pipeline[0]["$match"]
    assert match["plant_id"] == {"$in": ["NNTPP", "KKNP"]}
    # it rides along into the union, so both sides are narrowed before the sort
    union = pipeline[_indexes(pipeline, "$unionWith")[0]]["$unionWith"]
    assert union["pipeline"][0]["$match"]["plant_id"] == {"$in": ["NNTPP", "KKNP"]}
    # nothing filters after the collapse — that is what makes the collapse cheap
    collapse = _indexes(pipeline, "$group")[0]
    assert not _indexes(pipeline[collapse:], "$match")


# ------------------------------------------------ one pipeline, many datasets

def test_dc_and_sg_ticked_together_produce_one_pipeline_with_two_accumulators(adapter):
    pipeline = _build(adapter, [DC_PLANT, SG_PLANT])
    analytics = pipeline[_indexes(pipeline, "$group")[-1]]
    assert sorted(_value_fields(analytics)) == ["DC_PLANT", "SG_PLANT"]
    assert len(_indexes(pipeline, "$unionWith")) == 1
    assert len(_indexes(pipeline, "$sort")) == 1


def test_dc_sg_discriminator_lives_inside_each_accumulator(adapter):
    pipeline = _build(adapter, [DC_PLANT, SG_PLANT])
    analytics = pipeline[_indexes(pipeline, "$group")[-1]]["$group"]
    assert analytics["DC_PLANT"] == {
        "$avg": {"$cond": [{"$and": [{"$eq": ["$dc_sg", "DC"]}]}, "$volume", None]}}
    assert analytics["SG_PLANT"] == {
        "$avg": {"$cond": [{"$and": [{"$eq": ["$dc_sg", "SG"]}]}, "$volume", None]}}


def test_disagreeing_discriminators_are_not_hoisted_into_the_shared_match(adapter):
    # dc_sg="DC" in the shared $match would delete every SG_PLANT row.
    match = _build(adapter, [DC_PLANT, SG_PLANT])[0]["$match"]
    assert "dc_sg" not in match


def test_a_unanimous_discriminator_is_hoisted_as_a_free_prefilter(adapter):
    pipeline = _build(adapter, [DC_PLANT, DC_TOTAL])
    assert pipeline[0]["$match"]["dc_sg"] == "DC"
    # and the $cond stays, so hoisting can never change a result
    analytics = pipeline[_indexes(pipeline, "$group")[-1]]["$group"]
    assert "$cond" in str(analytics["DC_PLANT"])


# ----------------------------------------------------- entity vs scalar

def test_plant_wise_datasets_group_by_plant(adapter):
    pipeline = _build(adapter, [DC_PLANT])
    _id = pipeline[_indexes(pipeline, "$group")[-1]]["$group"]["_id"]
    assert _id[base.ID_ENTITY] == "$plant_id"


def test_total_datasets_omit_the_entity_so_plants_sum_into_one_series(adapter):
    for ds in (DC_TOTAL, SG_TOTAL):
        pipeline = _build(adapter, [ds])
        _id = pipeline[_indexes(pipeline, "$group")[-1]]["$group"]["_id"]
        assert base.ID_ENTITY not in _id, f"{ds.code} must not key on plant_id"


def test_summed_totals_carry_a_null_guard(adapter):
    pipeline = _build(adapter, [DC_TOTAL])
    g = pipeline[_indexes(pipeline, "$group")[-1]]["$group"]
    assert base.NULL_GUARD_PREFIX + "DECLARED_CAPACITY_TOTAL" in g


# ----------------------------------------------------------- rows_from

def test_rows_from_maps_plant_wise_documents(adapter):
    docs = [{"_id": {"d": "2026-08-31", "b": "69", "e": "KKNP"}, "DC_PLANT": 32.61}]
    assert adapter.rows_from(docs, [DC_PLANT]) == [
        {"dataset": "DC_PLANT", "date_key": "2026-08-31", "block_key": "69",
         "entity": "KKNP", "value": 32.61},
    ]


def test_rows_from_never_coerces_a_missing_value_to_zero(adapter):
    docs = [
        {"_id": {"d": "2026-08-31", "b": "69", "e": "KKNP"}, "DC_PLANT": None},
        {"_id": {"d": "2026-08-31", "b": "70", "e": "KKNP"}, "DC_PLANT": 0.0},
    ]
    values = [r["value"] for r in adapter.rows_from(docs, [DC_PLANT])]
    assert values == [None, 0.0]


def test_a_total_that_summed_nothing_is_none_not_zero(adapter):
    docs = [
        {"_id": {"d": "2026-08-31", "b": "69"},
         "DECLARED_CAPACITY_TOTAL": 0, "_n_DECLARED_CAPACITY_TOTAL": 0},
        {"_id": {"d": "2026-08-31", "b": "70"},
         "DECLARED_CAPACITY_TOTAL": 0.0, "_n_DECLARED_CAPACITY_TOTAL": 4},
    ]
    values = [r["value"] for r in adapter.rows_from(docs, [DC_TOTAL])]
    assert values == [None, 0.0], "an all-null group is None; a real zero survives"


def test_mixing_plant_wise_and_total_folds_the_total_back_to_one_series(adapter):
    # A mixed tick forces plant_id into the shared _id, which would otherwise turn
    # Declared Capacity into 34 identical per-plant series.
    docs = [
        {"_id": {"d": "2026-08-31", "b": "69", "e": "KKNP"},
         "DC_PLANT": 32.61, "DECLARED_CAPACITY_TOTAL": 32.61,
         "_n_DECLARED_CAPACITY_TOTAL": 1},
        {"_id": {"d": "2026-08-31", "b": "69", "e": "NNTPP"},
         "DC_PLANT": 100.0, "DECLARED_CAPACITY_TOTAL": 100.0,
         "_n_DECLARED_CAPACITY_TOTAL": 1},
    ]
    rows = adapter.rows_from(docs, [DC_PLANT, DC_TOTAL])
    plant_rows = [r for r in rows if r["dataset"] == "DC_PLANT"]
    total_rows = [r for r in rows if r["dataset"] == "DECLARED_CAPACITY_TOTAL"]
    assert [(r["entity"], r["value"]) for r in plant_rows] == [
        ("KKNP", 32.61), ("NNTPP", 100.0)]
    assert total_rows == [
        {"dataset": "DECLARED_CAPACITY_TOTAL", "date_key": "2026-08-31",
         "block_key": "69", "entity": None, "value": 132.61},
    ]


def test_a_folded_total_with_no_contributing_plant_stays_none(adapter):
    docs = [
        {"_id": {"d": "2026-08-31", "b": "69", "e": "KKNP"},
         "DC_PLANT": None, "DECLARED_CAPACITY_TOTAL": 0,
         "_n_DECLARED_CAPACITY_TOTAL": 0},
        {"_id": {"d": "2026-08-31", "b": "69", "e": "NNTPP"},
         "DC_PLANT": None, "DECLARED_CAPACITY_TOTAL": 0,
         "_n_DECLARED_CAPACITY_TOTAL": 0},
    ]
    rows = adapter.rows_from(docs, [DC_PLANT, DC_TOTAL])
    total = [r for r in rows if r["dataset"] == "DECLARED_CAPACITY_TOTAL"]
    assert [r["value"] for r in total] == [None]


def test_a_block_outside_every_band_is_dropped_rather_than_bucketed(adapter):
    docs = [{"_id": {"d": "2026-08-31", "b": None, "e": "KKNP"}, "DC_PLANT": 1.0}]
    assert adapter.rows_from(docs, [DC_PLANT]) == []


# ---------------------------------------------------------- validation

def test_no_datasets_raises(adapter):
    with pytest.raises(ValueError, match="No datasets"):
        _build(adapter, [])


def test_a_derived_dataset_never_reaches_a_pipeline(adapter):
    derived = replace(DC_PLANT, code="FAKE_DERIVED", kind="derived")
    with pytest.raises(ValueError, match="derived"):
        _build(adapter, [derived])


def test_an_unfoldable_scalar_mixed_with_a_plant_series_is_refused(adapter):
    # No such dataset exists today; the guard exists so adding one cannot silently
    # produce an average-of-averages.
    unfoldable = replace(DC_TOTAL, code="FAKE_AVG_TOTAL", agg="avg", series="scalar")
    with pytest.raises(ValueError, match="FAKE_AVG_TOTAL"):
        _build(adapter, [DC_PLANT, unfoldable])
