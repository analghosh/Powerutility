"""
Time-Series Repository for Serve Data
Handles demand and supply serve collections with time-series optimization
"""

import logging
from typing import Dict, Any, List, Optional
from datetime import datetime
from bson import ObjectId

from libs.database.base_serve_repository import BaseServeRepository
from libs.models.demand_serve_entity import DemandServeEntity, DemandServeHistoricalEntity
from libs.models.supply_serve_entity import SupplyServeEntity, SupplyServeHistoricalEntity
from libs.enum.updated_flag import UpdatedFlag

logger = logging.getLogger(__name__)


class ServeDataRepository(BaseServeRepository):
    """Time-series repository for serve data collections"""
    
    def __init__(self, mongodb_uri: str, database_name: str):
        super().__init__(
            mongodb_uri=mongodb_uri,
            database_name=database_name,
            serve_collection="demand_serve",
            historical_collection="demand_serve_historical"
        )
        self.demand_serve_collection = "demand_serve"
        self.demand_historical_collection = "demand_serve_historical"
        self.supply_serve_collection = "supply_serve"
        self.supply_historical_collection = "supply_serve_historical"
        self._ensure_timeseries_collections()
    
    def _ensure_timeseries_collections(self):
        """Ensure time-series collections are created for serve data"""
        try:
            # Create demand serve time-series collection
            if self.demand_serve_collection not in self.database.list_collection_names():
                self.database.create_collection(
                    self.demand_serve_collection,
                    timeseries={
                        "timeField": "scheduled_at",
                        "metaField": "metadata",
                        "granularity": "minutes"  # More granular for serve data
                    }
                )
                logger.info("Created time-series collection: %s", self.demand_serve_collection)
            
            # Create demand historical time-series collection
            if self.demand_historical_collection not in self.database.list_collection_names():
                self.database.create_collection(
                    self.demand_historical_collection,
                    timeseries={
                        "timeField": "scheduled_at",
                        "metaField": "metadata",
                        "granularity": "minutes"
                    }
                )
                logger.info("Created time-series collection: %s", self.demand_historical_collection)
            
            # Create supply serve time-series collection
            if self.supply_serve_collection not in self.database.list_collection_names():
                self.database.create_collection(
                    self.supply_serve_collection,
                    timeseries={
                        "timeField": "scheduled_at",
                        "metaField": "metadata",
                        "granularity": "minutes"
                    }
                )
                logger.info("Created time-series collection: %s", self.supply_serve_collection)
            
            # Create supply historical time-series collection
            if self.supply_historical_collection not in self.database.list_collection_names():
                self.database.create_collection(
                    self.supply_historical_collection,
                    timeseries={
                        "timeField": "scheduled_at",
                        "metaField": "metadata",
                        "granularity": "minutes"
                    }
                )
                logger.info("Created time-series collection: %s", self.supply_historical_collection)
            
            # Create indexes for better performance
            self._create_indexes()
            
        except Exception as e:
            logger.error("Error creating time-series collections: %s", e, exc_info=True)
    
    def _create_indexes(self):
        """Create indexes for time-series collections"""
        try:
            # Demand serve collection indexes
            self.demand_serve_collection_obj.create_index([("scheduled_at", 1), ("time_block", 1)])
            self.demand_serve_collection_obj.create_index([("zone", 1), ("scheduled_at", 1)])
            self.demand_serve_collection_obj.create_index([("type", 1), ("scheduled_at", 1)])
            
            # Demand historical collection indexes
            self.demand_historical_collection_obj.create_index([("scheduled_at", 1), ("time_block", 1)])
            self.demand_historical_collection_obj.create_index([("zone", 1), ("scheduled_at", 1)])
            
            # Supply serve collection indexes
            self.supply_serve_collection_obj.create_index([("scheduled_at", 1), ("time_block", 1)])
            self.supply_serve_collection_obj.create_index([("plant_name", 1), ("scheduled_at", 1)])
            self.supply_serve_collection_obj.create_index([("technology", 1), ("scheduled_at", 1)])
            
            # Supply historical collection indexes
            self.supply_historical_collection_obj.create_index([("scheduled_at", 1), ("time_block", 1)])
            self.supply_historical_collection_obj.create_index([("plant_name", 1), ("scheduled_at", 1)])
            
            logger.info("Created indexes for time-series serve collections")
            
        except Exception as e:
            logger.error("Error creating indexes: %s", e, exc_info=True)
    
    async def create_demand_serve_record(self, entity: DemandServeEntity) -> str:
        """Create demand serve record with time-series structure"""
        try:
            now = datetime.now()
            document = {
                "scheduled_at": entity.scheduled_at,
                "type": entity.type,
                "forecast": entity.forecast,
                "actual": entity.actual,
                "rostering": entity.rostering,
                "mape": entity.mape,
                "mpe": entity.mpe,
                "zone": entity.zone,
                "udm": entity.udm,
                "time_block": entity.time_block,
                "created_at": now,
                "updated_at": now,
                # Time-series specific fields
                "timestamp": entity.scheduled_at,  # Primary time field
                "metadata": {
                    "data_type": "demand",
                    "provider_type": getattr(entity, 'provider_type', 'unknown'),
                    "ingestion_method": getattr(entity, 'ingestion_method', 'unknown'),
                    "raw_data_id": getattr(entity, 'raw_data_id', None)
                }
            }
            
            result = await self.demand_serve_collection_obj.insert_one(document)
            document_id = str(result.inserted_id)
            
            logger.info("Created demand serve record: %s", document_id)
            return document_id
            
        except Exception as e:
            logger.error("Error creating demand serve record: %s", e, exc_info=True)
            raise
    
    async def create_supply_serve_record(self, entity: SupplyServeEntity) -> str:
        """Create supply serve record with time-series structure"""
        try:
            now = datetime.now()
            document = {
                "scheduled_at": entity.scheduled_at,
                "time_block": entity.time_block,
                "plant_name": entity.plant_name,
                "dc_sg": entity.dc_sg,
                "volume": entity.volume,
                "fc_price": entity.fc_price,
                "px_price": entity.px_price,
                "vc_price": entity.vc_price,
                "category": entity.category,
                "technology": entity.technology,
                "price_group": entity.price_group,
                "c_s": entity.c_s,
                "created_at": now,
                "updated_at": now,
                # Time-series specific fields
                "timestamp": entity.scheduled_at,  # Primary time field
                "metadata": {
                    "data_type": "supply",
                    "provider_type": getattr(entity, 'provider_type', 'unknown'),
                    "ingestion_method": getattr(entity, 'ingestion_method', 'unknown'),
                    "raw_data_id": getattr(entity, 'raw_data_id', None)
                }
            }
            
            result = await self.supply_serve_collection_obj.insert_one(document)
            document_id = str(result.inserted_id)
            
            logger.info("Created supply serve record: %s", document_id)
            return document_id
            
        except Exception as e:
            logger.error("Error creating supply serve record: %s", e, exc_info=True)
            raise
    
    async def get_demand_serve_data(self, start_time: datetime, end_time: datetime, limit: int = 1000) -> List[Dict[str, Any]]:
        """Get demand serve data for time range"""
        try:
            query = {
                "scheduled_at": {
                    "$gte": start_time,
                    "$lte": end_time
                }
            }
            sort = [("scheduled_at", 1)]

            records = await self.demand_serve_collection_obj.find(query).sort(sort).limit(limit).to_list(length=limit)
            logger.info("Retrieved %d demand serve records for time range", len(records))
            return records

        except Exception as e:
            logger.error("Error retrieving demand serve data: %s", e, exc_info=True)
            return []

    async def get_supply_serve_data(self, start_time: datetime, end_time: datetime, limit: int = 1000) -> List[Dict[str, Any]]:
        """Get supply serve data for time range"""
        try:
            query = {
                "scheduled_at": {
                    "$gte": start_time,
                    "$lte": end_time
                }
            }
            sort = [("scheduled_at", 1)]

            records = await self.supply_serve_collection_obj.find(query).sort(sort).limit(limit).to_list(length=limit)
            logger.info("Retrieved %d supply serve records for time range", len(records))
            return records

        except Exception as e:
            logger.error("Error retrieving supply serve data: %s", e, exc_info=True)
            return []
    
    @property
    def demand_serve_collection_obj(self):
        """Get demand serve collection"""
        return self.database[self.demand_serve_collection]
    
    @property
    def demand_historical_collection_obj(self):
        """Get demand historical collection"""
        return self.database[self.demand_historical_collection]
    
    @property
    def supply_serve_collection_obj(self):
        """Get supply serve collection"""
        return self.database[self.supply_serve_collection]
    
    @property
    def supply_historical_collection_obj(self):
        """Get supply historical collection"""
        return self.database[self.supply_historical_collection]
