"""
Parse scheduled_at values from CSV uploads (ingestion / limit values).

Accepts DD-MM-YYYY (04-06-2026) and YYYY-MM-DD (2026-06-04), with optional time.
All results are normalized to naive midnight for consistent MongoDB keys/queries.
"""

import re
from datetime import date, datetime, timedelta
from typing import Any, Dict

_ISO_DATE_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}")
_DD_MM_YYYY_PREFIX = re.compile(r"^\d{2}-\d{2}-\d{4}")


def _invalid_date_message(value: str) -> str:
    return (
        f"Invalid date '{value}'. "
        "Use DD-MM-YYYY (04-06-2026), YYYY-MM-DD (2026-06-04), "
        "or DD/MM/YYYY (04/06/2026)."
    )


def midnight_naive(dt: datetime) -> datetime:
    """Normalize to naive datetime at 00:00:00 (calendar date from value)."""
    if dt.tzinfo is not None:
        return datetime.combine(dt.date(), datetime.min.time())
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


def parse_csv_scheduled_at(value: Any) -> datetime:
    """
    Parse a CSV scheduled_at value into a datetime.

    Args:
        value: datetime, date, or string in a supported format

    Returns:
        datetime (naive midnight)

    Raises:
        ValueError: If the value is empty or not in a supported format
    """
    if isinstance(value, datetime):
        return midnight_naive(value)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)

    if value is None:
        raise ValueError(_invalid_date_message(""))

    text = str(value).strip()
    if not text:
        raise ValueError(_invalid_date_message(text))

    if _ISO_DATE_PREFIX.match(text):
        return midnight_naive(_parse_iso_prefixed(text))
    if _DD_MM_YYYY_PREFIX.match(text):
        return midnight_naive(_parse_dd_mm_yyyy_prefixed(text))

    for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y"):
        try:
            return midnight_naive(datetime.strptime(text, fmt))
        except ValueError:
            continue

    raise ValueError(_invalid_date_message(text))


def scheduled_at_day_range_filter(day: datetime) -> Dict[str, Any]:
    """
    MongoDB filter for scheduled_at covering a single calendar day.

    Matches BSON datetimes in [day, day+1) and legacy string forms for that day.
    """
    start = midnight_naive(day if isinstance(day, datetime) else parse_csv_scheduled_at(day))
    end = start + timedelta(days=1)
    return {
        "$or": [
            {"scheduled_at": {"$gte": start, "$lt": end}},
            {"scheduled_at": start.strftime("%Y-%m-%d")},
            {"scheduled_at": start.strftime("%d-%m-%Y")},
            {"scheduled_at": start.strftime("%d/%m/%Y")},
        ]
    }


def parse_schedule_date_filter(date_filter: str) -> datetime:
    """Parse API/CSV schedule_date query param (YYYY-MM-DD or DD-MM-YYYY)."""
    return parse_csv_scheduled_at(date_filter)


def parse_ingestion_date(value: Any) -> datetime:
    """
    Parse scheduled/delivery dates for ingestion parsers.

    Accepts the three CSV upload formats plus legacy abbreviated months
    (e.g. 21-Feb-2026) used by some exchange feeds.
    """
    try:
        return parse_csv_scheduled_at(value)
    except ValueError:
        pass

    text = str(value).strip()
    for fmt in ("%d-%b-%Y", "%d-%B-%Y"):
        try:
            return midnight_naive(datetime.strptime(text, fmt))
        except ValueError:
            continue

    raise ValueError(_invalid_date_message(text))


def _parse_iso_prefixed(text: str) -> datetime:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        pass
    raise ValueError(_invalid_date_message(text))


def _parse_dd_mm_yyyy_prefixed(text: str) -> datetime:
    for fmt in ("%d-%m-%Y %H:%M:%S", "%d-%m-%Y"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ValueError(_invalid_date_message(text))
