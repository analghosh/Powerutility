"""The four things the analytics screen asks the backend for.

Three of them are catalogue calls — what can I plot, which plants exist for it,
which block bands are configured — and the fourth is the query itself. The query is
a one-line hand-off to `AnalyticsService`, which owns permissions, the cell budget
and the cache. What actually lives here is the catalogue, and it carries three
decisions worth stating.

**1. The catalogue lists everything, including what this caller may not see and
what has no storage.** A dataset that disappears from the list because a grant is
missing is indistinguishable from a feature that was never built, and PTC's own
handover pack lists all of them; a dataset dropped because it is `absent` reappears
as a support ticket asking where Weekly Forecast went. So every registered dataset
is returned, each carrying `availability` and `permitted`, and the UI renders the
ones it cannot use as disabled with a reason. The flag is a convenience for the
screen, never a control: `/query` re-checks the grant server-side and drops what the
caller may not see, which is the check that actually matters.

**2. The plant list is read from the collection the ticked dataset queries.**
`supply_serve` carries 34 plant ids while `plant_master` has 35 and they do not
match — counted in the local UAT import. (Until 2026-09-18 a second per-plant
source, `serve_dam_scheduling` with 53 ids, made the two lists non-interchangeable;
it is gone, but the per-dataset read stays because a master list that matches the
data still does not exist.) Offering the wrong list produces a selection that
matches no document and a table of empty cells, which reads as "no data in this
range" rather than as the wrong dropdown.

That read is a distinct over an unindexed field and it is triggered by ticking a
checkbox, so it would run on every dashboard load. It is therefore cached in process for
`PLANT_CACHE_TTL_SECONDS`. The list changes when a plant is commissioned, which is a
quarterly event, not a per-request one. This is an ordinary dict rather than Redis
on purpose: it must not be able to fail, and a per-worker copy of a 53-element list
is not worth a network round trip.

**3. A failed plant lookup is a 503, not an empty list.** `GenericRepository`
returns `[]` for an exception everywhere else in this service, which is exactly how
a broken query becomes an innocent-looking empty screen; the analytics repository
raises instead, and that raise has to survive as far as the HTTP status. An empty
plant list leaves Apply permanently disabled with no explanation.
"""
import logging
import time
from typing import Any, Dict, List, Optional

from ...core.config import settings as default_settings
from ..analytics import block_bands, registry
from ..analytics.adapters import base as adapters
from ..analytics.grouping import DATE_GROUPINGS
from ..analytics.models import AnalyticsQueryRequest
from ..services.analytics_service import DEFAULT_EXCHANGE_COUNT, AnalyticsService

logger = logging.getLogger(__name__)

#: How long a plant list is trusted. Long on purpose: the alternative is a
#: collection scan per dashboard load for a list that changes when a plant is
#: commissioned.
PLANT_CACHE_TTL_SECONDS = 900

#: Shown against the five bands PTC has never supplied ranges for. The UI uses it
#: verbatim as the tooltip on the disabled pill.
INACTIVE_BAND_NOTE = ("Not defined yet — set parameter '{key}' under Masters → Parameters "
                      "(e.g. {example})")

#: Which filter multiplies a dataset's series count, for the live cell estimate the
#: filter panel shows before anything is submitted.
CARDINALITY_BASIS = {"per_plant": "plants", "per_exchange": "exchanges"}

#: The series kind this route can enumerate. Exchanges are a fixed three-item list
#: the frontend already holds; plants are not.
PLANT_SERIES = "per_plant"


class DatasetHasNoPlantsError(ValueError):
    """A plant list was asked for a dataset that is not reported per plant."""


class EntityListUnavailableError(RuntimeError):
    """The plant lookup failed. Deliberately not an empty list — see the docstring."""


class AnalyticsController:
    """Catalogue endpoints plus a hand-off to the service for the query itself."""

    def __init__(self, service: Any = None, repository: Any = None,
                 cache: Any = None, settings: Any = None):
        self._service = service
        self._repository = repository
        self._cache = cache
        self._cache_resolved = cache is not None
        self.settings = settings or default_settings
        self._plants: Dict[str, Dict[str, Any]] = {}

    # ------------------------------------------------------------- plumbing

    @property
    def repository(self):
        """Lazy, like every v2 repository: a Motor client built at import time is
        what binds it to the wrong event loop, and that failure returns `[]` from
        every query instead of raising."""
        if self._repository is None:
            from ..repository.analytics_repository import AnalyticsRepository
            self._repository = AnalyticsRepository()
        return self._repository

    @repository.setter
    def repository(self, value):
        self._repository = value
        self._service = None  # a rebuilt service must not keep the old repository

    @property
    def cache(self):
        """The query cache handed to the service, built on first use."""
        return self._query_cache()

    @cache.setter
    def cache(self, value):
        # Settable so a test can run the routes without a live Redis — and so a
        # caller can hand in its own client rather than the one built from settings.
        self._cache = value
        self._cache_resolved = True
        self._service = None

    @property
    def service(self) -> AnalyticsService:
        if self._service is None:
            # One repository object, shared with the plant lookup: both would
            # otherwise ask the factory for the same Motor client anyway.
            self._service = AnalyticsService(
                repository=self.repository, cache=self._query_cache(),
                settings=self.settings)
        return self._service

    @service.setter
    def service(self, value):
        self._service = value

    def reset(self) -> None:
        """Drop every lazily built collaborator and the plant cache.

        Exists for tests: the router talks to the module singleton, so one test's
        stub repository would otherwise be the next test's.
        """
        self._service = None
        self._repository = None
        self._cache = None
        self._cache_resolved = False
        self._plants.clear()

    def _query_cache(self):
        """An async Redis client for the query cache, or None.

        `AnalyticsService` has no default cache on purpose — the obvious one,
        `libs.cache.CacheService`, is the *blocking* redis client, and a blocking
        call inside an async handler is precisely the defect that makes a login rush
        401 the whole platform. So the async client is built here, once, lazily
        inside the running loop, and a failure to build one is not fatal: the
        dashboard then runs uncached, which is slower, not broken.
        """
        if self._cache_resolved:
            return self._cache
        self._cache_resolved = True
        try:
            import redis.asyncio as aioredis
            s = self.settings
            self._cache = aioredis.Redis(
                host=getattr(s, "REDIS_HOST", "localhost"),
                port=int(getattr(s, "REDIS_PORT", 6379)),
                db=int(getattr(s, "REDIS_DB", 0)),
                password=getattr(s, "REDIS_PASSWORD", "") or None,
                decode_responses=True,
            )
            logger.info("Analytics query cache using Redis at %s:%s/%s",
                        getattr(s, "REDIS_HOST", "localhost"),
                        getattr(s, "REDIS_PORT", 6379),
                        getattr(s, "REDIS_DB", 0))
        except Exception as exc:  # pragma: no cover - import/config failure only
            logger.warning(
                "Analytics query cache disabled (%s); queries will not be cached", exc)
            self._cache = None
        return self._cache

    # ------------------------------------------------------------ catalogue

    async def datasets(self, user_context: Optional[dict] = None) -> dict:
        """Every registered dataset, with availability, cardinality and the grant."""
        permissions = AnalyticsService.permissions_for(user_context)
        entries: List[Dict[str, Any]] = []
        groups: Dict[str, List[str]] = {}

        for dataset in registry.DATASETS.values():
            entries.append({
                "code": dataset.code,
                "label": dataset.label,
                "group": dataset.group,
                "unit": dataset.unit,
                "agg": dataset.agg,
                "kind": dataset.kind,
                "depends": list(dataset.depends),
                "series": dataset.series,
                # A roll-up dataset pins the date grouping for the whole request, so
                # the UI can stop the user combining WEEK_WISE with MONTH_WISE
                # before the server has to refuse it.
                "fixed_grouping": dataset.fixed_grouping,
                # Per-plant breakdown of a state-level total: sorted after it, badged.
                "drilldown": dataset.drilldown,
                "availability": dataset.availability,
                "permission": dataset.permission,
                "permitted": AnalyticsService.has_permission(
                    dataset.permission, permissions),
                # What the UI multiplies by to show a live cell estimate: one
                # series per selected plant, one per exchange, or just one.
                "cardinality_basis": CARDINALITY_BASIS.get(dataset.series),
                "default_cardinality": registry.series_cardinality(
                    dataset, {"plant_count": 1,
                              "exchange_count": DEFAULT_EXCHANGE_COUNT}),
            })
            groups.setdefault(dataset.group, []).append(dataset.code)

        return {"data": {
            "datasets": entries,
            "groups": [
                {"group": g, "label": g.replace("_", " ").title(), "codes": codes}
                for g, codes in groups.items()
            ],
            "date_groupings": list(DATE_GROUPINGS),
        }}

    async def plants(self, dataset: str,
                     user_context: Optional[dict] = None) -> dict:
        """The plant ids present in the collection this dataset actually reads."""
        entry = registry.get(dataset)  # raises UnknownDatasetError -> 422
        if entry.series != PLANT_SERIES:
            raise DatasetHasNoPlantsError(
                f"Dataset {entry.code} is reported as a single series, not per "
                f"plant; it has no plant list."
            )

        adapter = adapters.get_adapter(entry.group)
        field = adapter.entity_field()
        collection = adapter.serve_collection
        if not field:
            raise DatasetHasNoPlantsError(
                f"Source group '{entry.group}' carries no per-entity field, so "
                f"{entry.code} cannot be split by plant."
            )

        cached = self._cached_plants(entry.code)
        if cached is not None:
            return {"data": dict(cached, cached=True)}

        # A `$group` on a single field is the distinct Mongo can serve from an
        # index when one exists, and the `$match` keeps documents with no plant id
        # out of the dropdown rather than rendering a blank option.
        pipeline = [
            {"$match": {field: {"$ne": None}}},
            {"$group": {"_id": f"${field}"}},
            {"$sort": {"_id": 1}},
        ]
        try:
            docs = await self.repository.run_pipeline(
                collection, pipeline,
                max_time_ms=int(getattr(
                    self.settings, "ANALYTICS_QUERY_MAX_TIME_MS", 20_000)),
            )
        except Exception as exc:
            logger.error("Analytics plant lookup failed on '%s': %s", collection, exc)
            raise EntityListUnavailableError(
                f"The plant list for {entry.code} could not be read from "
                f"'{collection}'. This is a database failure, not an empty range."
            ) from exc

        ids = sorted({str(d.get("_id")) for d in docs
                      if d.get("_id") not in (None, "")})
        payload = {
            "dataset": entry.code,
            "collection": collection,
            "field": field,
            "plants": [{"id": pid, "label": pid} for pid in ids],
            "count": len(ids),
            "cached": False,
        }
        self._plants[entry.code] = {"at": time.monotonic(), "payload": payload}
        logger.info("Analytics plant list for %s: %d ids from '%s'",
                    entry.code, len(ids), collection)
        return {"data": payload}

    def _cached_plants(self, code: str) -> Optional[dict]:
        hit = self._plants.get(code)
        if not hit:
            return None
        if time.monotonic() - hit["at"] > PLANT_CACHE_TTL_SECONDS:
            self._plants.pop(code, None)
            return None
        return hit["payload"]

    async def block_bands(self, user_context: Optional[dict] = None) -> dict:
        """The configured bands, the named ones read from Masters → Parameters.

        `fresh=True`: this is the call the dashboard makes on load, and an admin
        who has just saved a parameter expects the pill to light up on the next
        reload, not up to 30 s later. Queries still use the short cache.
        """
        current = await self.service.bands(fresh=True)
        values = type(self.service)._band_cache.get("values") or {}
        errors = block_bands.parameter_errors(values)
        bands = []
        for band in current.values():
            key = block_bands.PARAMETER_KEYS.get(band.code)
            if band.active:
                note = None
            elif band.code in errors:
                note = (f"Parameter '{key}' could not be read: {errors[band.code]}")
            elif key:
                note = INACTIVE_BAND_NOTE.format(key=key, example=block_bands.RANGES_EXAMPLE)
            else:
                note = None
            bands.append({
                "code": band.code,
                "label": band.label,
                "ranges": [list(r) for r in band.ranges],
                "active": band.active,
                "effective_from": band.effective_from,
                "parameter_key": key,
                "note": note,
            })
        return {"data": {
            "bands": bands,
            "min_block": block_bands.MIN_BLOCK,
            "max_block": block_bands.MAX_BLOCK,
        }}

    # ---------------------------------------------------------------- query

    async def query(self, request: AnalyticsQueryRequest,
                    user_context: Optional[dict] = None) -> dict:
        """One dashboard query. Permissions, budget, cache and Mongo live below."""
        response = await self.service.query(request, user_context)
        return {"data": response}


analytics_controller = AnalyticsController()
