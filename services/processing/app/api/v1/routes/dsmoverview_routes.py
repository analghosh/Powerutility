from enum import Enum
from fastapi import APIRouter, Request, HTTPException, status
from typing import Dict, Any
from pydantic import BaseModel, Field
from datetime import datetime
import logging
from typing import Optional


from ....controller.dsmoverview_controller import dsmoverview_controller

router = APIRouter(
    prefix="/processing/dsmoverview",
    tags=["Dsmoverview"]
)

logger = logging.getLogger(__name__)


class DsmoverviewResponse(BaseModel):
    """
    Response schema for Dsmoverview APIs.
    """
    # message: str = Field(..., description="Success message")
    # timestamp: str = Field(..., description="ISO formatted timestamp")
    data: Dict[str, Any] = Field(..., description="Payload data")


@router.get(
    "/dsm",
    response_model=DsmoverviewResponse,
    status_code=status.HTTP_200_OK,
    summary="Get DSM Data",
    response_description="DSM data with count and records"
)
async def get_data(schedule_date: Optional[str] = None):
    """
    Fetch DSM ingestion records from the database.

    Args:
        schedule_date (Optional[str]): The schedule date to filter DSM data. 
                                        Format: YYYY-MM-DD depending on your DB.

    Returns:
        DsmoverviewResponse: DSM data with metadata.
    """
    try:
        result = await dsmoverview_controller.dsm(schedule_date)
        return DsmoverviewResponse(
            # message="DSM Data Fetched Successfully.",
            # timestamp=datetime.now().isoformat(),
            data=result
        )
    except Exception as e:
        logger.exception("Error fetching DSM data")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch DSM data."
        )
    

@router.get(
    "/dsm-historical",
    response_model=DsmoverviewResponse,
    status_code=status.HTTP_200_OK,
    summary="Get DSM Data",
    response_description="DSM data with count and records"
)
    
async def get_dsmHistorical(from_date: Optional[str] = None,
    to_date: Optional[str] = None):
    """
    Fetch DSM ingestion records from the database.

    Args:
        from_date (Optional[str]): Start date (YYYY-MM-DD).
        to_date (Optional[str]): End date (YYYY-MM-DD).

    Returns:
        DsmoverviewResponse: DSM data with metadata.
    """
    try:
        result = await dsmoverview_controller.dsmHistorical(from_date, to_date)
        return DsmoverviewResponse(
            # message="DSM Data Fetched Successfully.",
            # timestamp=datetime.now().isoformat(),
            data=result
        )
    except Exception as e:
        logger.exception("Error fetching DSM data")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch DSM data."
        )

@router.get(
    "/dsm-historical-table",
    response_model=DsmoverviewResponse,
    status_code=status.HTTP_200_OK,
    summary="Get DSM Data",
    response_description="DSM data with count and records"
)
    
async def get_dsmHistoricalTable(from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    time_block: Optional[str] = None):
    """
    Fetch DSM ingestion records from the database.

    Args:
        from_date (Optional[str]): Start date (YYYY-MM-DD).
        to_date (Optional[str]): End date (YYYY-MM-DD).

    Returns:
        DsmoverviewResponse: DSM data with metadata.
    """
    try:
        result = await dsmoverview_controller.dsmHistoricalTable(from_date, to_date, time_block)
        return DsmoverviewResponse(
            # message="DSM Data Fetched Successfully.",
            # timestamp=datetime.now().isoformat(),
            data=result
        )
    except Exception as e:
        logger.exception("Error fetching DSM data")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch DSM data."
        )
    

