"""How much of each rolled-up period the requested date range actually covers.

A weekly roll-up over Wed 26 → Fri 28 Aug returns a bucket labelled `2026-W35`, but
it averages three days, not seven. The number is honest; the label is not, unless the
response says so. This module computes, for every period key the grouping produces,
the fraction of that period's days that fall inside the requested range — so the
client can mark a bucket "3 of 7 days" and the file can carry a note.

Pure date arithmetic, no I/O; the engine's own keys are reproduced here (ISO week
`%G-W%V`, month `%Y-%m`, year `%Y`) so the annotation lines up with the axis.
"""
from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta
from typing import Dict, List, Optional

PERIOD_GROUPINGS = ("WEEK_WISE", "MONTH_WISE", "YEAR_WISE")


def _iso_week_key(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def _period_bounds(d: date, grouping: str):
    """(first_day, last_day, key) of the period containing `d`."""
    if grouping == "WEEK_WISE":
        first = d - timedelta(days=d.isocalendar()[2] - 1)
        return first, first + timedelta(days=6), _iso_week_key(d)
    if grouping == "MONTH_WISE":
        first = d.replace(day=1)
        last = d.replace(day=monthrange(d.year, d.month)[1])
        return first, last, f"{d.year:04d}-{d.month:02d}"
    if grouping == "YEAR_WISE":
        return date(d.year, 1, 1), date(d.year, 12, 31), f"{d.year:04d}"
    raise ValueError(f"Not a period grouping: {grouping}")


def period_coverage(from_date: date, to_date: date, grouping: str) -> Optional[Dict[str, dict]]:
    """Per period key: days requested, days in the period, and whether it is partial.

    Returns None for non-period groupings (block/date/hourly are never partial).
    """
    if grouping not in PERIOD_GROUPINGS or to_date < from_date:
        return None
    out: Dict[str, dict] = {}
    cursor = from_date
    while cursor <= to_date:
        first, last, key = _period_bounds(cursor, grouping)
        lo, hi = max(first, from_date), min(last, to_date)
        covered = (hi - lo).days + 1
        total = (last - first).days + 1
        out[key] = {
            "days_covered": covered,
            "days_in_period": total,
            "partial": covered < total,
            "from": lo.isoformat(),
            "to": hi.isoformat(),
        }
        cursor = last + timedelta(days=1)
    return out


def partial_keys(coverage: Optional[Dict[str, dict]]) -> List[str]:
    if not coverage:
        return []
    return [k for k, v in coverage.items() if v["partial"]]
