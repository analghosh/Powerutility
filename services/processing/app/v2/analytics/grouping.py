"""Block bucketing is pushed into Mongo's `$group`; date bucketing is not.

**Group Date By is a label, not an aggregation** (user decision, 2026-09-20). Every
dashboard query groups on the trading *day*, whatever the user picked, and the chosen
grouping only decides which identity columns the exported file carries — a Week
column, an Hourly column, and so on. A week-wise file therefore holds the same block
values a block-wise one does, with a week number beside them, rather than seven days
averaged into one.

That is a deliberate reversal of the original design, and it removes two defects with
it. `BLOCK_WISE` and `DATE_WISE` both formatted `%Y-%m-%d` and left the block axis to
`block_band`, so they produced byte-identical files — two pills, one behaviour; they
are now the single `BLOCK_DATE_WISE`. And `HOURLY_WISE` keyed on the hour while the
block axis still keyed on the block, which merely scattered 96 values across 24 sparse
columns, because a block *is* a quarter hour. As a column, the hour finally reads.

The old codes still arrive from stored export jobs and saved preferences, so
`canonical` maps them forward; nothing on disk needs rewriting.

Nothing aggregates over a period any more. The weekly and monthly Forecast/MAPE
datasets were removed on 2026-09-20 — they read the same stored fields as the daily
ones and differed only in aggregation period, so the daily columns cover them — and
`date_key_expr` went with them. One date key remains: the trading day.

Two details are silently wrong if left to the defaults:

* Week labels use the ISO year (%G), not the calendar year (%Y). 29 Dec 2025 is ISO
  week 2026-W01; formatted with %Y it reads "2025-W01" and collides with the first
  week of January 2025.
* The day key carries an explicit timezone. EPMS stores UTC instants, the day is an
  IST trading day, and blocks 1-4 of every day fall on the previous UTC date.
"""
from datetime import date, datetime
from typing import Dict, List, Optional, Sequence, Tuple, Union

from .block_bands import (BAND_CUSTOM, BAND_INDIVIDUAL, PARAMETER_KEYS, RANGES_EXAMPLE,
                          Band, validate_block_range)

try:  # pragma: no cover - the setting lands with the analytics config block
    from ...core.config import settings
    TIMEZONE: str = getattr(settings, "ANALYTICS_TIMEZONE", "Asia/Kolkata")
except Exception:  # pragma: no cover - keeps the module importable standalone
    TIMEZONE = "Asia/Kolkata"

#: The pill values the dashboard offers. `BLOCK_DATE_WISE` is the 2026-09-20 merge of
#: the old `BLOCK_WISE` and `DATE_WISE`, which were the same file.
BLOCK_DATE_WISE = "BLOCK_DATE_WISE"
HOURLY_WISE = "HOURLY_WISE"
WEEK_WISE = "WEEK_WISE"
MONTH_WISE = "MONTH_WISE"
YEAR_WISE = "YEAR_WISE"

DATE_GROUPINGS = (BLOCK_DATE_WISE, HOURLY_WISE, WEEK_WISE, MONTH_WISE, YEAR_WISE)

#: Retired codes, and what they mean now. Stored export jobs and saved preferences
#: hold these; they are canonicalised on the way in rather than migrated on disk.
LEGACY_GROUPINGS: Dict[str, str] = {
    "BLOCK_WISE": BLOCK_DATE_WISE,
    "DATE_WISE": BLOCK_DATE_WISE,
}


def canonical(grouping: Optional[str]) -> str:
    """The current code for a grouping, accepting the two retired spellings."""
    value = str(grouping or "").strip().upper()
    value = LEGACY_GROUPINGS.get(value, value)
    if value not in DATE_GROUPINGS:
        raise ValueError(
            f"Unknown date grouping: {grouping!r}; expected one of "
            f"{', '.join(DATE_GROUPINGS)}")
    return value


#: The trading day, and the only key any pipeline groups on.
DAY_FORMAT = "%Y-%m-%d"

AGGREGATIONS = ("avg", "sum", "max", "min", "last")

BLOCK_FIELD = "$time_block"

BLOCKS_PER_HOUR = 4


def hour_of_block(block: int) -> int:
    """Hour of day (0-23) that a 15-minute block falls in.

    Blocks 1-4 are hour 0, blocks 93-96 are hour 23.
    """
    return (int(block) - 1) // BLOCKS_PER_HOUR


def day_key_expr(field: str = "$scheduled_at") -> dict:
    """The trading-day key every dashboard query groups on."""
    return {"$dateToString": {"format": DAY_FORMAT, "date": field, "timezone": TIMEZONE}}


# ------------------------------------------------------- identity columns

#: Column headings, coarse to fine, for each pill. `Date` and `Time block` are on
#: every file; the chosen grouping adds its own column in front of (or between)
#: them. Matches the layout PTC drew on 2026-09-20.
COLUMN_DATE = "Date"
COLUMN_BLOCK = "Time block"
COLUMN_WEEK = "Week"
COLUMN_HOURLY = "Hourly"
COLUMN_MONTH = "Month"
COLUMN_YEAR = "Year"

IDENTITY_COLUMNS: Dict[str, Tuple[str, ...]] = {
    BLOCK_DATE_WISE: (COLUMN_DATE, COLUMN_BLOCK),
    HOURLY_WISE: (COLUMN_DATE, COLUMN_HOURLY, COLUMN_BLOCK),
    WEEK_WISE: (COLUMN_WEEK, COLUMN_DATE, COLUMN_BLOCK),
    MONTH_WISE: (COLUMN_MONTH, COLUMN_DATE, COLUMN_BLOCK),
    YEAR_WISE: (COLUMN_YEAR, COLUMN_DATE, COLUMN_BLOCK),
}

_MONTH_NAMES = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def identity_columns(grouping: str) -> Tuple[str, ...]:
    """The identity column headings this grouping puts in front of the data."""
    return IDENTITY_COLUMNS[canonical(grouping)]


def parse_day(day_key: str) -> date:
    """`"2026-09-19"` -> a `date`. The day key is the only date the pipeline emits."""
    return datetime.strptime(str(day_key), DAY_FORMAT).date()


def hour_label(block: Union[int, str]) -> Optional[str]:
    """`"Hour 1"` for blocks 1-4, `"Hour 24"` for 93-96.

    One-based to match the block numbering beside it: a spreadsheet that starts
    blocks at 1 and hours at 0 invites an off-by-one in every formula written
    against it. `None` for a banded block key, which spans hours and has no one hour.
    """
    try:
        block_no = int(block)
    except (TypeError, ValueError):
        return None
    return f"Hour {hour_of_block(block_no) + 1}"


def week_label(day: date) -> str:
    """`"W38 2026"`. ISO week and ISO year, which disagree with the calendar year
    for a handful of days every New Year — see the module docstring."""
    iso = day.isocalendar()
    return f"W{iso[1]} {iso[0]}"


def month_label(day: date) -> str:
    """`"Sep 2026"`."""
    return f"{_MONTH_NAMES[day.month - 1]} {day.year}"


def year_label(day: date) -> str:
    """`"2026"`."""
    return str(day.year)


def identity_values(grouping: str, day_key: str,
                    block_key: Union[int, str]) -> List[Union[str, date, int, None]]:
    """The identity cells for one row, in `identity_columns` order.

    `block_key` is a block number for INDIVIDUAL and a band label otherwise, and it
    is passed through as given: "Night Off-Peak" is what the user chose, so it is
    what the Time block column says.
    """
    day = parse_day(day_key)
    out: List[Union[str, date, int, None]] = []
    for column in identity_columns(grouping):
        if column == COLUMN_WEEK:
            out.append(week_label(day))
        elif column == COLUMN_MONTH:
            out.append(month_label(day))
        elif column == COLUMN_YEAR:
            out.append(year_label(day))
        elif column == COLUMN_DATE:
            out.append(day)
        elif column == COLUMN_HOURLY:
            out.append(hour_label(block_key))
        elif column == COLUMN_BLOCK:
            out.append(block_key)
    return out


# ------------------------------------------------------------- block keys

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

    CUSTOM left the filter panel on 2026-09-20 but stays reachable here: stored
    export jobs still hold it, and a replay has to reproduce the file it made.
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
            key = PARAMETER_KEYS.get(band.code, band.code)
            raise ValueError(
                f"The {band.label} band has no block ranges yet. Set parameter "
                f"'{key}' under Masters → Parameters (e.g. {RANGES_EXAMPLE})."
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
