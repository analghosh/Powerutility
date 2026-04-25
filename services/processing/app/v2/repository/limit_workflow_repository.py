"""
Limit Workflow Repository (approval + audit)
"""

import logging
from typing import List, Dict, Any, Optional

from .base_repository import GenericRepository

from pymongo.results import UpdateResult
from bson import ObjectId

logger = logging.getLogger(__name__)


class LimitWorkflowRepository:
    def __init__(self):
        self.repository = GenericRepository()

    async def ensure_collection_exists(self, collection_name: str):
        """
        Ensures the approval collection exists and has the required indexes.
        """
        try:
            # Index for filtering by status (common query)
            await self.repository.create_index(
                collection_name,
                [("status", 1)],
                name="status_idx"
            )
            
            # Index for sorting by requested_at
            await self.repository.create_index(
                collection_name,
                [("requested_at", -1)],
                name="requested_at_desc_idx"
            )

            # Compound index for business key lookup in approvals
            await self.repository.create_index(
                collection_name,
                [
                    ("block", 1),
                    ("id", 1),
                    ("market", 1),
                    ("category", 1),
                    ("area", 1),
                    ("status", 1)
                ],
                name="approval_business_key_idx"
            )

        except Exception as e:
            logger.error(f"Failed to ensure approval collection {collection_name}: {e}")

    async def find_one(self, collection: str, filter_doc: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Finds a single document matching the filter."""
        try:
            # Assuming self.repository has a pymongo-like find_one method
            return await self.repository.find_one(collection, filter_doc)
        except Exception as e:
            logger.exception(f"[LimitWorkflowRepository] find_one failed: {str(e)}")
            raise

    async def upsert_one(self, collection: str, filter_doc: Dict[str, Any], update_doc: Dict[str, Any]) -> UpdateResult:
        """Performs an upsert operation."""
        try:
            # Assuming self.repository has a pymongo-like update_one method
            res = await self.repository.update_one(collection, filter_doc, update_doc, upsert=True)
            return res
        except Exception as e:
            logger.exception(f"[LimitWorkflowRepository] upsert_one failed: {str(e)}")
            raise

    async def write_audit_many(self, audit_collection: str, log_docs: List[Dict[str, Any]]) -> None:
        """Writes a batch of audit logs."""
        try:
            if not log_docs:
                return
            # Assuming self.repository has a pymongo-like insert_many method
            await self.repository.insert_many(audit_collection, log_docs)
        except Exception as e:
            logger.exception(f"[LimitWorkflowRepository] write_audit_many failed: {str(e)}")
            raise

    # This is your original function, just for context
    async def write_audit(self, audit_collection: str, log_doc: Dict[str, Any]) -> None:
        try:
            await self.repository.insert_one(audit_collection, log_doc)
        except Exception as e:
            logger.exception(f"[LimitWorkflowRepository] write_audit failed: {str(e)}")
            raise

    # -------- Approval documents --------
    async def create_approval(self, approval_collection: str, doc: Dict[str, Any]) -> Any:
        try:
            res = await self.repository.insert_one(approval_collection, doc)
            return res.inserted_id
        except Exception as e:
            logger.exception(f"[LimitWorkflowRepository] create_approval failed: {str(e)}")
            raise

    async def get_approval(self, approval_collection: str, approval_id: Any) -> Optional[Dict[str, Any]]:
        try:
            return await self.repository.find_one(approval_collection, {"_id": ObjectId(approval_id)})
        except Exception as e:
            logger.exception(f"[LimitWorkflowRepository] get_approval failed: {str(e)}")
            raise

    async def update_approval(self, approval_collection: str, approval_id: Any, updates: Dict[str, Any]) -> None:
        try:
            logger.info(f"[LimitWorkflowRepository] Updating approval {approval_id} in {approval_collection} with {updates}")
            result = await self.repository.update_one(approval_collection, {"_id": ObjectId(approval_id)}, {"$set": updates}, upsert=False)
            logger.info(f"[LimitWorkflowRepository] Update result: matched={result.matched_count}, modified={result.modified_count}")
        except Exception as e:
            logger.exception(f"[LimitWorkflowRepository] update_approval failed: {str(e)}")
            raise

    async def delete_approval(self, approval_collection: str, approval_id: Any) -> None:
        try:
            logger.info(f"[LimitWorkflowRepository] Attempting to delete approval {approval_id} from collection {approval_collection}")
            result = await self.repository.delete_one(approval_collection, {"_id": ObjectId(approval_id)})
            logger.info(f"[LimitWorkflowRepository] Delete operation result: deleted_count={result.deleted_count}")
            if result.deleted_count == 0:
                logger.warning(f"[LimitWorkflowRepository] No approval document found to delete with ID: {approval_id}")
            else:
                logger.info(f"[LimitWorkflowRepository] Successfully deleted approval document: {approval_id}")
        except Exception as e:
            logger.exception(f"[LimitWorkflowRepository] delete_approval failed: {str(e)}")
            raise

    async def list_approvals(self, approval_collection: str, status: Optional[str] = None) -> List[Dict[str, Any]]:
        try:
            filt = {"status": status} if status else {}
            cursor = self.repository.find(approval_collection, filt).sort("requested_at", -1)
            if hasattr(cursor, 'close'):
                try:
                    return await cursor.to_list(length=10000)
                finally:
                    cursor.close()
            else:
                return await cursor.to_list(length=10000)
        except Exception as e:
            logger.exception(f"[LimitWorkflowRepository] list_approvals failed: {str(e)}")
            raise

    async def get_combined_approvals(
        self, 
        approval_collection: str, 
        main_collection: str, 
        status_filter: List[str]
    ) -> List[Dict[str, Any]]:
        """
        Fetches approvals combined with current main values (join).
        """
        try:
            # Ensure indexes exist
            await self.ensure_collection_exists(approval_collection)
            
            pipeline = [
                # Stage 1: Filter approvals by status
                {
                    "$match": {
                        "status": {"$in": status_filter}
                    }
                },
                
                # Stage 2: Lookup corresponding records from main collection
                {
                    "$lookup": {
                        "from": main_collection,
                        "let": {
                            "key_block": "$block",
                            "key_id": "$id",
                            "key_market": "$market", 
                            "key_category": "$category",
                            "key_area": "$area",
                            "key_date": "$scheduled_at"
                        },
                        "pipeline": [
                            {
                                "$match": {
                                    "$expr": {
                                        "$and": [
                                            {"$eq": ["$block", "$$key_block"]},
                                            {"$eq": ["$id", "$$key_id"]},
                                            {"$eq": ["$market", "$$key_market"]},
                                            {"$eq": ["$category", "$$key_category"]},
                                            {"$eq": ["$area", "$$key_area"]},
                                            {"$eq": ["$scheduled_at", "$$key_date"]}
                                        ]
                                    }
                                }
                            }
                        ],
                        "as": "main_record"
                    }
                },
                
                # Stage 3: Unwind main record (preserve null for approvals without main data)
                {
                    "$unwind": {
                        "path": "$main_record",
                        "preserveNullAndEmptyArrays": True
                    }
                },
                
                # Stage 4: Project the final structure
                {
                    "$project": {
                        "_id": 1,
                        "approval_id": "$_id",
                        "block": 1,
                        "id": 1,
                        "market": 1,
                        "category": 1,
                        "area": 1,
                        "status": 1,
                        "request_type": 1,
                        "title": 1,
                        "requested_by": 1,
                        "requested_username": 1,
                        "requested_at": 1,
                        "last_edited_at": 1,
                        "old_value": "$main_record",
                        "new_value": {
                            "scheduled_at": "$scheduled_at",
                            "block": "$block",
                            "id": "$id",
                            "market": "$market",
                            "category": "$category",
                            "area": "$area",
                            "values": "$value"
                        }
                    }
                },
                
                # Stage 5: Sort by request date
                {"$sort": {"requested_at": -1}}
            ]
            
            cursor = await self.repository.aggregate(approval_collection, pipeline)
            return cursor
            
        except Exception as e:
            logger.exception(f"[LimitWorkflowRepository] get_combined_approvals failed: {str(e)}")
            raise

    async def get_combined_approvals_paginated(
        self, 
        approval_collection: str, 
        main_collection: str, 
        status_filter: List[str],
        page: int = 1,
        page_size: int = 50,
        filters: Dict[str, Any] = None,
        date_filter: str = None
    ) -> Dict[str, Any]:
        """
        Paginated version of get_combined_approvals with filtering support.
        Returns data with pagination metadata.
        """
        try:
            # Ensure indexes exist
            await self.ensure_collection_exists(approval_collection)
            
            filters = filters or {}
            
            # Build the match stage for filtering
            match_stage = {
                "status": {"$in": status_filter}
            }
            
            # Add date filtering if provided
            if date_filter:
                from datetime import datetime, timedelta
                filter_date_dt = datetime.strptime(date_filter, "%Y-%m-%d")
                match_stage["$or"] = [
                    {"schedule_date": date_filter},
                    {
                        "created_at": {
                            "$gte": filter_date_dt,
                            "$lt": filter_date_dt + timedelta(days=1)
                        }
                    }
                ]
            
            # Add filters for the unique key fields
            if "block" in filters:
                match_stage["block"] = filters["block"]
            if "id" in filters:
                match_stage["id"] = filters["id"]
            if "market" in filters:
                match_stage["market"] = filters["market"]
            if "category" in filters:
                match_stage["category"] = filters["category"]
            if "area" in filters:
                match_stage["area"] = filters["area"]
            if "status" in filters:
                # Override status filter if explicitly provided
                match_stage["status"] = filters["status"]
            
            # Pipeline for counting total documents (without pagination)
            count_pipeline = [
                {"$match": match_stage},
                {"$count": "total"}
            ]
            
            # Get total count with increased timeout
            count_result = await self.repository.aggregate(approval_collection, count_pipeline, max_time_ms=60000)

            total = count_result[0]["total"] if count_result else 0
            
            # Calculate pagination
            page = max(1, page)
            page_size = max(1, min(page_size, 1000))  # Cap at 1000
            skip = (page - 1) * page_size
            total_pages = (total + page_size - 1) // page_size
            has_next = page < total_pages
            has_prev = page > 1
            
            # Main pipeline with pagination
            pipeline = [
                # Stage 1: Filter approvals by status and other filters
                {"$match": match_stage},
                
                # Stage 2: Lookup corresponding records from main collection
                {
                    "$lookup": {
                        "from": main_collection,
                        "let": {
                            "key_block": "$block",
                            "key_id": "$id",
                            "key_market": "$market", 
                            "key_category": "$category",
                            "key_area": "$area",
                            "key_date": "$scheduled_at"
                        },
                        "pipeline": [
                            {
                                "$match": {
                                    "$expr": {
                                        "$and": [
                                            {"$eq": ["$block", "$$key_block"]},
                                            {"$eq": ["$id", "$$key_id"]},
                                            {"$eq": ["$market", "$$key_market"]},
                                            {"$eq": ["$category", "$$key_category"]},
                                            {"$eq": ["$area", "$$key_area"]},
                                            {"$eq": ["$scheduled_at", "$$key_date"]}
                                        ]
                                    }
                                }
                            }
                        ],
                        "as": "main_record"
                    }
                },
                
                # Stage 3: Unwind main record (preserve null for approvals without main data)
                {
                    "$unwind": {
                        "path": "$main_record",
                        "preserveNullAndEmptyArrays": True
                    }
                },
                
                # Stage 4: Project the final structure
                {
                    "$project": {
                        "_id": 1,
                        "approval_id": "$_id",
                        "block": 1,
                        "id": 1,
                        "market": 1,
                        "category": 1,
                        "area": 1,
                        "status": 1,
                        "request_type": 1,
                        "title": 1,
                        "requested_by": 1,
                        "requested_username": 1,
                        "requested_at": 1,
                        "last_edited_at": 1,
                        "old_value": "$main_record",
                        "new_value": {
                            "scheduled_at": "$scheduled_at",
                            "block": "$block",
                            "id": "$id",
                            "market": "$market",
                            "category": "$category",
                            "area": "$area",
                            "values": "$value"
                        }
                    }
                },
                
                # Stage 5: Sort by block number (ascending)
                {"$sort": {"block": 1}},
                
                # Stage 6: Pagination
                {"$skip": skip},
                {"$limit": page_size}
            ]
            
            # Execute the main pipeline with increased timeout
            data = await self.repository.aggregate(approval_collection, pipeline, max_time_ms=60000)
            
            return {
                "data": data,
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": total_pages,
                "has_next": has_next,
                "has_prev": has_prev
            }
            
        except Exception as e:
            # Check for specific MongoDB timeout errors
            error_msg = str(e).lower()
            if "timeout" in error_msg or "execution" in error_msg:
                logger.error(f"[LimitWorkflowRepository] MongoDB aggregation timeout - consider reducing page_size or adding indexes: {str(e)}")
            else:
                logger.exception(f"[LimitWorkflowRepository] get_combined_approvals_paginated failed: {str(e)}")
            raise


limit_workflow_repository = LimitWorkflowRepository()
