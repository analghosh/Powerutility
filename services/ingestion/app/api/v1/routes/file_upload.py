"""
File Upload Routes
Handles CSV file upload endpoints for asynchronous file processing
"""

from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Depends, Security, Query, BackgroundTasks
from fastapi.responses import JSONResponse, Response, FileResponse
from fastapi.security import HTTPBearer
from typing import Dict, Any, Optional

from libs.audit_queue.simple_logger import AuditLogger
from ....core.logger import get_ingestion_logger
from ....core.dependencies import get_file_upload_service, get_audit_logger, get_upload_raw_data_processor
from ....application.services import FileUploadApplicationService
from ....controller.file_upload_controller import create_file_upload_controller
from ....middleware.page_access import require_page_access
from ....processors.upload_raw_data_processor import UploadRawDataProcessor

logger = get_ingestion_logger("file_upload_routes")
audit_logger = get_audit_logger()


# Data types and provider types are now validated against the database
# No need for hardcoded enums since they are dynamically configured

# Security scheme
security = HTTPBearer()

# Create router
router = APIRouter(prefix="/file-upload", tags=["File Upload"])


@router.post(
    "/upload",
    summary="Upload CSV file for processing",
    description="""
    Upload a CSV file for asynchronous data processing with comprehensive validation.
    
    ## Validation Process:
    1. **Provider Validation**: Verifies the provider exists in the database and is enabled
    2. **Data Type Validation**: Ensures the data type is supported by the provider
    3. **Ingestion Type Validation**: Confirms the provider supports UPLOAD ingestion
    4. **File Validation**: Checks file type (CSV), size (max 100MB), and content
    
    ## Provider Requirements:
    - Provider must exist in the database with the specified code and data type
    - Provider must be enabled (not disabled)
    - Provider must support UPLOAD ingestion type
    - Data type must be valid (demand, supply, etc.)
    
    ## File Requirements:
    - File must be a CSV format
    - File size must not exceed 100MB
    - File must not be empty
    - File must contain valid CSV data
    
    Requires Bearer token authentication and 'Data Ingestion' page access.
    """,
    responses={
        201: {
            "description": "File uploaded successfully and queued for processing",
            "content": {
                "application/json": {
                    "example": {
                        "upload_id": "123e4567-e89b-12d3-a456-426614174000",
                        "tracking_id": "507f1f77bcf86cd799439011",
                        "file_name": "demand_data.csv",
                        "file_size": 1024,
                        "status": "pending",
                        "status_message": "File uploaded successfully, processing in background",
                        "processing_time_ms": 45.2,
                        "upload_timestamp": "2024-01-15T10:30:00Z",
                        "success": True
                    }
                }
            }
        },
        400: {
            "description": "Validation error - provider not found, disabled, or file validation failed",
            "content": {
                "application/json": {
                    "examples": {
                        "provider_not_found": {
                            "summary": "Provider not found",
                            "value": {
                                "detail": "Provider validation failed: No provider found with code 'invalid_provider' for data type 'demand'"
                            }
                        },
                        "provider_disabled": {
                            "summary": "Provider disabled",
                            "value": {
                                "detail": "Provider validation failed: Provider 'test_data' is disabled for data type 'demand'"
                            }
                        },
                        "invalid_ingestion_type": {
                            "summary": "Provider doesn't support upload",
                            "value": {
                                "detail": "Provider validation failed: Provider 'api_data' does not support file upload. Ingestion type: API"
                            }
                        },
                        "file_validation_failed": {
                            "summary": "File validation failed",
                            "value": {
                                "detail": "File validation failed: ['File must be a CSV file', 'File size exceeds limit of 100MB']"
                            }
                        }
                    }
                }
            }
        },
        401: {"description": "Authentication required - Bearer token missing or invalid"},
        403: {"description": "Access denied - insufficient permissions for Data Ingestion"},
        500: {"description": "Internal server error during file upload or processing"}
    },
    dependencies=[Security(security)]
)
async def upload_csv_data(
    file: UploadFile = File(..., description="CSV file to upload (max 100MB)"),
    data_type: str = Form(..., description="Type of data to upload (e.g., 'demand', 'supply') - must be supported by provider"),
    provider_type: str = Form(..., description="Provider code for data source (must exist in database and be enabled)"),
    _current_user: Dict[str, Any] = Depends(require_page_access("Data Ingestion")),
    file_upload_service: FileUploadApplicationService = Depends(get_file_upload_service),
    background_tasks: BackgroundTasks = None,
    processor: UploadRawDataProcessor = Depends(get_upload_raw_data_processor)
):
    """
    Upload CSV file for asynchronous processing with comprehensive validation
    
    This endpoint performs multiple validation checks before accepting the file:
    
    ## Provider Validation:
    - Verifies the provider exists in the database with the specified code and data type
    - Ensures the provider is enabled (not disabled)
    - Confirms the provider supports UPLOAD ingestion type
    
    ## File Validation:
    - Checks file is CSV format
    - Validates file size (max 100MB)
    - Ensures file is not empty
    - Verifies file contains valid CSV data
    
    ## Processing:
    - File is stored temporarily and queued for background processing
    - Returns immediately with upload tracking information
    - Processing status can be checked via the status endpoint
    
    Args:
        file: CSV file to upload (must be valid CSV format, max 100MB)
        data_type: Type of data (e.g., 'demand', 'supply') - must be supported by provider
        provider_type: Provider code (e.g., 'TEST_DATA', 'SCADA_DEMAND') - must exist in database and be enabled
        _current_user: Current authenticated user (injected by dependency)
        
    Returns:
        JSONResponse: Upload result with tracking information including:
            - upload_id: Unique identifier for this upload
            - tracking_id: Database tracking record ID
            - file_name: Original filename
            - file_size: File size in bytes
            - status: Current processing status (pending)
            - status_message: Human-readable status message
            - processing_time_ms: Time taken for upload validation
            - upload_timestamp: ISO timestamp of upload
            - success: Boolean indicating success
            
    Raises:
        HTTPException: 
            - 400: Provider validation failed, file validation failed, or invalid parameters
            - 401: Authentication required (missing or invalid Bearer token)
            - 403: Access denied (insufficient permissions for Data Ingestion)
            - 500: Internal server error during upload or processing
    """
    user_id = _current_user.get("user_id", "unknown")
    session_id = _current_user.get("session_id", "unknown")
    
    try:
        # Generate request ID for tracking
        import uuid
        request_id = str(uuid.uuid4())[:8]  # Short request ID for logging
        
        logger.info("Upload request [%s]: file=%s, type=%s, provider=%s, user=%s", 
                   request_id, file.filename, data_type, provider_type, user_id)
        
        # Create controller with injected service
        controller = create_file_upload_controller(file_upload_service)
        
        # Delegate to controller
        result = await controller.upload_single_file(
            file=file,
            data_type=data_type,
            provider_type=provider_type,
            user_id=user_id,
            session_id=session_id
        )
        
        logger.info("Upload request [%s] completed successfully", request_id)
        
        # Log successful file upload activity
        audit_logger.log_user_action(
            service="ingestion",
            user_id=user_id,
            action="upload",
            message=f"File upload successful of {data_type} {provider_type}: {file.filename}",
            session_id=session_id,
            upload_id=result.get("upload_id"),
            file_name=file.filename,
            file_size=result.get("file_size", 0),
            data_type=data_type,
            provider_type=provider_type,
            request_id=request_id,
            endpoint="/file-upload/upload"
        ) # type: ignore
        
        # Trigger immediate background processing
        if background_tasks and result.get("success"):
            upload_id = result.get("upload_id")
            if upload_id:
                logger.info("Triggering immediate background processing for upload_id: %s", upload_id)
                background_tasks.add_task(
                    processor.process_data,
                    upload_id=upload_id,
                    data_type=data_type,
                    provider_type=provider_type
                )
        
        return JSONResponse(
            status_code=201,
            content=result
        )
        
    except HTTPException as http_exc:
        # Log failed file upload activity for HTTP exceptions
        audit_logger.log_user_action(
            service="ingestion",
            user_id=user_id,
            action="file_upload_failed",
            message=f"File upload failed: {http_exc.detail}",
            session_id=session_id,
            success=False,
            file_name=file.filename,
            data_type=data_type,
            provider_type=provider_type,
            status_code=http_exc.status_code,
            endpoint="/file-upload/upload"
        ) # type: ignore
        # Re-raise HTTP exceptions
        raise
    except Exception as e:
        logger.error("Unexpected error in upload_csv_data endpoint: %s", e)
        
        # Log failed file upload activity for unexpected errors
        audit_logger.log_user_action(
            service="ingestion",
            user_id=user_id,
            action="file_upload_error",
            message=f"File upload error: {str(e)}",
            session_id=session_id,
            success=False,
            file_name=file.filename,
            data_type=data_type,
            provider_type=provider_type,
            exception_type=type(e).__name__,
            endpoint="/file-upload/upload"
        ) # type: ignore
        
        raise HTTPException(
            status_code=500,
            detail="Internal server error occurred during file upload"
        ) from e


@router.get(
    "/upload/{upload_id}/status",
    summary="Get upload processing status",
    description="""
    Get the current processing status of an uploaded file.
    
    ## Status Values:
    - **pending**: File uploaded successfully, queued for processing
    - **processing**: File is currently being processed
    - **completed**: File processed successfully
    - **failed**: File processing failed with error details
    
    ## Response Information:
    - Upload metadata (filename, size, timestamps)
    - Current processing status and message
    - Completion or failure timestamps
    - Error details if processing failed
    
    Requires Bearer token authentication and 'Data Ingestion' page access.
    """,
    responses={
        200: {
            "description": "Upload status retrieved successfully",
            "content": {
                "application/json": {
                    "examples": {
                        "pending": {
                            "summary": "File pending processing",
                            "value": {
                                "upload_id": "123e4567-e89b-12d3-a456-426614174000",
                                "file_name": "demand_data.csv",
                                "status": "pending",
                                "status_message": "File uploaded successfully, processing queued",
                                "file_size": 1024,
                                "created_at": "2024-01-15T10:30:00Z",
                                "updated_at": "2024-01-15T10:30:00Z",
                                "completed_at": None,
                                "failed_at": None
                            }
                        },
                        "processing": {
                            "summary": "File being processed",
                            "value": {
                                "upload_id": "123e4567-e89b-12d3-a456-426614174000",
                                "file_name": "demand_data.csv",
                                "status": "processing",
                                "status_message": "Processing file data and validating records",
                                "file_size": 1024,
                                "created_at": "2024-01-15T10:30:00Z",
                                "updated_at": "2024-01-15T10:32:15Z",
                                "completed_at": None,
                                "failed_at": None
                            }
                        },
                        "completed": {
                            "summary": "File processed successfully",
                            "value": {
                                "upload_id": "123e4567-e89b-12d3-a456-426614174000",
                                "file_name": "demand_data.csv",
                                "status": "completed",
                                "status_message": "File processed successfully, 150 records ingested",
                                "file_size": 1024,
                                "created_at": "2024-01-15T10:30:00Z",
                                "updated_at": "2024-01-15T10:35:42Z",
                                "completed_at": "2024-01-15T10:35:42Z",
                                "failed_at": None
                            }
                        },
                        "failed": {
                            "summary": "File processing failed",
                            "value": {
                                "upload_id": "123e4567-e89b-12d3-a456-426614174000",
                                "file_name": "demand_data.csv",
                                "status": "failed",
                                "status_message": "Validation failed: Invalid time block values found in rows 5, 12, 18",
                                "file_size": 1024,
                                "created_at": "2024-01-15T10:30:00Z",
                                "updated_at": "2024-01-15T10:33:20Z",
                                "completed_at": None,
                                "failed_at": "2024-01-15T10:33:20Z"
                            }
                        }
                    }
                }
            }
        },
        401: {"description": "Authentication required - Bearer token missing or invalid"},
        403: {"description": "Access denied - insufficient permissions for Data Ingestion"},
        404: {"description": "Upload not found - invalid upload_id"},
        500: {"description": "Internal server error while retrieving upload status"}
    },
    dependencies=[Security(security)]
)
async def get_upload_status(
    upload_id: str,
    _current_user: Dict[str, Any] = Depends(require_page_access("Data Ingestion")),
    file_upload_service: FileUploadApplicationService = Depends(get_file_upload_service)
):
    """
    Get upload processing status
    
    Args:
        upload_id: Upload identifier
        
    Returns:
        JSONResponse: Upload status information
        
    Raises:
        HTTPException: If upload not found or error occurs
    """
    user_id = _current_user.get("user_id", "unknown")
    session_id = _current_user.get("session_id", "unknown")
    
    try:
        logger.info(f"Received request to get status for upload_id: {upload_id} from user: {user_id}")
        
        # Create controller with injected service
        controller = create_file_upload_controller(file_upload_service)
        
        # Delegate to controller
        result = await controller.get_upload_status(
            upload_id=upload_id,
            user_id=user_id,
            session_id=session_id # type: ignore
        )
        
        # Log successful upload status check activity
        audit_logger.log_user_action(
            service="ingestion",
            user_id=user_id,
            action="upload_status_check",
            message=f"Upload status checked: {upload_id}",
            session_id=session_id,
            upload_id=upload_id,
            upload_status=result.get("status"),
            file_name=result.get("file_name"),
            endpoint=f"/file-upload/upload/{upload_id}/status"
        ) # type: ignore
        json_result = JSONResponse(status_code=200, content=result)
        logger.info("Upload status retrieved successfully: %s", json_result)
        return json_result
        
    except HTTPException as http_exc:
        # Log failed upload status check for HTTP exceptions
        audit_logger.log_user_action(
            service="ingestion", # type: ignore
            user_id=user_id,
            action="upload_status_check_failed",
            message=f"Upload status check failed: {http_exc.detail}",
            session_id=session_id,
            success=False,
            upload_id=upload_id,
            status_code=http_exc.status_code,
            endpoint=f"/file-upload/upload/{upload_id}/status"
        ) # type: ignore
        raise
    except Exception as e:
        logger.error("Unexpected error in get_upload_status endpoint: %s", e)
        
        # Log failed upload status check for unexpected errors
        audit_logger.log_user_action(
            service="ingestion",
            user_id=user_id,
            action="upload_status_check_error",
            message=f"Upload status check error: {str(e)}",
            session_id=session_id,
            success=False,
            upload_id=upload_id,
            exception_type=type(e).__name__,
            endpoint=f"/file-upload/upload/{upload_id}/status"
        ) # type: ignore
        
        raise HTTPException(
            status_code=500,
            detail="Internal server error occurred while getting upload status"
        ) from e


@router.get(
    "/upload/{upload_id}/raw-status",
    summary="Get detailed raw data processing status",
    description="""
    Get the detailed processing status of an uploaded file from the raw data processing status collection.
    
    ## Response Information:
    - raw_data_id: Reference to the raw data document
    - provider_id: Reference to the provider
    - status: Current processing status (pending, processing, completed, failed)
    - processing_started_at: When processing started
    - processing_completed_at: When processing completed
    - error_message: Error details if processing failed
    - retry_count: Number of retry attempts
    - processing_metadata: Additional processing info (e.g., total_records)
    
    Requires Bearer token authentication and 'Data Ingestion' page access.
    """,
    responses={
        200: {
            "description": "Detailed processing status retrieved successfully",
            "content": {
                "application/json": {
                    "example": {
                        "raw_data_id": "6945c9000775427023b48e45",
                        "provider_id": "6945c8bb0775427023b48e1f",
                        "status": "completed",
                        "processing_started_at": "2025-12-19T21:54:00.253Z",
                        "processing_completed_at": "2025-12-19T21:55:55.525Z",
                        "error_message": None,
                        "retry_count": 0,
                        "processing_metadata": {
                            "total_records": 192
                        }
                    }
                }
            }
        },
        401: {"description": "Authentication required"},
        403: {"description": "Access denied"},
        404: {"description": "Status not found for the given upload_id"},
        500: {"description": "Internal server error"}
    },
    dependencies=[Security(security)]
)
async def get_raw_processing_status(
    upload_id: str,
    _current_user: Dict[str, Any] = Depends(require_page_access("Data Ingestion")),
    file_upload_service: FileUploadApplicationService = Depends(get_file_upload_service)
):
    """
    Get detailed raw data processing status
    
    Args:
        upload_id: Upload identifier
        
    Returns:
        JSONResponse: Detailed processing status information
    """
    user_id = _current_user.get("user_id", "unknown")
    session_id = _current_user.get("session_id", "unknown")
    
    try:
        logger.info(f"Received request for raw status for upload_id: {upload_id}")
        
        # Create controller
        controller = create_file_upload_controller(file_upload_service)
        
        # Delegate to controller
        result = await controller.get_raw_processing_status(
            upload_id=upload_id,
            user_id=user_id,
            session_id=session_id
        )
        
        return JSONResponse(status_code=200, content=result)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Unexpected error in get_raw_processing_status endpoint: %s", e)
        raise HTTPException(
            status_code=500,
            detail="Internal server error occurred while getting raw processing status"
        ) from e


@router.get(
    "/uploads",
    summary="Get paginated file upload status",
    description="""
    Get paginated list of all file uploads with their status information.
    
    ## Query Parameters:
    - **page**: Page number (1-based, default: 1)
    - **page_size**: Number of records per page (default: 20, max: 100)
    - **status**: Filter by upload status (pending, processing, completed, failed)
    - **sort_by**: Field to sort by (created_at, updated_at, file_name, status)
    - **sort_order**: Sort order (asc, desc)
    
    ## Response Information:
    - Upload details: upload_id, file_name, status, error, createdAt, completedAt, failedAt
    - Pagination metadata: total_count, total_pages, current_page, has_next, has_prev
    
    Requires Bearer token authentication and 'Data Ingestion' page access.
    """,
    responses={
        200: {
            "description": "Paginated uploads retrieved successfully",
            "content": {
                "application/json": {
                    "example": {
                        "uploads": [
                            {
                                "upload_id": "123e4567-e89b-12d3-a456-426614174000",
                                "file_name": "demand_data.csv",
                                "status": "completed",
                                "status_message": "File processed successfully, 150 records ingested",
                                "file_size": 1024,
                                "data_type": "demand",
                                "provider_type": "TEST_DATA",
                                "created_at": "2024-01-15T10:30:00Z",
                                "updated_at": "2024-01-15T10:35:42Z",
                                "completed_at": "2024-01-15T10:35:42Z",
                                "failed_at": None,
                                "error": None
                            }
                        ],
                        "pagination": {
                            "total_count": 150,
                            "total_pages": 8,
                            "current_page": 1,
                            "page_size": 20,
                            "has_next": True,
                            "has_prev": False,
                            "skip": 0,
                            "limit": 20
                        }
                    }
                }
            }
        },
        400: {
            "description": "Invalid query parameters",
            "content": {
                "application/json": {
                    "example": {
                        "detail": "Invalid page size. Must be between 1 and 100"
                    }
                }
            }
        },
        401: {"description": "Authentication required - Bearer token missing or invalid"},
        403: {"description": "Access denied - insufficient permissions for Data Ingestion"},
        500: {"description": "Internal server error while retrieving uploads"}
    },
    dependencies=[Security(security)]
)
async def get_uploads_paginated(
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    page_size: int = Query(20, ge=1, le=100, description="Number of records per page (max 100)"),
    status: Optional[str] = Query(None, description="Filter by upload status (pending, processing, completed, failed)"),
    sort_by: str = Query("created_at", description="Field to sort by (created_at, updated_at, file_name, status)"),
    sort_order: str = Query("desc", description="Sort order (asc, desc)"),
    _current_user: Dict[str, Any] = Depends(require_page_access("Data Ingestion")),
    file_upload_service: FileUploadApplicationService = Depends(get_file_upload_service)
):
    """
    Get paginated list of file uploads with status information
    
    Args:
        page: Page number (1-based)
        page_size: Number of records per page (max 100)
        status: Optional status filter
        sort_by: Field to sort by
        sort_order: Sort order (asc or desc)
        _current_user: Current authenticated user (injected by dependency)
        
    Returns:
        JSONResponse: Paginated uploads with metadata
        
    Raises:
        HTTPException: If invalid parameters or error occurs
    """
    user_id = _current_user.get("user_id", "unknown")
    session_id = _current_user.get("session_id", "unknown")
    
    try:
        # Validate sort order
        if sort_order.lower() not in ["asc", "desc"]:
            raise HTTPException(
                status_code=400,
                detail="Invalid sort_order. Must be 'asc' or 'desc'"
            )
        
        # Validate sort_by field
        allowed_sort_fields = ["created_at", "updated_at", "file_name", "status"]
        if sort_by not in allowed_sort_fields:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid sort_by field. Must be one of: {', '.join(allowed_sort_fields)}"
            )
        
        # Validate status filter
        if status and status not in ["pending", "processing", "completed", "failed"]:
            raise HTTPException(
                status_code=400,
                detail="Invalid status filter. Must be one of: pending, processing, completed, failed"
            )
        
        # Convert page to skip
        skip = (page - 1) * page_size
        
        # Convert sort order to integer
        sort_order_int = -1 if sort_order.lower() == "desc" else 1
        
        # Generate request ID for tracking
        import uuid
        request_id = str(uuid.uuid4())[:8]
        
        logger.info("Paginated uploads request [%s]: page=%d, page_size=%d, status=%s, sort_by=%s, sort_order=%s, user=%s", 
                   request_id, page, page_size, status, sort_by, sort_order, user_id)
        
        # Create controller with injected service
        controller = create_file_upload_controller(file_upload_service)
        
        # Delegate to controller
        result = await controller.get_uploads_paginated(
            skip=skip,
            limit=page_size,
            status_filter=status,
            sort_by=sort_by,
            sort_order=sort_order_int,
            user_id=user_id, # type: ignore
            session_id=session_id # type: ignore
        )
        
        logger.info("Paginated uploads request [%s] completed successfully", request_id)
        
        # Log successful pagination request activity
        audit_logger.log_user_action(
            service="ingestion",
            user_id=user_id,
            action="uploads_paginated",
            message=f"Paginated uploads retrieved: page {page}, {len(result['uploads'])} records",
            session_id=session_id,
            page=page,
            page_size=page_size,
            status_filter=status,
            total_count=result["pagination"]["total_count"],
            request_id=request_id,
            endpoint="/file-upload/uploads" # type: ignore
        )
        
        return JSONResponse(
            status_code=200,
            content=result
        )
        
    except HTTPException as http_exc:
        # Log failed pagination request for HTTP exceptions
        audit_logger.log_user_action(
            service="ingestion",
            user_id=user_id,
            action="uploads_paginated_failed",
            message=f"Paginated uploads request failed: {http_exc.detail}",
            session_id=session_id,
            success=False,
            page=page,
            page_size=page_size,
            status_filter=status,
            status_code=http_exc.status_code,
            endpoint="/file-upload/uploads" # type: ignore
        ) # type: ignore
        # Re-raise HTTP exceptions
        raise
    except Exception as e:
        logger.error("Unexpected error in get_uploads_paginated endpoint: %s", e)
        
        # Log failed pagination request for unexpected errors
        audit_logger.log_user_action(
            service="ingestion",
            user_id=user_id,
            action="uploads_paginated_error",
            message=f"Paginated uploads request error: {str(e)}",
            session_id=session_id,
            success=False,
            page=page,
            page_size=page_size,
            status_filter=status,
            exception_type=type(e).__name__,
            endpoint="/file-upload/uploads" # type: ignore
        ) # type: ignore
        
        raise HTTPException(
            status_code=500,
            detail="Internal server error occurred while retrieving paginated uploads"
        ) from e


@router.get(
    "/csv-format/{data_type}/{provider_code}",
    summary="Download CSV template for data upload",
    description="""
    Download a CSV template file for uploading data to a specific provider.
    
    This endpoint provides a downloadable CSV template file that users can:
    - Download and fill with their data
    - Use as a reference for the correct format
    - Ensure all required fields are included
    
    ## What You Get:
    - **CSV Template File**: Ready-to-use template with headers and sample data
    - **Proper Formatting**: Correct headers, data types, and structure
    - **Sample Data**: Example records showing the expected format
    
    ## Usage:
    1. Call this endpoint to download the CSV template
    2. Open the downloaded file in Excel or any CSV editor
    3. Replace the sample data with your actual data
    4. Upload the completed CSV using the upload endpoint
    
    Requires Bearer token authentication and 'Data Ingestion' page access.
    """,
    responses={
        200: {
            "description": "CSV template file downloaded successfully",
            "content": {
                "text/csv": {
                    "example": "scheduled_at,time_block,type,forecast,actual\n2024-01-15,1,IDF,1250.5,1200.0\n2024-01-15,2,IDF,1300.0,1250.0"
                }
            }
        },
        400: {
            "description": "Invalid data type or provider code",
            "content": {
                "application/json": {
                    "examples": {
                        "provider_not_found": {
                            "summary": "Provider not found",
                            "value": {
                                "detail": "No provider found with code 'INVALID_PROVIDER' for data type 'demand'"
                            }
                        },
                        "provider_disabled": {
                            "summary": "Provider disabled",
                            "value": {
                                "detail": "Provider 'TEST_DATA' is disabled for data type 'demand'"
                            }
                        },
                        "no_schema": {
                            "summary": "Provider has no schema",
                            "value": {
                                "detail": "Provider 'TEST_DATA' does not have a defined schema for data type 'demand'"
                            }
                        },
                        "invalid_data_type": {
                            "summary": "Invalid data type",
                            "value": {
                                "detail": "Invalid data type 'invalid_type'. Valid types: demand, supply, bid, offer"
                            }
                        }
                    }
                }
            }
        },
        401: {"description": "Authentication required - Bearer token missing or invalid"},
        403: {"description": "Access denied - insufficient permissions for Data Ingestion"},
        404: {"description": "Provider not found for the specified data type and code"},
        500: {"description": "Internal server error while retrieving CSV format"}
    },
    dependencies=[Security(security)]
)
async def get_csv_format(
    data_type: str,
    provider_code: str,
    _current_user: Dict[str, Any] = Depends(require_page_access("Data Ingestion")),
    file_upload_service: FileUploadApplicationService = Depends(get_file_upload_service)
):
    """
    Get accepted CSV format for specific data type and provider code
    
    This endpoint provides comprehensive information about the expected CSV format
    for uploading data to a specific provider, including:
    
    ## Provider Information:
    - Provider details (code, name, data type, status)
    - Provider capabilities and requirements
    
    ## CSV Format Details:
    - Required and optional column headers
    - Data types for each field
    - Validation rules and constraints
    - Field descriptions and examples
    
    ## Sample Data:
    - Example CSV records showing correct format
    - CSV template for download
    - File format requirements (encoding, delimiter, etc.)
    
    Args:
        data_type: Type of data (e.g., 'demand', 'supply', 'bid', 'offer')
        provider_code: Provider code (e.g., 'TEST_DATA', 'SCADA_DEMAND')
        _current_user: Current authenticated user (injected by dependency)
        
    Returns:
        JSONResponse: CSV format information including:
            - provider_info: Provider details and status
            - csv_format: Field specifications and requirements
            - sample_data: Example CSV records
            - csv_template: Downloadable CSV template
            - file_requirements: File format specifications
            
    Raises:
        HTTPException: 
            - 400: Invalid data type or provider validation failed
            - 401: Authentication required (missing or invalid Bearer token)
            - 403: Access denied (insufficient permissions for Data Ingestion)
            - 404: Provider not found for the specified data type and code
            - 500: Internal server error while retrieving format information
    """
    user_id = _current_user.get("user_id", "unknown")
    session_id = _current_user.get("session_id", "unknown")
    
    try:
        # Generate request ID for tracking
        import uuid
        request_id = str(uuid.uuid4())[:8]  # Short request ID for logging
        
        logger.info("CSV format request [%s]: data_type=%s, provider_code=%s, user=%s", 
                   request_id, data_type, provider_code, user_id)
        
        # Create controller with injected service
        controller = create_file_upload_controller(file_upload_service)
        
        # Delegate to controller
        result = await controller.get_csv_format(
            data_type=data_type,
            provider_code=provider_code,
            user_id=user_id, # type: ignore
            session_id=session_id # type: ignore
        )
        
        logger.info("CSV format request [%s] completed successfully", request_id)
        
        # Log successful CSV format request activity
        audit_logger.log_user_action(
            service="ingestion",
            user_id=user_id,
            action="download",
            message=f"CSV format requested: {provider_code} for {data_type}",
            session_id=session_id,
            data_type=data_type,
            provider_code=provider_code,
            request_id=request_id,
            endpoint=f"/file-upload/csv-format/{data_type}/{provider_code}" # type: ignore
        )
        
        # Extract CSV template from result
        csv_template = result.get('csv_template', '')
        if not csv_template:
            raise HTTPException(
                status_code=500,
                detail="No CSV template available for this provider"
            )
        
        # Generate filename
        filename = f"{provider_code}_{data_type}_template.csv"
        
        # Return CSV file as response
        return Response(
            content=csv_template,
            media_type="text/csv",
            headers={
                "Content-Disposition": f"attachment; filename={filename}",
                "Content-Type": "text/csv; charset=utf-8"
            }
        )
        
    except HTTPException as http_exc:
        # Log failed CSV format request for HTTP exceptions
        audit_logger.log_user_action(
            service="ingestion",
            user_id=user_id,
            action="csv_format_request_failed",
            message=f"CSV format request failed: {http_exc.detail}",
            session_id=session_id,
            success=False,
            data_type=data_type,
            provider_code=provider_code,
            status_code=http_exc.status_code,
            endpoint=f"/file-upload/csv-format/{data_type}/{provider_code}" # type: ignore
        ) # type: ignore
        # Re-raise HTTP exceptions
        raise
    except Exception as e:
        logger.error("Unexpected error in get_csv_format endpoint: %s", e)
        
        # Log failed CSV format request for unexpected errors
        audit_logger.log_user_action(
            service="ingestion",
            user_id=user_id,
            action="csv_format_request_error",
            message=f"CSV format request error: {str(e)}",
            session_id=session_id,
            success=False,
            data_type=data_type,
            provider_code=provider_code,
            exception_type=type(e).__name__,
            endpoint=f"/file-upload/csv-format/{data_type}/{provider_code}"
        )
        
        raise HTTPException(
            status_code=500,
            detail="Internal server error occurred while retrieving CSV format"
        ) from e


@router.get(
    "/upload/{upload_id}/download",
    summary="Download uploaded file",
    description="""
    Download the raw file associated with an upload.
    
    This endpoint allows downloading the original file that was uploaded for processing.
    The file is returned as an attachment with the original filename.
    
    Requires Bearer token authentication and 'Data Ingestion' page access.
    """,
    responses={
        200: {
            "description": "File downloaded successfully",
            "content": {
                "text/csv": {},
                "application/octet-stream": {}
            }
        },
        401: {"description": "Authentication required - Bearer token missing or invalid"},
        403: {"description": "Access denied - insufficient permissions for Data Ingestion"},
        404: {"description": "Upload or file not found"},
        500: {"description": "Internal server error while downloading file"}
    },
    dependencies=[Security(security)]
)
async def download_upload_file(
    upload_id: str,
    _current_user: Dict[str, Any] = Depends(require_page_access("Data Ingestion")),
    file_upload_service: FileUploadApplicationService = Depends(get_file_upload_service)
):
    """
    Download uploaded file
    
    Args:
        upload_id: Upload identifier
        _current_user: Current authenticated user (injected by dependency)
        
    Returns:
        FileResponse: The file as a download
        
    Raises:
        HTTPException: If file not found or error occurs
    """
    user_id = _current_user.get("user_id", "unknown")
    session_id = _current_user.get("session_id", "unknown")
    
    try:
        # Create controller with injected service
        controller = create_file_upload_controller(file_upload_service)
        
        # Delegate to controller to get file info
        result = await controller.download_file(
            upload_id=upload_id,
            user_id=user_id,
            session_id=session_id # type: ignore
        )
        
        file_path = result.get("file_path")
        file_name = result.get("file_name")
        
        logger.info("Serving file download: %s (%s)", file_name, file_path)
        
        return FileResponse(
            path=file_path,
            filename=file_name,
            media_type="application/octet-stream"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Unexpected error in download_upload_file endpoint: %s", e)
        raise HTTPException(
            status_code=500,
            detail="Internal server error occurred while downloading file"
        ) from e
