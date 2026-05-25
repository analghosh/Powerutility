"""
RTM forecast block filter helpers (IST).
Shared by ingestion RTM API processor and scheduler IDF forecast merge.
"""

from datetime import date, datetime
from typing import Optional, Tuple
from zoneinfo import ZoneInfo

# After this local time, intraday block filter is disabled (all blocks writable for today).
EVENING_CUTOVER_HM: Tuple[int, int] = (22, 30)

# From 12:00 IST (12h before next delivery day), include tomorrow in API fetch and merge.
TOMORROW_FORECAST_START_HM: Tuple[int, int] = (12, 0)

DEFAULT_CRON_TIMEZONE = "Asia/Kolkata"
DEFAULT_MIN_AHEAD_BLOCKS = 5


def get_rtm_timezone(tz_name: Optional[str] = None):
    name = tz_name or DEFAULT_CRON_TIMEZONE
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("Asia/Kolkata")


def current_time_block_1_to_96(now_local: datetime) -> int:
    minutes_from_midnight = now_local.hour * 60 + now_local.minute
    time_block = (minutes_from_midnight // 15) + 1
    if time_block > 96:
        time_block = 96
    if time_block < 1:
        time_block = 1
    return time_block


def is_after_evening_cutover(now_local: datetime) -> bool:
    return (now_local.hour, now_local.minute) >= EVENING_CUTOVER_HM


def is_tomorrow_forecast_window(now_local: datetime) -> bool:
    """True from 12:00 IST onward — tomorrow forecast ingest/merge may run."""
    return (now_local.hour, now_local.minute) >= TOMORROW_FORECAST_START_HM


def should_write_forecast_block(
    scheduled_date: date,
    time_block: int,
    today_ist: date,
    current_block: int,
    after_evening_cutover: bool = False,
    min_ahead_blocks: int = 0,
) -> bool:
    """
    Decide whether a time block should be written on this cron run.

    - Past dates: never write.
    - After evening cutover (22:30 IST): write all blocks 1–96 (delivery day is tomorrow only).
    - Same day before cutover: only blocks from active session onward (current + offset).
    - Future dates (before cutover): write all blocks.
    """
    if scheduled_date < today_ist:
        return False
    if after_evening_cutover:
        return True
    if scheduled_date == today_ist:
        target_block = ((int(current_block) - 1 + int(min_ahead_blocks)) % 96) + 1
        next_block = (target_block % 96) + 1
        return int(time_block) >= next_block
    return True


def rtm_forecast_run_context(
    tz_name: Optional[str] = None,
    min_ahead_blocks: Optional[int] = None,
) -> dict:
    """IST context for block filtering and target dates."""
    tz = get_rtm_timezone(tz_name)
    now_local = datetime.now(tz)
    today_ist = now_local.date()
    current_block = current_time_block_1_to_96(now_local)
    after_evening = is_after_evening_cutover(now_local)
    include_tomorrow = is_tomorrow_forecast_window(now_local)
    ahead = max(0, int(min_ahead_blocks if min_ahead_blocks is not None else DEFAULT_MIN_AHEAD_BLOCKS))
    return {
        "tz": tz,
        "now_local": now_local,
        "today_ist": today_ist,
        "current_block": current_block,
        "after_evening_cutover": after_evening,
        "include_tomorrow_forecast": include_tomorrow,
        "min_ahead_blocks": ahead,
    }


def target_scheduled_dates_for_rtm(
    today_ist: date,
    after_evening_cutover: bool,
    include_tomorrow_forecast: bool = False,
) -> list:
    """
    Dates to process (IST):
    - Before noon: today only
    - Noon–22:29: today and tomorrow
    - From 22:30: tomorrow only
    """
    from datetime import timedelta

    tomorrow = today_ist + timedelta(days=1)
    if after_evening_cutover:
        return [tomorrow]
    dates = [today_ist]
    if include_tomorrow_forecast:
        dates.append(tomorrow)
    return dates


def is_rtm_forecast_scheduled_date_allowed(
    scheduled_date: date,
    today_ist: date,
    after_evening_cutover: bool,
    include_tomorrow_forecast: bool = False,
) -> bool:
    """True if this delivery date should be ingested (demand + forecasted price)."""
    return scheduled_date in set(
        target_scheduled_dates_for_rtm(
            today_ist, after_evening_cutover, include_tomorrow_forecast
        )
    )


def rtm_api_fetch_date_range(
    today_ist: date,
    after_evening_cutover: bool,
    include_tomorrow_forecast: bool = False,
) -> Tuple[str, str]:
    """Return (start_date, end_date) as YYYY-MM-DD strings for the RTM forecast API."""
    from datetime import timedelta

    tomorrow = today_ist + timedelta(days=1)
    if after_evening_cutover:
        d = tomorrow.isoformat()
        return d, d
    start = today_ist.isoformat()
    if include_tomorrow_forecast:
        return start, tomorrow.isoformat()
    return start, start
