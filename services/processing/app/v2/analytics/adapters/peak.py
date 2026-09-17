"""Peak source group — `peak_demand_serve` ∪ `peak_demand_serve_historical`.

Four daily summary figures: peak demand, minimum demand, average demand and total
energy in MU. Each carries its own aggregation rule in the registry — `max`, `min`,
`avg`, `sum` — and applying `avg` to all four is the classic wrong answer once the
user groups month-wise, so the rule travels with the dataset rather than with the
adapter.

Thin by nature: 10 documents in the serve collection locally and 5 in the historical
twin, one per trading day. A range that returns few rows here is the data, not a bug.

⚠️ **`peak_demand_serve.time_block` is the block at which the peak occurred, not an
axis.** Verified against the local UAT import: one document per day, each carrying a
single `time_block` (89, i.e. 22:00–22:15, on eight of the ten days). Two consequences
worth knowing before reading a peak column:

* Block-wise, a day's peak lands in exactly one block column and every other block
  is empty. That is faithful to what is stored.
* Narrowing the block range **filters days out** rather than windowing the peak —
  ask for blocks 1–48 and a day whose peak fell at block 89 disappears entirely. It
  cannot compute "the peak within blocks 1–48", because the per-block series it
  would need is in `demand_serve`, not here. Raised for PTC in the task report; the
  adapter deliberately does not paper over it, because silently ignoring the user's
  block range would be the worse lie.
"""
from typing import List, Optional, Sequence

from ..registry import Dataset
from .base import SourceGroupAdapter, register


@register
class PeakAdapter(SourceGroupAdapter):
    """Daily peak / minimum / average demand and total energy."""

    group = "peak"
    serve_collection = "peak_demand_serve"
    historical_collection = "peak_demand_serve_historical"
    entity = None  # state-level summary; no plant or exchange dimension
    # The serve collection is unique on `scheduled_at` alone (one document per
    # trading day); `time_block` is carried too because it is finer, and a dedupe
    # key may safely be finer than the natural key but never coarser.
    dedupe_key = ("scheduled_at", "time_block")

    def build_pipeline(self, *, datasets: Sequence[Dataset], filters: dict,
                       ctx: Optional[dict] = None) -> List[dict]:
        return self._default_pipeline(datasets=datasets, filters=filters, ctx=ctx)
