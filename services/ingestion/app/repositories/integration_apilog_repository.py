import logging
from typing import Optional
from datetime import datetime
from pymongo import IndexModel, ASCENDING
from pymongo.errors import OperationFailure
from libs.database.mongo_client_factory import get_motor_client
from libs.models.integration_apilog_entity import IntegrationApiLogEntity

logger = logging.getLogger(__name__)

class IntegrationApiLogRepository:
    """
    Repository for Integration API Logs
    
    Handles blind writes to dynamic collections based on data_type.
    """
    
    def __init__(self, mongodb_uri: str, database_name: str):
        self.mongo_client = get_motor_client(mongodb_uri)
        self.database = self.mongo_client[database_name]
        self._initialized_collections = set()

    async def _ensure_collection(self, collection_name: str):
        """
        Ensure the collection exists with timeseries options and indexes.
        """
        if collection_name in self._initialized_collections:
            return

        try:
            if collection_name not in await self.database.list_collection_names():
                # Create timeseries collection
                await self.database.create_collection(
                    collection_name,
                    timeseries={
                        "timeField": "timestamp",
                        "metaField": "metadata",
                        "granularity": "seconds"
                    }
                )
                logger.info(f"Created timeseries collection: {collection_name}")
            
            # Create index on provider_id
            collection = self.database[collection_name]
            try:
                await collection.create_index([("provider_id", ASCENDING)])
            except OperationFailure as e:
                if e.code == 85:
                    # IndexOptionsConflict: equivalent index already exists (time series originalSpec ordering), safe to ignore
                    logger.debug("Index already exists (skipping): %s", e)
                else:
                    logger.error(f"Error creating index on {collection_name}: {e}")

            self._initialized_collections.add(collection_name)

        except Exception as e:
            logger.error(f"Error initializing collection {collection_name}: {e}")
            # Don't raise, try to proceed with insert which might auto-create (though without timeseries opts if logic fails completely)

    async def save_log(self, data_type: str, entity: IntegrationApiLogEntity) -> str:
        """
        Save the integration log to the appropriate collection.
        Returns the inserted ID as a string.
        """
        collection_name = f"{data_type}_integration_apilog"
        await self._ensure_collection(collection_name)
        
        try:
            document = entity.model_dump(by_alias=True, exclude={"id"})
            result = await self.database[collection_name].insert_one(document)
            log_id = str(result.inserted_id)
            logger.info(f"Saved {data_type} log with ID: {log_id}")
            return log_id
            
        except Exception as e:
            logger.error(f"Error saving integration log for {data_type}: {e}")
            raise
