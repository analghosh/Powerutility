"""Demand and peak adapters — the two source groups with the simple
shape: no revision selection, one document per `(date, block)` or
`(date, block, plant)`.

These tests assert on the pipeline built, not on stored state, which is the house
convention for pipeline code (see `test_forecast_serve_repository.py`). Task 18
proves the pipelines are valid MongoDB against the real database.

Two of them exist to catch silent-wrong-answer bugs rather than crashes:

* `type="DAF"` belongs inside `MAPE_DAF`'s accumulator. In the shared `$match` it
  would delete every `ACTUAL_DEMAND` row from the same query and the user would see
  an empty column, not an error.
* (The scheduling group — the one with no `_historical` twin — was removed on
  2026-09-18 with its dataset; the no-union path is covered in test_analytics_adapter_base.)
"""
import sys
from datetime import date, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services.processing.app.v2.analytics import block_bands as bb  # noqa: E402
from services.processing.app.v2.analytics import registry  # noqa: E402
from services.processing.app.v2.analytics.adapters import base  # noqa: E402
from services.processing.app.v2.analytics.adapters import demand as demand_mod  # noqa: E402
from services.processing.app.v2.analytics.adapters import peak as peak_mod  # noqa: E402


FILTERS = {
    "from_date": date(2026, 8, 1),
    "to_date": date(2026, 8, 3),
    "from_block": 1,
    "to_block": 96,
    "date_grouping": "BLOCK_WISE",
    "block_band": bb.BAND_INDIVIDUAL,
}
CTX = {"plant_count": 53, "exchange_count": 3}


def _stage(pipeline, name):
    return [s for s in pipeline if name in s]


def _group_of(pipeline):
    """The analytics `$group` — always the last one.

    A group with a historical twin carries an earlier `$group` as well: the one
    that drops the copy the archival window leaves in both collections.
    """
    groups = _stage(pipeline, "$group")
    assert groups, "pipeline has no $group"
    return groups[-1]["$group"]


def _value_fields(group):
    """Accumulator fields carrying a dataset's value, excluding the null guards."""
    return [k for k in group
            if k != "_id" and not k.startswith(base.NULL_GUARD_PREFIX)]


def _ds(*codes):
    return [registry.get(c) for c in codes]


# ------------------------------------------------------------- registration

def test_the_two_adapters_register_themselves_under_their_registry_group():
    # The planner looks an adapter up by the dataset's `group`, so these strings
    # have to match registry.Dataset.group exactly or the group is unroutable.
    assert isinstance(base.get_adapter("demand"), demand_mod.DemandAdapter)
    assert isinstance(base.get_adapter("peak"), peak_mod.PeakAdapter)


def test_every_group_named_in_the_registry_for_these_two_has_an_adapter():
    for code in ("ACTUAL_DEMAND", "PEAK_DEMAND"):
        assert base.get_adapter(registry.get(code).group) is not None


def test_no_dataset_names_the_removed_scheduling_group():
    assert all(d.group != "scheduling" for d in registry.DATASETS.values())
    with pytest.raises(KeyError):
        base.get_adapter("scheduling")


def test_the_adapters_name_their_collections():
    d, p = demand_mod.DemandAdapter(), peak_mod.PeakAdapter()
    assert (d.serve_collection, d.historical_collection) == (
        "demand_serve", "demand_serve_historical")
    assert (p.serve_collection, p.historical_collection) == (
        "peak_demand_serve", "peak_demand_serve_historical")


def _analytics_group(pipeline):
    """The final analytics $group — not the serve/historical dedup group before it."""
    return [st["$group"] for st in pipeline if "$group" in st][-1]


def test_demand_declares_type_as_its_entity_but_peak_has_none():
    """`type` was the entity that turned a weekly roll-up into Day Ahead + Intra Day
    columns. Those four datasets were removed on 2026-09-20 and no demand dataset is
    `per_forecast_type` any more, so the declaration is currently inert — kept
    because the adapter's `dedupe_key` includes `type` and because a future
    per-type dataset would need it. Peak has no per-series axis at all."""
    assert demand_mod.DemandAdapter().entity_field() == "type"
    assert peak_mod.PeakAdapter().entity_field() is None


def test_no_demand_dataset_is_reported_per_forecast_type_any_more():
    """If one is ever added, `test_an_ordinary_demand_query...` below is the trap
    it has to clear: keying the shared group on `type` doubles every other column."""
    demand = [d for d in registry.DATASETS.values() if d.group == "demand"]
    assert demand, "the demand group is empty — the registry did not load"
    assert [d.code for d in demand if d.series == "per_forecast_type"] == []


def test_an_ordinary_demand_query_is_not_split_by_forecast_type():
    """The trap: demand rows exist for BOTH types (DAF and IDF) on every block. If
    the group keyed on `type` for Actual Demand, one series would become two, and
    the DAF/IDF rows carry the SAME `drawal` value — a silent duplicate column."""
    ds = [registry.get("ACTUAL_DEMAND"), registry.get("FREQUENCY")]
    pipeline = demand_mod.DemandAdapter().build_pipeline(datasets=ds, filters=FILTERS)
    group = _analytics_group(pipeline)
    assert "e" not in group["_id"], group["_id"]


def test_the_demand_group_is_never_keyed_on_type_today():
    """With no `per_forecast_type` dataset left, nothing asks for the entity key.

    The two tests this replaces pinned the opposite for the weekly roll-ups: with
    one ticked, the whole shared group keyed on `type` and Actual Demand came back
    per type as well.
    """
    ds = [registry.get(c) for c in ("ACTUAL_DEMAND", "FORECAST_DAF", "MAPE_IDF")]
    group = _analytics_group(
        demand_mod.DemandAdapter().build_pipeline(datasets=ds, filters=FILTERS))
    assert "e" not in group["_id"], group["_id"]


# ------------------------------------------------- demand: one pipeline, two rules

def test_two_demand_datasets_produce_one_pipeline_with_two_accumulators():
    p = demand_mod.DemandAdapter().build_pipeline(
        datasets=_ds("ACTUAL_DEMAND", "MAPE_DAF"), filters=FILTERS, ctx=CTX)
    # Both datasets ride one query: one prefilter, one read of the pair of
    # collections, one analytics $group carrying both accumulators.
    assert len(_stage(p, "$match")) == 1
    assert len(_stage(p, "$unionWith")) == 1
    assert _value_fields(_group_of(p)) == ["ACTUAL_DEMAND", "MAPE_DAF"]


def test_the_daf_discriminator_never_reaches_the_shared_match():
    # THE bug this file exists for: type="DAF" in the top-level $match deletes
    # every ACTUAL_DEMAND row, and the user sees an empty column, not an error.
    p = demand_mod.DemandAdapter().build_pipeline(
        datasets=_ds("ACTUAL_DEMAND", "MAPE_DAF"), filters=FILTERS, ctx=CTX)
    assert "type" not in p[0]["$match"]
    union = _stage(p, "$unionWith")[0]["$unionWith"]
    assert "type" not in union["pipeline"][0]["$match"], \
        "the historical half must not be narrowed either"


def test_the_daf_discriminator_is_a_cond_inside_its_own_accumulator():
    g = _group_of(demand_mod.DemandAdapter().build_pipeline(
        datasets=_ds("ACTUAL_DEMAND", "MAPE_DAF"), filters=FILTERS, ctx=CTX))
    assert g["ACTUAL_DEMAND"] == {"$avg": "$actual"}
    assert g["MAPE_DAF"] == {
        "$avg": {"$cond": [{"$and": [{"$eq": ["$type", "DAF"]}]}, "$mape", None]}
    }


def test_a_non_matching_document_contributes_none_not_zero():
    g = _group_of(demand_mod.DemandAdapter().build_pipeline(
        datasets=_ds("MAPE_DAF"), filters=FILTERS, ctx=CTX))
    assert g["MAPE_DAF"]["$avg"]["$cond"][2] is None


def test_daf_and_idf_together_keep_both_columns_separable():
    # The two forecasts share one field (`forecast`) and differ only by `type`.
    # Without the per-dataset guard they would collapse into the same number.
    g = _group_of(demand_mod.DemandAdapter().build_pipeline(
        datasets=_ds("FORECAST_DAF", "FORECAST_IDF"), filters=FILTERS, ctx=CTX))
    assert g["FORECAST_DAF"]["$avg"]["$cond"][0] == {
        "$and": [{"$eq": ["$type", "DAF"]}]}
    assert g["FORECAST_IDF"]["$avg"]["$cond"][0] == {
        "$and": [{"$eq": ["$type", "IDF"]}]}


def test_an_all_daf_selection_still_guards_each_accumulator():
    # A discriminator every dataset shares is hoisted into $match as a free
    # prefilter. The $cond stays anyway: redundant, but it means the answer does
    # not depend on the hoist, which is an optimisation.
    p = demand_mod.DemandAdapter().build_pipeline(
        datasets=_ds("FORECAST_DAF", "MAPE_DAF"), filters=FILTERS, ctx=CTX)
    assert p[0]["$match"]["type"] == "DAF"
    assert "$cond" in _group_of(p)["MAPE_DAF"]["$avg"]


def test_the_demand_pipeline_unions_serve_with_historical_before_grouping():
    p = demand_mod.DemandAdapter().build_pipeline(
        datasets=_ds("ACTUAL_DEMAND"), filters=FILTERS, ctx=CTX)
    assert "$match" in p[0]
    unions = _stage(p, "$unionWith")
    assert len(unions) == 1
    assert unions[0]["$unionWith"]["coll"] == "demand_serve_historical"
    names = [next(iter(s)) for s in p]
    assert names.index("$unionWith") < names.index("$group")


def test_the_demand_match_carries_the_date_and_block_range():
    m = demand_mod.DemandAdapter().build_pipeline(
        datasets=_ds("ACTUAL_DEMAND"), filters=FILTERS, ctx=CTX)[0]["$match"]
    assert m["scheduled_at"] == {"$gte": datetime(2026, 8, 1),
                                 "$lt": datetime(2026, 8, 4)}
    assert m["time_block"] == {"$gte": 1, "$lte": 96}


def test_the_demand_group_keys_only_on_date_and_block():
    _id = _group_of(demand_mod.DemandAdapter().build_pipeline(
        datasets=_ds("ACTUAL_DEMAND"), filters=FILTERS, ctx=CTX))["_id"]
    assert set(_id) == {base.ID_DATE, base.ID_BLOCK}, \
        "demand_serve has no per-entity dimension"


def test_a_plant_filter_is_ignored_by_the_demand_adapter():
    f = dict(FILTERS, plants=["NNTPP"])
    m = demand_mod.DemandAdapter().build_pipeline(
        datasets=_ds("ACTUAL_DEMAND"), filters=f, ctx=CTX)[0]["$match"]
    assert "plant_id" not in m


def test_every_live_demand_dataset_builds():
    live = [d for d in registry.DATASETS.values()
            if d.group == "demand" and d.kind == "direct"
            and d.availability != "absent"]
    assert len(live) >= 13
    g = _group_of(demand_mod.DemandAdapter().build_pipeline(
        datasets=live, filters=FILTERS, ctx=CTX))
    assert _value_fields(g) == [d.code for d in live]


def test_the_derived_dataset_is_refused_by_the_demand_adapter():
    # OD_UD_QUANTUM is drawal - schedule, resolved after the group returns.
    with pytest.raises(ValueError):
        demand_mod.DemandAdapter().build_pipeline(
            datasets=_ds("OD_UD_QUANTUM"), filters=FILTERS, ctx=CTX)


# --------------------------------------------------------- demand: nulls stay null

def _doc(date_key="2026-08-01", block_key="17", entity=None, **values):
    _id = {base.ID_DATE: date_key, base.ID_BLOCK: block_key}
    if entity is not None:
        _id[base.ID_ENTITY] = entity
    return dict(values, _id=_id)


def test_the_demand_adapter_preserves_a_null_rather_than_reporting_zero():
    rows = demand_mod.DemandAdapter().rows_from(
        [_doc(ACTUAL_DEMAND=None)], _ds("ACTUAL_DEMAND"))
    assert rows == [{"dataset": "ACTUAL_DEMAND", "date_key": "2026-08-01",
                     "block_key": "17", "entity": None, "value": None}]


def test_a_genuine_zero_demand_survives_as_zero():
    rows = demand_mod.DemandAdapter().rows_from(
        [_doc(ACTUAL_DEMAND=0.0)], _ds("ACTUAL_DEMAND"))
    assert rows[0]["value"] == 0.0


def test_a_summed_demand_dataset_that_matched_nothing_reads_as_none():
    # $sum over an all-null group returns 0 in Mongo; the guard counter is what
    # keeps "no DSM rows at all" distinguishable from "DSM amount of zero".
    d = _ds("OD_UD_AMOUNT")
    g = _group_of(demand_mod.DemandAdapter().build_pipeline(
        datasets=d, filters=FILTERS, ctx=CTX))
    assert base.NULL_GUARD_PREFIX + "OD_UD_AMOUNT" in g
    doc = _doc(OD_UD_AMOUNT=0, **{base.NULL_GUARD_PREFIX + "OD_UD_AMOUNT": 0})
    assert demand_mod.DemandAdapter().rows_from([doc], d)[0]["value"] is None


# -------------------------------------------------------------------- peak

def test_the_peak_pipeline_unions_its_historical_twin():
    p = peak_mod.PeakAdapter().build_pipeline(
        datasets=_ds("PEAK_DEMAND"), filters=FILTERS, ctx=CTX)
    unions = _stage(p, "$unionWith")
    assert len(unions) == 1
    assert unions[0]["$unionWith"]["coll"] == "peak_demand_serve_historical"


def test_each_peak_dataset_keeps_its_own_aggregation_rule():
    # Peak takes max, minimum takes min, average averages and energy sums.
    # Applying avg to all four is the classic wrong answer under coarse grouping.
    g = _group_of(peak_mod.PeakAdapter().build_pipeline(
        datasets=_ds("PEAK_DEMAND", "MIN_DEMAND", "AVG_DEMAND", "TOTAL_ENERGY_MU"),
        filters=FILTERS, ctx=CTX))
    assert g["PEAK_DEMAND"] == {"$max": "$peak_demand"}
    assert g["MIN_DEMAND"] == {"$min": "$min_demand"}
    assert g["AVG_DEMAND"] == {"$avg": "$average_demand"}
    assert g["TOTAL_ENERGY_MU"] == {"$sum": "$total_energy_mu"}


def test_the_four_peak_datasets_ride_one_pipeline():
    p = peak_mod.PeakAdapter().build_pipeline(
        datasets=_ds("PEAK_DEMAND", "MIN_DEMAND", "AVG_DEMAND", "TOTAL_ENERGY_MU"),
        filters=FILTERS, ctx=CTX)
    assert len(_stage(p, "$match")) == 1
    assert len(_stage(p, "$unionWith")) == 1
    assert len(_value_fields(_group_of(p))) == 4


def test_the_summed_peak_dataset_carries_a_null_guard():
    g = _group_of(peak_mod.PeakAdapter().build_pipeline(
        datasets=_ds("TOTAL_ENERGY_MU"), filters=FILTERS, ctx=CTX))
    # $type, not $ne: a missing field is not equal to null in an aggregation
    # expression, so a comparison guard counts documents that contributed nothing.
    assert g[base.NULL_GUARD_PREFIX + "TOTAL_ENERGY_MU"] == {
        "$sum": {"$cond": [
            {"$in": [{"$type": "$total_energy_mu"}, ["missing", "null"]]}, 0, 1]}}


def test_the_peak_group_has_no_entity_dimension():
    _id = _group_of(peak_mod.PeakAdapter().build_pipeline(
        datasets=_ds("PEAK_DEMAND"), filters=FILTERS, ctx=CTX))["_id"]
    assert base.ID_ENTITY not in _id


def test_an_empty_peak_range_yields_no_rows_rather_than_zeros():
    assert peak_mod.PeakAdapter().rows_from([], _ds("PEAK_DEMAND")) == []


# ------------------------------------------------------- shared refusals

@pytest.mark.parametrize("adapter", [
    demand_mod.DemandAdapter(), peak_mod.PeakAdapter()])
def test_no_adapter_builds_a_pipeline_for_an_empty_selection(adapter):
    with pytest.raises(ValueError):
        adapter.build_pipeline(datasets=[], filters=FILTERS, ctx=CTX)


@pytest.mark.parametrize("adapter", [
    demand_mod.DemandAdapter(), peak_mod.PeakAdapter()])
def test_no_adapter_scans_an_unbounded_date_range(adapter):
    f = {k: v for k, v in FILTERS.items() if k != "to_date"}
    with pytest.raises(ValueError):
        adapter.build_pipeline(datasets=_ds("ACTUAL_DEMAND"), filters=f, ctx=CTX)


@pytest.mark.parametrize("adapter", [
    demand_mod.DemandAdapter(), peak_mod.PeakAdapter()])
def test_an_absent_dataset_is_refused_rather_than_returning_nulls(adapter):
    # WEEKLY_FORECAST has no value field; {"$avg": None} would return null for
    # every block, which reads as "no data in this range", not "not built".
    with pytest.raises(ValueError):
        adapter.build_pipeline(datasets=_ds("TAM_TRADED"),
                               filters=FILTERS, ctx=CTX)
