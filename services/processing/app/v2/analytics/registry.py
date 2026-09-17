"""Dataset registry for the analytics dashboard.

Every dataset PTC can tick resolves to an entry here. Availability is data rather
than hardcoded UI so a dataset can be switched on when its storage lands, without
touching the frontend.
"""
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

MARKETS = ("DAM", "RTM", "GDAM", "HPDAM")


class UnknownDatasetError(KeyError):
    """Raised when a dataset code is not in the registry."""


@dataclass(frozen=True)
class Dataset:
    code: str
    label: str
    group: str
    unit: str
    agg: str
    series: str = "scalar"
    availability: str = "live"
    permission: str = "analytics:read"
    kind: str = "direct"
    value: Optional[str] = None
    match: Optional[Dict[str, Any]] = None
    depends: Tuple[str, ...] = ()
    expr: Optional[str] = None


def _d(**kw) -> Dataset:
    return Dataset(**kw)


_ALL: List[Dataset] = [
    # ---- demand group -------------------------------------------------
    _d(code="ACTUAL_DEMAND", label="Actual Demand", group="demand",
       value="$actual", unit="MW", agg="avg", permission="analytics:demand"),
    _d(code="DRAWAL", label="Drawal", group="demand",
       value="$drawal", unit="MW", agg="avg", permission="analytics:demand"),
    _d(code="DEMAND_UDM", label="Demand (UDM)", group="demand",
       value="$comp_udm", unit="MW", agg="avg", permission="analytics:demand"),
    _d(code="ROSTERING", label="Rostering", group="demand",
       value="$rostering", unit="MW", agg="avg", permission="analytics:demand"),
    _d(code="FREQUENCY", label="Frequency", group="demand",
       value="$frequency", unit="Hz", agg="avg", permission="analytics:demand"),
    _d(code="FREQUENCY_FORECAST", label="Forecasted Frequency", group="demand",
       value="$forecasted_frequency", unit="Hz", agg="avg",
       match={"type": "IDF"}, permission="analytics:demand"),
    _d(code="FORECAST_DAF", label="Day Ahead Forecast", group="demand",
       value="$forecast", unit="MW", agg="avg",
       match={"type": "DAF"}, permission="analytics:forecast"),
    _d(code="FORECAST_IDF", label="Intra Day Forecast", group="demand",
       value="$forecast", unit="MW", agg="avg",
       match={"type": "IDF"}, permission="analytics:forecast"),
    _d(code="MAPE_DAF", label="Day Ahead MAPE", group="demand",
       value="$mape", unit="%", agg="avg",
       match={"type": "DAF"}, permission="analytics:forecast"),
    _d(code="MAPE_IDF", label="Intra Day MAPE", group="demand",
       value="$mape", unit="%", agg="avg",
       match={"type": "IDF"}, permission="analytics:forecast"),
    _d(code="FINAL_SCHEDULE_STATE", label="Final Schedule (State)", group="demand",
       value="$schedule", unit="MW", agg="avg", permission="analytics:schedule"),
    _d(code="OD_UD_AMOUNT", label="OD/UD Amount", group="demand",
       value="$net_dsm_amount", unit="INR", agg="sum", permission="analytics:dsm"),
    _d(code="DSM_RATE", label="DSM Rate", group="demand",
       value="$dsm_rate", unit="INR/MWh", agg="avg", permission="analytics:dsm"),
    # ---- derived ------------------------------------------------------
    _d(code="OD_UD_QUANTUM", label="OD/UD Quantum", group="demand", kind="derived",
       depends=("DRAWAL", "FINAL_SCHEDULE_STATE"), expr="drawal - schedule",
       unit="MW", agg="avg", permission="analytics:dsm"),
    # ---- supply group -------------------------------------------------
    _d(code="DC_PLANT", label="Plant wise DC", group="supply", value="$volume",
       unit="MW", agg="avg", series="per_plant",
       match={"dc_sg": "DC"}, permission="analytics:supply"),
    _d(code="SG_PLANT", label="Plant wise SG", group="supply", value="$volume",
       unit="MW", agg="avg", series="per_plant",
       match={"dc_sg": "SG"}, permission="analytics:supply"),
    _d(code="DECLARED_CAPACITY_TOTAL", label="Declared Capacity", group="supply",
       value="$volume", unit="MW", agg="sum",
       match={"dc_sg": "DC"}, permission="analytics:supply"),
    _d(code="SG_TOTAL", label="Scheduled Generation (Total)", group="supply",
       value="$volume", unit="MW", agg="sum",
       match={"dc_sg": "SG"}, permission="analytics:supply"),
    # ---- scheduling ---------------------------------------------------
    _d(code="FINAL_SCHEDULE_PLANT", label="Final Schedule (Plant wise)",
       group="scheduling", value="$volume", unit="MW", agg="avg",
       series="per_plant", permission="analytics:schedule"),
    # ---- peak ---------------------------------------------------------
    _d(code="PEAK_DEMAND", label="Peak Demand", group="peak",
       value="$peak_demand", unit="MW", agg="max", permission="analytics:demand"),
    _d(code="MIN_DEMAND", label="Minimum Demand", group="peak",
       value="$min_demand", unit="MW", agg="min", permission="analytics:demand"),
    _d(code="AVG_DEMAND", label="Average Demand", group="peak",
       value="$average_demand", unit="MW", agg="avg", permission="analytics:demand"),
    _d(code="TOTAL_ENERGY_MU", label="Total Energy", group="peak",
       value="$total_energy_mu", unit="MU", agg="sum", permission="analytics:demand"),
    # ---- absent -------------------------------------------------------
    _d(code="WEEKLY_FORECAST", label="Weekly Forecast", group="demand",
       unit="MW", agg="avg", availability="absent", permission="analytics:forecast"),
    _d(code="MONTHLY_FORECAST", label="Monthly Forecast", group="demand",
       unit="MW", agg="avg", availability="absent", permission="analytics:forecast"),
    _d(code="WEEKLY_MAPE", label="Weekly MAPE", group="demand",
       unit="%", agg="avg", availability="absent", permission="analytics:forecast"),
    _d(code="MONTHLY_MAPE", label="Monthly MAPE", group="demand",
       unit="%", agg="avg", availability="absent", permission="analytics:forecast"),
    _d(code="TAM_TRADED", label="TAM Traded", group="trade",
       unit="MW", agg="sum", availability="absent", permission="analytics:market"),
    _d(code="TAM_BIDDED", label="TAM Bidded", group="trade",
       unit="MW", agg="sum", availability="absent", permission="analytics:market"),
]

# ---- exchange group, generated per market -----------------------------
for _m in MARKETS:
    _acv_value = "$acv.total" if _m == "GDAM" else "$acv"
    _ALL += [
        _d(code=f"ACP_{_m}", label=f"{_m} ACP", group="exchange", value="$acp",
           unit="INR/MWh", agg="avg", series="per_exchange",
           match={"market_type": _m}, permission="analytics:market"),
        _d(code=f"ACV_{_m}", label=f"{_m} ACV (Traded)", group="exchange",
           value=_acv_value, unit="MW", agg="sum", series="per_exchange",
           match={"market_type": _m}, permission="analytics:market"),
        _d(code=f"MKT_BUY_BID_{_m}", label=f"{_m} Buy Bid (Market)", group="exchange",
           value="$buy_bid", unit="MW", agg="sum", series="per_exchange",
           match={"market_type": _m}, permission="analytics:market"),
        _d(code=f"MKT_SELL_BID_{_m}", label=f"{_m} Sell Bid (Market)", group="exchange",
           value="$sell_bid", unit="MW", agg="sum", series="per_exchange",
           match={"market_type": _m}, permission="analytics:market"),
    ]

# ---- trade group, portfolio -------------------------------------------
# bid_trade_serve carries DAM and RTM only; GDAM/HPDAM rows do not exist yet.
for _m in MARKETS:
    _avail = "live" if _m in ("DAM", "RTM") else "empty"
    _ALL += [
        _d(code=f"PF_TRADED_{_m}", label=f"{_m} Traded (Portfolio)", group="trade",
           value="$traded", unit="MW", agg="sum", match={"type": _m},
           availability=_avail, permission="analytics:market"),
        _d(code=f"PF_BIDDED_{_m}", label=f"{_m} Bidded (Portfolio)", group="trade",
           value="$bidded", unit="MW", agg="sum", match={"type": _m},
           availability=_avail, permission="analytics:market"),
    ]

DATASETS: Dict[str, Dataset] = {d.code: d for d in _ALL}


def get(code: str) -> Dataset:
    try:
        return DATASETS[code]
    except KeyError:
        raise UnknownDatasetError(code) from None


def selectable() -> List[Dataset]:
    """Everything the UI may offer — `absent` datasets are rendered disabled."""
    return [d for d in DATASETS.values() if d.availability != "absent"]


def by_group(codes) -> Dict[str, List[Dataset]]:
    """Collapse selected codes into one bucket per source group."""
    out: Dict[str, List[Dataset]] = {}
    for c in codes:
        d = get(c)
        out.setdefault(d.group, []).append(d)
    return out


def series_cardinality(dataset: Dataset, ctx: Dict[str, int]) -> int:
    if dataset.series == "per_plant":
        return max(1, int(ctx.get("plant_count", 1)))
    if dataset.series == "per_exchange":
        return max(1, int(ctx.get("exchange_count", 1)))
    return 1
