import logging
from typing import List, Dict, Any, Optional
from datetime import datetime
from bson import ObjectId

from .base_repository import GenericRepository

logger = logging.getLogger(__name__)

# these are the 12 default records we seed when the collection is first hit
# rtm -> standalone, dam/gdam/hpdam -> ocf (as per business rules)
DEFAULT_POWER_EXCHANGE_PARAMETERS = [
    # IEX
    {"exchange": "iex",  "market": "rtm",   "bid_type": "single_bid", "allocation_percent": 100.0, "ocf_standalone": "standalone", "premium_discount": "premium"},
    {"exchange": "iex",  "market": "dam",   "bid_type": "single_bid", "allocation_percent": 100.0, "ocf_standalone": "ocf",        "premium_discount": "premium"},
    {"exchange": "iex",  "market": "gdam",  "bid_type": "single_bid", "allocation_percent": 100.0, "ocf_standalone": "ocf",        "premium_discount": "premium"},
    {"exchange": "iex",  "market": "hpdam", "bid_type": "single_bid", "allocation_percent": 100.0, "ocf_standalone": "standalone", "premium_discount": "premium"},
    # HPX
    {"exchange": "hpx",  "market": "rtm",   "bid_type": "single_bid", "allocation_percent": 100.0, "ocf_standalone": "standalone", "premium_discount": "premium"},
    {"exchange": "hpx",  "market": "dam",   "bid_type": "single_bid", "allocation_percent": 100.0, "ocf_standalone": "ocf",        "premium_discount": "premium"},
    {"exchange": "hpx",  "market": "gdam",  "bid_type": "single_bid", "allocation_percent": 100.0, "ocf_standalone": "ocf",        "premium_discount": "premium"},
    {"exchange": "hpx",  "market": "hpdam", "bid_type": "single_bid", "allocation_percent": 100.0, "ocf_standalone": "standalone", "premium_discount": "premium"},
    # PXIL
    {"exchange": "pxil", "market": "rtm",   "bid_type": "single_bid", "allocation_percent": 100.0, "ocf_standalone": "standalone", "premium_discount": "premium"},
    {"exchange": "pxil", "market": "dam",   "bid_type": "single_bid", "allocation_percent": 100.0, "ocf_standalone": "ocf",        "premium_discount": "premium"},
    {"exchange": "pxil", "market": "gdam",  "bid_type": "single_bid", "allocation_percent": 100.0, "ocf_standalone": "ocf",        "premium_discount": "premium"},
    {"exchange": "pxil", "market": "hpdam", "bid_type": "single_bid", "allocation_percent": 100.0, "ocf_standalone": "standalone", "premium_discount": "premium"},
]


class PowerExchangeParameterRepository:

    def __init__(self):
        self.repo = GenericRepository()
        self.collection_name = "power_exchange_parameters"

    async def seed_default_data(self) -> None:
        # only seeds if collection is empty, runs silently otherwise
        try:
            count = await self.repo.get_count(self.collection_name, {"is_deleted": {"$ne": True}})
            if count == 0:
                logger.info("power_exchange_parameters is empty, seeding defaults")
                now = datetime.now()
                docs = [
                    {**item, "is_deleted": False, "created_at": now, "updated_at": now}
                    for item in DEFAULT_POWER_EXCHANGE_PARAMETERS
                ]
                await self.repo.insert_many_async(self.collection_name, docs)
        except Exception as e:
            logger.error(f"seed_default_data failed: {e}")

    async def create_parameter(self, parameter_data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            now = datetime.now()
            parameter_data["is_deleted"] = False
            parameter_data["created_at"] = now
            parameter_data["updated_at"] = now
            result = await self.repo.insert_one_async(self.collection_name, parameter_data)
            logger.info(f"inserted power_exchange_parameter: {result.inserted_id}")
            created = await self.repo.find_one_async(
                self.collection_name,
                {"_id": result.inserted_id, "is_deleted": {"$ne": True}}
            )
            if not created:
                raise ValueError("insert succeeded but couldn't fetch the doc back")
            return created
        except Exception as e:
            logger.error(f"create_parameter failed: {e}")
            raise

    async def create_parameters_bulk(self, parameters: List[Dict[str, Any]]) -> Dict[str, Any]:
        try:
            now = datetime.now()
            for param in parameters:
                param["is_deleted"] = False
                param["created_at"] = now
                param["updated_at"] = now
            result = await self.repo.insert_many_async(self.collection_name, parameters)
            logger.info(f"bulk inserted {len(result.inserted_ids)} records")
            return {
                "inserted_ids": [str(i) for i in result.inserted_ids],
                "count": len(result.inserted_ids)
            }
        except Exception as e:
            logger.error(f"create_parameters_bulk failed: {e}")
            raise

    async def get_parameter_by_id(self, parameter_id: str) -> Optional[Dict[str, Any]]:
        try:
            if not ObjectId.is_valid(parameter_id):
                return None
            return await self.repo.find_one_async(
                self.collection_name,
                {"_id": ObjectId(parameter_id), "is_deleted": {"$ne": True}}
            )
        except Exception as e:
            logger.error(f"get_parameter_by_id failed: {e}")
            raise

    async def get_parameter_by_exchange(self, exchange: str) -> List[Dict[str, Any]]:
        try:
            await self.seed_default_data()
            return await self.repo.get_all_async(
                self.collection_name,
                filter={"exchange": exchange.lower(), "is_deleted": {"$ne": True}}
            )
        except Exception as e:
            logger.error(f"get_parameter_by_exchange failed: {e}")
            raise

    async def get_parameter_by_exchange_and_market(self, exchange: str, market: str) -> Optional[Dict[str, Any]]:
        try:
            await self.seed_default_data()
            return await self.repo.find_one_async(
                self.collection_name,
                {"exchange": exchange.lower(), "market": market.lower(), "is_deleted": {"$ne": True}}
            )
        except Exception as e:
            logger.error(f"get_parameter_by_exchange_and_market failed: {e}")
            raise

    async def get_all_parameters(
        self,
        skip: int = 0,
        limit: int = 100,
        sort_by: str = "created_at",
        sort_order: int = -1,
        exchange: Optional[str] = None,
        market: Optional[str] = None
    ) -> Dict[str, Any]:
        try:
            await self.seed_default_data()

            query: Dict[str, Any] = {"is_deleted": {"$ne": True}}

            if exchange:
                query["exchange"] = exchange.lower()

            # market=rtm -> exact match, market=dam -> picks up dam + gdam + hpdam
            if market:
                m = market.lower()
                if m == "rtm":
                    query["market"] = "rtm"
                elif m == "dam":
                    query["market"] = {"$in": ["dam", "gdam", "hpdam"]}

            total = await self.repo.get_count(self.collection_name, query)
            parameters = await self.repo.get_all_async(
                self.collection_name,
                filter=query,
                skip=skip,
                limit=limit,
                sort=[(sort_by, sort_order)]
            )
            return {"parameters": parameters, "total": total, "skip": skip, "limit": limit}
        except Exception as e:
            logger.error(f"get_all_parameters failed: {e}")
            raise

    async def update_parameter(self, parameter_id: str, update_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            if not ObjectId.is_valid(parameter_id):
                return None
            update_data["updated_at"] = datetime.now()
            result = await self.repo.update_one_async(
                self.collection_name,
                {"_id": ObjectId(parameter_id), "is_deleted": {"$ne": True}},
                {"$set": update_data}
            )
            if result.modified_count > 0 or result.matched_count > 0:
                logger.info(f"updated power_exchange_parameter: {parameter_id}")
                return await self.get_parameter_by_id(parameter_id)
            return None
        except Exception as e:
            logger.error(f"update_parameter failed: {e}")
            raise

    async def delete_parameter(self, parameter_id: str, deleted_by: Optional[str] = None) -> bool:
        # soft delete only - we never actually remove docs
        try:
            if not ObjectId.is_valid(parameter_id):
                return False
            payload: Dict[str, Any] = {
                "is_deleted": True,
                "deleted_at": datetime.now(),
                "updated_at": datetime.now()
            }
            if deleted_by:
                payload["deleted_by"] = deleted_by
            result = await self.repo.update_one_async(
                self.collection_name,
                {"_id": ObjectId(parameter_id), "is_deleted": {"$ne": True}},
                {"$set": payload}
            )
            return result.modified_count > 0
        except Exception as e:
            logger.error(f"delete_parameter failed: {e}")
            raise


power_exchange_parameter_repository = PowerExchangeParameterRepository()
