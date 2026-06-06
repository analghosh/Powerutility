"""
Parse scheduled_at values from CSV uploads and ingestion feeds.

Manual CSV uploads use parse_csv_scheduled_at (format flags in libs.core.csv_date_config).
External API/scrape feeds use parse_ingestion_date (all standard formats, unrestricted).
"""

import re
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Tuple

from libs.core.csv_date_config import get_csv_date_settings

_ISO_DATE_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}")
_DD_MM_YYYY_PREFIX = re.compile(r"^\d{2}-\d{2}-\d{4}")
_SLASH_DATE_PREFIX = re.compile(r"^\d{1,2}/\d{1,2}/\d{4}")


def _enabled_csv_format_hints() -> List[Tuple[str, str]]:
    settings = get_csv_date_settings()
    hints: List[Tuple[str, str]] = []
    if settings.CSV_DATE_DD_MM_YYYY_ENABLED:
        hints.append(("DD-MM-YYYY", "04-06-2026"))
    if settings.CSV_DATE_YYYY_MM_DD_ENABLED:
        hints.append(("YYYY-MM-DD", "2026-06-04"))
    if settings.CSV_DATE_DD_MM_SLASH_ENABLED:
        hints.append(("DD/MM/YYYY", "04/06/2026"))
    if settings.CSV_DATE_ABBREV_MONTH_ENABLED:
        hints.append(("DD-Mon-YYYY", "21-Feb-2026"))
    return hints


def _invalid_date_message(value: str, *, for_csv: bool = True) -> str:
    hints = _enabled_csv_format_hints() if for_csv else [
        ("DD-MM-YYYY", "04-06-2026"),
        ("YYYY-MM-DD", "2026-06-04"),
        ("DD/MM/YYYY", "04/06/2026"),
    ]
    if not hints:
        return f"Invalid date '{value}'. No CSV date formats are enabled in configuration."
    parts = [f"{name} ({example})" for name, example in hints]
    if len(parts) == 1:
        use_text = parts[0]
    else:
        use_text = ", ".join(parts[:-1]) + f", or {parts[-1]}"
    return f"Invalid date '{value}'. Use {use_text}."


def midnight_naive(dt: datetime) -> datetime:
    """Normalize to naive datetime at 00:00:00 (calendar date from value)."""
    if dt.tzinfo is not None:
        return datetime.combine(dt.date(), datetime.min.time())
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


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
    raise ValueError(_invalid_date_message(text, for_csv=False))


def _parse_dd_mm_yyyy_prefixed(text: str) -> datetime:
    for fmt in ("%d-%m-%Y %H:%M:%S", "%d-%m-%Y"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ValueError(_invalid_date_message(text, for_csv=False))


def _parse_slash_formats(text: str) -> datetime:
    for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ValueError(_invalid_date_message(text, for_csv=False))


def _parse_all_supported_formats(text: str) -> datetime:
    """Parse a date string using all standard formats (not gated by CSV upload flags)."""
    if _ISO_DATE_PREFIX.match(text):
        return midnight_naive(_parse_iso_prefixed(text))
    if _DD_MM_YYYY_PREFIX.match(text):
        return midnight_naive(_parse_dd_mm_yyyy_prefixed(text))
    if _SLASH_DATE_PREFIX.match(text):
        return midnight_naive(_parse_slash_formats(text))
    raise ValueError(_invalid_date_message(text, for_csv=False))


def _parse_csv_string(text: str) -> datetime:
    """Parse a date string respecting CSV upload format flags."""
    settings = get_csv_date_settings()

    if _ISO_DATE_PREFIX.match(text):
        if settings.CSV_DATE_YYYY_MM_DD_ENABLED:
            return midnight_naive(_parse_iso_prefixed(text))
        raise ValueError(_invalid_date_message(text, for_csv=True))

    if _DD_MM_YYYY_PREFIX.match(text):
        if settings.CSV_DATE_DD_MM_YYYY_ENABLED:
            return midnight_naive(_parse_dd_mm_yyyy_prefixed(text))
        raise ValueError(_invalid_date_message(text, for_csv=True))

    if _SLASH_DATE_PREFIX.match(text):
        if settings.CSV_DATE_DD_MM_SLASH_ENABLED:
            return midnight_naive(_parse_slash_formats(text))
        raise ValueError(_invalid_date_message(text, for_csv=True))

    if settings.CSV_DATE_ABBREV_MONTH_ENABLED:
        for fmt in ("%d-%b-%Y", "%d-%B-%Y"):
            try:
                return midnight_naive(datetime.strptime(text, fmt))
            except ValueError:
                continue

    raise ValueError(_invalid_date_message(text, for_csv=True))


def parse_csv_scheduled_at(value: Any) -> datetime:
    """
    Parse a manual CSV scheduled_at value into a datetime.

    Format acceptance is controlled by CSV_DATE_* env flags (default: DD-MM-YYYY only).
    """
    if isinstance(value, datetime):
        return midnight_naive(value)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)

    if value is None:
        raise ValueError(_invalid_date_message("", for_csv=True))

    text = str(value).strip()
    if not text:
        raise ValueError(_invalid_date_message(text, for_csv=True))

    return _parse_csv_string(text)


def scheduled_at_day_range_filter(day: datetime) -> Dict[str, Any]:
    """
    MongoDB filter for scheduled_at covering a single calendar day.

    Matches BSON datetimes in [day, day+1) and legacy string forms for that day.
    """
    if isinstance(day, datetime):
        start = midnight_naive(day)
    elif isinstance(day, date):
        start = datetime(day.year, day.month, day.day)
    else:
        start = midnight_naive(_parse_all_supported_formats(str(day).strip()))
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
    """Parse limit-values API date filter (not gated by CSV upload flags)."""
    return _parse_all_supported_formats(str(date_filter).strip())


def parse_ingestion_date(value: Any) -> datetime:
    """
    Parse scheduled/delivery dates for external API/scrape ingestion parsers.

    Accepts all standard formats plus abbreviated months (e.g. 21-Feb-2026).
    Not gated by CSV upload format flags.
    """
    if isinstance(value, datetime):
        return midnight_naive(value)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)

    if value is None:
        raise ValueError(_invalid_date_message("", for_csv=False))

    text = str(value).strip()
    if not text:
        raise ValueError(_invalid_date_message(text, for_csv=False))

    try:
        return _parse_all_supported_formats(text)
    except ValueError:
        pass

    for fmt in ("%d-%b-%Y", "%d-%B-%Y"):
        try:
            return midnight_naive(datetime.strptime(text, fmt))
        except ValueError:
            continue

    raise ValueError(_invalid_date_message(text, for_csv=False))
