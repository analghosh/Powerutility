"""
Split inclusive YYYY-MM-DD ranges into past vs current dates for historical collection routing.

Past dates (strictly before today) read from *_historical collections.
Current dates (today and future) read from live collections, with historical fallback where applicable.
"""

from datetime import date, datetime, timedelta
from typing import List, Optional, Tuple

REPORTS_FORECAST_MAX_DATE_RANGE_DAYS = 90


def today_date_str() -> str:
    """Calendar today in local server timezone (YYYY-MM-DD)."""
    return datetime.now().strftime("%Y-%m-%d")


def iter_dates_inclusive(from_date: str, to_date: str) -> List[str]:
    start = datetime.strptime(from_date, "%Y-%m-%d")
    end = datetime.strptime(to_date, "%Y-%m-%d")
    dates: List[str] = []
    cursor = start
    while cursor <= end:
        dates.append(cursor.strftime("%Y-%m-%d"))
        cursor += timedelta(days=1)
    return dates


def is_past_date(date_str: str, today: Optional[str] = None) -> bool:
    """True when date is strictly before today."""
    ref = today or today_date_str()
    return date_str < ref


def split_date_range_by_today(
    from_date: str,
    to_date: str,
    today: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """
    Split an inclusive date range around today.

    Returns:
        (past_from, past_to, current_from, current_to)
        Any segment absent when the range does not overlap that side.
    """
    ref = today or today_date_str()
    dates = iter_dates_inclusive(from_date, to_date)
    past_dates = [d for d in dates if d < ref]
    current_dates = [d for d in dates if d >= ref]

    past_from = past_dates[0] if past_dates else None
    past_to = past_dates[-1] if past_dates else None
    current_from = current_dates[0] if current_dates else None
    current_to = current_dates[-1] if current_dates else None
    return past_from, past_to, current_from, current_to


def count_days_inclusive(from_date: str, to_date: str) -> int:
    """Inclusive day count between two YYYY-MM-DD strings."""
    return len(iter_dates_inclusive(from_date, to_date))


def validate_max_date_range_days(
    from_date: str,
    to_date: str,
    max_days: int = REPORTS_FORECAST_MAX_DATE_RANGE_DAYS,
) -> None:
    """Raise ValueError when the inclusive range exceeds max_days."""
    day_count = count_days_inclusive(from_date, to_date)
    if day_count > max_days:
        raise ValueError(
            f"Date range cannot exceed {max_days} days (requested {day_count} days)"
        )
