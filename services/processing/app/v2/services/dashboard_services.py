"""
Dashboard V2 Service
Enhanced dashboard service with business logic and repository integration
"""

import logging
from copy import deepcopy
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta
from fastapi import HTTPException


# Update these import paths to match your project structure
from ..repository.dashboard_repository import dashboard_repository
from libs.helpers.timeblock_helper import ist_now, block_from_hhmm
from libs.utils.date_filters import validate_schedule_date

from ...core.config import settings

logger = logging.getLogger(__name__)


class DashboardService:
    """
    Service layer for Dashboard V2 operations.
    Handles business logic and data processing.
    """

    def __init__(self):
        self.dashboard_repository = dashboard_repository
        self.plant_master = settings.MONGODB_PLANT_MASTER_COLLECTION

    DEFAULT_POWER_UNIT = "MW"
    DEFAULT_PRICE_UNIT = "RS_PER_MWH"
    DEFAULT_FREQUENCY_UNIT = "HZ"
    DEFAULT_CURRENCY_UNIT = "RS"

    POWER_CONVERSION_FACTORS = {
        "MW": 1.0,
        "KW": 1000.0,
        "GW": 0.001,
        "KWH": 1000.0,  # Treat 1 MW as 1000 kWh for display equivalence
    }

    PRICE_CONVERSION_FACTORS = {
        "RS_PER_MWH": 10.0,      # Paise/kWh → Rs/MWh
        "RS_PER_KWH": 0.01,      # Paise → Rupees
        "PAISE_PER_KWH": 1.0,    # Already Paise/kWh
    }
    FREQUENCY_CONVERSION_FACTORS = {
        "HZ": 1.0,
        "KHZ": 0.001,
    }
    CURRENCY_CONVERSION_FACTORS = {
        "RS": 1.0,
        "PAISE": 100.0,
    }
    # Currency scale factors for converting FROM database format (lakhs) TO requested format
    # Database stores net_dsm_amount and total_net_dsm_amount in LAKHS
    CURRENCY_SCALE_FACTORS = {
        "UNITS": 1.0,        # Already in rupees
        "THOUSANDS": 0.001,  # Rupees → thousands
        "LAKHS": 0.00001,    # Rupees → lakhs
        "CRORES": 0.0000001, # Rupees → crores
    }

    POWER_SECTIONS = ("demand", "schedule_drawal", "od_ud", "peak_demand")
    PRICE_SECTIONS = ("mcp", "acp", "dsm_rate")
    CURRENCY_SECTIONS = ("net_dsm_amount", "total_net_dsm_amount")
    FREQUENCY_SECTIONS = ("frequency",)

    SECTION_DEFAULT_UNITS = {
        "demand": DEFAULT_POWER_UNIT,
        "schedule_drawal": DEFAULT_POWER_UNIT,
        "od_ud": DEFAULT_POWER_UNIT,
        "peak_demand": DEFAULT_POWER_UNIT,
        "mcp": DEFAULT_PRICE_UNIT,
        "acp": "PAISE_PER_KWH",
        "dsm_rate": DEFAULT_PRICE_UNIT,
        "net_dsm_amount": DEFAULT_CURRENCY_UNIT,
        "total_net_dsm_amount": DEFAULT_CURRENCY_UNIT,
        "frequency": DEFAULT_FREQUENCY_UNIT,
    }

    async def get_dashboard_home_data(
        self,
        schedule_date: Optional[str] = None,
        unit_overrides: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """
        Get dashboard home data, always centered on the current time block.
        If a schedule_date is provided, it will be centered on block 96 of that day.
        """
        try:
            now_ist = ist_now()
            today_str = now_ist.date().isoformat()

            # If a specific date is requested AND it's a past date, we show the end of that day.
            # Otherwise (today or no date), we use the current real-time date and block.
            if schedule_date and validate_schedule_date(schedule_date) and schedule_date != today_str:
                effective_date_str = schedule_date
                current_block = 96 # Show the full day for historical queries
                logger.info(f"Using user-provided historical date: {effective_date_str}, centering on Block 96")
            else:
                effective_date_str = today_str
                current_block = block_from_hhmm(now_ist.strftime("%H:%M"))
                # Ensure current_block is always in valid range (1-96), never 0
                current_block = max(1, min(96, current_block))
                logger.info(f"Using current time. Date: {effective_date_str}, Block: {current_block}")

            # Get dashboard data from repository
            dashboard_data = await self.dashboard_repository.get_dashboard_data(
                schedule_date_str=effective_date_str,
                current_block=current_block
            )

            # Process and enhance the data
            processed_data = await self._process_dashboard_data(
                dashboard_data=dashboard_data,
                schedule_date=effective_date_str,
                current_block=current_block,
                unit_overrides=unit_overrides,
            )

            supply_last_updated_at = await self._get_last_updated_at(collection_name=settings.MONGODB_SUPPLY_SERVE_COLLECTION)
            demand_last_updated_at = await self._get_last_updated_at(collection_name=settings.MONGODB_DEMAND_SERVE_COLLECTION)

            # Safely calculate max updated_at, ignoring None values
            timestamps = [ts for ts in [supply_last_updated_at, demand_last_updated_at] if ts]
            max_updated_at = max(timestamps) if timestamps else None
            
            processed_data["last_updated_at"] = max_updated_at

            logger.info(f"Successfully processed dashboard home data for {effective_date_str}")
            return processed_data

        except Exception as e:
            logger.error(f"Error processing dashboard home data - schedule_date: {schedule_date}, error: {str(e)}")
            raise

    async def _get_last_updated_at(self, collection_name: str) -> Optional[str]:
        try:
            return await self.dashboard_repository.get_max_updated_at(collection_name=collection_name)
        except Exception as e:
            logger.exception(f"Failed to get max updated_at from {collection_name}")
            return None
    async def _process_dashboard_data(
        self,
        dashboard_data: Dict[str, Any],
        schedule_date: str,
        current_block: int,
        unit_overrides: Optional[Dict[str, str]],
    ) -> Dict[str, Any]:
        """
        Process and enhance dashboard data with business logic.
        """
        processed_data = {
            "schedule_date": schedule_date,
            "current_block": current_block,
            "timestamp": datetime.now().isoformat(),
            **dashboard_data,
        }
        # Ensure mcp and acp remain untouched if defaults are RS_PER_MWH.
        # But we want to explicitly prevent them from being converted. We can enforce unit_overrides
        if unit_overrides is None:
            unit_overrides = {}
        # The database is already in RS_PER_MWH, if we pass RS_PER_MWH, it will multiply by 10.0
        # By setting it to a unit that maps to 1.0 (like PAISE_PER_KWH), we skip scaling!
        unit_overrides["mcp"] = "PAISE_PER_KWH"
        unit_overrides["acp"] = "PAISE_PER_KWH"

        return self._apply_unit_conversions(
            payload=processed_data,
            unit_overrides=unit_overrides,
        )

    async def get_dashboard_capacity_chart(self) -> Dict[str, Any]:
        """
        Returns hierarchical sunburst data with colors:
        Total Capacity -> c_s -> category -> plant_name (with ppa)
        Reads from `plant_master` where:
        - c_s (central/state/other) is used directly
        - capacity comes from `ppa` (converted to double)
        """
        try:
            pipeline = [
                # Convert ppa safely
                {
                    "$addFields": {
                        "ppa_num": {
                            "$convert": {
                                "input": "$ppa",
                                "to": "double",
                                "onError": 0.0,
                                "onNull": 0.0
                            }
                        }
                    }
                },

                # 1) plant level
                {
                    "$group": {
                        "_id": {
                            "c_s": "$c_s",
                            "category": "$category",
                            "plant_name": "$plant_name"
                        },
                        "ppa": {"$sum": "$ppa_num"}
                    }
                },

                # 2) category level
                {
                    "$group": {
                        "_id": {
                            "c_s": "$_id.c_s",
                            "category": "$_id.category"
                        },
                        "plants": {
                            "$push": {
                                "plant_name": "$_id.plant_name",
                                "ppa": "$ppa"
                            }
                        },
                        "category_capacity": {"$sum": "$ppa"}
                    }
                },

                # 3) c_s level
                {
                    "$group": {
                        "_id": "$_id.c_s",
                        "categories": {
                            "$push": {
                                "category": "$_id.category",
                                "capacity": "$category_capacity",
                                "children": "$plants"
                            }
                        },
                        "c_s_capacity": {"$sum": "$category_capacity"}
                    }
                },

                # 4) root
                {
                    "$group": {
                        "_id": None,
                        "total_capacity": {"$sum": "$c_s_capacity"},
                        "c_s_groups": {
                            "$push": {
                                "c_s": "$_id",
                                "capacity": "$c_s_capacity",
                                "children": "$categories"
                            }
                        }
                    }
                },

                # 5) shape final
                {
                    "$project": {
                        "_id": 0,
                        "name": {"$literal": "Total Capacity"},
                        "capacity": "$total_capacity",
                        "children": "$c_s_groups"
                    }
                }
            ]

            result = list(await self.dashboard_repository.repository.aggregate_async(self.plant_master, pipeline))
            if not result:
                raise HTTPException(status_code=404, detail="No capacity data found")

            root = result[0]

            # Color scheme by c_s
            cs_colors = {
                "central": "#34A853",
                "state": "#FF9900"
            }

            def lighten_color(base_hex: str, factor: float) -> str:
                base_hex = base_hex.lstrip('#')
                r, g, b = int(base_hex[:2], 16), int(base_hex[2:4], 16), int(base_hex[4:], 16)
                r = min(int(r + (255 - r) * factor), 255)
                g = min(int(g + (255 - g) * factor), 255)
                b = min(int(b + (255 - b) * factor), 255)
                return f"#{r:02X}{g:02X}{b:02X}"

            for cs_group in root['children']:
                cs_value = (cs_group.get('c_s') or "other").lower()
                cs_group['color'] = cs_colors.get(cs_value, "#CCCCCC")

                num_categories = len(cs_group.get('children', []))
                for c_idx, cat in enumerate(cs_group.get('children', [])):
                    factor = 0.2 + 0.6 * c_idx / max(1, num_categories - 1)
                    cat['color'] = lighten_color(cs_group['color'], factor)

                    num_plants = len(cat.get('children', []))
                    for p_idx, plant in enumerate(cat.get('children', [])):
                        factor_p = 0.3 + 0.4 * p_idx / max(1, num_plants - 1)
                        plant['color'] = lighten_color(cat['color'], factor_p)

            return root

        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    async def get_demand_raw_v2_data(
        self,
        filters: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """
        Fetches raw demand data from the V2 collection based on provided filters.
        """
        try:
            query = {}
            # Date range filter
            if filters.get("date_from") or filters.get("date_to"):
                date_query = {}
                if filters.get("date_from"):
                    start_date = datetime.strptime(filters["date_from"], "%Y-%m-%d")
                    date_query["$gte"] = start_date
                if filters.get("date_to"):
                    end_date = datetime.strptime(filters["date_to"], "%Y-%m-%d") + timedelta(days=1, microseconds=-1)
                    date_query["$lte"] = end_date
                query["scheduled_at"] = date_query

            # Time block range filter
            if filters.get("from_block") or filters.get("to_block"):
                time_block_query = {}
                if filters.get("from_block"):
                    time_block_query["$gte"] = filters["from_block"]
                if filters.get("to_block"):
                    time_block_query["$lte"] = filters["to_block"]
                query["time_block"] = time_block_query

            # Type and Zone filters
            if filters.get("type"):
                query["type"] = filters["type"]
            if filters.get("zone"):
                query["zone"] = filters["zone"]

            # Min/Max filters for numerical fields
            numerical_fields = [
                "actual", "drawal", "mape", "mpe", "Raw_Frequency", "Schedule",
                "comp_act_demand", "comp_udm", "dsm_rate", "forecast",
                "forecasted_frequency", "net_dsm_amount", "rostering"
            ]
            for field in numerical_fields:
                min_val = filters.get(f"{field.lower()}_min")
                max_val = filters.get(f"{field.lower()}_max")
                if min_val is not None or max_val is not None:
                    field_query = {}
                    if min_val is not None:
                        field_query["$gte"] = min_val
                    if max_val is not None:
                        field_query["$lte"] = max_val
                    query[field] = field_query

            # Updated_at range filter
            if filters.get("updated_from") or filters.get("updated_to"):
                updated_at_query = {}
                if filters.get("updated_from"):
                    updated_at_query["$gte"] = filters["updated_from"]
                if filters.get("updated_to"):
                    updated_at_query["$lte"] = filters["updated_to"]
                query["updated_at"] = updated_at_query

            # Projection (include/exclude fields)
            projection = None
            if filters.get("include"):
                include_fields = {f.strip(): 1 for f in filters["include"].split(",") if f.strip()}
                if "_id" not in include_fields: # _id is included by default unless explicitly excluded
                    include_fields["_id"] = 0
                projection = include_fields
            elif filters.get("exclude"):
                exclude_fields = {f.strip(): 0 for f in filters["exclude"].split(",") if f.strip()}
                projection = exclude_fields

            # Sorting
            sort_params = None
            if filters.get("sort"):
                sort_params = []
                for s_param in filters["sort"].split(","):
                    field, direction = s_param.strip().split(":")
                    sort_params.append((field, 1 if direction.lower() == "asc" else -1))

            # Pagination
            skip = filters.get("skip", 0)
            limit = filters.get("limit")

            logger.info(f"Fetching demand raw V2 data with query: {query}, projection: {projection}, sort: {sort_params}, skip: {skip}, limit: {limit}")
            collection_name = self.dashboard_repository.demand_serve
            data = await self.dashboard_repository.get_demand_raw_v2(
                collection_name= collection_name,
                query=query,
                projection=projection,
                sort=sort_params,
                skip=skip,
                limit=limit
            )
            return data
        except Exception as e:
            logger.error(f"Error in get_demand_raw_v2_data service: {str(e)}")
            raise

    def _apply_unit_conversions(
        self,
        payload: Dict[str, Any],
        unit_overrides: Optional[Dict[str, str]],
    ) -> Dict[str, Any]:
        """
        Apply dynamic unit conversions requested by the client.
        Converts only known numeric sections, leaving raw data intact otherwise.
        """
        if not payload:
            return payload

        converted = deepcopy(payload)
        overrides = unit_overrides or {}

        resolved_units = {}
        for section, default_unit in self.SECTION_DEFAULT_UNITS.items():
            raw_value = overrides.get(section) if overrides else None
            resolved_units[section] = self._normalize_unit(raw_value or default_unit)

        power_factors = {
            section: self.POWER_CONVERSION_FACTORS.get(resolved_units[section])
            for section in self.POWER_SECTIONS
        }
        price_factors = {
            section: self.PRICE_CONVERSION_FACTORS.get(resolved_units[section])
            for section in self.PRICE_SECTIONS
        }
        # ACP is stored in Rs/MWh; to show in Paise/kWh we divide by 10 (factor 0.1)
        if resolved_units.get("acp") == "PAISE_PER_KWH":
            price_factors["acp"] = 0.1
        currency_factors = {}
        for section in self.CURRENCY_SECTIONS:
            unit_factor = self.CURRENCY_CONVERSION_FACTORS.get(resolved_units[section])
            # Database stores values in LAKHS, so default scale is LAKHS (no conversion)
            # If user wants UNITS (rupees), we multiply by 100,000
            scale_value = overrides.get(f"{section}_scale") if overrides else None
            scale_key = self._normalize_unit(scale_value) if scale_value else "LAKHS"
            scale_factor = self.CURRENCY_SCALE_FACTORS.get(scale_key, 1.0)
            if unit_factor is not None and scale_factor is not None:
                currency_factors[section] = unit_factor * scale_factor
            else:
                currency_factors[section] = unit_factor
        frequency_factors = {
            section: self.FREQUENCY_CONVERSION_FACTORS.get(resolved_units[section])
            for section in self.FREQUENCY_SECTIONS
        }

        self._convert_power_sections(converted, power_factors)
        self._convert_price_sections(converted, price_factors)
        self._convert_currency_sections(converted, currency_factors)
        self._convert_frequency_sections(converted, frequency_factors)

        converted["unit_metadata"] = resolved_units
        for section in self.CURRENCY_SECTIONS:
            scale_key = f"{section}_scale"
            scale_value = overrides.get(scale_key) if overrides else None
            # Default scale is LAKHS since database stores values in lakhs
            converted["unit_metadata"][scale_key] = (
                self._normalize_unit(scale_value) if scale_value else "LAKHS"
            )
        return converted

    def _normalize_unit(self, value: str) -> str:
        return str(value).upper()

    def _convert_value(self, value: Any, factor: float, precision: int = 2) -> Any:
        if value in (None, "", float("inf"), float("-inf")):
            return value
        try:
            return round(float(value) * factor, precision)
        except (TypeError, ValueError):
            return value

    def _convert_series(
        self,
        series: Optional[List[Dict[str, Any]]],
        field: str,
        factor: float,
        precision: int,
    ) -> None:
        if not isinstance(series, list):
            return
        for item in series:
            if isinstance(item, dict) and field in item:
                item[field] = self._convert_value(item.get(field), factor, precision)

    def _convert_power_sections(self, payload: Dict[str, Any], factors: Dict[str, Optional[float]]) -> None:
        precision = 2
        demand_section = payload.get("demand")
        factor = factors.get("demand")
        if isinstance(demand_section, dict) and factor is not None and factor != 1.0:
            demand_section["current_demand"] = self._convert_value(
                demand_section.get("current_demand"), factor, precision
            )
            self._convert_series(demand_section.get("data"), "demand", factor, precision)

        schedule_section = payload.get("schedule_drawal")
        factor = factors.get("schedule_drawal")
        if isinstance(schedule_section, dict) and factor is not None and factor != 1.0:
            for key in ("inter_state", "intra_state"):
                schedule_section[key] = self._convert_value(
                    schedule_section.get(key), factor, precision
                )
            self._convert_series(
                schedule_section.get("inter_state_data"), "data", factor, precision
            )
            self._convert_series(
                schedule_section.get("intra_state_data"), "data", factor, precision
            )

        od_ud_section = payload.get("od_ud")
        factor = factors.get("od_ud")
        if isinstance(od_ud_section, dict) and factor is not None and factor != 1.0:
            od_ud_section["current_od_ud"] = self._convert_value(
                od_ud_section.get("current_od_ud"), factor, precision
            )
            self._convert_series(od_ud_section.get("data"), "od_ud", factor, precision)

        peak_section = payload.get("peak_demand")
        factor = factors.get("peak_demand")
        if isinstance(peak_section, dict) and factor is not None and factor != 1.0:
            overall = peak_section.get("overall_peak_demand_31_days")
            if isinstance(overall, dict):
                overall["peak_demand"] = self._convert_value(
                    overall.get("peak_demand"), factor, precision
                )
            self._convert_series(
                peak_section.get("daily_peak_data"), "peak_demand", factor, precision
            )

    def _convert_price_sections(self, payload: Dict[str, Any], factors: Dict[str, Optional[float]]) -> None:
        precision = 4
        mcp_wrapper = payload.get("mcp")
        factor = factors.get("mcp")
        if isinstance(mcp_wrapper, dict) and factor is not None and factor != 1.0:
            mcp_section = mcp_wrapper.get("mcp")
            if isinstance(mcp_section, dict):
                for key in ("rtm", "dam"):
                    mcp_section[key] = self._convert_value(
                        mcp_section.get(key), factor, precision
                    )
                self._convert_series(
                    mcp_section.get("rtm_data"), "data", factor, precision
                )
                self._convert_series(
                    mcp_section.get("dam_data"), "data", factor, precision
                )

        acp_wrapper = payload.get("acp")
        factor = factors.get("acp")
        if isinstance(acp_wrapper, dict) and factor is not None and factor != 1.0:
            acp_section = acp_wrapper.get("acp")
            if isinstance(acp_section, dict):
                acp_section["current_acp"] = self._convert_value(
                    acp_section.get("current_acp"), factor, precision
                )
                self._convert_series(
                    acp_section.get("data"), "acp", factor, precision
                )

        dsm_rate_section = payload.get("dsm_rate")
        factor = factors.get("dsm_rate")
        if isinstance(dsm_rate_section, dict) and factor is not None and factor != 1.0:
            dsm_rate_section["current_dsm_rate"] = self._convert_value(
                dsm_rate_section.get("current_dsm_rate"), factor, precision
            )
            self._convert_series(
                dsm_rate_section.get("data"), "dsm_rate", factor, precision
            )

    def _convert_currency_sections(self, payload: Dict[str, Any], factors: Dict[str, Optional[float]]) -> None:
        precision = 2
        net_section = payload.get("net_dsm_amount")
        factor = factors.get("net_dsm_amount")
        if isinstance(net_section, dict) and factor is not None and factor != 1.0:
            net_section["current_net_dsm_amount"] = self._convert_value(
                net_section.get("current_net_dsm_amount"), factor, precision
            )
            self._convert_series(
                net_section.get("data"), "net_dsm_amount", factor, precision
            )

        total_section = payload.get("total_net_dsm_amount")
        factor = factors.get("total_net_dsm_amount")
        if isinstance(total_section, dict) and factor is not None and factor != 1.0:
            total_section["current_total_net_dsm_amount"] = self._convert_value(
                total_section.get("current_total_net_dsm_amount"), factor, precision
            )
            self._convert_series(
                total_section.get("data"), "total_net_dsm_amount", factor, precision
            )

    def _convert_frequency_sections(self, payload: Dict[str, Any], factors: Dict[str, Optional[float]]) -> None:
        precision = 4
        freq_section = payload.get("frequency")
        factor = factors.get("frequency")
        if isinstance(freq_section, dict) and factor is not None and factor != 1.0:
            freq_section["current_frequency"] = self._convert_value(
                freq_section.get("current_frequency"), factor, precision
            )
            self._convert_series(freq_section.get("data"), "frequency", factor, precision)
    
    async def get_temp_demand_raw_v2_data(
        self,
        filters: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """
        Fetches raw demand data from the V2 collection based on provided filters.
        """
        try:
            query = {}
            # Date range filter
            if filters.get("date_from") or filters.get("date_to"):
                date_query = {}
                if filters.get("date_from"):
                    start_date = datetime.strptime(filters["date_from"], "%Y-%m-%d")
                    date_query["$gte"] = start_date
                if filters.get("date_to"):
                    end_date = datetime.strptime(filters["date_to"], "%Y-%m-%d") + timedelta(days=1, microseconds=-1)
                    date_query["$lte"] = end_date
                query["scheduled_at"] = date_query
            # Time block range filter
            if filters.get("from_block") or filters.get("to_block"):
                time_block_query = {}
                if filters.get("from_block"):
                    time_block_query["$gte"] = filters["from_block"]
                if filters.get("to_block"):
                    time_block_query["$lte"] = filters["to_block"]
            numerical_fields = [
                "drawal",  "forecasted_frequency",  "rostering"
            ]
            for field in numerical_fields:
                min_val = filters.get(f"{field.lower()}_min")
                max_val = filters.get(f"{field.lower()}_max")
                if min_val is not None or max_val is not None:
                    field_query = {}
                    if min_val is not None:
                        field_query["$gte"] = min_val
                    if max_val is not None:
                        field_query["$lte"] = max_val
                    query[field] = field_query

            # Updated_at range filter
            if filters.get("updated_from") or filters.get("updated_to"):
                updated_at_query = {}
                if filters.get("updated_from"):
                    updated_at_query["$gte"] = filters["updated_from"]
                if filters.get("updated_to"):
                    updated_at_query["$lte"] = filters["updated_to"]
                query["updated_at"] = updated_at_query

            # Projection (include/exclude fields)
            projection = None
            if filters.get("include"):
                include_fields = {f.strip(): 1 for f in filters["include"].split(",") if f.strip()}
                if "_id" not in include_fields: # _id is included by default unless explicitly excluded
                    include_fields["_id"] = 0
                projection = include_fields
            elif filters.get("exclude"):
                exclude_fields = {f.strip(): 0 for f in filters["exclude"].split(",") if f.strip()}
                projection = exclude_fields

            # Sorting
            sort_params = None
            if filters.get("sort"):
                sort_params = []
                for s_param in filters["sort"].split(","):
                    field, direction = s_param.strip().split(":")
                    sort_params.append((field, 1 if direction.lower() == "asc" else -1))

            # Pagination
            skip = filters.get("skip", 0)
            limit = filters.get("limit")

            logger.info(f"Fetching demand raw V2 data with query: {query}, projection: {projection}, sort: {sort_params}, skip: {skip}, limit: {limit}")
            collection_name = self.dashboard_repository.temp_demand_serve
            data = await self.dashboard_repository.get_demand_raw_v2(
                collection_name= collection_name,
                query=query,
                projection=projection,
                sort=sort_params,
                skip=skip,
                limit=limit
            )
            return data
        except Exception as e:
            logger.error(f"Error in get_demand_raw_v2_data service: {str(e)}")
            raise

# Global instance
dashboard_service = DashboardService()
