# repositories/serve_dam_scheduling_repository.py
from typing import List, Dict, Any, Optional
import logging
from bson import ObjectId
from fastapi.encoders import jsonable_encoder
from ..repository.base_repository import GenericRepository
from ..repository.base_repository import GenericRepository
from ...core.config import settings
logger = logging.getLogger(__name__)

def convert_objectid(obj):
    if isinstance(obj, dict):
        return {k: convert_objectid(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [convert_objectid(i) for i in obj]
    elif isinstance(obj, ObjectId):
        return str(obj)
    else:
        return obj
    
class BlockBidCurrentDataRepository(GenericRepository):
    """
    Thin repository for the 'serve_dam_scheduling' collection.
    Keeps the same interface style as your other repos (e.g., serveExchangeTableRepository).
    """

    def __init__(self):
        super().__init__()
        self.collection_name = settings.MONGODB_RTM_BLOCK_BID_COLLECTION
    

    @property
    def collection(self):
        """Get the MongoDB collection for this repository"""
        return self.db[self.collection_name]
    

    async def get_max_revisions(self, biding_date: str, market_type: str, trade_sessions: List[int]) -> Dict[tuple, int]:
        """
        Returns the current max revision_no for each (trade_session, time_block) pair.
        Used by insert_block_bid to assign the next revision number.
        Pairs with no existing documents get revision 0 (so first insert becomes 1).
        """
        try:
            pipeline = [
                {
                    "$match": {
                        "biding_date": biding_date,
                        "market_type": market_type,
                        "trade_session": {"$in": trade_sessions},
                    }
                },
                {
                    "$group": {
                        "_id": {"trade_session": "$trade_session", "time_block": "$time_block"},
                        "max_revision": {"$max": "$revision_no"}
                    }
                }
            ]
            cursor = self.collection.aggregate(pipeline)
            return {(doc["_id"]["trade_session"], doc["_id"]["time_block"]): doc["max_revision"] for doc in cursor}
        except Exception as e:
            logger.error(f"Failed to get max revisions: {str(e)}")
            return {}

    # ---- Generic passthrough used by the service layer ----
    async def insert_many(self, documents: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Insert multiple documents into the collection asynchronously.
        Returns {"data": True} if successful.
        """
        
        try:    
            if not documents:
                raise ValueError("documents must be a non-empty list")

            # Ensure all documents are dicts
            for i, doc in enumerate(documents):
                if not isinstance(doc, dict):
                    raise TypeError(f"Document at index {i} is not a dict: {type(doc)}")

            logger.info(f"Inserting {len(documents)} documents into collection...")
            
            result= self.collection.insert_many(documents, ordered=False)
            inserted_ids = [str(_id) for _id in result.inserted_ids]
            
            logger.info("Bulk insert successful")
            return {"data": True}
        except Exception as e:
            logger.error("Failed to bulk insert iex exchange serve records: %s", str(e))
            return str(e) # type: ignore
    
    async def aggregate(self, pipeline: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Run aggregation pipeline on the collection.
        """
        try:
            cursor = self.collection.aggregate(pipeline)
            return list(cursor)
        except Exception as e:
            logger.error("Failed to aggregate DAM product data: %s", str(e))
            return []

    async def get_data(self, date) -> List[Dict]:
        """
        Returns the latest revision per (trade_session, time_block) for the given date.
        Multiple time blocks per session are shown independently.
        - Excludes lapsed records (statuses ending in '_lapse').
        - For past dates: also excludes lapsable statuses (Pending/pending/Approved)
          that the cron should have already lapsed.
        """
        try:
            cursor = (
                self.collection.find({"biding_date": date})
                .sort([("from", 1), ("trade_session", 1), ("revision_no", 1)])
            )
            result = []
            for doc in cursor:
                if "_id" in doc and isinstance(doc["_id"], ObjectId):
                    doc["_id"] = str(doc["_id"])
                result.append(doc)
            return result

        except Exception as e:
            logger.error(f"Failed to get pending approvals in {self.collection}: {str(e)}")
            raise ValueError(f"Error fetching data: {str(e)}") from e

        
    async def updatedata(self, record_id, data):
        result =  self.collection.update_one(
            {"_id": ObjectId(record_id)},
            {"$set": data}
        )
        logger.debug(f"Update result: {result}")
        return result.modified_count

    async def update_one(self, filter_query: Dict[str, Any], update_query: Dict[str, Any]) -> Dict[str, Any]:
        """Update a single document matching the filter (e.g. to add exchange fields after submission)."""
        try:
            result = self.collection.update_one(filter_query, update_query)
            return {"matched_count": result.matched_count, "modified_count": result.modified_count}
        except Exception as e:
            logger.error(f"Failed to update block bid record: {str(e)}")
            return {"error": str(e)}

    @staticmethod
    def time_to_period_code(time_str: str) -> Optional[int]:
        """
        Convert time string (HH:MM) to period code (1-96).
        
        Args:
            time_str: Time in "HH:MM" format (e.g., "15:00")
        
        Returns:
            Period code (1-96) or None if invalid
        """
        try:
            hour, minute = map(int, time_str.split(":"))
            # Calculate period code: hour * 4 + (minute // 15) + 1
            period_code = hour * 4 + (minute // 15) + 1
            if 1 <= period_code <= 96:
                return period_code
            return None
        except (ValueError, IndexError, AttributeError) as e:
            logger.warning(f"Error converting time '{time_str}' to period_code: {str(e)}")
            return None
    
    async def get_block_bid_data_by_date_and_market_type(
        self, 
        datebid: str, 
        market_type: str = "RTM"
    ) -> List[Dict[str, Any]]:
        """
        Get block bid data for a specific date and market type.
        
        Args:
            datebid: Schedule date in YYYY-MM-DD format
            market_type: Market type (RTM, DAM, etc.). Defaults to "RTM"
        
        Returns:
            List of block bid data dictionaries
        """
        try:
            query = {
                "biding_date": datebid,
                "market_type": market_type
            }
            
            cursor = self.collection.find(query).sort("from", 1)
            results = list(cursor)
            
            # Convert ObjectId to string for JSON serialization
            for result in results:
                if "_id" in result and isinstance(result["_id"], ObjectId):
                    result["_id"] = str(result["_id"])
            
            logger.info(f"Retrieved {len(results)} block bid data records for date {datebid}, market_type {market_type}")
            return results
            
        except Exception as e:
            logger.error(f"Error getting block bid data for date {datebid}, market_type {market_type}: {str(e)}")
            return []

    async def get_approved_block_bid_quantities_by_time_block(
        self,
        biding_date: str,
        market_type: str = "RTM"
    ) -> Dict[str, float]:
        """
        Sum block-bid quantity for every matched document, grouped by ``time_block``.
        Includes status ``Approved`` and ``Submitted``; every matching row contributes
        (no revision de-duplication). If ``number_of_bids`` > 1, quantum is multiplied
        by ``number_of_bids`` to get quantity per row.

        Args:
            biding_date: Schedule date in YYYY-MM-DD format
            market_type: Market type (RTM, DAM, etc.). Defaults to "RTM"

        Returns:
            Dictionary mapping time_block (e.g., "19:00-19:15") to total quantum

        Note:
            For DAM, only ``trade_session`` 0 (or null / missing) is included — same as
            ``insert_block_bid`` (DAM has no multi-session split). RTM sums all sessions
            that share the same clock ``time_block`` string. If old revision rows remain
            in the collection, they are all summed unless filtered elsewhere.
        """
        try:
            match_filter: Dict[str, Any] = {
                "biding_date": biding_date,
                "market_type": market_type,
                "status": {"$in": ["Submitted", "submitted"]},
            }
            if str(market_type).upper() == "DAM":
                # Avoid double-counting when stray non-zero trade_session rows exist for DAM
                match_filter["trade_session"] = {"$in": [0, None]}

            pipeline = [
                {"$match": match_filter},
                {
                    "$project": {
                        "time_block": 1,
                        "quantum": 1,
                        "number_of_bids": {"$ifNull": ["$number_of_bids", 1]},
                        "calculated_quantity": {
                            "$cond": {
                                "if": {"$gt": [{"$ifNull": ["$number_of_bids", 1]}, 1]},
                                "then": {
                                    "$multiply": [
                                        "$quantum",
                                        {"$ifNull": ["$number_of_bids", 1]},
                                    ]
                                },
                                "else": "$quantum",
                            }
                        },
                    }
                },
                {
                    "$group": {
                        "_id": "$time_block",
                        "total_quantum": {"$sum": "$calculated_quantity"},
                    }
                },
                {
                    "$project": {
                        "_id": 0,
                        "time_block": "$_id",
                        "total_quantum": 1,
                    }
                },
            ]

            results = await self.aggregate(pipeline)

            quantities_dict = {}
            for result in results:
                time_block = result.get("time_block", "")
                total_quantum = float(result.get("total_quantum", 0))
                if time_block:
                    quantities_dict[time_block] = total_quantum

            logger.info(
                f"Retrieved block bid quantities (all matched Approved/Submitted rows) for "
                f"{len(quantities_dict)} time blocks on {biding_date}, market_type {market_type}"
            )
            if quantities_dict:
                logger.debug(f"Block bid time blocks: {list(quantities_dict.keys())}")
            else:
                logger.warning(
                    f"No Approved/Submitted block bids found for date {biding_date}, "
                    f"market_type {market_type}."
                )
            return quantities_dict

        except Exception as e:
            logger.error(
                f"Error getting block bid quantities for date {biding_date}, "
                f"market_type {market_type}: {str(e)}"
            )
            return {}

    async def get_approved_reserved_block_bid_quantities_by_time_block(
        self,
        biding_date: str,
    ) -> Dict[str, float]:
        """
        Sum block-bid quantity for **Reserved** selections only, grouped by ``time_block``.

        Matches ``biding_date``, ``status`` exactly ``Approved``, and ``selected_for``
        exactly ``Reserved`` only (no ``market_type`` filter, so e.g. RTM/GRTM rows all
        count). Quantity per row matches ``get_approved_block_bid_quantities_by_time_block``
        (``number_of_bids`` multiplies ``quantum`` when > 1).
        """
        try:
            match_filter: Dict[str, Any] = {
                "biding_date": biding_date,
                "status": "Approved",
                "selected_for": "Reserved",
            }

            pipeline = [
                {"$match": match_filter},
                {
                    "$project": {
                        "time_block": 1,
                        "quantum": 1,
                        "number_of_bids": {"$ifNull": ["$number_of_bids", 1]},
                        "calculated_quantity": {
                            "$cond": {
                                "if": {"$gt": [{"$ifNull": ["$number_of_bids", 1]}, 1]},
                                "then": {
                                    "$multiply": [
                                        "$quantum",
                                        {"$ifNull": ["$number_of_bids", 1]},
                                    ]
                                },
                                "else": "$quantum",
                            }
                        },
                    }
                },
                {
                    "$group": {
                        "_id": "$time_block",
                        "total_quantum": {"$sum": "$calculated_quantity"},
                    }
                },
                {
                    "$project": {
                        "_id": 0,
                        "time_block": "$_id",
                        "total_quantum": 1,
                    }
                },
            ]

            results = await self.aggregate(pipeline)

            quantities_dict: Dict[str, float] = {}
            for result in results:
                time_block = result.get("time_block", "")
                total_quantum = float(result.get("total_quantum", 0))
                if time_block:
                    quantities_dict[time_block] = total_quantum

            logger.info(
                f"Retrieved Reserved (Approved-only) block bid quantities for "
                f"{len(quantities_dict)} time blocks on {biding_date} (all market_type)"
            )
            if not quantities_dict:
                logger.debug(
                    f"No Approved Reserved block bids for {biding_date}."
                )
            return quantities_dict

        except Exception as e:
            logger.error(
                f"Error getting reserved block bid quantities for date {biding_date}: {str(e)}"
            )
            return {}

    
    
    async def get_active_bids_by_sessions(
        self,
        biding_date: str,
        market_type: str,
        trade_sessions: List[int],
    ) -> Dict[tuple, Dict[str, Any]]:
        """
        Returns any active (pending or Approved) bid per (trade_session, time_block) pair
        for the given date and market type.

        Returns a dict keyed by (trade_session, time_block) tuple containing the most recent active doc.
        """
        try:
            pipeline = [
                {
                    "$match": {
                        "biding_date": biding_date,
                        "market_type": market_type,
                        "trade_session": {"$in": trade_sessions},
                        "status": {"$in": ["pending", "Approved"]},
                    }
                },
                {"$sort": {"revision_no": -1}},
                {
                    "$group": {
                        "_id": {"trade_session": "$trade_session", "time_block": "$time_block"},
                        "doc": {"$first": "$$ROOT"},
                    }
                },
                {"$replaceRoot": {"newRoot": "$doc"}},
            ]
            cursor = self.collection.aggregate(pipeline)
            result: Dict[tuple, Dict[str, Any]] = {}
            for doc in cursor:
                if "_id" in doc and isinstance(doc["_id"], ObjectId):
                    doc["_id"] = str(doc["_id"])
                result[(int(doc["trade_session"]), doc.get("time_block", ""))] = doc
            return result
        except Exception as e:
            logger.error(f"Failed to get active bids by sessions: {str(e)}")
            return {}

    async def find_by_filters(self, filters: Dict[str, Any], projection: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """
        Find documents by filters with optional projection.
        """
        try:
            query = self.collection.find(filters, projection or {})
            return list(query)
        except Exception as e:
            logger.error(f"Failed to find block bid records: {str(e)}")
            return []

    async def get_approved_bids(self, biding_date: str, market_type: str = "RTM") -> List[Dict[str, Any]]:
        """
        Fetch the latest Approved block bid per session for a given date and market type.
        If the same session has been submitted/approved multiple times, only the most
        recently created document (highest _id) is returned for exchange submission.
        """
        try:
            pipeline = [
                {
                    "$match": {
                        "biding_date": biding_date,
                        "market_type": market_type,
                        "status": "Approved"
                    }
                },
                {"$sort": {"revision_no": -1}},  # latest revision first
                {
                    "$group": {
                        "_id": {"trade_session": "$trade_session", "time_block": "$time_block"},
                        "doc": {"$first": "$$ROOT"}
                    }
                },
                {"$replaceRoot": {"newRoot": "$doc"}},
                {"$sort": {"from": 1}},  # restore chronological order
            ]
            cursor = self.collection.aggregate(pipeline)
            results = list(cursor)
            for doc in results:
                if "_id" in doc and isinstance(doc["_id"], ObjectId):
                    doc["_id"] = str(doc["_id"])
            logger.info(f"Found {len(results)} latest approved bids for {biding_date} [{market_type}]")
            return results
        except Exception as e:
            logger.error(f"Failed to get approved bids: {str(e)}")
            return []

    async def bulk_update_status(self, biding_date: str, from_status: str, to_status: str, market_type: str = "RTM") -> int:
        """
        Update status only on the latest revision per trade_session.
        Old revisions are left unchanged to preserve history.
        Returns count of modified documents.
        """
        try:
            # Find the _id of the latest revision per session that matches from_status
            pipeline = [
                {
                    "$match": {
                        "biding_date": biding_date,
                        "market_type": market_type,
                        "status": from_status,
                    }
                },
                {"$sort": {"revision_no": -1}},
                {
                    "$group": {
                        "_id": {"trade_session": "$trade_session", "time_block": "$time_block"},
                        "doc_id": {"$first": "$_id"}
                    }
                }
            ]
            latest_docs = list(self.collection.aggregate(pipeline))
            ids_to_update = [doc["doc_id"] for doc in latest_docs]

            if not ids_to_update:
                return 0

            result = self.collection.update_many(
                {"_id": {"$in": ids_to_update}},
                {"$set": {"status": to_status}}
            )
            logger.info(f"bulk_update_status: {from_status} -> {to_status}, modified={result.modified_count} (latest revisions only)")
            return result.modified_count
        except Exception as e:
            logger.error(f"Failed to bulk update status: {str(e)}")
            return 0
