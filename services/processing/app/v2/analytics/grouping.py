"""Grouping is pushed into Mongo's $group, never done in Python.

This is what makes long ranges tractable: month-wise over five months returns about
five rows per dataset instead of 14,400. The pathological case is only ever a long
range at individual-block granularity.

Two details are silently wrong if left to the defaults:

* Week keys use the ISO year (%G), not the calendar year (%Y). 29 Dec 2025 is ISO
  week 2026-W01; formatted with %Y it reads "2025-W01" and averages into the same
  bucket as the first week of January 2025.
* Every date key carries an explicit timezone. EPMS stores UTC instants, the day is
  an IST trading day, and blocks 1-4 of every day fall on the previous UTC date.
"""
from typing import Optional, Sequence

from .block_bands import BAND_CUSTOM, BAND_INDIVIDUAL, Band, validate_block_range

try:  # pragma: no cover - the setting lands with the analytics config block
    from ...core.config import settings
    TIMEZONE: str = getattr(settings, "ANALYTICS_TIMEZONE", "Asia/Kolkata")
except Exception:  # pragma: no cover - keeps the module importable standalone
    TIMEZONE = "Asia/Kolkata"

DATE_GROUPINGS = ("BLOCK_WISE", "DATE_WISE", "HOURLY_WISE",
                  "WEEK_WISE", "MONTH_WISE", "YEAR_WISE")

DATE_FORMATS = {
    "BLOCK_WISE": "%Y-%m-%d",
    "DATE_WISE": "%Y-%m-%d",
    "HOURLY_WISE": "%Y-%m-%d %H",
    "WEEK_WISE": "%G-W%V",
    "MONTH_WISE": "%Y-%m",
    "YEAR_WISE": "%Y",
}

AGGREGATIONS = ("avg", "sum", "max", "min", "last")

BLOCK_FIELD = "$time_block"


def date_key_expr(grouping: str, field: str = "$scheduled_at") -> dict:
    """Mongo expression producing a sortable string date key for one grouping."""
    fmt = DATE_FORMATS.get(grouping)
    if fmt is None:
        raise ValueError(f"Unknown date grouping: {grouping}")
    return {"$dateToString": {"format": fmt, "date": field, "timezone": TIMEZONE}}


def _branch(lo: int, hi: int, label: str) -> dict:
    return {
        "case": {"$and": [{"$gte": [BLOCK_FIELD, lo]}, {"$lte": [BLOCK_FIELD, hi]}]},
        "then": label,
    }


def block_key_expr(band: Band, custom: Optional[Sequence[Sequence[int]]] = None) -> dict:
    """Mongo expression producing the block bucket key.

    INDIVIDUAL keys on the block number itself, as a string, so the key matches what
    `block_bands.bucket_for` returns (unpadded — order numerically, not lexically).
    A named band collapses each of its ranges onto the band code. CUSTOM gives every
    supplied range its own "lo-hi" bucket. Blocks outside every range key to None,
    never to 0 — a block that belongs to no bucket has no value, it does not have
    zero.
    """
    if band.code == BAND_INDIVIDUAL:
        return {"$toString": BLOCK_FIELD}

    if band.code == BAND_CUSTOM:
        ranges = [tuple(r) for r in (custom or [])]
        if not ranges:
            raise ValueError("CUSTOM band needs at least one block range")
        for lo, hi in ranges:
            validate_block_range(lo, hi)
        branches = [_branch(lo, hi, f"{lo}-{hi}") for lo, hi in ranges]
    else:
        if not band.ranges:
            raise ValueError(
                f"Band {band.code} has no block ranges; it is inactive until PTC "
                f"supplies them"
            )
        for lo, hi in band.ranges:
            validate_block_range(lo, hi)
        branches = [_branch(lo, hi, band.code) for lo, hi in band.ranges]

    return {"$switch": {"branches": branches, "default": None}}


def accumulator(agg: str, value_expr: str) -> dict:
    """The $group accumulator for a dataset's aggregation rule."""
    if agg not in AGGREGATIONS:
        raise ValueError(f"Unknown aggregation rule: {agg}")
    return {f"${agg}": value_expr}
