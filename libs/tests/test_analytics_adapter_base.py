"""Source group adapter base.

Every concrete adapter subclasses `SourceGroupAdapter`, so the shared machinery has
to be right once: the serve/historical `$unionWith`, the shape of the initial
`$match`, and the rule that a dataset's own discriminator goes *inside* its
accumulator rather than into the shared `$match`.

These tests assert on the pipeline built, not on stored state — the house convention
for pipeline code (see `test_forecast_serve_repository.py`). Task 18 proves the
pipelines are valid MongoDB against the real database.
"""
import sys
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from typing import List

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services.processing.app.v2.analytics import block_bands as bb  # noqa: E402
from services.processing.app.v2.analytics import registry  # noqa: E402
from services.processing.app.v2.analytics.adapters import base  # noqa: E402


FILTERS = {
    "from_date": date(2026, 4, 1),
    "to_date": date(2026, 4, 3),
    "from_block": 1,
    "to_block": 96,
    "date_grouping": "DATE_WISE",
    "block_band": bb.BAND_INDIVIDUAL,
}
CTX = {"plant_count": 34, "exchange_count": 3}


class _Demandish(base.SourceGroupAdapter):
    """Stand-in for the simple shape — one document per (date, block)."""
    group = "demandish"
    serve_collection = "demand_serve"
    historical_collection = "demand_serve_historical"
    dedupe_key = ("scheduled_at", "time_block", "type")

    def build_pipeline(self, *, datasets, filters, ctx=None) -> List[dict]:
        return self._default_pipeline(datasets=datasets, filters=filters, ctx=ctx)


class _Plantish(base.SourceGroupAdapter):
    """Stand-in for a per-entity group with no historical twin (no live group is
    shaped like this since the DAM-schedule group went; the base path stays)."""
    group = "plantish"
    serve_collection = "plantish_serve"
    historical_collection = None
    entity = "plant_id"

    def build_pipeline(self, *, datasets, filters, ctx=None) -> List[dict]:
        return self._default_pipeline(datasets=datasets, filters=filters, ctx=ctx)


def _stage(pipeline, name):
    return [s for s in pipeline if name in s]


def _group_of(pipeline):
    """The analytics `$group` — always the last one.

    A group with a historical twin also carries an earlier `$group` that drops the
    copy the archival window leaves in both collections; that one is asserted on
    its own below, not here.
    """
    groups = _stage(pipeline, "$group")
    assert groups, "pipeline has no $group"
    return groups[-1]["$group"]


def _value_fields(group):
    """Accumulator fields that carry a dataset's value, excluding null guards."""
    return [k for k in group
            if k != "_id" and not k.startswith(base.NULL_GUARD_PREFIX)]


# ------------------------------------------------------------- union_stage

def test_union_stage_names_the_historical_collection_and_reuses_the_match():
    match = {"scheduled_at": {"$gte": 1, "$lt": 2}}
    stage = base.union_stage("demand_serve_historical", match)
    assert stage == {
        "$unionWith": {
            "coll": "demand_serve_historical",
            "pipeline": [{"$match": match}],
        }
    }


def test_pipeline_unions_serve_with_historical_exactly_once():
    p = _Demandish().build_pipeline(
        datasets=[registry.get("ACTUAL_DEMAND")], filters=FILTERS, ctx=CTX)
    assert "$match" in p[0], "pipeline must open with the $match so it can use an index"
    unions = _stage(p, "$unionWith")
    assert len(unions) == 1
    assert unions[0]["$unionWith"]["coll"] == "demand_serve_historical"
    # the union carries the same filter, or the historical half is unbounded
    assert unions[0]["$unionWith"]["pipeline"][0]["$match"] == p[0]["$match"]
    assert len(_stage(p, "$group")) == 2, "the dedupe $group plus the analytics one"


def test_union_comes_before_the_group_so_both_halves_are_aggregated():
    p = _Demandish().build_pipeline(
        datasets=[registry.get("ACTUAL_DEMAND")], filters=FILTERS, ctx=CTX)
    names = [next(iter(s)) for s in p]
    assert names.index("$unionWith") < names.index("$group")


def test_the_union_is_deduplicated_on_the_collections_natural_key():
    # The archival copies a day into the historical twin and only deletes it from
    # serve some days later, so for the length of the rolling window the union
    # returns the same document twice and every $sum dataset reads double.
    p = _Demandish().build_pipeline(
        datasets=[registry.get("OD_UD_AMOUNT")], filters=FILTERS, ctx=CTX)
    names = [next(iter(s)) for s in p]
    assert names.index("$unionWith") < names.index("$sort") < names.index("$group")
    dedupe = _stage(p, "$group")[0]["$group"]
    assert dedupe["_id"] == {"scheduled_at": "$scheduled_at",
                             "time_block": "$time_block", "type": "$type"}
    assert dedupe[base.DEDUPED_DOC] == {"$last": "$$ROOT"}
    assert _stage(p, "$replaceRoot")[0] == {
        "$replaceRoot": {"newRoot": "$" + base.DEDUPED_DOC}}


def test_the_dedupe_sort_prefers_the_live_copy_over_the_archived_one():
    # Descending on the archival stamp puts the historical copy first and the
    # serve copy last, which is where $last finds it. If the two ever differ,
    # serve is the newer of the pair.
    p = _Demandish().build_pipeline(
        datasets=[registry.get("ACTUAL_DEMAND")], filters=FILTERS, ctx=CTX)
    assert _stage(p, "$sort")[0] == {"$sort": {base.ARCHIVED_AT_FIELD: -1}}


def test_an_adapter_that_unions_without_a_dedupe_key_is_refused():
    class _Careless(base.SourceGroupAdapter):
        group = "careless"
        serve_collection = "demand_serve"
        historical_collection = "demand_serve_historical"

        def build_pipeline(self, *, datasets, filters, ctx=None):
            return self._default_pipeline(datasets=datasets, filters=filters, ctx=ctx)

    with pytest.raises(ValueError, match="dedupe_key"):
        _Careless().build_pipeline(
            datasets=[registry.get("ACTUAL_DEMAND")], filters=FILTERS, ctx=CTX)


def test_group_with_no_historical_twin_emits_no_union():
    # A group without a _historical collection must not union one; that would 500.
    p = _Plantish().build_pipeline(
        datasets=[registry.get("SG_PLANT")], filters=FILTERS, ctx=CTX)
    assert _stage(p, "$unionWith") == []


# ------------------------------------------------------------------ $match

def test_match_carries_both_the_date_range_and_the_block_range():
    p = _Demandish().build_pipeline(
        datasets=[registry.get("ACTUAL_DEMAND")], filters=FILTERS, ctx=CTX)
    m = p[0]["$match"]
    assert m["scheduled_at"] == {"$gte": datetime(2026, 4, 1),
                                 "$lt": datetime(2026, 4, 4)}
    assert m["time_block"] == {"$gte": 1, "$lte": 96}


def test_date_upper_bound_is_exclusive_of_the_day_after_to_date():
    f = dict(FILTERS, from_date=date(2026, 4, 1), to_date=date(2026, 4, 1))
    m = _Demandish()._base_match(f, [registry.get("ACTUAL_DEMAND")])
    assert m["scheduled_at"]["$lt"] == datetime(2026, 4, 2), \
        "to_date must be inclusive, so the exclusive bound is the next midnight"


def test_block_range_is_narrowed_when_the_user_narrows_it():
    f = dict(FILTERS, from_block=33, to_block=40)
    m = _Demandish()._base_match(f, [registry.get("ACTUAL_DEMAND")])
    assert m["time_block"] == {"$gte": 33, "$lte": 40}


def test_an_out_of_range_block_is_rejected():
    f = dict(FILTERS, from_block=0)
    with pytest.raises(ValueError):
        _Demandish()._base_match(f, [registry.get("ACTUAL_DEMAND")])


def test_a_missing_date_is_rejected_rather_than_scanning_everything():
    f = {k: v for k, v in FILTERS.items() if k != "from_date"}
    with pytest.raises(ValueError):
        _Demandish()._base_match(f, [registry.get("ACTUAL_DEMAND")])


# --------------------------------------------- discriminators (the subtle bug)

def test_two_datasets_with_different_discriminators_hoist_nothing():
    # ACTUAL_DEMAND has no discriminator; MAPE_DAF has type=DAF. Hoisting DAF into
    # the shared $match would delete every ACTUAL_DEMAND row.
    ds = [registry.get("ACTUAL_DEMAND"), registry.get("MAPE_DAF")]
    m = _Demandish()._base_match(FILTERS, ds)
    assert "type" not in m


def test_conflicting_discriminators_hoist_nothing():
    ds = [registry.get("FORECAST_DAF"), registry.get("FORECAST_IDF")]
    m = _Demandish()._base_match(FILTERS, ds)
    assert "type" not in m


def test_a_discriminator_every_dataset_shares_is_hoisted_into_the_match():
    ds = [registry.get("FORECAST_DAF"), registry.get("MAPE_DAF")]
    m = _Demandish()._base_match(FILTERS, ds)
    assert m["type"] == "DAF", \
        "a discriminator all datasets share is a free prefilter"


def test_each_datasets_discriminator_is_guarded_inside_its_own_accumulator():
    ds = [registry.get("ACTUAL_DEMAND"), registry.get("MAPE_DAF")]
    g = _group_of(_Demandish().build_pipeline(datasets=ds, filters=FILTERS, ctx=CTX))
    assert _value_fields(g) == ["ACTUAL_DEMAND", "MAPE_DAF"]
    assert g["ACTUAL_DEMAND"] == {"$avg": "$actual"}
    assert g["MAPE_DAF"] == {
        "$avg": {"$cond": [{"$and": [{"$eq": ["$type", "DAF"]}]}, "$mape", None]}
    }


def test_the_guard_falls_back_to_none_never_to_zero():
    g = _group_of(_Demandish().build_pipeline(
        datasets=[registry.get("MAPE_DAF")], filters=FILTERS, ctx=CTX))
    cond = g["MAPE_DAF"]["$avg"]["$cond"]
    assert cond[2] is None, "a non-matching document contributes nothing, not zero"


# ------------------------------------------------------------------ $group

def test_group_id_keys_on_the_date_and_block_expressions():
    p = _Demandish().build_pipeline(
        datasets=[registry.get("ACTUAL_DEMAND")], filters=FILTERS, ctx=CTX)
    _id = _group_of(p)["_id"]
    assert _id[base.ID_DATE] == {"$dateToString": {
        "format": "%Y-%m-%d", "date": "$scheduled_at",
        "timezone": base.grouping.TIMEZONE}}
    assert _id[base.ID_BLOCK] == {"$toString": "$time_block"}


@pytest.mark.parametrize("grouping", ["BLOCK_DATE_WISE", "WEEK_WISE", "MONTH_WISE",
                                      "YEAR_WISE", "HOURLY_WISE"])
def test_the_users_grouping_never_reaches_the_group_id(grouping):
    """Group Date By is a column in the file, not a bucket in Mongo (2026-09-20).

    Every dashboard query groups on the trading day. Before this, Block-wise and
    Date-wise both formatted `%Y-%m-%d` and so produced identical files, while a
    ticked weekly dataset silently rewrote the grouping for the whole request.
    """
    f = dict(FILTERS, date_grouping=grouping)
    p = _Demandish().build_pipeline(
        datasets=[registry.get("ACTUAL_DEMAND")], filters=f, ctx=CTX)
    assert _group_of(p)["_id"][base.ID_DATE]["$dateToString"]["format"] == "%Y-%m-%d"


def test_a_scalar_only_selection_leaves_the_entity_out_of_the_group_id():
    # DECLARED_CAPACITY_TOTAL sums plants into one series, so keying on plant_id
    # would silently turn one series into 34.
    p = _Plantish().build_pipeline(
        datasets=[registry.get("DECLARED_CAPACITY_TOTAL")],
        filters=FILTERS, ctx=CTX)
    assert base.ID_ENTITY not in _group_of(p)["_id"]


def test_a_per_plant_dataset_puts_the_entity_into_the_group_id():
    p = _Plantish().build_pipeline(
        datasets=[registry.get("SG_PLANT")], filters=FILTERS, ctx=CTX)
    assert _group_of(p)["_id"][base.ID_ENTITY] == "$plant_id"


def test_the_plant_filter_lands_in_the_initial_match_not_after_grouping():
    f = dict(FILTERS, plants=["NNTPP", "ABCREPL_BHDL2"])
    p = _Plantish().build_pipeline(
        datasets=[registry.get("SG_PLANT")], filters=f, ctx=CTX)
    assert p[0]["$match"]["plant_id"] == {"$in": ["NNTPP", "ABCREPL_BHDL2"]}


def test_an_adapter_without_an_entity_ignores_the_plant_filter():
    f = dict(FILTERS, plants=["NNTPP"])
    m = _Demandish()._base_match(f, [registry.get("ACTUAL_DEMAND")])
    assert "plant_id" not in m


def test_each_dataset_gets_its_own_accumulator_in_one_pipeline():
    ds = [registry.get("ACTUAL_DEMAND"), registry.get("ROSTERING"),
          registry.get("FREQUENCY")]
    g = _group_of(_Demandish().build_pipeline(datasets=ds, filters=FILTERS, ctx=CTX))
    assert _value_fields(g) == ["ACTUAL_DEMAND", "ROSTERING", "FREQUENCY"]
    assert g["FREQUENCY"] == {"$avg": "$frequency"}


def test_the_aggregation_rule_comes_from_the_dataset_not_a_default():
    g = _group_of(_Demandish().build_pipeline(
        datasets=[registry.get("PEAK_DEMAND")], filters=FILTERS, ctx=CTX))
    assert g["PEAK_DEMAND"] == {"$max": "$peak_demand"}


def test_a_summed_dataset_carries_a_null_guard_counter():
    # $sum over an all-null group returns 0 in Mongo, which would render an
    # uncleared HPDAM block as a traded volume of zero. The counter lets
    # rows_from tell "summed nothing" from "summed to zero".
    g = _group_of(_Demandish().build_pipeline(
        datasets=[registry.get("TOTAL_ENERGY_MU")], filters=FILTERS, ctx=CTX))
    # The test is on $type, not on $ne: a *missing* field is not equal to null in
    # an aggregation expression, so a comparison guard counted documents that
    # contributed nothing and an absent DSM amount rendered as a real zero.
    assert g[base.NULL_GUARD_PREFIX + "TOTAL_ENERGY_MU"] == {
        "$sum": {"$cond": [
            {"$in": [{"$type": "$total_energy_mu"}, ["missing", "null"]]}, 0, 1]}}


def test_an_averaged_dataset_needs_no_counter():
    # $avg already returns null for an all-null group; an extra accumulator per
    # dataset would be paid on every document for nothing.
    g = _group_of(_Demandish().build_pipeline(
        datasets=[registry.get("ACTUAL_DEMAND")], filters=FILTERS, ctx=CTX))
    assert base.NULL_GUARD_PREFIX + "ACTUAL_DEMAND" not in g


# ---------------------------------------------------------------- rows_from

def _doc(date_key="2026-04-01", block_key="17", entity=None, **values):
    _id = {base.ID_DATE: date_key, base.ID_BLOCK: block_key}
    if entity is not None:
        _id[base.ID_ENTITY] = entity
    return dict(values, _id=_id)


def test_rows_from_maps_a_grouped_document_to_canonical_rows():
    rows = _Demandish().rows_from(
        [_doc(ACTUAL_DEMAND=2850.5)], [registry.get("ACTUAL_DEMAND")])
    assert rows == [{"dataset": "ACTUAL_DEMAND", "date_key": "2026-04-01",
                     "block_key": "17", "entity": None, "value": 2850.5}]


def test_rows_from_preserves_a_null_value_and_never_coerces_it_to_zero():
    rows = _Demandish().rows_from(
        [_doc(ACTUAL_DEMAND=None)], [registry.get("ACTUAL_DEMAND")])
    assert rows[0]["value"] is None
    assert rows[0]["value"] != 0


def test_a_real_zero_survives_as_zero():
    rows = _Demandish().rows_from(
        [_doc(ACTUAL_DEMAND=0.0)], [registry.get("ACTUAL_DEMAND")])
    assert rows[0]["value"] == 0.0


def test_a_summed_group_that_matched_nothing_reads_as_none_not_zero():
    d = registry.get("OD_UD_AMOUNT")
    doc = _doc(OD_UD_AMOUNT=0, **{base.NULL_GUARD_PREFIX + "OD_UD_AMOUNT": 0})
    assert _Demandish().rows_from([doc], [d])[0]["value"] is None


def test_a_summed_group_that_really_totals_zero_stays_zero():
    d = registry.get("OD_UD_AMOUNT")
    doc = _doc(OD_UD_AMOUNT=0, **{base.NULL_GUARD_PREFIX + "OD_UD_AMOUNT": 4})
    assert _Demandish().rows_from([doc], [d])[0]["value"] == 0


def test_rows_from_carries_the_entity_through():
    rows = _Plantish().rows_from(
        [_doc(entity="NNTPP", SG_PLANT=120.0)],
        [registry.get("SG_PLANT")])
    assert rows[0]["entity"] == "NNTPP"


def test_rows_from_fans_one_document_out_to_one_row_per_dataset():
    ds = [registry.get("ACTUAL_DEMAND"), registry.get("ROSTERING")]
    rows = _Demandish().rows_from([_doc(ACTUAL_DEMAND=1.0, ROSTERING=2.0)], ds)
    assert [r["dataset"] for r in rows] == ["ACTUAL_DEMAND", "ROSTERING"]
    assert [r["value"] for r in rows] == [1.0, 2.0]


def test_a_block_outside_every_band_bucket_is_dropped_rather_than_mislabelled():
    # block_key_expr defaults to None for a block in no range; such a row cannot
    # be placed on the block axis.
    rows = _Demandish().rows_from(
        [_doc(block_key=None, ACTUAL_DEMAND=1.0)], [registry.get("ACTUAL_DEMAND")])
    assert rows == []


def test_rows_from_tolerates_an_empty_result():
    assert _Demandish().rows_from([], [registry.get("ACTUAL_DEMAND")]) == []


# ----------------------------------------------------------------- registry

def test_register_puts_the_adapter_in_the_lookup_by_group():
    class _Once(base.SourceGroupAdapter):
        group = "registrable_once"
        serve_collection = "x_serve"

        def build_pipeline(self, *, datasets, filters, ctx=None):
            return []

    try:
        base.register(_Once())
        assert base.ADAPTERS["registrable_once"].group == "registrable_once"
        assert base.get_adapter("registrable_once") is base.ADAPTERS["registrable_once"]
    finally:
        base.unregister("registrable_once")


def test_registering_two_adapters_for_the_same_group_raises():
    class _A(base.SourceGroupAdapter):
        group = "collide"
        serve_collection = "a_serve"

        def build_pipeline(self, *, datasets, filters, ctx=None):
            return []

    class _B(_A):
        pass

    try:
        base.register(_A())
        with pytest.raises(ValueError):
            base.register(_B())
    finally:
        base.unregister("collide")


def test_get_adapter_raises_for_an_unknown_group():
    with pytest.raises(KeyError):
        base.get_adapter("no_such_group")


def test_the_base_class_cannot_be_instantiated():
    with pytest.raises(TypeError):
        base.SourceGroupAdapter()


def test_entity_field_defaults_to_none():
    assert _Demandish().entity_field() is None
    assert _Plantish().entity_field() == "plant_id"


def test_a_dataset_with_no_value_field_is_rejected_before_it_reaches_mongo():
    # `absent` datasets carry value=None; building an accumulator on them would
    # emit {"$avg": None} and silently return nulls for every block.
    absent = registry.get("TAM_TRADED")
    assert absent.value is None
    with pytest.raises(ValueError):
        _Demandish().build_pipeline(datasets=[absent], filters=FILTERS, ctx=CTX)


def test_building_a_pipeline_with_no_datasets_is_rejected():
    with pytest.raises(ValueError):
        _Demandish().build_pipeline(datasets=[], filters=FILTERS, ctx=CTX)


def test_a_custom_band_reaches_the_block_key():
    f = dict(FILTERS, block_band=bb.BAND_CUSTOM, custom_blocks=[[1, 8], [9, 16]])
    p = _Demandish().build_pipeline(
        datasets=[registry.get("ACTUAL_DEMAND")], filters=f, ctx=CTX)
    branches = _group_of(p)["_id"][base.ID_BLOCK]["$switch"]["branches"]
    assert [b["then"] for b in branches] == ["1-8", "9-16"]


def test_the_date_field_is_overridable_for_a_group_that_names_it_differently():
    class _Odd(_Demandish):
        group = "oddish"
        date_field = "delivery_date"

    m = _Odd()._base_match(FILTERS, [registry.get("ACTUAL_DEMAND")])
    assert "delivery_date" in m and "scheduled_at" not in m


def test_a_datetime_bound_is_accepted_as_well_as_a_date():
    f = dict(FILTERS, from_date=datetime(2026, 4, 1, 9, 30))
    m = _Demandish()._base_match(f, [registry.get("ACTUAL_DEMAND")])
    assert m["scheduled_at"]["$gte"] == datetime(2026, 4, 1)


def test_a_derived_dataset_never_reaches_a_pipeline():
    derived = registry.get("OD_UD_QUANTUM")
    assert derived.kind == "derived"
    with pytest.raises(ValueError):
        _Demandish().build_pipeline(datasets=[derived], filters=FILTERS, ctx=CTX)


def test_value_expr_leaves_an_undiscriminated_dataset_alone():
    assert base.value_expr(registry.get("ACTUAL_DEMAND")) == "$actual"


def test_value_expr_handles_a_multi_key_discriminator():
    d = replace(registry.get("ACTUAL_DEMAND"),
                match={"type": "DAF", "market_type": "DAM"})
    assert base.value_expr(d) == {"$cond": [
        {"$and": [{"$eq": ["$type", "DAF"]},
                  {"$eq": ["$market_type", "DAM"]}]}, "$actual", None]}


# --------------------------------------------------------------- sentinel_null

def test_a_sentinel_zero_becomes_a_gap_before_aggregation():
    """demand_processor.py writes forecasted_frequency=0.0 when the feed carried
    none. 0 Hz is not a forecast; it must aggregate as None, never drag the average."""
    d = registry.get("FREQUENCY_FORECAST")
    assert d.sentinel_null == 0.0
    expr = base.value_expr(d)
    inner = expr["$cond"][1]                      # the value branch of the type match
    assert inner == {"$cond": [{"$eq": ["$forecasted_frequency", 0.0]}, None,
                               "$forecasted_frequency"]}


def test_datasets_without_a_sentinel_keep_genuine_zeros():
    """A real 0 MW actual is a value, not a gap — only a declared sentinel is."""
    d = registry.get("ACTUAL_DEMAND")
    assert d.sentinel_null is None
    assert base.value_expr(d) == "$actual"


# ------------------------------------------------------- band resolution


def test_a_named_band_resolves_from_the_stamped_ranges_not_from_mongo():
    """The worker replays a job row's filters; the ranges ride in that row, so the
    adapters never need the parameters collection."""
    band = base._resolve_band({"block_band": "PEAK", "block_band_ranges": [[65, 72], [81, 88]]})
    assert band.code == "PEAK" and band.active
    assert band.ranges == ((65, 72), (81, 88))


def test_a_named_band_with_no_stamped_ranges_stays_inactive():
    band = base._resolve_band({"block_band": "PEAK"})
    assert band.code == "PEAK" and not band.active and band.ranges == ()


def test_stamped_ranges_never_touch_individual_or_custom():
    # A stale key on a replayed row must not turn INDIVIDUAL into a one-bucket band.
    assert base._resolve_band({"block_band": "INDIVIDUAL",
                               "block_band_ranges": [[1, 96]]}) is bb.DEFAULT_BANDS["INDIVIDUAL"]
    assert base._resolve_band({"block_band": "CUSTOM",
                               "block_band_ranges": [[1, 96]]}) is bb.DEFAULT_BANDS["CUSTOM"]
