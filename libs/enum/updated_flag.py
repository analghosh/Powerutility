from enum import Enum


class UpdatedFlag(str, Enum):
    """Enum for updated flag values"""
    REPLACED = "R"
    OLD = "O"
    UPDATED = "U"

