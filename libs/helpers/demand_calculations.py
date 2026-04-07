"""
Demand calculation helper functions for conditional field calculation.
"""

import logging
from datetime import datetime
from typing import Dict, List, Any, Optional
from .dsm_calculation import get_deviation_charge, get_fifteen_time_block_id, dsm_calculation, get_time_block_id

logger = logging.getLogger(__name__)


def calculate_dsm_metrics(
    acp: float, 
    data_item: Dict[str, Any], 
    cursor_areapricertm: Optional[List[Dict]] = None,
    cursor_areapricedam: Optional[List[Dict]] = None,
    index: int = 0
) -> Dict[str, Any]:
    """
    Calculate DSM (Demand Side Management) metrics for a single data item.
    Uses dsm_calculation function for accurate calculations.
    
    Args:
        acp: Area clearing price
        data_item: Single data item containing frequency, schedule, actual drawal, etc.
        cursor_areapricertm: RTM price data cursor (optional)
        cursor_areapricedam: DAM price data cursor (optional)
        index: Index of the current item in the data array
        
    Returns:
        Dict containing calculated DSM metrics
    """
    try:
        # Extract basic data
        time_blocks_id = data_item.get("time_block", 0)
        frequency = float(data_item.get("frequency", data_item.get("Raw_Frequency", 0)))
        schedule = float(data_item.get("schedule", data_item.get("Schedule", 0)))
        actual = float(data_item.get("actual", data_item.get("Drawal", 0)))
        drawal = float(data_item.get("drawal", data_item.get("Drawal", 0)))
        
        # Get start_time from time_block (time_block is 1-indexed, convert to 0-indexed for function)
        start_time = get_fifteen_time_block_id(time_blocks_id - 1 if time_blocks_id > 0 else 0)
        
        # Get RTM and DAM prices (for reference, but dsm_calculation doesn't use them)
        w1_rtm = 0.0
        w1_dam = 0.0
        
        if cursor_areapricertm and len(cursor_areapricertm) > index:
            w1_rtm = float(cursor_areapricertm[index].get("W1", 0) or 0)
        
        if cursor_areapricedam and len(cursor_areapricedam) > index:
            w1_dam = float(cursor_areapricedam[index].get("W1", 0) or 0)
        
        # Debug logging
        logger.debug(f"DSM Calculation Debug - Index: {index}, Frequency: {frequency}, Schedule: {schedule}, Actual: {actual}")
        logger.debug(f"DSM Calculation Debug - W1_RTM: {w1_rtm}, W1_DAM: {w1_dam}, ACP: {acp}")
        
        # Prepare data array in the format expected by dsm_calculation
        inserted_date = data_item.get("inserted_date")
        if not inserted_date:
            inserted_date = datetime.now().strftime('%Y-%m-%d')
        
        data_array = [{
            'start_time': start_time,
            'frequency': frequency,
            'schedule': schedule,
            'drawal': drawal,
            'inserted_date': inserted_date,
            'demand': actual if actual else drawal
        }]
        
        # Call dsm_calculation - returns dict with numeric keys (0, 1, 2...)
        dsm_result = dsm_calculation(acp, data_array)
        
        # Access the first element (key 0) from the result
        # dsm_calculation returns comprehensive DSM metrics
        if dsm_result and isinstance(dsm_result, dict) and 0 in dsm_result:
            result_item = dsm_result[0]
            
            # Extract values from dsm_calculation result
            net_dsm_amount = float(result_item.get('net_dsm_amount', 0.0))
            deviation_rate = float(result_item.get('deviation_rate', 0.0))
            odd_udd = float(result_item.get('odd_udd', 0.0))
            
            # Extract additional components from dsm_calculation
            basic_deviation_charge = float(result_item.get('basic_deviation_charge', 0.0))
            capped_dsm = float(result_item.get('capped_dsm', 0.0))
            additional_dsm = float(result_item.get('additional_dsm', 0.0))
            under_drawal_penalty = float(result_item.get('under_drawal_penalty', 0.0))
            sign_violation = float(result_item.get('sign_violation', 0.0))
            total_deviation_amount = float(result_item.get('total_deviation_amount', 0.0))
            
            # Calculate under/over drawal for return values
            if odd_udd > 0:
                difference_in_deviation_under_drawl = 0
                difference_in_deviation_over_drawl = abs(odd_udd)
            else:
                difference_in_deviation_under_drawl = abs(odd_udd)
                difference_in_deviation_over_drawl = 0
            
            # Calculate total_dsm_ud and total_dsm_od based on sign of deviation
            # If under-drawal (negative odd_udd), net_dsm_amount will be negative
            # If over-drawal (positive odd_udd), net_dsm_amount will be positive
            if odd_udd < 0:
                # Under-drawal: net_dsm_amount is negative
                total_dsm_ud = net_dsm_amount
                total_dsm_od = 0.0
            elif odd_udd > 0:
                # Over-drawal: net_dsm_amount is positive
                total_dsm_ud = 0.0
                total_dsm_od = net_dsm_amount
            else:
                # No deviation
                total_dsm_ud = 0.0
                total_dsm_od = 0.0
            
            # Use deviation_rate as dsm_rate (as per business logic)
            dsm_rate = deviation_rate
            
            logger.debug(f"DSM Final Values from dsm_calculation - DSM Rate: {dsm_rate}, Net DSM: {net_dsm_amount}")
            
            # Return comprehensive DSM metrics
            return {
                "inserted_date": inserted_date,
                "start_time": start_time,
                "time_block": time_blocks_id,
                "net_dsm_amount": round(net_dsm_amount, 2),
                "dsm_rate": round(dsm_rate, 2),
                "total_dsm_ud": round(total_dsm_ud, 2),
                "total_dsm_od": round(total_dsm_od, 2),
                "deviation_charge": round(deviation_rate, 2),
                "frequency": round(frequency, 2),
                "actual_drawal": round(actual, 2),
                "schedule": round(schedule, 2),
                "deviation": round(odd_udd, 2),
                "under_drawal": round(difference_in_deviation_under_drawl, 2),
                "over_drawal": round(difference_in_deviation_over_drawl, 2),
                "basic_deviation_charge": round(basic_deviation_charge, 2),
                "total_deviation_amount": round(total_deviation_amount, 2),
                "capped_dsm": round(capped_dsm, 2),
                "additional_dsm": round(additional_dsm, 2),
                "under_drawal_penalty": round(under_drawal_penalty, 2),
                "sign_violation": round(sign_violation, 2)
            }
        else:
            # Fallback: return error response
            logger.error(f"dsm_calculation returned invalid result: {dsm_result}")
            return {
                "inserted_date": data_item.get("inserted_date", datetime.now().strftime('%Y-%m-%d')),
                "start_time": start_time,
                "time_block": time_blocks_id,
                "net_dsm_amount": 0.0,
                "dsm_rate": 0.0,
                "error": "dsm_calculation returned invalid result"
            }
        
    except Exception as e:
        logger.error(f"Error calculating DSM metrics: {str(e)}")
        return {
            "inserted_date": data_item.get("inserted_date"),
            "start_time": data_item.get("start_time", ""),
            "time_block": data_item.get("time_block", 0),
            "net_dsm_amount": 0.0,
            "dsm_rate": 0.0,
            "error": str(e)
        }



def calculate_demand_metrics(actual: float, forecast: float, frequency: float, time_block: int, rostering: float, rtm_data: float, dam_data: float, acp: float, drawal: float = 0.0, schedule: float = 0.0) -> Dict[str, float]:
    """
    Calculate demand metrics including MPE, MAPE, comp_act_demand, and comp_udm.
    
    Args:
        actual: Actual demand value
        forecast: Forecast demand value  
        rostering: Rostering value
        
    Returns:
        Dict containing calculated metrics
    """
    try:
        # Sanitize inputs to ensure they are floats (handling None passed by callers)
        actual = float(actual or 0.0)
        forecast = float(forecast or 0.0)
        rostering = float(rostering or 0.0)
        frequency = float(frequency or 0.0)
        rtm_data = float(rtm_data or 0.0)
        dam_data = float(dam_data or 0.0)
        acp = float(acp or 0.0)
        drawal = float(drawal or 0.0)
        schedule = float(schedule or 0.0)
        
        # Calculate MPE/MAPE only when actual > 0
        # if actual == 0: no MAPE/MPE calculation
        if actual == 0:
            mpe = 0.0
            mape = 0.0
        else:
            denominator = actual + rostering
            if denominator != 0:
                mpe = ((actual + rostering - forecast) / denominator) * 100
            else:
                mpe = 0.0
            mape = abs(mpe)
        
        # Calculate comp_act_demand: if actual is available then actual, otherwise forecast - rostering
        if actual and actual != 0:
            comp_act_demand = actual
        else:
            comp_act_demand = forecast - rostering
        
        # Calculate comp_udm: if actual is available then actual + rostering, otherwise forecast
        if actual and actual != 0:
            comp_udm = actual + rostering
        else:
            comp_udm = forecast

        # Initialize dsm_rate with default value
        # dsm_calculation expects an array of dictionaries with specific keys
        # Get start_time from time_block (time_block is 1-indexed, convert to 0-indexed for function)
        start_time = get_fifteen_time_block_id(time_block - 1 if time_block > 0 else 0)
        
        # Prepare data array in the format expected by dsm_calculation
        data_array = [{
            'start_time': start_time,
            'frequency': frequency,
            'schedule': schedule,
            'drawal': drawal,  
            'inserted_date': datetime.now().strftime('%Y-%m-%d'),
            'demand': actual if actual else drawal  
        }]
        
        # Call dsm_calculation - returns dict with numeric keys (0, 1, 2...)
        dsm_result = dsm_calculation(acp, data_array)
        
        # Access the first element (key 0) from the result
        # dsm_calculation returns:
        #   - 'deviation_rate': deviation charge based on frequency 
        #   - 'net_dsm_amount': final calculated net DSM amount (complex calculation)
        if dsm_result and isinstance(dsm_result, dict) and 0 in dsm_result:
            result_item = dsm_result[0]
            net_dsm_amount = float(result_item.get('net_dsm_amount', 0.0))
            dsm_rate = float(result_item.get('deviation_rate', 0.0))
        else:
            dsm_rate = 0.0
            net_dsm_amount = 0.0
        
        
        return {
            "mpe": round(mpe, 2),
            "mape": round(mape, 2), 
            "comp_act_demand": round(comp_act_demand, 2),
            "comp_udm": round(comp_udm, 2),
            # "net_dsm_amount": round(net_dsm_amount, 2),
            # "dsm_rate": round(dsm_rate, 2)
        }
        
    except Exception as e:
        logger.error(f"Error calculating demand metrics: {str(e)}")
        # Apply same logic for fallback values
        fallback_comp_act_demand = actual if (actual and actual != 0) else (forecast - rostering)
        fallback_comp_udm = (actual + rostering) if (actual and actual != 0) else forecast
        
        return {
            "mpe": 0.0,
            "mape": 0.0,
            "comp_act_demand": fallback_comp_act_demand,
            "comp_udm": fallback_comp_udm,
            "net_dsm_amount": 0.0,
            "dsm_rate": 0.0
        }


def is_field_empty_or_zero(value: Any) -> bool:
    """
    Check if a field value is empty, zero, or null.
    
    Args:
        value: Field value to check
        
    Returns:
        bool: True if field is empty, zero, or null
    """
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, (int, float)):
        return value == 0
    return False


def process_demand_calculations_conditional(data_array: List[Dict[str, Any]], force_recalculate: bool = False) -> List[Dict[str, Any]]:
    """
    Process demand data array and add calculated metrics only for fields that are empty, zero, or null.
    
    Args:
        data_array: List of demand data dictionaries
        force_recalculate: If True, recalculate all fields regardless of existing values
        
    Returns:
        List of demand data with calculated metrics added only where needed
    """
    processed_data = []
    
    for item in data_array:
        try:
            # Create a copy of the item to avoid modifying the original
            processed_item = item.copy()
            
            # Extract values for calculation
            # Extract values for calculation
            actual = float(item.get("actual") or 0)
            forecast = float(item.get("forecast") or 0)
            rostering = float(item.get("rostering") or 0)
            frequency = float(item.get("frequency") or 0)
            time_block = int(item.get("time_block") or 0)
            rtm_data = float(item.get("rtm_data") or 0)
            dam_data = float(item.get("dam_data") or 0)
            acp = float(item.get("acp") or 0)
            drawal = float(item.get("drawal") or 0)
            schedule = float(item.get("schedule") or 0)
            
            # Check which fields need calculation
            fields_to_calculate = []
            
            # Always recalculate MPE/MAPE to ensure correct values
            # (These are critical metrics that should always be up-to-date)
            fields_to_calculate.append("mpe")
            fields_to_calculate.append("mape")
            
            # Check if comp_act_demand needs calculation
            if force_recalculate or is_field_empty_or_zero(item.get("comp_act_demand")):
                fields_to_calculate.append("comp_act_demand")
            
            # Check if comp_udm needs calculation
            if force_recalculate or is_field_empty_or_zero(item.get("comp_udm")):
                fields_to_calculate.append("comp_udm")
            
            # Always recalculate DSM fields to fix previous incorrect calculations
            # (Previous bug: drawal=0, schedule=0 was being passed)
            fields_to_calculate.append("dsm_rate")
            fields_to_calculate.append("net_dsm_amount")
            
            # Only calculate if at least one field needs calculation
            if fields_to_calculate:
                logger.debug(f"Calculating fields for demand record: {fields_to_calculate}")
                metrics = calculate_demand_metrics(actual, forecast, frequency, time_block, rostering, rtm_data, dam_data, acp, drawal, schedule)
                
                # Add only the calculated fields that were missing
                for field in fields_to_calculate:
                    if field in metrics:
                        processed_item[field] = metrics[field]
                        logger.debug(f"Calculated {field}: {metrics[field]}")
            else:
                logger.debug("All calculated fields already present in raw data, skipping calculation")
            
            processed_data.append(processed_item)
            
        except Exception as e:
            logger.error(f"Error processing demand item: {str(e)}")
            # Add item with original data if calculation fails
            processed_data.append(item)
    
    return processed_data


# def process_dsm_calculations_conditional(
#     data_array: List[Dict[str, Any]], 
#     acp: Optional[float] = None,
#     cursor_areapricertm: Optional[List[Dict]] = None,
#     cursor_areapricedam: Optional[List[Dict]] = None
# ) -> List[Dict[str, Any]]:
#     """
#     Process DSM calculations with optional exchange data parameters.
    
#     Args:
#         data_array: List of demand data dictionaries
#         acp: Area clearing price (optional)
#         cursor_areapricertm: RTM data cursor (optional)
#         cursor_areapricedam: DAM data cursor (optional)
        
#     Returns:
#         List of demand data with calculated DSM metrics added only where needed
#     """
#     processed_data = []
    
#     # Default ACP if not provided
#     if acp is None:
#         acp = 0.0
#         logger.warning("ACP not provided, using default value of 0.0")
    
#     logger.debug(f"Processing DSM calculations with acp={acp}, rtm_data={cursor_areapricertm is not None}, dam_data={cursor_areapricedam is not None}")
    
#     for index, item in enumerate(data_array):
#         try:
#             # Create a copy of the item to avoid modifying the original
#             processed_item = item.copy()
            
#             # Check which DSM fields need calculation
#             dsm_fields_to_calculate = []
            
#             # Check if DSM fields need calculation
#             if is_field_empty_or_zero(item.get("net_dsm_amount")):
#                 dsm_fields_to_calculate.append("net_dsm_amount")
            
#             if is_field_empty_or_zero(item.get("dsm_rate")):
#                 dsm_fields_to_calculate.append("dsm_rate")
            
#             if is_field_empty_or_zero(item.get("total_dsm_ud")):
#                 dsm_fields_to_calculate.append("total_dsm_ud")
            
#             if is_field_empty_or_zero(item.get("total_dsm_od")):
#                 dsm_fields_to_calculate.append("total_dsm_od")
            
#             if is_field_empty_or_zero(item.get("deviation_charge")):
#                 dsm_fields_to_calculate.append("deviation_charge")
            
#             # Only calculate DSM fields if at least one needs calculation
#             if dsm_fields_to_calculate:
#                 logger.debug(f"Calculating DSM fields for record {index}: {dsm_fields_to_calculate}")
#                 dsm_metrics = calculate_dsm_metrics(
#                     acp=acp,
#                     data_item=item,
#                     cursor_areapricertm=cursor_areapricertm,
#                     cursor_areapricedam=cursor_areapricedam,
#                     index=index
#                 )
                
#                 # Add only the calculated DSM fields that were missing
#                 for field in dsm_fields_to_calculate:
#                     if field in dsm_metrics:
#                         processed_item[field] = dsm_metrics[field]
#                         logger.debug(f"Calculated DSM {field}: {dsm_metrics[field]}")
                
#                 # Also add additional DSM fields if they were calculated
#                 additional_dsm_fields = [
#                     "frequency", "actual_drawal", "schedule", "deviation", 
#                     "under_drawal", "over_drawal", "start_time"
#                 ]
#                 for field in additional_dsm_fields:
#                     if field in dsm_metrics and field not in processed_item:
#                         processed_item[field] = dsm_metrics[field]
#             else:
#                 logger.debug(f"All DSM fields already present in record {index}, skipping DSM calculation")
            
#             # Also process regular demand calculations
#             processed_item = process_single_demand_calculation(processed_item)
            
#             processed_data.append(processed_item)
            
#         except Exception as e:
#             logger.error(f"Error processing DSM item {index}: {str(e)}")
#             # Add item with original data if calculation fails
#             processed_data.append(item)
    
#     return processed_data


def process_single_demand_calculation(item: Dict[str, Any]) -> Dict[str, Any]:
    """
    Process demand calculations for a single item.
    
    Args:
        item: Single demand data dictionary
        
    Returns:
        Dictionary with calculated demand metrics added only where needed
    """
    try:
        # Extract values for calculation
        # Extract values for calculation
        actual = float(item.get("actual") or 0)
        forecast = float(item.get("forecast") or 0)
        rostering = float(item.get("rostering") or 0)
        rtm_data = float(item.get("rtm_data") or 0)
        dam_data = float(item.get("dam_data") or 0)
        frequency = float(item.get("frequency") or 0)
        time_block = int(item.get("time_block") or 0)
        acp = float(item.get("acp") or 0)
        drawal = float(item.get("drawal") or 0)
        schedule = float(item.get("schedule") or 0)
        
        # Check which fields need calculation
        fields_to_calculate = []
        
        # Always recalculate MPE/MAPE to ensure correct values
        fields_to_calculate.append("mpe")
        fields_to_calculate.append("mape")
        
        # Check if comp_act_demand needs calculation
        if is_field_empty_or_zero(item.get("comp_act_demand")):
            fields_to_calculate.append("comp_act_demand")
        
        # Check if comp_udm needs calculation
        if is_field_empty_or_zero(item.get("comp_udm")):
            fields_to_calculate.append("comp_udm")
        
        # Always recalculate DSM fields
        fields_to_calculate.append("dsm_rate")
        fields_to_calculate.append("net_dsm_amount")
        
        # Only calculate if at least one field needs calculation
        if fields_to_calculate:
            logger.debug(f"Calculating demand fields: {fields_to_calculate}")
            metrics = calculate_demand_metrics(actual, forecast, frequency, time_block, rostering, rtm_data, dam_data, acp, drawal, schedule)
            
            # Add only the calculated fields that were missing
            for field in fields_to_calculate:
                if field in metrics:
                    item[field] = metrics[field]
                    logger.debug(f"Calculated {field}: {metrics[field]}")
        else:
            logger.debug("All demand calculated fields already present, skipping calculation")
        
        return item
        
    except Exception as e:
        logger.error(f"Error processing demand calculations: {str(e)}")
        return item


def get_calculation_summary(data_array: List[Dict[str, Any]]) -> Dict[str, int]:
    """
    Get summary of how many records need calculation for each field.
    
    Args:
        data_array: List of demand data dictionaries
        
    Returns:
        Dict with count of records needing calculation for each field
    """
    summary = {
        "total_records": len(data_array),
        "mape_calculated": 0,
        "mpe_calculated": 0,
        "comp_act_demand_calculated": 0,
        "comp_udm_calculated": 0,
        "dsm_rate_calculated": 0,
        "net_dsm_amount_calculated": 0,
        "no_calculation_needed": 0
    }
    
    for item in data_array:
        needs_calculation = False
        
        if is_field_empty_or_zero(item.get("mape")):
            summary["mape_calculated"] += 1
            needs_calculation = True
        
        if is_field_empty_or_zero(item.get("mpe")):
            summary["mpe_calculated"] += 1
            needs_calculation = True
        
        if is_field_empty_or_zero(item.get("comp_act_demand")):
            summary["comp_act_demand_calculated"] += 1
            needs_calculation = True
        
        if is_field_empty_or_zero(item.get("comp_udm")):
            summary["comp_udm_calculated"] += 1
            needs_calculation = True
        
        if is_field_empty_or_zero(item.get("dsm_rate")):
            summary["dsm_rate_calculated"] += 1
            needs_calculation = True
        
        if is_field_empty_or_zero(item.get("net_dsm_amount")):
            summary["net_dsm_amount_calculated"] += 1
            needs_calculation = True

        if not needs_calculation:
            summary["no_calculation_needed"] += 1
    
    return summary


# def get_dsm_calculation_summary(data_array: List[Dict[str, Any]]) -> Dict[str, int]:
#     """
#     Get summary of how many records need DSM calculation for each field.
    
#     Args:
#         data_array: List of demand data dictionaries
        
#     Returns:
#         Dict with count of records needing DSM calculation for each field
#     """
#     summary = {
#         "total_records": len(data_array),
#         "net_dsm_amount_calculated": 0,
#         "dsm_rate_calculated": 0,
#         "total_dsm_ud_calculated": 0,
#         "total_dsm_od_calculated": 0,
#         "deviation_charge_calculated": 0,
#         "no_dsm_calculation_needed": 0
#     }
    
#     for item in data_array:
#         needs_dsm_calculation = False
        
#         if is_field_empty_or_zero(item.get("net_dsm_amount")):
#             summary["net_dsm_amount_calculated"] += 1
#             needs_dsm_calculation = True
        
#         if is_field_empty_or_zero(item.get("dsm_rate")):
#             summary["dsm_rate_calculated"] += 1
#             needs_dsm_calculation = True
        
#         if is_field_empty_or_zero(item.get("total_dsm_ud")):
#             summary["total_dsm_ud_calculated"] += 1
#             needs_dsm_calculation = True
        
#         if is_field_empty_or_zero(item.get("total_dsm_od")):
#             summary["total_dsm_od_calculated"] += 1
#             needs_dsm_calculation = True
        
#         if is_field_empty_or_zero(item.get("deviation_charge")):
#             summary["deviation_charge_calculated"] += 1
#             needs_dsm_calculation = True
        
#         if not needs_dsm_calculation:
#             summary["no_dsm_calculation_needed"] += 1
    
#     return summary


def get_combined_calculation_summary(data_array: List[Dict[str, Any]]) -> Dict[str, int]:
    """
    Get combined summary of both demand and DSM calculations.
    
    Args:
        data_array: List of demand data dictionaries
        
    Returns:
        Dict with count of records needing calculation for each field type
    """
    demand_summary = get_calculation_summary(data_array)
    
    # Combine summaries
    combined = {
        "total_records": demand_summary["total_records"],
        "demand_calculations": {
            "mape_calculated": demand_summary["mape_calculated"],
            "mpe_calculated": demand_summary["mpe_calculated"],
            "comp_act_demand_calculated": demand_summary["comp_act_demand_calculated"],
            "comp_udm_calculated": demand_summary["comp_udm_calculated"],
            "dsm_rate_calculated": demand_summary.get("dsm_rate_calculated", 0),
            "net_dsm_amount_calculated": demand_summary.get("net_dsm_amount_calculated", 0),
            "no_demand_calculation_needed": demand_summary["no_calculation_needed"]
        }
    }
    
    return combined


# def example_dsm_usage():
#     """
#     Example function demonstrating how to use the DSM calculation functions.
#     This is for documentation and testing purposes.
#     """
#     # Sample data array
#     sample_data = [
#         {
#             "time_block": 1,
#             "inserted_date": "2024-01-01",
#             "Raw_Frequency": 49.95,
#             "Schedule": 1000.0,
#             "Drawal": 1050.0,
#             "actual": 1000.0,
#             "forecast": 1100.0,
#             "rostering": 50.0
#         },
#         {
#             "time_block": 2,
#             "inserted_date": "2024-01-01", 
#             "Raw_Frequency": 50.02,
#             "Schedule": 1200.0,
#             "Drawal": 1150.0,
#             "actual": 1200.0,
#             "forecast": 1250.0,
#             "rostering": 30.0
#         }
#     ]
    
#     # Sample RTM and DAM price data
#     rtm_data = [
#         {"W1": 5000.0},
#         {"W1": 5200.0}
#     ]
    
#     dam_data = [
#         {"W1": 4800.0},
#         {"W1": 5100.0}
#     ]
    
#     # ACP value
#     acp = 100.0
    
#     # Process DSM calculations
#     result = process_dsm_calculations_conditional(
#         data_array=sample_data,
#         acp=acp,
#         cursor_areapricertm=rtm_data,
#         cursor_areapricedam=dam_data
#     )
    
#     # Get calculation summary
#     summary = get_combined_calculation_summary(sample_data)
    
#     return {
#         "processed_data": result,
#         "calculation_summary": summary
#     }
