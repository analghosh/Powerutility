"""
DAM (Day Ahead Market) bidding controller layer using v2 API patterns.
Handles HTTP requests for DAM bidding-related operations with authentication, authorization, and audit logging.
"""

import logging
from typing import Dict, Any, Optional, List
from fastapi import HTTPException, status, Request

from ..services.dam_bidding_service import dam_bidding_service

logger = logging.getLogger(__name__)


class DAMBiddingController:
    """
    Controller for handling DAM bidding-related business logic and request processing with v2 API patterns.
    """

    def __init__(self):
        self.dam_bidding_service = dam_bidding_service

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

    async def dam_single_bid_graph(self, schedule_date: Optional[str], from_block: int = 1, to_block: int = 96, request: Request = None, current_user: Dict[str, Any] = None, audit_logger = None) -> Dict[str, Any]:
        """
        Get DAM single bid graph data with authentication, authorization, and audit logging.
        
        Args:
            schedule_date (Optional[str]): The schedule date in 'YYYY-MM-DD' format
            from_block (int): Starting time block
            to_block (int): Ending time block
            request (Request): FastAPI request object
            current_user (Dict[str, Any]): Current authenticated user context
            
        Returns:
            Dict[str, Any]: DAM single bid graph data
        """
        try:
            # Extract request info for audit logging
            request_info = self._extract_request_info(request) if request else {}
            
            # Log the request
            logger.info(f"DAM single bid graph request initiated by user: {current_user.get('user_id', 'unknown')}, "
                       f"schedule_date: {schedule_date}, blocks: {from_block}-{to_block}")
            
            # Audit log the system operation start
            if audit_logger:
                audit_logger.log_system_operation(
                    service="processing",
                    operation="dam_single_bid_graph_request",
                    message=f"DAM single bid graph request initiated by user: {current_user.get('user_id', 'unknown')}",
                    correlation_id=current_user.get('correlation_id', 'unknown'),
                    user_id=current_user.get('user_id', 'unknown'),
                    schedule_date=schedule_date,
                    from_block=from_block,
                    to_block=to_block,
                    request_info=request_info
                )
            
            # Call service method
            data = await self.dam_bidding_service.dam_single_bid_graph(schedule_date, from_block, to_block)
            
            # Log successful response
            logger.info(f"DAM single bid graph request completed successfully for user: {current_user.get('user_id', 'unknown')}")
            
            # Audit log the user action success
            if audit_logger:
                audit_logger.log_user_action(
                    service="processing",
                    user_id=current_user.get('user_id', 'unknown'),
                    action="dam_single_bid_graph_response",
                    message="DAM single bid graph request completed successfully",
                    session_id=current_user.get('correlation_id', 'unknown'),
                    username=current_user.get('username', 'anonymous'),
                    record_count=len(data.get('data', [])) if data and data.get('data') else 0,
                    success=True
                )
            
            return data
            
        except HTTPException:
            raise 
        except Exception as e:
            logger.error(f"Error occurred in DAMBiddingController.dam_single_bid_graph: {str(e)}")
            
            # Audit log the error
            if audit_logger:
                audit_logger.log_error_event(
                    service="processing",
                    error_name="DAMSingleBidGraphError",
                    error_message=str(e),
                    user_id=current_user.get('user_id', 'unknown') if current_user else 'unknown',
                    correlation_id=current_user.get('correlation_id', 'unknown') if current_user else 'unknown',
                    schedule_date=schedule_date,
                    from_block=from_block,
                    to_block=to_block
                )
            
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to process DAM single bid graph data request."
            )
    
    async def dam_single_bid_table(self, schedule_date: Optional[str], from_block: int = 1, to_block: int = 96, request: Request = None, current_user: Dict[str, Any] = None, audit_logger = None) -> Dict[str, Any]:
        """
        Get DAM single bid table data with authentication, authorization, and audit logging.
        
        Args:
            schedule_date (Optional[str]): The schedule date in 'YYYY-MM-DD' format
            from_block (int): Starting time block
            to_block (int): Ending time block
            request (Request): FastAPI request object
            current_user (Dict[str, Any]): Current authenticated user context
            
        Returns:
            Dict[str, Any]: DAM single bid table data
        """
        try:
            # Extract request info for audit logging
            request_info = self._extract_request_info(request) if request else {}
            
            # Log the request
            logger.info(f"DAM single bid table request initiated by user: {current_user.get('user_id', 'unknown')}, "
                       f"schedule_date: {schedule_date}, blocks: {from_block}-{to_block}")
            
            # Audit log the system operation start
            if audit_logger:
                audit_logger.log_system_operation(
                    service="processing",
                    operation="dam_single_bid_table_request",
                    message=f"DAM single bid table request initiated by user: {current_user.get('user_id', 'unknown')}",
                    correlation_id=current_user.get('correlation_id', 'unknown'),
                    user_id=current_user.get('user_id', 'unknown'),
                    schedule_date=schedule_date,
                    from_block=from_block,
                    to_block=to_block,
                    request_info=request_info
                )
            
            # Call service method
            data = await self.dam_bidding_service.dam_single_bid_table(schedule_date, from_block, to_block)
            
            # Log successful response
            logger.info(f"DAM single bid table request completed successfully for user: {current_user.get('user_id', 'unknown')}")
            
            # Audit log the user action success
            if audit_logger:
                audit_logger.log_user_action(
                    service="processing",
                    user_id=current_user.get('user_id', 'unknown'),
                    action="dam_single_bid_table_response",
                    message="DAM single bid table request completed successfully",
                    session_id=current_user.get('correlation_id', 'unknown'),
                    username=current_user.get('username', 'anonymous'),
                    has_data=bool(data),
                    success=True
                )
            
            return data
            
        except HTTPException:
            raise 
        except Exception as e:
            logger.error(f"Error occurred in DAMBiddingController.dam_single_bid_table: {str(e)}")
            
            # Audit log the error
            if audit_logger:
                audit_logger.log_error_event(
                    service="processing",
                    error_name="DAMSingleBidTableError",
                    error_message=str(e),
                    user_id=current_user.get('user_id', 'unknown') if current_user else 'unknown',
                    correlation_id=current_user.get('correlation_id', 'unknown') if current_user else 'unknown',
                    schedule_date=schedule_date,
                    from_block=from_block,
                    to_block=to_block
                )
            
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to process DAM single bid table data request."
            )

    async def approval_request_list(self, schedule_date: Optional[str], from_block: int = 1, to_block: int = 96, request: Request = None, current_user: Dict[str, Any] = None, audit_logger = None) -> Dict[str, Any]:
        """
        Get DAM approval request list with authentication, authorization, and audit logging.
        
        Args:
            schedule_date (Optional[str]): The schedule date in 'YYYY-MM-DD' format
            from_block (int): Starting time block
            to_block (int): Ending time block
            request (Request): FastAPI request object
            current_user (Dict[str, Any]): Current authenticated user context
            
        Returns:
            Dict[str, Any]: DAM approval request list data
        """
        try:
            # Extract request info for audit logging
            request_info = self._extract_request_info(request) if request else {}
            
            # Log the request
            logger.info(f"DAM approval request list request initiated by user: {current_user.get('user_id', 'unknown')}, "
                       f"schedule_date: {schedule_date}, blocks: {from_block}-{to_block}")
            
            # Audit log the system operation start
            if audit_logger:
                audit_logger.log_system_operation(
                    service="processing",
                    operation="dam_approval_request_list_request",
                    message=f"DAM approval request list initiated by user: {current_user.get('user_id', 'unknown')}",
                    correlation_id=current_user.get('correlation_id', 'unknown'),
                    user_id=current_user.get('user_id', 'unknown'),
                    schedule_date=schedule_date,
                    from_block=from_block,
                    to_block=to_block,
                    request_info=request_info
                )
            
            # Call service method
            data = await self.dam_bidding_service.approval_request_list(schedule_date, from_block, to_block)
            
            # Log successful response
            logger.info(f"DAM approval request list request completed successfully for user: {current_user.get('user_id', 'unknown')}")
            
            # Audit log the user action success
            if audit_logger:
                audit_logger.log_user_action(
                    service="processing",
                    user_id=current_user.get('user_id', 'unknown'),
                    action="dam_approval_request_list_response",
                    message="DAM approval request list completed successfully",
                    session_id=current_user.get('correlation_id', 'unknown'),
                    username=current_user.get('username', 'anonymous'),
                    request_count=len(data.get('data', [])) if data and data.get('data') else 0,
                    success=True
                )
            
            return data
            
        except HTTPException:
            raise 
        except Exception as e:
            logger.error(f"Error occurred in DAMBiddingController.approval_request_list: {str(e)}")
            
            # Audit log the error
            if audit_logger:
                audit_logger.log_error_event(
                    service="processing",
                    error_name="DAMApprovalRequestListError",
                    error_message=str(e),
                    user_id=current_user.get('user_id', 'unknown') if current_user else 'unknown',
                    correlation_id=current_user.get('correlation_id', 'unknown') if current_user else 'unknown',
                    schedule_date=schedule_date,
                    from_block=from_block,
                    to_block=to_block
                )
            
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to process DAM approval request list data request."
            )

    async def bid_creation_post(self, post_data: Dict[str, Any], request: Request = None, current_user: Dict[str, Any] = None, audit_logger = None) -> Dict[str, Any]:
        """
        Create DAM bid data with authentication, authorization, and audit logging.
        
        Args:
            post_data (Dict[str, Any]): Bid creation data
            request (Request): FastAPI request object
            current_user (Dict[str, Any]): Current authenticated user context
            
        Returns:
            Dict[str, Any]: Bid creation result
        """
        try:
            # Extract request info for audit logging
            request_info = self._extract_request_info(request) if request else {}
            
            # Log the request
            logger.info(f"DAM bid creation request initiated by user: {current_user.get('user_id', 'unknown')}")
            
            # Audit log the system operation start
            if audit_logger:
                audit_logger.log_system_operation(
                    service="processing",
                    operation="dam_bid_creation_request",
                    message=f"DAM bid creation request initiated by user: {current_user.get('user_id', 'unknown')}",
                    correlation_id=current_user.get('correlation_id', 'unknown'),
                    user_id=current_user.get('user_id', 'unknown'),
                    bid_date=post_data.get("bidingDate", ""),
                    blocks_count=len(post_data.get("time_block_range", [])),
                    request_info=request_info
                )
            
            # Call service method
            result = await self.dam_bidding_service.bid_creation_post(post_data)
            
            # Log successful response
            logger.info(f"DAM bid creation request completed successfully for user: {current_user.get('user_id', 'unknown')}")
            
            # Audit log the user action success
            if audit_logger:
                audit_logger.log_user_action(
                    service="processing",
                    user_id=current_user.get('user_id', 'unknown'),
                    action="dam_bid_creation_response",
                    message="DAM bid creation completed successfully",
                    session_id=current_user.get('correlation_id', 'unknown'),
                    username=current_user.get('username', 'anonymous'),
                    inserted_count=result.get("inserted_count", 0),
                    success=True
                )
            
            return result
            
        except HTTPException:
            raise 
        except Exception as e:
            logger.error(f"Error occurred in DAMBiddingController.bid_creation_post: {str(e)}")
            
            # Audit log the error
            if audit_logger:
                audit_logger.log_error_event(
                    service="processing",
                    error_name="DAMBidCreationError",
                    error_message=str(e),
                    user_id=current_user.get('user_id', 'unknown') if current_user else 'unknown',
                    correlation_id=current_user.get('correlation_id', 'unknown') if current_user else 'unknown',
                    bid_date=post_data.get("bidingDate", "")
                )
            
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to process DAM bid creation request."
            )

    async def submit_for_approval_post_data(self, request_data: Dict[str, Any], request: Request = None, current_user: Dict[str, Any] = None, audit_logger = None) -> Dict[str, Any]:
        """
        Submit DAM bid data for approval with authentication, authorization, and audit logging.
        
        Args:
            request_data (Dict[str, Any]): Approval request data
            request (Request): FastAPI request object
            current_user (Dict[str, Any]): Current authenticated user context
            
        Returns:
            Dict[str, Any]: Submission result
        """
        try:
            # Extract request info for audit logging
            request_info = self._extract_request_info(request) if request else {}
            
            # Log the request
            logger.info(f"DAM bid approval submission request initiated by user: {current_user.get('user_id', 'unknown')}")
            
            # Audit log the system operation start
            if audit_logger:
                audit_logger.log_system_operation(
                    service="processing",
                    operation="dam_bid_approval_submission_request",
                    message=f"DAM bid approval submission request initiated by user: {current_user.get('user_id', 'unknown')}",
                    correlation_id=current_user.get('correlation_id', 'unknown'),
                    user_id=current_user.get('user_id', 'unknown'),
                    current_user_data=request_data.get("current_user", ""),
                    data_count=len(request_data.get("data", [])),
                    request_info=request_info
                )
            
            # Call service method
            result = await self.dam_bidding_service.submit_for_approval_post_data(request_data)
            
            # Log successful response
            logger.info(f"DAM bid approval submission request completed successfully for user: {current_user.get('user_id', 'unknown')}")
            
            # Audit log the user action success
            if audit_logger:
                audit_logger.log_user_action(
                    service="processing",
                    user_id=current_user.get('user_id', 'unknown'),
                    action="dam_bid_approval_submission_response",
                    message="DAM bid approval submission completed successfully",
                    session_id=current_user.get('correlation_id', 'unknown'),
                    username=current_user.get('username', 'anonymous'),
                    submitted_count=result.get("submitted_count", 0),
                    success=True
                )
            
            return result
            
        except HTTPException:
            raise 
        except ValueError as ve:
            logger.error(f"Validation error in DAMBiddingController.submit_for_approval_post_data: {str(ve)}")
            
            # Audit log the validation error
            if audit_logger:
                audit_logger.log_error_event(
                    service="processing",
                    error_name="DAMBidApprovalValidationError",
                    error_message=str(ve),
                    user_id=current_user.get('user_id', 'unknown') if current_user else 'unknown',
                    correlation_id=current_user.get('correlation_id', 'unknown') if current_user else 'unknown'
                )
            
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid request data: {str(ve)}"
            )
        except Exception as e:
            logger.error(f"Error occurred in DAMBiddingController.submit_for_approval_post_data: {str(e)}")
            
            # Audit log the error
            if audit_logger:
                audit_logger.log_error_event(
                    service="processing",
                    error_name="DAMBidApprovalSubmissionError",
                    error_message=str(e),
                    user_id=current_user.get('user_id', 'unknown') if current_user else 'unknown',
                    correlation_id=current_user.get('correlation_id', 'unknown') if current_user else 'unknown'
                )
            
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to process DAM bid approval submission request."
            )

    async def submit_for_pre_exchange_data(self, data: List[Dict[str, Any]], request: Request = None, current_user: Dict[str, Any] = None, audit_logger = None) -> Dict[str, Any]:
        """
        Submit DAM bid data for pre-exchange processing with authentication, authorization, and audit logging.
        
        Args:
            data (List[Dict[str, Any]]): Pre-exchange data
            request (Request): FastAPI request object
            current_user (Dict[str, Any]): Current authenticated user context
            
        Returns:
            Dict[str, Any]: Processing result
        """
        try:
            # Extract request info for audit logging
            request_info = self._extract_request_info(request) if request else {}
            
            # Log the request
            logger.info(f"DAM pre-exchange data submission request initiated by user: {current_user.get('user_id', 'unknown')}")
            
            # Audit log the system operation start
            if audit_logger:
                audit_logger.log_system_operation(
                    service="processing",
                    operation="dam_pre_exchange_submission_request",
                    message=f"DAM pre-exchange data submission request initiated by user: {current_user.get('user_id', 'unknown')}",
                    correlation_id=current_user.get('correlation_id', 'unknown'),
                    user_id=current_user.get('user_id', 'unknown'),
                    data_count=len(data),
                    request_info=request_info
                )
            
            # Call service method
            result = await self.dam_bidding_service.submit_for_pre_exchange_data(data)
            
            # Log successful response
            logger.info(f"DAM pre-exchange data submission request completed successfully for user: {current_user.get('user_id', 'unknown')}")
            
            # Audit log the user action success
            if audit_logger:
                audit_logger.log_user_action(
                    service="processing",
                    user_id=current_user.get('user_id', 'unknown'),
                    action="dam_pre_exchange_submission_response",
                    message="DAM pre-exchange data submission completed successfully",
                    session_id=current_user.get('correlation_id', 'unknown'),
                    username=current_user.get('username', 'anonymous'),
                    updated_count=result.get("updated_count", 0),
                    success=True
                )
            
            return result
            
        except HTTPException:
            raise 
        except Exception as e:
            logger.error(f"Error occurred in DAMBiddingController.submit_for_pre_exchange_data: {str(e)}")
            
            # Audit log the error
            if audit_logger:
                audit_logger.log_error_event(
                    service="processing",
                    error_name="DAMPreExchangeSubmissionError",
                    error_message=str(e),
                    user_id=current_user.get('user_id', 'unknown') if current_user else 'unknown',
                    correlation_id=current_user.get('correlation_id', 'unknown') if current_user else 'unknown'
                )
            
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to process DAM pre-exchange data submission request."
            )

    async def dam_scheduling_cal_table(self, schedule_date: Optional[str], request: Request = None, current_user: Dict[str, Any] = None, audit_logger = None) -> Dict[str, Any]:
        """
        Calculate DAM scheduling table data with authentication, authorization, and audit logging.
        
        Args:
            schedule_date (Optional[str]): The schedule date in 'YYYY-MM-DD' format
            request (Request): FastAPI request object
            current_user (Dict[str, Any]): Current authenticated user context
            
        Returns:
            Dict[str, Any]: DAM scheduling calculation data
        """
        try:
            # Extract request info for audit logging
            request_info = self._extract_request_info(request) if request else {}
            
            # Log the request
            logger.info(f"DAM scheduling table calculation request initiated by user: {current_user.get('user_id', 'unknown')}, "
                       f"schedule_date: {schedule_date}")
            
            # Audit log the system operation start
            if audit_logger:
                audit_logger.log_system_operation(
                    service="processing",
                    operation="dam_scheduling_table_calculation_request",
                    message=f"DAM scheduling table calculation request initiated by user: {current_user.get('user_id', 'unknown')}",
                    correlation_id=current_user.get('correlation_id', 'unknown'),
                    user_id=current_user.get('user_id', 'unknown'),
                    schedule_date=schedule_date,
                    request_info=request_info
                )
            
            # Call service method
            data = await self.dam_bidding_service.dam_scheduling_cal_table(schedule_date)
            
            # Log successful response
            logger.info(f"DAM scheduling table calculation request completed successfully for user: {current_user.get('user_id', 'unknown')}")
            
            # Audit log the user action success
            if audit_logger:
                audit_logger.log_user_action(
                    service="processing",
                    user_id=current_user.get('user_id', 'unknown'),
                    action="dam_scheduling_table_calculation_response",
                    message="DAM scheduling table calculation completed successfully",
                    session_id=current_user.get('correlation_id', 'unknown'),
                    username=current_user.get('username', 'anonymous'),
                    has_data=bool(data),
                    success=True
                )
            
            return data
            
        except HTTPException:
            raise 
        except Exception as e:
            logger.error(f"Error occurred in DAMBiddingController.dam_scheduling_cal_table: {str(e)}")
            
            # Audit log the error
            if audit_logger:
                audit_logger.log_error_event(
                    service="processing",
                    error_name="DAMSchedulingTableCalculationError",
                    error_message=str(e),
                    user_id=current_user.get('user_id', 'unknown') if current_user else 'unknown',
                    correlation_id=current_user.get('correlation_id', 'unknown') if current_user else 'unknown',
                    schedule_date=schedule_date
                )
            
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to process DAM scheduling table calculation request."
            )


# Global controller instance
dam_bidding_controller = DAMBiddingController()