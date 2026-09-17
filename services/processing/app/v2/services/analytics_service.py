"""Orchestration for the analytics dashboard: permissions -> budget -> cache -> query.

Everything below the service layer is pure: the registry knows what a dataset is, the
budget knows what a request costs, the planner knows which collections to read, the
executor runs them and the pivot lays the answer out. This module is where those are
put in the one order that is safe, and it owns the three decisions that cannot live
anywhere else.

**1. Dataset permissions are enforced here, per dataset, by dropping.**
There is no per-permission route guard anywhere in the processing service to copy —
`libs/core/authz/guard.py` is used only by the auth service — and a route guard could
not express this rule anyway, because the grant is per dataset rather than per route:
one request can carry `ACTUAL_DEMAND` (`analytics:demand`) and `ACP_DAM`
(`analytics:market`) together. A user entitled to five of six ticked datasets gets
five, with the sixth named in `meta.denied_datasets`; a blanket 403 would make a
legitimate dashboard unusable and silently ignoring the grant would be the hole. The
frontend disables what a user may not see, but that is a convenience, not a control —
§13 of the spec is explicit that the check is server-side.

When *nothing* survives the filter this returns an empty envelope carrying
`permission_denied: True` rather than raising. Raising would be the cleaner shape,
but only if every caller mapped it to a 403; an unmapped exception from a route is a
500, and the client cannot tell a permissions problem from a broken server. The empty
envelope is well-formed, costs no database call, and names every dataset it dropped.

**2. The cell budget is consulted before anything touches MongoDB.**
The whole point of computing cost arithmetically is that a refused request is refused
in about a millisecond. Consult the budget after planning and the five-month scan has
already been paid for. `BudgetExceeded` carries the estimate and its suggestions so
the route can answer 413 with something the user can act on ("switch to Week-wise",
"pick 5 plants") instead of a bare refusal.

**3. The cache key is computed from the *permitted* dataset set, not the requested
one.** Two users ticking the same six datasets, one of whom may not see the market
columns, must not share a cache entry — otherwise the first response served decides
what the second user can see, in either direction. Filtering first and hashing second
makes that structurally impossible. `denied_datasets` is attached after the cache
read, so the two users still each get an accurate account of what was dropped.

A partial result — any source group that failed — is never written to the cache. A
failed group already reads as an empty one unless we say otherwise (`GenericRepository`
returns `[]` for an exception, which is why analytics has its own repository that
raises); caching that for a day would turn a transient timeout into a stale, confident,
wrong dashboard. We have form here: `submit-for-approval` returns HTTP 200 on failure
and the frontend never checks, which is how bids go missing.

The cache itself is injected. It must expose `get(key)` and `set(key, value, ttl)`,
either sync or async; values are stored as JSON text so both `libs.cache.CacheService`
and a plain `redis.asyncio` client work. There is no default: a service constructed
without one simply does not cache, because the obvious default —
`libs.cache.CacheService` — is the *blocking* redis client, and a blocking call in an
async handler is precisely the defect that makes a login rush 401 the whole platform.
Every cache call is wrapped: Redis being down slows the dashboard, it does not break it.
"""
import hashlib
import inspect
import json
import logging
from datetime import date, datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from ...core.config import settings as default_settings
from ..analytics import block_bands, budget, derived, executor, pivot, planner, registry
from ..analytics.models import AnalyticsQueryRequest

logger = logging.getLogger(__name__)

#: Series count assumed for a per-exchange dataset when the user narrows nothing.
#: `exchange_serve` carries IEX, PXIL and HPX; an empty `exchanges` filter means all
#: three, so the budget has to price three series rather than one.
DEFAULT_EXCHANGE_COUNT = 3

#: Bumped whenever the cached payload's shape changes, so a deploy cannot serve a
#: yesterday-shaped response to today's frontend out of a 24-hour entry.
# Bump whenever a dataset's MEANING changes (field, formula, grouping) — the key is built
# from dataset codes, so a redefinition would otherwise keep serving the old payload for
# the length of the TTL. v2: ACTUAL_DEMAND moved from `actual` to `drawal` (2026-09-17).
CACHE_VERSION = "v2"
CACHE_PREFIX = "analytics:query"

#: Bands that cannot be rendered inline. The route turns this into a 413 plus the
#: estimate's suggestions; the export path takes the same request as a queued job.
OVER_BUDGET_BANDS = (budget.BAND_ASYNC_ONLY, budget.BAND_REFUSED)

#: Permission strings that mean "everything", matching `libs/core/authz`.
WILDCARD_PERMISSIONS = ("*", "admin:*")

#: Roles that mean "everything", matching `guard.py`'s super-admin detection.
SUPER_ADMIN_ROLES = ("admin", "superadmin", "super_admin")

CACHE_HIT = "hit"
CACHE_MISS = "miss"
CACHE_SKIPPED = "skipped"


class BudgetExceeded(RuntimeError):
    """The request costs more cells than an inline render is allowed to produce.

    Carries the whole `Estimate` — the route answers 413 with `estimate` and
    `suggestions` so the user is told what to change, not merely that they were
    refused.
    """

    def __init__(self, estimate: budget.Estimate, message: Optional[str] = None):
        self.estimate = estimate
        self.cells = estimate.cells
        self.band = estimate.band
        self.suggestions = list(estimate.suggestions)
        super().__init__(message or (
            f"This selection would render {estimate.cells:,} cells, more than the "
            f"inline limit. Narrow the range, group the blocks, or export it instead."
        ))


class FixedGroupingConflict(ValueError):
    """Two selected datasets demand different fixed date groupings."""


def forced_grouping(codes) -> Tuple[Optional[str], List[str]]:
    """The date grouping the selected datasets impose, if any, and who imposed it.

    A weekly forecast viewed block-wise is meaningless, so a roll-up dataset carries a
    `fixed_grouping` that overrides the user's choice for the whole request. Mixing a
    weekly and a monthly roll-up in one request has no sensible answer and is refused.
    """
    forced: Dict[str, List[str]] = {}
    for code in codes:
        fg = registry.get(code).fixed_grouping
        if fg:
            forced.setdefault(fg, []).append(code)
    if not forced:
        return None, []
    if len(forced) > 1:
        detail = "; ".join(f"{g}: {', '.join(c)}" for g, c in sorted(forced.items()))
        raise FixedGroupingConflict(
            f"These datasets require different date groupings and cannot be combined "
            f"in one request — {detail}")
    (grouping, by), = forced.items()
    return grouping, by


def _as_request(request: Any) -> AnalyticsQueryRequest:
    """Accept a validated model or the raw filter dict a stored export job holds.

    Also the single choke point where a dataset's `fixed_grouping` overrides the
    user's `date_grouping`, so the budget, the cache key and the planner all see the
    same, already-forced value.
    """
    if isinstance(request, AnalyticsQueryRequest):
        req = request
    elif isinstance(request, dict):
        req = AnalyticsQueryRequest(**request)
    else:
        raise TypeError(
            f"Expected an AnalyticsQueryRequest or a filter dict, got {type(request).__name__}")
    grouping, _ = forced_grouping(req.datasets)
    if grouping and grouping != req.date_grouping:
        req = req.model_copy(update={"date_grouping": grouping})
    return req


async def _maybe_await(value: Any) -> Any:
    """Support a sync or an async cache client behind one call site."""
    if inspect.isawaitable(value):
        return await value
    return value


class AnalyticsService:
    """The only object a route needs to answer an analytics query."""

    def __init__(self, repository: Any = None, cache: Any = None,
                 settings: Any = None):
        self._repository = repository
        self.cache = cache
        self.settings = settings or default_settings

    # ------------------------------------------------------------- plumbing

    @property
    def repository(self):
        """Lazy, like every v2 repository: building a Motor client in `__init__`
        is what binds it to the wrong event loop, and that failure returns `[]`
        from every query instead of raising."""
        if self._repository is None:
            from ..repository.analytics_repository import AnalyticsRepository
            self._repository = AnalyticsRepository()
        return self._repository

    @property
    def thresholds(self) -> Dict[str, int]:
        s = self.settings
        return {
            "inline": int(getattr(s, "ANALYTICS_CELLS_INLINE",
                                  budget.THRESHOLDS["inline"])),
            "inline_capped": int(getattr(s, "ANALYTICS_CELLS_INLINE_CAPPED",
                                         budget.THRESHOLDS["inline_capped"])),
            "max": int(getattr(s, "ANALYTICS_CELLS_MAX", budget.THRESHOLDS["max"])),
        }

    # ---------------------------------------------------------- permissions

    @staticmethod
    def permissions_for(user_context: Optional[dict]) -> Set[str]:
        """The effective permission set for a request context.

        A super admin is normalised to the `*` wildcard here rather than being a
        second code path through every check — the three ways EPMS says "super
        admin" (`is_super_admin` on the user document, an `admin` role in the token,
        an explicit wildcard grant) are exactly what `libs/core/authz/guard.py`
        accepts, and they all have to mean the same thing on this screen.
        """
        ctx = user_context or {}
        user = ctx.get("user") or {}
        permissions = set(ctx.get("permissions") or [])
        if not permissions and isinstance(user, dict):
            permissions = set(user.get("permissions") or [])

        roles = list(ctx.get("roles") or [])
        if isinstance(user, dict):
            roles += list(user.get("roles") or [])

        is_super = (
            bool(ctx.get("is_super_admin"))
            or (isinstance(user, dict) and bool(user.get("is_super_admin")))
            or any(r in SUPER_ADMIN_ROLES for r in roles)
            or any(p in permissions for p in WILDCARD_PERMISSIONS)
        )
        if is_super:
            permissions.add("*")
        return permissions

    @staticmethod
    def has_permission(required: Optional[str], permissions: Iterable[str]) -> bool:
        """Exact grant, global wildcard, or resource wildcard (`analytics:*`)."""
        if not required:
            return False
        held = set(permissions or ())
        if required in held:
            return True
        if any(p in held for p in WILDCARD_PERMISSIONS):
            return True
        if ":" in required:
            resource = required.split(":", 1)[0]
            if f"{resource}:*" in held:
                return True
        return False

    @staticmethod
    def filter_by_permission(codes: Sequence[str],
                             permissions: Iterable[str]) -> Tuple[List[str], List[str]]:
        """Split ticked dataset codes into `(allowed, denied)`, order preserved.

        Unknown codes raise `registry.UnknownDatasetError` rather than landing in
        either list: the request model already rejects them with a 422, so a code
        reaching here that the registry does not know is a programming error, and
        silently dropping it would look identical to a permissions denial.
        """
        held = set(permissions or ())
        allowed: List[str] = []
        denied: List[str] = []
        for code in codes or ():
            dataset = registry.get(code)  # raises UnknownDatasetError
            target = allowed if AnalyticsService.has_permission(
                dataset.permission, held) else denied
            target.append(code)
        return allowed, denied

    # --------------------------------------------------------------- budget

    def context_for(self, request: Any) -> Dict[str, int]:
        """The series-cardinality context the budget and the planner both take.

        An empty `exchanges` filter means *every* exchange, so it prices three
        series rather than one; an empty `plants` filter can only happen when no
        per-plant dataset is selected, because the request model refuses that
        combination outright.
        """
        req = _as_request(request)
        return {
            "plant_count": max(1, len(req.plants)),
            "exchange_count": max(1, len(req.exchanges) or DEFAULT_EXCHANGE_COUNT),
        }

    def estimate_only(self, request: Any,
                      ctx: Optional[dict] = None) -> budget.Estimate:
        """Price a request without touching MongoDB. Pure arithmetic, ~1ms."""
        req = _as_request(request)
        ctx = ctx or self.context_for(req)
        band = block_bands.DEFAULT_BANDS[req.block_band]
        return budget.estimate(
            date_buckets=budget.date_bucket_count(
                req.from_date, req.to_date, req.date_grouping),
            block_buckets=block_bands.bucket_count(
                band, req.from_block, req.to_block, req.custom_blocks),
            datasets=req.datasets,
            ctx=ctx,
            thresholds=self.thresholds,
        )

    # ---------------------------------------------------------------- cache

    def cache_key(self, request: Any) -> str:
        """sha256 over the canonicalised filter set.

        Canonicalisation is the whole point: the same question asked with the JSON
        keys in a different order, or the same plants listed in a different order,
        must hit the same entry — that is what turns a Monday-morning herd into one
        aggregation. Two things are deliberately *not* canonicalised away:

        * `datasets` order, which decides the order of the series in the response;
        * `custom_blocks`, whose ranges are part of the answer's shape.

        `format` (on an `ExportRequest`) is ignored on purpose — a CSV and an XLSX
        of the same question are the same data.
        """
        req = _as_request(request)
        canonical = {
            "v": CACHE_VERSION,
            "datasets": list(req.datasets),
            "from_date": req.from_date.isoformat(),
            "to_date": req.to_date.isoformat(),
            "from_block": req.from_block,
            "to_block": req.to_block,
            "date_grouping": req.date_grouping,
            "block_band": req.block_band,
            "custom_blocks": [list(p) for p in req.custom_blocks],
            "plants": sorted(set(req.plants)),
            "exchanges": sorted(set(req.exchanges)),
        }
        blob = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(blob.encode("utf-8")).hexdigest()
        return f"{CACHE_PREFIX}:{CACHE_VERSION}:{digest}"

    def today(self) -> date:
        """Today on the trading calendar, not on the server's clock.

        EPMS trades on IST blocks; a UTC server rolls the date over at 05:30 local,
        and for those five and a half hours "is this range finished?" would be
        answered wrongly in both directions.
        """
        tz_name = getattr(self.settings, "ANALYTICS_TIMEZONE", None)
        if tz_name:
            try:
                from zoneinfo import ZoneInfo
                return datetime.now(ZoneInfo(tz_name)).date()
            except Exception as exc:  # pragma: no cover - missing tzdata only
                logger.warning(
                    "Analytics timezone %r unavailable (%s); falling back to UTC",
                    tz_name, exc)
        return datetime.now(timezone.utc).date()

    def is_fully_past(self, request: Any) -> bool:
        """True when every date in the range is finished and can no longer change.

        Today's blocks are still being filled in, and a future range is forecast
        that gets revised, so both take the short TTL. Only a closed range earns the
        long one.
        """
        req = _as_request(request)
        return req.to_date < self.today()

    def cache_ttl(self, request: Any) -> int:
        """Long for an immutable past range, short for anything still moving."""
        s = self.settings
        if self.is_fully_past(request):
            return int(getattr(s, "ANALYTICS_CACHE_TTL_PAST_SECONDS", 86_400))
        return int(getattr(s, "ANALYTICS_CACHE_TTL_SECONDS", 300))

    async def _cache_get(self, key: str) -> Optional[dict]:
        if self.cache is None:
            return None
        try:
            raw = await _maybe_await(self.cache.get(key))
        except Exception as exc:
            logger.warning("Analytics cache read failed for %s: %s", key, exc)
            return None
        if raw is None:
            return None
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8")
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except ValueError as exc:
                logger.warning("Analytics cache holds unreadable JSON at %s: %s", key, exc)
                return None
        return raw if isinstance(raw, dict) else None

    async def _cache_set(self, key: str, payload: dict, ttl: int) -> None:
        if self.cache is None:
            return
        try:
            await _maybe_await(self.cache.set(
                key, json.dumps(payload, default=str), ttl))
        except Exception as exc:
            logger.warning("Analytics cache write failed for %s: %s", key, exc)

    # ---------------------------------------------------------------- query

    async def query(self, request: Any, user_context: Optional[dict] = None) -> dict:
        """Answer one dashboard query. The only orchestration path.

        Order is the contract: permissions, then budget, then cache, then MongoDB.
        Each step is cheaper than the one after it, and each one can end the request.
        """
        req = _as_request(request)
        permissions = self.permissions_for(user_context)
        allowed, denied = self.filter_by_permission(req.datasets, permissions)

        if denied:
            logger.info(
                "Analytics: dropped %d dataset(s) the caller may not see [%s] (user=%s)",
                len(denied), ", ".join(denied),
                (user_context or {}).get("user_id", "unknown"))

        if not allowed:
            return self._denied_response(denied)

        effective = req.model_copy(update={"datasets": allowed})
        ctx = self.context_for(effective)
        estimate = self.estimate_only(effective, ctx)

        if estimate.band in OVER_BUDGET_BANDS:
            logger.info(
                "Analytics refused inline render: %d cells, band=%s (user=%s)",
                estimate.cells, estimate.band,
                (user_context or {}).get("user_id", "unknown"))
            raise BudgetExceeded(estimate)

        key = self.cache_key(effective)
        cached = await self._cache_get(key)
        if cached is not None:
            logger.info("Analytics cache hit for %s", key)
            return self._finalise(cached, denied=denied, estimate=estimate,
                                  cache_state=CACHE_HIT)

        plans = planner.plan(allowed, effective.to_filters(), ctx)
        result = await executor.execute(
            plans, self.repository,
            concurrency=int(getattr(self.settings, "ANALYTICS_GROUP_CONCURRENCY",
                                    executor.DEFAULT_CONCURRENCY)),
            max_time_ms=int(getattr(self.settings, "ANALYTICS_QUERY_MAX_TIME_MS",
                                    executor.DEFAULT_MAX_TIME_MS)),
        )

        rows = derived.resolve(result.rows, allowed)
        _, forced_by = forced_grouping(allowed)
        response = pivot.build_response(rows, requested=allowed, meta={
            "failed_datasets": result.failed_datasets,
            "partial": result.partial,
            # Part of the question, not the caller, so it belongs in the cached payload.
            "date_grouping": req.date_grouping,
            "grouping_forced_by": forced_by,
        })

        if result.partial:
            # Never cache a partial answer: a transient timeout would otherwise be
            # served as a confident, wrong dashboard for the length of the TTL.
            logger.warning(
                "Analytics result not cached: %d group(s) failed",
                len(result.failed_groups))
        else:
            await self._cache_set(key, response, self.cache_ttl(effective))

        return self._finalise(response, denied=denied, estimate=estimate,
                              cache_state=CACHE_MISS)

    # ------------------------------------------------------------- envelope

    def _denied_response(self, denied: Sequence[str]) -> dict:
        """Everything the caller ticked was refused. No database call is made."""
        logger.warning(
            "Analytics: every requested dataset was denied [%s]", ", ".join(denied))
        response = pivot.build_response([], requested=[], meta={
            "partial": False,
            "failed_datasets": [],
        })
        response["meta"]["denied_datasets"] = list(denied)
        response["meta"]["permission_denied"] = True
        response["meta"]["cache"] = CACHE_SKIPPED
        return response

    @staticmethod
    def _finalise(response: dict, *, denied: Sequence[str],
                  estimate: budget.Estimate, cache_state: str) -> dict:
        """Attach the per-caller facts that must never be part of a cached payload.

        `denied_datasets` depends on who is asking and `cache` on how this
        particular request was served, so both are added after the cache read — the
        stored payload stays the same object for every user entitled to it.
        """
        meta = dict(response.get("meta") or {})
        meta["denied_datasets"] = list(denied)
        meta["permission_denied"] = False
        meta["cache"] = cache_state
        meta["band"] = estimate.band
        meta["estimated_cells"] = estimate.cells
        meta["capped"] = estimate.band == budget.BAND_INLINE_CAPPED
        if meta["capped"]:
            # §6: an inline-capped render is served in full and paged client-side by
            # the date axis; the banner needs the real count to say so.
            meta["suggestions"] = list(estimate.suggestions)
        return {
            "axes": response.get("axes", {"dates": [], "blocks": []}),
            "series": response.get("series", []),
            "meta": meta,
        }
