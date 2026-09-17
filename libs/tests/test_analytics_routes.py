"""The analytics HTTP surface: catalogue, plant selector, block bands, query.

Five claims here decide whether the screen works at all, and four of them fail
quietly rather than loudly.

1. **The payload is nested under `data`.** Every v2 controller in this service
   returns `{"data": ...}` and the client reads `response.data.data`. A route that
   returns the envelope bare is a blank dashboard with a 200 in the network tab.

2. **The plant list is dataset-aware.** `supply_serve` holds 34 plant ids and
   `serve_dam_scheduling` holds 53 — verified against the local UAT import. They are
   not interchangeable: offering the scheduling list to Plant-wise DC produces a
   plant selection that matches no supply document and a table of empty cells, which
   reads as "no data" rather than as the wrong dropdown.

3. **An over-budget query is refused with 413 and a way out.** The budget is
   arithmetic done before any database call, so the refusal must carry the estimate
   and its suggestions — "Week-wise brings this to 23,520" — or the user is left
   with a dead Apply button and no idea which filter is the expensive one. The
   repository recording zero calls is what proves the refusal happened before Mongo.

4. **A dataset with no storage is refused by name, not rendered as an empty
   column.** `WEEKLY_FORECAST` is registered `absent`; the request model lets it
   through (it is a known code) and the planner raises. Unmapped, that exception is
   a 500 and the client cannot tell it from a broken server.

5. **A failed plant lookup is not an empty plant list.** `GenericRepository` returns
   `[]` for an exception across this codebase, which is exactly how a broken query
   becomes an innocent-looking empty screen. The analytics repository raises, and
   this route has to turn that into a 503 rather than a dropdown with nothing in it.

Mongo is mocked — the controller is handed a stub repository recording every
`run_pipeline` call, the house convention.
"""
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pymongo.errors import PyMongoError

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services.processing.app.v2.analytics import block_bands, registry  # noqa: E402
from services.processing.app.v2.api.router import analytics_routes  # noqa: E402
from services.processing.app.v2.controller import analytics_controller as ctrl  # noqa: E402
from services.processing.app.v2.dependencies import get_user_context  # noqa: E402

MAIN_PY = Path(__file__).resolve().parents[2] / "services/processing/app/main.py"

SUPER_ADMIN = {"user_id": "root", "username": "superadmin", "roles": ["admin"],
               "permissions": ["*"], "correlation_id": "test"}


def _ctx(*permissions, user_id="u1"):
    return {"user_id": user_id, "username": "analyst", "roles": ["user"],
            "permissions": list(permissions), "correlation_id": "test"}


# --------------------------------------------------------------------- fakes


class StubRepo:
    """Only `run_pipeline`, keyed by collection, plus a record of every call.

    It raises for a collection named in `fail` because that is the contract the
    analytics repository actually has: an exception, never `[]`.
    """

    def __init__(self, entities=None, docs=None, fail=()):
        self.entities = entities or {}
        self.docs = docs or {}
        self.fail = set(fail)
        self.calls = []

    async def run_pipeline(self, collection, pipeline, max_time_ms=None):
        self.calls.append({"collection": collection, "pipeline": pipeline,
                           "max_time_ms": max_time_ms})
        if collection in self.fail:
            raise PyMongoError(f"boom on {collection}")
        if collection in self.entities:
            return [{"_id": e} for e in self.entities[collection]]
        return self.docs.get(collection, [])

    @property
    def collections(self):
        return [c["collection"] for c in self.calls]


def _demand_docs(*values):
    """Documents shaped like the analytics `$group` output for demand_serve."""
    return [
        {"_id": {"d": "2026-08-01", "b": str(block)}, "ACTUAL_DEMAND": value}
        for block, value in enumerate(values, start=1)
    ]


#: The two plant universes, as counted in the local UAT import.
SUPPLY_PLANTS = [f"SUP{n:02d}" for n in range(34)]
SCHEDULING_PLANTS = [f"SCH{n:02d}" for n in range(53)]


def _client(context=None, repo=None):
    """A minimal app mounting only this router, with the auth dependency overridden.

    The real app puts `get_user_context` behind `AuthMiddleware`; mounting the router
    alone keeps this a test of the routes rather than of the middleware stack.
    """
    app = FastAPI()
    app.include_router(analytics_routes.router, prefix="/api/v2")
    app.dependency_overrides[get_user_context] = lambda: (
        SUPER_ADMIN if context is None else context)
    ctrl.analytics_controller.repository = repo if repo is not None else StubRepo()
    # No Redis in a route test. Left wired, a developer machine with Redis running
    # serves one test's response to the next one and the repository assertions
    # below go quiet — which is the cache working, but not what is under test here.
    ctrl.analytics_controller.cache = None
    return TestClient(app)


@pytest.fixture(autouse=True)
def _reset_controller():
    """The router talks to the module singleton, so every test starts it clean."""
    ctrl.analytics_controller.reset()
    yield
    ctrl.analytics_controller.reset()


def _query_body(**over):
    body = {
        "from_date": "2026-08-01",
        "to_date": "2026-08-01",
        "from_block": 1,
        "to_block": 96,
        "date_grouping": "BLOCK_WISE",
        "datasets": ["ACTUAL_DEMAND"],
    }
    body.update(over)
    return body


# ----------------------------------------------------------------- catalogue


def test_the_dataset_catalogue_lists_absent_datasets_so_the_ui_can_disable_them():
    response = _client().get("/api/v2/analytics/datasets")
    assert response.status_code == 200

    payload = response.json()["data"]
    by_code = {d["code"]: d for d in payload["datasets"]}

    # Every registered code is offered, including the ones with no storage — the UI
    # renders those disabled rather than pretending they do not exist.
    assert set(by_code) == set(registry.DATASETS)
    assert by_code["TAM_TRADED"]["availability"] == "absent"
    assert by_code["ACTUAL_DEMAND"]["availability"] == "live"
    # bid_trade_serve has no GDAM rows yet: selectable, but honest about it.
    assert by_code["PF_TRADED_GDAM"]["availability"] == "empty"


def test_the_catalogue_carries_the_series_cardinality_the_budget_multiplies_by():
    payload = _client().get("/api/v2/analytics/datasets").json()["data"]
    by_code = {d["code"]: d for d in payload["datasets"]}

    assert by_code["ACTUAL_DEMAND"]["series"] == "scalar"
    assert by_code["ACTUAL_DEMAND"]["cardinality_basis"] is None
    assert by_code["DC_PLANT"]["series"] == "per_plant"
    assert by_code["DC_PLANT"]["cardinality_basis"] == "plants"
    # An untouched exchange filter means all three exchanges, so the default
    # multiplier the UI shows before anything is narrowed is 3, not 1.
    assert by_code["ACP_DAM"]["cardinality_basis"] == "exchanges"
    assert by_code["ACP_DAM"]["default_cardinality"] == 3


def test_the_catalogue_flags_what_this_caller_may_not_see_without_hiding_it():
    payload = _client(_ctx("analytics:demand")).get(
        "/api/v2/analytics/datasets").json()["data"]
    by_code = {d["code"]: d for d in payload["datasets"]}

    assert by_code["ACTUAL_DEMAND"]["permitted"] is True
    assert by_code["ACP_DAM"]["permitted"] is False
    # Flagged, not dropped: the server still enforces it on /query, and a dataset
    # that vanishes from the list looks like a missing feature rather than a grant.
    assert "ACP_DAM" in by_code


# ------------------------------------------------------------ plant selector


def test_the_plant_list_comes_from_the_collection_the_dataset_actually_reads():
    repo = StubRepo(entities={"supply_serve": SUPPLY_PLANTS,
                              "serve_dam_scheduling": SCHEDULING_PLANTS})
    client = _client(repo=repo)

    supply = client.get("/api/v2/analytics/plants?dataset=DC_PLANT").json()["data"]
    scheduling = client.get(
        "/api/v2/analytics/plants?dataset=FINAL_SCHEDULE_PLANT").json()["data"]

    assert supply["collection"] == "supply_serve"
    assert scheduling["collection"] == "serve_dam_scheduling"
    # The whole reason this endpoint is dataset-aware: the two universes differ.
    assert supply["count"] == 34
    assert scheduling["count"] == 53
    assert [p["id"] for p in supply["plants"]] == sorted(SUPPLY_PLANTS)
    assert [p["id"] for p in scheduling["plants"]] != [p["id"] for p in supply["plants"]]
    assert repo.collections == ["supply_serve", "serve_dam_scheduling"]


def test_a_second_plant_lookup_is_served_from_memory():
    repo = StubRepo(entities={"supply_serve": SUPPLY_PLANTS})
    client = _client(repo=repo)

    first = client.get("/api/v2/analytics/plants?dataset=DC_PLANT").json()["data"]
    second = client.get("/api/v2/analytics/plants?dataset=DC_PLANT").json()["data"]

    assert first["plants"] == second["plants"]
    # A distinct over serve_dam_scheduling is a collection scan of ~470k documents.
    # Paying it on every dashboard load, for a dropdown that changes when a plant is
    # commissioned, is the kind of self-inflicted load /health used to cause.
    assert repo.collections == ["supply_serve"]
    assert second["cached"] is True


def test_asking_for_the_plants_of_a_dataset_that_has_none_is_a_422_not_an_empty_list():
    repo = StubRepo()
    response = _client(repo=repo).get("/api/v2/analytics/plants?dataset=ACTUAL_DEMAND")

    assert response.status_code == 422
    assert "ACTUAL_DEMAND" in str(response.json()["detail"])
    assert repo.calls == []


def test_an_unknown_dataset_code_on_the_plant_route_is_a_422():
    response = _client().get("/api/v2/analytics/plants?dataset=NOT_A_DATASET")
    assert response.status_code == 422


def test_a_failed_plant_lookup_is_a_503_rather_than_an_empty_dropdown():
    repo = StubRepo(entities={"supply_serve": SUPPLY_PLANTS}, fail=["supply_serve"])
    response = _client(repo=repo).get("/api/v2/analytics/plants?dataset=DC_PLANT")

    # An empty list here disables Apply forever and looks like "this plant has no
    # data". The dashboard has to be able to tell a broken query from an empty one.
    assert response.status_code == 503


# --------------------------------------------------------------- block bands


def test_the_block_bands_route_marks_the_five_undefined_bands_inactive():
    payload = _client().get("/api/v2/analytics/block-bands").json()["data"]
    by_code = {b["code"]: b for b in payload["bands"]}

    assert set(by_code) == set(block_bands.DEFAULT_BANDS)
    assert by_code["INDIVIDUAL"]["active"] is True
    assert by_code["CUSTOM"]["active"] is True
    assert by_code["EVENING_PEAK"]["active"] is False
    # PTC has never supplied the ranges; the UI needs the reason for its tooltip.
    assert by_code["EVENING_PEAK"]["note"]
    assert (payload["min_block"], payload["max_block"]) == (1, 96)


# --------------------------------------------------------------------- query


def test_a_query_nests_its_envelope_under_data():
    repo = StubRepo(docs={"demand_serve": _demand_docs(100.0, 101.0)})
    response = _client(repo=repo).post("/api/v2/analytics/query", json=_query_body())

    assert response.status_code == 200
    body = response.json()
    # The client reads response.data.data; a bare envelope renders nothing.
    assert set(body) == {"data"}
    payload = body["data"]
    assert [s["dataset"] for s in payload["series"]] == ["ACTUAL_DEMAND"]
    assert payload["axes"]["dates"] == ["2026-08-01"]
    assert payload["meta"]["cache"] == "miss"
    assert repo.collections == ["demand_serve"]


def test_an_over_budget_query_is_413_with_the_arithmetic_and_a_way_out():
    repo = StubRepo()
    body = _query_body(
        datasets=["DC_PLANT"],
        plants=SUPPLY_PLANTS,
        from_date="2026-01-01",
        to_date=(date(2026, 1, 1) + timedelta(days=199)).isoformat(),
    )
    response = _client(repo=repo).post("/api/v2/analytics/query", json=body)

    assert response.status_code == 413
    detail = response.json()["detail"]
    assert detail["estimate"]["cells"] == 200 * 96 * 34
    assert detail["estimate"]["band"] == "async_only"
    assert detail["suggestions"], "a refusal with no alternative is a dead end"
    # Refused by arithmetic, before a five-month scan was paid for.
    assert repo.calls == []


def test_an_inline_capped_query_is_served_and_says_so():
    repo = StubRepo(docs={"supply_serve": []})
    # 92 days x 96 blocks x 34 plants = 300,288 cells: past `inline`, inside
    # `inline_capped`. It is served in full and the banner gets the real count.
    body = _query_body(
        datasets=["DC_PLANT"],
        plants=SUPPLY_PLANTS,
        from_date="2026-01-01",
        to_date=(date(2026, 1, 1) + timedelta(days=91)).isoformat(),
    )
    response = _client(repo=repo).post("/api/v2/analytics/query", json=body)

    assert response.status_code == 200
    meta = response.json()["data"]["meta"]
    assert meta["band"] == "inline_capped"
    assert meta["capped"] is True
    assert meta["suggestions"]
    assert repo.collections == ["supply_serve"]


def test_an_unknown_dataset_code_is_rejected_by_the_request_model():
    repo = StubRepo()
    response = _client(repo=repo).post(
        "/api/v2/analytics/query", json=_query_body(datasets=["NOT_A_DATASET"]))

    assert response.status_code == 422
    assert repo.calls == []


def test_a_per_plant_dataset_with_no_plants_is_rejected_before_it_can_fan_out():
    repo = StubRepo()
    response = _client(repo=repo).post(
        "/api/v2/analytics/query", json=_query_body(datasets=["DC_PLANT"], plants=[]))

    assert response.status_code == 422
    assert repo.calls == []


def test_a_dataset_with_no_storage_is_refused_by_name_not_rendered_empty():
    repo = StubRepo()
    response = _client(repo=repo).post(
        "/api/v2/analytics/query", json=_query_body(datasets=["TAM_TRADED"]))

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "TAM_TRADED" in detail["datasets"]
    assert repo.calls == []


def test_a_caller_who_may_see_nothing_gets_an_empty_envelope_not_a_500():
    repo = StubRepo()
    response = _client(_ctx("analytics:demand"), repo=repo).post(
        "/api/v2/analytics/query", json=_query_body(datasets=["ACP_DAM"]))

    # 200 with permission_denied, not 403: dataset grants are per dataset, and the
    # same request carrying one permitted dataset must still serve that one.
    assert response.status_code == 200
    meta = response.json()["data"]["meta"]
    assert meta["permission_denied"] is True
    assert meta["denied_datasets"] == ["ACP_DAM"]
    assert repo.calls == []


def test_the_calling_users_permissions_decide_what_the_query_returns():
    repo = StubRepo(docs={"demand_serve": _demand_docs(100.0)})
    response = _client(_ctx("analytics:demand"), repo=repo).post(
        "/api/v2/analytics/query",
        json=_query_body(datasets=["ACTUAL_DEMAND", "ACP_DAM"]))

    payload = response.json()["data"]
    assert [s["dataset"] for s in payload["series"]] == ["ACTUAL_DEMAND"]
    assert payload["meta"]["denied_datasets"] == ["ACP_DAM"]
    assert repo.collections == ["demand_serve"]


def test_a_failed_source_group_is_a_partial_result_not_a_silent_empty_one():
    repo = StubRepo(fail=["demand_serve"])
    response = _client(repo=repo).post("/api/v2/analytics/query", json=_query_body())

    assert response.status_code == 200
    meta = response.json()["data"]["meta"]
    assert meta["partial"] is True
    assert meta["failed_datasets"] == ["ACTUAL_DEMAND"]


# -------------------------------------------------------------- registration


def test_the_query_cache_the_controller_wires_is_the_async_redis_client():
    ctrl.analytics_controller.reset()
    cache = ctrl.analytics_controller.cache

    # `libs.cache.CacheService` is the *blocking* redis client. Wiring that into an
    # async handler is the defect that makes a login rush 401 the whole platform,
    # and it would be an easy, invisible substitution to make here.
    assert type(cache).__module__.startswith("redis.asyncio")
    ctrl.analytics_controller.reset()


def test_the_router_declares_its_own_prefix_and_tag():
    # main.py calls include_router with no prefix, so the router has to carry it.
    assert analytics_routes.router.prefix == "/analytics"
    assert analytics_routes.router.tags == ["Analytics"]


def test_main_registers_the_router_on_the_live_v2_app():
    source = MAIN_PY.read_text()
    assert "from .v2.api.router.analytics_routes import router as analytics_router" in source
    assert "v2_app.include_router(analytics_router)" in source


def test_the_analytics_path_is_never_excluded_from_authentication():
    # get_user_context returns an anonymous context instead of raising, so the only
    # thing standing between /api/v2/analytics and the public internet is that this
    # path is absent from every exclude list in main.py.
    source = MAIN_PY.read_text()
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith('"') and "analytics" in stripped:
            pytest.fail(f"analytics appears in a main.py path list: {stripped}")
