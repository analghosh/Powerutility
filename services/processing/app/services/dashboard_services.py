"""
Dashboard service layer using repository pattern.
Handles business logic for dashboard-related data operations.
"""

import logging
from typing import List, Optional, Dict, Any

from ..repository.dashboard_repository import DashboardRepository

logger = logging.getLogger(__name__)


class DashboardService:
    """
    Service class responsible for dashboard business logic.
    """

    def __init__(self):
        self.dashboard_repo = DashboardRepository()
        # Import here to avoid circular dependency
        from ..repository.serve_supply_table_repository import serveSupplyTableRepository
        self.supply_repo = serveSupplyTableRepository()

    def get_max_updated_at_from_supply(self) -> Optional[str]:
        """
        Get the maximum updated_at timestamp from serve_supply_table.
        Returns ISO formatted datetime string or None if no data.
        """
        try:
            from datetime import datetime
            pipeline = [
                {"$group": {"_id": None, "max_updated": {"$max": "$updated_at"}}},
                {"$project": {"_id": 0, "max_updated": 1}}
            ]
            result = self.supply_repo.aggregate(pipeline)
            if result and len(result) > 0 and result[0].get("max_updated"):
                max_updated = result[0]["max_updated"]
                # Convert to ISO string if it's a datetime object
                if isinstance(max_updated, datetime):
                    return max_updated.isoformat()
                return str(max_updated)
            return None
        except Exception as e:
            logger.exception("Failed to get max updated_at from serve_supply_table")
            return None

    def transform_od_ud(self, doc: Dict[str, Any]) -> Dict[str, Any]:
        # Get values (but do not remove)
        mp_schedule = doc.get("MP_Schedule", 0)
        mp_drawal = doc.get("MP_Drawal", 0)

        # Compute and add OD_UD
        doc["OD_UD"] = round(mp_schedule - mp_drawal, 2)

        return doc
        


    async def demand_data(self) -> Optional[Dict[str, Any]]:
        """
        Fetch demand data from the database where data_type='demand' and dc_sg_type='actual'.

        Returns:
            Optional[Dict[str, Any]]: A dictionary containing the list of documents,
                                      or None if no data found.
        """
        filter_query = {
            "data_type": "demand",
            "dc_sg_type": "actual"
        }

        try:
            # Fetch matching documents
            cursor = self.dashboard_repo.find_many(filter_query)
            count = self.dashboard_repo.count(filter_query)

            documents: List[Dict[str, Any]] = []

            for doc in cursor:
                if "_id" in doc:
                    doc["_id"] = str(doc["_id"])  #  Convert ObjectId to string
                transformed_doc = self.transform_od_ud(doc)
                documents.append(transformed_doc)

            if documents:
                return {
                    "count": count,
                    "documents": documents,
                    "last_updated_at": self.get_max_updated_at_from_supply()
                }
            else:
                logger.warning("[DashboardService] No demand data found.")
                return None

        except Exception as e:
            logger.exception(f"[DashboardService] Failed to fetch demand data: {str(e)}")
            raise


# Global instance
dashboard_service = DashboardService()
