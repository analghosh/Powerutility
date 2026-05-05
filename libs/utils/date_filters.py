"""
Common date filtering utilities for database queries
Provides consistent date handling across repositories with MongoDB compatibility
"""

from datetime import datetime, date, timedelta
from typing import Dict, Any, Union, Tuple, Optional


def create_scheduled_at_filter(scheduled_at: Union[datetime, date, str]) -> Dict[str, Any]:
    """
    Create a MongoDB filter for scheduled_at field using $expr and $dateFromString
    This allows proper date comparison when scheduled_at is stored as date objects
    
    Args:
        scheduled_at: Can be datetime, date, or string in YYYY-MM-DD format
        
    Returns:
        MongoDB filter dictionary for scheduled_at field using $expr
    """
    if isinstance(scheduled_at, str):
        # Validate string format (YYYY-MM-DD)
        try:
            date_obj = datetime.strptime(scheduled_at, "%Y-%m-%d").date()
        except ValueError:
            raise ValueError(f"Invalid date string format: {scheduled_at}. Expected YYYY-MM-DD")
    elif isinstance(scheduled_at, datetime):
        # Convert datetime to date
        date_obj = scheduled_at.date()
    elif isinstance(scheduled_at, date):
        # Already a date
        date_obj = scheduled_at
    else:
        raise TypeError(f"Unsupported scheduled_at type: {type(scheduled_at)}")
    
    # Create ISO date strings for MongoDB $dateFromString
    start_date_str = f"{date_obj.strftime('%Y-%m-%d')}T00:00:00.000Z"
    end_date_str = f"{(date_obj + timedelta(days=1)).strftime('%Y-%m-%d')}T00:00:00.000Z"
    
    # Use $expr with $dateFromString for proper date comparison
    return {
        "$expr": {
            "$and": [
                {
                    "$gte": [
                        "$scheduled_at",
                        {"$dateFromString": {"dateString": start_date_str}}
                    ]
                },
                {
                    "$lt": [
                        "$scheduled_at", 
                        {"$dateFromString": {"dateString": end_date_str}}
                    ]
                }
            ]
        }
    }


def create_scheduled_at_range_filter(start_date: Union[datetime, date, str], 
                                    end_date: Union[datetime, date, str]) -> Dict[str, Any]:
    """
    Create a MongoDB filter for scheduled_at field within a date range using $expr and $dateFromString
    
    Args:
        start_date: Start date (inclusive)
        end_date: End date (inclusive)
        
    Returns:
        MongoDB filter dictionary for scheduled_at range using $expr
    """
    def normalize_date(date_input: Union[datetime, date, str]) -> date:
        if isinstance(date_input, str):
            try:
                return datetime.strptime(date_input, "%Y-%m-%d").date()
            except ValueError:
                raise ValueError(f"Invalid date string format: {date_input}. Expected YYYY-MM-DD")
        elif isinstance(date_input, datetime):
            return date_input.date()
        elif isinstance(date_input, date):
            return date_input
        else:
            raise TypeError(f"Unsupported date type: {type(date_input)}")
    
    start_date_obj = normalize_date(start_date)
    end_date_obj = normalize_date(end_date)
    
    # Create ISO date strings for MongoDB $dateFromString
    start_date_str = f"{start_date_obj.strftime('%Y-%m-%d')}T00:00:00.000Z"
    # End date is inclusive, so we add 1 day and use $lt
    end_date_str = f"{(end_date_obj + timedelta(days=1)).strftime('%Y-%m-%d')}T00:00:00.000Z"
    
    return {
        "$expr": {
            "$and": [
                {
                    "$gte": [
                        "$scheduled_at",
                        {"$dateFromString": {"dateString": start_date_str}}
                    ]
                },
                {
                    "$lt": [
                        "$scheduled_at",
                        {"$dateFromString": {"dateString": end_date_str}}
                    ]
                }
            ]
        }
    }


def create_archive_filter(days_to_keep: int, reference_date: Union[datetime, date] = None) -> Dict[str, Any]:
    """
    Create a MongoDB filter for archiving records older than specified days using $expr and $dateFromString
    
    Args:
        days_to_keep: Number of days to keep (records older than this will be archived)
        reference_date: Reference date for calculation (defaults to today)
        
    Returns:
        MongoDB filter dictionary for archiving using $expr
    """
    if reference_date is None:
        reference_date = datetime.now().date()
    elif isinstance(reference_date, datetime):
        reference_date = reference_date.date()
    
    cutoff_date = reference_date - timedelta(days=days_to_keep - 1)
    cutoff_date_str = f"{cutoff_date.strftime('%Y-%m-%d')}T23:59:59.999Z"
    
    return {
        "$expr": {
            "$lte": [
                "$scheduled_at",
                {"$dateFromString": {"dateString": cutoff_date_str}}
            ]
        }
    }


def combine_filters(*filters: Dict[str, Any]) -> Dict[str, Any]:
    """
    Combine multiple MongoDB filters into a single filter using $and
    
    Args:
        *filters: Variable number of filter dictionaries
        
    Returns:
        Combined MongoDB filter dictionary
    """
    if not filters:
        return {}
    
    if len(filters) == 1:
        return filters[0]
    
    # Check if any filter uses $expr - if so, combine with $and
    has_expr = any("$expr" in f for f in filters)
    
    if has_expr:
        return {"$and": list(filters)}
    else:
        # Simple field-based filters can be merged directly
        combined = {}
        for filter_dict in filters:
            combined.update(filter_dict)
        return combined


def get_day_bounds_utc(schedule_date: Union[str, date, datetime]) -> Tuple[str, str]:
    """
    Given a date, return (start, end) ISO date strings for MongoDB $dateFromString
    Compatible with market repository pattern
    
    Args:
        schedule_date: Date in various formats
        
    Returns:
        Tuple of (start_date_str, end_date_str) in ISO format
    """
    if isinstance(schedule_date, str):
        # Assume YYYY-MM-DD format
        try:
            date_obj = datetime.strptime(schedule_date, "%Y-%m-%d").date()
        except ValueError:
            raise ValueError(f"Invalid date string format: {schedule_date}. Expected YYYY-MM-DD")
    elif isinstance(schedule_date, datetime):
        date_obj = schedule_date.date()
    elif isinstance(schedule_date, date):
        date_obj = schedule_date
    else:
        raise TypeError(f"Unsupported date type: {type(schedule_date)}")
    
    # Create ISO date strings for MongoDB date parsing
    start_date_str = f"{date_obj.strftime('%Y-%m-%d')}T00:00:00.000Z"
    
    # Calculate next day for end date
    end_date = date_obj + timedelta(days=1)
    end_date_str = f"{end_date.strftime('%Y-%m-%d')}T00:00:00.000Z"
    
    return start_date_str, end_date_str


# Convenience functions for common patterns
def filter_by_date(scheduled_at: Union[datetime, date, str]) -> Dict[str, Any]:
    """Convenience function for simple date filtering"""
    return create_scheduled_at_filter(scheduled_at)


def filter_by_date_range(start_date: Union[datetime, date, str], 
                        end_date: Union[datetime, date, str]) -> Dict[str, Any]:
    """Convenience function for date range filtering"""
    return create_scheduled_at_range_filter(start_date, end_date)


def filter_for_archive(days_to_keep: int) -> Dict[str, Any]:
    """Convenience function for archive filtering"""
    return create_archive_filter(days_to_keep)


def validate_schedule_date(schedule_date: str) -> bool:
    """
    Validate schedule date format (YYYY-MM-DD).
    
    Args:
        schedule_date: Date string to validate
        
    Returns:
        bool: True if valid, False otherwise
    """
    try:
        datetime.strptime(schedule_date, "%Y-%m-%d")
        return True
    except (ValueError, TypeError):
        return False


def normalize_schedule_date(schedule_date: Optional[str] = None) -> str:
    """
    Normalize schedule date input, defaulting to today if None.
    
    Args:
        schedule_date: Optional date string in YYYY-MM-DD format
        
    Returns:
        str: Normalized date string in YYYY-MM-DD format
        
    Raises:
        ValueError: If provided date is invalid
    """
    if not schedule_date:
        return datetime.now().date().isoformat()
    
    if not validate_schedule_date(schedule_date):
        raise ValueError(f"Invalid schedule date format: {schedule_date}. Expected YYYY-MM-DD")
    
    return schedule_date


def is_today_utc(schedule_date: str) -> bool:
    """
    Check if the given schedule date is today in UTC.
    
    Args:
        schedule_date: Date string in YYYY-MM-DD format
        
    Returns:
        bool: True if the date is today in UTC
    """
    try:
        parsed_date = datetime.strptime(schedule_date, "%Y-%m-%d").date()
        today_utc = datetime.now().date()
        return parsed_date == today_utc
    except (ValueError, TypeError):
        return False
