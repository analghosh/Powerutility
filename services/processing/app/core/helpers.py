import logging
from datetime import datetime
from typing import List, Dict, Any
logger = logging.getLogger(__name__)

def get_time_block_id(time_str: str):
    """
    Get the 15-minute time block index for a given time string.
    
    :param time_str: Time in "HH:MM" format (e.g., "10:25")
    :return: Integer index of the time block
    """
    # Generate time blocks every 15 minutes
    time_blocks = [f"{h:02}:{m:02}" for h in range(24) for m in (0, 15, 30, 45)]
    time_blocks.append("24:00")  # Add end of the day

    # Convert given time to datetime object for comparison
    input_time = datetime.strptime(time_str, "%H:%M")

    time_index = None
    for i, block in enumerate(time_blocks[:-1]):
        start = datetime.strptime(block, "%H:%M")
        end = datetime.strptime(time_blocks[i + 1], "%H:%M")

        if start <= input_time < end:
            time_index = i
            break

    # Edge case for "24:00"
    if time_index is None and time_str == "24:00":
        time_index = len(time_blocks) - 1

    return time_index

def get_fifteen_time_block_id(idx: int):
    """
    Get the 15-minute time block string for a given index.
    
    :param idx: Index of the time block (0–96)
    :return: Time string in "HH:MM" format
    """
    # Generate all 15-minute intervals
    time_blocks = [f"{h:02}:{m:02}" for h in range(24) for m in (0, 15, 30, 45)]
    time_blocks.append("24:00")  # Add final block

    return time_blocks[idx]

def get_deviation_charge(acp: float, frequency: float) -> float:
    frequency_array = [
        51.50, 51.49, 51.48, 51.47, 51.46, 51.45, 51.44, 51.43, 51.42, 51.41, 51.40, 51.39,
        51.38, 51.37, 51.36, 51.35, 51.34, 51.33, 51.32, 51.31, 51.30, 51.29, 51.28, 51.27,
        51.26, 51.25, 51.24, 51.23, 51.22, 51.21, 51.20, 51.19, 51.18, 51.17, 51.16, 51.15,
    ]

    deviation_charges_array = [
        0.00, 0.00, 0.00, 0.00, 0.00, 0.00, 0.00, 0.00, 0.00, 0.00,
    ]

    deviation_sum_array = [
        1, 1, 1, 1, 1, 1, 1, 1, 1, 1,
    ]

    deviation_divisible_array = [
        1, 1, 1, 1, 1, 1, 1, 1, 1, 1,
    ]

    # Core logic
    if frequency > 50.04:
        deviation_charge = 0.00
    elif frequency < 49.85:
        deviation_charge = 800
    else:
        try:
            idx = frequency_array.index(round(frequency, 2))  # match upto 2 decimals
            deviation_charge = (
                deviation_charges_array[idx] +
                (deviation_sum_array[idx] * acp) / deviation_divisible_array[idx]
            )
        except ValueError:
            deviation_charge = 0.00  # frequency not found

    return deviation_charge


def dsm_calculation(acp: float, data_array, cursor_areapricertm, cursor_areapricedam):
    """
    DSM Calculation  .
    """
    re_rich_state_data = 250
    cap_price = 800

    data = []

    for key, value in enumerate(data_array):
        # Call your existing helper functions
        time_blocks_id = value.get("time_block")
        
        #logger.warning(f"[DashboardService] =========++++++++.: {cursor_areapricertm, time_blocks_id}")
        if time_blocks_id == 96:
            next_time_blocks = get_fifteen_time_block_id(0)
        else:
            next_time_blocks = get_fifteen_time_block_id(time_blocks_id + 1)
        
        start_time = get_fifteen_time_block_id(time_blocks_id )

        frequency = float(value.get("Raw_Frequency", 0))
        schedule = float(value.get("Schedule", 0))
        actual = float(value.get("Drawal", 0))
        if cursor_areapricertm and len(cursor_areapricertm) > key:
            w1_rtm = float(cursor_areapricertm[key].get("W1", 0) or 0)
        else:
            w1_rtm = 0.0

        if cursor_areapricedam and len(cursor_areapricedam) > key:
            w1_dam = float(cursor_areapricedam[key].get("W1", 0) or 0)
        else:
            w1_dam = 0.0

        dsm_rate = max(w1_rtm, w1_dam) / 10
        deviation_charge_array = get_deviation_charge(acp, round(frequency, 2))

        odd_udd = actual - schedule
        if odd_udd > 0:
            difference_in_deviation_under_drawl = 0
            difference_in_deviation_over_drawl = abs(odd_udd)
        else:
            difference_in_deviation_under_drawl = abs(odd_udd)
            difference_in_deviation_over_drawl = 0

        # ----- UNDER DRAWL -----
        if 0 <= difference_in_deviation_under_drawl <= 200:
            lessthentwohundred = (0.9 * dsm_rate * difference_in_deviation_under_drawl) / 40000
        else:
            lessthentwohundred = (0.9 * dsm_rate * 200) / 40000

        if 200 <= difference_in_deviation_under_drawl <= 300:
            lessthenthreehundred = (0.5 * dsm_rate * (difference_in_deviation_under_drawl - 200)) / 40000
        elif difference_in_deviation_under_drawl > 300:
            lessthenthreehundred = (0.5 * dsm_rate * 100) / 40000
        else:
            lessthenthreehundred = 0

        total_dsm_ud = -(lessthentwohundred + lessthenthreehundred)

        # ----- OVER DRAWL -----
        if 0 <= difference_in_deviation_over_drawl <= 200:
            lessthanhundred = (1 * dsm_rate * difference_in_deviation_over_drawl) / 40000
        else:
            lessthanhundred = (1 * dsm_rate * 200) / 40000

        if 200 <= difference_in_deviation_over_drawl <= 300:
            lessthanonehundredtwenty = (1.2 * dsm_rate * (difference_in_deviation_over_drawl - 200)) / 40000
        elif difference_in_deviation_over_drawl > 300:
            lessthanonehundredtwenty = (1.2 * dsm_rate * 100) / 40000
        else:
            lessthanonehundredtwenty = 0

        if difference_in_deviation_over_drawl > 300:
            lessthanhundredfifty = (1.5 * dsm_rate * (difference_in_deviation_over_drawl - 300)) / 40000
        else:
            lessthanhundredfifty = 0

        # ----- FREQUENCY CONDITIONS -----
        if 49.95 <= frequency < 50.03:
            final_less_thanonehundred = lessthentwohundred
            final_less_thanonehundredtwent = lessthenthreehundred
            final_less_lessthanhundredfifty = lessthanhundredfifty
            final_less_hundred = lessthanhundred
            final_less_hundredtwenty = lessthanonehundredtwenty
        else:
            final_less_thanonehundred = 0
            final_less_thanonehundredtwent = 0
            final_less_lessthanhundredfifty = 0
            final_less_hundred = 0
            final_less_hundredtwenty = 0

        fourtyninpontninezeroandnightyfive = (1.2 * dsm_rate * difference_in_deviation_under_drawl) / 40000 if 49.90 < frequency < 49.95 else 0
        fourtyninpontninezero = (1.5 * dsm_rate * difference_in_deviation_under_drawl) / 40000 if frequency <= 49.90 else 0
        fiftypontzerothree = (0.5 * dsm_rate * difference_in_deviation_under_drawl) / 40000 if 50.03 <= frequency < 50.05 else 0
        fiftypontninezerothree = (0 * dsm_rate * difference_in_deviation_under_drawl) / 40000 if frequency >= 50.03 else 0

        fourtyninpontninezeroandnightyfive_1 = (1.2 * dsm_rate * difference_in_deviation_over_drawl) / 40000 if 49.90 < frequency < 49.95 else 0
        fourtyninpontninezero_2 = (2 * dsm_rate * difference_in_deviation_over_drawl) / 40000 if frequency <= 49.90 else 0
        fiftypontzerothree_3 = (0.75 * dsm_rate * difference_in_deviation_over_drawl) / 40000 if 50.03 <= frequency < 50.05 else 0
        fiftypontninezerothree_4 = (0 * dsm_rate * difference_in_deviation_over_drawl) / 40000 if frequency >= 50.03 else 0

        # ----- TOTAL DSM -----
        total_dsm_ud = -1 * (
            fiftypontninezerothree
            + fiftypontzerothree
            + fourtyninpontninezero
            + final_less_thanonehundred
            + final_less_thanonehundredtwent
            + fourtyninpontninezeroandnightyfive
        )

        total_dsm_od = (
            fiftypontninezerothree_4
            + fiftypontzerothree_3
            + fourtyninpontninezero_2
            + final_less_hundred
            + final_less_hundredtwenty
            + fourtyninpontninezeroandnightyfive_1
            + final_less_lessthanhundredfifty
        )

        net_dsm = total_dsm_ud + total_dsm_od

        # ----- Build Result -----
        row = {
            "inserted_date": value.get("inserted_date"),
            "start_time": start_time,
            "time_block": time_blocks_id,
            "net_dsm_amount": f"{net_dsm:.2f}",
            "dsm_rate": f"{dsm_rate:.2f}",
        }

        data.append(row)

    return data if data else "Error: data not found!"  


def calculate_demand_metrics(actual: float, forecast: float, rostering: float) -> Dict[str, float]:
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
        # Calculate MPE: ((actual + rostering - forecast) / (actual + rostering)) * 100
        denominator = actual + rostering
        if denominator != 0:
            mpe = ((actual + rostering - forecast) / denominator) * 100
        else:
            mpe = 0.0
            
        # Calculate MAPE: absolute value of MPE
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
        
        return {
            "mpe": round(mpe, 2),
            "mape": round(mape, 2), 
            "comp_act_demand": round(comp_act_demand, 2),
            "comp_udm": round(comp_udm, 2)
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
            "comp_udm": fallback_comp_udm
        }


def process_demand_calculations(data_array: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Process demand data array and add calculated metrics.
    
    Args:
        data_array: List of demand data dictionaries
        
    Returns:
        List of demand data with calculated metrics added
    """
    processed_data = []
    
    for item in data_array:
        try:
            # Extract values
            actual = float(item.get("actual", 0))
            forecast = float(item.get("forecast", 0))
            rostering = float(item.get("rostering", 0))
            
            # Calculate metrics
            metrics = calculate_demand_metrics(actual, forecast, rostering)
            
            # Add calculated metrics to the item
            item.update(metrics)
            processed_data.append(item)
            
        except Exception as e:
            logger.error(f"Error processing demand item: {str(e)}")
            # Add item with default values if calculation fails using same logic
            actual_val = float(item.get("actual", 0))
            forecast_val = float(item.get("forecast", 0))
            rostering_val = float(item.get("rostering", 0))
            
            fallback_comp_act_demand = actual_val if (actual_val and actual_val != 0) else (forecast_val - rostering_val)
            fallback_comp_udm = (actual_val + rostering_val) if (actual_val and actual_val != 0) else forecast_val
            
            item.update({
                "mpe": 0.0,
                "mape": 0.0,
                "comp_act_demand": fallback_comp_act_demand,
                "comp_udm": fallback_comp_udm
            })
            processed_data.append(item)
    
    return processed_data

