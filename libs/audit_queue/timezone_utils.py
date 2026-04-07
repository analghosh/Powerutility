"""
Timezone utilities for consistent timestamp handling across the application.

This module provides utilities to work with India Standard Time (IST) 
for all audit logging and application timestamps.
"""

from datetime import datetime, timezone, timedelta
from typing import Optional

# India Standard Time (IST) - UTC+5:30
IST = timezone(timedelta(hours=5, minutes=30))


def now_ist() -> datetime:
    """
    Get current datetime in India Standard Time (IST).
    
    Returns:
        datetime: Current datetime in IST timezone
    """
    return datetime.now(IST)


def utc_to_ist(utc_dt: datetime) -> datetime:
    """
    Convert UTC datetime to IST.
    
    Args:
        utc_dt: UTC datetime (timezone-aware or naive)
        
    Returns:
        datetime: Datetime converted to IST
    """
    if utc_dt.tzinfo is None:
        # Assume naive datetime is UTC
        utc_dt = utc_dt.replace(tzinfo=timezone.utc)
    
    return utc_dt.astimezone(IST)


def ist_to_utc(ist_dt: datetime) -> datetime:
    """
    Convert IST datetime to UTC.
    
    Args:
        ist_dt: IST datetime (timezone-aware or naive)
        
    Returns:
        datetime: Datetime converted to UTC
    """
    if ist_dt.tzinfo is None:
        # Assume naive datetime is IST
        ist_dt = ist_dt.replace(tzinfo=IST)
    
    return ist_dt.astimezone(timezone.utc)


def format_ist_timestamp(dt: Optional[datetime] = None) -> str:
    """
    Format datetime as IST timestamp string.
    
    Args:
        dt: Datetime to format (defaults to current IST time)
        
    Returns:
        str: ISO format timestamp string in IST
    """
    if dt is None:
        dt = now_ist()
    elif dt.tzinfo is None:
        # Assume naive datetime is UTC and convert to IST
        dt = dt.replace(tzinfo=timezone.utc).astimezone(IST)
    elif dt.tzinfo != IST:
        # Convert to IST if different timezone
        dt = dt.astimezone(IST)
    
    return dt.isoformat()


def parse_ist_timestamp(timestamp_str: str) -> datetime:
    """
    Parse IST timestamp string to datetime object.
    
    Args:
        timestamp_str: ISO format timestamp string
        
    Returns:
        datetime: Parsed datetime in IST
    """
    try:
        dt = datetime.fromisoformat(timestamp_str)
        if dt.tzinfo is None:
            # Assume naive datetime is IST
            dt = dt.replace(tzinfo=IST)
        return dt.astimezone(IST)
    except ValueError:
        # Fallback to current IST time
        return now_ist()


def get_timezone_info() -> dict:
    """
    Get timezone information for debugging and logging.
    
    Returns:
        dict: Timezone information
    """
    current_ist = now_ist()
    current_utc = datetime.now(timezone.utc)
    
    return {
        "timezone": "Asia/Kolkata (IST)",
        "offset": "+05:30",
        "current_ist": current_ist.isoformat(),
        "current_utc": current_utc.isoformat(),
        "offset_seconds": IST.utcoffset(None).total_seconds()
    }


# Backward compatibility aliases
def now_india() -> datetime:
    """Alias for now_ist() for backward compatibility"""
    return now_ist()


def india_timestamp() -> str:
    """Get current IST timestamp as string"""
    return format_ist_timestamp()