import logging
from typing import Dict, Any, Optional

from fastapi import HTTPException

from ..services.dam_scheduling_services import serve_dam_scheduling_services

logger = logging.getLogger(__name__)


class DamSchedulingController:
    """
    Controller for Fresh DAM Scheduling endpoints. Delegates to service layer.
    """

    def __init__(self, dam_services=serve_dam_scheduling_services):
        self.dam_services = dam_services

    def schedule_by_date(self, schedule_date: Optional[str]) -> Dict[str, Any]:
        try:
            return self.dam_services.schedule_by_date(schedule_date)
        except HTTPException:
            raise
        except Exception:
            logger.exception("schedule_by_date failed | schedule_date=%s", schedule_date)
            raise HTTPException(status_code=500, detail="Failed to fetch DAM schedule.")


# Global instance for routes to import
dam_scheduling_controller = DamSchedulingController()
