# repositories/dam_product_hardCoded_data_repository.py
from typing import List, Dict, Any, Optional
import logging

from .base_repository import BaseRepository

logger = logging.getLogger(__name__)


class DAMProductHardCodedDataRepository(BaseRepository):
    """
    Repository for the 'dam_product_hardCoded_data' collection.
    Handles dayahead market product hardcoded data operations.
    """

    def __init__(self, collection_name: str = "rtm_product_hardCoded_data"):
        super().__init__(collection_name=collection_name)
        logger.info(f"DAMProductHardCodedDataRepository initialized with collection: {collection_name}")

    # ---- Generic passthrough used by the service layer ----
    async def get_Data(self):
        """
        Get all product data sorted by Product_Code.
        Returns list of product data.
        """
        try:    
            pipeline = [
                {"$sort": {"Product_Code": 1}},
                {"$project": { "_id": 0}}
            ]
            result = await self.collection.aggregate(pipeline).to_list(length=10000)
            logger.info("Successfully retrieved product data")
            return result
        except Exception as e:
            logger.error(f"Failed to retrieve product data from {self.collection_name}: {str(e)}")
            return str(e) # type: ignore
        
    async def get_period(self, period):
        """
        Get data for a specific period.
        """
        try:
            result = await self.collection.find_one({"From_Period_Id": period})
            return result
        except Exception as e:
            logger.error(f"Failed to get period data from {self.collection_name}: {str(e)}")
            return None

    async def get_product_by_code(self, product_code: str) -> Optional[Dict[str, Any]]:
        """
        Get product data by product code.
        """
        try:
            result = await self.collection.find_one({"Product_Code": product_code})
            return result
        except Exception as e:
            logger.error(f"Failed to get product by code from {self.collection_name}: {str(e)}")
            return None

    async def get_all_products(self) -> List[Dict[str, Any]]:
        """
        Get all products with basic information.
        """
        try:
            pipeline = [
                {"$project": {
                    "_id": 0,
                    "Product_Code": 1,
                    "Product_Name": 1,
                    "From_Period_Id": 1,
                    "To_Period_Id": 1
                }},
                {"$sort": {"Product_Code": 1}}
            ]
            result = await self.collection.aggregate(pipeline).to_list(length=10000)
            return result
        except Exception as e:
            logger.error(f"Failed to get all products from {self.collection_name}: {str(e)}")
            return []
