"""Run the planned aggregations concurrently, and survive one of them failing.

Two rules, both of which exist because the alternative is silent:

1. **A failed group must never look like an empty one.** `GenericRepository`
   swallows aggregation errors and returns `[]`, so a timed-out query and a range
   with no data are the same thing to the caller. Analytics must tell them apart —
   the docx asks for a partial-data banner, and a chart that confidently draws
   nothing is worse than one that says a column could not be loaded. The analytics
   repository therefore raises, and this module turns a raise into
   `failed_groups` + `failed_datasets` while every other group still returns rows.

2. **One group's failure is not the request's failure.** Four groups run; if the
   exchange aggregation times out, demand, supply and peak still render. Failing
   the whole request would mean a single sparse collection can black out a screen
   that was mostly fine.

Concurrency is bounded by a semaphore rather than by `gather`'s appetite: the
repository has its own Motor pool precisely so analytics cannot starve the bidding
screens, and an unbounded fan-out would spend that pool on one user's query.
`maxTimeMS` rides every call so a pathological pipeline dies in the server rather
than holding a connection for the length of the HTTP timeout.
"""
import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Sequence

from .adapters.base import CanonicalRow

logger = logging.getLogger(__name__)

#: Matches `settings.ANALYTICS_GROUP_CONCURRENCY` / `ANALYTICS_QUERY_MAX_TIME_MS`.
#: Duplicated as literals so this module stays importable (and testable) without
#: the service settings; the service layer passes the configured values in.
DEFAULT_CONCURRENCY = 4
DEFAULT_MAX_TIME_MS = 20_000


@dataclass
class ExecutionResult:
    """What came back, and what did not.

    `rows` holds every canonical row from the groups that succeeded, in plan order.
    `failed_datasets` is the list the response's `meta` reports as `quality:
    "failed"` — an empty `rows` with an empty `failed_datasets` genuinely means
    "no data in this range".
    """
    rows: List[CanonicalRow] = field(default_factory=list)
    failed_groups: List[Dict[str, Any]] = field(default_factory=list)
    failed_datasets: List[str] = field(default_factory=list)

    @property
    def partial(self) -> bool:
        """True when at least one group failed — the partial-data banner's flag."""
        return bool(self.failed_groups)


async def execute(plans: Sequence[dict], repo, *,
                  concurrency: int = DEFAULT_CONCURRENCY,
                  max_time_ms: int = DEFAULT_MAX_TIME_MS) -> ExecutionResult:
    """Run every plan's pipeline, bounded by `concurrency`, and flatten the results.

    `repo` needs one method: `run_pipeline(collection, pipeline, max_time_ms)`, and
    it must **raise** on failure rather than returning `[]` — that raise is the only
    thing that distinguishes a broken query from an empty range.
    """
    if not plans:
        return ExecutionResult()

    sem = asyncio.Semaphore(max(1, int(concurrency)))

    async def _run(p):
        async with sem:
            docs = await repo.run_pipeline(
                p["adapter"].serve_collection, p["pipeline"], max_time_ms=max_time_ms
            )
            return p, p["adapter"].rows_from(docs, p["datasets"])

    settled = await asyncio.gather(*(_run(p) for p in plans), return_exceptions=True)

    result = ExecutionResult()
    for plan_, outcome in zip(plans, settled):
        if isinstance(outcome, asyncio.CancelledError):
            # Cancellation is the caller going away (client disconnect, shutdown),
            # not a query defect. Recording it as a failed dataset would report a
            # data problem for a request nobody is waiting for any more.
            raise outcome
        if isinstance(outcome, BaseException):
            # Never report success for a dataset whose query failed — the frontend
            # must be able to show a partial-data banner.
            codes = [d.code for d in plan_["datasets"]]
            result.failed_groups.append({
                "group": plan_["group"],
                "collection": plan_["adapter"].serve_collection,
                "datasets": codes,
                "error": f"{type(outcome).__name__}: {outcome}",
            })
            result.failed_datasets.extend(codes)
            logger.error(
                "Analytics group '%s' failed on %s (%s): %s",
                plan_["group"], plan_["adapter"].serve_collection,
                ", ".join(codes), outcome,
            )
        else:
            result.rows.extend(outcome[1])

    if result.failed_groups:
        logger.warning(
            "Analytics request partially served: %d/%d group(s) failed, "
            "%d dataset(s) unavailable",
            len(result.failed_groups), len(plans), len(result.failed_datasets),
        )
    return result
