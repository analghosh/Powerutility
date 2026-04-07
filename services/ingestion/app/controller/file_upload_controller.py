"""
File Upload Controller - Clean Architecture Implementation
Thin controller that delegates to application services
"""

import os
from datetime import datetime
from typing import Dict, Any, Optional
from fastapi import UploadFile, HTTPException, status

from libs.audit_queue.simple_logger import AuditLogger
from ..core.logger import get_ingestion_logger
from ..core.dependencies import get_audit_logger
from ..application.services import FileUploadApplicationService
from ..domain.entities import FileUploadRequest

logger = get_ingestion_logger("file_upload_controller")
audit_logger = get_audit_logger()


class FileUploadController:
    """
    Clean controller for file upload operations
    Delegates business logic to application services
    """
    
    def __init__(self, file_upload_service: FileUploadApplicationService):
        """Initialize with injected dependencies"""
        self._file_upload_service = file_upload_service
    
    async def upload_single_file(
        self,
        file: UploadFile,
        data_type: str,
        provider_type: str,
        user_id: str = "system",
        session_id: str = "unknown"
    ) -> Dict[str, Any]:
        """
        Upload file using clean architecture pattern
        
        Args:
            file: Uploaded file
            data_type: Type of data ('demand' or 'supply')
            provider_type: Specific provider type
            user_id: User ID for audit logging
            session_id: Session ID for audit logging
            
        Returns:
            Dict[str, Any]: Upload result with tracking information
            
        Raises:
            HTTPException: If validation fails or processing error occurs
        """
        try:
            logger.info("Upload request: file=%s, type=%s, provider=%s, user=%s", 
                       file.filename, data_type, provider_type, user_id)
            
            # Log upload attempt
            audit_logger.log_user_action(
                service="ingestion",
                user_id=user_id,
                action="file_upload_started",
                message=f"File upload started: {file.filename}",
                session_id=session_id,
                file_name=file.filename,
                data_type=data_type,
                provider_type=provider_type,
                file_size=file.size
            )
            
            # Read file content
            file_content = await file.read()
            
            # Generate custom filename: provider_type_timestamp with original extension
            original_extension = os.path.splitext(file.filename)[1] if file.filename else ".csv"
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            custom_filename = f"{provider_type}_{timestamp}{original_extension}"
            
            # Create domain request object
            upload_request = FileUploadRequest(
                file_content=file_content,
                file_name=custom_filename,
                file_size=len(file_content),
                data_type=data_type,
                provider_type=provider_type
            )
            
            # Delegate to application service
            result = await self._file_upload_service.upload_file(upload_request, user_id)
            
            logger.info("Upload successful: upload_id=%s", result.upload_id)
            
            # Log successful upload
            audit_logger.log_user_action(
                service="ingestion",
                user_id=user_id,
                action="file_upload_completed",
                message=f"File upload completed successfully: {file.filename}",
                session_id=session_id,
                success=True,
                upload_id=result.upload_id,
                file_name=file.filename,
                file_size=result.file_size,
                data_type=data_type,
                provider_type=provider_type,
                processing_time_ms=result.processing_time_ms
            )
            
            return result.to_dict()
            
        except ValueError as e:
            logger.warning("Upload validation failed: %s", str(e))
            
            # Log validation failure
            audit_logger.log_user_action(
                service="ingestion",
                user_id=user_id,
                action="file_upload_validation_failed",
                message=f"File upload validation failed: {str(e)}",
                session_id=session_id,
                success=False,
                file_name=file.filename,
                data_type=data_type,
                provider_type=provider_type,
                error_message=str(e)
            )
            
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(e)
            ) from e
        except RuntimeError as e:
            logger.error("Upload processing failed: %s", str(e))
            
            # Log processing failure
            audit_logger.log_user_action(
                service="ingestion",
                user_id=user_id,
                action="file_upload_processing_failed",
                message=f"File upload processing failed: {str(e)}",
                session_id=session_id,
                success=False,
                file_name=file.filename,
                data_type=data_type,
                provider_type=provider_type,
                error_message=str(e)
            )
            
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Internal server error occurred during file upload"
            ) from e
        except Exception as e:
            logger.error("Unexpected upload error: %s", str(e), exc_info=True)
            
            # Log unexpected error
            audit_logger.log_user_action(
                service="ingestion",
                user_id=user_id,
                action="file_upload_error",
                message=f"Unexpected file upload error: {str(e)}",
                session_id=session_id,
                success=False,
                file_name=file.filename,
                data_type=data_type,
                provider_type=provider_type,
                exception_type=type(e).__name__,
                error_message=str(e)
            )
            
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Internal server error occurred during file upload"
            ) from e
    
    async def get_upload_status(
        self, 
        upload_id: str,
        user_id: str = "system",
        session_id: str = "unknown"
    ) -> Dict[str, Any]:
        """
        Get upload processing status
        
        Args:
            upload_id: Upload identifier
            user_id: User ID for audit logging
            session_id: Session ID for audit logging
            
        Returns:
            Dict[str, Any]: Upload status information
        """
        try:
            logger.info(f"Controller: Getting status for upload_id: {upload_id}")
            # Log status check attempt
            audit_logger.log_user_action(
                service="ingestion",
                user_id=user_id,
                action="upload_status_check",
                message=f"Upload status check requested: {upload_id}",
                session_id=session_id,
                upload_id=upload_id
            )
            
            # Delegate to application service
            result = await self._file_upload_service.get_upload_status(upload_id)
            
            # Log successful status check
            audit_logger.log_user_action(
                service="ingestion",
                user_id=user_id,
                action="upload_status_retrieved",
                message=f"Upload status retrieved successfully: {upload_id}",
                session_id=session_id,
                success=True,
                upload_id=upload_id,
                upload_status=result.get("status"),
                file_name=result.get("file_name")
            )
            
            return result
            
        except ValueError as e:
            logger.warning("Upload status request failed: %s", str(e))
            
            # Log status check failure
            audit_logger.log_user_action(
                service="ingestion",
                user_id=user_id,
                action="upload_status_not_found",
                message=f"Upload status check failed - not found: {upload_id}",
                session_id=session_id,
                success=False,
                upload_id=upload_id,
                error_message=str(e)
            )
            
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(e)
            ) from e
        except Exception as e:
            logger.error("Error getting upload status for %s: %s", upload_id, e, exc_info=True)
            
            # Log status check error
            audit_logger.log_user_action(
                service="ingestion",
                user_id=user_id,
                action="upload_status_error",
                message=f"Upload status check error: {str(e)}",
                session_id=session_id,
                success=False,
                upload_id=upload_id,
                exception_type=type(e).__name__,
                error_message=str(e)
            )
            
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Internal server error occurred while getting upload status"
            ) from e

    async def get_raw_processing_status(
        self,
        upload_id: str,
        user_id: str = "system",
        session_id: str = "unknown"
    ) -> Dict[str, Any]:
        """
        Get detailed raw data processing status
        
        Args:
            upload_id: Upload identifier
            user_id: User ID for audit logging
            session_id: Session ID for audit logging
            
        Returns:
            Dict[str, Any]: Processing status information
        """
        try:
            logger.info(f"Controller: Getting raw status for upload_id: {upload_id}")
            
            # Delegate to application service
            result = await self._file_upload_service.get_raw_processing_status(upload_id)
            
            if not result:
                raise ValueError(f"Raw processing status not found for upload_id: {upload_id}")
            
            return result
            
        except ValueError as e:
            logger.warning("Raw status request failed: %s", str(e))
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(e)
            ) from e
        except Exception as e:
            logger.error("Error getting raw status for %s: %s", upload_id, e, exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Internal server error occurred while getting raw status"
            ) from e
    
    async def get_uploads_paginated(
        self,
        skip: int = 0,
        limit: int = 20,
        status_filter: Optional[str] = None,
        sort_by: str = "created_at",
        sort_order: int = -1,
        user_id: str = "system",
        session_id: str = "unknown"
    ) -> Dict[str, Any]:
        """
        Get paginated uploads
        
        Args:
            skip: Number of records to skip
            limit: Maximum number of records to return
            status_filter: Optional status filter
            sort_by: Field to sort by
            sort_order: Sort order (1 for ascending, -1 for descending)
            user_id: User ID for audit logging
            session_id: Session ID for audit logging
            
        Returns:
            Dict containing paginated uploads and metadata
        """
        try:
            # Log pagination request
            audit_logger.log_user_action(
                service="ingestion",
                user_id=user_id,
                action="uploads_paginated_request",
                message=f"Paginated uploads request: skip={skip}, limit={limit}, status={status_filter}",
                session_id=session_id,
                skip=skip,
                limit=limit,
                status_filter=status_filter
            )
            
            # Delegate to application service
            result = await self._file_upload_service.get_uploads_paginated(
                skip=skip,
                limit=limit,
                status_filter=status_filter,
                sort_by=sort_by,
                sort_order=sort_order
            )
            
            # Log successful pagination request
            audit_logger.log_user_action(
                service="ingestion",
                user_id=user_id,
                action="uploads_paginated_success",
                message=f"Paginated uploads retrieved successfully: {len(result['uploads'])} records",
                session_id=session_id,
                success=True,
                total_count=result["pagination"]["total_count"],
                current_page=result["pagination"]["current_page"],
                page_size=result["pagination"]["page_size"]
            )
            
            return result
            
        except Exception as e:
            logger.error("Error getting paginated uploads: %s", str(e), exc_info=True)
            
            # Log pagination error
            audit_logger.log_user_action(
                service="ingestion",
                user_id=user_id,
                action="uploads_paginated_error",
                message=f"Paginated uploads error: {str(e)}",
                session_id=session_id,
                success=False,
                exception_type=type(e).__name__,
                error_message=str(e)
            )
            
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Internal server error occurred while getting paginated uploads"
            ) from e
    
    async def get_csv_format(
        self,
        data_type: str,
        provider_code: str,
        user_id: str = "system",
        session_id: str = "unknown"
    ) -> Dict[str, Any]:
        """
        Get CSV format for specific data type and provider code
        
        Args:
            data_type: Type of data ('demand', 'supply', etc.)
            provider_code: Provider code
            user_id: User ID for audit logging
            session_id: Session ID for audit logging
            
        Returns:
            Dict[str, Any]: CSV format information
            
        Raises:
            HTTPException: If provider not found or validation fails
        """
        try:
            # Log CSV format request attempt
            audit_logger.log_user_action(
                service="ingestion",
                user_id=user_id,
                action="csv_format_request",
                message=f"CSV format requested: {provider_code} for {data_type}",
                session_id=session_id,
                data_type=data_type,
                provider_code=provider_code
            )
            
            # Delegate to application service
            result = await self._file_upload_service.get_csv_format(
                data_type=data_type,
                provider_code=provider_code
            )
            
            # Log successful CSV format request
            audit_logger.log_user_action(
                service="ingestion",
                user_id=user_id,
                action="csv_format_retrieved",
                message=f"CSV format retrieved successfully: {provider_code} for {data_type}",
                session_id=session_id,
                success=True,
                data_type=data_type,
                provider_code=provider_code,
                fields_count=len(result.get("csv_format", {}).get("field_specifications", []))
            )
            
            return result
            
        except ValueError as e:
            logger.warning("CSV format request failed: %s", str(e))
            
            # Log CSV format request failure
            audit_logger.log_user_action(
                service="ingestion",
                user_id=user_id,
                action="csv_format_not_found",
                message=f"CSV format request failed - not found: {str(e)}",
                session_id=session_id,
                success=False,
                data_type=data_type,
                provider_code=provider_code,
                error_message=str(e)
            )
            
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(e)
            ) from e
        except Exception as e:
            logger.error("Error getting CSV format for %s/%s: %s", data_type, provider_code, e, exc_info=True)
            
            # Log CSV format request error
            audit_logger.log_user_action(
                service="ingestion",
                user_id=user_id,
                action="csv_format_error",
                message=f"CSV format request error: {str(e)}",
                session_id=session_id,
                success=False,
                data_type=data_type,
                provider_code=provider_code,
                exception_type=type(e).__name__,
                error_message=str(e)
            )
            
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Internal server error occurred while getting CSV format"
            ) from e

    async def download_file(
        self,
        upload_id: str,
        user_id: str = "system",
        session_id: str = "unknown"
    ) -> Dict[str, Any]:
        """
        Download uploaded file
        
        Args:
            upload_id: Upload identifier
            user_id: User ID for audit logging
            session_id: Session ID for audit logging
            
        Returns:
            Dict[str, Any]: File path and metadata
            
        Raises:
            HTTPException: If file not found or error occurs
        """
        try:
            # Log download request attempt
            audit_logger.log_user_action(
                service="ingestion",
                user_id=user_id,
                action="file_download_request",
                message=f"File download requested: {upload_id}",
                session_id=session_id,
                upload_id=upload_id
            )
            
            # Delegate to application service
            result = await self._file_upload_service.download_file(upload_id)
            
            # Log successful download request
            audit_logger.log_user_action(
                service="ingestion",
                user_id=user_id,
                action="file_download_success",
                message=f"File download successful: {result.get('file_name')}",
                session_id=session_id,
                success=True,
                upload_id=upload_id,
                file_name=result.get("file_name"),
                data_type=result.get("data_type")
            )
            
            return result
            
        except ValueError as e:
            logger.warning("File download failed: %s", str(e))
            
            # Log download failure
            audit_logger.log_user_action(
                service="ingestion",
                user_id=user_id,
                action="file_download_not_found",
                message=f"File download failed - not found: {str(e)}",
                session_id=session_id,
                success=False,
                upload_id=upload_id,
                error_message=str(e)
            )
            
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(e)
            ) from e
        except Exception as e:
            logger.error("Error downloading file for %s: %s", upload_id, e, exc_info=True)
            
            # Log download error
            audit_logger.log_user_action(
                service="ingestion",
                user_id=user_id,
                action="file_download_error",
                message=f"File download error: {str(e)}",
                session_id=session_id,
                success=False,
                upload_id=upload_id,
                exception_type=type(e).__name__,
                error_message=str(e)
            )
            
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Internal server error occurred while downloading file"
            ) from e


def create_file_upload_controller(file_upload_service: FileUploadApplicationService) -> FileUploadController:
    """Factory function to create controller with dependencies"""
    return FileUploadController(file_upload_service)