"""Dataset registry for the analytics dashboard.

Every dataset PTC can tick resolves to an entry here. Availability is data rather
than hardcoded UI so a dataset can be switched on when its storage lands, without
touching the frontend.
"""
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

MARKETS = ("DAM", "RTM", "GDAM", "HPDAM")
FORECAST_TYPES = ("DAF", "IDF")           # Day Ahead, Intra Day
FORECAST_TYPE_COUNT = len(FORECAST_TYPES)


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
    # A roll-up dataset (weekly/monthly forecast or MAPE) is meaningless at any other
    # granularity, so the service forces this date grouping when the dataset is selected.
    fixed_grouping: Optional[str] = None
    # A per-entity breakdown of a dataset that also exists as a state-level total.
    # Listed after the totals and badged in the UI; never the default pick.
    drilldown: bool = False


def _d(**kw) -> Dataset:
    return Dataset(**kw)


_ALL: List[Dataset] = [
    # ---- demand group -------------------------------------------------
    # PTC decision 2026-09-17: Actual Demand is `drawal`, not `actual`. The two are
    # never equal (drawal ≈ 0.9676 × actual — a loss multiplier); `actual` is the gross
    # figure. A separate DRAWAL dataset would duplicate this one, so there is none.
    _d(code="ACTUAL_DEMAND", label="Actual Demand", group="demand",
       value="$drawal", unit="MW", agg="avg", permission="analytics:demand"),
    # PTC defines UDM as actual + rostering. comp_udm IS exactly that wherever actual
    # exists (1,864 rows, 0 mismatches) and is additionally populated on rows where
    # actual has not landed yet, so it is the better source. (`udm` is zero everywhere.)
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
    _d(code="FINAL_SCHEDULE_STATE", label="Final Schedule", group="demand",
       value="$schedule", unit="MW", agg="avg", permission="analytics:schedule"),
    _d(code="OD_UD_AMOUNT", label="OD/UD Amount", group="demand",
       value="$net_dsm_amount", unit="INR", agg="sum", permission="analytics:dsm"),
    _d(code="DSM_RATE", label="DSM Rate", group="demand",
       value="$dsm_rate", unit="INR/MWh", agg="avg", permission="analytics:dsm"),
    # ---- derived ------------------------------------------------------
    _d(code="OD_UD_QUANTUM", label="OD/UD Quantum", group="demand", kind="derived",
       depends=("ACTUAL_DEMAND", "FINAL_SCHEDULE_STATE"), expr="drawal - schedule",
       unit="MW", agg="avg", permission="analytics:dsm"),
    # ---- supply group -------------------------------------------------
    # PTC decision 2026-09-17: DC and SG are reported on `volume`, no loss variants.
    # Be aware the field is NOT on one basis: on DC rows `volume` is GROSS
    # (dc_afterloss = volume × multiplier) while on SG rows it is NET
    # (sg_beforelosses = volume ÷ multiplier). This is PTC's convention, not a defect.
    # State-level totals lead (user decision 2026-09-17): every other dataset on the
    # screen is one series per block, and these are what "Declared capacity data for
    # mapped power plants" means in PTC's document. Summed over supply_serve rows —
    # there is no separate state-level row — so the basis matches the per-plant
    # figures exactly.
    _d(code="DECLARED_CAPACITY_TOTAL", label="Declared Capacity", group="supply",
       value="$volume", unit="MW", agg="sum",
       match={"dc_sg": "DC"}, permission="analytics:supply"),
    _d(code="SG_TOTAL", label="Scheduled Generation", group="supply",
       value="$volume", unit="MW", agg="sum",
       match={"dc_sg": "SG"}, permission="analytics:supply"),
    # Per-plant drill-downs: the same rows, one series per plant. Opt-in only —
    # they need a plant selection and are what makes a long range exceed the budget.
    _d(code="DC_PLANT", label="Declared Capacity — by plant", group="supply",
       value="$volume", unit="MW", agg="avg", series="per_plant", drilldown=True,
       match={"dc_sg": "DC"}, permission="analytics:supply"),
    _d(code="SG_PLANT", label="Scheduled Generation — by plant", group="supply",
       value="$volume", unit="MW", agg="avg", series="per_plant", drilldown=True,
       match={"dc_sg": "SG"}, permission="analytics:supply"),
    # ---- scheduling ---------------------------------------------------
    _d(code="FINAL_SCHEDULE_PLANT", label="Final Schedule — by plant",
       group="scheduling", value="$volume", unit="MW", agg="avg",
       series="per_plant", drilldown=True, permission="analytics:schedule"),
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
    _d(code="TAM_TRADED", label="TAM Traded", group="trade",
       unit="MW", agg="sum", availability="absent", permission="analytics:market"),
    _d(code="TAM_BIDDED", label="TAM Bidded", group="trade",
       unit="MW", agg="sum", availability="absent", permission="analytics:market"),
]

# ---- weekly / monthly roll-ups ---------------------------------------------
# PTC's mapping is "DAF | IDF". DAF and IDF share the same (date, block) key in
# demand_serve, so they are two COLUMNS of one table, not two tables — one tick per
# roll-up, and the file carries a "Day Ahead" and an "Intra Day" column, the way the
# exchange datasets carry one column per exchange. Both `forecast` and `mape` are
# stored per block, so this is ordinary aggregation. Weekly/monthly MAPE is the MEAN
# of the per-block MAPE — the same definition daily MAPE already uses in
# libs/helpers/demand_calculations.py — not a recomputation from summed totals,
# which gives a figure nobody could reconcile with the daily one.
for _period, _grouping in (("WEEKLY", "WEEK_WISE"), ("MONTHLY", "MONTH_WISE")):
    _label = _period.title()
    _ALL += [
        _d(code=f"{_period}_FORECAST", label=f"{_label} Forecast", group="demand",
           value="$forecast", unit="MW", agg="avg", series="per_forecast_type",
           fixed_grouping=_grouping, permission="analytics:forecast"),
        _d(code=f"{_period}_MAPE", label=f"{_label} MAPE", group="demand",
           value="$mape", unit="%", agg="avg", series="per_forecast_type",
           fixed_grouping=_grouping, permission="analytics:forecast"),
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
    if dataset.series == "per_forecast_type":
        return FORECAST_TYPE_COUNT
    return 1
