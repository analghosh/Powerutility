import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List
from fastapi import HTTPException

from ..repository.block_bid_current_data_repository import BlockBidCurrentDataRepository

logger = logging.getLogger(__name__)

class BlockBidServices:
    def __init__(self, collection_name: str = "block_bid_current_data"):
        self.block_bid_repo = BlockBidCurrentDataRepository(collection_name=collection_name)


    async def insert_block_bid(self, request: Dict[str, Any]) -> Dict[str, Any]:
        now = datetime.now()

        # Determine inserted date
        date = (now + timedelta(days=1)).strftime("%d-%m-%Y") if now.time() > datetime.strptime("22:30", "%H:%M").time() else now.strftime("%d-%m-%Y")
        dateObj = datetime.strptime(date, "%d-%m-%Y")  # type: ignore # parse string to datetime
        formattedDate = dateObj.strftime("%Y-%m-%d")
        if not request:
            raise HTTPException(status_code=400, detail="Invalid request")

        bidding_date = request.get("dates")
        date_obj = datetime.strptime(bidding_date, "%d-%m-%Y")  # type: ignore # parse string to datetime
        formatted_date = date_obj.strftime("%Y-%m-%d")
        time_block_range = []
        data = []

        trade_sessions = request.get("trade_session", [])
        for i in range(len(trade_sessions)):
            time_block = f"{request['from'][i]}-{request['to'][i]}"
            record = {
                "trade_session": int(request["trade_session"][i]),
                "from": request["from"][i],
                "to": request["to"][i],
                "time_block": time_block,
                "quantum": int(request["quantum"][i]),
                "bid_price": float(request["bid_price"][i]),
                "number_of_bids": int(request["number_of_bids"][i]),
                "inserted_date": formattedDate,
                "created_by": request.get("user_id", None),
                "created_time": now.strftime("%d-%m-%Y %H:%M:%S"),
                "approve_by": "",
                "approve_time": "",
                "status": "pending",
                "biding_date": formatted_date,
                "reason": "",
                "type": request["type"],
                "market_type": request["market_type"],
                "ex_split_string": f"IEX-{request['iex_price'][i]}_PXIL-{request['pxil_price'][i]}"
            }
            data.append(record)
            time_block_range.append(time_block)

        # Insert into MongoDB
        try:
            insert_result = await self.block_bid_repo.insert_many(data)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

        # Notification placeholder
        notification_data = {
            "bid_status": "submitForApproval Block Bid",
            "module_name": "Block Bid",
            "title": "RTM Block Bid Submitted for Approval",
            "description": "RTM Block bid submitted for approval of selected time blocks",
            "time_block": time_block_range,
            "submitted_time": now.strftime("%d-%m-%Y %H:%M"),
            "notification_type": "bid_for_Approvel"
        }
        logger.info(f"Notification placeholder: {insert_result}")

        if insert_result:
            return {"message": "Bid Submitted Successfully For Approval."}

        raise HTTPException(status_code=500, detail="Data could not be created")


    async def get_block_bid_submitted_for_approval_list(self, date):
       data = await self.block_bid_repo.get_data(date)
       logger.info(f" placeholder: {data}")
       return data
    
    async def update_block_bid_status(self, request: Dict[str, Any]):
        try:
            
            statuses = request.get("status", [])
            reasons = request.get("reason", [])
            rej_reasons = request.get("rej_reason", [])
            other_remarks = request.get("other_remark", [])
            other_remark_rej = request.get("other_remark_rej", [])
            ids = request.get("id", [])

            for i in range(len(statuses)):
                if statuses[i] is None:
                    continue

                # ✅ Determine remark
                if statuses[i] == "Approved":
                    remark = reasons[i]
                    if remark == "other":
                        remark = other_remarks[i]
                else:
                    remark = rej_reasons[i]
                    if remark == "other":
                        remark = other_remark_rej[i]

                # ✅ Prepare data for update
                data = {
                    "status": statuses[i],
                    "approve_by": request.get("user_id"),  # Replace with auth user
                    "approve_time": datetime.now().strftime("%d-%m-%Y %H:%M:%S"),
                    "remark": remark,
                }
                
                # ✅ Update DB record
                await self.block_bid_repo.updatedata(ids[i], data)

                # ✅ Notification data
                status_label = "Approved" if statuses[i] == "Approved" else "Rejected"
                notification_data = {
                    "value_status": statuses[i],
                    "module_name": "Block Bid",
                    "title": f"RTM Block Bid {status_label} by PTC",
                    "description": f"RTM Block Bid {status_label} for , {remark}",
                    "action": "#",
                    "notification_type": "bid_for_approve_reject",
                }

               
            return {"message": "Bid Approved Successfully"}

        except Exception as e:
            logger.error(f"Failed to get pending approvals in : {str(e)}")
            raise

block_bid_service = BlockBidServices()