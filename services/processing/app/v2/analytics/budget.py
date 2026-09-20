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


def date_bucket_count(from_date: date, to_date: date,
                      grouping: Optional[str] = None) -> int:
    """Date buckets in the range: one per trading day, whatever the grouping.

    `grouping` is accepted and ignored. Since 2026-09-20 Group Date By adds a column
    to the file rather than collapsing the date axis, so a week-wise request costs
    exactly what a block-wise one does — the parameter stays only so the existing
    call sites and stored jobs keep working. The roll-up datasets aggregate over
    their own period but are then written against each day of it, so they occupy the
    same cells as everything else.
    """
    days = (to_date - from_date).days + 1
    return days if days > 0 else 0


def _classify(cells: int, thresholds: Dict[str, int]) -> str:
    if cells <= thresholds["inline"]:
        return BAND_INLINE
    if cells <= thresholds["inline_capped"]:
        return BAND_INLINE_CAPPED
    if cells <= thresholds["max"]:
        return BAND_ASYNC_ONLY
    return BAND_REFUSED


def _suggestions(date_buckets, block_buckets, datasets, ctx, cells) -> List[Dict]:
    """Concrete ways to get under the ceiling, each priced.

    Every suggestion must actually shrink the result, so each is filtered against
    the current estimate before it is offered. "Switch Group Date By to Week-wise"
    used to be on this list and was removed on 2026-09-20: the pill no longer
    collapses dates, so following that advice changed the cell count by nothing.
    """
    out: List[Dict] = []
    series = sum(registry.series_cardinality(registry.get(c), ctx) for c in datasets)

    if block_buckets > 1:
        out.append({
            "kind": "coarser_blocks",
            "cells": date_buckets * 1 * series,
            "message": "Group blocks into a band instead of individual blocks",
        })
    if date_buckets > 1:
        halved = max(1, date_buckets // 2)
        out.append({
            "kind": "shorter_range",
            "cells": halved * block_buckets * series,
            "message": f"Halve the date range to about {halved} day(s)",
        })
    if any(registry.get(c).series == "per_plant" for c in datasets):
        # Plant-wise datasets carry one series per plant — 34 locally — so they are
        # almost always what pushed a selection over. There is no plant picker
        # (2026-09-18 decision: every plant is always returned), so the only lever
        # the user has is the tick itself.
        without = sum(
            registry.series_cardinality(registry.get(c), ctx)
            for c in datasets if registry.get(c).series != "per_plant"
        )
        out.append({
            "kind": "fewer_datasets",
            "cells": date_buckets * block_buckets * without,
            "message": "Untick the plant-wise data sets, which carry one column per plant",
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
