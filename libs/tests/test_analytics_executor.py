"""Planner, executor and the analytics repository.

Three claims are load-bearing here and each one is silent when it breaks:

1. **Twelve ticked datasets cost four queries, not twelve.** The whole query engine
   is built on collapsing datasets onto their source group. If the planner ever
   emits one plan per dataset the screen still works — it just multiplies the load
   on the single EC2 box by three, and nothing in the response says so.

2. **A failed group is not an empty group.** `GenericRepository.aggregate` logs the
   exception and returns `[]`, which is indistinguishable from "no data in this
   range". For analytics that is the difference between an honest partial-data
   banner and a chart that confidently shows nothing. The analytics repository
   therefore raises, and the executor turns the raise into `failed_datasets` while
   the other groups still return their rows.

3. **Analytics gets its own Motor client.** `MongoClientFactory` keys one client per
   name, so asking for `"processing_service"` would let a five-month sweep exhaust
   the pool the bidding screens depend on. Bidding is the money path.

Mongo is mocked — `.db` is a plain dict of fake collections and the assertions are on
`call_args`, the house convention.
"""
import asyncio
import inspect
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pymongo.errors import ExecutionTimeout, PyMongoError

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services.processing.app.v2.analytics import executor, planner, registry  # noqa: E402
from services.processing.app.v2.analytics.adapters import base as adapter_base  # noqa: E402
from services.processing.app.v2.repository import analytics_repository as repo_mod  # noqa: E402

# Twelve datasets across four source groups — the central claim of the design.
TWELVE = [
    "ACTUAL_DEMAND", "ROSTERING", "MAPE_DAF", "MAPE_IDF",          # demand
    "DC_PLANT", "DECLARED_CAPACITY_TOTAL",                       # supply
    "ACP_DAM", "ACV_DAM", "ACP_RTM",                             # exchange
    "PEAK_DEMAND", "MIN_DEMAND", "AVG_DEMAND",                   # peak
]

FILTERS = {
    "from_date": "2026-08-01",
    "to_date": "2026-08-03",
    "from_block": 1,
    "to_block": 96,
    "date_grouping": "BLOCK_WISE",
}


# --------------------------------------------------------------------- fakes


class StubAdapter:
    """Only what the executor touches: a collection name and `rows_from`."""

    def __init__(self, group, collection):
        self.group = group
        self.serve_collection = collection
        self.rows_from_calls = []

    def rows_from(self, docs, datasets):
        self.rows_from_calls.append((list(docs or []), list(datasets)))
        return [
            adapter_base.CanonicalRow(
                dataset=d.code, date_key=doc["_id"]["d"], block_key=doc["_id"]["b"],
                entity=None, value=doc.get(d.code),
            )
            for doc in docs or []
            for d in datasets
        ]


def stub_plan(group, collection, codes, pipeline=None):
    return {
        "group": group,
        "adapter": StubAdapter(group, collection),
        "datasets": [registry.get(c) for c in codes],
        "pipeline": pipeline if pipeline is not None else [{"$match": {}}],
    }


class RecordingRepo:
    """Records every call, can fail chosen collections, and measures overlap."""

    def __init__(self, docs=None, fail=(), delay=0.005):
        self.docs = docs or {}
        self.fail = set(fail)
        self.delay = delay
        self.calls = []
        self.in_flight = 0
        self.peak = 0

    async def run_pipeline(self, collection, pipeline, max_time_ms=None):
        self.calls.append({"collection": collection, "pipeline": pipeline,
                           "max_time_ms": max_time_ms})
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        try:
            await asyncio.sleep(self.delay)
        finally:
            self.in_flight -= 1
        if collection in self.fail:
            raise PyMongoError(f"boom on {collection}")
        return self.docs.get(collection, [])


class FakeCursor:
    """What Motor's `collection.aggregate()` returns: a lazily iterated cursor.

    `__aiter__` returns `self` and the work happens in `__anext__`, the way Motor's
    own cursor does — not an async generator, so abandoning it part-way (the
    `max_docs` path) behaves like the real thing instead of warning about an
    un-awaited `aclose`.
    """

    def __init__(self, docs, raise_mid_stream=None):
        self.docs = list(docs)
        self.raise_mid_stream = raise_mid_stream
        self._i = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._i < len(self.docs):
            doc = self.docs[self._i]
            self._i += 1
            return doc
        if self.raise_mid_stream:
            raise self.raise_mid_stream
        raise StopAsyncIteration


def fake_collection(docs=None, raises=None, raise_mid_stream=None):
    col = MagicMock()
    if raises is not None:
        col.aggregate = MagicMock(side_effect=raises)
    else:
        col.aggregate = MagicMock(
            return_value=FakeCursor(docs or [], raise_mid_stream))
    return col


# -------------------------------------------------------------- the planner


def test_twelve_datasets_across_four_groups_produce_four_plans():
    plans = planner.plan(TWELVE, FILTERS)
    assert len(plans) == 4, [p["group"] for p in plans]
    assert {p["group"] for p in plans} == {"demand", "supply", "exchange", "peak"}
    assert sum(len(p["datasets"]) for p in plans) == 12


def test_each_plan_carries_only_its_own_groups_datasets():
    for p in planner.plan(TWELVE, FILTERS):
        assert p["datasets"], p["group"]
        for d in p["datasets"]:
            assert isinstance(d, registry.Dataset)
            assert d.group == p["group"]


def test_plan_uses_the_registered_adapter_instance():
    plans = {p["group"]: p for p in planner.plan(TWELVE, FILTERS)}
    for group, p in plans.items():
        assert p["adapter"] is adapter_base.get_adapter(group)


def test_plan_pipeline_is_what_that_groups_adapter_builds():
    """Not a generic pipeline: the supply plan must carry the revision collapse."""
    plans = {p["group"]: p for p in planner.plan(TWELVE, FILTERS)}
    supply = plans["supply"]
    assert supply["pipeline"] == supply["adapter"].build_pipeline(
        datasets=supply["datasets"], filters=FILTERS, ctx=None)
    stages = [next(iter(s)) for s in supply["pipeline"]]
    assert stages[0] == "$match"
    assert "$replaceRoot" in stages, stages
    # demand has no revision collapse to do, but it still drops the copy the
    # archival window leaves in both collections before it aggregates.
    assert [next(iter(s)) for s in plans["demand"]["pipeline"]] == [
        "$match", "$unionWith", "$sort", "$group", "$replaceRoot", "$group"]


def test_importing_the_adapters_package_registers_every_group():
    """The planner resolves groups through `base.ADAPTERS`; an unwired package
    would make every query a KeyError."""
    assert {"demand", "supply", "exchange", "trade", "peak"} <= set(
        adapter_base.ADAPTERS)
    # Every group the registry names must resolve — the real guard.
    from services.processing.app.v2.analytics import registry
    assert {d.group for d in registry.DATASETS.values()} <= set(adapter_base.ADAPTERS)


def test_ctx_reaches_the_adapter():
    ctx = {"plant_count": 3}
    plans = planner.plan(["DC_PLANT"], {**FILTERS, "plants": ["p1"]}, ctx)
    assert plans[0]["pipeline"] == plans[0]["adapter"].build_pipeline(
        datasets=plans[0]["datasets"], filters={**FILTERS, "plants": ["p1"]}, ctx=ctx)


def test_a_derived_dataset_plans_its_dependencies_and_not_itself():
    plans = planner.plan(["OD_UD_QUANTUM"], FILTERS)
    assert len(plans) == 1
    codes = [d.code for d in plans[0]["datasets"]]
    assert codes == ["DRAWAL", "FINAL_SCHEDULE_STATE"]
    assert "OD_UD_QUANTUM" not in codes


def test_a_dependency_ticked_in_its_own_right_is_not_fetched_twice():
    plans = planner.plan(["OD_UD_QUANTUM", "ROSTERING"], FILTERS)
    codes = [d.code for d in plans[0]["datasets"]]
    assert codes.count("ROSTERING") == 1


def test_duplicate_codes_collapse():
    plans = planner.plan(["ROSTERING", "ROSTERING", "ROSTERING"], FILTERS)
    assert [d.code for d in plans[0]["datasets"]] == ["ROSTERING"]


def test_plan_requires_at_least_one_dataset():
    with pytest.raises(ValueError):
        planner.plan([], FILTERS)


def test_unknown_dataset_code_raises():
    with pytest.raises(registry.UnknownDatasetError):
        planner.plan(["NO_SUCH_DATASET"], FILTERS)


def test_an_absent_dataset_is_refused_by_name():
    """`absent` has no storage at all. Dropping it quietly would report success for
    a column the user ticked and will never see."""
    with pytest.raises(planner.UnavailableDatasetError) as excinfo:
        planner.plan(["ACTUAL_DEMAND", "TAM_TRADED"], FILTERS)
    assert "TAM_TRADED" in str(excinfo.value)


def test_an_empty_dataset_is_planned_not_refused():
    """`empty` means the query is legitimate and there are simply no rows yet."""
    plans = planner.plan(["PF_TRADED_GDAM"], FILTERS)
    assert [d.code for d in plans[0]["datasets"]] == ["PF_TRADED_GDAM"]


def test_plan_accepts_dataset_objects_as_well_as_codes():
    plans = planner.plan([registry.get("ROSTERING")], FILTERS)
    assert [d.code for d in plans[0]["datasets"]] == ["ROSTERING"]


def test_plan_group_order_follows_the_request():
    plans = planner.plan(["ACP_DAM", "ROSTERING", "PEAK_DEMAND"], FILTERS)
    assert [p["group"] for p in plans] == ["exchange", "demand", "peak"]


# ------------------------------------------------------------- the executor


@pytest.mark.asyncio
async def test_execute_returns_rows_from_every_group():
    plans = [
        stub_plan("demand", "demand_serve", ["ROSTERING"]),
        stub_plan("peak", "peak_demand_serve", ["PEAK_DEMAND"]),
    ]
    repo = RecordingRepo(docs={
        "demand_serve": [{"_id": {"d": "2026-08-01", "b": "1"}, "ROSTERING": 10.0}],
        "peak_demand_serve": [{"_id": {"d": "2026-08-01", "b": "89"},
                               "PEAK_DEMAND": 900.0}],
    })
    result = await executor.execute(plans, repo)

    assert isinstance(result, executor.ExecutionResult)
    assert [(r["dataset"], r["value"]) for r in result.rows] == [
        ("ROSTERING", 10.0), ("PEAK_DEMAND", 900.0)]
    assert result.failed_groups == []
    assert result.failed_datasets == []


@pytest.mark.asyncio
async def test_rows_come_from_the_adapter_with_that_plans_datasets():
    plans = [stub_plan("demand", "demand_serve", ["ROSTERING", "ACTUAL_DEMAND"])]
    repo = RecordingRepo(docs={"demand_serve": [
        {"_id": {"d": "2026-08-01", "b": "1"}, "ROSTERING": 1.0, "ACTUAL_DEMAND": None}]})
    await executor.execute(plans, repo)

    docs, datasets = plans[0]["adapter"].rows_from_calls[0]
    assert len(docs) == 1
    assert [d.code for d in datasets] == ["ROSTERING", "ACTUAL_DEMAND"]


@pytest.mark.asyncio
async def test_a_missing_value_survives_as_none():
    plans = [stub_plan("demand", "demand_serve", ["ACTUAL_DEMAND"])]
    repo = RecordingRepo(docs={"demand_serve": [
        {"_id": {"d": "2026-08-01", "b": "1"}, "ACTUAL_DEMAND": None}]})
    result = await executor.execute(plans, repo)
    assert result.rows[0]["value"] is None


@pytest.mark.asyncio
async def test_max_time_ms_is_passed_on_every_call():
    plans = [stub_plan("demand", "demand_serve", ["ROSTERING"]),
             stub_plan("peak", "peak_demand_serve", ["PEAK_DEMAND"]),
             stub_plan("exchange", "exchange_serve", ["ACP_DAM"])]
    repo = RecordingRepo()
    await executor.execute(plans, repo, max_time_ms=7_500)
    assert [c["max_time_ms"] for c in repo.calls] == [7_500, 7_500, 7_500]


@pytest.mark.asyncio
async def test_max_time_ms_defaults_to_twenty_seconds():
    repo = RecordingRepo()
    await executor.execute([stub_plan("demand", "demand_serve", ["ROSTERING"])], repo)
    assert repo.calls[0]["max_time_ms"] == 20_000


@pytest.mark.asyncio
async def test_each_plan_queries_its_own_collection_with_its_own_pipeline():
    pipeline = [{"$match": {"time_block": 1}}]
    plans = [stub_plan("demand", "demand_serve", ["ROSTERING"], pipeline)]
    repo = RecordingRepo()
    await executor.execute(plans, repo)
    assert repo.calls[0]["collection"] == "demand_serve"
    assert repo.calls[0]["pipeline"] is pipeline


@pytest.mark.asyncio
async def test_groups_run_concurrently_under_the_semaphore():
    plans = [stub_plan(f"g{i}", f"c{i}", ["ROSTERING"]) for i in range(5)]
    repo = RecordingRepo()
    await executor.execute(plans, repo, concurrency=2)
    assert repo.peak == 2, repo.peak
    assert len(repo.calls) == 5


@pytest.mark.asyncio
async def test_default_concurrency_runs_groups_in_parallel():
    plans = [stub_plan(f"g{i}", f"c{i}", ["ROSTERING"]) for i in range(6)]
    repo = RecordingRepo()
    await executor.execute(plans, repo)
    assert 1 < repo.peak <= 4, repo.peak


@pytest.mark.asyncio
async def test_one_group_failing_does_not_fail_the_request():
    plans = [
        stub_plan("demand", "demand_serve", ["ROSTERING"]),
        stub_plan("exchange", "exchange_serve", ["ACP_DAM", "ACV_DAM"]),
    ]
    repo = RecordingRepo(
        docs={"demand_serve": [{"_id": {"d": "2026-08-01", "b": "1"}, "ROSTERING": 5.0}]},
        fail=["exchange_serve"],
    )
    result = await executor.execute(plans, repo)

    assert [r["dataset"] for r in result.rows] == ["ROSTERING"]
    assert result.failed_datasets == ["ACP_DAM", "ACV_DAM"]
    assert [f["group"] for f in result.failed_groups] == ["exchange"]
    assert "boom on exchange_serve" in result.failed_groups[0]["error"]


@pytest.mark.asyncio
async def test_a_failure_is_distinguishable_from_an_empty_result():
    """Both return no rows. Only one of them may be reported as success."""
    empty = [stub_plan("demand", "demand_serve", ["ROSTERING"])]
    empty_result = await executor.execute(empty, RecordingRepo())
    assert empty_result.rows == []
    assert empty_result.failed_datasets == []
    assert empty_result.failed_groups == []

    failed = [stub_plan("demand", "demand_serve", ["ROSTERING"])]
    failed_result = await executor.execute(
        failed, RecordingRepo(fail=["demand_serve"]))
    assert failed_result.rows == []
    assert failed_result.failed_datasets == ["ROSTERING"]
    assert failed_result.failed_groups


@pytest.mark.asyncio
async def test_every_group_failing_still_returns_a_result_not_an_exception():
    plans = [stub_plan("demand", "demand_serve", ["ROSTERING"]),
             stub_plan("peak", "peak_demand_serve", ["PEAK_DEMAND"])]
    repo = RecordingRepo(fail=["demand_serve", "peak_demand_serve"])
    result = await executor.execute(plans, repo)
    assert result.rows == []
    assert result.failed_datasets == ["ROSTERING", "PEAK_DEMAND"]
    assert len(result.failed_groups) == 2


@pytest.mark.asyncio
async def test_no_plans_touches_the_repository_at_all():
    repo = RecordingRepo()
    result = await executor.execute([], repo)
    assert (result.rows, result.failed_groups, result.failed_datasets) == ([], [], [])
    assert repo.calls == []


@pytest.mark.asyncio
async def test_cancellation_propagates_rather_than_being_logged_as_a_failure():
    class Cancelling:
        async def run_pipeline(self, collection, pipeline, max_time_ms=None):
            raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await executor.execute(
            [stub_plan("demand", "demand_serve", ["ROSTERING"])], Cancelling())


def test_execution_result_defaults_are_independent():
    a, b = executor.ExecutionResult(), executor.ExecutionResult()
    a.rows.append("x")
    assert b.rows == []


# ----------------------------------------------------------- the repository


def _repo(collections):
    repo = repo_mod.AnalyticsRepository()
    repo.db = collections
    return repo


@pytest.mark.asyncio
async def test_run_pipeline_returns_the_documents():
    docs = [{"_id": {"d": "2026-08-01", "b": "1"}, "ROSTERING": 12.5}]
    col = fake_collection(docs)
    result = await _repo({"demand_serve": col}).run_pipeline(
        "demand_serve", [{"$match": {}}])
    assert result == docs


@pytest.mark.asyncio
async def test_run_pipeline_passes_max_time_ms_and_allow_disk_use():
    col = fake_collection([])
    pipeline = [{"$match": {}}]
    await _repo({"demand_serve": col}).run_pipeline(
        "demand_serve", pipeline, max_time_ms=9_000)
    args, kwargs = col.aggregate.call_args
    assert args[0] is pipeline
    assert kwargs["maxTimeMS"] == 9_000
    assert kwargs["allowDiskUse"] is True


@pytest.mark.asyncio
async def test_run_pipeline_max_time_ms_defaults_from_settings():
    col = fake_collection([])
    await _repo({"demand_serve": col}).run_pipeline("demand_serve", [{"$match": {}}])
    assert col.aggregate.call_args.kwargs["maxTimeMS"] == \
        repo_mod.settings.ANALYTICS_QUERY_MAX_TIME_MS


@pytest.mark.asyncio
async def test_run_pipeline_raises_instead_of_returning_an_empty_list():
    """The whole point of not inheriting `GenericRepository.aggregate`."""
    col = fake_collection(raises=PyMongoError("connection reset"))
    with pytest.raises(PyMongoError):
        await _repo({"demand_serve": col}).run_pipeline(
            "demand_serve", [{"$match": {}}])


@pytest.mark.asyncio
async def test_a_timeout_mid_stream_also_raises():
    col = fake_collection([{"_id": 1}], raise_mid_stream=ExecutionTimeout("maxTimeMS"))
    with pytest.raises(ExecutionTimeout):
        await _repo({"demand_serve": col}).run_pipeline(
            "demand_serve", [{"$match": {}}])


@pytest.mark.asyncio
async def test_the_inherited_aggregate_still_swallows_and_that_is_the_contrast():
    """Pins the defect this repository exists to avoid, so a future refactor that
    routes analytics back through `aggregate()` fails here."""
    col = fake_collection(raises=PyMongoError("connection reset"))
    assert await _repo({"demand_serve": col}).aggregate(
        "demand_serve", [{"$match": {}}]) == []


@pytest.mark.asyncio
async def test_max_docs_raises_rather_than_truncating_silently():
    docs = [{"_id": i} for i in range(5)]
    with pytest.raises(repo_mod.ResultTooLargeError):
        await _repo({"demand_serve": fake_collection(docs)}).run_pipeline(
            "demand_serve", [{"$match": {}}], max_docs=2)


def test_analytics_uses_its_own_motor_client(monkeypatch):
    """Sharing `processing_service` would let a five-month sweep exhaust the pool
    the bidding screens need."""
    from libs.database import mongo_client_factory

    seen = {}

    def _fake(uri, client_name="default", **kwargs):
        seen["uri"], seen["client_name"], seen["kwargs"] = uri, client_name, kwargs
        return MagicMock()

    monkeypatch.setattr(mongo_client_factory, "get_motor_client", _fake)
    repo_mod.AnalyticsRepository().client

    assert seen["client_name"] == repo_mod.ANALYTICS_CLIENT_NAME
    assert seen["client_name"] == "processing_analytics"
    assert seen["client_name"] != "processing_service"
    assert seen["kwargs"]["maxPoolSize"] >= 1


def test_repository_does_no_io_in_its_constructor(monkeypatch):
    """The lazy properties exist to avoid the Motor event-loop misbind."""
    from libs.database import mongo_client_factory

    def _boom(*a, **kw):
        raise AssertionError("constructed a client at __init__ time")

    monkeypatch.setattr(mongo_client_factory, "get_motor_client", _boom)
    repo_mod.AnalyticsRepository()


def test_run_pipeline_signature_matches_the_executor_call():
    sig = inspect.signature(repo_mod.AnalyticsRepository.run_pipeline)
    assert list(sig.parameters)[:4] == ["self", "collection", "pipeline", "max_time_ms"]
