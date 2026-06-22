"""
Limit Values service layer using repository pattern.
Implements: file upload + edit (PENDING), approve/reject, latest read,
and writes to limit_audit_log.
"""

import csv
import io
import json
import logging
from typing import List, Optional, Dict, Any
from datetime import datetime
from zoneinfo import ZoneInfo
from bson.objectid import ObjectId
import pandas as pd

from ..repository.limit_values_repository import limit_values_repository
from ..repository.limit_workflow_repository import limit_workflow_repository
from ...core.config import settings

from libs.audit_queue.simple_logger import AuditLogger
from libs.utils.csv_date_parse import parse_csv_scheduled_at, parse_schedule_date_filter

logger = logging.getLogger(__name__)

# ---------- helpers ----------
REQUIRED_COLUMNS = ["schedule_date", "block", "id_type", "values"]
ALLOWED_ACTIONS = {"UPSERT", "DELETE"}
MATRIX_FIXED_COLUMNS = ["Date", "Exchange", "Market", "Block"]

# CSV Header -> Internal ID partial map (excluding dynamic ones)
CSV_TO_DB_MAP = {
    "Buffer Price": "Buffer",
    "N-1": "n_1",
    "Ceiling Price": "Ceiling_Price",
    "NOC Buy": "noc_buy",
    "NOC Sell": "noc_sell",
    "Min Sell Price": "minsellprice",
    "OCF Premium/Discount Price": "ocfPremiumDiscountPrice",
    "OCF Max OCF Qty": "ocfMaxOCFQty",
    # "Forecasted Price" is handled dynamically
}

# Internal ID -> CSV Header partial map
DB_TO_CSV_MAP = {v: k for k, v in CSV_TO_DB_MAP.items()}

def _normalize_item(row: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalizes a single row from the file into the
    clean dictionary format required for the database.
    """
    try:
        # Get action first to decide how to handle 'values'
        action = str(row.get("action", "UPSERT")).upper()
        if action not in ALLOWED_ACTIONS:
            raise ValueError(f"Invalid action {action}")

        value = None
        raw_values = row.get("values")

        if action == "UPSERT":
            if raw_values is None or raw_values == "":
                 raise ValueError("values required for UPSERT/update")
            value = float(raw_values)
        # If action is "DELETE", value remains None

        # Build the final item with the correct keys for the database
        # Map id_type (from API/CSV) to id (internal DB field)
        item_id = row.get("id_type")
        if not item_id:
            raise ValueError("id_type is required")

        item = {
            "block": int(row["block"]),
            "id": str(item_id).strip(),
            "market": row.get("market", "RTM"),
            "category": row.get("category", "Limits"),
            "area": row.get("area", ""),
            "exchange": row.get("exchange", "IEX"),
            "value": value,  # <-- RENAMED from 'values'
            "comment": row.get("comment"),
        }
        
        # Map scheduled_at/schedule_date from CSV to inserted_at field in database
        # STRICTLY require scheduled_at or schedule_date
        raw_date_value = row.get("scheduled_at") or row.get("schedule_date")

        if not raw_date_value:
            raise ValueError("scheduled_at is required")

        try:
            item["scheduled_at"] = parse_schedule_date_filter(raw_date_value)
        except ValueError:
            raise
    
        return item
    
    except Exception as e:
        if isinstance(e, (ValueError, KeyError)):
            raise ValueError(f"Invalid item: {row}. Error: {str(e)}")
        raise ValueError(f"Processing failed for {row}. Error: {str(e)}")
    
def _business_key(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "block": item["block"],
        "id": item["id"],
        "market": item["market"],
        "category": item["category"],
        "area": item["area"],
        "exchange": item.get("exchange", "IEX"),
    }

def _is_rtm_forecast_past_block(item: Dict[str, Any]) -> bool:
    """
    Return True if the item should be blocked from staging/updating:
    - RTM_forecasted_price: block past dates and past time blocks on today.
    - DAM_forecasted_price: block past dates and tomorrow's date after 11:00 AM IST.
    """
    item_id = item.get("id")
    if item_id not in ("RTM_forecasted_price", "DAM_forecasted_price"):
        return False
    try:
        from datetime import timedelta
        tz = ZoneInfo("Asia/Kolkata")
        now_local = datetime.now(tz)
        today_ist = now_local.date()
        scheduled_at = item.get("scheduled_at")
        if scheduled_at is None:
            return False
        if isinstance(scheduled_at, datetime):
            item_date = scheduled_at.date()
        else:
            # Repository serialization returns ISO strings; parse before comparing.
            item_date = parse_schedule_date_filter(scheduled_at).date()

        if item_id == "RTM_forecasted_price":
            current_block = (now_local.hour * 60 + now_local.minute) // 15 + 1
            if item_date < today_ist:
                return True
            if item_date == today_ist and int(item.get("block", 0)) < current_block:
                return True

        elif item_id == "DAM_forecasted_price":
            tomorrow_ist = today_ist + timedelta(days=1)
            if item_date < today_ist:
                return True
            if item_date == tomorrow_ist and (now_local.hour, now_local.minute) >= (11, 0):
                return True

    except Exception:
        pass
    return False


def _values_doc(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "scheduled_at": parse_schedule_date_filter(item["scheduled_at"]),
        "block": item["block"],
        "id": item["id"],
        "market": item["market"],
        "category": item["category"],
        "area": item["area"],
        "exchange": item.get("exchange", "IEX"),
        "values": item["values"],
        "is_active": True,
    }

class LimitService:
    def __init__(self):
        self.values_repo = limit_values_repository
        self.flow_repo = limit_workflow_repository
        self.values_collection = settings.MONGODB_LIMIT_VALUES_COLLECTION              # "limit_values"
        self.approval_collection = settings.MONGODB_LIMIT_APPROVAL_COLLECTION          # "limit_approval"
        self.audit_collection = settings.MONGODB_LIMIT_AUDIT_COLLECTION                # "limit_audit_log"

    def get_sample_csv_content(self) -> str:
        """
        Returns a sample CSV content for limit values upload.
        Headers: exchange, market, schedule_date, block, id_type, category, area, values, comment, action
        """
        headers = [
            "exchange",
            "market",
            "schedule_date",
            "block",
            "id_type",
            "category",
            "area",
            "values",
            "comment",
            "action",
        ]
        sample_rows = [
            ["IEX", "GDAM", "2026-06-21", "1", "Ceiling_Price", "Limits", "", "5000", "", "UPSERT"],
            ["IEX", "GDAM", "2026-06-21", "1", "Buffer", "Limits", "", "200", "", "UPSERT"],
            ["PXIL", "DAM", "2026-06-21", "2", "Ceiling_Price", "Limits", "", "5100", "", "UPSERT"],
        ]
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(headers)
        writer.writerows(sample_rows)
        return output.getvalue()

    # --------- Read (latest) ---------
    async def latest(self, date_filter: str = None) -> Dict[str, Any]:
        try:
            rows = await self.values_repo.get_latest_inserted_at_data_pipeline_with_filters(
                self.values_collection,
                date_filter=date_filter
            )

            # Convert BSON ObjectId and datetimes to JSON-serializable values
            try:
                from bson import ObjectId
            except Exception:
                ObjectId = None

            def _serialize(value):
                # Handle ObjectId
                if ObjectId is not None and isinstance(value, ObjectId):
                    return str(value)
                # Datetime -> ISO
                if isinstance(value, datetime):
                    return value.isoformat()
                # Dict -> recurse
                if isinstance(value, dict):
                    return {k: _serialize(v) for k, v in value.items()}
                # List/tuple -> recurse
                if isinstance(value, (list, tuple)):
                    return [_serialize(v) for v in value]
                return value

            serialized = [_serialize(r) for r in rows]
            for record in serialized:
                record.setdefault("exchange", "IEX")
            return {"records": serialized, "count": len(serialized)}
        except Exception as e:
            logger.exception(f"[LimitService] latest failed: {str(e)}")
            raise

    async def latest_paginated(self, from_block: int, to_block: int, filters: Dict[str, Any], date_filter: str = None) -> Dict[str, Any]:
        """
        Get limit values with block-based pagination and filtering support.
        If date_filter is None, returns data for the latest available date.
        """
        try:
            result = await self.values_repo.get_latest_inserted_at_data_pipeline_paginated(
                self.values_collection,
                from_block=from_block,
                to_block=to_block,
                filters=filters,
                date_filter=date_filter
            )
            for record in result.get("data", []):
                record.setdefault("exchange", "IEX")
            return result
        except Exception as e:
            logger.exception(f"[LimitService] latest_paginated failed: {str(e)}")
            raise

    async def latest_all(self, filters: Dict[str, Any], date_filter: str = None) -> Dict[str, Any]:
        """
        Get all limit values without pagination.
        If date_filter is None, returns ALL data irrespective of date.
        If date_filter is provided, uses latest inserted_at per key for the given date.
        """
        try:
            if date_filter is None:
                # Return all data without date filtering
                data = await self.values_repo.get_all_data_with_filters(
                    self.values_collection,
                    filters=filters
                )
            else:
                # Return data filtered by date
                data = await self.values_repo.get_latest_inserted_at_data_pipeline_with_filters(
                    self.values_collection,
                    filters=filters,
                    date_filter=date_filter
                )
            for record in data:
                record.setdefault("exchange", "IEX")
            return {"data": data, "total": len(data)}
        except Exception as e:
            logger.exception(f"[LimitService] latest_all failed: {str(e)}")
            raise

    async def generate_matrix_csv(self, market: Optional[str] = None, schedule_date: Optional[str] = None) -> str:
        """
        Generates a CSV string in the matrix format for TODAY only:
        Date, Market, Block, <MappedID1>, <MappedID2>, ...
        """
        try:
            filters = {
                "category": "Limits"
            }
            if market:
                filters["market"] = market
            
            # Use provided schedule_date or default to today
            if schedule_date:
                today = parse_schedule_date_filter(schedule_date)
            else:
                today = datetime.now()
                
            start_dt = today.replace(hour=0, minute=0, second=0, microsecond=0)
            end_dt = today.replace(hour=23, minute=59, second=59, microsecond=999999)
            
            # Add date range to filters
            filters["scheduled_at"] = {"$gte": start_dt, "$lte": end_dt}
            
            docs = await self.values_repo.get_all_data_with_filters(self.values_collection, filters=filters)
            
            if not docs:
                return "Date,Market,Block"

            data = []
            
            for doc in docs:
                datestr = doc["scheduled_at"].strftime("%Y-%m-%d") if isinstance(doc["scheduled_at"], datetime) else str(doc["scheduled_at"]).split("T")[0]
                
                db_id = doc.get("id")
                market_val = doc.get("market", "RTM")
                
                # Map Internal ID -> CSV Header
                csv_header = DB_TO_CSV_MAP.get(db_id)
                if not csv_header:
                    # Check dynamic mapping
                    if db_id == f"{market_val}_forecasted_price":
                         csv_header = "Forecasted Price"
                    else:
                         # Fallback: use ID as is (or skip?)
                         # User requirement implies specific columns. 
                         # If we have extra IDs in DB not in map, showing them is cleaner than hiding.
                         csv_header = db_id

                row = {
                    "Date": datestr,
                    "Exchange": doc.get("exchange", "IEX"),
                    "Market": market_val,
                    "Block": doc.get("block"),
                    "ID_Header": csv_header,
                    "Value": doc.get("values", doc.get("value"))
                }
                data.append(row)

            if not data:
                 return "Date,Exchange,Market,Block"

            df = pd.DataFrame(data)

            # Drop duplicates
            df = df.drop_duplicates(subset=["Date", "Exchange", "Market", "Block", "ID_Header"], keep='last')

            # Pivot
            pivot_df = df.pivot(index=["Date", "Exchange", "Market", "Block"], columns="ID_Header", values="Value")

            # Reset index
            pivot_df = pivot_df.reset_index()

            # Ensure specific column order if possible, or at least fixed columns first
            cols = list(pivot_df.columns)
            # Remove index cols from list to re-order
            for c in ["Date", "Exchange", "Market", "Block"]:
                if c in cols: cols.remove(c)

            # Sort remaining columns based on user preference if needed, or alpha?
            # User list: Buffer Price, N-1, Ceiling Price, NOC Buy, Forecasted Price, Min Sell Price, NOC Sell
            # Let's try to order them nicely if they exist
            preferred_order = ["Ceiling Price", "Buffer Price", "N-1", "NOC Buy", "NOC Sell", "Forecasted Price", "Min Sell Price"]
            ordered_cols = []
            for p in preferred_order:
                if p in cols:
                    ordered_cols.append(p)
                    cols.remove(p)
            ordered_cols.extend(cols) # Add any remaining
            preferred_order =['OCF Max Qty', 'OCF Premium/Discount Price']
            final_cols = ["Date", "Exchange", "Market", "Block"] + ordered_cols
            pivot_df = pivot_df[final_cols]

            return pivot_df.to_csv(index=False)
            
        except Exception as e:
            logger.exception(f"[LimitService] generate_matrix_csv failed: {str(e)}")
            raise

    async def stage_file_upload(self, 
                                file_bytes: bytes, filename: str, 
                                user_ctx: Dict[str, Any], 
                                audit_logger: AuditLogger) -> Dict[str, Any]:
        """
        Parses a file (Standard or Matrix CSV, or JSON) and upserts items into approval collection.
        Auto-detects Matrix CSV format by looking at headers.
        """
        try:
            name = filename.lower()
            raw_items: List[Dict[str, Any]] = []
            normalized_items: List[Dict[str, Any]] = []
            normalization_errors = 0
            
            is_matrix = False

            # 1. Parse File
            if name.endswith(".csv"):
                stream = io.StringIO(file_bytes.decode("utf-8-sig"))
                reader = csv.DictReader(stream)
                fieldnames = reader.fieldnames or []
                
                # Detect Matrix Format
                # Standard format has 'id_type', 'values' (or 'value').
                # Matrix format has 'Date', 'Market', 'Block'.
                # We check for presence of Matrix fixed columns
                if set(["Date", "Market", "Block"]).issubset(set(fieldnames)):
                    is_matrix = True
                    # Matrix Parsing Logic
                    id_columns = [c for c in fieldnames if c not in MATRIX_FIXED_COLUMNS]

                    for row in reader:
                        try:
                            date_val = row.get("Date")
                            market = row.get("Market", "RTM")
                            exchange = row.get("Exchange", "IEX") or "IEX"
                            block_raw = row.get("Block")

                            if not date_val or not block_raw:
                                continue

                            block = int(block_raw)

                            for header in id_columns:
                                val_str = row.get(header)
                                if val_str is None or val_str.strip() == "":
                                    continue

                                # Map CSV Header -> Internal ID
                                db_id = CSV_TO_DB_MAP.get(header)
                                if not db_id:
                                    if header == "Forecasted Price":
                                        db_id = f"{market.upper()}_forecasted_price"
                                    else:
                                        db_id = header # Fallback

                                item = {
                                    "block": block,
                                    "id": db_id,
                                    "id_type": db_id,
                                    "market": market,
                                    "exchange": exchange,
                                    "category": "Limits", # ALWAYS Limits
                                    "area": "Puducherry", # ALWAYS Puducherry
                                    "values": float(val_str),
                                    "scheduled_at": date_val,
                                    "schedule_date": date_val,
                                    "action": "UPSERT"
                                }

                                try:
                                    normalized_items.append(_normalize_item(item))
                                except Exception as ex:
                                    normalization_errors += 1
                                    logger.warning(f"Normalization failed for matrix cell {header} at {date_val}: {ex}")

                        except Exception as row_ex:
                            normalization_errors += 1
                            logger.warning(f"Failed to process matrix row {row.get('Date', '?')}: {row_ex}")

                else:
                    # Standard Parsing Logic
                    # Reset stream to read again if needed, or just iterate since we haven't consumed reader in 'else' branch?
                    # reader was not consumed if is_matrix is False (check logic above: 'if set(...).issubset' didn't execute loop)
                    # Use existing standard logic
                    raw_items = [dict(r) for r in reader]
            
            elif name.endswith(".json"):
                payload = json.loads(file_bytes.decode("utf-8"))
                raw_items = payload.get("records", payload) if isinstance(payload, dict) else payload
            else:
                raise ValueError("Unsupported file type. Use .csv or .json")


            # 2. Normalize Items (Only if not matrix, as matrix does normalization inline to handle pivoting)
            if not is_matrix:
                for r in raw_items:
                    try:
                        # _normalize_item MUST return a dict containing the 5 key fields
                        normalized_items.append(_normalize_item(r))
                    except Exception as ex:
                        normalization_errors += 1
                        logger.warning(f"Normalization failed for row: {r}. Error: {str(ex)}")
            
            # 3. Process each normalized item (Find, Upsert, Audit)
            return await self._stage_items(
                normalized_items=normalized_items,
                filename=filename,
                user_ctx=user_ctx,
                audit_logger=audit_logger,
                raw_item_count=len(raw_items) if not is_matrix else len(normalized_items), # Approximate for matrix
                normalization_errors=normalization_errors
            )

        except Exception as e:
            logger.exception(f"[LimitService] stage_file_upload failed: {str(e)}")
            raise

    # --------- Stage: create (file upload) ---------
    async def _stage_items(self, 
                          normalized_items: List[Dict[str, Any]], 
                          filename: str, 
                          user_ctx: Dict[str, Any], 
                          audit_logger: AuditLogger,
                          raw_item_count: int,
                          normalization_errors: int) -> Dict[str, Any]:
        """
        Reusable logic to stage a list of already-normalized items.
        """
        try:
             # Ensure the approval collection exists with indexes before we write to it
            await self.flow_repo.ensure_collection_exists(self.approval_collection)

            now = datetime.now()
            actor_id = user_ctx.get("user_id", "anonymous")
            actor_username = user_ctx.get("username", "anonymous")
            correlation_id = user_ctx.get("correlation_id", "unknown")
            batch_id = str(ObjectId()) # One ID for this entire upload
            
            UNIQUE_KEY_FIELDS = ["block", "id", "market", "category", "area", "exchange", "scheduled_at"]

            audit_logs_to_write = []
            successful_upserts = 0
            upsert_errors = 0

            for item in normalized_items:
                try:

                    # <--- FIX #1: Clean the item dictionary ---
                    if "values" in item and "value" not in item:
                        item["value"] = item.pop("values")

                    item.pop("action", None)

                    # RTM_forecasted_price: skip past blocks (only allow current + future)
                    if _is_rtm_forecast_past_block(item):
                        continue

                    # Build the filter_doc from the 5 unique keys
                    filter_doc = {}
                    missing_keys = []
                    for key_field in UNIQUE_KEY_FIELDS:
                        if key_field not in item:
                            missing_keys.append(key_field)
                        else:
                            filter_doc[key_field] = item[key_field]
                    
                    if missing_keys:
                        raise ValueError(f"Item missing required unique key fields: {', '.join(missing_keys)}")

                    # A. Find the *previous* document
                    previous_doc = await self.flow_repo.find_one(self.approval_collection, filter_doc)
                    
                    # We need to find the *data* from the previous doc
                    previous_value_data = None
                    if previous_doc:
                        previous_value_data = {
                            k: v for k, v in previous_doc.items() 
                            if k not in [
                                "_id", "status", "request_type", "title",
                                "last_edited_at", "last_edited_by", "batch_id",
                                "correlation_id", "requested_at", "requested_by",
                                "requested_username", "created_at", "created_by"
                            ]
                        }

                    # B. Prepare the new document for upsert
                    if "scheduled_at" not in item:
                        item["scheduled_at"] = datetime.combine(datetime.now().date(), datetime.min.time())
                    
                    item_with_inserted_at = item
                    
                    update_payload = {
                        "$set": {
                            "status": "PENDING",
                            "last_edited_at": now,
                            "last_edited_by": actor_id,
                            "batch_id": batch_id, 
                            **item_with_inserted_at
                        },
                        "$setOnInsert": {
                            "requested_at": now,
                            "requested_by": actor_id,
                            "requested_username": actor_username,
                            "created_at": now,
                            "created_by": actor_id,
                        }
                    }

                    # C. Perform the upsert
                    upsert_result = await self.flow_repo.upsert_one(
                        self.approval_collection,
                        filter_doc,  # Find by composite key
                        update_payload
                    )
                    
                    successful_upserts += 1
                    
                    entity_id = upsert_result.upserted_id or (previous_doc and previous_doc.get("_id"))

                    # D. Create the audit log
                    audit_log = {
                        "ts": now,
                        "actor_id": actor_id,
                        "actor_username": actor_username,
                        "action": "APPROVAL_UPSERT_STAGED",
                        "entity_type": "limit_approval_item",
                        "entity_id": str(entity_id),
                        "operation": "UPSERT",
                        "batch_id": batch_id,
                        "correlation_id": correlation_id,
                        "success": True,
                        "changes": {
                            "previous_value": previous_value_data,
                            "new_value": item
                        }
                    }
                    audit_logs_to_write.append(audit_log)

                except Exception as e:
                    upsert_errors += 1
                    logger.error(f"Failed to process item {item.get('id', 'UNKNOWN')}: {str(e)}")

            # 4. Batch write all audit logs at once
            if audit_logs_to_write:
                await self.flow_repo.write_audit_many(self.audit_collection, audit_logs_to_write)
            
            if audit_logger:
                try:
                    unique_blocks = sorted(list(set(x["block"] for x in normalized_items)))
                    unique_cats = sorted(list(set(x["category"] for x in normalized_items)))
                    unique_markets = sorted(list(set(x["market"] for x in normalized_items)))
                    
                    block_str = str(unique_blocks[0]) if len(unique_blocks) == 1 else "All" if len(unique_blocks) > 1 else "None"
                    cat_str = "/".join(unique_cats)
                    market_str = unique_markets[0] if unique_markets else "Limit Values"

                    action_taken_for = f"Limits/Block: {block_str}/{cat_str}"
                    
                    audit_logger.log_user_action(
                        service="Limit Values",
                        user_id=actor_id,
                        action="approval",
                        message=f"Uploaded Limit Values file: {filename}",
                        module=market_str,
                        action_taken_for=action_taken_for,
                        success=True
                    )
                except Exception as e:
                    logger.error(f"Failed to log audit in stage_file_upload: {e}")


            # 5. Return a summary
            return {
                "batch_id": batch_id,
                "total_items_in_file": raw_item_count,
                "normalization_errors": normalization_errors,
                "items_processed_for_upsert": len(normalized_items),
                "successful_upserts": successful_upserts,
                "upsert_errors": upsert_errors
            }
        except Exception as e:
            logger.exception(f"[LimitService] _stage_items failed: {str(e)}")
            raise


    
    
    async def stage_edit(self, approval_id: Any, items: List[Dict[str, Any]], user_ctx: Dict[str, Any]) -> Dict[str, Any]:
        try:
            approval = await self.flow_repo.get_approval(self.approval_collection, approval_id)
            if not approval:
                raise ValueError("Approval request not found")
            if approval["status"] != "PENDING" and approval["status"] != "REJECTED":
                raise ValueError("Only PENDING or REJECTED requests can be edited")
            if approval["requested_by"] != user_ctx.get("user_id"):
                raise PermissionError("Only the requester can edit this approval")

            normalized, errors = [], 0
            for r in items:
                try:
                    normalized.append(_normalize_item(r))
                except Exception as ex:
                    errors += 1
                    normalized.append({"row_status": "ERROR", "row_errors": [str(ex)], **r})

            now = datetime.now()
            counts = {
                "total_items": len(normalized),
                "valid_items": len([i for i in normalized if i.get("row_status") != "ERROR"]),
                "invalid_items": errors,
                "applied_upserts": approval.get("counts", {}).get("applied_upserts", 0),
                "applied_deletes": approval.get("counts", {}).get("applied_deletes", 0),
            }
            await self.flow_repo.update_approval(
                self.approval_collection,
                approval_id,
                {
                    "items": normalized,
                    "status": "PENDING",  # Reset status to PENDING on edit
                    "last_edited_at": now,
                    "last_edited_by": user_ctx.get("user_id", "anonymous"),
                    "counts": counts
                }
            )

            await self.flow_repo.write_audit(self.audit_collection, {
                "ts": now,
                "actor_id": user_ctx.get("user_id", "anonymous"),
                "actor_username": user_ctx.get("username", "anonymous"),
                "action": "STAGE_EDIT",
                "entity_type": "limit_approval",
                "entity_id": approval_id,
                "operation": "UPDATE",
                "request_id": approval_id,
                "correlation_id": user_ctx.get("correlation_id", "unknown"),
                "success": True
            })

            return {"approval_id": str(approval_id), "total": counts["total_items"], "invalid": counts["invalid_items"]}
        except Exception as e:
            logger.exception(f"[LimitService] stage_edit failed: {str(e)}")
            raise

    # --------- Pending list ---------
    async def list_pending(self, user_ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
        try:
            return await self.flow_repo.list_approvals(self.approval_collection)
        except Exception as e:
            logger.exception(f"[LimitService] list_pending failed: {str(e)}")
            raise
    
    # In your LimitService class

    async def list_for_approver(self, user_ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Fetches pending/rejected items for an approver, combining data from
        the main collection (old_value) and approval collection (new_value).
        """
        try:
            roles = user_ctx.get('roles', [])
            
            status_filter = []
            if "maker" in roles or "admin" in roles:
                # A "maker" can see PENDING and their own REJECTED items
                status_filter = ["PENDING", "REJECTED"]
            elif "checker" in roles or "admin" in roles:
                # A "checker" (approver) only sees PENDING items
                status_filter = ["PENDING"]
            else:
                # If the user is not a maker or checker, they see nothing.
                logger.warning(f"User {user_ctx.get('username')} has no approval roles.")
                return []

            # Call the new repository method
            approvals = await self.flow_repo.get_combined_approvals(
                approval_collection=self.approval_collection,
                main_collection=self.values_collection,
                status_filter=status_filter
            )
            for approval in approvals:
                approval.setdefault("exchange", "IEX")
                if approval.get("old_value"):
                    approval["old_value"].setdefault("exchange", "IEX")
                if approval.get("new_value"):
                    approval["new_value"].setdefault("exchange", "IEX")
            return approvals
        except Exception as e:
            logger.exception(f"[LimitService] list_for_approver failed: {str(e)}")
            raise

    async def list_for_approver_paginated(self, user_ctx: Dict[str, Any], page: int, page_size: int, filters: Dict[str, Any], date_filter: str = None) -> Dict[str, Any]:
        """
        Fetches pending/rejected items for an approver with pagination and filters,
        combining data from the main collection (old_value) and approval collection (new_value).
        Uses latest inserted_at per key for the given date.
        """
        try:
            roles = user_ctx.get('roles', [])
            
            # Debug logging
            logger.info(f"[DEBUG] User {user_ctx.get('username')} has roles: {roles}")
            
            status_filter = []
            if "maker" in roles or "admin" in roles:
                # A "maker" can see PENDING and their own REJECTED items
                status_filter = ["PENDING", "REJECTED"]
            elif "checker" in roles or "admin" in roles:
                # A "checker" (approver) only sees PENDING items
                status_filter = ["PENDING"]
            else:
                # If the user is not a maker or checker, they see nothing.
                logger.warning(f"User {user_ctx.get('username')} has no approval roles. Available roles: {roles}")
                return {
                    "data": [],
                    "page": page,
                    "page_size": page_size,
                    "total": 0,
                    "total_pages": 0,
                    "has_next": False,
                    "has_prev": False
                }
            
            logger.info(f"[DEBUG] Status filter for user {user_ctx.get('username')}: {status_filter}")

            # Call the new paginated repository method with date filter
            result = await self.flow_repo.get_combined_approvals_paginated(
                approval_collection=self.approval_collection,
                main_collection=self.values_collection,
                status_filter=status_filter,
                page=page,
                page_size=page_size,
                filters=filters,
                date_filter=date_filter
            )
            for approval in result.get("data", []):
                approval.setdefault("exchange", "IEX")
                if approval.get("old_value"):
                    approval["old_value"].setdefault("exchange", "IEX")
                if approval.get("new_value"):
                    approval["new_value"].setdefault("exchange", "IEX")
            return result
        except Exception as e:
            logger.exception(f"[LimitService] list_for_approver_paginated failed: {str(e)}")
            raise

    # --------- Approve ---------
# In your LimitService class

    async def approve(self, approval_id: Any, approver_ctx: Dict[str, Any], audit_logger: Optional[AuditLogger] = None) -> Dict[str, Any]:
        """
        Approves a SINGLE item.
        This moves the 'item_data' from the approval document to the
        main 'limit_values' collection.
        """
        try:
            # 1. Get the single approval document
            # (Assuming self.flow_repo.get_approval is a find_one_by_id)
            approval = await self.flow_repo.get_approval(self.approval_collection, approval_id)
            
            if not approval:
                raise ValueError(f"Approval request '{approval_id}' not found")
            if approval["status"] != "PENDING":
                return {"message": "Request already processed", "status": approval["status"]}

            now = datetime.now()
            actor_id = approver_ctx.get("user_id", "approver")
            actor_username = approver_ctx.get("username", "approver")
            correlation_id = approver_ctx.get("correlation_id", "unknown")

            # 2. Get the 5-field business key from the approval doc
            try:
                scheduled_at_raw = approval.get("scheduled_at")
                if scheduled_at_raw is None:
                    raise ValueError("Approval document is missing scheduled_at")
                # Repository serialization returns ISO strings; convert back to datetime.
                scheduled_at = (
                    parse_schedule_date_filter(scheduled_at_raw)
                    if isinstance(scheduled_at_raw, str)
                    else scheduled_at_raw
                )

                key = {
                    "block": approval["block"],
                    "id": approval["id"],
                    "market": approval["market"],
                    "category": approval["category"],
                    "area": approval["area"],
                    "scheduled_at": scheduled_at,
                }
            except KeyError as e:
                logger.error(f"Approval doc {approval_id} is malformed. Missing key: {str(e)}")
                raise ValueError(f"Approval document is missing a key field: {str(e)}")

            # 4. Check if date is future
            # We treat "Today" and "Tomorrow" as immediate processing (not future)
            from datetime import timedelta
            today = now.date() # Define today here, as it's used in the next line
            tomorrow = today + timedelta(days=1)
            is_future_date = scheduled_at.date() > tomorrow
            
            logger.info(f"Approval {approval_id}: schedule_date={scheduled_at}, today={today}, is_future={is_future_date}")

            # 5. Build the limit_values document structure
            
            # Ensure the limit_values collection exists with indexes before we potentially write to it
            await self.values_repo.ensure_collection_exists(self.values_collection)

            limit_values_doc = {
                "scheduled_at": scheduled_at,
                "block": approval.get("block"),
                "id": approval.get("id"),
                "market": approval.get("market", "RTM"),
                "category": approval.get("category", "Limits"),
                "area": approval.get("area", ""),
                "exchange": approval.get("exchange", "IEX"),
                "values": approval.get("value", approval.get("values")),
                "updated_at": now,
                "updated_by": actor_id,
            }
            
            # Validate required fields
            if limit_values_doc["values"] is None:
                raise ValueError(f"Approval document {approval_id} missing 'value' or 'values' field")
            if limit_values_doc["block"] is None:
                raise ValueError(f"Approval document {approval_id} missing 'block' field")
            if limit_values_doc["id"] is None:
                raise ValueError(f"Approval document {approval_id} missing 'id' field")
            
            logger.info(f"Built limit_values document for approval {approval_id}: {limit_values_doc}")

            # 6. Mark the approval document as "APPROVED"
            await self.flow_repo.update_approval(
                self.approval_collection,
                approval["_id"],
                {
                    "status": "APPROVED",
                    "approved_by": actor_id,
                    "approved_username": actor_username,
                    "approved_at": now,
                    "is_future_dated": is_future_date,
                    "scheduled_at": scheduled_at
                }
            )

            # 7. Write the "APPROVE" audit log
            await self.flow_repo.write_audit(self.audit_collection, {
                "ts": now,
                "actor_id": actor_id,
                "actor_username": actor_username,
                "action": "APPROVE",
                "entity_type": "limit_approval",
                "entity_id": approval["_id"],
                "operation": "UPDATE",
                "request_id": approval["_id"],
                "correlation_id": correlation_id,
                "is_future_dated": is_future_date,
                "scheduled_at": scheduled_at,
                "success": True
            })

            # 8. If date is today or past, move to limit_values collection
            if not is_future_date:
                # Apply the upsert to the main 'limit_values' collection
                update_payload = {
                    "$set": limit_values_doc,
                    "$setOnInsert": {
                        "created_at": now,
                        "created_by": approval.get("requested_by", "anonymous")
                    }
                }
                
                # Debug logging to understand upsert behavior
                logger.info(f"[UPSERT DEBUG] Attempting upsert with key: {key}")
                logger.info(f"[UPSERT DEBUG] Update payload: {update_payload}")
                
                result = await self.values_repo.repository.update_one(
                    self.values_collection,
                    key,  # Unique business key matching limit_values_unique_idx
                    update_payload,
                    upsert=True
                )
                
                logger.info(f"[UPSERT DEBUG] Upsert result - matched: {result.matched_count}, modified: {result.modified_count}, upserted_id: {result.upserted_id}")

                # Write the "APPLY_UPSERT" audit log
                await self.flow_repo.write_audit(self.audit_collection, {
                    "ts": now,
                    "actor_id": actor_id,
                    "actor_username": actor_username,
                    "action": "APPLY_UPSERT",
                    "entity_type": "limit_values",
                    "entity_id": None,
                    "operation": "UPSERT",
                    "request_id": approval["_id"],
                    "correlation_id": correlation_id,
                    "key": key,
                    "applied_data": limit_values_doc,
                    "success": True
                })

                # Delete from approval collection
                await self.flow_repo.write_audit(self.audit_collection, {
                    "ts": now,
                    "actor_id": actor_id,
                    "actor_username": actor_username,
                    "action": "DELETE_APPROVED",
                    "entity_type": "limit_approval",
                    "entity_id": approval["_id"],
                    "operation": "DELETE",
                    "request_id": approval["_id"],
                    "correlation_id": correlation_id,
                    "message": "Deleting approved request from approval collection (date is today or past)",
                    "success": True
                })

                logger.info(f"[LimitService] About to delete approved request: {approval['_id']}")
                await self.flow_repo.delete_approval(self.approval_collection, approval["_id"])
                logger.info(f"[LimitService] Successfully deleted approval: {approval['_id']}")
                
                if audit_logger:
                    try:
                        audit_logger.log_user_action(
                            service="Limit Values",
                            user_id=actor_id,
                            action="approved",
                            message=f"Approved limit value {approval.get('id', '')}",
                            module=approval.get("market", "Limit Values"),
                            action_taken_for=f"Limits/Block: {approval.get('block', '')}/{approval.get('category', '')}",
                            success=True
                        )
                    except Exception as e:
                        logger.error(f"Failed to log audit in approve (moved): {e}")

                return {
                    "status": "APPROVED",
                    "approval_id": str(approval_id),
                    "moved_to_final": True,
                    "scheduled_at": scheduled_at
                }
            else:
                # Future date - keep in approval collection with APPROVED status
                logger.info(f"[LimitService] Future-dated approval {approval['_id']} kept in approval collection until {scheduled_at}")
                
                if audit_logger:
                    try:
                        audit_logger.log_user_action(
                            service="Limit Values",
                            user_id=actor_id,
                            action="approved",
                            message=f"Approved limit value {approval.get('id', '')} (Future dated)",
                            module=approval.get("market", "Limit Values"),
                            action_taken_for=f"Limits/Block: {approval.get('block', '')}/{approval.get('category', '')}",
                            success=True
                        )
                    except Exception as e:
                        logger.error(f"Failed to log audit in approve (future): {e}")

                return {
                    "status": "APPROVED",
                    "approval_id": str(approval_id),
                    "moved_to_final": False,
                    "scheduled_at": scheduled_at,
                    "message": f"Approval will be applied on {scheduled_at}"
                }

        except Exception as e:
            logger.exception(f"[LimitService] approve failed for {approval_id}: {str(e)}")
            # Optionally, mark the item as FAILED
            try:
                await self.flow_repo.update_approval(
                    self.approval_collection,
                    approval_id,
                    {"status": "FAILED", "error_message": str(e)}
                )
            except Exception as update_e:
                logger.error(f"Failed to even mark {approval_id} as FAILED: {str(update_e)}")
            raise

    async def approve_all(self, user_ctx: Dict[str, Any], audit_logger: Optional[AuditLogger] = None) -> Dict[str, Any]:
        try:
            import asyncio
            pending_approvals = await self.flow_repo.list_approvals(self.approval_collection, status="PENDING")
            total_approvals = len(pending_approvals)
            results = await asyncio.gather(
                *[self.approve(approval["_id"], user_ctx, audit_logger) for approval in pending_approvals],
                return_exceptions=True
            )
            approved_count = sum(1 for r in results if not isinstance(r, Exception))
            failed = [str(r) for r in results if isinstance(r, Exception)]
            if failed:
                logger.warning(f"[LimitService] approve_all: {len(failed)} item(s) failed: {failed}")
            return {"total_pending": total_approvals, "total_approved": approved_count, "failed": len(failed)}
        except Exception as e:
            logger.exception(f"[LimitService] approve_all failed: {str(e)}")
            raise

    # --------- Reject ---------
    async def reject(self, approval_id: Any, approver_ctx: Dict[str, Any], reason: Optional[str], audit_logger: Optional[AuditLogger] = None) -> Dict[str, Any]:
        try:
            logger.info(f"[LimitService] reject called with approval_id={approval_id} (type={type(approval_id)})")
            
            # Handle business ID (string) case
            is_valid_oid = False
            try:
                ObjectId(approval_id)
                is_valid_oid = True
            except:
                pass

            logger.info(f"[LimitService] approval_id={approval_id} is_valid_oid={is_valid_oid}")

            if not is_valid_oid:
                logger.info(f"[LimitService] Treating {approval_id} as business ID")
                pending = await self.flow_repo.list_approvals(self.approval_collection, status="PENDING")
                matches = [x for x in pending if str(x.get("id")) == str(approval_id)]
                
                logger.info(f"[LimitService] Found {len(matches)} matches for business ID {approval_id}")
                
                if not matches:
                    raise ValueError(f"No pending approvals found for ID '{approval_id}'")
                
                results = []
                for item in matches:
                    res = await self.reject(item["_id"], approver_ctx, reason)
                    results.append(res)
                return {"message": f"Rejected {len(results)} items for ID {approval_id}", "details": results}

            logger.info(f"[LimitService] Fetching approval doc for {approval_id}")
            approval = await self.flow_repo.get_approval(self.approval_collection, approval_id)
            if not approval:
                logger.error(f"[LimitService] Approval request {approval_id} not found")
                raise ValueError("Approval request not found")
            
            logger.info(f"[LimitService] Found approval {approval['_id']} with status {approval['status']}")
            
            if approval["status"] != "PENDING":
                return {"message": "Already processed", "status": approval["status"]}

            now = datetime.now()
            
            # Ensure we are using the ObjectId from the document we just fetched
            doc_id = approval["_id"]
            
            await self.flow_repo.update_approval(
                self.approval_collection,
                doc_id,
                {
                    "status": "REJECTED",
                    "rejected_by": approver_ctx.get("user_id", "approver"),
                    "rejected_username": approver_ctx.get("username", "approver"),
                    "rejected_at": now,
                    "decision_reason": reason or "Not specified"
                }
            )
            logger.info(f"[LimitService] Updated approval {doc_id} status to REJECTED")

            await self.flow_repo.write_audit(self.audit_collection, {
                "ts": now,
                "actor_id": approver_ctx.get("user_id", "approver"),
                "actor_username": approver_ctx.get("username", "approver"),
                "action": "REJECT",
                "entity_type": "limit_approval",
                "entity_id": approval["_id"],
                "operation": "UPDATE",
                "request_id": approval["_id"],
                "correlation_id": approver_ctx.get("correlation_id", "unknown"),
                "success": True
            })
            
            if audit_logger:
                 try:
                     audit_logger.log_user_action(
                        service="Limit Values",
                        user_id=approver_ctx.get("user_id", "unknown"),
                        action="rejected",
                        message=f"Rejected approval {approval_id}",
                        module="Limit Values",
                        action_taken_for=f"Limits/Rejected: {approval_id}",
                        success=True
                    )
                 except Exception as e:
                     logger.error(f"Failed to log audit in reject: {e}")

            return {"status": "REJECTED"}
        except Exception as e:
            logger.exception(f"[LimitService] reject failed: {str(e)}")
            raise

    async def reject_all(self, user_ctx: Dict[str, Any], reason: Optional[str], audit_logger: Optional[AuditLogger] = None) -> Dict[str, Any]:
        try:
            logger.info("[LimitService] reject_all called")
            pending_approvals = await self.flow_repo.list_approvals(self.approval_collection, status="PENDING")
            total_approvals = len(pending_approvals)
            logger.info(f"[LimitService] Found {total_approvals} pending approvals to reject")
            
            rejected_count = 0
            for approval in pending_approvals:
                try:
                    await self.reject(approval["_id"], user_ctx, reason, audit_logger)
                    rejected_count += 1
                except Exception as ex:
                    logger.error(f"[LimitService] Failed to reject {approval['_id']} in reject_all: {str(ex)}")
            
            logger.info(f"[LimitService] reject_all completed. Rejected {rejected_count}/{total_approvals}")
            return {"total_pending": total_approvals, "total_rejected": rejected_count}
        except Exception as e:
            logger.exception(f"[LimitService] reject_all failed: {str(e)}")
            raise

    # --------- Single Update (goes through approval) ---------
    async def single_update(self, item: Dict[str, Any], user_ctx: Dict[str, Any], audit_logger: Optional[AuditLogger] = None) -> Dict[str, Any]:
        """
        Create approval request for single limit value update.
        Fetches current values and shows both old and new for approver comparison.
        """
        try:
            # Validate the item first
            try:
                normalized_item = _normalize_item(item)
            except Exception as e:
                raise ValueError(f"Invalid item data: {str(e)}")

            if _is_rtm_forecast_past_block(normalized_item):
                raise ValueError(f"{normalized_item.get('id')} update not allowed for this date/time block")

            # Get current values for comparison
            current_values = None
            business_key = _business_key(normalized_item)
            
            # Try to find existing record
            try:
                existing_record = await self.values_repo.repository.find_one(
                    self.values_collection, business_key
                )
                if existing_record:
                    current_values = {
                        "scheduled_at": existing_record.get("scheduled_at"),
                        "block": existing_record.get("block"),
                        "id": existing_record.get("id"),
                        "values": existing_record.get("values"),
                        "market": existing_record.get("market"),
                        "category": existing_record.get("category"),
                        "area": existing_record.get("area"),
                        "exchange": existing_record.get("exchange", "IEX"),
                        "is_active": existing_record.get("is_active"),
                        "version": existing_record.get("version"),
                        "created_at": existing_record.get("created_at"),
                        "updated_at": existing_record.get("updated_at")
                    }
            except Exception as e:
                logger.warning(f"Could not fetch current values for comparison: {e}")
            
            # Create approval request with old/new comparison
            now = datetime.now()
            approval_doc = {
                "status": "PENDING",
                "request_type": "SINGLE_UPDATE",
                "title": f"Single update: {normalized_item['scheduled_at']}-{normalized_item['block']}-{normalized_item['id']}",
                "description": f"Single limit value update by {user_ctx.get('username', 'unknown')}",
                "requested_by": user_ctx.get("user_id", "anonymous"),
                "requested_username": user_ctx.get("username", "anonymous"),
                "requested_at": now,
                "last_edited_at": now,
                "last_edited_by": user_ctx.get("user_id", "anonymous"),
                **normalized_item,  # Spread top-level fields for approve() method
                "items": [{
                    **normalized_item,
                    "old_values": current_values,  # Include old values for comparison
                    "change_type": "UPDATE" if current_values else "INSERT"
                }],
                "counts": {
                    "total_items": 1,
                    "valid_items": 1,
                    "invalid_items": 0,
                    "applied_upserts": 0,
                    "applied_deletes": 0,
                },
                "correlation_id": user_ctx.get("correlation_id", "unknown"),
                "created_at": now,
                "created_by": user_ctx.get("user_id", "anonymous"),
            }
            
            # Create approval request
            approval_id = await self.flow_repo.create_approval(self.approval_collection, approval_doc)

            # Write audit log
            await self.flow_repo.write_audit(self.audit_collection, {
                "ts": now,
                "actor_id": user_ctx.get("user_id", "anonymous"),
                "actor_username": user_ctx.get("username", "anonymous"),
                "action": "SINGLE_UPDATE_STAGED",
                "entity_type": "limit_approval",
                "entity_id": approval_id,
                "operation": "INSERT",
                "request_id": approval_id,
                "correlation_id": user_ctx.get("correlation_id", "unknown"),
                "key": {
                    **{k: (v.isoformat() if hasattr(v, 'isoformat') else v) for k, v in business_key.items()}
                },
                "old_values": current_values,
                "new_values": normalized_item,
                "success": True
            })
            
            logger.info(f"Single update approval request created: {approval_id}")
            
            if audit_logger:
                 try:
                     market_str = normalized_item.get("market", "Limit Values")
                     action_taken_for = f"Limits/Block: {normalized_item.get('block')}/{normalized_item.get('category')}"
                     
                     audit_logger.log_user_action(
                        service="Limit Values",
                        user_id=user_ctx.get("user_id", "unknown"),
                        action="approval",
                        message=f"Created single update request for {normalized_item.get('id')}",
                        module=market_str,
                        action_taken_for=action_taken_for,
                        success=True
                    )
                 except Exception as e:
                     logger.error(f"Failed to log audit in single_update: {e}")

            return {
                "approval_id": str(approval_id),
                "status": "PENDING_APPROVAL",
                "message": "Single update request created and staged for approval",
                "old_values": current_values,
                "new_values": normalized_item,
                "change_type": "UPDATE" if current_values else "INSERT"
            }
            
        except Exception as e:
            logger.exception(f"[LimitService] single_update failed: {str(e)}")
            raise

    async def multi_update(self, items: List[Dict[str, Any]], user_ctx: Dict[str, Any], audit_logger: Optional[AuditLogger] = None) -> Dict[str, Any]:
        """
        Create approval requests for multiple limit value updates.
        """
        try:
            results = []
            batch_id = str(ObjectId())
            success_count = 0
            fail_count = 0
            
            for item in items:
                try:
                    # We can reuse single_update logic but creating a specialized version might be cleaner if we want to share batch_id.
                    # reusing single_update restricts passing batch_id unless we modify it.
                    # For now, let's call single_update and we accept it generates individual requests without a shared batch_id field 
                    # OR we modify approval doc after creation, which is inefficient.
                    # Alternatively, implement logic here similar to single_update.
                    
                    # Implementation similar to single_update + batch_id
                    try:
                        normalized_item = _normalize_item(item)
                    except Exception as e:
                        results.append({"status": "FAILED", "error": str(e), "item": item})
                        fail_count += 1
                        continue

                    if _is_rtm_forecast_past_block(normalized_item):
                        results.append({"status": "FAILED", "error": f"{normalized_item.get('id')} update not allowed for this date/time block", "item": item})
                        fail_count += 1
                        continue

                    business_key = _business_key(normalized_item)
                    current_values = None
                    try:
                        existing_record = await self.values_repo.repository.find_one(
                            self.values_collection, business_key
                        )
                        if existing_record:
                           current_values = {
                                "scheduled_at": existing_record.get("scheduled_at"),
                                "block": existing_record.get("block"),
                                "id": existing_record.get("id"),
                                "values": existing_record.get("values"),
                                "market": existing_record.get("market"),
                                "category": existing_record.get("category"),
                                "area": existing_record.get("area"),
                                "exchange": existing_record.get("exchange", "IEX"),
                                "is_active": existing_record.get("is_active"),
                                "version": existing_record.get("version"),
                                "created_at": existing_record.get("created_at"),
                                "updated_at": existing_record.get("updated_at")
                            }
                    except Exception:
                        pass
                        
                    now = datetime.now()
                    approval_doc = {
                        "status": "PENDING",
                        "request_type": "MULTI_UPDATE",
                        "title": f"Multi update item",
                        "description": f"Part of multi-update batch {batch_id}",
                        "requested_by": user_ctx.get("user_id", "anonymous"),
                        "requested_username": user_ctx.get("username", "anonymous"),
                        "requested_at": now,
                        "last_edited_at": now,
                        "last_edited_by": user_ctx.get("user_id", "anonymous"),
                        "batch_id": batch_id,
                        **normalized_item,
                        "items": [{
                            **normalized_item,
                            "old_values": current_values,
                            "change_type": "UPDATE" if current_values else "INSERT"
                        }],
                        "counts": {
                            "total_items": 1,
                            "valid_items": 1,
                            "invalid_items": 0,
                            "applied_upserts": 0,
                            "applied_deletes": 0,
                        },
                        "correlation_id": user_ctx.get("correlation_id", "unknown"),
                        "created_at": now,
                        "created_by": user_ctx.get("user_id", "anonymous"),
                    }
                    
                    approval_id = await self.flow_repo.create_approval(self.approval_collection, approval_doc)

                    # Audit log for each item
                    await self.flow_repo.write_audit(self.audit_collection, {
                        "ts": now,
                        "actor_id": user_ctx.get("user_id", "anonymous"),
                        "actor_username": user_ctx.get("username", "anonymous"),
                        "action": "MULTI_UPDATE_ITEM",
                        "entity_type": "limit_approval",
                        "entity_id": approval_id,
                        "operation": "INSERT",
                        "request_id": approval_id,
                        "batch_id": batch_id,
                        "correlation_id": user_ctx.get("correlation_id", "unknown"),
                        "success": True
                    })
                    
                    results.append({"status": "STAGED", "approval_id": str(approval_id)})
                    success_count += 1
                except Exception as ex:
                    results.append({"status": "FAILED", "error": str(ex), "item": item})
                    fail_count += 1
                    
            if audit_logger:
                 try:
                     action_taken_for = f"Limits/Batch Update: {len(items)} items"
                     
                     audit_logger.log_user_action(
                        service="Limit Values",
                        user_id=user_ctx.get("user_id", "unknown"),
                        action="approval",
                        message=f"Created batch update request for {len(items)} items",
                        module="Limit Values",
                        action_taken_for=action_taken_for,
                        success=True
                    )
                 except Exception as e:
                     logger.error(f"Failed to log audit in multi_update: {e}")

            return {
                "batch_id": batch_id,
                "results": results,
                "summary": {
                    "total": len(items),
                    "created": len(results),
                    "pending_approvals": len(results)
                }
            }
        except Exception as e:
            logger.exception(f"[LimitService] multi_update failed: {str(e)}")
            raise

    async def approve_batch(self, ids: List[str], user_ctx: Dict[str, Any], audit_logger: Optional[AuditLogger] = None) -> Dict[str, Any]:
        results = []
        success = 0
        failed = 0
        for approval_id in ids:
            try:
                # Assuming approval_id could be string ObjectId
                res = await self.approve(approval_id, user_ctx, audit_logger)
                results.append({"id": approval_id, "status": "APPROVED", "details": res})
                success += 1
            except Exception as e:
                results.append({"id": approval_id, "status": "FAILED", "error": str(e)})
                failed += 1
        return {"total": len(ids), "success": success, "failed": failed, "results": results}

    async def reject_batch(self, ids: List[str], user_ctx: Dict[str, Any], reason: Optional[str], audit_logger: Optional[AuditLogger] = None) -> Dict[str, Any]:
        results = []
        success = 0
        failed = 0
        for approval_id in ids:
            try:
                res = await self.reject(approval_id, user_ctx, reason, audit_logger)
                results.append({"id": approval_id, "status": "REJECTED", "details": res})
                success += 1
            except Exception as e:
                results.append({"id": approval_id, "status": "FAILED", "error": str(e)})
                failed += 1
        return {"total": len(ids), "success": success, "failed": failed, "results": results}
        
    async def edit_batch(self, items: List[Dict[str, Any]], user_ctx: Dict[str, Any]) -> Dict[str, Any]:
        results = []
        success = 0
        failed = 0
        for item in items:
            approval_id = item.get("approval_id")
            if not approval_id:
                results.append({"item": item, "status": "FAILED", "error": "Missing approval_id"})
                failed += 1
                continue
            
            # Remove approval_id from data passed to stage_edit as it expects limit data
            item_data = {k: v for k, v in item.items() if k != "approval_id"}
            
            try:
                # stage_edit expects a list of items to replace the 'items' list in the doc
                # Since we are editing a single approval request that corresponds to a single limit value,
                # we pass a single-item list.
                res = await self.stage_edit(approval_id, [item_data], user_ctx)
                results.append({"id": approval_id, "status": "EDITED", "details": res})
                success += 1
            except Exception as e:
                results.append({"id": approval_id, "status": "FAILED", "error": str(e)})
                failed += 1
        return {"total": len(items), "success": success, "failed": failed, "results": results}
    
    async def generate_ocf_matrix_csv(
    self,
    market: Optional[str] = None,
    schedule_date: Optional[str] = None
) -> str:
        """
        Generates OCF-specific matrix CSV:
        Date, Market, Block, OCF Max Qty, OCF Premium/Discount Price
        """

        try:
            from datetime import datetime, timezone
            import pandas as pd

            filters = {
                "category": "Limits"
            }

            if market:
                filters["market"] = market

            # ✅ Date handling (UTC safe)
            if schedule_date:
                today = parse_schedule_date_filter(schedule_date).replace(tzinfo=timezone.utc)
            else:
                today = datetime.now(timezone.utc)

            start_dt = today.replace(hour=0, minute=0, second=0, microsecond=0)
            end_dt = today.replace(hour=23, minute=59, second=59, microsecond=999999)

            filters["scheduled_at"] = {"$gte": start_dt, "$lte": end_dt}

            docs = await self.values_repo.get_all_data_with_filters(
                self.values_collection,
                filters=filters
            )

            if not docs:
                return "Date,Market,Block"

            # ✅ Correct OCF Mapping
            OCF_DB_TO_CSV_MAP = {
                "ocfMaxOCFQty": "OCF Max Qty",
                "ocfPremiumDiscountPrice": "OCF Premium/Discount Price"
            }

            allowed_ids = set(OCF_DB_TO_CSV_MAP.keys())

            data = []

            for doc in docs:
                db_id = str(doc.get("id", "")).strip()

                # ✅ Strict filter (ONLY OCF fields)
                if db_id not in allowed_ids:
                    continue

                csv_header = OCF_DB_TO_CSV_MAP[db_id]

                datestr = (
                    doc["scheduled_at"].strftime("%Y-%m-%d")
                    if isinstance(doc["scheduled_at"], datetime)
                    else str(doc["scheduled_at"]).split("T")[0]
                )

                row = {
                    "Date": datestr,
                    "Exchange": doc.get("exchange", "IEX"),
                    "Market": doc.get("market", "RTM"),
                    "Block": doc.get("block"),
                    "ID_Header": csv_header,
                    "Value": doc.get("values", doc.get("value"))
                }

                data.append(row)

            # ✅ If no OCF data found
            if not data:
                logger.warning(f"No OCF data found for filters: {filters}")
                return "Date,Exchange,Market,Block"

            df = pd.DataFrame(data)

            # Remove duplicates
            df = df.drop_duplicates(
                subset=["Date", "Exchange", "Market", "Block", "ID_Header"],
                keep="last"
            )

            # Pivot
            pivot_df = df.pivot(
                index=["Date", "Exchange", "Market", "Block"],
                columns="ID_Header",
                values="Value"
            ).reset_index()

            # ✅ Column Order
            preferred_order = [
                "OCF Max Qty",
                "OCF Premium/Discount Price"
            ]

            cols = list(pivot_df.columns)

            for c in ["Date", "Exchange", "Market", "Block"]:
                if c in cols:
                    cols.remove(c)

            ordered_cols = []
            for p in preferred_order:
                if p in cols:
                    ordered_cols.append(p)
                    cols.remove(p)

            ordered_cols.extend(cols)

            final_cols = ["Date", "Exchange", "Market", "Block"] + ordered_cols
            pivot_df = pivot_df[final_cols]

            return pivot_df.to_csv(index=False)

        except Exception as e:
            logger.exception(f"[LimitService] generate_ocf_matrix_csv failed: {str(e)}")
            raise


limit_service = LimitService()