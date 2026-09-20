"""Base class every source group adapter subclasses.

One adapter owns one MongoDB source group. It turns a set of ticked datasets plus
the user's filters into a single aggregation pipeline, and turns the grouped
documents back into flat `CanonicalRow`s that the pivot can lay out without knowing
anything about where they came from.

Three rules are baked in here because getting any of them wrong is silent rather
than loud:

1. **serve + historical, minus the overlap.** `*_serve` holds only a rolling ~3-day
   window; everything older is in `*_serve_historical`. A range that crosses that
   boundary loses data with no error unless the pipeline `$unionWith`s the pair.
   But the hand-off is not clean: the archival copies a day into historical and
   only deletes it from serve some days later, so for the length of the window the
   same document is in both and the union returns it twice. Every adapter with a
   historical twin therefore declares a `dedupe_key` and the base class splices
   `dedupe_stages` in after the union — without it every `$sum` dataset reads
   exactly double on the most recent dates. A group with no historical twin sets
   `historical_collection = None`: no union is emitted and no dedupe is paid for.
   (No live group is like that since the DAM-schedule group was removed on
   2026-09-18, but the base keeps the path — unioning a collection that does not
   exist is not an error either, it just returns nothing.)

2. **A dataset's own discriminator goes inside its accumulator.** `MAPE_DAF` is
   `type="DAF"`; `ACTUAL_DEMAND` has no discriminator at all. Putting `type="DAF"`
   in the shared `$match` would delete every `ACTUAL_DEMAND` row from the result of
   the same query. The discriminator therefore becomes a `$cond` inside that one
   dataset's accumulator. The only exception is a discriminator that *every*
   selected dataset shares, which is hoisted into the `$match` as a free prefilter —
   it cannot cost another dataset any rows because there is no other dataset.

3. **A missing value is `None`, never `0`.** The `$cond` falls back to `None`, and
   `$avg`/`$max`/`$min` already return `null` for an all-null group. `$sum` does
   not — it returns `0` — so a summed dataset also carries a null-guard counter and
   `rows_from` reports `None` when nothing was summed. Without it an uncleared
   HPDAM block would render as a traded volume of zero rather than as no trade.
"""
from abc import ABC, abstractmethod
from datetime import date, datetime, timedelta
from typing import (Any, Dict, Iterable, List, Optional, Sequence, Tuple,
                    TypedDict, Union)

from .. import block_bands, grouping
from ..registry import Dataset

# `$group` `_id` sub-keys. Short on purpose: this document is emitted once per
# output cell, and at individual-block granularity that is hundreds of thousands
# of documents crossing the wire.
ID_DATE = "d"
ID_BLOCK = "b"
ID_ENTITY = "e"

# Prefix for the per-dataset null-guard counter. Dataset codes are [A-Z0-9_], so a
# leading "_n_" can never collide with one.
NULL_GUARD_PREFIX = "_n_"

# Which filter key supplies the entity allow-list for a given entity field.
ENTITY_FILTER_KEYS = {
    "plant_id": "plants",
    "px_name": "exchanges",
    "exchange": "exchanges",
}

class CanonicalRow(TypedDict):
    """One value, fully addressed. Every adapter returns these and nothing else."""
    dataset: str
    date_key: str
    block_key: str
    entity: Optional[str]
    value: Optional[float]


def union_stage(historical_collection: str, match: dict) -> dict:
    """serve holds only a rolling ~3-day window; everything older lives in
    <collection>_historical. A range that crosses that boundary silently loses data
    without this stage, which is the single most common way to get these queries wrong.
    """
    return {
        "$unionWith": {
            "coll": historical_collection,
            "pipeline": [{"$match": match}],
        }
    }


#: Stamped on a document when the archival copies it into the historical twin, and
#: never present on a live serve document. Used only as a sort tiebreak, so a
#: historical document that somehow lacks it costs nothing: the two copies are
#: identical, and the dedupe collapses them either way.
ARCHIVED_AT_FIELD = "archived_at"

#: Holds the surviving document between the dedupe `$group` and its `$replaceRoot`.
DEDUPED_DOC = "doc"


def dedupe_stages(key_fields: Sequence[str]) -> List[dict]:
    """Collapse the copy the archival window leaves in **both** collections.

    The rolling window is not a clean hand-off. Every archival run copies
    everything older than today into the historical twin but only deletes from
    serve what is older than `today - days_to_keep`, so for those few days the
    same document is in both collections — verified live: on the overlap day
    `exchange_serve` and `exchange_serve_historical` each held 671 documents, key
    for key. `$unionWith` therefore returns it twice, and every `$sum` dataset
    reads exactly double. Measured before this stage existed: `ACV_RTM` for one
    IEX block came back as 9,486.18 against a stored 4,743.09, and the portfolio
    traded volume as 44 MW against a stored 22. Those are the money columns, and
    the dates they are wrong on are today and yesterday.

    `key_fields` must be at least as fine as the collection's natural key — too
    fine merely leaves a duplicate that cannot occur (the copies are byte
    identical apart from the archival stamp), while too coarse would merge two
    genuinely different rows and lose one of them.

    Sorting the stamp descending puts the archived copy first and the live one
    last, so `$last` keeps the serve document: if the two ever differ, serve is
    the newer of the pair.
    """
    if not key_fields:
        raise ValueError("dedupe_stages needs the collection's natural key")
    return [
        {"$sort": {ARCHIVED_AT_FIELD: -1}},
        {"$group": {
            "_id": {f: f"${f}" for f in key_fields},
            DEDUPED_DOC: {"$last": "$$ROOT"},
        }},
        {"$replaceRoot": {"newRoot": f"${DEDUPED_DOC}"}},
    ]


def value_expr(dataset: Dataset) -> Union[str, dict]:
    """The expression this dataset's accumulator consumes.

    A dataset's own match (e.g. type=DAF) must not filter the shared $match, or a
    second dataset on the same group loses its rows — so it becomes a $cond here.
    A document that does not match contributes `None`, never `0`.
    """
    if not dataset.value:
        raise ValueError(
            f"Dataset {dataset.code} has no value field; it is not queryable "
            f"(availability={dataset.availability})"
        )
    value: Union[str, dict] = dataset.value
    sentinel = getattr(dataset, "sentinel_null", None)
    if sentinel is not None:
        # The source writes this value to mean "absent" (see registry.sentinel_null);
        # turn it into a real gap before the accumulator ever sees it.
        value = {"$cond": [{"$eq": [dataset.value, sentinel]}, None, dataset.value]}
    if not dataset.match:
        return value
    cond = {"$and": [{"$eq": [f"${k}", v]} for k, v in dataset.match.items()]}
    return {"$cond": [cond, value, None]}


#: `$type` values meaning "this document carried no value". `missing` is a field
#: that is not present at all; `null` is one stored as an explicit null.
NO_VALUE_TYPES = ("missing", "null")


def null_guard_expr(dataset: Dataset) -> dict:
    """Counts the documents that actually contributed a value to a `$sum`.

    `$sum` over an all-null group returns 0, which is indistinguishable from a
    genuine total of zero. This counter makes the two distinguishable again.

    The test has to be on `$type`, not on `$ne: [..., null]`. In an aggregation
    expression a **missing** field is not equal to `null` — verified against
    MongoDB 7.0, `{"$ne": ["$no_such_field", null]}` evaluates to `true` — so a
    comparison guard counted every document whose field was simply absent. Proved
    live against the UAT import: `demand_serve_historical` has 1,056 documents
    with `net_dsm_amount` missing and 1,459 with it null, and a block where every
    document was one or the other reported an OD/UD Amount of ₹0 rather than "no
    amount recorded". `$type` reports `missing` and `null` distinctly from any
    real value, including a stored `0`, which must keep counting.
    """
    return {"$sum": {"$cond": [
        {"$in": [{"$type": value_expr(dataset)}, list(NO_VALUE_TYPES)]}, 0, 1]}}


_MISSING = object()


def common_match(datasets: Sequence[Dataset]) -> Dict[str, Any]:
    """Discriminators every selected dataset agrees on, safe to hoist into `$match`.

    Empty whenever the datasets disagree — including the common case where one of
    them has no discriminator at all.
    """
    if not datasets:
        return {}
    shared = dict(datasets[0].match or {})
    for d in datasets[1:]:
        other = d.match or {}
        shared = {k: v for k, v in shared.items() if other.get(k, _MISSING) == v}
        if not shared:
            break
    return shared


def _as_day(value: Any, name: str) -> datetime:
    """Normalise a filter bound to naive midnight, matching how EPMS stores dates.

    `scheduled_at` is the trading date stamped at midnight; the time of day lives in
    `time_block`. Bounds therefore snap to the day.
    """
    if value is None:
        raise ValueError(f"{name} is required; an unbounded analytics scan is refused")
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if isinstance(value, datetime):
        return datetime(value.year, value.month, value.day)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    raise ValueError(f"{name} must be a date, datetime or ISO string, got {value!r}")


def _resolve_band(filters: dict) -> block_bands.Band:
    """The band this filter set groups by.

    A named band's ranges travel in `block_band_ranges`, stamped by the service from
    the band's parameter when the request was accepted — so a queued export replays
    with the definition it was made under, and this module never reads MongoDB.
    """
    band = filters.get("block_band") or block_bands.BAND_INDIVIDUAL
    if isinstance(band, block_bands.Band):
        return band
    try:
        base = block_bands.DEFAULT_BANDS[band]
    except KeyError:
        raise ValueError(f"Unknown block band: {band}") from None
    ranges = filters.get("block_band_ranges")
    if ranges and band in block_bands.PARAMETER_KEYS:
        return block_bands.band_with_ranges(base, block_bands.parse_ranges(ranges))
    return base


class SourceGroupAdapter(ABC):
    """One MongoDB source group, one pipeline, one set of canonical rows."""

    #: registry `group` value this adapter serves
    group: str = ""
    #: rolling-window collection
    serve_collection: str = ""
    #: its historical twin, or None when the group has none
    historical_collection: Optional[str] = None
    #: the collection's natural key, used to drop the copy the archival window
    #: leaves in both collections. Required whenever `historical_collection` is
    #: set; see `dedupe_stages`.
    dedupe_key: Tuple[str, ...] = ()
    #: field carrying the per-series entity (plant, exchange), or None
    entity: Optional[str] = None
    #: filter key holding the entity allow-list; derived from `entity` when unset
    entity_filter_key: Optional[str] = None
    #: date field on this group's documents
    date_field: str = "scheduled_at"
    #: block field on this group's documents
    block_field: str = "time_block"

    # ------------------------------------------------------------- contract

    def entity_field(self) -> Optional[str]:
        """The per-series dimension, or None for a group that has none."""
        return self.entity

    @abstractmethod
    def build_pipeline(self, *, datasets: Sequence[Dataset], filters: dict,
                       ctx: Optional[dict] = None) -> List[dict]:
        """The single aggregation pipeline serving every ticked dataset here."""

    def rows_from(self, docs: Iterable[dict],
                  datasets: Sequence[Dataset]) -> List[CanonicalRow]:
        """Flatten grouped documents into one row per (document, dataset).

        A `None` value stays `None`. A summed dataset that matched no documents is
        reported as `None` rather than as the `0` Mongo returns.
        """
        rows: List[CanonicalRow] = []
        for doc in docs or []:
            key = doc.get("_id") or {}
            date_key = key.get(ID_DATE)
            block_key = key.get(ID_BLOCK)
            if date_key is None or block_key is None:
                # The block fell outside every band range, so it belongs to no
                # bucket and cannot be placed on an axis. Dropping beats inventing
                # a bucket for it.
                continue
            entity = key.get(ID_ENTITY)
            for d in datasets:
                value = doc.get(d.code)
                if d.agg == "sum" and not doc.get(NULL_GUARD_PREFIX + d.code, 1):
                    value = None
                rows.append(CanonicalRow(
                    dataset=d.code,
                    date_key=str(date_key),
                    block_key=str(block_key),
                    entity=None if entity is None else str(entity),
                    value=value,
                ))
        return rows

    # -------------------------------------------------------------- helpers

    def _validate(self, datasets: Sequence[Dataset]) -> None:
        if not datasets:
            raise ValueError(f"No datasets selected for source group '{self.group}'")
        for d in datasets:
            if d.kind != "direct":
                raise ValueError(
                    f"Dataset {d.code} is {d.kind}; derived datasets are resolved "
                    f"after the source groups return, not in a pipeline"
                )
            # Raises for a dataset with no value field. An `absent` dataset would
            # otherwise emit {"$avg": None} and return nulls for every block, which
            # reads as "no data in this range" rather than "not implemented".
            value_expr(d)

    def _entity_values(self, filters: dict) -> Optional[list]:
        field = self.entity_field()
        if not field:
            return None
        key = self.entity_filter_key or ENTITY_FILTER_KEYS.get(field)
        if not key:
            return None
        values = filters.get(key)
        return list(values) if values else None

    def _base_match(self, filters: dict, datasets: Sequence[Dataset]) -> dict:
        """Date range, block range, entity allow-list and shared discriminators.

        Everything here runs before `$group`, so it is what the indexes can serve.
        """
        start = _as_day(filters.get("from_date"), "from_date")
        end = _as_day(filters.get("to_date"), "to_date")
        if end < start:
            raise ValueError(f"from_date ({start.date()}) is after to_date ({end.date()})")

        from_block = filters.get("from_block", block_bands.MIN_BLOCK)
        to_block = filters.get("to_block", block_bands.MAX_BLOCK)
        block_bands.validate_block_range(from_block, to_block)

        match: Dict[str, Any] = {
            self.date_field: {
                "$gte": start,
                # to_date is inclusive, so the exclusive bound is the next midnight
                "$lt": end + timedelta(days=1),
            },
            self.block_field: {"$gte": from_block, "$lte": to_block},
        }

        entities = self._entity_values(filters)
        if entities:
            match[self.entity_field()] = {"$in": entities}

        match.update(common_match(datasets))
        return match

    def _wants_entity(self, datasets: Sequence[Dataset]) -> bool:
        """True when at least one selected dataset is reported per entity.

        `DECLARED_CAPACITY_TOTAL` sums plants into one number; keying the group on
        `plant_id` for it would silently turn one series into thirty-four.
        """
        return bool(self.entity_field()) and any(
            d.series in ("per_plant", "per_exchange", "per_forecast_type")
            for d in datasets)

    def _group_stage(self, datasets: Sequence[Dataset], filters: dict) -> dict:
        """The analytics `$group`: one `_id` per output cell, one field per dataset.

        The date key is always the trading **day**. The user's Group Date By never
        reaches here: it chooses identity columns in the file, not buckets in Mongo.
        Before 2026-09-20 it keyed this `_id`, which is how Block-wise and Date-wise
        came to produce identical files, and how one ticked weekly dataset silently
        rewrote the date axis for everything else in the request.
        """
        band = _resolve_band(filters)
        custom = filters.get("custom_blocks", filters.get("custom"))

        _id: Dict[str, Any] = {
            ID_DATE: grouping.day_key_expr(f"${self.date_field}"),
            ID_BLOCK: grouping.block_key_expr(band, custom),
        }
        if self._wants_entity(datasets):
            _id[ID_ENTITY] = f"${self.entity_field()}"

        stage: Dict[str, Any] = {"_id": _id}
        for d in datasets:
            stage[d.code] = grouping.accumulator(d.agg, value_expr(d))
            if d.agg == "sum":
                stage[NULL_GUARD_PREFIX + d.code] = null_guard_expr(d)
        return {"$group": stage}

    def _default_pipeline(self, *, datasets: Sequence[Dataset], filters: dict,
                          ctx: Optional[dict] = None) -> List[dict]:
        """match -> union historical -> group. The shape most groups need verbatim.

        A group that has to collapse revisions or reshape documents first overrides
        `build_pipeline` and splices its own stages between the union and the group.
        """
        self._validate(datasets)
        match = self._base_match(filters, datasets)
        stages: List[dict] = [{"$match": match}]
        if self.historical_collection:
            stages.append(union_stage(self.historical_collection, match))
            if not self.dedupe_key:
                raise ValueError(
                    f"{type(self).__name__} unions '{self.historical_collection}' "
                    f"but declares no dedupe_key. The archival leaves the same "
                    f"document in both collections for the length of the rolling "
                    f"window, so every summed dataset would read double."
                )
            stages += dedupe_stages(self.dedupe_key)
        stages.append(self._group_stage(datasets, filters))
        return stages


# --------------------------------------------------------------- registration

ADAPTERS: Dict[str, SourceGroupAdapter] = {}


def register(adapter):
    """Register an adapter under its group. Usable directly or as a class decorator.

    Returns whatever it was given, so `@register` above a class leaves the class
    bound to its own name.
    """
    obj = adapter() if isinstance(adapter, type) else adapter
    if not isinstance(obj, SourceGroupAdapter):
        raise TypeError(f"{adapter!r} is not a SourceGroupAdapter")
    if not obj.group:
        raise ValueError(f"{type(obj).__name__} does not declare a group")
    if not obj.serve_collection:
        raise ValueError(f"{type(obj).__name__} does not declare a serve_collection")
    if obj.group in ADAPTERS:
        raise ValueError(
            f"An adapter is already registered for group '{obj.group}': "
            f"{type(ADAPTERS[obj.group]).__name__}"
        )
    ADAPTERS[obj.group] = obj
    return adapter


def unregister(group: str) -> None:
    """Drop a registration. Exists so tests do not leak into each other."""
    ADAPTERS.pop(group, None)


def get_adapter(group: str) -> SourceGroupAdapter:
    try:
        return ADAPTERS[group]
    except KeyError:
        raise KeyError(f"No adapter registered for source group '{group}'") from None
