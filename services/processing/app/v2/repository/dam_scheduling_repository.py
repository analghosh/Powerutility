"""
DAM Scheduling Repository for V2 API
Handles DAM scheduling-related database operations using the generic repository pattern.
"""

import logging
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime
from bson import ObjectId

from .base_repository import GenericRepository, _serialize_value
from ...core.config import settings

logger = logging.getLogger(__name__)


class DamSchedulingRepository:
    """
    Repository for DAM scheduling operations.
    Uses the generic repository pattern for consistent database operations.
    """

    def __init__(self):
        self.repository = GenericRepository()
        self.collection_name = settings.MONGODB_DAM_SCHEDULING_COLLECTION
        self.staging_collection_name = settings.MONGODB_DAM_SCHEDULING_STAGING_COLLECTION
        self.plant_master_collection = settings.MONGODB_PLANT_MASTER_COLLECTION
        logger.info(f"DamSchedulingRepository initialized with collections: {self.collection_name}, staging: {self.staging_collection_name}")

    async def ensure_indexes(self):
        """
        Ensure indexes on serve_dam_scheduling for common query patterns.
        Called once at service startup.
        """
        try:
            # Primary sort: schedule_date + time_block (most queries use this order)
            await self.repository.create_index(
                self.collection_name,
                [("schedule_date", 1), ("time_block", 1)],
                name="schedule_date_time_block_idx"
            )
            # Compound for paginated listing with plant filter
            await self.repository.create_index(
                self.collection_name,
                [("schedule_date", 1), ("time_block", 1), ("plant_id", 1)],
                name="schedule_date_time_block_plant_idx"
            )
            # scheduled_at for datetime-based queries
            await self.repository.create_index(
                self.collection_name,
                [("scheduled_at", -1)],
                name="scheduled_at_desc_idx"
            )
            logger.info(f"Indexes ensured on '{self.collection_name}'")
        except Exception as e:
            logger.error(f"Failed to ensure indexes on {self.collection_name}: {e}")

    async def validate_plant_exists(self, plant_id: str) -> bool:
        """
        Validate if a plant exists in the plant_master collection.
        
        Args:
            plant_id: Plant identifier to validate
            
        Returns:
            True if plant exists, False otherwise
        """
        try:
            logger.info(f"Validating plant_id: '{plant_id}' in collection: '{self.plant_master_collection}'")
            
            # First, let's check what documents exist in the collection
            sample_docs = await self.repository.find_many(
                collection_name=self.plant_master_collection,
                filter={},
                limit=5
            )

            logger.info(f"Sample plant_ids in collection: {[doc.get('plant_id') for doc in sample_docs]}")

            result = await self.repository.find_one(
                collection_name=self.plant_master_collection,
                filter={"plant_id": plant_id}
            )
            exists = result is not None
            logger.info(f"Plant validation for '{plant_id}': {'EXISTS' if exists else 'NOT FOUND'}")

            if not exists:
                # Let's also try a case-insensitive search
                case_insensitive_result = await self.repository.find_one(
                    collection_name=self.plant_master_collection,
                    filter={"plant_id": {"$regex": f"^{plant_id}$", "$options": "i"}}
                )
                if case_insensitive_result:
                    logger.info(f"Found plant with case-insensitive search: {case_insensitive_result.get('plant_id')}")
            
            return exists
            
        except Exception as e:
            logger.error(f"Error validating plant {plant_id}: {e}")
            return False

    async def validate_multiple_plants_exist(self, plant_ids: List[str]) -> Dict[str, bool]:
        """
        Validate multiple plants exist in the plant_master collection.
        
        Args:
            plant_ids: List of plant identifiers to validate
            
        Returns:
            Dictionary mapping plant_id to existence status
        """
        try:
            if not plant_ids:
                return {}
            
            # Query for all plant_ids at once
            result = await self.repository.find_many(
                collection_name=self.plant_master_collection,
                filter={"plant_id": {"$in": plant_ids}}
            )
            
            # Create set of existing plant_ids
            existing_plants = {plant["plant_id"] for plant in result}
            
            # Create validation result
            validation_result = {plant_id: plant_id in existing_plants for plant_id in plant_ids}
            
            logger.debug(f"Bulk plant validation: {len(existing_plants)}/{len(plant_ids)} plants exist")
            return validation_result
            
        except Exception as e:
            logger.error(f"Error validating multiple plants: {e}")
            # Return all as False if validation fails
            return {plant_id: False for plant_id in plant_ids}

    async def create_dam_scheduling(self, scheduling_data: Dict[str, Any]) -> str:
        """
        Create a new DAM scheduling record with plant validation.
        
        Args:
            scheduling_data: Dictionary containing scheduling data
            
        Returns:
            String ID of the created record
            
        Raises:
            ValueError: If plant_id is not found in plant_master collection
        """
        try:
            # Validate plant exists
            plant_id = scheduling_data.get("plant_id")
            if not plant_id:
                raise ValueError("plant_id is required")
            
            plant_exists = await self.validate_plant_exists(plant_id)
            if not plant_exists:
                raise ValueError(f"Plant with ID '{plant_id}' not found in plant_master collection")
            
            # Add timestamp if not present
            if "created_at" not in scheduling_data:
                scheduling_data["created_at"] = datetime.now()
            if "updated_at" not in scheduling_data:
                scheduling_data["updated_at"] = datetime.now()
            
            result = await self.repository.insert_one(self.collection_name, scheduling_data)
            logger.info(f"Created DAM scheduling record with ID: {result.inserted_id} for plant: {plant_id}")
            return str(result.inserted_id)
            
        except ValueError:
            raise
        except Exception as e:
            logger.error(f"Error creating DAM scheduling record: {e}")
            raise

    async def get_dam_scheduling_by_id(self, scheduling_id: str) -> Optional[Dict[str, Any]]:
        """
        Get a DAM scheduling record by ID.
        
        Args:
            scheduling_id: String ID of the scheduling record
            
        Returns:
            Dictionary containing the scheduling record or None if not found
        """
        try:
            result = await self.repository.find_by_id(self.collection_name, scheduling_id)
            if result:
                logger.info(f"Retrieved DAM scheduling record: {scheduling_id}")
            return result
            
        except Exception as e:
            logger.error(f"Error retrieving DAM scheduling record {scheduling_id}: {e}")
            return None

    async def get_dam_scheduling_list(
        self,
        filters: Optional[Dict[str, Any]] = None,
        page: int = 1,
        page_size: int = 50,
        sort: Optional[List[Tuple[str, int]]] = None
    ) -> Dict[str, Any]:
        """
        Get paginated list of DAM scheduling records.
        
        Args:
            filters: Optional MongoDB filter query
            page: Page number (1-based)
            page_size: Number of records per page
            sort: List of (field, direction) tuples for sorting
            
        Returns:
            Dictionary containing paginated results and metadata
        """
        try:
            # Default sorting by created_at descending
            if sort is None:
                sort = [("created_at", -1)]
            
            result = await self.repository.get_paginated_response(
                collection_name=self.collection_name,
                filter=filters,
                sort=sort,
                page=page,
                page_size=page_size
            )
            
            logger.info(f"Retrieved {len(result['items'])} DAM scheduling records (page {page})")
            return result
            
        except Exception as e:
            logger.error(f"Error retrieving DAM scheduling list: {e}")
            return {
                "items": [],
                "page": page,
                "page_size": page_size,
                "total": 0,
                "total_pages": 0,
                "has_next": False,
                "has_prev": False
            }

    async def update_dam_scheduling(
        self, 
        scheduling_id: str, 
        update_data: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """
        Update a DAM scheduling record with plant validation.
        
        Args:
            scheduling_id: String ID of the scheduling record
            update_data: Dictionary containing fields to update
            
        Returns:
            Updated scheduling record or None if not found
            
        Raises:
            ValueError: If plant_id is being updated and not found in plant_master collection
        """
        try:
            if not ObjectId.is_valid(scheduling_id):
                logger.warning(f"Invalid ObjectId format: {scheduling_id}")
                return None
            
            # Validate plant if plant_id is being updated
            if "plant_id" in update_data:
                plant_id = update_data["plant_id"]
                if not plant_id:
                    raise ValueError("plant_id cannot be empty")
                
                plant_exists = await self.validate_plant_exists(plant_id)
                if not plant_exists:
                    raise ValueError(f"Plant with ID '{plant_id}' not found in plant_master collection")
            
            # Add updated timestamp
            update_data["updated_at"] = datetime.now()
            
            result = await self.repository.update_one(
                self.collection_name,
                {"_id": ObjectId(scheduling_id)},
                {"$set": update_data}
            )

            if result.modified_count > 0:
                logger.info(f"Updated DAM scheduling record: {scheduling_id}")
                return await self.repository.find_by_id(self.collection_name, scheduling_id)
            else:
                logger.warning(f"No changes made to DAM scheduling record: {scheduling_id}")
                return await self.repository.find_by_id(self.collection_name, scheduling_id)
                
        except ValueError:
            raise
        except Exception as e:
            logger.error(f"Error updating DAM scheduling record {scheduling_id}: {e}")
            return None

    async def delete_dam_scheduling(self, scheduling_id: str) -> bool:
        """
        Delete a DAM scheduling record.
        
        Args:
            scheduling_id: String ID of the scheduling record
            
        Returns:
            True if deleted successfully, False otherwise
        """
        try:
            if not ObjectId.is_valid(scheduling_id):
                logger.warning(f"Invalid ObjectId format: {scheduling_id}")
                return False
            
            result = await self.repository.delete_one(
                self.collection_name,
                {"_id": ObjectId(scheduling_id)}
            )
            
            if result.deleted_count > 0:
                logger.info(f"Deleted DAM scheduling record: {scheduling_id}")
                return True
            else:
                logger.warning(f"DAM scheduling record not found for deletion: {scheduling_id}")
                return False
                
        except Exception as e:
            logger.error(f"Error deleting DAM scheduling record {scheduling_id}: {e}")
            return False

    async def get_dam_scheduling_by_date_range(
        self,
        start_date: datetime,
        end_date: datetime,
        additional_filters: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """
        Get DAM scheduling records within a date range.
        
        Args:
            start_date: Start date for filtering
            end_date: End date for filtering
            additional_filters: Optional additional MongoDB filters
            
        Returns:
            List of scheduling records within the date range
        """
        try:
            # Build date range filter
            date_filter = {
                "schedule_date": {
                    "$gte": start_date,
                    "$lte": end_date
                }
            }
            
            # Combine with additional filters if provided
            if additional_filters:
                date_filter.update(additional_filters)
            
            result = await self.repository.get_all_async(
                collection_name=self.collection_name,
                filter=date_filter,
                sort=[("schedule_date", 1), ("time_block", 1)]
            )
            
            logger.info(f"Retrieved {len(result)} DAM scheduling records for date range {start_date} to {end_date}")
            return result
            
        except Exception as e:
            logger.error(f"Error retrieving DAM scheduling records by date range: {e}")
            return []

    async def get_dam_scheduling_by_plant_id(
        self, 
        plant_id: str, 
        start_date: Optional[str] = None, 
        end_date: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Get DAM scheduling records for a specific plant with date range filtering.
        
        Args:
            plant_id: Plant identifier
            start_date: Optional start date in YYYY-MM-DD format (defaults to today)
            end_date: Optional end date in YYYY-MM-DD format (defaults to tomorrow)
            
        Returns:
            List of scheduling records for the plant with only required fields
        """
        try:
            from datetime import datetime, timedelta
            
            # Set default date range (today to tomorrow) if not provided
            if not start_date:
                start_date = datetime.now().strftime("%Y-%m-%d")
            if not end_date:
                end_date = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
            
            # Build filter with plant_id and date range
            filter_query = {
                "plant_id": plant_id,
                "schedule_date": {
                    "$gte": start_date,
                    "$lte": end_date
                }
            }
            
            # Define projection to return only required fields
            projection = {
                "_id": 0,  # Exclude MongoDB _id
                "plant_id": 1,
                "schedule_date": 1,
                "time_block": 1,
                "created_at": 1,
                "schedule_volume": 1
            }
            
            result = await self.repository.get_all_async(
                collection_name=self.collection_name,
                filter=filter_query,
                projection=projection,
                sort=[("schedule_date", 1), ("time_block", 1)]
            )
            
            logger.info(f"Retrieved {len(result)} DAM scheduling records for plant: {plant_id} (date range: {start_date} to {end_date})")
            return result
            
        except Exception as e:
            logger.error(f"Error retrieving DAM scheduling records for plant {plant_id}: {e}")
            return []

    async def get_dam_scheduling_summary(
        self,
        start_date: datetime,
        end_date: datetime
    ) -> Dict[str, Any]:
        """
        Get DAM scheduling summary with aggregated data.
        
        Args:
            start_date: Start date for summary
            end_date: End date for summary
            
        Returns:
            Dictionary containing summary statistics
        """
        try:
            pipeline = [
                {
                    "$match": {
                        "scheduled_at": {
                            "$gte": start_date,
                            "$lte": end_date
                        }
                    }
                },
                {
                    "$group": {
                        "_id": {
                            "date": {"$dateToString": {"format": "%Y-%m-%d", "date": "$scheduled_at"}},
                            "plant_id": "$plant_id"
                        },
                        "total_volume": {"$sum": "$volume"},
                        "avg_price": {"$avg": "$price"},
                        "max_price": {"$max": "$price"},
                        "min_price": {"$min": "$price"},
                        "record_count": {"$sum": 1}
                    }
                },
                {
                    "$group": {
                        "_id": "$_id.date",
                        "plants": {
                            "$push": {
                                "plant_id": "$_id.plant_id",
                                "total_volume": "$total_volume",
                                "avg_price": "$avg_price",
                                "max_price": "$max_price",
                                "min_price": "$min_price",
                                "record_count": "$record_count"
                            }
                        },
                        "daily_total_volume": {"$sum": "$total_volume"},
                        "daily_avg_price": {"$avg": "$avg_price"},
                        "daily_plant_count": {"$sum": 1}
                    }
                },
                {
                    "$sort": {"_id": 1}
                }
            ]
            
            result = await self.repository.aggregate_async(
                collection_name=self.collection_name,
                pipeline=pipeline
            )
            
            summary = {
                "date_range": {
                    "start_date": start_date.isoformat(),
                    "end_date": end_date.isoformat()
                },
                "daily_summaries": result,
                "total_days": len(result),
                "overall_volume": sum(day.get("daily_total_volume", 0) for day in result),
                "overall_avg_price": sum(day.get("daily_avg_price", 0) for day in result) / len(result) if result else 0
            }
            
            logger.info(f"Generated DAM scheduling summary for {len(result)} days")
            return summary
            
        except Exception as e:
            logger.error(f"Error generating DAM scheduling summary: {e}")
            return {
                "date_range": {
                    "start_date": start_date.isoformat(),
                    "end_date": end_date.isoformat()
                },
                "daily_summaries": [],
                "total_days": 0,
                "overall_volume": 0,
                "overall_avg_price": 0,
                "error": str(e)
            }

    async def bulk_upsert_dam_scheduling(self, scheduling_records: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Bulk upsert multiple DAM scheduling records with plant validation.
        Updates existing records or creates new ones based on unique criteria.
        
        Args:
            scheduling_records: List of scheduling data dictionaries
            
        Returns:
            Dictionary with upsert statistics and validation results
        """
        try:
            if not scheduling_records:
                return {"inserted": 0, "updated": 0, "total": 0, "invalid_plants": [], "valid_records": 0}
            
            from pymongo import UpdateOne
            
            # Extract all unique plant_ids for validation
            plant_ids = list(set(record.get("plant_id") for record in scheduling_records if record.get("plant_id")))
            
            # Validate all plants at once
            plant_validation = await self.validate_multiple_plants_exist(plant_ids)
            
            # Filter records with valid plants
            valid_records = []
            invalid_plants = []
            
            for record in scheduling_records:
                plant_id = record.get("plant_id")
                if not plant_id:
                    invalid_plants.append({"plant_id": None, "reason": "plant_id is required"})
                    continue
                
                if not plant_validation.get(plant_id, False):
                    invalid_plants.append({"plant_id": plant_id, "reason": "plant not found in plant_master"})
                    continue
                
                valid_records.append(record)
            
            if not valid_records:
                logger.warning("No valid records to upsert - all plants failed validation")
                return {
                    "inserted": 0,
                    "updated": 0,
                    "total": 0,
                    "invalid_plants": invalid_plants,
                    "valid_records": 0,
                    "total_records": len(scheduling_records)
                }
            
            # Create bulk upsert operations for valid records only
            current_time = datetime.now()
            bulk_ops = []
            
            for record in valid_records:
                # Use the correct field names for filtering
                # If the record has scheduled_at (datetime), use it; otherwise use schedule_date
                if "scheduled_at" in record:
                    filter_criteria = {
                        "plant_id": record["plant_id"],
                        "scheduled_at": record["scheduled_at"],
                        "time_block": record["time_block"]
                    }
                else:
                    # For CSV uploads, we might have schedule_date instead
                    filter_criteria = {
                        "plant_id": record["plant_id"],
                        "schedule_date": record.get("schedule_date"),
                        "time_block": record["time_block"]
                    }
                
                # Prepare update data - don't include created_at in $set to avoid conflict
                update_data = record.copy()
                update_data["updated_at"] = current_time
                
                # Remove created_at from $set to avoid conflict with $setOnInsert
                if "created_at" in update_data:
                    del update_data["created_at"]
                
                bulk_ops.append(
                    UpdateOne(
                        filter_criteria,
                        {
                            "$set": update_data,
                            "$setOnInsert": {"created_at": current_time}
                        },
                        upsert=True
                    )
                )
            
            result = await self.repository.db[self.collection_name].bulk_write(bulk_ops)

            stats = {
                "inserted": result.upserted_count,
                "updated": result.modified_count,
                "total": result.upserted_count + result.modified_count,
                "invalid_plants": invalid_plants,
                "valid_records": len(valid_records),
                "total_records": len(scheduling_records)
            }
            
            logger.info(f"Bulk upserted {stats['total']} DAM scheduling records (inserted: {stats['inserted']}, updated: {stats['updated']}, invalid: {len(invalid_plants)})")
            return stats
            
        except Exception as e:
            logger.error(f"Error bulk upserting DAM scheduling records: {e}")
            return {
                "inserted": 0,
                "updated": 0,
                "total": 0,
                "invalid_plants": [],
                "valid_records": 0,
                "total_records": len(scheduling_records),
                "error": str(e)
            }



    async def stage_dam_scheduling_data(self, scheduling_records: List[Dict[str, Any]], user_id: str) -> Dict[str, Any]:
        """
        Stage DAM scheduling data for preview before final submission.
        
        Args:
            scheduling_records: List of scheduling data dictionaries
            user_id: User ID for tracking staged data
            
        Returns:
            Dictionary with staging results and preview data
        """
        try:
            if not scheduling_records:
                return {"staged": 0, "preview": [], "user_id": user_id}
            
            # Validate all plants at once
            plant_ids = list(set(record.get("plant_id") for record in scheduling_records if record.get("plant_id")))
            plant_validation = await self.validate_multiple_plants_exist(plant_ids)
            
            # Filter records with valid plants and add metadata
            valid_records = []
            invalid_plants = []
            current_time = datetime.now()
            
            for record in scheduling_records:
                plant_id = record.get("plant_id")
                if not plant_id:
                    invalid_plants.append({"plant_id": None, "reason": "plant_id is required"})
                    continue
                
                if not plant_validation.get(plant_id, False):
                    invalid_plants.append({"plant_id": plant_id, "reason": "plant not found in plant_master"})
                    continue
                
                # Add staging metadata
                staged_record = record.copy()
                staged_record.update({
                    "staged_by": user_id,
                    "staged_at": current_time,
                    "status": "staged"
                })
                valid_records.append(staged_record)
            
            if not valid_records:
                return {
                    "staged": 0,
                    "preview": [],
                    "invalid_plants": invalid_plants,
                    "user_id": user_id,
                    "total_records": len(scheduling_records)
                }
            
            # Clear any existing staged data for this user
            await self.clear_staged_data(user_id)
            
            # Insert valid records into staging collection
            result = await self.repository.db[self.staging_collection_name].insert_many(valid_records)

            # Create preview data (first 10 records for display)
            preview_data = []
            for record in valid_records[:10]:
                preview_data.append({
                    "plant_id": record["plant_id"],
                    "scheduled_at": record.get("scheduled_at"),
                    "time_block": record["time_block"],
                    "schedule_volume": record.get("schedule_volume")
                })
            
            stats = {
                "staged": len(result.inserted_ids),
                "preview": preview_data,
                "invalid_plants": invalid_plants,
                "user_id": user_id,
                "total_records": len(scheduling_records),
                "valid_records": len(valid_records),
                "preview_count": len(preview_data),
                "has_more": len(valid_records) > 10
            }
            
            logger.info(f"Staged {stats['staged']} DAM scheduling records for user: {user_id}")
            return stats
            
        except Exception as e:
            logger.error(f"Error staging DAM scheduling records: {e}")
            return {
                "staged": 0,
                "preview": [],
                "invalid_plants": [],
                "user_id": user_id,
                "total_records": len(scheduling_records),
                "error": str(e)
            }

    async def commit_staged_data(self, user_id: str) -> Dict[str, Any]:
        """
        Commit staged data to main collection and clear staging.
        
        Args:
            user_id: User ID to identify staged data
            
        Returns:
            Dictionary with commit results
        """
        try:
            # Get staged data for this user
            cursor = self.repository.db[self.staging_collection_name].find({
                "staged_by": user_id,
                "status": "staged"
            })
            staged_records = await cursor.to_list(length=10000)
            
            if not staged_records:
                return {
                    "committed": 0,
                    "message": "No staged data found for user",
                    "user_id": user_id
                }
            
            # Remove staging metadata and prepare for main collection
            from pymongo import UpdateOne
            current_time = datetime.now()
            bulk_ops = []
            
            for record in staged_records:
                # Remove staging metadata
                clean_record = {k: v for k, v in record.items() 
                              if k not in ["_id", "staged_by", "staged_at", "status"]}
                clean_record["updated_at"] = current_time

                # Handle scheduled_at conversion - it might be string or datetime already
                scheduled_at_value = clean_record.get("scheduled_at")
                scheduled_at = None
                
                if scheduled_at_value:
                    if isinstance(scheduled_at_value, datetime):
                        # Already a datetime object, use as is
                        scheduled_at = scheduled_at_value
                    elif isinstance(scheduled_at_value, str):
                        # String value, need to parse
                        scheduled_at_str = scheduled_at_value
                        if scheduled_at_str.endswith('Z'):
                            scheduled_at_str = scheduled_at_str[:-1] + '+00:00'
                        
                        try:
                            scheduled_at = datetime.strptime(scheduled_at_str, "%Y-%m-%dT%H:%M:%S.%f%z")
                        except ValueError:
                            try:
                                scheduled_at = datetime.strptime(scheduled_at_str, "%Y-%m-%dT%H:%M:%S%z")
                            except ValueError:
                                try:
                                    # Handle cases without timezone info
                                    scheduled_at = datetime.strptime(scheduled_at_str, "%Y-%m-%dT%H:%M:%S.%f")
                                except ValueError:
                                    try:
                                        scheduled_at = datetime.strptime(scheduled_at_str, "%Y-%m-%dT%H:%M:%S")
                                    except ValueError:
                                        logger.warning(f"Could not parse scheduled_at: {scheduled_at_str}")
                                        scheduled_at = None
                    
                    # Update the clean_record with the datetime object
                    if scheduled_at:
                        clean_record["scheduled_at"] = scheduled_at
                
                # Create upsert operation
                if "scheduled_at" in clean_record and scheduled_at:
                    filter_criteria = {
                        "plant_id": clean_record["plant_id"],
                        "scheduled_at": scheduled_at,
                        "time_block": clean_record["time_block"]
                    }
                else:
                    filter_criteria = {
                        "plant_id": clean_record["plant_id"],
                        "schedule_date": clean_record.get("schedule_date"),
                        "time_block": clean_record["time_block"]
                    }
                
                # Remove created_at from $set to avoid conflict with $setOnInsert
                if "created_at" in clean_record:
                    del clean_record["created_at"]
                
                bulk_ops.append(
                    UpdateOne(
                        filter_criteria,
                        {
                            "$set": clean_record,
                            "$setOnInsert": {"created_at": current_time}
                        },
                        upsert=True
                    )
                )
            
            # Execute bulk upsert to main collection
            result = await self.repository.db[self.collection_name].bulk_write(bulk_ops)
            
            # Clear staged data for this user
            clear_result = await self.clear_staged_data(user_id)
            
            stats = {
                "committed": result.upserted_count + result.modified_count,
                "inserted": result.upserted_count,
                "updated": result.modified_count,
                "staged_records_processed": len(staged_records),
                "staging_cleared": clear_result,
                "user_id": user_id
            }
            
            logger.info(f"Committed {stats['committed']} DAM scheduling records for user: {user_id}")
            return stats
            
        except Exception as e:
            logger.error(f"Error committing staged DAM scheduling data: {e}")
            return {
                "committed": 0,
                "error": str(e),
                "user_id": user_id
            }

    async def clear_staged_data(self, user_id: str) -> bool:
        """
        Clear staged data for a specific user.
        
        Args:
            user_id: User ID to identify staged data to clear
            
        Returns:
            True if cleared successfully
        """
        try:
            result = await self.repository.db[self.staging_collection_name].delete_many({
                "staged_by": user_id
            })
            logger.info(f"Cleared {result.deleted_count} staged records for user: {user_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error clearing staged data for user {user_id}: {e}")
            return False

    async def get_staged_preview(self, user_id: str, limit: int = 50) -> Dict[str, Any]:
        """
        Get preview of staged data for a user.
        
        Args:
            user_id: User ID to identify staged data
            limit: Maximum number of records to return
            
        Returns:
            Dictionary with preview data
        """
        try:
            cursor = self.repository.db[self.staging_collection_name].find(
                {"staged_by": user_id, "status": "staged"},
                {
                    "_id": 0,
                    "plant_id": 1,
                    "schedule_date": 1,
                    "time_block": 1,
                    "schedule_volume": 1,
                    "staged_at": 1
                }
            ).limit(limit).sort([("schedule_date", 1), ("time_block", 1)])
            
            staged_records = await cursor.to_list(length=10000)

            total_count = await self.repository.db[self.staging_collection_name].count_documents({
                "staged_by": user_id,
                "status": "staged"
            })
            
            return {
                "preview": staged_records,
                "count": len(staged_records),
                "total_staged": total_count,
                "has_more": total_count > limit,
                "user_id": user_id
            }
            
        except Exception as e:
            logger.error(f"Error getting staged preview for user {user_id}: {e}")
            return {
                "preview": [],
                "count": 0,
                "total_staged": 0,
                "has_more": False,
                "user_id": user_id,
                "error": str(e)
            }

    async def get_staged_data_paginated(
        self, 
        user_id: str, 
        page: int = 1, 
        page_size: int = 50
    ) -> Dict[str, Any]:
        """
        Get paginated staged data for a user to review all records before committing.
        
        Args:
            user_id: User ID to identify staged data
            page: Page number (1-based)
            page_size: Number of records per page
            
        Returns:
            Dictionary with paginated staged data
        """
        try:
            # Validate pagination parameters
            page = max(1, page)
            page_size = max(1, min(page_size, 1000))  # Cap at 1000 records per page
            skip = (page - 1) * page_size
            
            # Get total count
            total_count = await self.repository.db[self.staging_collection_name].count_documents({
                "staged_by": user_id,
                "status": "staged"
            })

            logger.info(f"Found {total_count} staged records for user {user_id} in collection {self.staging_collection_name}")
            
            # Get paginated records with all relevant fields
            cursor = self.repository.db[self.staging_collection_name].find(
                {"staged_by": user_id, "status": "staged"},
                {
                    "_id": 0,
                    "plant_id": 1,
                    "schedule_date": 1,
                    "time_block": 1,
                    "schedule_volume": 1,
                    "uploaded_by": 1,
                    "upload_filename": 1,
                    "staged_at": 1
                }
            ).sort([("schedule_date", 1), ("time_block", 1), ("plant_id", 1)]).skip(skip).limit(page_size)
            
            try:
                staged_records = await cursor.to_list(length=10000)
            finally:
                cursor.close()
            
            # Apply serialization to handle any BSON types
            serialized_records = [_serialize_value(record) for record in staged_records]
            
            # Calculate pagination metadata
            total_pages = (total_count + page_size - 1) // page_size
            has_next = page < total_pages
            has_prev = page > 1
            
            result = {
                "data": serialized_records,
                "pagination": {
                    "page": page,
                    "page_size": page_size,
                    "total_records": total_count,
                    "total_pages": total_pages,
                    "has_next": has_next,
                    "has_prev": has_prev,
                    "records_on_page": len(serialized_records)
                },
                "user_id": user_id,
                "status": "staged"
            }
            
            logger.info(f"Retrieved paginated staged data for user {user_id}: page {page}/{total_pages}, {len(serialized_records)} records")
            return result
            
        except Exception as e:
            logger.error(f"Error getting paginated staged data for user {user_id}: {e}")
            return {
                "data": [],
                "pagination": {
                    "page": page,
                    "page_size": page_size,
                    "total_records": 0,
                    "total_pages": 0,
                    "has_next": False,
                    "has_prev": False,
                    "records_on_page": 0
                },
                "user_id": user_id,
                "status": "staged",
                "error": str(e)
            }

    async def bulk_create_dam_scheduling(self, scheduling_records: List[Dict[str, Any]]) -> Dict[str, int]:
        """
        Bulk create multiple DAM scheduling records (deprecated - use staging flow).
        This method now calls bulk_upsert_dam_scheduling for backward compatibility.
        
        Args:
            scheduling_records: List of scheduling data dictionaries
            
        Returns:
            Dictionary with creation statistics
        """
        logger.warning("bulk_create_dam_scheduling is deprecated. Use staging flow instead.")
        return await self.bulk_upsert_dam_scheduling(scheduling_records)


# Global singleton
dam_scheduling_repository = DamSchedulingRepository()
