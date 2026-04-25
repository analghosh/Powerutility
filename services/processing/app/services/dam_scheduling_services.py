import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional, List

from fastapi import HTTPException

from ..repository.serve_dam_scheduling import serveDamSchedulingRepository
from ..repository.plants_master_repository import PlantsMasterRepository
from ..repository.limit_value_repository import limitVallueRepository
from ..repository.serve_demand_table_repository import DashboardRepository

logger = logging.getLogger(__name__)


class ServeDamSchedulingServices:
    """
    Service layer for fresh DAM scheduling API.
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
            "plant_data_order": [
              {
                "px_price": <float>,
                "plant_id": <string>,
                "category": <string>,
                "plant_name": <string>,
                "volume": <float>
              },
              ...
            ]
          }
        },
        ...
      ]
    }
    """

    IST = timezone(timedelta(hours=5, minutes=30))

    def __init__(self):
        self.dam_repo = serveDamSchedulingRepository(collection_name="serve_dam_scheduling")
        self.plant_repo = PlantsMasterRepository(collection_name="plant_master")
        self.limit_repo = limitVallueRepository(collection_name="limit_values")
        self.demand_repo = DashboardRepository(collection_name="serve_demand_table")

    # ---------- Helpers ----------
    @classmethod
    def _today_ist_str(cls) -> str:
        return datetime.now(cls.IST).date().isoformat()
    
    @classmethod
    def _tomorrow_ist_str(cls) -> str:
        return (datetime.now(cls.IST).date() + timedelta(days=1)).isoformat()

    # ---------- Main methods ----------

    async def schedule_by_date(
        self, schedule_date: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Build fresh DAM scheduling data with the requested structure.
        """
        date_str = schedule_date or self._tomorrow_ist_str()
        if schedule_date:
            try:
                datetime.strptime(schedule_date, "%Y-%m-%d")
            except ValueError:
                raise HTTPException(status_code=422, detail="schedule_date must be YYYY-MM-DD")

        try:
            # Get all time blocks (1-96)
            time_blocks = list(range(1, 97))
            result_data = []

            for time_block in time_blocks:
                # Get plant data for this time block
                plant_data = await self._get_plant_data_for_timeblock(date_str, time_block)
                result_data.append({
                    "time_block": time_block,
                    "plant_data": plant_data
                })

            return {"data": result_data}

        except Exception:
            logger.exception("schedule_by_date failed | schedule_date=%s", date_str)
            raise HTTPException(status_code=500, detail="Failed to build DAM schedule view.")

    async def _get_plant_data_for_timeblock(self, schedule_date: str, time_block: int) -> Dict[str, Any]:
        """
        Get plant data for a specific time block.
        """
        # Get DAM scheduling data for this time block
        dam_pipeline = [
            {"$match": {"schedule_date": schedule_date, "time_block": time_block}},
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
                    "schedule_volume": 1,
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
            {"$sort": {"px_price": -1}}  # Sort by px_price in descending order
        ]

        dam_data = await self.dam_repo.aggregate(dam_pipeline)

        # Calculate totals
        total_supply = sum(item.get("schedule_volume", 0) for item in dam_data)
        
        # Get forecast data (assuming it's in limit_values collection)
        forecast_data = await self._get_forecast_data(schedule_date, time_block)
        forecast = forecast_data.get("forecast", 0)
        demand_forecast = forecast_data.get("demand_forecast", 0)
        dam_forecasted_price = forecast_data.get("dam_forecasted_price", 0)

        # Calculate derived values
        surplus = max(0, total_supply - forecast)
        deficit = max(0, forecast - total_supply)
        bid_volume = total_supply - forecast
        remaining_volume = bid_volume
        updated_volume = total_supply
        updated_total_supply = total_supply

        # Round all values to 2 decimal places
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
        # Build plant_data_order array
        plant_data_order = []
        for item in dam_data:
            plant_data_order.append({
                "px_price": round(item.get("px_price", 0), 2),
                "plant_id": item.get("plant_id", ""),
                "category": item.get("category", ""),
                "plant_name": item.get("plant_name", ""),
                "volume": round(item.get("schedule_volume", 0), 2)
            })

        return {
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

    async def _get_forecast_data(self, schedule_date: str, time_block: int) -> Dict[str, float]:
        """
        Get forecast data from serve_demand_table collection.
        """
        try:
            # Get latest DAM forecasted price from limit_values (most recent date)
            dam_price_pipeline = [
                {
                    "$match": {
                        "block": time_block,
                        "ID": "DAM_forecasted_price"
                    }
                },
                {
                    "$sort": {"schedule_date": -1}  # Sort by date descending to get latest
                },
                {
                    "$limit": 1  # Get only the most recent record
                },
                {
                    "$project": {
                        "_id": 0,
                        "dam_forecasted_price": {"$ifNull": ["$values", 0]}
                    }
                }
            ]

            dam_price_data = await self.limit_repo.aggregate(dam_price_pipeline)
            dam_forecasted_price = dam_price_data[0].get("dam_forecasted_price", 0) if dam_price_data else 0

            # Get forecast data from serve_demand_table
            demand_pipeline = [
                {
                    "$match": {
                        "schedule_date": schedule_date,
                        "time_block": time_block,
                        "type": "DAF"  # DAF (Day Ahead Forecast) type
                    }
                },
                {
                    "$project": {
                        "_id": 0,
                        "forecast": {"$ifNull": ["$forecast", 0]},
                        "demand_forecast": {"$ifNull": ["$forecast", 0]}  # Using forecast as demand_forecast
                    }
                }
            ]

            forecast_data = await self.demand_repo.aggregate(demand_pipeline)
            if forecast_data:
                return {
                    "forecast": forecast_data[0].get("forecast", 0),
                    "demand_forecast": forecast_data[0].get("demand_forecast", 0),
                    "dam_forecasted_price": dam_forecasted_price
                }
            else:
                return {
                    "forecast": 0,
                    "demand_forecast": 0,
                    "dam_forecasted_price": dam_forecasted_price
                }

        except Exception as e:
            logger.warning(f"Failed to get forecast data for {schedule_date}, block {time_block}: {e}")
            return {
                "forecast": 0,
                "demand_forecast": 0,
                "dam_forecasted_price": 0
            }


# Global instance for controller import
serve_dam_scheduling_services = ServeDamSchedulingServices()
