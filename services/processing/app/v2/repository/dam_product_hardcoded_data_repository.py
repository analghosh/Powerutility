# repositories/dam_product_hardcoded_data_repository.py
from typing import List, Dict, Any, Optional
import logging
import asyncio
from concurrent.futures import ThreadPoolExecutor

from .base_repository import GenericRepository
from ...core.config import settings

logger = logging.getLogger(__name__)


class DAMProductHardCodedDataRepository(GenericRepository):
    """
    Repository for DAM product hardcoded data collection.
    Uses standard field names following industry practice.
    """

    def __init__(self):
        super().__init__()
        self.collection_name = settings.MONGODB_DAM_PRODUCT_HARDCODED_DATA_COLLECTION if hasattr(settings, 'MONGODB_DAM_PRODUCT_HARDCODED_DATA_COLLECTION') else "rtm_product_hardCoded_data"
        logger.info(f"DAMProductHardCodedDataRepository initialized with collection: {self.collection_name}")

    def _get_data_sync(self) -> List[Dict[str, Any]]:
        """
        Get all DAM product data, sorted by product_code.
        """
        try:
            pipeline = [
                {"$sort": {"Product_Code": 1}},
                {"$project": {"_id": 0}}
            ]
            result = self.aggregate(self.collection_name, pipeline)
            logger.info(f"Retrieved {len(result)} DAM product records")
            return result
        except Exception as e:
            logger.error(f"Failed to retrieve DAM product data: {str(e)}")
            return []

    async def get_Data(self) -> List[Dict[str, Any]]:
        """
        Get all DAM product hardcoded data asynchronously.
        """
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor() as executor:
            return await loop.run_in_executor(executor, self._get_data_sync)

    def _get_period_sync(self, period_time: str) -> Optional[Dict[str, Any]]:
        """
        Get DAM product data by period time.
        
        Args:
            period_time: Period time in HH:MM format
            
        Returns:
            Document matching the period time
        """
        try:
            filter_query = {"From_Period_Id": period_time}
            projection = {"_id": 0}
            
            result = self.find_one(self.collection_name, filter_query, projection)
            return result
        except Exception as e:
            logger.error(f"Failed to get period data for {period_time}: {str(e)}")
            return None

    async def get_period(self, period_time: str) -> Optional[Dict[str, Any]]:
        """
        Get DAM product data by period time asynchronously.
        
        Args:
            period_time: Period time in HH:MM format
            
        Returns:
            Document matching the period time
        """
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor() as executor:
            return await loop.run_in_executor(executor, self._get_period_sync, period_time)

    async def get_product_by_code(self, product_code: str) -> Optional[Dict[str, Any]]:
        """
        Get product data by product code asynchronously.
        
        Args:
            product_code: Product code to search for
            
        Returns:
            Document matching the product code
        """
        try:
            filter_query = {"Product_Code": product_code}
            projection = {"_id": 0}
            
            result = await asyncio.get_event_loop().run_in_executor(
                None, 
                lambda: self.find_one(self.collection_name, filter_query, projection)
            )
            return result
        except Exception as e:
            logger.error(f"Failed to get product by code {product_code}: {str(e)}")
            return None

    async def get_all_products(self) -> List[Dict[str, Any]]:
        """
        Get all products with basic information asynchronously.
        
        Returns:
            List of product documents
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
            
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self.aggregate(self.collection_name, pipeline)
            )
            return result
        except Exception as e:
            logger.error(f"Failed to get all products: {str(e)}")
            return []