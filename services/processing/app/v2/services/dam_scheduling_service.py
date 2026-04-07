
from typing import Any, Dict, List, Optional
import logging
from fastapi import UploadFile, HTTPException
import pandas as pd
from io import BytesIO
from bson import ObjectId
import numpy as np
from datetime import datetime, timedelta, timezone

from ..repository.dam_scheduling_repository import dam_scheduling_repository
from ..repository.master_plants_repository import master_plants_repository
from ..repository.limit_values_repository import limit_values_repository
from ..repository.demand_forecast_repository import demand_forecast_repository
from libs.models.plant_entity import PlantEntity
from ...core.config import settings

logger = logging.getLogger(__name__)

class DamSchedulingService:
    def __init__(self):
        self.repository = dam_scheduling_repository
        self._create_dam_scheduling_upsert_filter = lambda plant: {'plant_id': plant['plant_id']}
        
        # V2 repositories for aggregations
        self.limit_repo = limit_values_repository
        self.demand_repo = demand_forecast_repository
        
        # Collection name for demand (from settings)
        self.demand_collection = settings.MONGODB_DEMAND_SERVE_COLLECTION
        
        # IST timezone
        self.IST = timezone(timedelta(hours=5, minutes=30))

    async def create_dam_scheduling(self, scheduling_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create a new DAM scheduling record with plant validation.
        
        Args:
            scheduling_data: Dictionary containing scheduling data
            
        Returns:
            Dictionary with creation result
        """
        try:
            scheduling_id = await self.repository.create_dam_scheduling(scheduling_data)
            return {
                "success": True,
                "scheduling_id": scheduling_id,
                "message": "DAM scheduling record created successfully"
            }
        except ValueError as e:
            # Plant validation error
            logger.warning(f"Plant validation failed: {e}")
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            logger.error(f"Error creating DAM scheduling record: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to create DAM scheduling record: {str(e)}")

    async def get_dam_scheduling_by_id(self, scheduling_id: str) -> Dict[str, Any]:
        """
        Get a DAM scheduling record by ID.
        
        Args:
            scheduling_id: String ID of the scheduling record
            
        Returns:
            Dictionary containing the scheduling record
        """
        try:
            result = await self.repository.get_dam_scheduling_by_id(scheduling_id)
            if not result:
                raise HTTPException(status_code=404, detail="DAM scheduling record not found")
            
            return {
                "success": True,
                "data": result
            }
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error retrieving DAM scheduling record {scheduling_id}: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to retrieve DAM scheduling record: {str(e)}")

    async def get_dam_scheduling_list(
        self,
        filters: Optional[Dict[str, Any]] = None,
        page: int = 1,
        page_size: int = 50,
        sort_by: Optional[str] = None,
        sort_order: str = "desc"
    ) -> Dict[str, Any]:
        """
        Get paginated list of DAM scheduling records.
        
        Args:
            filters: Optional filters to apply
            page: Page number
            page_size: Number of records per page
            sort_by: Field to sort by
            sort_order: Sort order (asc/desc)
            
        Returns:
            Dictionary containing paginated results
        """
        try:
            # Build sort parameter
            sort = None
            if sort_by:
                sort_direction = 1 if sort_order.lower() == "asc" else -1
                sort = [(sort_by, sort_direction)]
            
            result = await self.repository.get_dam_scheduling_list(
                filters=filters,
                page=page,
                page_size=page_size,
                sort=sort
            )
            
            return {
                "success": True,
                **result
            }
        except Exception as e:
            logger.error(f"Error retrieving DAM scheduling list: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to retrieve DAM scheduling list: {str(e)}")

    async def update_dam_scheduling(self, scheduling_id: str, update_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Update a DAM scheduling record with plant validation.
        
        Args:
            scheduling_id: String ID of the scheduling record
            update_data: Dictionary containing fields to update
            
        Returns:
            Dictionary with update result
        """
        try:
            result = await self.repository.update_dam_scheduling(scheduling_id, update_data)
            if not result:
                raise HTTPException(status_code=404, detail="DAM scheduling record not found")
            
            return {
                "success": True,
                "data": result,
                "message": "DAM scheduling record updated successfully"
            }
        except ValueError as e:
            # Plant validation error
            logger.warning(f"Plant validation failed during update: {e}")
            raise HTTPException(status_code=400, detail=str(e))
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error updating DAM scheduling record {scheduling_id}: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to update DAM scheduling record: {str(e)}")

    async def delete_dam_scheduling(self, scheduling_id: str) -> Dict[str, Any]:
        """
        Delete a DAM scheduling record.
        
        Args:
            scheduling_id: String ID of the scheduling record
            
        Returns:
            Dictionary with deletion result
        """
        try:
            success = await self.repository.delete_dam_scheduling(scheduling_id)
            if not success:
                raise HTTPException(status_code=404, detail="DAM scheduling record not found")
            
            return {
                "success": True,
                "message": "DAM scheduling record deleted successfully"
            }
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error deleting DAM scheduling record {scheduling_id}: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to delete DAM scheduling record: {str(e)}")

    async def get_dam_scheduling_by_plant_id(
        self, 
        plant_id: str, 
        start_date: Optional[str] = None, 
        end_date: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get DAM scheduling records for a specific plant with date range filtering.
        
        Args:
            plant_id: Plant identifier
            start_date: Optional start date in YYYY-MM-DD format (defaults to today)
            end_date: Optional end date in YYYY-MM-DD format (defaults to tomorrow)
            
        Returns:
            Dictionary containing scheduling records for the plant
        """
        try:
            result = await self.repository.get_dam_scheduling_by_plant_id(plant_id, start_date, end_date)
            
            # Get the actual date range used (for response metadata)
            from datetime import datetime, timedelta
            actual_start = start_date or datetime.now().strftime("%Y-%m-%d")
            actual_end = end_date or (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
            
            return {
                "success": True,
                "data": result,
                "count": len(result),
                "date_range": {
                    "start_date": actual_start,
                    "end_date": actual_end
                },
                "plant_id": plant_id
            }
        except Exception as e:
            logger.error(f"Error retrieving DAM scheduling records for plant {plant_id}: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to retrieve DAM scheduling records: {str(e)}")

    async def process_scheduling_file(self, file: UploadFile, actor: Dict[str, Any], request_info: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process uploaded DAM scheduling file.
        
        Args:
            file: Uploaded file
            actor: User information
            request_info: Request metadata
            
        Returns:
            Dictionary with processing result
        """
        try:
            if not file.filename or not file.filename.lower().endswith(('.csv', '.xlsx')):
                raise HTTPException(status_code=400, detail="Invalid file type. Only CSV and XLSX files are allowed.")

            file_content = await file.read()
            df = pd.read_csv(BytesIO(file_content)) if file.filename.lower().endswith('.csv') else pd.read_excel(BytesIO(file_content))
            df = df.replace({np.nan: None})  # Replace numpy NaN with None

            # Define required columns for DAM scheduling
            required_columns = ['plant_id', 'scheduled_at', 'time_block', 'volume']
            if not all(col in df.columns for col in required_columns):
                raise HTTPException(status_code=400, detail=f"Missing required columns. Required: {', '.join(required_columns)}")

            scheduling_data = []
            for _, row in df.iterrows():
                try:
                    scheduling_dict = row.to_dict()
                    # Remove backend-managed fields from the uploaded data
                    scheduling_dict.pop('created_at', None)
                    scheduling_dict.pop('updated_at', None)
                    
                    # Add metadata
                    scheduling_dict['uploaded_by'] = actor.get('user_id')
                    scheduling_dict['upload_filename'] = file.filename
                    scheduling_dict['scheduled_at'] = pd.to_datetime(scheduling_dict['scheduled_at'])
                    
                    scheduling_data.append(scheduling_dict)
                except Exception as e:
                    logger.warning(f"Skipping invalid scheduling data: {row.to_dict()}, Error: {e}")
                    continue
            
            if not scheduling_data:
                raise HTTPException(status_code=400, detail="No valid scheduling data found in the file")

            # Stage the data instead of directly inserting
            result = await self.repository.stage_dam_scheduling_data(scheduling_data, actor.get('user_id'))

            return {
                "success": True,
                "message": "File processed and staged for review",
                "filename": file.filename,
                "action_required": "Please review the data and click 'Submit' to confirm",
                **result
            }
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error processing scheduling file: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to process scheduling file: {str(e)}")

    async def bulk_upsert_dam_scheduling(self, scheduling_records: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Bulk upsert multiple DAM scheduling records with plant validation.
        
        Args:
            scheduling_records: List of scheduling data dictionaries
            
        Returns:
            Dictionary with upsert result including validation details
        """
        try:
            if not scheduling_records:
                raise HTTPException(status_code=400, detail="No scheduling records provided")
            
            result = await self.repository.bulk_upsert_dam_scheduling(scheduling_records)
            
            # Check if there were validation errors
            invalid_plants = result.get("invalid_plants", [])
            if invalid_plants and result.get("valid_records", 0) == 0:
                # All records failed validation
                raise HTTPException(
                    status_code=400, 
                    detail=f"All records failed plant validation. Invalid plants: {[p['plant_id'] for p in invalid_plants]}"
                )
            
            success_message = "DAM scheduling records upserted successfully"
            if invalid_plants:
                success_message += f" (Note: {len(invalid_plants)} records skipped due to invalid plant_ids)"
            
            return {
                "success": True,
                "message": success_message,
                **result
            }
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error bulk upserting DAM scheduling records: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to upsert DAM scheduling records: {str(e)}")

    def get_csv_format_template(self) -> str:
        """
        Get CSV format template with headers only.
        
        Returns:
            CSV template string with headers
        """
        return "scheduled_at,time_block,plant_id,volume\n"

    async def validate_plants(self, plant_ids: List[str]) -> Dict[str, Any]:
        """
        Validate multiple plant IDs against plant_master collection.
        
        Args:
            plant_ids: List of plant IDs to validate
            
        Returns:
            Dictionary with validation results
        """
        try:
            if not plant_ids:
                return {"valid_plants": [], "invalid_plants": [], "validation_results": {}}
            
            validation_results = await self.repository.validate_multiple_plants_exist(plant_ids)
            
            valid_plants = [plant_id for plant_id, is_valid in validation_results.items() if is_valid]
            invalid_plants = [plant_id for plant_id, is_valid in validation_results.items() if not is_valid]
            
            return {
                "success": True,
                "valid_plants": valid_plants,
                "invalid_plants": invalid_plants,
                "validation_results": validation_results,
                "total_checked": len(plant_ids),
                "valid_count": len(valid_plants),
                "invalid_count": len(invalid_plants)
            }
        except Exception as e:
            logger.error(f"Error validating plants: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to validate plants: {str(e)}")

    async def commit_staged_scheduling(self, user_id: str) -> Dict[str, Any]:
        """
        Commit staged DAM scheduling data to main collection.
        
        Args:
            user_id: User ID to identify staged data
            
        Returns:
            Dictionary with commit result
        """
        try:
            result = await self.repository.commit_staged_data(user_id)
            
            if result.get("committed", 0) > 0:
                return {
                    "success": True,
                    "message": "DAM scheduling data committed successfully",
                    **result
                }
            else:
                return {
                    "success": False,
                    "message": result.get("message", "No data to commit"),
                    **result
                }
                
        except Exception as e:
            logger.error(f"Error committing staged scheduling data: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to commit staged data: {str(e)}")

    async def get_staged_preview(self, user_id: str) -> Dict[str, Any]:
        """
        Get preview of staged DAM scheduling data (limited to 50 records).
        
        Args:
            user_id: User ID to identify staged data
            
        Returns:
            Dictionary with preview data
        """
        try:
            result = await self.repository.get_staged_preview(user_id)
            return {
                "success": True,
                **result
            }
        except Exception as e:
            logger.error(f"Error getting staged preview: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to get staged preview: {str(e)}")

    async def get_staged_data_paginated(
        self, 
        user_id: str, 
        page: int = 1, 
        page_size: int = 50
    ) -> Dict[str, Any]:
        """
        Get paginated staged DAM scheduling data for full review.
        
        Args:
            user_id: User ID to identify staged data
            page: Page number (1-based)
            page_size: Number of records per page (max 1000)
            
        Returns:
            Dictionary with paginated staged data
        """
        try:
            result = await self.repository.get_staged_data_paginated(user_id, page, page_size)
            return {
                "success": True,
                **result
            }
        except Exception as e:
            logger.error(f"Error getting paginated staged data: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to get paginated staged data: {str(e)}")

    async def cancel_staged_data(self, user_id: str) -> Dict[str, Any]:
        """
        Cancel/clear staged DAM scheduling data.
        
        Args:
            user_id: User ID to identify staged data
            
        Returns:
            Dictionary with cancellation result
        """
        try:
            cleared = await self.repository.clear_staged_data(user_id)
            return {
                "success": cleared,
                "message": "Staged data cleared successfully" if cleared else "No staged data found",
                "user_id": user_id
            }
        except Exception as e:
            logger.error(f"Error canceling staged data: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to cancel staged data: {str(e)}")

    async def get_dam_scheduling_summary(
        self,
        start_date: str,
        end_date: str
    ) -> Dict[str, Any]:
        """
        Get DAM scheduling summary for a date range.
        
        Args:
            start_date: Start date in YYYY-MM-DD format
            end_date: End date in YYYY-MM-DD format
            
        Returns:
            Dictionary containing summary statistics
        """
        try:
            from datetime import datetime
            
            start_dt = datetime.strptime(start_date, "%Y-%m-%d")
            end_dt = datetime.strptime(end_date, "%Y-%m-%d")
            
            result = await self.repository.get_dam_scheduling_summary(start_dt, end_dt)
            
            return {
                "success": True,
                "data": result
            }
        except Exception as e:
            logger.error(f"Error generating DAM scheduling summary: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to generate summary: {str(e)}")

    # ---------- Schedule by date methods (for dam-scheduling endpoint) ----------
    
    @classmethod
    def _today_ist_str(cls) -> str:
        """Get today's date in IST as string."""
        return datetime.now(timezone(timedelta(hours=5, minutes=30))).date().isoformat()
    
    @classmethod
    def _tomorrow_ist_str(cls) -> str:
        """Get tomorrow's date in IST as string."""
        return (datetime.now(timezone(timedelta(hours=5, minutes=30))).date() + timedelta(days=1)).isoformat()

    def schedule_by_date(
        self, schedule_date: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Build fresh DAM scheduling data with the requested structure.
        
        Produces the exact shape requested:
        {
          "data": [
            {
              "time_block": 1,
              "plant_data": {
                "forecast": <float>,
                "total_supply": <float>,
                "surplus": <float>,
                "deficit": <float>,
                "bid_volume": <float>,
                "remaining_volume": <float>,
                "updated_volume": <float>,
                "dam_forecasted_price": <float>,
                "updated_total_supply": <float>,
                "demand_forecast": <float>,
                "plant_data_order": [...]
              }
            },
            ...
          ]
        }
        """
        date_str = schedule_date or self._tomorrow_ist_str()
        if schedule_date:
            try:
                datetime.strptime(schedule_date, "%Y-%m-%d")
            except ValueError:
                raise HTTPException(status_code=422, detail="schedule_date must be YYYY-MM-DD")

        try:
            # Prepare target datetime (start of day)
            target_datetime = datetime.strptime(date_str, "%Y-%m-%d").replace(hour=0, minute=0, second=0, microsecond=0)
            
            logger.info(f"Starting optimized schedule_by_date for {date_str}")
            
            # --- Step 1: Fetch ALL DAM scheduling data for the day ---
            dam_pipeline = [
                {"$match": {"scheduled_at": target_datetime}},
                {
                    "$lookup": {
                        "from": "plant_master",
                        "localField": "plant_id",
                        "foreignField": "plant_id",
                        "as": "plant_info"
                    }
                },
                {
                    "$unwind": {
                        "path": "$plant_info",
                        "preserveNullAndEmptyArrays": True
                    }
                },
                {
                    "$project": {
                        "_id": 0,
                        "plant_id": 1,
                        "time_block": 1,
                        "volume": 1,
                        "px_price": {
                            "$convert": {
                                "input": {"$ifNull": ["$plant_info.px_price", "$plant_info.PX_price"]},
                                "to": "double",
                                "onError": 0,
                                "onNull": 0
                            }
                        },
                        "category": {"$ifNull": ["$plant_info.category", ""]},
                        "plant_name": {"$ifNull": ["$plant_info.plant_name", ""]}
                    }
                },
                {"$sort": {"time_block": 1, "px_price": -1}}
            ]
            
            dam_data_all = self.repository.repository.aggregate(self.repository.collection_name, dam_pipeline)
            
            # Group by time_block
            dam_data_by_block = {i: [] for i in range(1, 97)}
            for item in dam_data_all:
                block = item.get("time_block")
                if block in dam_data_by_block:
                    dam_data_by_block[block].append(item)
            
            logger.info(f"Fetched {len(dam_data_all)} DAM records for {date_str}")

            # --- Step 2: Fetch ALL Forecast data for the day ---
            demand_pipeline = [
                {
                    "$match": {
                        "scheduled_at": target_datetime,
                        "type": "DAF"
                    }
                },
                {
                    "$project": {
                        "_id": 0,
                        "time_block": 1,
                        "forecast": {"$ifNull": ["$forecast", 0]},
                        "demand_forecast": {"$ifNull": ["$forecast", 0]}
                    }
                }
            ]
            forecast_data_all = self.demand_repo.repository.aggregate(self.demand_collection, demand_pipeline)
            
            forecast_by_block = {i: {"forecast": 0, "demand_forecast": 0} for i in range(1, 97)}
            for item in forecast_data_all:
                block = item.get("time_block")
                if block in forecast_by_block:
                    forecast_by_block[block] = item
            
            logger.info(f"Fetched {len(forecast_data_all)} forecast records for {date_str}")

            # --- Step 3: Fetch ALL DAM Forecasted Prices ---
            dam_price_pipeline = [
                {
                    "$match": {
                        "scheduled_at": {
                            "$gte": target_datetime,
                            "$lt": target_datetime + timedelta(days=1)
                        },
                        "ID": "DAM_forecasted_price"
                    }
                },
                # We need the latest inserted record per block.
                # Assuming the limit_values_repository logic for "latest" applies here.
                # For simplicity and performance, we can just sort by scheduled_at desc and group by block.
                {
                   "$sort": {"scheduled_at": -1} 
                },
                {
                    "$group": {
                        "_id": "$block",
                        "doc": {"$first": "$$ROOT"}
                    }
                },
                {
                    "$replaceRoot": {"newRoot": "$doc"}
                },
                {
                     "$project": {
                         "block": 1,
                         "values": 1
                     }
                }
            ]
            
            price_data_all = self.limit_repo.repository.aggregate(settings.MONGODB_LIMIT_VALUES_COLLECTION, dam_price_pipeline)
            
            price_by_block = {i: 0 for i in range(1, 97)}
            for item in price_data_all:
                block = item.get("block")
                if block in price_by_block:
                    price_by_block[block] = item.get("values", 0)

            logger.info(f"Fetched {len(price_data_all)} price records for {date_str}")

            # --- Step 4: Assemble Response ---
            result_data = []
            
            for time_block in range(1, 97):
                # DAM Data
                block_dam_data = dam_data_by_block[time_block]
                
                # Forecast Data
                block_forecast = forecast_by_block[time_block]
                forecast = block_forecast.get("forecast", 0)
                demand_forecast = block_forecast.get("demand_forecast", 0)
                
                # Price Data
                dam_forecasted_price = price_by_block[time_block]

                # Calculations
                total_supply = sum(item.get("volume", 0) for item in block_dam_data)
                
                surplus = max(0, total_supply - forecast)
                deficit = max(0, forecast - total_supply)
                bid_volume = total_supply - forecast
                remaining_volume = bid_volume
                updated_volume = total_supply
                updated_total_supply = total_supply

                # Rounding
                total_supply = round(total_supply, 2)
                forecast = round(forecast, 2)
                demand_forecast = round(demand_forecast, 2)
                dam_forecasted_price = round(dam_forecasted_price, 2)
                surplus = round(surplus, 2)
                deficit = round(deficit, 2)
                bid_volume = round(bid_volume, 2)
                remaining_volume = round(remaining_volume, 2)
                updated_volume = round(updated_volume, 2)
                updated_total_supply = round(updated_total_supply, 2)

                # Plant Data Order
                plant_data_order = []
                for item in block_dam_data:
                    plant_data_order.append({
                        "px_price": round(item.get("px_price", 0), 2),
                        "plant_id": item.get("plant_id", ""),
                        "category": item.get("category", ""),
                        "plant_name": item.get("plant_name", ""),
                        "volume": round(item.get("volume", 0), 2)
                    })
                
                # Sort plant_data_order by px_price desc (already sorted in pipeline, but good to be safe/explicit if needed)
                # It was sorted in the pipeline: {"$sort": {"time_block": 1, "px_price": -1}}
                
                plant_data = {
                    "forecast": forecast,
                    "total_supply": total_supply,
                    "surplus": surplus,
                    "deficit": deficit,
                    "bid_volume": bid_volume,
                    "remaining_volume": remaining_volume,
                    "updated_volume": updated_volume,
                    "dam_forecasted_price": dam_forecasted_price,
                    "updated_total_supply": updated_total_supply,
                    "demand_forecast": demand_forecast,
                    "plant_data_order": plant_data_order
                }
                
                result_data.append({
                    "time_block": time_block,
                    "plant_data": plant_data
                })

            return {"data": result_data}

        except Exception:
            logger.exception("schedule_by_date failed | schedule_date=%s", date_str)
            raise HTTPException(status_code=500, detail="Failed to build DAM schedule view.")

dam_scheduling_service = DamSchedulingService()