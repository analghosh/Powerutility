"""Admission control for analytics queries.

Everything reduces to one number:
    cells = date_buckets x block_buckets x sum(series cardinality per dataset)

It is computed from the filters alone, before any database call, so a request that
cannot be served fails in about a millisecond with concrete advice instead of dying
part-way through a scan.
"""
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional, Sequence

from . import registry

BAND_INLINE = "inline"
BAND_INLINE_CAPPED = "inline_capped"
BAND_ASYNC_ONLY = "async_only"
BAND_REFUSED = "refused"

THRESHOLDS = {"inline": 150_000, "inline_capped": 500_000, "max": 10_000_000}


@dataclass
class Estimate:
    cells: int
    band: str
    date_buckets: int
    block_buckets: int
    series: int
    suggestions: List[Dict] = field(default_factory=list)


def date_bucket_count(from_date: date, to_date: date, grouping: str) -> int:
    days = (to_date - from_date).days + 1
    if days < 1:
        return 0
    if grouping == "HOURLY_WISE":
        return days * 24
    if grouping in ("BLOCK_WISE", "DATE_WISE"):
        return days
    if grouping == "WEEK_WISE":
        # Distinct ISO weeks touched, not ceil(days/7): Sat 29 → Mon 31 Aug is 3 days
        # but TWO weeks, and the engine keys on the ISO week, so the estimate must too.
        # Underestimating lets a request through that is bigger than promised.
        first = from_date.isocalendar()
        last = to_date.isocalendar()
        # ISO weeks are contiguous across the year boundary; count via ordinal Mondays.
        first_monday = from_date.toordinal() - (first[2] - 1)
        last_monday = to_date.toordinal() - (last[2] - 1)
        return max(1, (last_monday - first_monday) // 7 + 1)
    if grouping == "MONTH_WISE":
        return max(1, (to_date.year - from_date.year) * 12 + (to_date.month - from_date.month) + 1)
    if grouping == "YEAR_WISE":
        return max(1, to_date.year - from_date.year + 1)
    return days


def _classify(cells: int, thresholds: Dict[str, int]) -> str:
    if cells <= thresholds["inline"]:
        return BAND_INLINE
    if cells <= thresholds["inline_capped"]:
        return BAND_INLINE_CAPPED
    if cells <= thresholds["max"]:
        return BAND_ASYNC_ONLY
    return BAND_REFUSED


def _suggestions(date_buckets, block_buckets, datasets, ctx, cells) -> List[Dict]:
    out: List[Dict] = []
    series = sum(registry.series_cardinality(registry.get(c), ctx) for c in datasets)

    if block_buckets > 1:
        out.append({
            "kind": "coarser_blocks",
            "cells": date_buckets * 1 * series,
            "message": "Group blocks into a band instead of individual blocks",
        })
    if date_buckets > 7:
        weekly = max(1, (date_buckets + 6) // 7)
        out.append({
            "kind": "coarser_dates",
            "cells": weekly * block_buckets * series,
            "message": "Switch Group Date By to Week-wise",
        })
    if any(registry.get(c).series == "per_plant" for c in datasets):
        reduced = sum(
            5 if registry.get(c).series == "per_plant"
            else registry.series_cardinality(registry.get(c), ctx)
            for c in datasets
        )
        out.append({
            "kind": "fewer_plants",
            "cells": date_buckets * block_buckets * reduced,
            "message": "Select 5 plants instead of all",
        })
    return [s for s in out if s["cells"] < cells]


def estimate(*, date_buckets: int, block_buckets: int,
             datasets: Sequence[str], ctx: Dict[str, int],
             thresholds: Optional[Dict[str, int]] = None) -> Estimate:
    th = thresholds or THRESHOLDS
    series = sum(registry.series_cardinality(registry.get(c), ctx) for c in datasets)
    cells = date_buckets * block_buckets * series
    band = _classify(cells, th)
    sugg = _suggestions(date_buckets, block_buckets, datasets, ctx, cells) \
        if band in (BAND_INLINE_CAPPED, BAND_ASYNC_ONLY, BAND_REFUSED) else []
    return Estimate(cells=cells, band=band, date_buckets=date_buckets,
                    block_buckets=block_buckets, series=series, suggestions=sugg)
