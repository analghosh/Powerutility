"""
Parameter Approval Repository
Repository for bid-configuration approval workflow.
"""

import logging
from typing import List, Dict, Any, Optional
from datetime import datetime
from bson import ObjectId

from .base_repository import GenericRepository

logger = logging.getLogger(__name__)


class ParameterApprovalRepository:
    """Repository for parameter_approval collection operations"""

    def __init__(self):
        self.repo = GenericRepository()
        self.collection_name = "parameter_approval"

    async def get_pending(self, config_key: str = "bid_configuration") -> Optional[Dict[str, Any]]:
        """Return the single PENDING approval for the given config key, if any."""
        try:
            return await self.repo.find_one_async(
                self.collection_name,
                {"config_key": config_key, "status": "PENDING"}
            )
        except Exception as e:
            logger.error(f"Error getting pending approval: {e}")
            raise

    async def stage(
        self,
        config_key: str,
        config_value: Dict[str, Any],
        user_ctx: Dict[str, Any]
    ) -> Any:
        """
        Upsert a PENDING approval. Only one PENDING record per config_key.
        Returns the approval _id.
        """
        try:
            now = datetime.now()
            actor_id = user_ctx.get("user_id", "anonymous")
            actor_username = user_ctx.get("username", "anonymous")
            correlation_id = user_ctx.get("correlation_id", "unknown")

            filter_doc = {"config_key": config_key, "status": "PENDING"}
            update_doc = {
                "$set": {
                    "config_key": config_key,
                    "config_value": config_value,
                    "status": "PENDING",
                    "last_edited_at": now,
                    "last_edited_by": actor_id,
                    "correlation_id": correlation_id,
                },
                "$setOnInsert": {
                    "requested_at": now,
                    "requested_by": actor_id,
                    "requested_username": actor_username,
                    "created_at": now,
                    "created_by": actor_id,
                },
            }

            result = await self.repo.update_one_async(
                self.collection_name,
                filter_doc,
                update_doc,
                upsert=True
            )

            if result.upserted_id:
                logger.info(f"Staged new parameter approval: {result.upserted_id}")
                return result.upserted_id

            # Existing pending was updated; fetch and return its id.
            pending = await self.get_pending(config_key)
            return pending.get("_id") if pending else None

        except Exception as e:
            logger.error(f"Error staging parameter approval: {e}")
            raise

    async def get_by_id(self, approval_id: Any) -> Optional[Dict[str, Any]]:
        """Get approval by ID."""
        try:
            if isinstance(approval_id, str) and ObjectId.is_valid(approval_id):
                approval_id = ObjectId(approval_id)
            return await self.repo.find_one_async(
                self.collection_name,
                {"_id": approval_id}
            )
        except Exception as e:
            logger.error(f"Error getting approval by id: {e}")
            raise

    async def mark_status(
        self,
        approval_id: Any,
        status: str,
        actor_ctx: Dict[str, Any],
        reason: Optional[str] = None
    ) -> bool:
        """Mark approval as APPROVED or REJECTED."""
        try:
            if isinstance(approval_id, str) and ObjectId.is_valid(approval_id):
                approval_id = ObjectId(approval_id)

            now = datetime.now()
            actor_id = actor_ctx.get("user_id", "approver")
            actor_username = actor_ctx.get("username", "approver")

            updates = {
                "status": status,
                f"{status.lower()}_at": now,
                f"{status.lower()}_by": actor_id,
                f"{status.lower()}_username": actor_username,
                "last_edited_at": now,
                "last_edited_by": actor_id,
            }
            if reason:
                updates["decision_reason"] = reason

            result = await self.repo.update_one_async(
                self.collection_name,
                {"_id": approval_id},
                {"$set": updates}
            )

            return result.modified_count > 0

        except Exception as e:
            logger.error(f"Error marking approval status {status}: {e}")
            raise

    async def list_approvals(
        self,
        config_key: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """List approvals with optional filters."""
        try:
            filt: Dict[str, Any] = {}
            if config_key:
                filt["config_key"] = config_key
            if status:
                filt["status"] = status

            return await self.repo.get_all_async(
                self.collection_name,
                filter=filt,
                sort=[("requested_at", -1)],
                limit=limit
            )

        except Exception as e:
            logger.error(f"Error listing approvals: {e}")
            raise

    async def delete(self, approval_id: Any) -> bool:
        """Delete an approval record."""
        try:
            if isinstance(approval_id, str) and ObjectId.is_valid(approval_id):
                approval_id = ObjectId(approval_id)
            result = await self.repo.delete_one(
                self.collection_name,
                {"_id": approval_id}
            )
            return result.deleted_count > 0
        except Exception as e:
            logger.error(f"Error deleting approval: {e}")
            raise


parameter_approval_repository = ParameterApprovalRepository()
