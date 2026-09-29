"""DSM Published source group — `exchange_dsm_report_day`.

The DSM statement as the RPC published it, uploaded through the Report Uploader
(DSM Report category). One dataset, DSM_PUBLISHED, whose series are the
statement's own figures — Schedule Total, Actual Total, Frequency, Deviation
Rate, Deviation, Overdrawl and Underdrawl Charges — so one tick gives the whole
statement, a column per figure, in the order the RPC prints them.

The storage is not shaped like the other groups and the pipeline reshapes it
first:

* **One document per settlement day, blocks inline.** `report_date` is an ISO
  date string, not a datetime, and each document carries its 96 blocks in a
  `blocks` array. The pipeline unwinds the blocks, then unwinds the figures so
  each (day, block, figure) is one row whose entity is the figure's name, and
  builds the `scheduled_at` / `time_block` pair every other group is keyed on.
* **No historical twin, no revisions.** A reissued statement replaces the days
  it carries (unique on `report_date`), so there is nothing to dedupe.
* **Two aggregation rules in one dataset.** When blocks are grouped into bands,
  the charges are money and add up; the rest are MW, Hz and rates, and average.
  Both accumulators are computed and `rows_from` keeps the one each figure's rule
  names — a single `agg` on the dataset would get one half of the statement wrong.
"""
from typing import Dict, List, Optional, Sequence

from .. import block_bands, grouping, registry
from ..registry import Dataset
from .base import (ID_BLOCK, ID_DATE, ID_ENTITY, CanonicalRow,
                   SourceGroupAdapter, _as_day, _resolve_band, register)

#: Accumulator field names in the `$group` output.
AVG_FIELD = "avg"
SUM_FIELD = "sum"
#: How many rows carried a value, so an all-null `$sum` reads as None, not 0.
COUNT_FIELD = "n"


@register
class DsmPublishedAdapter(SourceGroupAdapter):
    """The uploaded DSM statement, one series per published figure."""

    group = "dsm_published"
    serve_collection = "exchange_dsm_report_day"
    historical_collection = None
    entity = "field"
    # Set by the reshape below, not stored on the documents.
    date_field = "scheduled_at"
    block_field = "time_block"

    def build_pipeline(self, *, datasets: Sequence[Dataset], filters: dict,
                       ctx: Optional[dict] = None) -> List[dict]:
        self._validate(datasets)
        start = _as_day(filters.get("from_date"), "from_date")
        end = _as_day(filters.get("to_date"), "to_date")
        if end < start:
            raise ValueError(f"from_date ({start.date()}) is after to_date ({end.date()})")
        from_block = filters.get("from_block", block_bands.MIN_BLOCK)
        to_block = filters.get("to_block", block_bands.MAX_BLOCK)
        block_bands.validate_block_range(from_block, to_block)

        band = _resolve_band(filters)
        custom = filters.get("custom_blocks", filters.get("custom"))
        figures = [{"k": field, "v": f"$blocks.{field}"}
                   for field in registry.DSM_PUBLISHED_FIELDS]

        return [
            # ISO strings compare in date order, so the index on report_date serves this.
            {"$match": {"report_date": {"$gte": start.date().isoformat(),
                                        "$lte": end.date().isoformat()}}},
            {"$unwind": "$blocks"},
            {"$match": {"blocks.block": {"$gte": from_block, "$lte": to_block}}},
            {"$project": {
                "_id": 0,
                # Midnight IST, so the IST day key below reads back the same date.
                self.date_field: {"$dateFromString": {
                    "dateString": "$report_date", "format": grouping.DAY_FORMAT,
                    "timezone": grouping.TIMEZONE}},
                self.block_field: "$blocks.block",
                "figures": figures,
            }},
            {"$unwind": "$figures"},
            {"$group": {
                "_id": {
                    ID_DATE: grouping.day_key_expr(f"${self.date_field}"),
                    ID_BLOCK: grouping.block_key_expr(band, custom),
                    ID_ENTITY: "$figures.k",
                },
                AVG_FIELD: {"$avg": "$figures.v"},
                SUM_FIELD: {"$sum": "$figures.v"},
                COUNT_FIELD: {"$sum": {"$cond": [
                    {"$in": [{"$type": "$figures.v"}, ["missing", "null"]]}, 0, 1]}},
            }},
        ]

    def rows_from(self, docs, datasets: Sequence[Dataset]) -> List[CanonicalRow]:
        rows: List[CanonicalRow] = []
        for doc in docs or []:
            key: Dict = doc.get("_id") or {}
            date_key, block_key = key.get(ID_DATE), key.get(ID_BLOCK)
            if date_key is None or block_key is None:
                continue
            field = key.get(ID_ENTITY)
            if not doc.get(COUNT_FIELD):
                value = None
            elif field in registry.DSM_PUBLISHED_SUMMED:
                value = doc.get(SUM_FIELD)
            else:
                value = doc.get(AVG_FIELD)
            for d in datasets:
                rows.append(CanonicalRow(
                    dataset=d.code, date_key=str(date_key), block_key=str(block_key),
                    entity=None if field is None else str(field), value=value))
        return rows
