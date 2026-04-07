"""
Dashboard service layer using repository pattern.
Handles business logic for dashboard-related data operations.
"""

import logging
from typing import Dict, Any
from fastapi import UploadFile, File
import pandas as pd
import io  # <-- add this

from ..repository.serve_supply_table_repository import serveSupplyTableRepository
from ..repository.limit_value_repository import limitVallueRepository
from ..core.helpers import dsm_calculation

logger = logging.getLogger(__name__)


class LimitValueService:
    """
    Service class responsible for dashboard business logic.
    """

    def __init__(self, collection_name: str = "limit_value"):
        self.limitvalue_repo = limitVallueRepository()

    async def upload_csv(self, file: UploadFile = File(...)) -> Dict[str, Any]:
        try:
            # Read file contents
            contents = await file.read()
            
            # Use io.BytesIO to wrap bytes
            df = pd.read_csv(io.BytesIO(contents))

            # Convert DataFrame to list of dicts
            data = df.to_dict(orient="records")
            logger.info(f"Bulk insert successful {list(data)}")
            # Insert into MongoDB using repository
            if data:
                await self.limitvalue_repo.insert_many(data)

            return {"status": "success", }

        except Exception as e:
            logger.error(f"CSV Upload Failed: {e}", exc_info=True)
            return {"status": "error", "message": str(e)}
        
    async def fetch_all(self, schedule_date) -> Dict[str, Any]:
        try:
            
            data = await self.limitvalue_repo.fetch_all(schedule_date)

            return {"status": "success", "data": data}

        except Exception as e:
            logger.error(f"CSV Upload Failed: {e}", exc_info=True)
            return {"status": "error", "message": str(e)}

    
# Global instance
limit_value_service = LimitValueService()
