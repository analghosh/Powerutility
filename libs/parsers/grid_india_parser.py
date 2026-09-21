"""
Grid India JSON Parser
Parses complex JSON structure from grid-india.in API for Nuclear, Thermal, and Renewable supply data.
"""

import json
import logging
import datetime
from typing import Dict, Any, List, Optional
from collections import defaultdict

from libs.parsers.base_parser import BaseParser
from libs.models.provider_models import ProviderEntity
from libs.utils.csv_date_parse import parse_ingestion_date

logger = logging.getLogger(__name__)

#: Marks a record as un-requisitioned surplus rather than a schedule or an
#: entitlement. Shared with the supply processor, which routes on it.
URS_TYPE = "URS"


def _sum_blocks(*lists) -> List[float]:
    """Add several 96-block arrays element-wise, tolerating short or ragged ones."""
    totals = [0.0] * 96
    for values in lists:
        if not values:
            continue
        for idx in range(min(96, len(values))):
            try:
                value = values[idx]
                if value is not None:
                    totals[idx] += float(value)
            except (ValueError, TypeError):
                continue
    return totals


class GridIndiaParser(BaseParser):
    """
    Parser for Grid India formatted JSON data.
    Extracts DC (Entitlement) and SG (Schedule) for Nuclear and Thermal plants,
    including OA_REMC (Renewable) and ISGS components.
    """

    def get_parser_type(self) -> str:
        """Get the parser type identifier"""
        return "grid_india"

    def parse(self, content: str, provider: Optional[ProviderEntity] = None, **kwargs) -> List[Dict[str, Any]]:
        """
        Parse Grid India JSON content into flattened serve records.

        Args:
            content: Raw JSON content string
            provider: Provider entity (optional)
            **kwargs: Additional parsing parameters

        Returns:
            List[Dict[str, Any]]: Flattened supply data records with fields:
                - scheduled_at (str): Date from ResponseBody
                - time_block (int): 1-96
                - dc_sg (str): 'DC' or 'SG'
                - plant_id (str): SellerAcronym
                - volume (float): Extracted amount
        """
        try:
            # Parse JSON content
            if isinstance(content, str):
                data = json.loads(content)
            else:
                data = content

            # Handle wrapped "data" field if present (based on sample file structure)
            if "ResponseBody" in data:
                response_body = data["ResponseBody"]
            else:
                logger.warning("ResponseBody not found in content structure")
                return []

            if response_body is None:
                logger.warning("ResponseBody is null — no data available from API")
                return []

            date_str = response_body.get("Date")
            # Parse date "DD-MM-YYYY" to ISO format
            try:
                if date_str:
                    date_str = parse_ingestion_date(date_str).date().isoformat()
            except ValueError:
                logger.warning("Could not parse date: %s", date_str)

            # Extract schedule published timestamp from ResponseBody if present.
            # Preserves the full time component, unlike parse_ingestion_date which
            # normalizes to midnight.
            schedule_published_time = None
            schedule_published_time_raw = response_body.get("SchedulePublishedTime")
            if schedule_published_time_raw:
                try:
                    schedule_published_time = self._parse_schedule_published_time(
                        schedule_published_time_raw
                    )
                except ValueError:
                    logger.warning("Could not parse SchedulePublishedTime: %s", schedule_published_time_raw)

            group_list = response_body.get("GroupWiseDataList", [])
            
            raw_data = []

            # Extract SG Revision Number (Global for the response body)
            full_schd_revision_no = response_body.get("FullSchdRevisionNo")

            # Aggregation storage for SG: plant_id -> {'volume': [], 'sg_bl': [], ...}
            aggregated_sg = defaultdict(lambda: {
                "volume": [0.0] * 96,
                "sg_bl": [0.0] * 96,
                "revision_no": full_schd_revision_no,
                "approval_no": None
            })

            # Aggregation storage for URS: plant_id -> {'volume': []}.
            #
            # Un-requisitioned surplus is DERIVED, not published. Grid India
            # sends no FullschdList entry of type "URS" for this utility -
            # verified against a live payload, whose schedule types are only
            # ISGS, OA_REMC, OA_PX and OA_GNA. What it does send is the two
            # halves the definition is made of: EntitlementList, the share of
            # each plant's declared capacity, and RequisitionList, how much of
            # that share was actually asked for. The remainder is the surplus.
            #
            # Filled after EntitlementList is read, further down.
            aggregated_urs = defaultdict(lambda: {
                "volume": [0.0] * 96,
                "revision_no": full_schd_revision_no,
                "approval_no": None
            })

            # Aggregation storage for DC (from EntitlementList): plant_id -> {'volume': [], 'onbar': [], 'offbar': []}
            aggregated_dc = defaultdict(lambda: {
                "volume": [0.0] * 96,
                "onbar": [0.0] * 96,
                "offbar": [0.0] * 96,
                "revision_no": full_schd_revision_no,
                "approval_no": None
            })

            for group in group_list:
                # 1. Process EntitlementList (DC)
                # 2. Process NetScheduleSummary for OA_PX, OA_GNA, OA_TGNA
                net_schd_summary = group.get("NetScheduleSummary", {})
                net_schd_list = net_schd_summary.get("NetSchdDataList", [])
                
                # Aggregation storage for OA types
                aggregated_oa = defaultdict(lambda: {
                    "volume": [0.0] * 96,
                    "sg_beforelosses": [0.0] * 96,
                    "revision_no": full_schd_revision_no
                })

                for net_item in net_schd_list:
                    schd_type = net_item.get("EnergyScheduleTypeName")
                    if schd_type in ["OA_PX", "OA_GNA", "OA_TGNA"]:
                        plant_id = schd_type
                        net_schd_amount = net_item.get("NetSchdAmount", [])
                        
                        entry = aggregated_oa[plant_id]
                        for idx, val in enumerate(net_schd_amount):
                            if idx < 96 and val is not None:
                                try:
                                    entry["volume"][idx] += float(val)
                                except (ValueError, TypeError):
                                    pass
                
                # Append OA Aggregated Records
                for plant_id, agg in aggregated_oa.items():
                    self._append_records(
                        raw_data, 
                        date_str, 
                        plant_id, 
                        "SG", 
                        agg["volume"], 
                        agg["revision_no"], 
                        None, # approval_no
                        sg_beforelosses_list=agg["sg_beforelosses"],
                        schedule_published_time=schedule_published_time
                    )

                # 3. Process FullschdList (SG) for ISGS, URS, OA_REMC
                full_schd_list = group.get("FullschdList", [])
                for schd_item in full_schd_list:
                    schd_type = schd_item.get("EnergyScheduleTypeName")
                    plant_id = schd_item.get("SellerAcronym")
                    full_schd_data = schd_item.get("FullScheduleData", {})

                    if not plant_id:
                        continue

                    if schd_type not in ["OA_REMC", "ISGS", "URS"]:
                        continue

                    # For ISGS, URS, OA_REMC -> Extract and Aggregate
                    vol_list = []
                    bl_list = []

                    data_source = {}
                    if schd_type == "ISGS":
                        isgs_data = full_schd_data.get("ISGSFullScheduleJsonData", {})
                        isgs_gen_type = isgs_data.get("ISGSGeneratorTypeName")
                        if isgs_gen_type == "THERMAL":
                            data_source = isgs_data.get("ISGSThermalFullScheduleJsonData", {})
                        elif isgs_gen_type == "NUCLEAR":
                            data_source = isgs_data.get("ISGSNuclearFullScheduleJsonData", {})
                        elif isgs_gen_type == "HYDRO":
                            # Bhutan hydro imports (Punatsangchhu_II) arrive here. Without
                            # this branch data_source stays empty and the plant is written
                            # as 96 blocks of zero rather than skipped — a silent loss.
                            data_source = isgs_data.get("ISGSHydroFullScheduleJsonData", {})
                    elif schd_type == "URS":
                        urs_data = full_schd_data.get("URSFullScheduleJsonData", {})
                        # Grid India splits URS by fuel the same way it splits
                        # ISGS. Reading only the thermal spelling is how the
                        # nuclear DC rows came to be zero for months; a URS
                        # block for a hydro or gas seller would fail the same
                        # way, as 96 blocks of zero rather than a skip.
                        data_source = (
                            urs_data.get("URSThermalFullScheduleJsonData")
                            or urs_data.get("URSNuclearFullScheduleJsonData")
                            or urs_data.get("URSHydroFullScheduleJsonData")
                            or urs_data.get("URSGasFullScheduleJsonData")
                            or {}
                        )
                    elif schd_type == "OA_REMC":
                        data_source = full_schd_data.get("OAFullScheduleJsonData", {})

                    if data_source:
                        vol_list = data_source.get("TotalDrwBoundrySchdAmt") or \
                                   data_source.get("TotalDrwBoundarySchdAmount") or \
                                   data_source.get("TotalDrwBoundrySchdAmount") or \
                                   data_source.get("TotalDrwBoundarySchdAmt", [])
                        
                        bl_list = data_source.get("TotalRegBoundrySchdAmt") or \
                                  data_source.get("TotalRegBoundarySchdAmount") or \
                                  data_source.get("TotalRegBoundrySchdAmount") or \
                                  data_source.get("TotalRegBoundarySchdAmt", [])

                    # Aggregate
                    entry = aggregated_sg[plant_id]
                    # Update revision if we found one? (Usually global, but just in case)
                    # Accumulate values
                    for idx in range(96):
                        v = 0.0
                        b = 0.0
                        
                        # Volume
                        if idx < len(vol_list):
                            try:
                                val = vol_list[idx]
                                if val is not None:
                                    v = float(val)
                            except (ValueError, TypeError):
                                pass
                        
                        # SG Before Losses
                        if idx < len(bl_list):
                            try:
                                val = bl_list[idx]
                                if val is not None:
                                    b = float(val)
                            except (ValueError, TypeError):
                                pass
                                
                        entry["volume"][idx] += v
                        entry["sg_bl"][idx] += b

                # 4. Process EntitlementList (DC) — the final entitlement is the DC before-loss value
                entitlement_final = {}
                entitlement_list = group.get("EntitlementList", [])
                for ent_item in entitlement_list:
                    plant_id = ent_item.get("SellerAcronym")
                    if not plant_id:
                        continue

                    ent_data = ent_item.get("EntitlementData", {})
                    # Grid India splits the entitlement by fuel and populates exactly one
                    # of these blocks per seller.
                    data_source = (
                        ent_data.get("ThermalEntJsonData")
                        or ent_data.get("NuclearEntJsonData")
                        or ent_data.get("HydroEntJsonData")
                        or ent_data.get("GasEntJsonData")
                        or {}
                    )

                    if not data_source:
                        continue

                    # Thermal and Gas report the entitlement split into on-bar and off-bar
                    # halves; Nuclear and Hydro have no such split and drop the prefix from
                    # the field names. Reading only the Onbar* spelling is why every nuclear
                    # DC row on this deployment has been zero — the keys never matched.
                    # A fuel with no split is entirely on-bar, so its off-bar stays 0.
                    vol_list = data_source.get("OnbarFinalEntAmount") or \
                               data_source.get("FinalEntAmount") or []
                    onbar_list = data_source.get("EntireSharedOnbarEntAmount") or \
                                 data_source.get("EntireSharedEntAmount") or []
                    offbar_list = data_source.get("EntireSharedOffbarEntAmount") or []

                    # The full final entitlement, both halves, for the URS
                    # derivation below. Kept separate from `vol_list` because DC
                    # deliberately carries the on-bar half only, and changing
                    # that would move every DC figure on the platform.
                    off_final_list = data_source.get("OffbarFinalEntAmount") or []
                    entitlement_final[plant_id] = _sum_blocks(vol_list, off_final_list)

                    entry = aggregated_dc[plant_id]
                    for idx in range(96):
                        if idx < len(vol_list) and vol_list[idx] is not None:
                            try:
                                entry["volume"][idx] += float(vol_list[idx])
                            except (ValueError, TypeError):
                                pass
                        if idx < len(onbar_list) and onbar_list[idx] is not None:
                            try:
                                entry["onbar"][idx] += float(onbar_list[idx])
                            except (ValueError, TypeError):
                                pass
                        if idx < len(offbar_list) and offbar_list[idx] is not None:
                            try:
                                entry["offbar"][idx] += float(offbar_list[idx])
                            except (ValueError, TypeError):
                                pass

                # 5. Derive URS: the entitlement that was not requisitioned.
                #
                # Both lists are keyed on SellerAcronym and both run 96 blocks.
                # A plant with an entitlement and no requisition row at all has
                # requisitioned nothing, so its whole share is surplus - which
                # is why the lookup defaults to empty rather than skipping.
                requisitions = {}
                for req_item in group.get("RequisitionList", []) or []:
                    seller = req_item.get("SellerAcronym")
                    if not seller:
                        continue
                    req_data = req_item.get("RequisitionData") or {}
                    # One block populated per fuel, the same shape as the
                    # entitlement and schedule lists.
                    req_source = (
                        req_data.get("ThermalReqJsonData")
                        or req_data.get("NuclearReqJsonData")
                        or req_data.get("HydroReqJsonData")
                        or req_data.get("GasReqJsonData")
                        or {}
                    )
                    requisitions[seller] = _sum_blocks(
                        req_source.get("OnbarReqAmount") or [],
                        req_source.get("OffbarReqAmount") or [],
                    )

                for plant_id, ent_blocks in entitlement_final.items():
                    req_blocks = requisitions.get(plant_id, [0.0] * 96)
                    entry = aggregated_urs[plant_id]
                    for idx in range(96):
                        # Floored at zero: a surplus cannot be negative, and a
                        # requisition above the entitlement is a source-data
                        # fault rather than a quantity to report. Verified
                        # against a live payload - 19 plants x 96 blocks, no
                        # negative anywhere.
                        entry["volume"][idx] += max(ent_blocks[idx] - req_blocks[idx], 0.0)

            # Append DC Aggregated Records (from EntitlementList)
            for plant_id, agg in aggregated_dc.items():
                self._append_records(
                    raw_data,
                    date_str,
                    plant_id,
                    "DC",
                    agg["volume"],
                    agg["revision_no"],
                    agg["approval_no"],
                    onbar_list=agg["onbar"],
                    offbar_list=agg["offbar"],
                    schedule_published_time=schedule_published_time
                )

            # Append Aggregated Records
            for plant_id, agg in aggregated_sg.items():
                # Check if any data exists to avoid zero records if desired? 
                # Requirement implies replacing existing logic, so we append what we found.
                # If aggregation was empty (all zeros), it's still a schedule of 0.
                self._append_records(
                    raw_data, 
                    date_str, 
                    plant_id, 
                    "SG", 
                    agg["volume"], 
                    agg["revision_no"], 
                    agg["approval_no"],
                    sg_beforelosses_list=agg["sg_bl"],
                    schedule_published_time=schedule_published_time
                )

            # Append URS Aggregated Records.
            #
            # Emitted under their own dc_sg so they ride the existing raw-data
            # transport without being mistaken for a schedule: the supply
            # processor diverts them to urs_serve and they never reach
            # supply_serve. Written last so that a failure here cannot cost the
            # DC and SG records that were already appended above.
            for plant_id, agg in aggregated_urs.items():
                self._append_records(
                    raw_data,
                    date_str,
                    plant_id,
                    URS_TYPE,
                    agg["volume"],
                    agg["revision_no"],
                    agg["approval_no"],
                    schedule_published_time=schedule_published_time
                )

            logger.info("Parsed Grid India JSON: created %d flattened records", len(raw_data))
            return raw_data

        except Exception as e:
            logger.error("Error parsing Grid India content: %s", e, exc_info=True)
            raise ValueError(f"Grid India parsing failed: {str(e)}") from e

    def _append_records(self, raw_data_list, date_str, plant_id, dc_sg, values_list, revision_no, approval_no=None, sg_beforelosses_list=None, onbar_list=None, offbar_list=None, schedule_published_time=None):
        """Helper to append 96-block records to the list"""
        
        # Determine if the record is for tomorrow
        is_tomorrow = False
        try:
            today = datetime.datetime.now().date()
            tomorrow = today + datetime.timedelta(days=1)
            # date_str is in ISO format from parse()
            record_date = datetime.datetime.fromisoformat(date_str).date()
            if record_date == tomorrow:
                is_tomorrow = True
        except Exception:
            pass

        # Ensure we don't go out of bounds if list is larger/smaller, 
        # though requirement says 96. We'll use index+1 as time_block.
        for idx, val in enumerate(values_list):
            if val is not None:
                current_time = datetime.datetime.now()
                
                crr_time = current_time.time()
                if (datetime.time(0, 0) <= crr_time <= datetime.time(23, 59)):
                    record = {
                        "scheduled_at": date_str,
                        "time_block": idx + 1,
                        "dc_sg": dc_sg,
                        "plant_id": plant_id,
                        "volume": float(val),
                        "revision_no": revision_no,
                        "rtm_tm": float(val)
                    }
                else:
                    record = {
                        "scheduled_at": date_str,
                        "time_block": idx + 1,
                        "dc_sg": dc_sg,
                        "plant_id": plant_id,
                        "volume": float(val),
                        "revision_no": revision_no,
                    }
                
                if schedule_published_time is not None:
                    record["schedule_published_time"] = schedule_published_time
                
                # Add optional fields if present
                if approval_no:
                    record["approval_no"] = approval_no

                if sg_beforelosses_list and idx < len(sg_beforelosses_list):
                    val_bl = sg_beforelosses_list[idx]
                    if val_bl is not None:
                        try:
                            record["sg_beforelosses"] = float(val_bl)
                        except (ValueError, TypeError):
                            pass
                logger.info("Onbar value for plant %s time_block %d: %s", plant_id, idx + 1, onbar_list[idx] if onbar_list and idx < len(onbar_list) else "N/A")
                if onbar_list and idx < len(onbar_list) and onbar_list[idx] is not None:
                    try:
                        record["entire_shared_onbar_ent_amount"] = float(onbar_list[idx])
                        logger.info("Added entire_shared_onbar_ent_amount for plant %s time_block %d: %f", plant_id, idx + 1, float(onbar_list[idx]))
                    except (ValueError, TypeError):
                        logger.warning("Could not parse onbar value for plant %s time_block %d: %s", plant_id, idx + 1, onbar_list[idx])
                logger.info("Offbar value for plant %s time_block %d: %s", plant_id, idx + 1, offbar_list[idx] if offbar_list and idx < len(offbar_list) else "N/A")
                if offbar_list and idx < len(offbar_list) and offbar_list[idx] is not None:
                    try:
                        record["entire_shared_offbar_ent_amount"] = float(offbar_list[idx])
                        logger.info("Added entire_shared_offbar_ent_amount for plant %s time_block %d: %f", plant_id, idx + 1, float(offbar_list[idx]))
                    except (ValueError, TypeError):
                        logger.warning("Could not parse offbar value for plant %s time_block %d: %s", plant_id, idx + 1, offbar_list[idx])

                raw_data_list.append(record)

    def _parse_schedule_published_time(self, value: Any) -> datetime.datetime:
        """
        Parse the Grid India SchedulePublishedTime value, preserving the full
        time component. Supports common Grid India datetime formats.
        """
        if isinstance(value, datetime.datetime):
            return value
        if isinstance(value, datetime.date):
            return datetime.datetime(value.year, value.month, value.day)

        text = str(value).strip()
        if not text:
            raise ValueError("Empty SchedulePublishedTime value")

        # ISO formats (with or without timezone)
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.datetime.strptime(text, fmt)
            except ValueError:
                continue

        try:
            return datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            pass

        # Grid India style DD-MM-YYYY formats
        for fmt in ("%d-%m-%Y %H:%M:%S", "%d-%m-%Y"):
            try:
                return datetime.datetime.strptime(text, fmt)
            except ValueError:
                continue

        # Slash-separated formats
        for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y"):
            try:
                return datetime.datetime.strptime(text, fmt)
            except ValueError:
                continue

        raise ValueError(f"Cannot parse SchedulePublishedTime: {text}")

    def validate_content(self, content: str) -> bool:
        """
        Validate if content looks like Grid India JSON
        """
        try:
            if isinstance(content, str):
                data = json.loads(content)
            else:
                data = content
            
            # Check for key signatures
            if "ResponseBody" in data:
                 return True
            return False
        except Exception:
            return False
