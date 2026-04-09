"""
Exchange DSM Repository
"""

import logging
from libs.database.base_serve_repository import BaseServeRepository

logger = logging.getLogger(__name__)


class ExchangeDsmRepository(BaseServeRepository):
    """Repository for Exchange DSM serve data"""
    
    def __init__(self, mongodb_uri: str, database_name: str):
        super().__init__(
            mongodb_uri=mongodb_uri,
            database_name=database_name,
            serve_collection="exchange_dsm_serve",
            historical_collection="exchange_dsm_serve_historical"
        )
        # Async initialization is deferred — call initialize() once at startup.

    async def initialize(self):
        """Initialize async resources — call once at startup from async context."""
        await self._ensure_indexes()

    async def _ensure_indexes(self):
        """Ensure indexes for optimal performance"""
        try:
            # Compound index for unique records
            await self.collection.create_index([
                ("scheduled_at", 1),
                ("time_block", 1),
                ("market_type", 1),
                ("px_name", 1)
            ], unique=True)

            # Additional indexes for common queries
            await self.collection.create_index([("scheduled_at", 1)])
            await self.collection.create_index([("px_name", 1)])

            # Historical collection indexes
            await self.historical_collection.create_index([
                ("scheduled_at", 1),
                ("time_block", 1),
                ("market_type", 1),
                ("px_name", 1)
            ])
            await self.historical_collection.create_index([("stored_at", 1)])

            logger.info("Created indexes for exchange_dsm_serve")
        except Exception as e:
            logger.error(f"Error creating indexes for exchange_dsm_serve: {e}")
