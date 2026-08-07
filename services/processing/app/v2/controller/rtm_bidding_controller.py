"""
RTM (Real-Time Market) bidding controller layer using v2 API patterns.
Handles HTTP requests for RTM bidding-related operations with authentication, authorization, and audit logging.
"""

import logging
from typing import Dict, Any, Optional, List
from fastapi import HTTPException, status, Request

from ..services.rtm_bidding_service import rtm_bidding_service
from ..services.block_bid_services import block_bid_service
logger = logging.getLogger(__name__)


class RTMBiddingController:
    """
    Controller for handling RTM bidding-related business logic and request processing with v2 API patterns.
    """

    def __init__(self):
        self.rtm_bidding_service = rtm_bidding_service
        self.block_bid_service = block_bid_service

    def _extract_request_info(self, request: Request) -> Dict[str, Optional[str]]:
        """
        Extract useful information from the request for auditing/logging.

        Args:
            request (Request): The FastAPI request object.

        Returns:
            Dict[str, Optional[str]]: Extracted request metadata.
        """
        return {
            "ip_address": request.client.host if request.client else None,
            "user_agent": request.headers.get("user-agent"),
            "method": request.method,
            "path": request.url.path,
            "timestamp": request.headers.get("x-request-timestamp")
        }

    async def rtm_single_bid_graph(self, schedule_date: Optional[str], from_block: Optional[int], to_block: Optional[int], request: Request, current_user: Dict[str, Any] = None, audit_logger = None) -> Dict[str, Any]:
        """
        Get RTM single bid graph data with authentication, authorization, and audit logging.
        
        Args:
            schedule_date (Optional[str]): The schedule date in 'YYYY-MM-DD' format
            from_block (Optional[int]): Starting time block
            to_block (Optional[int]): Ending time block
            request (Request): FastAPI request object
            current_user (Dict[str, Any]): Current authenticated user context
            
        Returns:
            Dict[str, Any]: RTM single bid graph data
        """
        try:
            # Extract request info for audit logging
            request_info = self._extract_request_info(request)
            
            # Log the request
            logger.info(f"RTM single bid graph request initiated by user: {current_user.get('user_id', 'unknown')}, "
                       f"schedule_date: {schedule_date}, blocks: {from_block}-{to_block}")
            
            # Audit log the system operation start
            if audit_logger:
                audit_logger.log_system_operation(
                    service="processing",
                    operation="rtm_single_bid_graph_request",
                    message=f"RTM single bid graph request initiated by user: {current_user.get('user_id', 'unknown')}",
                    correlation_id=current_user.get('correlation_id', 'unknown'),
                    user_id=current_user.get('user_id', 'unknown'),
                    schedule_date=schedule_date,
                    from_block=from_block,
                    to_block=to_block,
                    request_info=request_info
                )
            
            # Call service method
            data = await self.rtm_bidding_service.rtm_single_bid_graph(schedule_date, from_block, to_block)
            
            # Log successful response
            logger.info(f"RTM single bid graph request completed successfully for user: {current_user.get('user_id', 'unknown')}")
            
            # Audit log the user action success
            if audit_logger:
                audit_logger.log_user_action(
                    service="processing",
                    user_id=current_user.get('user_id', 'unknown'),
                    action="rtm_single_bid_graph_response",
                    message="RTM single bid graph request completed successfully",
                    session_id=current_user.get('correlation_id', 'unknown'),
                    username=current_user.get('username', 'anonymous'),
                    record_count=len(data.get('data', [])) if data and data.get('data') else 0,
                    success=True
                )
            
            return {"data": data}
            
        except HTTPException:
            raise 
        except Exception as e:
            logger.error(f"Error occurred in RTMBiddingController.rtm_single_bid_graph: {str(e)}")
            
            # Audit log the error
            if audit_logger:
                audit_logger.log_error_event(
                    service="processing",
                    error_name="RTMSingleBidGraphError",
                    error_message=str(e),
                    user_id=current_user.get('user_id', 'unknown') if current_user else 'unknown',
                    correlation_id=current_user.get('correlation_id', 'unknown') if current_user else 'unknown',
                    schedule_date=schedule_date,
                    from_block=from_block,
                    to_block=to_block
                )
            
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to process RTM single bid graph data request."
            )
    
    async def rtm_single_bid_table(self, schedule_date: Optional[str], from_block: Optional[int], to_block: Optional[int], request: Request, current_user: Dict[str, Any] = None, audit_logger = None) -> Dict[str, Any]:
        """
        Get RTM single bid table data with authentication, authorization, and audit logging.
        
        Args:
            schedule_date (Optional[str]): The schedule date in 'YYYY-MM-DD' format
            from_block (Optional[int]): Starting time block
            to_block (Optional[int]): Ending time block
            request (Request): FastAPI request object
            current_user (Dict[str, Any]): Current authenticated user context
            
        Returns:
            Dict[str, Any]: RTM single bid table data
        """
        try:
            # Extract request info for audit logging
            request_info = self._extract_request_info(request)
            
            # Log the request
            logger.info(f"RTM single bid table request initiated by user: {current_user.get('user_id', 'unknown')}, "
                       f"schedule_date: {schedule_date}, blocks: {from_block}-{to_block}")
            
            # Audit log the system operation start
            if audit_logger:
                audit_logger.log_system_operation(
                    service="processing",
                    operation="rtm_single_bid_table_request",
                    message=f"RTM single bid table request initiated by user: {current_user.get('user_id', 'unknown')}",
                    correlation_id=current_user.get('correlation_id', 'unknown'),
                    user_id=current_user.get('user_id', 'unknown'),
                    schedule_date=schedule_date,
                    from_block=from_block,
                    to_block=to_block,
                    request_info=request_info
                )
            
            # Call service method
            data = await self.rtm_bidding_service.rtm_single_bid_table(schedule_date, from_block, to_block)
            
            # Log successful response
            logger.info(f"RTM single bid table request completed successfully for user: {current_user.get('user_id', 'unknown')}")
            
            # Audit log the user action success
            if audit_logger:
                audit_logger.log_user_action(
                    service="processing",
                    user_id=current_user.get('user_id', 'unknown'),
                    action="rtm_single_bid_table_response",
                    message="RTM single bid table request completed successfully",
                    session_id=current_user.get('correlation_id', 'unknown'),
                    username=current_user.get('username', 'anonymous'),
                    has_data=bool(data),
                    success=True
                )
            
            return {"data": data}
            
        except HTTPException:
            raise 
        except Exception as e:
            logger.error(f"Error occurred in RTMBiddingController.rtm_single_bid_table: {str(e)}")
            
            # Audit log the error
            if audit_logger:
                audit_logger.log_error_event(
                    service="processing",
                    error_name="RTMSingleBidTableError",
                    error_message=str(e),
                    user_id=current_user.get('user_id', 'unknown') if current_user else 'unknown',
                    correlation_id=current_user.get('correlation_id', 'unknown') if current_user else 'unknown',
                    schedule_date=schedule_date,
                    from_block=from_block,
                    to_block=to_block
                )
            
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to process RTM single bid table data request."
            )

    async def rtm_single_bid_table_old(self, schedule_date: Optional[str], from_block: Optional[int], to_block: Optional[int], request: Request, current_user: Dict[str, Any] = None, audit_logger = None) -> List[Dict[str, Any]]:
        """
        Get RTM single bid table data (old method) with authentication, authorization, and audit logging.
        
        Args:
            schedule_date: Schedule date for data retrieval
            from_block: Starting time block
            to_block: Ending time block
            request: FastAPI request object
            current_user: Current authenticated user
            
        Returns:
            List[Dict[str, Any]]: RTM single bid table data with total availability
        """
        try:
            # Extract request info for audit logging
            request_info = self._extract_request_info(request)
            
            # Log the request
            logger.info(f"RTM single bid table (old method) request initiated by user: {current_user.get('user_id', 'unknown')}, "
                       f"schedule_date: {schedule_date}, blocks: {from_block}-{to_block}")
            
            # Audit log the system operation start
            if audit_logger:
                audit_logger.log_system_operation(
                    service="processing",
                    operation="rtm_single_bid_table_old_request",
                    message=f"RTM single bid table (old method) request initiated by user: {current_user.get('user_id', 'unknown')}",
                    correlation_id=current_user.get('correlation_id', 'unknown'),
                    user_id=current_user.get('user_id', 'unknown'),
                    schedule_date=schedule_date,
                    from_block=from_block,
                    to_block=to_block,
                    request_info=request_info
                )
            
            # Call service method
            data = await self.rtm_bidding_service.rtm_single_bid_table_old(schedule_date, from_block, to_block)
            
            # Log successful response
            logger.info(f"RTM single bid table (old method) response generated: {len(data) if data else 0} records")
            
            # Audit log the user action success
            if audit_logger:
                audit_logger.log_user_action(
                    service="processing",
                    user_id=current_user.get('user_id', 'unknown'),
                    action="rtm_single_bid_table_old_success",
                    message="RTM single bid table (old method) completed successfully",
                    session_id=current_user.get('correlation_id', 'unknown'),
                    username=current_user.get('username', 'anonymous'),
                    record_count=len(data) if data else 0,
                    success=True
                )
            
            return data
            
        except Exception as e:
            logger.error(f"Error occurred in RTMBiddingController.rtm_single_bid_table_old: {str(e)}")
            
            # Audit log the error
            if audit_logger:
                audit_logger.log_error_event(
                    service="processing",
                    error_name="RTMSingleBidTableOldError",
                    error_message=str(e),
                    user_id=current_user.get('user_id', 'unknown') if current_user else 'unknown',
                    correlation_id=current_user.get('correlation_id', 'unknown') if current_user else 'unknown',
                    schedule_date=schedule_date,
                    from_block=from_block,
                    to_block=to_block
                )
            
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to process RTM single bid table (old method) data request."
            )

    async def approval_request_list(self, schedule_date: Optional[str], from_block: Optional[int], to_block: Optional[int], request: Request, current_user: Dict[str, Any] = None, audit_logger = None) -> Dict[str, Any]:
        """
        Get RTM approval request list with authentication, authorization, and audit logging.
        
        Args:
            schedule_date (Optional[str]): The schedule date in 'YYYY-MM-DD' format
            from_block (Optional[int]): Starting time block
            to_block (Optional[int]): Ending time block
            request (Request): FastAPI request object
            current_user (Dict[str, Any]): Current authenticated user context
            
        Returns:
            Dict[str, Any]: RTM approval request list data
        """
        try:
            # Extract request info for audit logging
            request_info = self._extract_request_info(request)
            
            # Log the request
            logger.info(f"RTM approval request list request initiated by user: {current_user.get('user_id', 'unknown')}, "
                       f"schedule_date: {schedule_date}, blocks: {from_block}-{to_block}")
            
            # Audit log the system operation start
            if audit_logger:
                audit_logger.log_system_operation(
                    service="processing",
                    operation="rtm_approval_request_list_request",
                    message=f"RTM approval request list initiated by user: {current_user.get('user_id', 'unknown')}",
                    correlation_id=current_user.get('correlation_id', 'unknown'),
                    user_id=current_user.get('user_id', 'unknown'),
                    schedule_date=schedule_date,
                    from_block=from_block,
                    to_block=to_block,
                    request_info=request_info
                )
            
            # Call service method
            data = await self.rtm_bidding_service.approval_request_list(schedule_date, from_block, to_block)
            
            # Log successful response
            logger.info(f"RTM approval request list request completed successfully for user: {current_user.get('user_id', 'unknown')}")
            
            # Audit log the user action success
            if audit_logger:
                audit_logger.log_user_action(
                    service="processing",
                    user_id=current_user.get('user_id', 'unknown'),
                    action="rtm_approval_request_list_response",
                    message="RTM approval request list completed successfully",
                    session_id=current_user.get('correlation_id', 'unknown'),
                    username=current_user.get('username', 'anonymous'),
                    request_count=len(data.get('data', [])) if data and data.get('data') else 0,
                    success=True
                )
            
            return {"data": data}
            
        except HTTPException:
            raise 
        except Exception as e:
            logger.error(f"Error occurred in RTMBiddingController.approval_request_list: {str(e)}")
            
            # Audit log the error
            if audit_logger:
                audit_logger.log_error_event(
                    service="processing",
                    error_name="RTMApprovalRequestListError",
                    error_message=str(e),
                    user_id=current_user.get('user_id', 'unknown') if current_user else 'unknown',
                    correlation_id=current_user.get('correlation_id', 'unknown') if current_user else 'unknown',
                    schedule_date=schedule_date,
                    from_block=from_block,
                    to_block=to_block
                )
            
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to process RTM approval request list data request."
            )

    async def bid_creation_post(self, post_data: Dict[str, Any], request: Request, current_user: Dict[str, Any] = None, audit_logger = None) -> Dict[str, Any]:
        """
        Create RTM bid data with authentication, authorization, and audit logging.
        
        Args:
            post_data (Dict[str, Any]): Bid creation data
            request (Request): FastAPI request object
            current_user (Dict[str, Any]): Current authenticated user context
            
        Returns:
            Dict[str, Any]: Bid creation result
        """
        try:
            # Extract request info for audit logging
            request_info = self._extract_request_info(request)
            
            # Log the request
            logger.info(f"RTM bid creation request initiated by user: {current_user.get('user_id', 'unknown')}")
            
            # Audit log the system operation start
            if audit_logger:
                audit_logger.log_system_operation(
                    service="processing",
                    operation="rtm_bid_creation_request",
                    message=f"RTM bid creation request initiated by user: {current_user.get('user_id', 'unknown')}",
                    correlation_id=current_user.get('correlation_id', 'unknown'),
                    user_id=current_user.get('user_id', 'unknown'),
                    bid_date=post_data.get("bidingDate", ""),
                    blocks_count=len(post_data.get("time_block_range", [])),
                    request_info=request_info
                )
            
            # Call service method
            result = await self.rtm_bidding_service.bid_creation_post(post_data)
            
            # Log successful response
            logger.info(f"RTM bid creation request completed successfully for user: {current_user.get('user_id', 'unknown')}")
            
            # Audit log the user action success
            if audit_logger:
                audit_logger.log_user_action(
                    service="processing",
                    user_id=current_user.get('user_id', 'unknown'),
                    action="rtm_bid_creation_response",
                    message="RTM bid creation completed successfully",
                    session_id=current_user.get('correlation_id', 'unknown'),
                    username=current_user.get('username', 'anonymous'),
                    inserted_count=result.get("inserted_count", 0),
                    success=True
                )
            
            return result
            
        except HTTPException:
            raise 
        except Exception as e:
            logger.error(f"Error occurred in RTMBiddingController.bid_creation_post: {str(e)}")
            
            # Audit log the error
            if audit_logger:
                audit_logger.log_error_event(
                    service="processing",
                    error_name="RTMBidCreationError",
                    error_message=str(e),
                    user_id=current_user.get('user_id', 'unknown') if current_user else 'unknown',
                    correlation_id=current_user.get('correlation_id', 'unknown') if current_user else 'unknown',
                    bid_date=post_data.get("bidingDate", "")
                )
            
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to process RTM bid creation request."
            )

    async def submit_for_approval_post_data(self, request_data: Dict[str, Any], request: Request, current_user: Dict[str, Any] = None, audit_logger = None) -> Dict[str, Any]:
        """
        Submit RTM bid data for approval with authentication, authorization, and audit logging.
        
        Args:
            request_data (Dict[str, Any]): Approval request data
            request (Request): FastAPI request object
            current_user (Dict[str, Any]): Current authenticated user context
            
        Returns:
            Dict[str, Any]: Submission result
        """
        try:
            # Extract request info for audit logging
            request_info = self._extract_request_info(request)
            
            # Log the request
            logger.info(f"RTM bid approval submission request initiated by user: {current_user.get('user_id', 'unknown')}")
            
            # Audit log the system operation start
            if audit_logger:
                audit_logger.log_system_operation(
                    service="processing",
                    operation="rtm_bid_approval_submission_request",
                    message=f"RTM bid approval submission request initiated by user: {current_user.get('user_id', 'unknown')}",
                    correlation_id=current_user.get('correlation_id', 'unknown'),
                    user_id=current_user.get('user_id', 'unknown'),
                    current_user_data=request_data.get("current_user", ""),
                    data_count=len(request_data.get("data", [])),
                    request_info=request_info
                )
            
            # Call service method
            result = await self.rtm_bidding_service.submit_for_approval_post_data(request_data)
            
            # Log successful response
            logger.info(f"RTM bid approval submission request completed successfully for user: {current_user.get('user_id', 'unknown')}")
            
            # Audit log the user action success - Handled in service layer
            # if audit_logger:
            #     audit_logger.log_user_action(
            #         service="processing-",
            #         user_id=current_user.get('user_id', 'unknown'),
            #         action="approval",
            #         message="Approver will approve RTM single Bid",
            #         session_id=current_user.get('correlation_id', 'unknown'),
            #         username=current_user.get('username', 'anonymous'),
            #         submitted_count=result.get("submitted_count", 0),
            #         success=True
            #     )
            
            return result
            
        except HTTPException:
            raise 
        except ValueError as ve:
            logger.error(f"Validation error in RTMBiddingController.submit_for_approval_post_data: {str(ve)}")
            
            # Audit log the validation error
            if audit_logger:
                audit_logger.log_error_event(
                    service="processing",
                    error_name="RTMBidApprovalValidationError",
                    error_message=str(ve),
                    user_id=current_user.get('user_id', 'unknown') if current_user else 'unknown',
                    correlation_id=current_user.get('correlation_id', 'unknown') if current_user else 'unknown'
                )
            
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid request data: {str(ve)}"
            )
        except Exception as e:
            logger.error(f"Error occurred in RTMBiddingController.submit_for_approval_post_data: {str(e)}")
            
            # Audit log the error
            if audit_logger:
                audit_logger.log_error_event(
                    service="processing",
                    error_name="RTMBidApprovalSubmissionError",
                    error_message=str(e),
                    user_id=current_user.get('user_id', 'unknown') if current_user else 'unknown',
                    correlation_id=current_user.get('correlation_id', 'unknown') if current_user else 'unknown'
                )
            
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to process RTM bid approval submission request."
            )

    async def submit_for_pre_exchange_data(self, data: List[Dict[str, Any]], request: Request, current_user: Dict[str, Any] = None, audit_logger = None) -> Dict[str, Any]:
        """
        Submit RTM bid data for pre-exchange processing with authentication, authorization, and audit logging.
        
        Args:
            data (List[Dict[str, Any]]): Pre-exchange data
            request (Request): FastAPI request object
            current_user (Dict[str, Any]): Current authenticated user context
            
        Returns:
            Dict[str, Any]: Processing result
        """
        try:
            # Extract request info for audit logging
            request_info = self._extract_request_info(request)
            
            # Log the request
            logger.info(f"RTM pre-exchange data submission request initiated by user: {current_user.get('user_id', 'unknown')}")
            
            # Audit log the system operation start
            if audit_logger:
                audit_logger.log_system_operation(
                    service="processing",
                    operation="rtm_pre_exchange_submission_request",
                    message=f"RTM pre-exchange data submission request initiated by user: {current_user.get('user_id', 'unknown')}",
                    correlation_id=current_user.get('correlation_id', 'unknown'),
                    user_id=current_user.get('user_id', 'unknown'),
                    data_count=len(data),
                    request_info=request_info
                )
            
            # Call service method
            result = await self.rtm_bidding_service.submit_for_pre_exchange_data(data)
            
            # Log successful response
            logger.info(f"RTM pre-exchange data submission request completed successfully for user: {current_user.get('user_id', 'unknown')}")
            
            # Audit log the user action success
            if audit_logger:
                audit_logger.log_user_action(
                    service="processing",
                    user_id=current_user.get('user_id', 'unknown'),
                    action="rtm_pre_exchange_submission_response",
                    message="RTM pre-exchange data submission completed successfully",
                    session_id=current_user.get('correlation_id', 'unknown'),
                    username=current_user.get('username', 'anonymous'),
                    updated_count=result.get("updated_count", 0),
                    success=True
                )
            
            return result
            
        except HTTPException:
            raise 
        except Exception as e:
            logger.error(f"Error occurred in RTMBiddingController.submit_for_pre_exchange_data: {str(e)}")
            
            # Audit log the error
            if audit_logger:
                audit_logger.log_error_event(
                    service="processing",
                    error_name="RTMPreExchangeSubmissionError",
                    error_message=str(e),
                    user_id=current_user.get('user_id', 'unknown') if current_user else 'unknown',
                    correlation_id=current_user.get('correlation_id', 'unknown') if current_user else 'unknown'
                )
            
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to process RTM pre-exchange data submission request."
            )

    async def cal_scheduling_table_data(self, schedule_date: Optional[str], request: Request, current_user: Dict[str, Any] = None, audit_logger = None) -> Dict[str, Any]:
        """
        Calculate RTM scheduling table data with authentication, authorization, and audit logging.
        
        Args:
            schedule_date (Optional[str]): The schedule date in 'YYYY-MM-DD' format
            request (Request): FastAPI request object
            current_user (Dict[str, Any]): Current authenticated user context
            
        Returns:
            Dict[str, Any]: RTM scheduling calculation data
        """
        try:
            # Extract request info for audit logging
            request_info = self._extract_request_info(request)
            
            # Log the request
            logger.info(f"RTM scheduling table calculation request initiated by user: {current_user.get('user_id', 'unknown')}, "
                       f"schedule_date: {schedule_date}")
            
            # Audit log the system operation start
            if audit_logger:
                audit_logger.log_system_operation(
                    service="processing",
                    operation="rtm_scheduling_table_calculation_request",
                    message=f"RTM scheduling table calculation request initiated by user: {current_user.get('user_id', 'unknown')}",
                    correlation_id=current_user.get('correlation_id', 'unknown'),
                    user_id=current_user.get('user_id', 'unknown'),
                    schedule_date=schedule_date,
                    request_info=request_info
                )
            
            # Call service method
            data = await self.rtm_bidding_service.cal_scheduling_table_data(schedule_date)
            
            # Log successful response
            logger.info(f"RTM scheduling table calculation request completed successfully for user: {current_user.get('user_id', 'unknown')}")
            
            # Audit log the user action success
            if audit_logger:
                audit_logger.log_user_action(
                    service="processing",
                    user_id=current_user.get('user_id', 'unknown'),
                    action="rtm_scheduling_table_calculation_response",
                    message="RTM scheduling table calculation completed successfully",
                    session_id=current_user.get('correlation_id', 'unknown'),
                    username=current_user.get('username', 'anonymous'),
                    has_data=bool(data),
                    success=True
                )
            
            return {"data": data}
            
        except HTTPException:
            raise 
        except Exception as e:
            logger.error(f"Error occurred in RTMBiddingController.cal_scheduling_table_data: {str(e)}")
            
            # Audit log the error
            if audit_logger:
                audit_logger.log_error_event(
                    service="processing",
                    error_name="RTMSchedulingTableCalculationError",
                    error_message=str(e),
                    user_id=current_user.get('user_id', 'unknown') if current_user else 'unknown',
                    correlation_id=current_user.get('correlation_id', 'unknown') if current_user else 'unknown',
                    schedule_date=schedule_date
                )
            
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to process RTM scheduling table calculation request."
            )

    async def submit_for_approval_block_bid(self, request: Dict[str, Any]) -> Dict[str, Any]:
        try:
            logger.info(f"Checking request: {request}")
            data = await self.block_bid_service.insert_block_bid(request)
            return {"data": data}
        except HTTPException:
            raise
        except Exception:
            logger.exception("Error occurred in block bid")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to process block bid request."
            )
    
    async def block_bid_approval_request_list(self, schedule_date) -> Dict[str, Any]:
        try:
            data = await self.block_bid_service.get_block_bid_submitted_for_approval_list(schedule_date)
            return {"data": data}
        except HTTPException:
            raise 
        except Exception:
            logger.exception("Error occurred in DsmoverviewController.dsm")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to process dsm data request."
            )
    
    async def update_status_block_bid(self, request: Dict[str, Any]) -> Dict[str, Any]:
        try:
            logger.info(f"Checking request: {request}")
            data = await self.block_bid_service.update_block_bid_status(request)
            return {"data": data}
        except HTTPException:
            raise
        except Exception:
            logger.exception("Error occurred in block bid")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to process block bid request."
            )

    async def download_iex_excel(self, biding_date: str, market_type: str = "RTM"):
        try:
            return await self.block_bid_service.export_iex_excel(biding_date, market_type)
        except HTTPException:
            raise
        except Exception:
            logger.exception("Error generating IEX Excel for block bids")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to generate IEX Excel file."
            )

    async def get_block_bid_submission_details(
        self, date_str: str, market_type: Optional[str] = None
    ) -> Dict[str, Any]:
        try:
            data = await self.block_bid_service.get_bid_submission_details(date_str, market_type)
            return {"data": data}
        except HTTPException:
            raise
        except Exception:
            logger.exception("Error fetching block bid submission details")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to fetch bid submission details.")

    async def get_block_bid_book(self, biding_date: str) -> Dict[str, Any]:
        try:
            data = await self.block_bid_service.get_bid_book(biding_date)
            return {"data": data}
        except HTTPException:
            raise
        except Exception:
            logger.exception("Error fetching block bid book")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to fetch bid book.")

    async def get_block_bid_trade_book(self, schedule_date: str) -> Dict[str, Any]:
        try:
            return await self.block_bid_service.get_trade_book(schedule_date)
        except HTTPException:
            raise
        except Exception:
            logger.exception("Error fetching block bid trade book")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to fetch trade book.")

    async def mark_block_bid_withdrawal(self, post_data: Dict[str, Any]) -> Dict[str, Any]:
        """Mark a block bid as Cancelled locally (no exchange call)."""
        bid_id = post_data.get("bid_id", "")
        remark = post_data.get("remark", "")
        cancelled_by = post_data.get("username", "")
        if not bid_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="bid_id is required")
        try:
            return await self.block_bid_service.mark_bid_for_withdrawal(bid_id, remark, cancelled_by)
        except HTTPException:
            raise
        except Exception:
            logger.exception("Error marking block bid for withdrawal")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to mark bid for withdrawal.")

    async def cancel_block_bid(self, post_data: Dict[str, Any], correlation_id: str = "") -> Dict[str, Any]:
        """Submit block bid cancellation to IEX exchange for given order_ids."""
        order_ids    = post_data.get("order_ids", [])
        biding_date  = post_data.get("schedule_date", "")
        cancelled_by = post_data.get("username", "")
        if not order_ids or not biding_date:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="order_ids and schedule_date are required")
        try:
            return await self.block_bid_service.cancel_block_bid_on_exchange(
                order_ids=[int(oid) for oid in order_ids],
                biding_date=biding_date,
                cancelled_by=cancelled_by,
                correlation_id=correlation_id,
            )
        except HTTPException:
            raise
        except Exception:
            logger.exception("Error cancelling block bids on exchange")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to cancel block bids on exchange.")

    async def submit_block_bid_to_exchange(self, biding_date: str, submitted_by: str = "", correlation_id: str = "") -> Dict[str, Any]:
        try:
            return await self.block_bid_service.submit_to_exchange(biding_date, submitted_by, correlation_id)
        except HTTPException:
            raise
        except Exception:
            logger.exception("Error submitting block bids to exchange")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to submit block bids to exchange."
            )

# Global controller instance
rtm_bidding_controller = RTMBiddingController()