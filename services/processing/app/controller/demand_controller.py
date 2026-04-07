# controller/demand_controller.py

import logging
from typing import Dict, Any, Optional, List, Tuple
from fastapi import HTTPException
from ..services.serve_demand_services import serve_demand_services

logger = logging.getLogger(__name__)

class DemandController:
    def __init__(self, serve_demand_services=serve_demand_services):
        self.serve_demand_services = serve_demand_services

    def demand_raw(
        self,
        date_from: Optional[str],
        date_to: Optional[str],
        from_block: Optional[int],
        to_block: Optional[int],
        filters: Dict[str, Any],
        include_fields: Optional[List[str]],
        exclude_fields: Optional[List[str]],
        sort: Optional[List[Tuple[str, int]]],
        skip: int,
        limit: Optional[int],
    ) -> Dict[str, Any]:
        try:
            return self.serve_demand_services.demand_raw(
                date_from=date_from,
                date_to=date_to,
                from_block=from_block,
                to_block=to_block,
                filters=filters,
                include_fields=include_fields,
                exclude_fields=exclude_fields,
                sort=sort,
                skip=skip,
                limit=limit,
            )
        except Exception:
            logger.exception("demand_raw failed")
            raise HTTPException(status_code=500, detail="Failed to fetch raw serve_demand_table.")

# Global instance for routes to import
demand_controller = DemandController()
