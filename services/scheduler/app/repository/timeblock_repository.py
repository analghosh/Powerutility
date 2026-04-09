"""
Timeblock Repository
Handles database operations for timeblock master collection
"""

import logging
from typing import Dict, Any, List, Optional
from datetime import datetime

from ..core.config import settings
from libs.database.base_repository import BaseRepository

logger = logging.getLogger(__name__)


class TimeblockRepository(BaseRepository):
    """Repository for timeblock master collection"""

    def __init__(self):
        super().__init__(
            mongodb_uri=settings.MONGODB_URI,
            database_name=settings.MONGODB_DATABASE,
            collection_name=settings.MONGODB_TIMEBLOCK_MASTER_COLLECTION
        )
