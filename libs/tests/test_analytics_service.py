"""The analytics service layer: permissions, admission control, cache.

Four claims are load-bearing here, and three of them fail silently if they break.

1. **A denied dataset is dropped, not a 403 for the whole request.** Dataset access
   is per dataset — `analytics:market` and `analytics:demand` are different grants —
   so a user who ticks six datasets and may see five must get five, with the sixth
   named in `meta.denied_datasets`. Returning a blanket 403 would make a legitimate
   dashboard unusable; returning all six would be the authorization hole. There is
   no per-permission route guard anywhere in the processing service to copy, and a
   route-level guard could not express a per-dataset rule anyway, so the check lives
   here and this test is the only thing that proves it happens at all.

2. **An over-budget request never reaches MongoDB.** The cell budget is arithmetic
   done before any database call; if the service consults it after planning, a
   refused query has already cost a five-month scan. Asserted by the repository
   mock recording zero calls.

3. **A cache hit skips execution entirely.** Ten analysts pulling "last month, all
   markets" on a Monday must cost one aggregation, not ten — this cache is the
   single biggest protection the single EC2 box has. A hit that still runs the
   pipeline looks identical from the outside.

4. **A past range is immutable, so it earns the long TTL.** A range that includes
   today does not: blocks are still being filled in, and serving a stale copy for a
   day would show an analyst a half-empty day and no reason why.

Mongo is mocked — the repository is a stub recording `run_pipeline` calls, the house
convention.
"""
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from pymongo.errors import PyMongoError

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services.processing.app.core.config import settings  # noqa: E402
from services.processing.app.v2.analytics import budget  # noqa: E402
from services.processing.app.v2.analytics.models import AnalyticsQueryRequest  # noqa: E402
from services.processing.app.v2.services import analytics_service as svc  # noqa: E402

try:  # pragma: no cover - zoneinfo is stdlib on 3.9+, tzdata may not be installed
    from zoneinfo import ZoneInfo
    _TZ = ZoneInfo(settings.ANALYTICS_TIMEZONE)
except Exception:  # pragma: no cover
    _TZ = None


def _today() -> date:
    """Today in the configured trading timezone, computed independently of the
    service so the TTL assertions are not circular."""
    if _TZ is None:  # pragma: no cover
        return datetime.utcnow().date()
    return datetime.now(_TZ).date()


# --------------------------------------------------------------------- fakes


class RecordingRepo:
    """Only what the executor touches, plus a record of every call."""

    def __init__(self, docs=None, fail=(), parameters=None):
        self.docs = docs or {}
        self.fail = set(fail)
        self.calls = []
        self.parameters = dict(parameters or {})
        self.parameter_reads = 0

    async def get_parameters(self, keys, collection="parameters"):
        self.parameter_reads += 1
        return {k: v for k, v in self.parameters.items() if k in set(keys)}

    async def run_pipeline(self, collection, pipeline, max_time_ms=None):
        self.calls.append({"collection": collection, "pipeline": pipeline,
                           "max_time_ms": max_time_ms})
        if collection in self.fail:
            raise PyMongoError(f"boom on {collection}")
        return self.docs.get(collection, [])

    @property
    def collections(self):
        return [c["collection"] for c in self.calls]


class FakeCache:
    """An async get/set cache, the shape the service expects to be handed."""

    def __init__(self, store=None):
        self.store = dict(store or {})
        self.gets = []
        self.sets = []

    async def get(self, key):
        self.gets.append(key)
        return self.store.get(key)

    async def set(self, key, value, ttl=None):
        self.sets.append({"key": key, "value": value, "ttl": ttl})
        self.store[key] = value
        return True


def _demand_docs(*values):
    """Documents shaped like the analytics `$group` output for demand_serve."""
    return [
        {"_id": {"d": "2026-08-01", "b": str(block)}, "ACTUAL_DEMAND": value}
        for block, value in enumerate(values, start=1)
    ]


@pytest.fixture(autouse=True)
def _fresh_band_cache():
    # The band definitions are cached on the class for 30 s; a test that seeds
    # ranges must not see (or leave behind) another test's parameters.
    svc.AnalyticsService.forget_bands()
    yield
    svc.AnalyticsService.forget_bands()


def _request(**over):
    base = {
        "from_date": date(2026, 8, 1),
        "to_date": date(2026, 8, 1),
        "from_block": 1,
        "to_block": 96,
        "date_grouping": "BLOCK_WISE",
        "datasets": ["ACTUAL_DEMAND"],
    }
    base.update(over)
    return AnalyticsQueryRequest(**base)


SUPER_ADMIN = {"user_id": "root", "username": "superadmin", "roles": ["admin"],
               "permissions": ["*"]}


def _ctx(*permissions, user_id="u1"):
    return {"user_id": user_id, "username": "analyst", "roles": ["user"],
            "permissions": list(permissions)}


# --------------------------------------------------------- permission filter


def test_a_user_without_analytics_market_keeps_every_other_dataset():
    allowed, denied = svc.AnalyticsService.filter_by_permission(
        ["ACTUAL_DEMAND", "ACP_DAM", "DC_PLANT", "ACV_RTM"],
        ["analytics:demand", "analytics:supply"],
    )
    assert allowed == ["ACTUAL_DEMAND", "DC_PLANT"]
    assert denied == ["ACP_DAM", "ACV_RTM"]


def test_a_super_admin_passes_every_dataset():
    codes = ["ACTUAL_DEMAND", "ACP_DAM", "DC_PLANT", "OD_UD_AMOUNT"]
    allowed, denied = svc.AnalyticsService.filter_by_permission(codes, ["*"])
    assert allowed == codes
    assert denied == []

    # ...and the three ways a context can say "super admin" all reach that set.
    for context in (
        {"permissions": ["*"]},
        {"permissions": [], "roles": ["admin"]},
        {"permissions": [], "user": {"is_super_admin": True}},
    ):
        perms = svc.AnalyticsService.permissions_for(context)
        assert svc.AnalyticsService.filter_by_permission(codes, perms) == (codes, [])


def test_a_resource_wildcard_grants_the_whole_analytics_namespace():
    codes = ["ACTUAL_DEMAND", "ACP_DAM"]
    allowed, denied = svc.AnalyticsService.filter_by_permission(
        codes, ["analytics:*"])
    assert (allowed, denied) == (codes, [])


@pytest.mark.asyncio
async def test_query_drops_the_denied_dataset_and_still_serves_the_rest():
    repo = RecordingRepo(docs={"demand_serve": _demand_docs(100.0, 101.0)})
    service = svc.AnalyticsService(repository=repo)

    response = await service.query(
        _request(datasets=["ACTUAL_DEMAND", "ACP_DAM"]),
        _ctx("analytics:demand"),
    )

    # Never a blanket 403: the permitted dataset is served.
    assert [s["dataset"] for s in response["series"]] == ["ACTUAL_DEMAND"]
    assert response["meta"]["denied_datasets"] == ["ACP_DAM"]
    # And the exchange group was never queried at all.
    assert repo.collections == ["demand_serve"]


@pytest.mark.asyncio
async def test_a_request_with_nothing_permitted_returns_an_empty_envelope_not_rows():
    repo = RecordingRepo()
    service = svc.AnalyticsService(repository=repo)

    response = await service.query(
        _request(datasets=["ACP_DAM", "ACV_DAM"]), _ctx("analytics:demand"))

    assert response["series"] == []
    assert response["meta"]["denied_datasets"] == ["ACP_DAM", "ACV_DAM"]
    assert response["meta"]["permission_denied"] is True
    assert repo.calls == []


# ------------------------------------------------------------------ cache key


def test_cache_key_is_stable_under_request_key_order():
    ordered = AnalyticsQueryRequest(**{
        "from_date": date(2026, 8, 1), "to_date": date(2026, 8, 5),
        "from_block": 1, "to_block": 96, "date_grouping": "BLOCK_WISE",
        "datasets": ["ACTUAL_DEMAND"], "plants": ["A", "B"],
    })
    shuffled = AnalyticsQueryRequest(**{
        "datasets": ["ACTUAL_DEMAND"], "to_block": 96, "plants": ["B", "A"],
        "date_grouping": "BLOCK_WISE", "to_date": date(2026, 8, 5),
        "from_block": 1, "from_date": date(2026, 8, 1),
    })
    service = svc.AnalyticsService(repository=RecordingRepo())
    assert service.cache_key(ordered) == service.cache_key(shuffled)


def test_cache_key_changes_when_any_filter_value_changes():
    service = svc.AnalyticsService(repository=RecordingRepo())
    baseline = service.cache_key(_request())
    mutations = {
        "from_date": date(2026, 7, 31),
        "to_date": date(2026, 8, 2),
        "from_block": 2,
        "to_block": 95,
        "date_grouping": "DATE_WISE",
        "datasets": ["ROSTERING"],
        "plants": ["PLANT_A"],
        "exchanges": ["IEX"],
    }
    keys = {name: service.cache_key(_request(**{name: value}))
            for name, value in mutations.items()}
    for name, key in keys.items():
        assert key != baseline, f"{name} did not change the cache key"
    assert len(set(keys.values())) == len(keys), "two filters collide on one key"

    # A block band change has to change it too, and so does the custom range list.
    custom = service.cache_key(
        _request(block_band="CUSTOM", custom_blocks=[[1, 48], [49, 96]]))
    other = service.cache_key(
        _request(block_band="CUSTOM", custom_blocks=[[1, 24], [25, 96]]))
    assert baseline != custom != other


# ------------------------------------------------------------------- the TTL


def test_a_fully_past_range_earns_the_long_ttl_and_today_does_not():
    service = svc.AnalyticsService(repository=RecordingRepo())
    today = _today()

    past = _request(from_date=today - timedelta(days=30), to_date=today - timedelta(days=1))
    assert service.is_fully_past(past) is True
    assert service.cache_ttl(past) == settings.ANALYTICS_CACHE_TTL_PAST_SECONDS

    includes_today = _request(from_date=today - timedelta(days=30), to_date=today)
    assert service.is_fully_past(includes_today) is False
    assert service.cache_ttl(includes_today) == settings.ANALYTICS_CACHE_TTL_SECONDS

    tomorrow = _request(from_date=today, to_date=today + timedelta(days=1))
    assert service.is_fully_past(tomorrow) is False
    assert service.cache_ttl(tomorrow) == settings.ANALYTICS_CACHE_TTL_SECONDS


# -------------------------------------------------------- admission control


@pytest.mark.asyncio
async def test_an_over_budget_request_is_refused_before_mongo_is_touched():
    repo = RecordingRepo()
    service = svc.AnalyticsService(repository=repo)
    plants = [f"PLANT_{i:02d}" for i in range(40)]

    with pytest.raises(svc.BudgetExceeded) as exc:
        await service.query(
            _request(from_date=date(2026, 1, 1), to_date=date(2026, 5, 31),
                     datasets=["DC_PLANT"], plants=plants),
            SUPER_ADMIN,
        )

    assert repo.calls == [], "a refused query still ran an aggregation"
    assert exc.value.estimate.band == budget.BAND_ASYNC_ONLY
    assert exc.value.cells == 151 * 96 * 40
    assert exc.value.suggestions, "413 must carry something the user can act on"
    kinds = {s["kind"] for s in exc.value.suggestions}
    assert {"coarser_blocks", "coarser_dates", "fewer_plants"} <= kinds


@pytest.mark.asyncio
async def test_an_inline_capped_request_is_served_and_says_so():
    # 96 blocks x 60 days x 30 plants = 172,800 cells: over inline, under capped.
    repo = RecordingRepo()
    service = svc.AnalyticsService(repository=repo)
    plants = [f"PLANT_{i:02d}" for i in range(30)]

    response = await service.query(
        _request(from_date=date(2026, 6, 1), to_date=date(2026, 7, 30),
                 datasets=["DC_PLANT"], plants=plants),
        SUPER_ADMIN,
    )

    assert response["meta"]["band"] == budget.BAND_INLINE_CAPPED
    assert response["meta"]["estimated_cells"] == 60 * 96 * 30
    assert repo.collections == ["supply_serve"]


# ----------------------------------------------------------------- the cache


@pytest.mark.asyncio
async def test_a_cache_hit_skips_execution_entirely():
    payload = {
        "axes": {"dates": ["2026-08-01"], "blocks": [1]},
        "series": [{"dataset": "ACTUAL_DEMAND", "label": "Actual Demand",
                    "unit": "MW", "entity": None, "values": [[42.0]],
                    "quality": "complete"}],
        "meta": {"cells": 1, "truncated": False, "missing_datasets": [],
                 "failed_datasets": [], "generated_at": "2026-08-01T00:00:00+00:00"},
    }
    request = _request()
    service = svc.AnalyticsService(repository=RecordingRepo(), cache=FakeCache())
    key = service.cache_key(request)
    service.cache.store[key] = json.dumps(payload)

    response = await service.query(request, SUPER_ADMIN)

    assert service.repository.calls == [], "a cache hit still queried MongoDB"
    assert response["series"] == payload["series"]
    assert response["meta"]["cache"] == "hit"


@pytest.mark.asyncio
async def test_a_miss_stores_the_result_under_the_ttl_the_range_deserves():
    today = _today()
    repo = RecordingRepo(docs={"demand_serve": _demand_docs(100.0)})
    cache = FakeCache()
    service = svc.AnalyticsService(repository=repo, cache=cache)

    request = _request(from_date=today - timedelta(days=3),
                       to_date=today - timedelta(days=2))
    response = await service.query(request, SUPER_ADMIN)

    assert response["meta"]["cache"] == "miss"
    assert len(cache.sets) == 1
    assert cache.sets[0]["key"] == service.cache_key(request)
    assert cache.sets[0]["ttl"] == settings.ANALYTICS_CACHE_TTL_PAST_SECONDS
    # Stored as JSON text so a plain Redis client and libs.cache both work.
    assert json.loads(cache.sets[0]["value"])["series"][0]["dataset"] == "ACTUAL_DEMAND"


@pytest.mark.asyncio
async def test_a_broken_cache_never_breaks_the_query():
    class ExplodingCache:
        async def get(self, key):
            raise RuntimeError("redis is down")

        async def set(self, key, value, ttl=None):
            raise RuntimeError("redis is still down")

    repo = RecordingRepo(docs={"demand_serve": _demand_docs(100.0)})
    service = svc.AnalyticsService(repository=repo, cache=ExplodingCache())

    response = await service.query(_request(), SUPER_ADMIN)

    assert [s["dataset"] for s in response["series"]] == ["ACTUAL_DEMAND"]
    assert response["meta"]["cache"] == "miss"


# ------------------------------------------------------------- partial data


@pytest.mark.asyncio
async def test_a_failed_group_is_reported_rather_than_rendered_as_empty():
    repo = RecordingRepo(docs={"demand_serve": _demand_docs(100.0)},
                         fail={"exchange_serve"})
    service = svc.AnalyticsService(repository=repo)

    response = await service.query(
        _request(datasets=["ACTUAL_DEMAND", "ACP_DAM"]), SUPER_ADMIN)

    assert response["meta"]["failed_datasets"] == ["ACP_DAM"]
    assert response["meta"]["partial"] is True
    failed = [s for s in response["series"] if s["dataset"] == "ACP_DAM"]
    assert failed and failed[0]["quality"] == "failed"
    # A failed group must not be cached as if it were a good answer.
    assert response["meta"]["cache"] == "miss"


@pytest.mark.asyncio
async def test_a_partial_result_is_not_written_to_the_cache():
    repo = RecordingRepo(docs={"demand_serve": _demand_docs(100.0)},
                         fail={"exchange_serve"})
    cache = FakeCache()
    service = svc.AnalyticsService(repository=repo, cache=cache)

    await service.query(_request(datasets=["ACTUAL_DEMAND", "ACP_DAM"]), SUPER_ADMIN)

    assert cache.sets == [], "a failed group was cached as a successful answer"


# --------------------------------------------------------------- estimate API


def test_estimate_only_costs_no_database_call():
    repo = RecordingRepo()
    service = svc.AnalyticsService(repository=repo)
    estimate = service.estimate_only(
        _request(from_date=date(2026, 8, 1), to_date=date(2026, 8, 31)))
    assert estimate.cells == 31 * 96 * 1
    assert estimate.band == budget.BAND_INLINE
    assert repo.calls == []


def test_the_exchange_series_count_defaults_to_every_exchange():
    service = svc.AnalyticsService(repository=RecordingRepo())
    ctx = service.context_for(_request(datasets=["ACP_DAM"]))
    assert ctx["exchange_count"] == svc.DEFAULT_EXCHANGE_COUNT
    narrowed = service.context_for(_request(datasets=["ACP_DAM"], exchanges=["IEX"]))
    assert narrowed["exchange_count"] == 1


# ------------------------------------------ fixed grouping for roll-up datasets

def test_a_weekly_rollup_forces_week_wise_regardless_of_the_users_choice():
    """A weekly forecast viewed block-wise is meaningless."""
    req = svc._as_request(_request(datasets=["WEEKLY_FORECAST"], date_grouping="BLOCK_WISE"))
    assert req.date_grouping == "WEEK_WISE"


def test_a_monthly_rollup_forces_month_wise():
    req = svc._as_request(_request(datasets=["MONTHLY_MAPE"], date_grouping="DATE_WISE"))
    assert req.date_grouping == "MONTH_WISE"


def test_an_ordinary_request_keeps_the_users_grouping():
    req = svc._as_request(_request(datasets=["ACTUAL_DEMAND"], date_grouping="DATE_WISE"))
    assert req.date_grouping == "DATE_WISE"


def test_mixing_a_rollup_with_an_ordinary_dataset_rolls_the_whole_request_up():
    req = svc._as_request(_request(datasets=["ACTUAL_DEMAND", "WEEKLY_FORECAST"],
                                   date_grouping="BLOCK_WISE"))
    assert req.date_grouping == "WEEK_WISE"
    grouping, by = svc.forced_grouping(req.datasets)
    assert grouping == "WEEK_WISE"
    assert by == ["WEEKLY_FORECAST"]


def test_mixing_weekly_and_monthly_rollups_is_refused():
    with pytest.raises(svc.FixedGroupingConflict) as exc:
        svc._as_request(_request(datasets=["WEEKLY_FORECAST", "MONTHLY_FORECAST"]))
    msg = str(exc.value)
    # Human words in the message — a user does not know what MONTHLY_MAPE_IDF is.
    assert "weekly" in msg.lower() and "monthly" in msg.lower()
    assert "Weekly Forecast" in msg
    assert "Monthly Forecast" in msg
    assert "WEEKLY_FORECAST" not in msg
    # The codes travel in a structured field for the client's own logic.
    assert exc.value.groups == {"WEEK_WISE": ["WEEKLY_FORECAST"],
                                "MONTH_WISE": ["MONTHLY_FORECAST"]}


def test_forced_grouping_reaches_the_cache_key():
    """The same roll-up asked with two different user groupings is ONE cache entry."""
    s = svc.AnalyticsService(repository=object(), cache=None)
    a = s.cache_key(_request(datasets=["WEEKLY_FORECAST"], date_grouping="BLOCK_WISE"))
    b = s.cache_key(_request(datasets=["WEEKLY_FORECAST"], date_grouping="DATE_WISE"))
    assert a == b


def test_forced_grouping_reaches_the_budget():
    """31 days block-wise would be 2,976 cells; forced weekly it is the ISO weeks the
    range touches — Sat 1 Aug 2026 is W31 and Mon 31 Aug is W36, so SIX, not ceil(31/7)=5.
    The engine keys on the ISO week, so the estimate must count the same way."""
    s = svc.AnalyticsService(repository=object(), cache=None)
    est = s.estimate_only(_request(datasets=["WEEKLY_FORECAST"], date_grouping="BLOCK_WISE",
                                   from_date=date(2026, 8, 1), to_date=date(2026, 8, 31)))
    assert est.date_buckets == 6


def test_the_page_grant_the_menu_rbac_mints_unlocks_every_dataset():
    # Auth derives role permissions from menu rows, so a role that holds the
    # Analytics Dashboard menu carries `analytics_dashboard:read` (and usually the
    # parent's `analytics:read`) — never the per-group strings in the registry.
    # PTC's page model says page access is the whole grant.
    codes = ["ACTUAL_DEMAND", "ACP_DAM", "DC_PLANT", "OD_UD_AMOUNT"]
    for context in (
        {"permissions": ["analytics_dashboard:read"]},
        {"permissions": ["analytics:read"]},
        {"permissions": ["reports:read", "analytics_dashboard:read"]},
        {"user": {"permissions": ["analytics_dashboard:write"]}},
    ):
        perms = svc.AnalyticsService.permissions_for(context)
        assert "analytics:*" in perms
        assert svc.AnalyticsService.filter_by_permission(codes, perms) == (codes, [])


def test_a_role_without_the_page_still_gets_nothing():
    # The exact shape of a real `user`-role token: 92 grants, none of them analytics.
    codes = ["ACTUAL_DEMAND", "ACP_DAM"]
    perms = svc.AnalyticsService.permissions_for(
        {"permissions": ["reports:read", "forecast:read", "dam_single_bid:write"],
         "roles": ["user"]})
    assert "analytics:*" not in perms and "*" not in perms
    assert svc.AnalyticsService.filter_by_permission(codes, perms) == ([], codes)


# ------------------------------------------------------------ block bands


@pytest.mark.asyncio
async def test_a_named_band_takes_its_ranges_from_masters_parameters():
    repo = RecordingRepo(docs={"demand_serve": _demand_docs(100.0, 101.0)},
                         parameters={"analytics_band_peak": "65-72, 81-88"})
    service = svc.AnalyticsService(repository=repo)

    stamped = await service.with_band(_request(block_band="PEAK"))
    assert stamped.block_band_ranges == [[65, 72], [81, 88]]

    # ...and the planner keys the $group on those ranges, not on the block.
    await service.query(_request(block_band="PEAK"), _ctx("analytics:demand"))
    pipeline = json.dumps(repo.calls[-1]["pipeline"], default=str)
    assert '"then": "PEAK"' in pipeline and '"$gte": ["$time_block", 65]' in pipeline


@pytest.mark.asyncio
async def test_a_named_band_without_ranges_is_refused_with_the_parameter_named():
    service = svc.AnalyticsService(repository=RecordingRepo(parameters={}))
    with pytest.raises(svc.BandInactive) as exc:
        await service.with_band(_request(block_band="DAY_PEAK"))
    assert exc.value.parameter_key == "analytics_band_day_peak"
    assert "Masters" in str(exc.value)
    assert isinstance(exc.value, ValueError)   # the routes map ValueError → 422


@pytest.mark.asyncio
async def test_individual_and_custom_never_depend_on_parameters():
    class Broken(RecordingRepo):
        async def get_parameters(self, keys, collection="parameters"):
            raise RuntimeError("parameters collection is down")

    service = svc.AnalyticsService(repository=Broken(docs={"demand_serve": []}))
    req = await service.with_band(_request())
    assert req.block_band == "INDIVIDUAL" and req.block_band_ranges == []
    req = await service.with_band(_request(block_band="CUSTOM", custom_blocks=[[1, 8]]))
    assert req.block_band == "CUSTOM"


@pytest.mark.asyncio
async def test_band_parameters_are_read_once_per_ttl_not_per_request():
    repo = RecordingRepo(parameters={"analytics_band_peak": "65-88"})
    service = svc.AnalyticsService(repository=repo)
    for _ in range(5):
        await service.with_band(_request(block_band="PEAK"))
    assert repo.parameter_reads == 1
    await service.bands(fresh=True)
    assert repo.parameter_reads == 2


@pytest.mark.asyncio
async def test_changing_a_band_definition_changes_the_cache_key():
    repo = RecordingRepo(parameters={"analytics_band_peak": "65-88"})
    service = svc.AnalyticsService(repository=repo)
    before = service.cache_key(await service.with_band(_request(block_band="PEAK")))

    repo.parameters["analytics_band_peak"] = "61-88"
    svc.AnalyticsService.forget_bands()
    after = service.cache_key(await service.with_band(_request(block_band="PEAK")))
    assert before != after
    # ...while a plain request is unaffected by what the parameters say.
    assert service.cache_key(_request()) == service.cache_key(_request())


# ------------------------------------------------- all plants by default (2026-09-18)
#
# The per-plant datasets lost their picker: an empty `plants` list means every plant.
# The resolution has to happen BEFORE the budget prices the request, or 34 series get
# priced as one and a request far over the inline cap is waved through — a silent
# failure, because the response looks fine until the file is enormous.

def _plant_repo(ids=("ALPHA", "BRAVO", "CHARLIE")):
    return RecordingRepo(docs={"supply_serve": [{"_id": i} for i in ids]})


@pytest.mark.asyncio
async def test_an_empty_plant_list_is_resolved_to_every_plant():
    service = svc.AnalyticsService(repository=_plant_repo())
    req = await service.with_plants(_request(datasets=["DC_PLANT"], plants=[]))
    assert req.plants == ["ALPHA", "BRAVO", "CHARLIE"]


@pytest.mark.asyncio
async def test_an_explicit_plant_list_is_left_alone():
    service = svc.AnalyticsService(repository=_plant_repo())
    req = await service.with_plants(_request(datasets=["DC_PLANT"], plants=["BRAVO"]))
    assert req.plants == ["BRAVO"]


@pytest.mark.asyncio
async def test_a_request_with_no_per_plant_dataset_resolves_nothing():
    # No pointless distinct scan for a query that has no plant dimension at all.
    repo = _plant_repo()
    service = svc.AnalyticsService(repository=repo)
    req = await service.with_plants(_request(datasets=["SG_TOTAL"], plants=[]))
    assert req.plants == []
    assert repo.calls == []


@pytest.mark.asyncio
async def test_the_budget_prices_the_resolved_plants_not_one_series():
    # The whole point. Unresolved, `context_for` reads len([]) -> 1.
    service = svc.AnalyticsService(repository=_plant_repo())
    raw = _request(datasets=["DC_PLANT"], plants=[])
    assert service.context_for(raw)["plant_count"] == 1

    resolved = await service.with_plants(raw)
    assert service.context_for(resolved)["plant_count"] == 3
    assert (service.estimate_only(resolved).cells
            == 3 * service.estimate_only(raw).cells)


@pytest.mark.asyncio
async def test_the_plant_list_is_read_once_and_then_cached():
    repo = _plant_repo()
    service = svc.AnalyticsService(repository=repo)
    for _ in range(3):
        await service.with_plants(_request(datasets=["DC_PLANT"], plants=[]))
    assert len(repo.calls) == 1, f"{len(repo.calls)} distinct scans, expected 1"


@pytest.mark.asyncio
async def test_a_failed_plant_lookup_leaves_the_request_alone():
    # No plant filter is the same set of rows, so a plant-list blip must not fail the
    # query. The budget under-prices that one request; losing it entirely is worse.
    service = svc.AnalyticsService(repository=RecordingRepo(fail=["supply_serve"]))
    req = await service.with_plants(_request(datasets=["DC_PLANT"], plants=[]))
    assert req.plants == []
