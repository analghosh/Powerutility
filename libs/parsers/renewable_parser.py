"""
Renewable Energy Parser
Parses renewable energy data (OA_REMC) from Grid India formatted JSON.
"""

import json
import logging
import datetime
from typing import Dict, Any, List, Optional

from libs.parsers.base_parser import BaseParser
from libs.models.provider_models import ProviderEntity

logger = logging.getLogger(__name__)


class RenewableParser(BaseParser):
    """
    Parser for Grid India Renewable (OA_REMC) JSON data.
    """

    def get_parser_type(self) -> str:
        """Get the parser type identifier"""
        return "renewable"

    def parse(self, content: str, provider: Optional[ProviderEntity] = None, **kwargs) -> List[Dict[str, Any]]:
        """
        Parse Renewable JSON content into flattened serve records.

        Args:
            content: Raw JSON content string
            provider: Provider entity (optional)
            **kwargs: Additional parsing parameters

        Returns:
            List[Dict[str, Any]]: Flattened supply data records
        """
        try:
            # Parse JSON content
            if isinstance(content, str):
                data = json.loads(content)
            else:
                data = content

            if "ResponseBody" not in data:
                logger.warning("ResponseBody not found in content structure")
                return []

            response_body = data["ResponseBody"]
            
            # Extract root level metadata
            date_str = response_body.get("Date")
            full_schd_revision_no = response_body.get("FullSchdRevisionNo")
            
            # Format date to ISO
            try:
                if date_str:
                    date_obj = datetime.datetime.strptime(date_str, "%d-%m-%Y")
                    date_str = date_obj.isoformat()
            except ValueError:
                logger.warning("Could not parse date: %s", date_str)

            # Determine Data Type (DAF vs IDF) based on Revision No
            # RevNo <= 5 -> DAF (Day Ahead Forecast/Final)
            # RevNo > 5 -> IDF (Intra Day Forecast)
            data_type = "DAF"
            try:
                rev_no_int = int(full_schd_revision_no)
                if rev_no_int > 5:
                    data_type = "IDF"
            except (ValueError, TypeError):
                logger.warning("Invalid FullSchdRevisionNo: %s, defaulting to DAF", full_schd_revision_no)

            group_list = response_body.get("GroupWiseDataList", [])
            raw_data_map = {}
            raw_data = []

            for group in group_list:
                full_schd_list = group.get("FullschdList", [])
                
                for item in full_schd_list:
                    # Filter for OA_REMC only
                    if item.get("EnergyScheduleTypeName") != "OA_REMC":
                        continue

                    plant_id = item.get("SellerAcronym")
                    
                    full_schd_data = item.get("FullScheduleData", {})
                    oa_full_schd_data = full_schd_data.get("OAFullScheduleJsonData", {})
                    
                    if not oa_full_schd_data:
                        continue

                    # Extract Volume using TotalDrw... keys
                    vol_list = oa_full_schd_data.get("TotalDrwBoundrySchdAmt") or \
                               oa_full_schd_data.get("TotalDrwBoundarySchdAmount") or \
                               oa_full_schd_data.get("TotalDrwBoundrySchdAmount") or \
                               oa_full_schd_data.get("TotalDrwBoundarySchdAmt", [])
                    
                    # Extract SG Before Losses using TotalReg... keys
                    bl_list = oa_full_schd_data.get("TotalRegBoundrySchdAmt") or \
                              oa_full_schd_data.get("TotalRegBoundarySchdAmount") or \
                              oa_full_schd_data.get("TotalRegBoundrySchdAmount") or \
                              oa_full_schd_data.get("TotalRegBoundarySchdAmt", [])

                    # Pre-process aggregation
                    if not vol_list and not bl_list:
                        continue
                    
                    # Iterate over 96 blocks and aggregate
                    for idx in range(96):
                        volume = 0.0
                        sg_beforelosses = 0.0
                        
                        # Volume
                        if idx < len(vol_list):
                            try:
                                val = vol_list[idx]
                                if val is not None:
                                    volume = float(val)
                            except (ValueError, TypeError):
                                pass
                        
                        # SG Before Losses
                        if idx < len(bl_list):
                            try:
                                val = bl_list[idx]
                                if val is not None:
                                    sg_beforelosses = float(val)
                            except (ValueError, TypeError):
                                pass

                        # Create a unique key for aggregation
                        # Key: (plant_id, time_block)
                        key = (plant_id, idx + 1)

                        if key not in raw_data_map:
                            raw_data_map[key] = {
                                "scheduled_at": date_str,
                                "time_block": idx + 1,
                                "plant_id": plant_id,
                                "volume": 0.0,
                                "schedule_volume": 0.0,
                                "sg_beforelosses": 0.0,
                                "revision_no": full_schd_revision_no,
                                "type": data_type 
                            }
                        
                        # Aggregate values
                        raw_data_map[key]["volume"] += volume
                        raw_data_map[key]["schedule_volume"] += volume
                        raw_data_map[key]["sg_beforelosses"] += sg_beforelosses

            # Convert aggregated map to list
            raw_data = list(raw_data_map.values())

            logger.info("Parsed Renewable JSON: created %d records", len(raw_data))
            return raw_data

        except Exception as e:
            logger.error("Error parsing Renewable content: %s", e, exc_info=True)
            raise ValueError(f"Renewable parsing failed: {str(e)}") from e

    def validate_content(self, content: str) -> bool:
        """
        Validate if content looks like Grid India JSON
        """
        try:
            if isinstance(content, str):
                data = json.loads(content)
            else:
                data = content
            
            if "ResponseBody" in data:
                 return True
            return False
        except Exception:
            return False
