import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from zoneinfo import ZoneInfo  
from fastapi import HTTPException


from ..repository.scheduling_requisition_calculation import SchedulingRequisitionCalculationRepository

logger = logging.getLogger(__name__)



class RTMSchedulingServices:
    def __init__(self, collection_name: str = "scheduling_requisition_calculation_table"):
        # your existing repos
        self.scheduling_repo = SchedulingRequisitionCalculationRepository(collection_name=collection_name)
        


    def getAllPlantDataForScheduling(self) -> List[Dict[str, Any]]:
        return self.scheduling_repo.getRTMAllPlantDataForScheduling()

    
    
# # Global Instances
rtm_scheduling_services = RTMSchedulingServices()