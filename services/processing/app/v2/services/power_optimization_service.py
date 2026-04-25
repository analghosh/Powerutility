"""
Power Optimization Service
Aggregates demand, generation, IEX market, and plant data for a unified API.
"""
import logging
from typing import List, Dict, Any, Optional, cast
from datetime import datetime
import asyncio
import time

from ..repository.demand_serve_repository import DemandServeRepository
from ..repository.supply_serve_repository import SupplyServeRepository
from ..repository.exchange_repository import ExchangeRepository
from ..repository.master_plants_repository import MasterPlantsRepository
from ..services.limit_values_services import limit_service

logger = logging.getLogger(__name__)

# Thread pool for running synchronous repository methods


class PowerOptimizationService:
    def __init__(self):
        self.demand_repo = DemandServeRepository()
        self.supply_repo = SupplyServeRepository()
        self.exchange_repo = ExchangeRepository()
        self.plants_repo = MasterPlantsRepository()
        self.limit_service = limit_service

    async def get_power_optimization_input(
        self, 
        dates: List[str], 
        filter_type: str = "optimization",
        exchange: Optional[List[str]] = None,
        market_type: Optional[List[str]] = None,
        forecast_market: Optional[str] = None,
        demand_type: str = "IDF"
    ) -> Dict[str, Any]:
        """
        Fetch and aggregate data for power optimization API.
        Delegates to specific methods based on filter_type.
        """
        if filter_type == "forecast":
            return await self._get_forecast_data(dates, exchange, market_type, forecast_market, demand_type)
        else:
            return await self._get_optimization_data(dates, demand_type)

    async def _get_forecast_data(
        self, 
        dates: List[str],
        exchange: Optional[List[str]],
        market_type: Optional[List[str]],
        forecast_market: Optional[str],
        demand_type: str
    ) -> Dict[str, Any]:
        sorted_dates = sorted(dates)
        overall_start = time.time()
        
        result = {
            "meta": self._build_meta(sorted_dates),
            "dates": [],
            "plants": [], # Not needed in forecast
            "marginal_costs": [] # Not needed in forecast
        }
        
        # Determine DB filter based on input params
        market_filters = []
        if exchange and market_type:
            # All combinations
            for e in exchange:
                for m in market_type:
                    market_filters.append({"exchange": e, "market": m})
        elif exchange:
            # Filter by exchange only (all markets)
            for e in exchange:
                market_filters.append({"exchange": e})
        elif market_type:
            # Filter by market only (all exchanges)
            for m in market_type:
                market_filters.append({"market": m})
        elif forecast_market:
            # Fallback to forecast_market if no explicit list
            # Default behavior if not defined?
            # User previously had iex_market_data logic.
            market_filters.append({"market": forecast_market})
            # Should we restrict to IEX by default? OR let it match any?
            # "iex_market_data" implied IEX. But now general.
            # Let's assume forecast_market is a specific market on ANY exchange if not specified.
        else:
             # No filters -> Fetch everything? Or empty?
             pass 
             
        logger.info(f"Forecast mode. Market Filters: {market_filters}")


        async def process_date_refined(target_date: str):
             logger.info(f"Processing forecast date: {target_date}")

             # Fetch raw demand
             d_future = self.demand_repo.get_demand_power_optimization(
                 target_date, "forecast", demand_type
             )

             e_future = self.exchange_repo.get_exchange_power_optimization(target_date, market_filters)

             raw_demand, exchange_data = await asyncio.gather(d_future, e_future)
             
             # Process Demand
             demand_blocks = []
             freq_blocks = []
             if raw_demand:
                 for d in raw_demand:
                     b = d.get("time_block")
                     demand_blocks.append({
                         "block": b,
                         "demand_mw": d.get("comp_udm", 0)
                     })
                     freq_blocks.append({
                         "block": b,
                         "frequency": d.get("frequency", 0)
                     })
                 demand_blocks.sort(key=lambda x: x["block"])
                 freq_blocks.sort(key=lambda x: x["block"])
             
             return {
                 "date": target_date,
                 "demand": demand_blocks,
                 "frequency": freq_blocks,
                 "market_data": exchange_data # Structure from repo matches requirement
             }

        date_results = await asyncio.gather(*[process_date_refined(d) for d in sorted_dates])
        result["dates"] = date_results
        
        logger.info(f"Forecast mode processing completed in {time.time() - overall_start:.2f}s")
        return result

    async def _get_optimization_data(
        self, 
        dates: List[str], 
        demand_type: str
    ) -> Dict[str, Any]:
        sorted_dates = sorted(dates)
        overall_start = time.time()
        
        result = {
            "meta": self._build_meta(sorted_dates),
            "dates": [],
            "plants": [],
            "marginal_costs": []
        }
        
        # 1. Fetch Plan Master (Common)
        plant_master_list = await self.plants_repo.get_all_plants()
        plant_map = {p["plant_id"]: p for p in plant_master_list if "plant_id" in p}
        
        # 2. Populate Static Data
        for p_id, p_data in plant_map.items():
            tech = str(p_data.get("technology", "")).lower()
            availability_type = "VARIABLE" if "solar" in tech or "wind" in tech else "FIRM"
            dispatch_type = str(p_data.get("dispatch_type", "")).lower()
            must_run_flag = 1 if "must" in dispatch_type else 0
            
            result["plants"].append({
                "plant_id": p_id,
                "plant_name": p_data.get("plant_id"),
                "plant_type": p_data.get("technology"),
                "fuel_type": "NA",
                "min_up_time_blocks": 6,
                "min_down_time_blocks": 6,
                "min_up_time": p_data.get("min_up_time", 0),
                "min_shut_down": p_data.get("min_shut_down", 0),
                "max_shut_down": p_data.get("max_shut_down", 0),
                "must_run_flag": must_run_flag,
                "availability_type": availability_type
            })
            
            result["marginal_costs"].append({
                "plant_id": p_id,
                "marginal_cost_per_kwh": p_data.get("vc_price", 0),
                "effective_from": None,
                "effective_to": None
            })

        # 3. Process Dates
        async def process_date(target_date: str):
             # Fetch Demand & Supply
             d_future = self.demand_repo.get_demand_power_optimization(
                 target_date, "optimization", demand_type
             )

             s_future = self.supply_repo.get_supply_power_optimization(target_date)
             p_future = self.limit_service.latest_all(
                 filters={
                     "market": "DAM",
                     "id": "DAM_forecasted_price",
                     "category": "Limits",
                     "area": "Puducherry",
                 },
                 date_filter=target_date,
             )
             
             raw_demand, raw_supply, dam_price_result = await asyncio.gather(d_future, s_future, p_future)
             
             # Process Demand
             demand_blocks = []
             if raw_demand:
                 for d in raw_demand:
                     comp_act_demand = d.get("comp_act_demand", 0)
                     demand_mw = comp_act_demand
                     if demand_type == "DAF" and (comp_act_demand is None or float(comp_act_demand) == 0.0):
                         demand_mw = d.get("forecast", 0)
                     demand_blocks.append({
                         "block": d.get("time_block"),
                         "demand_mw": demand_mw
                     })
                 demand_blocks.sort(key=lambda x: x["block"])

             # Process DAM Forecasted Price (from limit_values)
             dam_forecasted_price = []
             for doc in (dam_price_result or {}).get("data", []) or []:
                 dam_forecasted_price.append({
                     "time_block": doc.get("block"),
                     "forecasted_price": doc.get("values"),
                 })
             dam_forecasted_price.sort(key=lambda x: x.get("time_block") or 0)
            
             # Process Supply
             gen_plants = self._process_supply_for_optimization(raw_supply, plant_map)
             
             # Optimization mode: No frequency, no Market data
             return {
                 "date": target_date,
                 "demand": demand_blocks,
                 "dam_forecasted_price": dam_forecasted_price,
                 "generation_forecast": {"plants": gen_plants}
             }

        date_results = await asyncio.gather(*[process_date(d) for d in sorted_dates])
        result["dates"] = date_results

        logger.info(f"Optimization mode processing completed in {time.time() - overall_start:.2f}s")
        return result

    def _process_supply_for_optimization(
        self, 
        raw_supply: List[Dict[str, Any]], 
        plant_map: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        # Group by plant_id
        gen_map = {}
        target_dispatch_type = "merit"
        
        for s in raw_supply:
            p_id = s.get("plant_id")
            if not p_id: continue
            
            if p_id not in gen_map:
                gen_map[p_id] = {"plant_id": p_id, "blocks_map": {}}
                
            t_block = s.get("time_block")
            vol = s.get("volume", 0)
            
            # Logic: Treat SG as DC in max_mw_dc
            # If dc_sg is SG or DC, we use volume as logic base?
            # User said: "inside optimization sg data will be treated as dc data in max_mw_dc"
            max_mw_dc = vol # Default assumption based on request
            
            # Schedule MW Logic (Original SG logic)
            schedule_mw = 0
            if s.get("dc_sg") == "SG":
                max_mw_dc_sg = s.get("max_mw_dc")
                schedule_mw = max_mw_dc_sg if max_mw_dc_sg is not None else vol

            # Tech Min Logic
            # "same 55% of dc will be technical_minimum_in_mw ... for merit plants"
            p_master = plant_map.get(p_id, {})
            p_dispatch_type = str(p_master.get("dispatch_type", "")).lower()
            
            tech_min_mw = 0
            if target_dispatch_type in p_dispatch_type:
                 # Apply 55% rule
                 tech_min_mw = 0.55 * max_mw_dc
            else:
                 # Use existing or default? User didn't specify for non-merit.
                 # Assuming keep existing behavior or 0 if not specified.
                 # Existing code checked 'tm' field or default 55%.
                 # We will use 0 or strict logic as requested?
                 # "this should also pass in parameters consider for all or only merit plants"
                 # if not matching apply_to, maybe we don't force it?
                 # Let's default to whatever is in DB or 0 if not merit.
                 # Actually commonly must-run plants have different tech min.
                 # Let's use 0 for now as safe default if not merit/targeted.
                 pass

            # Update: "below max_mw_dc" - implies strict Cap?
            
            # Ramp Logic
            ramp = 0.10 * max_mw_dc
            
            gen_map[p_id]["blocks_map"][t_block] = {
                "block": t_block,
                "schedule_mw": schedule_mw,
                "max_mw_dc": max_mw_dc,
                "technical_minimum_mw": tech_min_mw,
                "ramp_up_mw_per_block": ramp,
                "ramp_down_mw_per_block": ramp
            }
            
        # Convert to list
        plant_list = []
        for p_id, data in gen_map.items():
             blocks = sorted(data["blocks_map"].values(), key=lambda x: x["block"])
             # Add to plant list
             plant_list.append({
                 "plant_id": p_id,
                 "blocks": blocks
             })
             
        return plant_list



    def _build_meta(self, dates: List[str]) -> Dict[str, Any]:
        return {
            "timezone": "Asia/Kolkata",
            "block_duration_minutes": 15,
            "blocks_per_day": 96,
            "generated_at": datetime.now().isoformat() + "Z",
            "api_version": "2.0",
            "date_range": {
                "from": dates[0] if dates else None,
                "to": dates[-1] if dates else None
            },
            "total_dates": len(dates)
        }
