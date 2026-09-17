"""Scheduling source group — `serve_dam_scheduling`, plant-wise final schedule.

The one group with **no historical twin**: everything ever scheduled stays in this
collection (469,439 documents locally, back to February). `historical_collection` is
therefore `None` and the base class emits no `$unionWith`. That matters more than it
looks — `$unionWith` on a collection that does not exist is *not* an error in Mongo,
it simply contributes nothing, so an accidental union here would never surface at
runtime and would only ever cost a wasted plan stage.

It also does **not** need the latest-revision collapse that `supply_serve` needs,
despite carrying a `system_revision_number`. Verified against the local UAT import:
grouping 2026-02-04 by `(time_block, plant_id)` yields 3,168 keys, every one of them
with a count of exactly 1. The revision is a stamp of which upload wrote the row, not
a second copy of it. Collapsing anyway would add a sort of half a million documents
to pay for nothing.

Per-plant, and on a wider plant universe than supply: 53 `plant_id` values here
against `supply_serve`'s 34 and `plant_master`'s 35, which is why the plant selector
has to be dataset-aware. The plant allow-list is applied in the initial `$match` so
asking for one plant reads one plant's documents rather than all 53.
"""
from typing import List, Optional, Sequence

from ..registry import Dataset
from .base import SourceGroupAdapter, register


@register
class SchedulingAdapter(SourceGroupAdapter):
    """Plant-wise final schedule from `serve_dam_scheduling`."""

    group = "scheduling"
    serve_collection = "serve_dam_scheduling"
    historical_collection = None  # no historical twin; see the module docstring
    entity = "plant_id"

    def build_pipeline(self, *, datasets: Sequence[Dataset], filters: dict,
                       ctx: Optional[dict] = None) -> List[dict]:
        # match -> group. The base class skips the union because
        # historical_collection is None.
        return self._default_pipeline(datasets=datasets, filters=filters, ctx=ctx)
