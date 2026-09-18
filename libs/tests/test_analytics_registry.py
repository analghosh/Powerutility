"""The dataset registry is the contract every other analytics module reads.

Dataset availability is data, not hardcoded UI: six datasets in PTC's list have no
storage anywhere (Weekly/Monthly Forecast, Weekly/Monthly MAPE, TAM Traded/Bidded),
and the portfolio Traded/Bidded datasets exist only for DAM and RTM because
bid_trade_serve carries no GDAM or HPDAM rows.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services.processing.app.v2.analytics import registry  # noqa: E402


def test_actual_demand_maps_to_the_demand_group():
    d = registry.get("ACTUAL_DEMAND")
    assert d.group == "demand"
    assert d.value == "$actual"
    assert d.unit == "MW"
    assert d.agg == "avg"


def test_udm_reads_comp_udm_not_udm():
    """demand_serve.udm is zero in every document; the real values are in comp_udm."""
    assert registry.get("DEMAND_UDM").value == "$comp_udm"


def test_od_ud_quantum_is_derived_from_same_collection_fields():
    d = registry.get("OD_UD_QUANTUM")
    assert d.kind == "derived"
    assert set(d.depends) == {"DRAWAL", "FINAL_SCHEDULE_STATE"}


def test_od_ud_amount_is_stored_not_computed():
    """net_dsm_amount is populated (663 non-zero), so the CERC band maths is not re-run."""
    d = registry.get("OD_UD_AMOUNT")
    assert d.kind == "direct"
    assert d.value == "$net_dsm_amount"
    assert d.agg == "sum"


@pytest.mark.parametrize("code", [
    "TAM_TRADED", "TAM_TRADED", "TAM_TRADED", "TAM_TRADED",
    "TAM_TRADED", "TAM_BIDDED",
])
def test_datasets_with_no_storage_are_absent(code):
    assert registry.get(code).availability == "absent"


@pytest.mark.parametrize("code", ["PF_TRADED_GDAM", "PF_BIDDED_GDAM",
                                  "PF_TRADED_HPDAM", "PF_BIDDED_HPDAM"])
def test_portfolio_datasets_without_rows_are_empty_not_absent(code):
    """The collection and code path exist; only the data is missing."""
    assert registry.get(code).availability == "empty"


@pytest.mark.parametrize("code", ["PF_TRADED_DAM", "PF_BIDDED_DAM",
                                  "PF_TRADED_RTM", "PF_BIDDED_RTM"])
def test_portfolio_datasets_with_rows_are_live(code):
    assert registry.get(code).availability == "live"


def test_absent_datasets_are_not_selectable():
    codes = {d.code for d in registry.selectable()}
    assert "TAM_TRADED" not in codes
    assert "PF_TRADED_GDAM" in codes  # empty is still selectable


def test_plant_datasets_declare_per_plant_series():
    for code in ("DC_PLANT", "SG_PLANT", "FINAL_SCHEDULE_PLANT"):
        assert registry.get(code).series == "per_plant"


def test_exchange_datasets_declare_per_exchange_series():
    for code in ("ACP_DAM", "ACV_RTM", "MKT_BUY_BID_GDAM"):
        assert registry.get(code).series == "per_exchange"


def test_aggregate_totals_are_declared_per_dataset():
    """Applying avg to everything produces wrong totals under coarse grouping."""
    assert registry.get("TOTAL_ENERGY_MU").agg == "sum"
    assert registry.get("PEAK_DEMAND").agg == "max"
    assert registry.get("MIN_DEMAND").agg == "min"
    assert registry.get("ACTUAL_DEMAND").agg == "avg"


def test_by_group_collapses_many_datasets_into_few_queries():
    picked = ["ACTUAL_DEMAND", "DEMAND_UDM", "FREQUENCY", "MAPE_DAF", "ACP_DAM", "ACV_DAM"]
    groups = registry.by_group(picked)
    assert set(groups) == {"demand", "exchange"}
    assert len(groups["demand"]) == 4


def test_series_cardinality_scales_plant_datasets():
    ctx = {"plant_count": 34, "exchange_count": 3}
    assert registry.series_cardinality(registry.get("ACTUAL_DEMAND"), ctx) == 1
    assert registry.series_cardinality(registry.get("DC_PLANT"), ctx) == 34
    assert registry.series_cardinality(registry.get("ACP_DAM"), ctx) == 3


def test_every_dataset_declares_a_permission():
    for d in registry.DATASETS.values():
        assert d.permission, f"{d.code} has no permission"


def test_unknown_code_raises():
    with pytest.raises(registry.UnknownDatasetError):
        registry.get("NOT_A_DATASET")


def test_gdam_acv_projects_the_total_subfield():
    """GDAM acv is a sub-document; every other market's acv is a scalar."""
    assert registry.get("ACV_GDAM").value == "$acv.total"
    assert registry.get("ACV_DAM").value == "$acv"


# ------------------------------------------------- PTC answers, 2026-09-17

def test_actual_demand_is_the_actual_field_every_other_screen_shows():
    """User decision 2026-09-18: Actual Demand = `actual` (drawal + PPCLGPP's SG, the
    number the Demand Forecast / Demand-Supply / Management charts all label
    "Actual Demand"). Not `comp_act_demand` — its forecast fallback would put a
    forecast into a historical export."""
    assert registry.get("ACTUAL_DEMAND").value == "$actual"


def test_drawal_is_its_own_dataset_beside_actual_demand():
    """The raw feed value at the drawal point — what DSM settles on. Never equal to
    actual (actual = drawal + PPCLGPP SG), so it is not a duplicate."""
    d = registry.get("DRAWAL")
    assert d.value == "$drawal" and d.group == "demand" and d.agg == "avg"
    assert d.label == "Drawal"


def test_od_ud_quantum_depends_on_drawal_not_actual():
    """Deviation is drawal − schedule. Built on `actual` it would carry PPCLGPP's
    own generation into the over-drawal figure."""
    assert registry.get("OD_UD_QUANTUM").depends == ("DRAWAL", "FINAL_SCHEDULE_STATE")


@pytest.mark.parametrize("period,grouping", [("WEEKLY", "WEEK_WISE"), ("MONTHLY", "MONTH_WISE")])
def test_rollup_forecast_is_one_tick_reported_per_forecast_type(period, grouping):
    """DAF and IDF share the same (date, block) key, so they are two COLUMNS of one
    table — one dataset, one tick, "Day Ahead" + "Intra Day" columns in the file."""
    d = registry.get(f"{period}_FORECAST")
    assert d.availability == "live"
    assert d.kind == "direct"
    assert d.value == "$forecast"
    assert d.match is None, "no type discriminator — both types come back as series"
    assert d.series == "per_forecast_type"
    assert d.agg == "avg"
    assert d.fixed_grouping == grouping


@pytest.mark.parametrize("period,grouping", [("WEEKLY", "WEEK_WISE"), ("MONTHLY", "MONTH_WISE")])
def test_rollup_mape_is_the_mean_of_per_block_mape_per_forecast_type(period, grouping):
    """Same definition as daily MAPE, over more blocks — NOT recomputed from summed
    totals, which gives a different figure (7.24% vs 7.78% on a real week)."""
    d = registry.get(f"{period}_MAPE")
    assert d.availability == "live"
    assert d.value == "$mape"
    assert d.series == "per_forecast_type"
    assert d.agg == "avg"
    assert d.fixed_grouping == grouping


def test_a_forecast_type_rollup_counts_two_series_in_the_budget():
    assert registry.series_cardinality(registry.get("WEEKLY_FORECAST"), {}) == 2


def test_the_split_daf_idf_rollup_codes_are_gone():
    """They were replaced by one dataset per roll-up reporting both types."""
    for period in ("WEEKLY", "MONTHLY"):
        for kind in ("FORECAST", "MAPE"):
            for t in ("DAF", "IDF"):
                with pytest.raises(registry.UnknownDatasetError):
                    registry.get(f"{period}_{kind}_{t}")



def test_only_tam_remains_absent():
    absent = sorted(d.code for d in registry.DATASETS.values() if d.availability == "absent")
    assert absent == ["TAM_BIDDED", "TAM_TRADED"]


def test_ordinary_datasets_have_no_fixed_grouping():
    for code in ("ACTUAL_DEMAND", "FORECAST_DAF", "MAPE_IDF", "ACP_DAM", "DC_PLANT"):
        assert registry.get(code).fixed_grouping is None


# ---------------------------------------- state-level default, plant drill-downs

@pytest.mark.parametrize("total,plant", [
    ("DECLARED_CAPACITY_TOTAL", "DC_PLANT"),
    ("SG_TOTAL", "SG_PLANT"),
    ("FINAL_SCHEDULE_STATE", "FINAL_SCHEDULE_PLANT"),
])
def test_state_level_total_is_the_default_and_per_plant_is_a_drilldown(total, plant):
    """User decision 2026-09-17: every other dataset is one series per block, so the
    state-level figure leads and the per-plant breakdown is opt-in."""
    t, p_ = registry.get(total), registry.get(plant)
    assert t.series == "scalar" and t.drilldown is False
    assert p_.series == "per_plant" and p_.drilldown is True
    assert "by plant" in p_.label
    assert "by plant" not in t.label


def test_totals_and_drilldowns_read_the_same_rows():
    """The state figure is a SUM over the same supply_serve rows, so the basis is
    identical to the per-plant one — there is no separate state-level row."""
    for total, plant in (("DECLARED_CAPACITY_TOTAL", "DC_PLANT"), ("SG_TOTAL", "SG_PLANT")):
        t, p_ = registry.get(total), registry.get(plant)
        assert t.value == p_.value == "$volume"
        assert t.match == p_.match
        assert t.agg == "sum" and p_.agg == "avg"


def test_totals_are_listed_before_their_drilldowns():
    codes = list(registry.DATASETS)
    assert codes.index("DECLARED_CAPACITY_TOTAL") < codes.index("DC_PLANT")
    assert codes.index("SG_TOTAL") < codes.index("SG_PLANT")


def test_only_per_plant_datasets_are_drilldowns():
    for d in registry.DATASETS.values():
        if d.drilldown:
            assert d.series == "per_plant", d.code


# ------------------------------------------- Final Schedule = SG (PTC sheet)

def test_final_schedule_is_the_summed_sg_from_supply_serve():
    """PTC's sheet places Final Schedule under supply_serve. It is EPMS's own
    versioned, published scheduling output; the demand_serve `schedule` column is an
    uploaded CSV figure that excludes one plant (PPCLGPP) and the OA_PX line."""
    d = registry.get("SG_TOTAL")
    assert d.label == "Final Schedule"
    assert d.group == "supply"
    assert d.value == "$volume" and d.match == {"dc_sg": "SG"} and d.agg == "sum"


def test_the_demand_serve_schedule_is_labelled_for_settlement_not_as_final():
    d = registry.get("FINAL_SCHEDULE_STATE")
    assert d.label == "DSM Schedule"
    assert d.group == "demand" and d.value == "$schedule"


def test_od_ud_quantum_still_subtracts_the_dsm_schedule_not_sg():
    """Settlement compares drawal with the schedule at the drawal point — the sheet's
    own `drawal − schedule`. Swapping in ΣSG would put PPCLGPP's 15 MW into the money."""
    assert registry.get("OD_UD_QUANTUM").depends == ("DRAWAL", "FINAL_SCHEDULE_STATE")


def test_no_two_datasets_share_the_label_final_schedule():
    labels = [d.label for d in registry.DATASETS.values()]
    assert labels.count("Final Schedule") == 1
    assert labels.count("Final Schedule — by plant") == 1


# ------------------------------------------- IDF-only settlement fields

@pytest.mark.parametrize("code", ["FINAL_SCHEDULE_STATE", "OD_UD_AMOUNT", "DSM_RATE"])
def test_settlement_fields_are_pinned_to_the_idf_row(code):
    """demand_serve has a DAF and an IDF row per block. schedule / net_dsm_amount /
    dsm_rate are written only on IDF; DAF carries 0. Unpinned, the two rows averaged
    and every value was HALVED — DSM Schedule read 267.72 for a stored 535.44."""
    assert registry.get(code).match == {"type": "IDF"}


def test_shared_fields_stay_unpinned_because_both_rows_carry_the_same_value():
    """drawal / frequency / comp_udm are identical on the DAF and IDF rows, so the
    average is exact — and pinning would lose days where only one type exists."""
    for code in ("ACTUAL_DEMAND", "FREQUENCY", "DEMAND_UDM"):
        assert registry.get(code).match is None
