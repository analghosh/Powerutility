import logging
from typing import Dict, Any, Optional, List
from fastapi import HTTPException, status, UploadFile
from libs.audit_queue.simple_logger import AuditLogger

from ..services.limit_values_services import limit_service

logger = logging.getLogger(__name__)


class LimitController:
    def __init__(self):
        self.service = limit_service

    async def latest(self) -> Dict[str, Any]:
        try:
            return await self.service.latest()
        except Exception as e:
            logger.error(f"LimitController.latest error: {str(e)}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to fetch latest")

    async def latest_paginated(self, from_block: int, to_block: int, filters: Dict[str, Any], date_filter: str = None) -> Dict[str, Any]:
        try:
            return await self.service.latest_paginated(from_block, to_block, filters, date_filter)
        except Exception as e:
            logger.error(f"LimitController.latest_paginated error: {str(e)}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to fetch latest with pagination")

    async def latest_all(self, filters: Dict[str, Any], date_filter: str = None) -> Dict[str, Any]:
        try:
            return await self.service.latest_all(filters, date_filter)
        except Exception as e:
            logger.error(f"LimitController.latest_all error: {str(e)}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to fetch all latest data")

    async def get_sample_format(self) -> str:
        try:
            return self.service.get_sample_csv_content()
        except Exception as e:
            logger.error(f"LimitController.get_sample_format error: {str(e)}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to generate sample CSV")

    async def stage_upload(self, file: UploadFile, user_ctx: Dict[str, Any], audit_logger: AuditLogger) -> Dict[str, Any]:
        try:
            content = await file.read()
            return await self.service.stage_file_upload(content, file.filename, user_ctx, audit_logger)
        except Exception as e:
            logger.error(f"LimitController.stage_upload error: {str(e)}")
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Failed to stage file: {str(e)}")

    async def stage_edit(self, approval_id: str, items: List[Dict[str, Any]], user_ctx: Dict[str, Any]) -> Dict[str, Any]:
        try:
            from bson import ObjectId
            return await self.service.stage_edit(ObjectId(approval_id), items, user_ctx)
        except Exception as e:
            logger.error(f"LimitController.stage_edit error: {str(e)}")
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Failed to edit stage: {str(e)}")

    async def list_pending(self, user_ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
        try:
            return await self.service.list_for_approver(user_ctx)
        except Exception as e:
            logger.error(f"LimitController.list_pending error: {str(e)}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to list approvals")

    async def list_pending_paginated(self, user_ctx: Dict[str, Any], page: int, page_size: int, filters: Dict[str, Any], date_filter: str = None) -> Dict[str, Any]:
        try:
            return await self.service.list_for_approver_paginated(user_ctx, page, page_size, filters, date_filter)
        except Exception as e:
            logger.error(f"LimitController.list_pending_paginated error: {str(e)}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to list approvals with pagination")

    async def approve(self, approval_id: str, approver_ctx: Dict[str, Any], audit_logger: AuditLogger = None) -> Dict[str, Any]:
        try:
            from bson import ObjectId
            return await self.service.approve(ObjectId(approval_id), approver_ctx, audit_logger)
        except Exception as e:
            logger.error(f"LimitController.approve error: {str(e)}")
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Failed to approve: {str(e)}")

    async def reject(self, approval_id: str, approver_ctx: Dict[str, Any], reason: Optional[str], audit_logger: AuditLogger = None) -> Dict[str, Any]:
        try:
            return await self.service.reject(approval_id, approver_ctx, reason, audit_logger)
        except Exception as e:
            logger.error(f"LimitController.reject error: {str(e)}")
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Failed to reject: {str(e)}")

    async def single_update(self, item: Dict[str, Any], user_ctx: Dict[str, Any], audit_logger: AuditLogger = None) -> Dict[str, Any]:
        try:
            return await self.service.single_update(item, user_ctx, audit_logger)
        except Exception as e:
            logger.error(f"LimitController.single_update error: {str(e)}")
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Failed to create single update request: {str(e)}")

    async def approve_all(self, user_ctx: Dict[str, Any], audit_logger: AuditLogger = None) -> Dict[str, Any]:
        try:
            return await self.service.approve_all(user_ctx, audit_logger)
        except Exception as e:
            logger.error(f"LimitController.approve_all error: {str(e)}")
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Failed to approve all: {str(e)}")

    async def reject_all(self, user_ctx: Dict[str, Any], reason: Optional[str], audit_logger: AuditLogger = None) -> Dict[str, Any]:
        try:
            return await self.service.reject_all(user_ctx, reason, audit_logger)
        except Exception as e:
            logger.error(f"LimitController.reject_all error: {str(e)}")
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Failed to reject all: {str(e)}")

    async def multi_update(self, items: List[Dict[str, Any]], user_ctx: Dict[str, Any], audit_logger: AuditLogger = None) -> Dict[str, Any]:
        try:
            return await self.service.multi_update(items, user_ctx, audit_logger)
        except Exception as e:
            logger.error(f"LimitController.multi_update error: {str(e)}")
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Failed to create multi update request: {str(e)}")

    async def approve_batch(self, ids: List[str], user_ctx: Dict[str, Any], audit_logger: AuditLogger = None) -> Dict[str, Any]:
        try:
            return await self.service.approve_batch(ids, user_ctx, audit_logger)
        except Exception as e:
            logger.error(f"LimitController.approve_batch error: {str(e)}")
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Failed to approve batch: {str(e)}")

    async def reject_batch(self, ids: List[str], user_ctx: Dict[str, Any], reason: Optional[str], audit_logger: AuditLogger = None) -> Dict[str, Any]:
        try:
            return await self.service.reject_batch(ids, user_ctx, reason, audit_logger)
        except Exception as e:
            logger.error(f"LimitController.reject_batch error: {str(e)}")
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Failed to reject batch: {str(e)}")
            
    async def edit_batch(self, items: List[Dict[str, Any]], user_ctx: Dict[str, Any]) -> Dict[str, Any]:
        try:
            return await self.service.edit_batch(items, user_ctx)
        except Exception as e:
            logger.error(f"LimitController.edit_batch error: {str(e)}")
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Failed to edit batch: {str(e)}")

    async def download_matrix(self, market: Optional[str] = None, schedule_date: Optional[str] = None) -> str:
        try:
            return await self.service.generate_matrix_csv(market, schedule_date)
        except Exception as e:
            logger.error(f"LimitController.download_matrix error: {str(e)}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to generate matrix CSV")
   
    async def download_ocf_matrix(
        self,
        market: Optional[str] = None,
        schedule_date: Optional[str] = None
    ) -> str:
        try:
            return await self.service.generate_ocf_matrix_csv(market, schedule_date)
        except Exception as e:
            logger.error(f"LimitController.download_ocf_matrix error: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to generate OCF matrix CSV"
            )
limit_controller = LimitController()