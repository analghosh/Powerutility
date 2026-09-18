"""Ticked datasets -> one plan per MongoDB source group.

This is the step the whole query engine is built on. A user who ticks twelve
datasets is not asking for twelve queries: `MAPE_DAF`, `MAPE_IDF`, `ACTUAL_DEMAND`
and `DRAWAL` all live on the same `demand_serve` document, so they ride one
aggregation and cost one scan. Collapsing them here is what keeps a five-month
range tractable on a single EC2 box that is also serving the bidding screens.

Three things happen before the collapse, and each of them is a silent bug if it is
skipped:

1. **Derived datasets are expanded to their dependencies.** `OD_UD_QUANTUM` is not
   queryable — it is `DRAWAL - FINAL_SCHEDULE_STATE` computed in Python after the
   groups return. The planner fetches the two operands and leaves the subtraction to
   `derived.resolve`; handing a derived dataset to an adapter would raise inside the
   pipeline builder instead.

2. **Codes are de-duplicated.** Ticking OD/UD Quantum *and* Drawal must not fetch
   Drawal twice: the second copy would double the accumulators in the same `$group`
   and, at individual-block granularity, double the documents crossing the wire.

3. **`absent` datasets are refused by name.** They have no storage anywhere, so
   `value` is `None` and the adapter's own validation would raise a message about
   pipelines rather than about the checkbox the user ticked. Dropping them quietly
   would be worse still: the response would look like a success for a column that is
   never coming.

The output is deliberately a plain dict per group rather than an object: the
executor needs `adapter` + `pipeline`, the failure path needs `group` + `datasets`,
and nothing downstream benefits from a richer type.
"""
import logging
from typing import Any, Dict, List, Optional, Sequence, TypedDict, Union

from . import derived, registry
from .adapters import base  # importing the package is what registers the adapters

logger = logging.getLogger(__name__)


class UnavailableDatasetError(ValueError):
    """A ticked dataset has no storage at all (`availability == "absent"`)."""

    def __init__(self, codes: Sequence[str]):
        self.codes = list(codes)
        super().__init__(
            "No data source exists for: " + ", ".join(self.codes) +
            ". These datasets are registered 'absent' — the storage has never been "
            "built, so they cannot be queried."
        )


class GroupPlan(TypedDict):
    """One source group's share of the request."""
    group: str
    adapter: base.SourceGroupAdapter
    datasets: List[registry.Dataset]
    pipeline: List[dict]


def _codes(datasets: Sequence[Union[str, registry.Dataset]]) -> List[str]:
    """Accept dataset codes or `Dataset` objects, in the order given."""
    return [d.code if isinstance(d, registry.Dataset) else str(d) for d in datasets]


def resolve_codes(datasets: Sequence[Union[str, registry.Dataset]]) -> List[str]:
    """The direct dataset codes that actually have to be queried.

    Derived datasets are replaced by their dependencies and every code appears once,
    in first-seen order. Unknown codes raise `UnknownDatasetError` here rather than
    part-way through building a pipeline.
    """
    requested = _codes(datasets)
    if not requested:
        raise ValueError("No datasets selected; an analytics query needs at least one")

    # `derived.source_codes` walks the dependency graph, validates every code
    # against the registry and de-duplicates in one pass.
    codes = derived.source_codes(requested)

    absent = [c for c in codes if registry.get(c).availability == "absent"]
    if absent:
        raise UnavailableDatasetError(absent)
    return codes


def plan(datasets: Sequence[Union[str, registry.Dataset]], filters: dict,
         ctx: Optional[dict] = None) -> List[GroupPlan]:
    """Collapse the ticked datasets into one aggregation plan per source group.

    `filters` is the whole filter set (dates, blocks, groupings, plants, exchanges);
    each adapter takes what its group understands and ignores the rest. `ctx` carries
    the counts the budget already computed (`plant_count`, `exchange_count`) and is
    passed through untouched.

    Groups come back in the order the request first mentions them, so the response —
    and any log line about it — is stable for a stable request.
    """
    codes = resolve_codes(datasets)
    grouped: Dict[str, List[registry.Dataset]] = registry.by_group(codes)

    plans: List[GroupPlan] = []
    for group, group_datasets in grouped.items():
        adapter = base.get_adapter(group)
        plans.append(GroupPlan(
            group=group,
            adapter=adapter,
            datasets=group_datasets,
            pipeline=adapter.build_pipeline(
                datasets=group_datasets, filters=filters, ctx=ctx),
        ))

    logger.info(
        "Analytics plan: %d dataset(s) -> %d aggregation(s) [%s]",
        len(codes), len(plans), ", ".join(p["group"] for p in plans),
    )
    return plans


def planned_datasets(plans: Sequence[GroupPlan]) -> List[str]:
    """Every dataset code the plans will fetch, in plan order. Handy for logging
    and for naming the casualties when a group fails."""
    return [d.code for p in plans for d in p["datasets"]]


def describe(plans: Sequence[GroupPlan]) -> List[Dict[str, Any]]:
    """A JSON-safe summary of a plan set — no adapter objects, no pipelines.

    Exists so the service layer can log or echo what it is about to run without
    serialising several hundred kilobytes of aggregation stages.
    """
    return [
        {"group": p["group"],
         "collection": p["adapter"].serve_collection,
         "datasets": [d.code for d in p["datasets"]],
         "stages": len(p["pipeline"])}
        for p in plans
    ]
