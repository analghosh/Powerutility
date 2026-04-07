from datetime import datetime, timedelta
from typing import List


# Returns 15-min time block ID from HH:MM time (e.g., "13:30" → 54)
def get_fifteen_time_block(time_str: str) -> int:
    """
    Convert time (HH:MM) to 15-minute time block ID (1–96).
    """
    hour, minute = map(int, time_str.split(":"))
    return hour * 4 + (minute // 15) + 1


# Returns time block string (e.g., "13:30") from block ID
def get_fifteen_full_time_block_id(block_id: int) -> str:
    """
    Convert 15-minute block ID to time string (HH:MM).
    """
    block_id = max(1, min(96, block_id))  # Clamp between 1 and 96
    total_minutes = (block_id - 1) * 15
    hour = total_minutes // 60
    minute = total_minutes % 60
    return f"{hour:02d}:{minute:02d}"


# Returns list of last 4 block IDs before current
def get_past_blocks(current_block_id: int) -> List[int]:
    """
    Returns the 4 past 15-minute block IDs from the given current block.
    """
    return [b for b in range(current_block_id - 4, current_block_id) if b > 0]


# Returns next 4 block IDs after current
def get_next_blocks(current_block_id: int) -> List[int]:
    """
    Returns the 4 next 15-minute block IDs after the given current block.
    """
    return [b for b in range(current_block_id + 1, current_block_id + 5) if b <= 96]
