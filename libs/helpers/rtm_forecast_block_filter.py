"""
RTM forecast block filter helpers (IST).
Shared by ingestion RTM API processor and scheduler IDF forecast merge.
"""

from datetime import date, datetime
from typing import Optional, Tuple
from zoneinfo import ZoneInfo


def _time_block_to_session_id(time_block: int) -> int:
    block = max(1, min(96, int(time_block)))
    return (block - 1) // 2 + 1

# After this local time, intraday block filter is disabled (all blocks writable for today).
EVENING_CUTOVER_HM: Tuple[int, int] = (22, 30)

# From 12:00 IST (12h before next delivery day), include tomorrow in API fetch and merge.
TOMORROW_FORECAST_START_HM: Tuple[int, int] = (12, 0)

DEFAULT_CRON_TIMEZONE = "Asia/Kolkata"
DEFAULT_MIN_AHEAD_BLOCKS = 5

# Same offset as RTM bid lapse: active session is ahead of the current IST session.
RTM_ACTIVE_SESSION_OFFSET = 3
SESSIONS_PER_DAY = 48


def ist_current_session_id(now_local: datetime) -> int:
    """IST 30-minute session (1–48) for the clock time, not the 15-minute block index."""
    total_minutes = now_local.hour * 60 + now_local.minute
    return min(SESSIONS_PER_DAY, (total_minutes // 30) + 1)


def rtm_active_session_id(
    now_local: datetime,
    offset: int = RTM_ACTIVE_SESSION_OFFSET,
) -> int:
    """RTM active session = IST current session + offset (capped at 48 for today)."""
    return min(SESSIONS_PER_DAY, rtm_active_session_id_uncapped(now_local, offset))


def rtm_active_session_id_uncapped(
    now_local: datetime,
    offset: int = RTM_ACTIVE_SESSION_OFFSET,
) -> int:
    """IST current session + offset without end-of-day cap (matches RTM bid lapse)."""
    return ist_current_session_id(now_local) + offset


def tomorrow_overflow_session_id(
    now_local: datetime,
    offset: int = RTM_ACTIVE_SESSION_OFFSET,
) -> int:
    """
    Tomorrow's effective active session when uncapped active exceeds 48.

    E.g. at 22:30 IST uncapped active is 49 → tomorrow session 1 (00:00).
    """
    raw = rtm_active_session_id_uncapped(now_local, offset)
    if raw <= SESSIONS_PER_DAY:
        return 1
    return raw - SESSIONS_PER_DAY


def effective_active_session_for_delivery_date(
    scheduled_date: date,
    today_ist: date,
    now_local: datetime,
    after_evening_cutover: bool = False,
    offset: int = RTM_ACTIVE_SESSION_OFFSET,
) -> int:
    """
    Active session for auto-flip / manual validation for one delivery date.

    - Today: capped RTM active session (intraday).
    - Tomorrow after evening cutover or when uncapped active > 48: overflow session
      on the delivery day (session 1 at 22:30 IST).
    - Tomorrow before cutover while uncapped active <= 48: session 1.
    """
    if scheduled_date <= today_ist:
        return rtm_active_session_id(now_local, offset)
    raw = rtm_active_session_id_uncapped(now_local, offset)
    if after_evening_cutover or raw > SESSIONS_PER_DAY:
        return tomorrow_overflow_session_id(now_local, offset)
    return 1


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
    active_rtm_session_id: Optional[int] = None,
    now_local: Optional[datetime] = None,
) -> bool:
    """
    Decide whether a time block should be written on this cron run.

    - Past dates: never write.
    - After evening cutover (22:30 IST): write all blocks 1–96 (delivery day is tomorrow only).
    - Same day before cutover: RTM active session (IST session + offset) through block 96.
    - Future dates (before cutover): write all blocks.
    """
    _ = current_block, min_ahead_blocks
    if scheduled_date < today_ist:
        return False
    if after_evening_cutover:
        return True
    if scheduled_date == today_ist:
        active_sid = active_rtm_session_id
        if active_sid is None and now_local is not None:
            active_sid = rtm_active_session_id(now_local)
        if active_sid is None:
            active_sid = _time_block_to_session_id(int(current_block))
        block_session_id = _time_block_to_session_id(int(time_block))
        return block_session_id >= active_sid
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
    current_session_id = ist_current_session_id(now_local)
    active_session_id = rtm_active_session_id(now_local)
    raw_active_session_id = rtm_active_session_id_uncapped(now_local)
    return {
        "tz": tz,
        "now_local": now_local,
        "today_ist": today_ist,
        "current_block": current_block,
        "current_session_id": current_session_id,
        "active_session_id": active_session_id,
        "raw_active_session_id": raw_active_session_id,
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


def primary_scheduled_date_for_rtm(
    today_ist: date,
    after_evening_cutover: bool,
) -> date:
    """
    Primary intraday delivery date for switch validation and auto-flip.

    - Before 22:30 IST: today
    - From 22:30 IST: tomorrow
    """
    dates = target_scheduled_dates_for_rtm(
        today_ist,
        after_evening_cutover,
        include_tomorrow_forecast=False,
    )
    return dates[0] if dates else today_ist


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
