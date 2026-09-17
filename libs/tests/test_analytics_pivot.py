"""Pivot and request/response models.

The pivot is the last stage of the query engine: canonical rows in, the spec's §7
response contract out. Two properties matter more than anything else here.

1. **Encoding.** `values` is a numeric matrix indexed `[block][date]`, not a list of
   row objects. 144k values is ~1.2MB encoded this way and 5-10MB as objects, and
   that difference is the whole reason a five-month query is servable at all.

2. **A gap is `None`.** Never `0`. The docx is explicit and it is the difference
   between "nothing was scheduled" and "zero was scheduled". A genuine `0` must
   survive as `0` for the same reason.

Tests assert on the built response, not on stored state — the house convention.
"""
import sys
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services.processing.app.v2.analytics import models  # noqa: E402
from services.processing.app.v2.analytics import pivot  # noqa: E402
from services.processing.app.v2.analytics import registry  # noqa: E402
from services.processing.app.v2.analytics.adapters.base import CanonicalRow  # noqa: E402


def _row(dataset, date_key, block_key, value, entity=None) -> CanonicalRow:
    return CanonicalRow(dataset=dataset, date_key=date_key, block_key=block_key,
                        entity=entity, value=value)


def _series_for(resp, code, entity=None):
    for s in resp["series"]:
        if s["dataset"] == code and s["entity"] == entity:
            return s
    raise AssertionError(f"no series for {code}/{entity} in "
                         f"{[(s['dataset'], s['entity']) for s in resp['series']]}")


# ---------------------------------------------------------------- encoding

def test_values_is_a_block_by_date_matrix_not_row_objects():
    rows = [
        _row("ACTUAL_DEMAND", "2026-04-01", "1", 2850.0),
        _row("ACTUAL_DEMAND", "2026-04-01", "2", 2820.0),
        _row("ACTUAL_DEMAND", "2026-04-02", "1", 2900.0),
        _row("ACTUAL_DEMAND", "2026-04-02", "2", 2870.0),
    ]
    resp = pivot.build_response(rows, requested=["ACTUAL_DEMAND"],
                                axes_hint=None, meta={})

    assert resp["axes"] == {"dates": ["2026-04-01", "2026-04-02"], "blocks": [1, 2]}
    s = _series_for(resp, "ACTUAL_DEMAND")
    # outer index is the block axis, inner index is the date axis
    assert s["values"] == [[2850.0, 2900.0], [2820.0, 2870.0]]
    assert len(s["values"]) == len(resp["axes"]["blocks"])
    assert all(len(r) == len(resp["axes"]["dates"]) for r in s["values"])
    # every cell is a scalar, never a dict
    assert all(not isinstance(v, dict) for r in s["values"] for v in r)


def test_series_carries_label_and_unit_from_the_registry():
    resp = pivot.build_response([_row("ACTUAL_DEMAND", "2026-04-01", "1", 1.0)],
                                requested=["ACTUAL_DEMAND"], axes_hint=None, meta={})
    s = _series_for(resp, "ACTUAL_DEMAND")
    assert s["label"] == registry.get("ACTUAL_DEMAND").label
    assert s["unit"] == registry.get("ACTUAL_DEMAND").unit
    assert s["entity"] is None


# ------------------------------------------------------------------- gaps

def test_a_gap_becomes_none_never_zero():
    rows = [
        _row("ACTUAL_DEMAND", "2026-04-01", "1", 2850.0),
        # block 2 on 01 Apr never returned
        _row("ACTUAL_DEMAND", "2026-04-02", "2", 2870.0),
    ]
    resp = pivot.build_response(rows, requested=["ACTUAL_DEMAND"],
                                axes_hint=None, meta={})
    s = _series_for(resp, "ACTUAL_DEMAND")
    assert s["values"] == [[2850.0, None], [None, 2870.0]]
    assert 0 not in [v for r in s["values"] for v in r]


def test_an_explicit_none_row_stays_none():
    rows = [_row("ACP_HPDAM", "2026-04-01", "1", None, entity="IEX")]
    resp = pivot.build_response(rows, requested=["ACP_HPDAM"],
                                axes_hint=None, meta={})
    assert _series_for(resp, "ACP_HPDAM", "IEX")["values"] == [[None]]


def test_a_genuine_zero_is_preserved():
    rows = [_row("ACTUAL_DEMAND", "2026-04-01", "1", 0.0)]
    resp = pivot.build_response(rows, requested=["ACTUAL_DEMAND"],
                                axes_hint=None, meta={})
    s = _series_for(resp, "ACTUAL_DEMAND")
    assert s["values"] == [[0.0]]
    assert s["values"][0][0] is not None


def test_a_real_value_wins_over_a_duplicate_none_for_the_same_cell():
    rows = [
        _row("ACTUAL_DEMAND", "2026-04-01", "1", 2850.0),
        _row("ACTUAL_DEMAND", "2026-04-01", "1", None),
    ]
    resp = pivot.build_response(rows, requested=["ACTUAL_DEMAND"],
                                axes_hint=None, meta={})
    assert _series_for(resp, "ACTUAL_DEMAND")["values"] == [[2850.0]]


# ------------------------------------------------------------------- axes

def test_axes_are_sorted_and_deduplicated():
    rows = [
        _row("ACTUAL_DEMAND", "2026-04-02", "10", 1.0),
        _row("ACTUAL_DEMAND", "2026-04-01", "2", 2.0),
        _row("ACTUAL_DEMAND", "2026-04-02", "2", 3.0),
        _row("ACTUAL_DEMAND", "2026-04-01", "10", 4.0),
    ]
    resp = pivot.build_response(rows, requested=["ACTUAL_DEMAND"],
                                axes_hint=None, meta={})
    assert resp["axes"]["dates"] == ["2026-04-01", "2026-04-02"]
    # numeric order, not lexical - "10" must not sort before "2"
    assert resp["axes"]["blocks"] == [2, 10]


def test_block_axis_keeps_band_labels_as_strings():
    rows = [
        _row("ACTUAL_DEMAND", "2026-04-01", "EVENING_PEAK", 1.0),
        _row("ACTUAL_DEMAND", "2026-04-01", "DAY_PEAK", 2.0),
    ]
    resp = pivot.build_response(rows, requested=["ACTUAL_DEMAND"],
                                axes_hint=None, meta={})
    assert resp["axes"]["blocks"] == ["DAY_PEAK", "EVENING_PEAK"]
    assert _series_for(resp, "ACTUAL_DEMAND")["values"] == [[2.0], [1.0]]


def test_custom_block_buckets_sort_by_their_lower_bound():
    rows = [
        _row("ACTUAL_DEMAND", "2026-04-01", "9-20", 1.0),
        _row("ACTUAL_DEMAND", "2026-04-01", "1-8", 2.0),
    ]
    resp = pivot.build_response(rows, requested=["ACTUAL_DEMAND"],
                                axes_hint=None, meta={})
    assert resp["axes"]["blocks"] == ["1-8", "9-20"]


def test_axes_hint_supplies_axis_values_the_rows_never_returned():
    rows = [_row("ACTUAL_DEMAND", "2026-04-01", "1", 2850.0)]
    resp = pivot.build_response(
        rows, requested=["ACTUAL_DEMAND"],
        axes_hint={"dates": ["2026-04-01", "2026-04-02"], "blocks": [1, 2, 3]},
        meta={})
    assert resp["axes"]["dates"] == ["2026-04-01", "2026-04-02"]
    assert resp["axes"]["blocks"] == [1, 2, 3]
    assert _series_for(resp, "ACTUAL_DEMAND")["values"] == [
        [2850.0, None], [None, None], [None, None],
    ]


def test_a_row_outside_the_hint_still_appears_on_the_axis():
    """A hint narrows nothing away - dropping a returned value would be silent loss."""
    rows = [_row("ACTUAL_DEMAND", "2026-04-03", "5", 7.0)]
    resp = pivot.build_response(rows, requested=["ACTUAL_DEMAND"],
                                axes_hint={"dates": ["2026-04-01"], "blocks": [1]},
                                meta={})
    assert resp["axes"]["dates"] == ["2026-04-01", "2026-04-03"]
    assert resp["axes"]["blocks"] == [1, 5]


def test_empty_result_still_returns_well_formed_axes():
    resp = pivot.build_response([], requested=["ACTUAL_DEMAND"],
                                axes_hint={"dates": ["2026-04-01"], "blocks": [1, 2]},
                                meta={})
    assert resp["axes"] == {"dates": ["2026-04-01"], "blocks": [1, 2]}
    s = _series_for(resp, "ACTUAL_DEMAND")
    assert s["values"] == [[None], [None]]
    assert s["quality"] == pivot.QUALITY_EMPTY
    assert resp["meta"]["missing_datasets"] == ["ACTUAL_DEMAND"]


def test_empty_result_with_no_hint_returns_empty_axes_not_an_error():
    resp = pivot.build_response([], requested=["ACTUAL_DEMAND"],
                                axes_hint=None, meta={})
    assert resp["axes"] == {"dates": [], "blocks": []}
    assert _series_for(resp, "ACTUAL_DEMAND")["values"] == []
    assert resp["meta"]["cells"] == 0


# --------------------------------------------------------------- entities

def test_a_per_plant_dataset_produces_one_series_per_plant_with_entity_set():
    rows = [
        _row("DC_PLANT", "2026-04-01", "1", 100.0, entity="PLANT_B"),
        _row("DC_PLANT", "2026-04-01", "2", 110.0, entity="PLANT_B"),
        _row("DC_PLANT", "2026-04-01", "1", 200.0, entity="PLANT_A"),
        _row("DC_PLANT", "2026-04-01", "2", 210.0, entity="PLANT_A"),
    ]
    resp = pivot.build_response(rows, requested=["DC_PLANT"],
                                axes_hint=None, meta={})
    assert [s["entity"] for s in resp["series"]] == ["PLANT_A", "PLANT_B"]
    assert all(s["dataset"] == "DC_PLANT" for s in resp["series"])
    assert _series_for(resp, "DC_PLANT", "PLANT_A")["values"] == [[200.0], [210.0]]
    assert _series_for(resp, "DC_PLANT", "PLANT_B")["values"] == [[100.0], [110.0]]


def test_one_entity_missing_a_block_gets_none_not_the_other_entitys_value():
    rows = [
        _row("DC_PLANT", "2026-04-01", "1", 100.0, entity="PLANT_A"),
        _row("DC_PLANT", "2026-04-01", "2", 210.0, entity="PLANT_B"),
    ]
    resp = pivot.build_response(rows, requested=["DC_PLANT"],
                                axes_hint=None, meta={})
    assert _series_for(resp, "DC_PLANT", "PLANT_A")["values"] == [[100.0], [None]]
    assert _series_for(resp, "DC_PLANT", "PLANT_B")["values"] == [[None], [210.0]]


def test_series_follow_the_requested_dataset_order():
    rows = [
        _row("MAPE_DAF", "2026-04-01", "1", 1.0),
        _row("ACTUAL_DEMAND", "2026-04-01", "1", 2.0),
        _row("DC_PLANT", "2026-04-01", "1", 3.0, entity="PLANT_A"),
    ]
    resp = pivot.build_response(rows, requested=["ACTUAL_DEMAND", "DC_PLANT", "MAPE_DAF"],
                                axes_hint=None, meta={})
    assert [s["dataset"] for s in resp["series"]] == [
        "ACTUAL_DEMAND", "DC_PLANT", "MAPE_DAF"]


# ---------------------------------------------------------------- quality

def test_a_failed_dataset_is_marked_failed_and_listed_in_meta():
    rows = [_row("ACTUAL_DEMAND", "2026-04-01", "1", 2850.0)]
    resp = pivot.build_response(
        rows, requested=["ACTUAL_DEMAND", "ACP_DAM"],
        axes_hint=None, meta={"failed_datasets": ["ACP_DAM"]})

    assert _series_for(resp, "ACTUAL_DEMAND")["quality"] == pivot.QUALITY_COMPLETE
    failed = _series_for(resp, "ACP_DAM")
    assert failed["quality"] == pivot.QUALITY_FAILED
    assert failed["values"] == [[None]]
    assert resp["meta"]["failed_datasets"] == ["ACP_DAM"]
    # a failure is not a gap - it must not be reported as merely missing
    assert "ACP_DAM" not in resp["meta"]["missing_datasets"]


def test_a_failed_dataset_that_returned_partial_rows_is_still_failed():
    rows = [_row("ACP_DAM", "2026-04-01", "1", 4200.0, entity="IEX")]
    resp = pivot.build_response(rows, requested=["ACP_DAM"], axes_hint=None,
                                meta={"failed_datasets": ["ACP_DAM"]})
    assert _series_for(resp, "ACP_DAM", "IEX")["quality"] == pivot.QUALITY_FAILED


def test_a_partially_filled_series_is_marked_partial():
    rows = [
        _row("ACTUAL_DEMAND", "2026-04-01", "1", 2850.0),
        _row("ACTUAL_DEMAND", "2026-04-02", "2", 2870.0),
    ]
    resp = pivot.build_response(rows, requested=["ACTUAL_DEMAND"],
                                axes_hint=None, meta={})
    assert _series_for(resp, "ACTUAL_DEMAND")["quality"] == pivot.QUALITY_PARTIAL


# ------------------------------------------------------------------- meta

def test_meta_is_completed_without_mutating_the_callers_dict():
    caller_meta = {"truncated": True, "band": "inline_capped"}
    resp = pivot.build_response([_row("ACTUAL_DEMAND", "2026-04-01", "1", 1.0)],
                                requested=["ACTUAL_DEMAND"], axes_hint=None,
                                meta=caller_meta)
    assert caller_meta == {"truncated": True, "band": "inline_capped"}
    assert resp["meta"]["truncated"] is True
    assert resp["meta"]["band"] == "inline_capped"
    assert resp["meta"]["missing_datasets"] == []
    assert resp["meta"]["failed_datasets"] == []
    assert resp["meta"]["generated_at"]


def test_meta_cells_counts_the_rendered_matrix():
    rows = [
        _row("DC_PLANT", "2026-04-01", "1", 1.0, entity="PLANT_A"),
        _row("DC_PLANT", "2026-04-01", "2", 2.0, entity="PLANT_B"),
        _row("ACTUAL_DEMAND", "2026-04-01", "1", 3.0),
    ]
    resp = pivot.build_response(rows, requested=["ACTUAL_DEMAND", "DC_PLANT"],
                                axes_hint=None, meta={})
    # 3 series x 1 date x 2 blocks
    assert len(resp["series"]) == 3
    assert resp["meta"]["cells"] == 6


def test_a_caller_supplied_cell_estimate_is_not_overwritten():
    resp = pivot.build_response([_row("ACTUAL_DEMAND", "2026-04-01", "1", 1.0)],
                                requested=["ACTUAL_DEMAND"], axes_hint=None,
                                meta={"cells": 14400})
    assert resp["meta"]["cells"] == 14400


def test_caller_supplied_missing_datasets_survive():
    resp = pivot.build_response([], requested=["ACTUAL_DEMAND"], axes_hint=None,
                                meta={"missing_datasets": ["TAM_TRADED"]})
    assert resp["meta"]["missing_datasets"] == ["TAM_TRADED", "ACTUAL_DEMAND"]


def test_rows_for_an_unrequested_dataset_are_ignored():
    """Task 15 drops dependency-only rows; anything left over is not the user's ask."""
    rows = [
        _row("OD_UD_QUANTUM", "2026-04-01", "1", -1.48),
        _row("DRAWAL", "2026-04-01", "1", 533.96),
    ]
    resp = pivot.build_response(rows, requested=["OD_UD_QUANTUM"],
                                axes_hint=None, meta={})
    assert [s["dataset"] for s in resp["series"]] == ["OD_UD_QUANTUM"]
    assert _series_for(resp, "OD_UD_QUANTUM")["values"] == [[-1.48]]


def test_unknown_requested_dataset_raises():
    with pytest.raises(registry.UnknownDatasetError):
        pivot.build_response([], requested=["NOT_A_DATASET"], axes_hint=None, meta={})


def test_response_validates_against_the_response_model():
    rows = [_row("DC_PLANT", "2026-04-01", "1", 100.0, entity="PLANT_A")]
    resp = pivot.build_response(rows, requested=["DC_PLANT"], axes_hint=None,
                                meta={"truncated": False})
    models.AnalyticsQueryResponse.model_validate(resp)


# ------------------------------------------------------- request validation

def _req(**kw):
    base = {"from_date": date(2026, 4, 1), "to_date": date(2026, 4, 3),
            "datasets": ["ACTUAL_DEMAND"]}
    base.update(kw)
    return models.AnalyticsQueryRequest(**base)


def test_a_valid_request_defaults_to_the_whole_day_block_wise():
    r = _req()
    assert (r.from_block, r.to_block) == (1, 96)
    assert r.date_grouping == "BLOCK_WISE"
    assert r.block_band == "INDIVIDUAL"
    assert r.plants == [] and r.exchanges == []


def test_request_rejects_from_date_after_to_date():
    with pytest.raises(ValidationError, match="from_date"):
        _req(from_date=date(2026, 4, 5), to_date=date(2026, 4, 1))


def test_request_accepts_a_single_day_range():
    r = _req(from_date=date(2026, 4, 1), to_date=date(2026, 4, 1))
    assert r.from_date == r.to_date


def test_request_rejects_block_zero():
    with pytest.raises(ValidationError):
        _req(from_block=0)


def test_request_rejects_block_97():
    with pytest.raises(ValidationError):
        _req(to_block=97)


def test_request_rejects_from_block_after_to_block():
    with pytest.raises(ValidationError, match="from_block"):
        _req(from_block=40, to_block=10)


def test_request_rejects_an_empty_dataset_list():
    with pytest.raises(ValidationError):
        _req(datasets=[])


def test_request_rejects_an_unknown_dataset_code():
    with pytest.raises(ValidationError, match="NOT_A_DATASET"):
        _req(datasets=["ACTUAL_DEMAND", "NOT_A_DATASET"])


def test_request_rejects_a_per_plant_dataset_with_no_plants():
    with pytest.raises(ValidationError, match="plant"):
        _req(datasets=["DC_PLANT"])


def test_request_accepts_a_per_plant_dataset_with_plants():
    r = _req(datasets=["DC_PLANT"], plants=["PLANT_A"])
    assert r.plants == ["PLANT_A"]


def test_request_rejects_an_unknown_date_grouping():
    with pytest.raises(ValidationError, match="date_grouping"):
        _req(date_grouping="FORTNIGHTLY")


def test_request_rejects_an_unknown_block_band():
    with pytest.raises(ValidationError, match="block_band"):
        _req(block_band="GOLDEN_HOUR")


def test_request_rejects_a_custom_band_with_no_ranges():
    with pytest.raises(ValidationError, match="custom_blocks"):
        _req(block_band="CUSTOM")


def test_request_rejects_a_custom_range_outside_1_to_96():
    with pytest.raises(ValidationError):
        _req(block_band="CUSTOM", custom_blocks=[[0, 8]])


def test_request_deduplicates_datasets_while_keeping_order():
    r = _req(datasets=["MAPE_DAF", "ACTUAL_DEMAND", "MAPE_DAF"])
    assert r.datasets == ["MAPE_DAF", "ACTUAL_DEMAND"]


def test_to_filters_feeds_the_adapters_verbatim():
    r = _req(datasets=["DC_PLANT"], plants=["PLANT_A"], from_block=5, to_block=20,
             date_grouping="WEEK_WISE")
    f = r.to_filters()
    assert f["from_date"] == date(2026, 4, 1)
    assert f["to_date"] == date(2026, 4, 3)
    assert f["from_block"] == 5 and f["to_block"] == 20
    assert f["date_grouping"] == "WEEK_WISE"
    assert f["block_band"] == "INDIVIDUAL"
    assert f["plants"] == ["PLANT_A"]
    assert "datasets" not in f


def test_export_request_carries_the_same_filters_plus_a_format():
    e = models.ExportRequest(from_date=date(2026, 4, 1), to_date=date(2026, 4, 3),
                             datasets=["ACTUAL_DEMAND"], format="xlsx")
    assert e.format == "xlsx"
    assert e.to_filters()["from_block"] == 1


def test_export_request_rejects_an_unsupported_format():
    with pytest.raises(ValidationError):
        models.ExportRequest(from_date=date(2026, 4, 1), to_date=date(2026, 4, 3),
                             datasets=["ACTUAL_DEMAND"], format="pdf")


def test_export_request_inherits_the_query_validation():
    with pytest.raises(ValidationError, match="plant"):
        models.ExportRequest(from_date=date(2026, 4, 1), to_date=date(2026, 4, 3),
                             datasets=["DC_PLANT"], format="csv")


def test_request_rejects_an_inactive_block_band():
    """The five named bands have no ranges yet, and `block_key_expr` raises on one.

    Rejecting here turns a 500 deep in the pipeline builder into a 422 naming the
    band. Not in the plan's list; added because the failure it prevents is real.
    """
    with pytest.raises(ValidationError, match="inactive"):
        _req(block_band="EVENING_PEAK")
