"""Supply group — plant-wise Declared Capacity and Scheduled Generation.

`supply_serve` is the one source group whose documents are *revisions*. The grid
republishes a schedule several times a day and each republish lands as a new
document carrying a higher `system_revision_number` (2144, 2145, 2146…), so a
`(scheduled_at, time_block, plant_id, dc_sg)` key can be present many times over.

There are two ways that bites, and both are silent:

1. **Within the range.** A superseded revision has to lose to the latest one. An
   `$avg` across revisions is not the declared capacity, it is a smear of the
   day's revision history.
2. **Across the `$unionWith`.** Verified in the local UAT import: on 2026-08-31,
   the day the rolling window rolled, the *same* key exists in both
   `supply_serve` and `supply_serve_historical` with the same revision and the
   same volume. `DECLARED_CAPACITY_TOTAL` and `SG_TOTAL` are `$sum` datasets, so
   each such key would be added twice and the total would read high.

Both are fixed by the same stage, and it has to sit **after** the union and
**before** the analytics `$group`: sort by revision, keep the last document per
key, put it back at the root. Nothing filters after that point — the date, block
and plant predicates all run in the initial `$match`, which is also what rides
into the union, so the sort only ever sees rows the user asked for.

**Why there is no 01:00 cut-off here.** PTC define Declared Capacity and Final
Schedule as the figures captured at 01:00 the next morning from the last revision
received by then, and a cut-off stage was built for it on 2026-09-18 and then
removed: the collapse below already returns exactly that value, and a filter could
only ever delete a row, never improve one. The ingest window shuts at midnight —
`supply_processor` routes a delivery date to `supply_serve` only while it is
"today or tomorrow", and a later revision goes straight to the historical
collection with `system_revision_number = 1`, which loses this collapse to the
archived copy. So the value is already frozen an hour before PTC's capture time.
Measured over the whole UAT extract: the latest revision lands at +14.05 h and the
last write at +23.94 h, both inside the delivery day. Do not reintroduce a cut-off
without first re-checking those two facts.

One further supply-only wrinkle: this is the only group holding both per-plant
datasets (`DC_PLANT`, `SG_PLANT`) and whole-portfolio totals
(`DECLARED_CAPACITY_TOTAL`, `SG_TOTAL`). Ticking one of each forces `plant_id`
into the shared `$group` `_id`, which would turn Declared Capacity into 34
identical per-plant series instead of one total. `rows_from` folds those back
together — see `_fold_scalar`.
"""
from typing import Any, Dict, List, Optional, Sequence

from ..registry import Dataset
from . import base

#: `series` values that mean "one line per entity"
ENTITY_SERIES = ("per_plant", "per_exchange")

#: revision counter; higher wins
REVISION_FIELD = "system_revision_number"

#: DC/SG discriminator — part of the key, because a plant has one of each per block
DC_SG_FIELD = "dc_sg"

#: temporary field holding the winning document between the collapse and the
#: `$replaceRoot`. Underscore-free and short; it never leaves the pipeline.
COLLAPSED_DOC = "doc"

#: aggregations whose per-entity partials can be recombined into a whole-portfolio
#: figure in Python. `avg` is absent on purpose: the mean of per-plant means is not
#: the mean over plants unless every plant contributed the same number of documents.
FOLDABLE_AGGS = ("sum", "max", "min")


def revision_collapse_stages(date_field: str = "scheduled_at",
                             block_field: str = "time_block",
                             entity_field: str = "plant_id") -> List[dict]:
    """Keep only the latest revision of each key.

    Ordering `$last` by a `$sort` is the documented Mongo idiom; the sort has to
    run on the unioned stream, not on either collection alone, or a key present in
    both survives twice.

    The archival stamp is the second sort key so that the two copies the rolling
    window leaves behind — same key, same revision, one in each collection — break
    their tie deterministically in favour of the live serve document. Descending
    puts the archived copy first and the serve copy last, where `$last` finds it.
    """
    return [
        {"$sort": {REVISION_FIELD: 1, base.ARCHIVED_AT_FIELD: -1}},
        {"$group": {
            "_id": {
                "d": f"${date_field}",
                "b": f"${block_field}",
                "p": f"${entity_field}",
                "t": f"${DC_SG_FIELD}",
            },
            COLLAPSED_DOC: {"$last": "$$ROOT"},
        }},
        {"$replaceRoot": {"newRoot": f"${COLLAPSED_DOC}"}},
    ]


@base.register
class SupplyAdapter(base.SourceGroupAdapter):
    """`supply_serve` ∪ `supply_serve_historical`, latest revision per key."""

    group = "supply"
    serve_collection = "supply_serve"
    historical_collection = "supply_serve_historical"
    entity = "plant_id"

    # ---------------------------------------------------------- pipeline

    def build_pipeline(self, *, datasets: Sequence[Dataset], filters: dict,
                       ctx: Optional[dict] = None) -> List[dict]:
        # No `dedupe_key` and no `base.dedupe_stages` here: the revision collapse
        # below already groups on the natural key and keeps one document, so it
        # subsumes the archival dedupe every other group has to add after its
        # union. Doing both would be a second sort of the same stream.
        self._validate(datasets)
        match = self._base_match(filters, datasets)
        stages: List[dict] = [{"$match": match}]
        stages.append(base.union_stage(self.historical_collection, match))
        stages += revision_collapse_stages(
            self.date_field, self.block_field, self.entity_field())
        stages.append(self._group_stage(datasets, filters))
        return stages

    # -------------------------------------------------------- validation

    def _validate(self, datasets: Sequence[Dataset]) -> None:
        super()._validate(datasets)
        if not self._wants_entity(datasets):
            return
        for d in datasets:
            if d.series not in ENTITY_SERIES and d.agg not in FOLDABLE_AGGS:
                raise ValueError(
                    f"Dataset {d.code} is a '{d.agg}' total; it cannot share a "
                    f"query with a per-plant dataset, because the group would be "
                    f"keyed on plant_id and an average of per-plant averages is "
                    f"not the average over plants. Query it on its own."
                )

    # -------------------------------------------------------- canonical rows

    def rows_from(self, docs, datasets: Sequence[Dataset]) -> List[base.CanonicalRow]:
        """Flatten, folding whole-portfolio totals back to a single series.

        When the tick list mixes per-plant and total datasets the `$group` `_id`
        carries `plant_id`, so each total arrives split across plants. Recombining
        here costs one pass over rows already in memory and keeps the alternative
        — a second aggregation against the same collection — off the table.
        """
        docs = list(docs or [])
        if not self._wants_entity(datasets):
            return super().rows_from(docs, datasets)

        per_entity = [d for d in datasets if d.series in ENTITY_SERIES]
        scalar = [d for d in datasets if d.series not in ENTITY_SERIES]
        if not scalar:
            return super().rows_from(docs, datasets)

        rows = super().rows_from(docs, per_entity) if per_entity else []
        rows.extend(self._fold_scalar(docs, scalar))
        return rows

    # -------------------------------------------------------------- helpers

    @staticmethod
    def _cell_value(doc: dict, dataset: Dataset):
        """This document's value for one dataset, with the `$sum` null guard applied."""
        if dataset.agg == "sum" and not doc.get(
                base.NULL_GUARD_PREFIX + dataset.code, 1):
            return None
        return doc.get(dataset.code)

    def _fold_scalar(self, docs: Sequence[dict],
                     datasets: Sequence[Dataset]) -> List[base.CanonicalRow]:
        """Recombine per-plant partials into one value per (date, block).

        A cell to which no plant contributed stays `None`. It does not become 0 —
        a block nobody declared capacity for has no declared capacity, which is a
        different statement from a declared capacity of zero.
        """
        # {(date_key, block_key): {dataset_code: value or None}}, insertion ordered
        cells: Dict[tuple, Dict[str, Any]] = {}
        for doc in docs:
            key = doc.get("_id") or {}
            date_key, block_key = key.get(base.ID_DATE), key.get(base.ID_BLOCK)
            if date_key is None or block_key is None:
                continue
            cell = cells.setdefault((str(date_key), str(block_key)),
                                    {d.code: None for d in datasets})
            for d in datasets:
                value = self._cell_value(doc, d)
                if value is None:
                    continue
                current = cell[d.code]
                if current is None:
                    cell[d.code] = value
                elif d.agg == "sum":
                    cell[d.code] = current + value
                elif d.agg == "max":
                    cell[d.code] = max(current, value)
                else:  # min — _validate has already rejected anything else
                    cell[d.code] = min(current, value)

        return [
            base.CanonicalRow(dataset=d.code, date_key=date_key,
                              block_key=block_key, entity=None, value=cell[d.code])
            for (date_key, block_key), cell in cells.items()
            for d in datasets
        ]
