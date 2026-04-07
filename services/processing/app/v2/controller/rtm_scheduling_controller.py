import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Union
from fastapi import HTTPException, status
from zoneinfo import ZoneInfo
from libs.audit_queue.simple_logger import AuditLogger

from services.processing.app.v2.services.rtm_scheduling_services import RTMSchedulingServices

logger = logging.getLogger(__name__)

class RTMSchedulingController:
    def __init__(self):
        self.rtm_scheduling_services = RTMSchedulingServices()
        

    # helpers
    

    async def rtm_scheduling_table(
        self,
        user_context: Optional[Dict[str, Any]] = None,
        audit_logger: Optional[AuditLogger] = None
    ) -> Union[List[Dict[str, Any]], Dict[str, Any]]:
        """
        Returns RTM scheduling table data.
        Matches v1 API response format exactly.
        
        Args:
            user_context: Optional user context dictionary
            audit_logger: Optional audit logger instance
        
        Returns:
            List of scheduling records or {"detail": "No data"} if empty
        """
        correlation_id = user_context.get("correlation_id", "unknown") if user_context else "unknown"
        
        try:
            logger.info(f"[RTMSchedulingController] Starting getAllPlantDataForScheduling - Correlation ID: {correlation_id}")
            
            data = self.rtm_scheduling_services.getAllPlantDataForScheduling()
            
            logger.info(f"[RTMSchedulingController] Retrieved {len(data) if isinstance(data, list) else 0} records from getAllPlantDataForScheduling - Correlation ID: {correlation_id}")
            
            if not data:
                logger.warning(f"[RTMSchedulingController] No data found in getAllPlantDataForScheduling - Correlation ID: {correlation_id}")
                return {"detail": f"No data "}
            
            logger.info(f"[RTMSchedulingController] Successfully returning rtm_scheduling_table data - Correlation ID: {correlation_id}")
            return data
            
        except HTTPException as exc:
            logger.error(f"[RTMSchedulingController] HTTPException in rtm_scheduling_table - Status: {exc.status_code}, Detail: {exc.detail}, Correlation ID: {correlation_id}")
            if exc.status_code == 404:
                return {"detail": f"No data "}
            raise
        except Exception as e:
            logger.exception(f"[RTMSchedulingController] Unexpected error in rtm_scheduling_table - Correlation ID: {correlation_id}")
            if audit_logger:
                audit_logger.log_error_event(
                    service="processing",
                    error_name="RTMSchedulingTableServiceError",
                    error_message=str(e),
                    user_id=user_context.get("user_id", "anonymous") if user_context else "anonymous",
                    correlation_id=correlation_id,
                    error_type=type(e).__name__
                )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to process rtm scheduling table request: {str(e)}"
            )
    

    async def rtm_scheduling_cal_table(
        self,
        schedule_date: Optional[str] = None,
        run_with_exchange: bool = False,
        user_context: Optional[Dict[str, Any]] = None,
        audit_logger: Optional[AuditLogger] = None
    ) -> Dict[str, Any]:
        """
        Returns RTM scheduling calculation table data.
        Matches v1 API response format exactly: {"data": <result>}
        
        Args:
            schedule_date: Optional schedule date in YYYY-MM-DD format
            user_context: Optional user context dictionary
            audit_logger: Optional audit logger instance
        
        Returns:
            Dictionary with "data" key containing calculation results
        """
        correlation_id = user_context.get("correlation_id", "unknown") if user_context else "unknown"
        
        try:
            logger.info(f"[RTMSchedulingController] Starting calSchedulingTableData - Schedule Date: {schedule_date}, Correlation ID: {correlation_id}")
            
            # Validate schedule_date format if provided
            if schedule_date:
                try:
                    from datetime import datetime
                    datetime.strptime(schedule_date, "%Y-%m-%d")
                except ValueError:
                    logger.error(f"[RTMSchedulingController] Invalid schedule_date format: {schedule_date}, Correlation ID: {correlation_id}")
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Invalid schedule_date format. Expected YYYY-MM-DD, got: {schedule_date}"
                    )
            
            # Call the Scheduler service's trigger endpoint instead of running calculation directly
            import httpx
            from ...core.config import settings
            
            scheduler_url = settings.SCHEDULER_SERVICE_URL
            endpoint = f"{scheduler_url}/api/v1/scheduler/rtm-scheduling/trigger"
            
            # Prepare payload
            payload: Dict[str, Any] = {"run_with_exchange": run_with_exchange}
            if schedule_date:
                payload["schedule_date"] = schedule_date

            logger.info(f"[RTMSchedulingController] Calling scheduler service at {endpoint} with payload: {payload}, Correlation ID: {correlation_id}")

            async with httpx.AsyncClient(timeout=300.0) as client:  # 5 minute timeout for long calculations
                response = await client.post(endpoint, json=payload)
                
                if response.status_code != 200:
                    error_detail = response.text
                    logger.error(f"[RTMSchedulingController] Scheduler service returned error: {response.status_code} - {error_detail}, Correlation ID: {correlation_id}")
                    raise HTTPException(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        detail=f"Scheduler service error: {error_detail}"
                    )
                
                result_data = response.json()
                logger.info(f"[RTMSchedulingController] Received response from scheduler service, Status: {result_data.get('status')}, Correlation ID: {correlation_id}")
                
                # Extract data from scheduler response
                # Scheduler returns: {"status": "success", "message": "...", "data": {...}}
                result = result_data.get("data", {})
            
            # Validate result structure
            if not isinstance(result, dict):
                logger.error(f"[RTMSchedulingController] Invalid result type from service: {type(result)}, Correlation ID: {correlation_id}")
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Invalid response format from scheduling service"
                )
            
            logger.info(f"[RTMSchedulingController] Completed calSchedulingTableData - Schedule Date: {schedule_date}, Correlation ID: {correlation_id}")
            
            # Convert integer keys to strings for JSON compatibility (if needed)
            # V1's reviseDataTotalsupply has integer keys (1, 2, 3...), but JSON requires string keys
            data_dict = {}
            if isinstance(result, dict):
                for key, value in result.items():
                    # Convert key to string if it's an integer
                    str_key = str(key) if isinstance(key, (int, float)) else key
                    data_dict[str_key] = value
            
            # Wrap result in {"data": ...} to match V1 API response format
            # V1 returns {"data": reviseDataTotalsupply} where reviseDataTotalsupply is a dict keyed by time_block
            return {"data": data_dict if data_dict else result}
            
        except HTTPException as exc:
            logger.error(f"[RTMSchedulingController] HTTPException in rtm_scheduling_cal_table - Status: {exc.status_code}, Detail: {exc.detail}, Schedule Date: {schedule_date}, Correlation ID: {correlation_id}")
            if audit_logger:
                audit_logger.log_error_event(
                    service="processing",
                    error_name="RTMSchedulingCalculationHTTPError",
                    error_message=str(exc.detail),
                    user_id=user_context.get("user_id", "anonymous") if user_context else "anonymous",
                    correlation_id=correlation_id,
                    error_type="HTTPException",
                    status_code=exc.status_code,
                    details={"schedule_date": schedule_date}
                )
            raise
        except Exception as e:
            logger.exception(f"[RTMSchedulingController] Unexpected error in rtm_scheduling_cal_table - Schedule Date: {schedule_date}, Correlation ID: {correlation_id}")
            if audit_logger:
                audit_logger.log_error_event(
                    service="processing",
                    error_name="RTMSchedulingCalculationServiceError",
                    error_message=str(e),
                    user_id=user_context.get("user_id", "anonymous") if user_context else "anonymous",
                    correlation_id=correlation_id,
                    error_type=type(e).__name__,
                    details={"schedule_date": schedule_date}
                )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to process rtm scheduling calculation request: {str(e)}"
            )

    async def get_revision_no(
        self,
        schedule_date: str,
        user_context: Optional[Dict[str, Any]] = None,
        audit_logger: Optional[AuditLogger] = None
    ) -> Dict[str, Any]:
        """
        Get revision number for a schedule date.
        Mirrors PHP GetRevisionNo() method.
        
        Args:
            schedule_date: Schedule date in YYYY-MM-DD format
            user_context: Optional user context dictionary
            audit_logger: Optional audit logger instance
        
        Returns:
            Dict with revision numbers
        """
        correlation_id = user_context.get("correlation_id", "unknown") if user_context else "unknown"
        
        try:
            logger.info(f"[RTMSchedulingController] Starting get_revision_no - Schedule Date: {schedule_date}, Correlation ID: {correlation_id}")
            
            result = self.rtm_scheduling_services.get_revision_no(schedule_date)
            
            logger.info(f"[RTMSchedulingController] Completed get_revision_no - Schedule Date: {schedule_date}, Correlation ID: {correlation_id}")
            
            return result
            
        except Exception as e:
            logger.exception(f"[RTMSchedulingController] Unexpected error in get_revision_no - Schedule Date: {schedule_date}, Correlation ID: {correlation_id}")
            if audit_logger:
                audit_logger.log_error_event(
                    service="processing",
                    error_name="RTMSchedulingRevisionNoError",
                    error_message=str(e),
                    user_id=user_context.get("user_id", "anonymous") if user_context else "anonymous",
                    correlation_id=correlation_id,
                    error_type=type(e).__name__,
                    details={"schedule_date": schedule_date}
                )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to get revision number: {str(e)}"
            )

    async def get_scheduling_graph_data(
        self,
        schedule_date: str,
        from_block: Optional[int] = None,
        to_block: Optional[int] = None,
        user_context: Optional[Dict[str, Any]] = None,
        audit_logger: Optional[AuditLogger] = None
    ) -> List[Dict[str, Any]]:
        """
        Get scheduling graph data.
        Mirrors PHP getSchedulingGraphData() method.
        
        Args:
            schedule_date: Schedule date in YYYY-MM-DD format
            from_block: Optional starting time block
            to_block: Optional ending time block
            user_context: Optional user context dictionary
            audit_logger: Optional audit logger instance
        
        Returns:
            List of time block data for graphs
        """
        correlation_id = user_context.get("correlation_id", "unknown") if user_context else "unknown"
        
        try:
            logger.info(f"[RTMSchedulingController] Starting get_scheduling_graph_data - Schedule Date: {schedule_date}, Correlation ID: {correlation_id}")
            
            result = self.rtm_scheduling_services.get_scheduling_graph_data(
                schedule_date, from_block, to_block
            )
            
            logger.info(f"[RTMSchedulingController] Completed get_scheduling_graph_data - Schedule Date: {schedule_date}, Correlation ID: {correlation_id}")
            
            return result
            
        except Exception as e:
            logger.exception(f"[RTMSchedulingController] Unexpected error in get_scheduling_graph_data - Schedule Date: {schedule_date}, Correlation ID: {correlation_id}")
            if audit_logger:
                audit_logger.log_error_event(
                    service="processing",
                    error_name="RTMSchedulingGraphDataError",
                    error_message=str(e),
                    user_id=user_context.get("user_id", "anonymous") if user_context else "anonymous",
                    correlation_id=correlation_id,
                    error_type=type(e).__name__,
                    details={"schedule_date": schedule_date}
                )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to get scheduling graph data: {str(e)}"
            )

    async def get_realtime_scheduling_tb_data(
        self,
        schedule_date: str,
        from_block: Optional[int] = None,
        to_block: Optional[int] = None,
        user_context: Optional[Dict[str, Any]] = None,
        audit_logger: Optional[AuditLogger] = None
    ) -> Dict[str, Any]:
        """
        Get real-time scheduling table data.
        Mirrors PHP getRealtimeSchedulingTbData() method.
        
        Args:
            schedule_date: Schedule date in YYYY-MM-DD format
            from_block: Optional starting time block (1-96). If not provided, uses calculated current block
            to_block: Optional ending time block (1-96). If not provided, uses calculated end block
            user_context: Optional user context dictionary
            audit_logger: Optional audit logger instance
        
        Returns:
            Dict with table_data and Block_data
        """
        correlation_id = user_context.get("correlation_id", "unknown") if user_context else "unknown"
        
        try:
            logger.info(f"[RTMSchedulingController] Starting get_realtime_scheduling_tb_data - Schedule Date: {schedule_date}, From Block: {from_block}, To Block: {to_block}, Correlation ID: {correlation_id}")
            
            result = self.rtm_scheduling_services.get_realtime_scheduling_tb_data(
                schedule_date=schedule_date,
                from_block=from_block,
                to_block=to_block
            )
            
            logger.info(f"[RTMSchedulingController] Completed get_realtime_scheduling_tb_data - Schedule Date: {schedule_date}, From Block: {from_block}, To Block: {to_block}, Correlation ID: {correlation_id}")
            
            return result
            
        except Exception as e:
            logger.exception(f"[RTMSchedulingController] Unexpected error in get_realtime_scheduling_tb_data - Schedule Date: {schedule_date}, Correlation ID: {correlation_id}")
            if audit_logger:
                audit_logger.log_error_event(
                    service="processing",
                    error_name="RTMSchedulingRealtimeDataError",
                    error_message=str(e),
                    user_id=user_context.get("user_id", "anonymous") if user_context else "anonymous",
                    correlation_id=correlation_id,
                    error_type=type(e).__name__,
                    details={"schedule_date": schedule_date}
                )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to get realtime scheduling data: {str(e)}"
            )

    async def get_all_scheduling_data(
        self,
        exclude_date_type: str = "all",
        user_context: Optional[Dict[str, Any]] = None,
        audit_logger: Optional[AuditLogger] = None
    ) -> List[Dict[str, Any]]:
        """
        Get all scheduling data.
        Mirrors PHP getAllSchedulingData() method.
        
        Args:
            exclude_date_type: "exclude_1" (today-tomorrow), "exclude_2" (yesterday-today), 
                             "all" (yesterday-tomorrow)
            user_context: Optional user context dictionary
            audit_logger: Optional audit logger instance
        
        Returns:
            List of scheduling records
        """
        correlation_id = user_context.get("correlation_id", "unknown") if user_context else "unknown"
        
        try:
            logger.info(f"[RTMSchedulingController] Starting get_all_scheduling_data - Exclude Date Type: {exclude_date_type}, Correlation ID: {correlation_id}")
            
            result = self.rtm_scheduling_services.get_all_scheduling_data(exclude_date_type)
            
            logger.info(f"[RTMSchedulingController] Completed get_all_scheduling_data - Exclude Date Type: {exclude_date_type}, Correlation ID: {correlation_id}")
            
            return result
            
        except Exception as e:
            logger.exception(f"[RTMSchedulingController] Unexpected error in get_all_scheduling_data - Exclude Date Type: {exclude_date_type}, Correlation ID: {correlation_id}")
            if audit_logger:
                audit_logger.log_error_event(
                    service="processing",
                    error_name="RTMSchedulingAllDataError",
                    error_message=str(e),
                    user_id=user_context.get("user_id", "anonymous") if user_context else "anonymous",
                    correlation_id=correlation_id,
                    error_type=type(e).__name__,
                    details={"exclude_date_type": exclude_date_type}
                )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to get all scheduling data: {str(e)}"
            )

# Global singleton
rtm_scheduling_controller = RTMSchedulingController()