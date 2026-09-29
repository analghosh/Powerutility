"""DSM Published (As Per RPC) — the uploaded DSM statement as a dashboard dataset.

The statement lives in `exchange_dsm_report_day`: one document per settlement
day, an ISO date string, and the 96 blocks inline. None of that is the shape the
other groups have, so these tests pin the reshape, the one-column-per-figure
layout in the RPC's order, and the two aggregation rules one dataset carries.
The pipeline half runs against the real local MongoDB on a throwaway database.
"""
import sys
from datetime import date
from pathlib import Path

import pytest
from pymongo import MongoClient
from pymongo.errors import ServerSelectionTimeoutError

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services.processing.app.v2.analytics import block_bands as bb  # noqa: E402
from services.processing.app.v2.analytics import pivot, registry  # noqa: E402
from services.processing.app.v2.analytics.adapters import base  # noqa: E402

FIELDS = registry.DSM_PUBLISHED_FIELDS
FILTERS = {"from_date": date(2026, 3, 30), "to_date": date(2026, 3, 31),
           "from_block": 1, "to_block": 96, "block_band": bb.BAND_INDIVIDUAL}


def _adapter():
    return base.get_adapter("dsm_published")


def _dataset():
    return registry.get("DSM_PUBLISHED")


# ----------------------------------------------------------------- registry


def test_one_dataset_named_as_ptc_asked():
    dataset = _dataset()
    assert dataset.label == "DSM Published (As Per RPC)"
    assert dataset.group == "dsm_published"
    assert dataset.permission == "analytics:dsm"
    assert dataset.uploaded is True
    assert dataset in registry.selectable()


def test_one_tick_is_priced_as_one_column_per_published_figure():
    assert registry.series_cardinality(_dataset(), {}) == 7
    assert FIELDS == ("schedule_total", "actual_total", "frequency", "deviation_rate",
                      "deviation", "overdrawl_charges", "underdrawl_charges")


def test_only_the_charges_add_up():
    assert set(registry.DSM_PUBLISHED_SUMMED) == {"overdrawl_charges", "underdrawl_charges"}


# ----------------------------------------------------------------- pipeline


def test_the_day_is_matched_as_the_iso_string_it_is_stored_as():
    pipeline = _adapter().build_pipeline(datasets=[_dataset()], filters=FILTERS)
    assert pipeline[0] == {"$match": {"report_date": {"$gte": "2026-03-30",
                                                      "$lte": "2026-03-31"}}}


def test_the_block_range_filters_the_unwound_blocks():
    pipeline = _adapter().build_pipeline(
        datasets=[_dataset()], filters={**FILTERS, "from_block": 5, "to_block": 8})
    assert {"$match": {"blocks.block": {"$gte": 5, "$lte": 8}}} in pipeline


def test_a_backwards_range_is_refused():
    with pytest.raises(ValueError):
        _adapter().build_pipeline(datasets=[_dataset()], filters={
            **FILTERS, "from_date": date(2026, 4, 2)})


# -------------------------------------------------------------------- pivot


def _response(rows):
    return pivot.build_response(rows, requested=["DSM_PUBLISHED"], meta={})


def test_every_figure_is_a_column_in_the_rpc_order_even_with_no_data():
    series = _response([])["series"]
    assert [s["entity"] for s in series] == list(FIELDS)
    assert [s["entity_label"] for s in series] == [
        "Schedule Total", "Actual Total", "Frequency", "Deviation Rate", "Deviation",
        "Overdrawl_Charges", "Underdrawl_Charges"]


def test_the_column_heading_names_the_dataset_and_the_figure():
    from services.processing.app.v2.helpers.analytics_layout import column_label
    series = _response([])["series"]
    assert column_label(series[0]) == "DSM Published (As Per RPC) · Schedule Total"


# ------------------------------------------------------------ real MongoDB

MONGO_URI = "mongodb://localhost:27017"
ITEST_DB = "epms_analytics_itest_dsm"


def _mongo():
    try:
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=1500)
        client.server_info()
        return client
    except ServerSelectionTimeoutError:
        return None


def _block(n, **over):
    doc = {"block": n, "block_time": f"{(n - 1) // 4:02d}:{(n - 1) % 4 * 15:02d}",
           "schedule_total": 100.0 + n, "actual_total": 98.0 + n, "frequency": 50.0,
           "deviation_rate": 400.0 + n, "deviation": -2.0,
           "overdrawl_charges": 0.0, "underdrawl_charges": 1000.0 * n}
    doc.update(over)
    return doc


@pytest.fixture(scope="module")
def seeded():
    client = _mongo()
    if client is None:
        pytest.skip("local MongoDB not running")
    db = client[ITEST_DB]
    assert db.name == ITEST_DB
    db.drop_collection("exchange_dsm_report_day")
    db.exchange_dsm_report_day.insert_many([
        {"report_date": "2026-03-30",
         "blocks": [_block(n) for n in range(1, 97)]},
        # A figure the statement left blank stays blank; a stored 0 stays 0.
        {"report_date": "2026-03-31",
         "blocks": [_block(1, frequency=None), _block(2)]},
        # Outside the range asked for.
        {"report_date": "2026-04-01", "blocks": [_block(1)]},
    ])
    yield db
    client.drop_database(ITEST_DB)


def _run(db, filters):
    adapter, dataset = _adapter(), _dataset()
    docs = list(db[adapter.serve_collection].aggregate(
        adapter.build_pipeline(datasets=[dataset], filters=filters)))
    return _response(adapter.rows_from(docs, [dataset]))


def _cell(response, field, day, block):
    series = next(s for s in response["series"] if s["entity"] == field)
    di = response["axes"]["dates"].index(day)
    bi = [str(b) for b in response["axes"]["blocks"]].index(str(block))
    return series["values"][bi][di]


def test_the_stored_figures_come_back_block_by_block(seeded):
    response = _run(seeded, FILTERS)
    assert response["axes"]["dates"] == ["2026-03-30", "2026-03-31"]
    assert _cell(response, "schedule_total", "2026-03-30", 1) == 101.0
    assert _cell(response, "underdrawl_charges", "2026-03-30", 96) == 96000.0
    assert _cell(response, "overdrawl_charges", "2026-03-30", 5) == 0.0


def test_a_blank_figure_is_none_not_zero(seeded):
    response = _run(seeded, FILTERS)
    assert _cell(response, "frequency", "2026-03-31", 1) is None
    assert _cell(response, "frequency", "2026-03-31", 2) == 50.0


def test_grouped_blocks_add_the_charges_and_average_the_rest(seeded):
    response = _run(seeded, {**FILTERS, "block_band": bb.BAND_CUSTOM,
                             "custom_blocks": [[1, 4]]})
    # Blocks 1-4 of 30 March: underdrawl 1000+2000+3000+4000, schedule avg of 101..104.
    assert _cell(response, "underdrawl_charges", "2026-03-30", "1-4") == 10000.0
    assert _cell(response, "schedule_total", "2026-03-30", "1-4") == 102.5
