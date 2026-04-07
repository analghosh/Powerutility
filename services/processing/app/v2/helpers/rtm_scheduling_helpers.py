"""
RTM Scheduling Helper Functions
Contains utility functions for RTM scheduling operations
"""
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)


def get_time_new() -> List[str]:
    """
    Get list of time strings for 15-minute blocks from 00:00 to 24:00.
    Returns 97 elements (96 blocks + "24:00" at the end).
    
    Returns:
        List[str]: List of time strings like ["00:00", "00:15", "00:30", ..., "24:00"]
    """
    time_blocks = [f"{h:02}:{m:02}" for h in range(24) for m in (0, 15, 30, 45)]
    time_blocks.append("24:00")  # Add end of the day
    return time_blocks


def get_session_from() -> List[str]:
    """
    Get list of session start times (30-minute intervals).
    Returns 48 session times from 00:00 to 23:30.
    
    Returns:
        List[str]: List of session start times like ["00:00", "00:30", "01:00", ..., "23:30"]
    """
    time_block = [
        "00:00", "00:30", "01:00", "01:30", "02:00", "02:30", "03:00", "03:30",
        "04:00", "04:30", "05:00", "05:30", "06:00", "06:30", "07:00", "07:30",
        "08:00", "08:30", "09:00", "09:30", "10:00", "10:30", "11:00", "11:30",
        "12:00", "12:30", "13:00", "13:30", "14:00", "14:30", "15:00", "15:30",
        "16:00", "16:30", "17:00", "17:30", "18:00", "18:30", "19:00", "19:30",
        "20:00", "20:30", "21:00", "21:30", "22:00", "22:30", "23:00", "23:30"
    ]
    return time_block


def get_current_time_block() -> int:
    """
    Get the current time block number (1-96) based on current time.
    
    Returns:
        int: Current time block number (1-96)
    """
    now = datetime.now()
    hour = now.hour
    minute = now.minute
    
    # Calculate block number (1-96)
    block = (hour * 4) + (minute // 15) + 1
    return min(block, 96)  # Cap at 96


def calculate_today_current_block() -> int:
    """
    Calculate today's current block based on session times.
    This is a simplified version - the actual calculation may be more complex.
    
    Returns:
        int: Current block number
    """
    session_times = get_session_from()
    current_time = datetime.now().strftime("%H:%M")
    
    # Count the number of sessions that have passed
    passed_sessions_count = sum(
        1 for t in session_times if datetime.strptime(t, "%H:%M") < datetime.strptime(current_time, "%H:%M")
    )
    
    # Calculate today's current block
    today_current_block = (2 * passed_sessions_count) - 1 + 95
    return today_current_block


def get_parameter_value(parameter_key: str, default_value: Any = None) -> Any:
    """
    Get parameter value from database.
    This is a placeholder - actual implementation should query the parameter table.
    
    Args:
        parameter_key: Key of the parameter to retrieve
        default_value: Default value if parameter not found
    
    Returns:
        Any: Parameter value or default value
    """
    # TODO: Implement actual database query for parameter value
    # For now, return default value
    logger.warning(f"get_parameter_value: Not implemented, returning default for key: {parameter_key}")
    return default_value


def sum_of_data_filter(filter_data: List[Any]) -> float:
    """
    Sum all values in filterData array.
    Handles both list of numbers and list of dicts with 'totalvolcat' key.
    
    Args:
        filter_data: List of numbers or dicts with 'totalvolcat' key
    
    Returns:
        float: Sum of all values
    """
    total = 0.0
    if not isinstance(filter_data, list):
        return total
    
    for item in filter_data:
        if isinstance(item, dict):
            # Extract totalvolcat value from dict
            totalvolcat = item.get('totalvolcat', 0)
            if totalvolcat is not None:
                try:
                    total += float(totalvolcat)
                except (ValueError, TypeError):
                    continue
        elif isinstance(item, (int, float)):
            # If it's already a number, use it directly
            total += float(item)
    
    return total


def format_date_for_display(date_str: str) -> str:
    """
    Format date string from YYYY-MM-DD to DD-MM-YYYY for display.
    
    Args:
        date_str: Date string in YYYY-MM-DD format
    
    Returns:
        str: Date string in DD-MM-YYYY format
    """
    try:
        date_obj = datetime.strptime(date_str, "%Y-%m-%d")
        return date_obj.strftime("%d-%m-%Y")
    except (ValueError, TypeError):
        # If parsing fails, return original string
        logger.warning(f"format_date_for_display: Failed to parse date: {date_str}")
        return date_str


def get_time_block_start_end(time_block: int) -> Dict[str, str]:
    """
    Get start time, end time, and full time string for a given time block.
    
    Args:
        time_block: Time block number (1-96)
    
    Returns:
        Dict with keys: 'start_time', 'end_time', 'full_time'
    """
    if time_block < 1 or time_block > 96:
        return {
            'start_time': '00:00',
            'end_time': '00:00',
            'full_time': '00:00-00:00'
        }
    
    # Calculate start time
    minutes = (time_block - 1) * 15
    hours = minutes // 60
    mins = minutes % 60
    start_time = f"{hours:02}:{mins:02}"
    
    # Calculate end time
    if time_block == 96:
        end_time = "24:00"
    else:
        end_minutes = time_block * 15
        end_hours = end_minutes // 60
        end_mins = end_minutes % 60
        end_time = f"{end_hours:02}:{end_mins:02}"
    
    full_time = f"{start_time}-{end_time}"
    
    return {
        'start_time': start_time,
        'end_time': end_time,
        'full_time': full_time
    }



