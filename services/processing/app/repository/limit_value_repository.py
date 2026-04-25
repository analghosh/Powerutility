# repositories/dsmoverview_repository.py

from typing import List, Dict, Optional, Any
from .base_repository import BaseRepository
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class limitVallueRepository(BaseRepository):
    def __init__(self, collection_name: str = "limit_values"):
        super().__init__(collection_name=collection_name)
        logger.info(f"limitVallueRepository initialized with collection: {collection_name}")

    async def aggregate(self, pipeline: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Run an aggregation pipeline and return the list of documents.
        """
        logger.debug("limitVallueRepository.aggregate | pipeline=%s", pipeline)
        return await self.collection.aggregate(pipeline).to_list(length=10000)


    async def insert_many(self, documents: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Insert multiple documents into the collection asynchronously.
        Converts schedule_date and insert_date into string 'YYYY-MM-DD'.
        """
        try:    
            if not documents:
                raise ValueError("documents must be a non-empty list")

            cleaned_docs = []
            for i, doc in enumerate(documents):
                if not isinstance(doc, dict):
                    raise TypeError(f"Document at index {i} is not a dict: {type(doc)}")

                # 🔹 Normalize schedule_date into string YYYY-MM-DD
                if "schedule_date" in doc and doc["schedule_date"]:
                    raw_date = str(doc["schedule_date"]).strip()
                    parsed_date = None

                    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%m/%d/%Y", "%m/%d/%Y %H:%M:%S"):
                        try:
                            parsed_date = datetime.strptime(raw_date, fmt)
                            break
                        except ValueError:
                            continue

                    if parsed_date is None:
                        raise ValueError(f"Unsupported date format: {raw_date}")

                    doc["schedule_date"] = parsed_date.strftime("%Y-%m-%d")

                # 🔹 Always add insert_date in same format
                doc["inserted_date"] = datetime.now().strftime("%Y-%m-%d")

                cleaned_docs.append(doc)

            logger.info(f"Inserting {len(cleaned_docs)} documents into collection...")

            result = await self.collection.insert_many(cleaned_docs, ordered=False)
            inserted_ids = [str(_id) for _id in result.inserted_ids]

            logger.info("Bulk insert successful")
            return {"data": True, "inserted_ids": inserted_ids}

        except Exception as e:
            logger.error("Failed to bulk insert records: %s", str(e))
            return {"data": False, "error": str(e)}
    
    
    

    async def fetch_all(self, schedule_date) -> Dict[str, Any]:
        """
        Fetch documents with pagination.
        """
        try:
            
            # Exclude _id field
            pipeline = [
    {
        "$match": {
            "schedule_date": schedule_date
        }
    },
    {
        "$group": {
            "_id": {
                "schedule_date": "$schedule_date",
                "block": "$block"
            },
            "inserted_date": {"$first": "$inserted_date"},   # or $max if you want latest
            "cronJob_time": {"$first": "$cronJob_time"},     # same here
            "items": {
                "$push": {
                    "ID": "$ID",
                    "market": "$market",
                    "category": "$category",
                    "area": "$area",
                    "values": "$values",
                    "unit": "$unit"
                }
            }
        }
    },
    {
        "$project": {
            "_id": 0,
            "schedule_date": "$_id.schedule_date",
            "block": "$_id.block",
            "inserted_date": 1,
            "cronJob_time": 1,
            "items": 1
        }
    },
    {
        "$sort": { "block": 1 }
    }
]

            cursor = self.collection.aggregate(pipeline)
            data = await cursor.to_list(length=10000)


            return {
                    "data": data,
                    
                }

        except Exception as e:
            logger.error("Failed to fetch paginated records: %s", str(e))
            return {"data": [], "error": str(e)}
        
    
    async def get_data(self, yesterday, tomorrow,market, category, area ):
        try:
            """
        Fetch documents with pagination.
        """
            
            # Exclude _id field
            pipeline = [
    {
        "$match": {
            "schedule_date": {"$gte": yesterday, "$lte": tomorrow},
            "market": market,
            "category": category,
            "area": area
        }
    },
    {
        "$sort": {"schedule_date": 1}
    }
]

            cursor = self.collection.aggregate(pipeline)


            return await cursor.to_list(length=10000)
        except Exception as e:
            logger.error("Failed to fetch paginated records: %s", str(e))
            return {"data": [], "error": str(e)}
        
    async def get_data_byID(self,schedule_date, ID, market, category, area ):
        try:
            """
        Fetch documents with pagination.
        """
            
            # Exclude _id field
            pipeline = [
    {
        "$match": {
            "schedule_date":  schedule_date,
            "ID": ID,
            # "market": market,
            # "category": category,
            # "area": area
        }
    },
    {
        "$project": {
            "_id": 0,
        }
    },
    {
        "$sort": {"schedule_date": 1}
    }
]
            print(pipeline)
            cursor = self.collection.aggregate(pipeline)


            return await cursor.to_list(length=10000)
        except Exception as e:
            logger.error("Failed to fetch paginated records: %s", str(e))
            return {"data": [], "error": str(e)}