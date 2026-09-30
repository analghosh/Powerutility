"""DSM Published (As Per RPC) — choosing which of its figures to see.

Every upload still stores the whole statement; the choice is only what the
dashboard shows. So `dsm_fields` narrows the columns of the table and of both
files, prices the request by the columns actually chosen, and travels with the
request everywhere a filter does: the stored export, the Filters sheet, the cache
key and the saved defaults. An empty choice means every figure.
"""
import asyncio
import sys
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError
from pymongo import MongoClient
from pymongo.errors import ServerSelectionTimeoutError

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services.processing.app.v2.analytics import block_bands as bb  # noqa: E402
from services.processing.app.v2.analytics import pivot, registry  # noqa: E402
from services.processing.app.v2.analytics.adapters import base  # noqa: E402
from services.processing.app.v2.analytics.models import (  # noqa: E402
    AnalyticsQueryRequest, PreferenceRequest)
from services.processing.app.v2.controller.analytics_controller import (  # noqa: E402
    AnalyticsController)
from services.processing.app.v2.helpers.analytics_csv import filter_pairs  # noqa: E402
from services.processing.app.v2.repository import \
    analytics_preference_repository as prefs  # noqa: E402
from services.processing.app.v2.services import analytics_service as svc  # noqa: E402

FIELDS = registry.DSM_PUBLISHED_FIELDS
DSM = "DSM_PUBLISHED"


def _request(**over):
    body = {"from_date": date(2026, 3, 30), "to_date": date(2026, 3, 31),
            "datasets": [DSM]}
    body.update(over)
    return AnalyticsQueryRequest(**body)


# ------------------------------------------------------------------ request


def test_no_choice_means_every_figure():
    assert _request().dsm_fields == list(FIELDS)


def test_the_choice_is_kept_in_the_rpc_order_without_repeats():
    req = _request(dsm_fields=["deviation", "schedule_total", "deviation"])
    assert req.dsm_fields == ["schedule_total", "deviation"]


def test_a_figure_the_statement_does_not_have_is_refused():
    with pytest.raises(ValidationError, match="DSM field"):
        _request(dsm_fields=["schedule_total", "profit"])


def test_the_choice_travels_with_the_filters():
    assert _request(dsm_fields=["frequency"]).to_filters()["dsm_fields"] == ["frequency"]


# ------------------------------------------------------------------ pricing


def test_a_narrower_choice_is_priced_as_fewer_columns():
    service = svc.AnalyticsService(repository=object())
    ctx = service.context_for(_request(dsm_fields=["frequency", "deviation"]))
    assert ctx["dsm_field_count"] == 2
    assert registry.series_cardinality(registry.get(DSM), ctx) == 2


def test_with_no_count_given_every_figure_is_priced():
    assert registry.series_cardinality(registry.get(DSM), {}) == len(FIELDS)


def test_the_cache_tells_two_choices_apart():
    service = svc.AnalyticsService(repository=object())
    assert (service.cache_key(_request(dsm_fields=["frequency"]))
            != service.cache_key(_request(dsm_fields=["deviation"])))


# ----------------------------------------------------------------- pipeline

FILTERS = {"from_date": date(2026, 3, 30), "to_date": date(2026, 3, 31),
           "from_block": 1, "to_block": 96, "block_band": bb.BAND_INDIVIDUAL}


def _figures(pipeline):
    project = next(stage["$project"] for stage in pipeline if "$project" in stage)
    return [figure["k"] for figure in project["figures"]]


def test_the_pipeline_reads_only_the_chosen_figures():
    pipeline = base.get_adapter("dsm_published").build_pipeline(
        datasets=[registry.get(DSM)],
        filters={**FILTERS, "dsm_fields": ["actual_total", "overdrawl_charges"]})
    assert _figures(pipeline) == ["actual_total", "overdrawl_charges"]


def test_a_pipeline_given_no_choice_reads_every_figure():
    pipeline = base.get_adapter("dsm_published").build_pipeline(
        datasets=[registry.get(DSM)], filters=FILTERS)
    assert _figures(pipeline) == list(FIELDS)


# -------------------------------------------------------------------- pivot


def _row(field, value):
    return {"dataset": DSM, "date_key": "2026-03-30", "block_key": "1",
            "entity": field, "value": value}


def test_only_the_chosen_figures_become_columns_in_the_rpc_order():
    response = pivot.build_response(
        [_row("frequency", 50.0), _row("schedule_total", 101.0), _row("deviation", -2.0)],
        requested=[DSM], meta={},
        entity_order={"per_dsm_field": ["schedule_total", "frequency"]})
    series = response["series"]
    assert [s["entity"] for s in series] == ["schedule_total", "frequency"]
    assert [s["values"][0][0] for s in series] == [101.0, 50.0]


def test_a_chosen_figure_with_no_data_is_still_a_column():
    response = pivot.build_response(
        [], requested=[DSM], meta={}, entity_order={"per_dsm_field": ["dev_percent"]})
    assert [s["entity"] for s in response["series"]] == ["dev_percent"]


# ------------------------------------------------------------ filters sheet


def test_the_stored_export_remembers_the_choice_so_execute_replays_it():
    from services.processing.app.v2.analytics.models import ExportRequest
    from services.processing.app.v2.services.analytics_export_service import (
        AnalyticsExportService)
    stored = AnalyticsExportService(service=object(), jobs=object(), publisher=object()) \
        .filters_for(ExportRequest(**_request(dsm_fields=["frequency"]).model_dump()))
    assert stored["dsm_fields"] == ["frequency"]
    assert ExportRequest(**stored).dsm_fields == ["frequency"]


def test_the_filters_sheet_names_the_chosen_figures():
    filters = _request(dsm_fields=["schedule_total", "deviation"]).model_dump(mode="json")
    assert ("DSM fields", "Schedule Total, Deviation") in filter_pairs(filters)


def test_the_filters_sheet_is_silent_about_dsm_fields_when_dsm_is_not_ticked():
    filters = _request(datasets=["SG_TOTAL"]).model_dump(mode="json")
    assert "DSM fields" not in dict(filter_pairs(filters))


# ---------------------------------------------------------------- catalogue


def test_the_catalogue_lists_the_figures_to_choose_from():
    payload = asyncio.run(AnalyticsController().datasets(None))["data"]
    dsm = next(d for d in payload["datasets"] if d["code"] == DSM)
    assert dsm["cardinality_basis"] == "dsm_fields"
    assert dsm["fields"] == [
        {"key": field, "label": registry.DSM_PUBLISHED_LABELS[field]} for field in FIELDS]
    others = [d for d in payload["datasets"] if d["code"] != DSM]
    assert all("fields" not in d for d in others)


# ----------------------------------------------------------------- defaults


def test_a_saved_default_keeps_the_chosen_figures():
    stored = prefs.validate({"datasets": [DSM], "dsm_fields": ["deviation", "frequency"]})
    assert stored["dsm_fields"] == ["frequency", "deviation"]
    assert prefs.sanitize(stored)[0]["dsm_fields"] == ["frequency", "deviation"]


def test_a_saved_default_refuses_an_unknown_figure():
    with pytest.raises(prefs.InvalidPreference):
        prefs.validate({"datasets": [DSM], "dsm_fields": ["profit"]})


def test_a_default_saved_before_the_choice_existed_still_loads():
    loaded, dropped = prefs.sanitize({"datasets": [DSM], "plants": [], "format": "xlsx"})
    assert loaded["dsm_fields"] == [] and dropped == []
    assert prefs.empty_preference()["dsm_fields"] == []


def test_the_defaults_endpoint_accepts_the_choice():
    body = PreferenceRequest(datasets=[DSM], dsm_fields=["frequency"])
    assert body.dsm_fields == ["frequency"]


# ------------------------------------------------------------ real MongoDB

MONGO_URI = "mongodb://localhost:27017"
ITEST_DB = "epms_analytics_itest_dsm_fields"


@pytest.fixture(scope="module")
def seeded():
    try:
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=1500)
        client.server_info()
    except ServerSelectionTimeoutError:
        pytest.skip("local MongoDB not running")
    db = client[ITEST_DB]
    db.drop_collection("exchange_dsm_report_day")
    db.exchange_dsm_report_day.insert_one({"report_date": "2026-03-30", "blocks": [
        {"block": n, "schedule_total": 100.0 + n, "actual_total": 98.0 + n,
         "frequency": 50.0, "deviation_rate": 400.0, "deviation": -2.0,
         "dev_percent": 1.5, "overdrawl_charges": 0.0, "underdrawl_charges": 10.0 * n,
         "postfacto_charges": 3.0}
        for n in range(1, 97)]})
    yield db
    client.drop_database(ITEST_DB)


def test_a_real_statement_comes_back_with_only_the_chosen_figures(seeded):
    chosen = ["underdrawl_charges", "schedule_total"]
    req = _request(to_date=date(2026, 3, 30), dsm_fields=chosen)
    adapter, dataset = base.get_adapter("dsm_published"), registry.get(DSM)
    docs = list(seeded[adapter.serve_collection].aggregate(
        adapter.build_pipeline(datasets=[dataset], filters=req.to_filters())))
    response = pivot.build_response(
        adapter.rows_from(docs, [dataset]), requested=[DSM], meta={},
        entity_order={"per_dsm_field": req.dsm_fields})

    assert {doc["_id"][base.ID_ENTITY] for doc in docs} == set(chosen)
    series = {s["entity"]: s["values"] for s in response["series"]}
    assert list(series) == ["schedule_total", "underdrawl_charges"]
    assert series["schedule_total"][0][0] == 101.0
    assert series["underdrawl_charges"][95][0] == 960.0
