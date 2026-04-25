from typing import List, Dict, Any, Optional

import logging

from .base_repository import GenericRepository
from ...core.config import settings

logger = logging.getLogger(__name__)


class CaptureServeTableDataRepository(GenericRepository):

    def __init__(self, collection_name: Optional[str] = None):
        super().__init__()
        if collection_name is None:
            collection_name = getattr(
                settings,
                "MONGODB_RTM_BID_CAPTURE_COLLECTION",
                "rtm_bid_capture",
            )
        self.collection_name = collection_name
        logger.info(
            f"CaptureServeTableDataRepository initialized with collection: {collection_name}"
        )

    @property
    def collection(self):
        return self.db[self.collection_name]

    def ensure_idempotency_indexes(self) -> None:
        """Speed find_one_by_schedule_dc_sg_bid_block_revision (idempotent)."""
        try:
            self.collection.create_index(
                [
                    ("schedule_date", 1),
                    ("dc_sg_type", 1),
                    ("bid_block_no", 1),
                    ("bid_revision_no", 1),
                ],
                name="sched_dcsg_block_bidrev_1",
                background=True,
            )
        except Exception as e:
            logger.warning("capture collection index ensure skipped: %s", e)

    async def find_one_by_schedule_dc_sg_revision(
        self,
        schedule_date: str,
        dc_sg_type: str,
        rldc_revision_no: Any,
    ) -> Optional[Dict[str, Any]]:
        try:
            result = await self.collection.find_one(
                {
                    "schedule_date": schedule_date,
                    "dc_sg_type": dc_sg_type,
                    "rldc_revision_no": rldc_revision_no,
                }
            )
            if result is None:
                return None
            if "_id" in result:
                result["_id"] = str(result["_id"])
            return result
        except Exception as e:
            logger.error(
                f"Error in find_one_by_schedule_dc_sg_revision: {e}"
            )
            return None

    async def find_one_by_schedule_dc_sg_bid_block_revision(
        self,
        schedule_date: str,
        dc_sg_type: str,
        bid_block_no: int,
        bid_revision_no: int,
    ) -> Optional[Dict[str, Any]]:
        try:
            result = await self.collection.find_one(
                {
                    "schedule_date": schedule_date,
                    "dc_sg_type": dc_sg_type,
                    "bid_block_no": bid_block_no,
                    "bid_revision_no": bid_revision_no,
                }
            )
            if result is None:
                return None
            if "_id" in result:
                result["_id"] = str(result["_id"])
            return result
        except Exception as e:
            logger.error(
                f"Error in find_one_by_schedule_dc_sg_bid_block_revision: {e}"
            )
            return None

    async def insert_many(self, documents: List[Dict[str, Any]]) -> Any:
        if not documents:
            logger.warning("insert_many called with empty documents list")
            return []
        try:
            clean = [doc for doc in documents if isinstance(doc, dict)]
            if not clean:
                raise ValueError("No valid dict documents to insert")
            result = await self.collection.insert_many(clean, ordered=False)
            inserted_ids = [str(oid) for oid in result.inserted_ids]
            logger.info(
                f"CaptureServeTableDataRepository: inserted {len(inserted_ids)} documents"
            )
            return inserted_ids
        except Exception as e:
            logger.error(f"Failed to insert rtm_bid_capture: {e}")
            raise

    async def update_exchange_submission_flag(
        self,
        schedule_date: str,
        dc_sg_type: str,
        rldc_revision_no: Any,
        flag: str,
        bid_block_no: Optional[int] = None,
        bid_revision_no: Optional[int] = None,
    ) -> int:
        if flag not in ("Y", "N"):
            logger.warning(f"update_exchange_submission_flag: invalid flag '{flag}', expected Y or N")
            return 0
        try:
            filter_query = {
                "schedule_date": schedule_date,
                "dc_sg_type": dc_sg_type,
                "rldc_revision_no": rldc_revision_no,
            }
            if bid_block_no is not None:
                filter_query["bid_block_no"] = bid_block_no
            if bid_revision_no is not None:
                filter_query["bid_revision_no"] = bid_revision_no
            result = await self.collection.update_many(
                filter_query,
                {"$set": {"exchange_submission_flag": flag}},
            )
            if result.modified_count:
                logger.info(
                    f"CaptureServeTableDataRepository: set exchange_submission_flag={flag} "
                    f"for {result.modified_count} docs (schedule_date={schedule_date}, rldc_revision_no={rldc_revision_no})"
                )
            return result.modified_count
        except Exception as e:
            logger.error(f"Failed to update exchange_submission_flag: {e}")
            return 0
