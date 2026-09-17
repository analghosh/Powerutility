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
    "WEEKLY_FORECAST", "MONTHLY_FORECAST", "WEEKLY_MAPE", "MONTHLY_MAPE",
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
