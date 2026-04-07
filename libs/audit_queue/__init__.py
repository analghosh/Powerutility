"""
Audit Queue Package
"""

# This file exports the main components of the audit queue library.

from .queue_manager import MongoAuditQueue

__all__ = ['MongoAuditQueue']
