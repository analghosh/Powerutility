"""Demand source group — `demand_serve` ∪ `demand_serve_historical`.

The widest group in the dashboard: actual demand, drawal, UDM, rostering,
frequency, both forecasts, both MAPEs, the state schedule, and the stored DSM rate
and amount. All of it comes off one document per `(type, scheduled_at, time_block)`
with `type ∈ {DAF, IDF}`, so every ticked demand dataset rides a single pipeline.

Two facts about this collection shape the adapter and neither is obvious:

* **`type` is a row discriminator, not a filter.** `MAPE_DAF` and `MAPE_IDF` are the
  same `mape` field on different rows; `ACTUAL_DEMAND` is on both. Hoisting
  `type="DAF"` into the shared `$match` would delete every `ACTUAL_DEMAND` row from
  a query that ticked both — an empty column, not an error. The base class puts each
  dataset's discriminator inside its own accumulator as a `$cond`; all this module
  has to do is not undo that.
* **The serve half is tiny.** 164 documents locally against 3,406 historical, i.e.
  the rolling ~3-day window. Any range older than three days lives entirely in the
  historical twin, so the `$unionWith` is not a completeness nicety here — without it
  almost every query PTC runs returns nothing at all.

Field choices come from the registry, which records the two traps: `comp_udm` rather
than `udm` (dead, zero in every document) and the stored `net_dsm_amount` rather than
re-running the CERC band calculation.
"""
from typing import List, Optional, Sequence

from ..registry import Dataset
from .base import SourceGroupAdapter, register


@register
class DemandAdapter(SourceGroupAdapter):
    """State-level demand, forecast, frequency and DSM, block by block."""

    group = "demand"
    serve_collection = "demand_serve"
    historical_collection = "demand_serve_historical"
    # Ordinary demand datasets are one series per block. The weekly/monthly roll-ups
    # are reported per forecast type — a "Day Ahead" and an "Intra Day" column from one
    # tick — so `type` is the entity, and the base only keys the group on it when a
    # `per_forecast_type` dataset is actually selected (see `_wants_entity`).
    entity = "type"
    entity_filter_key = None  # never filtered by the user; both types always come back
    # The vendor's own unique key — `idx_unique_demand_key` on the serve collection
    # and `find_by_unique_key(scheduled_at, time_block, type)` in the scheduler's
    # repository. Drops the copy the archival window leaves in both collections.
    dedupe_key = ("scheduled_at", "time_block", "type")

    def build_pipeline(self, *, datasets: Sequence[Dataset], filters: dict,
                       ctx: Optional[dict] = None) -> List[dict]:
        # match -> union historical -> group. Nothing to splice in between: no
        # revisions to collapse and no sub-documents to reshape.
        return self._default_pipeline(datasets=datasets, filters=filters, ctx=ctx)
