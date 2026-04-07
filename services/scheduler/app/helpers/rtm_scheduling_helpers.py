"""
RTM Scheduling Helper Functions for Scheduler Service
Provides utility functions for RTM scheduling calculations
"""
from datetime import datetime, time
from typing import Dict, Tuple, List, Any
import logging

logger = logging.getLogger(__name__)


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


def get_time_block_start_end(time_block: int) -> Tuple[str, str, str]:
    """
    Get start time, end time, and full time range for a given time block.
    
    Args:
        time_block: Time block number (1-96)
        
    Returns:
        Tuple of (start_time, end_time, full_time) in HH:MM format
    """
    if time_block < 1 or time_block > 96:
        return ("00:00", "00:15", "00:00-00:15")
    
    # Each block is 15 minutes
    minutes_from_midnight = (time_block - 1) * 15
    hours = minutes_from_midnight // 60
    minutes = minutes_from_midnight % 60
    
    start_time = f"{hours:02d}:{minutes:02d}"
    
    # End time is 15 minutes later
    end_minutes = minutes_from_midnight + 15
    end_hours = end_minutes // 60
    end_mins = end_minutes % 60
    
    # Handle day rollover (block 96 ends at 00:00 next day)
    if end_hours >= 24:
        end_hours = 0
        end_mins = 0
    
    end_time = f"{end_hours:02d}:{end_mins:02d}"
    full_time = f"{start_time}-{end_time}"
    
    return (start_time, end_time, full_time)


def get_session_from() -> List[str]:
    """
    Get session start times for RTM scheduling.
    Returns list of session times in HH:MM format.
    
    Returns:
        List of session start times
    """
    # RTM sessions typically start every 30 minutes or hourly
    # This matches the PHP logic for session times
    session_times = [
        "00:00", "00:30", "01:00", "01:30", "02:00", "02:30",
        "03:00", "03:30", "04:00", "04:30", "05:00", "05:30",
        "06:00", "06:30", "07:00", "07:30", "08:00", "08:30",
        "09:00", "09:30", "10:00", "10:30", "11:00", "11:30",
        "12:00", "12:30", "13:00", "13:30", "14:00", "14:30",
        "15:00", "15:30", "16:00", "16:30", "17:00", "17:30",
        "18:00", "18:30", "19:00", "19:30", "20:00", "20:30",
        "21:00", "21:30", "22:00", "22:30", "23:00", "23:30"
    ]
    return session_times


def get_current_time_block() -> int:
    """
    Get the current time block based on current time.
    
    Returns:
        Current time block number (1-96)
    """
    now = datetime.now()
    minutes_from_midnight = now.hour * 60 + now.minute
    time_block = (minutes_from_midnight // 15) + 1
    
    # Ensure within valid range
    if time_block > 96:
        time_block = 96
    if time_block < 1:
        time_block = 1
    
    return time_block


def calculate_block_ranges(current_time: str = None) -> Dict[str, int]:
    """
    Calculate block ranges for scheduling based on current time.
    
    Args:
        current_time: Current time in HH:MM format (optional, uses now if not provided)
        
    Returns:
        Dictionary with block range values
    """
    if current_time is None:
        current_time = datetime.now().strftime("%H:%M")
    
    session_times = get_session_from()
    
    # Count the number of sessions that have passed
    passed_sessions_count = sum(
        1 for t in session_times 
        if datetime.strptime(t, "%H:%M") < datetime.strptime(current_time, "%H:%M")
    )
    
    # Calculate today's current block
    today_current_block = (2 * passed_sessions_count) - 1 + 95
    
    # Define shifting parameters
    shifting_block_no = 2
    shifting_block_no_running_to = 5
    shifting_block_no_req_from = 5
    shifting_block_no_req_to = 18
    
    return {
        "current_block": today_current_block,
        "blk_no_for_run_schedule": today_current_block + shifting_block_no,
        "blk_no_to_run_schedule": today_current_block + shifting_block_no_running_to,
        "blk_no_from_run_req": today_current_block + shifting_block_no_req_from,
        "blk_no_to_run_req": today_current_block + shifting_block_no_req_to
    }


def round_numeric_value(value):
    """
    Round numeric values: 2 decimals if decimal, otherwise integer.
    
    Args:
        value: Numeric value to round
        
    Returns:
        int if value is integer, float rounded to 2 decimals otherwise
    """
    if value is None:
        return 0
    try:
        val = float(value)
        # Check if value is effectively an integer (no significant decimal part)
        if abs(val - round(val)) < 0.001:
            return int(round(val))
        else:
            return round(val, 2)
    except (ValueError, TypeError):
        return 0
