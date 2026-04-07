from enum import Enum


class RawDataStatus(str, Enum):
    """Enum for raw data processing status"""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
