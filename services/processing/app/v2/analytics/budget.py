"""Admission control for analytics exports.

Everything reduces to one number:
    cells = date_buckets x block_buckets x sum(series cardinality per dataset)

which is the value count of the file: `rows x columns`, where rows are
`days x block buckets` and columns are one per dataset-entity series. It is computed
from the filters alone, before any database call, so a request that cannot be served
fails in about a millisecond instead of dying part-way through a scan.

**The thresholds were re-scaled on 2026-09-20, and the reason matters.** They were
set when this screen rendered an on-screen grid and the workbook was one sheet per
dataset — a "cell" was a thing the browser had to lay out, and 150k of them was a
lot. The screen produces one long-form file now, and the same number buys far more:
a request PTC hit at 411,264 cells is 3,936 rows and **builds in 3.5 seconds**, yet
the old ceiling refused it outright.

Measured on this data set, XLSX write time (the dominant cost; Mongo and the pivot
are ~1s even for a year):

    rows x cols          cells      xlsx     size
    2,880 x  20         57,600      0.4s   0.2 MB
    35,040 x  20       700,800      4.8s   2.0 MB
    35,040 x 140     4,905,600     25.8s  11.3 MB
    105,120 x 140   14,716,800     77.4s  33.7 MB

So roughly **190k cells per second**. `INLINE` is set where the wait is short enough
to hold an HTTP request open; everything above it is built in the background with
progress reported to the caller, and nothing is refused for being merely large.
`MAX` is not a rendering limit — it is a **memory** limit: the pivot holds the whole
value matrix in RAM, and this is a single EC2 box that also serves the bidding
screens.
"""
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional, Sequence

from . import registry

#: Served inside the request — a wait short enough to hold the connection open.
BAND_INLINE = "inline"
#: Also served inside the request; CSV streams, so it can afford more than XLSX.
BAND_INLINE_CAPPED = "inline_capped"
#: Built in the background with progress, then downloaded. Not a refusal.
BAND_ASYNC_ONLY = "async_only"
#: Refused. Only the memory cap reaches this.
BAND_REFUSED = "refused"

#: ~190k cells/s measured (see the module docstring), so:
#:   inline        1.5M  ≈  8s — held open
#:   inline_capped 3.0M  ≈ 16s — CSV only, which streams
#:   max          20.0M         — MEMORY, not time: the pivot holds the whole value
#:                               matrix in Python lists, ~24 bytes a float before
#:                               list overhead, so 20M cells is already ~460MB
#:                               resident on a box that is also serving bids. Three
#:                               years of every live dataset (14.7M) fits under it;
#:                               four does not, and that is the intended line.
THRESHOLDS = {"inline": 1_500_000, "inline_capped": 3_000_000, "max": 20_000_000}


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
    """Concrete ways to make the request smaller, each priced.

    Only the memory cap refuses now, so for the middle bands these are an offer
    rather than a condition. Every suggestion must actually shrink the result, so
    each is filtered against the current estimate before it is offered. "Switch
    Group Date By to Week-wise" used to be on this list and was removed on
    2026-09-20: the pill no longer collapses dates, so following that advice
    changed the cell count by nothing.
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
    # Everything but a comfortably inline request carries advice: a refusal needs a
    # way out, an `inline_capped` query is served but heavy enough that the banner
    # offers to narrow it, and an `async_only` export is produced but slow enough
    # that "this would be faster if…" is worth saying.
    sugg = _suggestions(date_buckets, block_buckets, datasets, ctx, cells) \
        if band != BAND_INLINE else []
    return Estimate(cells=cells, band=band, date_buckets=date_buckets,
                    block_buckets=block_buckets, series=series, suggestions=sugg)
