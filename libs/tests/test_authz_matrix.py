"""
The interim permission matrix seeded by tools/seed_authz_permissions.py.

The matrix is the risky half of the F6 work: too tight and 741 accounts stop being able
to do their job, too loose and the guards close nothing. These tests pin the shape of it
so a later edit has to be deliberate. They test the decision function only -- no database.
"""
import importlib.util
import pathlib

import pytest

_SEEDER = pathlib.Path(__file__).resolve().parents[2] / "tools" / "seed_authz_permissions.py"
_spec = importlib.util.spec_from_file_location("seed_authz_permissions", _SEEDER)
seeder = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(seeder)

MASTER_SCREENS = ["Power Exchange", "IEX API Detail", "Price Groups", "Plant Details",
                  "Parameter", "Bid Market", "Masters", "DAM Limit Values"]
ADMIN_SCREENS = ["Users", "Roles & Permissions", "Audit Log", "Administration", "SCADA Data"]
OPERATIONAL_SCREENS = ["DAM Block Bid", "RTM Single Bid", "DAM Scheduling", "Dashboard",
                       "Demand Forecast", "Renewable Supply"]
APPROVAL_SCREENS = ["DAM Block Bid Approval", "RTM Single Bid Approval",
                    "DAM Single Bid Approval"]

ORDINARY_ROLES = ["user", "operator", "checker"]


# --- classification --------------------------------------------------------


@pytest.mark.parametrize("menu", MASTER_SCREENS)
def test_master_screens_are_classified_as_masters(menu):
    assert seeder.classify(menu) == "masters"


@pytest.mark.parametrize("menu", ADMIN_SCREENS)
def test_admin_screens_are_classified_as_admin(menu):
    assert seeder.classify(menu) == "admin"


@pytest.mark.parametrize("menu", APPROVAL_SCREENS)
def test_approval_screens_are_classified_as_approval(menu):
    assert seeder.classify(menu) == "approval"


@pytest.mark.parametrize("menu", OPERATIONAL_SCREENS)
def test_everything_else_is_operational(menu):
    assert seeder.classify(menu) == "operational"


# --- what ordinary roles get ----------------------------------------------


@pytest.mark.parametrize("role", ORDINARY_ROLES)
@pytest.mark.parametrize("menu", MASTER_SCREENS)
def test_ordinary_roles_cannot_write_master_data(role, menu):
    # This is what closes VAPT F2's second half, F3 and F4: exchange credentials, price
    # groups and plants stop being writable by 741 of the 816 accounts.
    assert seeder.permissions_for(role, menu) == ["read"]


@pytest.mark.parametrize("role", ORDINARY_ROLES)
@pytest.mark.parametrize("menu", ADMIN_SCREENS)
def test_ordinary_roles_cannot_write_admin_screens(role, menu):
    assert seeder.permissions_for(role, menu) == ["read"]


@pytest.mark.parametrize("role", ORDINARY_ROLES)
@pytest.mark.parametrize("menu", OPERATIONAL_SCREENS)
def test_ordinary_roles_keep_operational_write(role, menu):
    # Their day job. Taking this away would be the outage, not the fix.
    assert seeder.permissions_for(role, menu) == ["read", "write"]


@pytest.mark.parametrize("role", ORDINARY_ROLES)
@pytest.mark.parametrize("menu", APPROVAL_SCREENS + OPERATIONAL_SCREENS + MASTER_SCREENS)
def test_no_ordinary_role_can_approve_anything(role, menu):
    # `approve` gates submit-to-exchange and update-status. Until PTC answers who may
    # check a maker's work, nobody below admin holds it.
    assert "approve" not in seeder.permissions_for(role, menu)


def test_a_role_literally_named_checker_still_cannot_approve():
    # There is a role named `checker` in the live database with 0 users assigned. Its
    # name is not evidence of a decision.
    assert "approve" not in seeder.permissions_for("checker", "DAM Block Bid Approval")


# --- viewer and the super roles -------------------------------------------


@pytest.mark.parametrize("menu", MASTER_SCREENS + OPERATIONAL_SCREENS + APPROVAL_SCREENS)
def test_viewer_stays_read_only_everywhere(menu):
    assert seeder.permissions_for("viewer", menu) == ["read"]


@pytest.mark.parametrize("role", ["admin", "superadmin", "Admin", "SuperAdmin"])
@pytest.mark.parametrize("menu", MASTER_SCREENS + APPROVAL_SCREENS)
def test_super_roles_get_everything_including_approve(role, menu):
    assert seeder.permissions_for(role, menu) == ["read", "write", "delete", "approve"]


# --- resource naming -------------------------------------------------------


def test_resource_names_match_the_auth_services_mapping():
    # If the seeder and MenuRoleRepository._get_resource_name_from_menu ever disagree,
    # permissions are granted under names no guard asks for and every write 403s.
    assert seeder.resource_name("Power Exchange", seeder.MENU_TO_RESOURCE) == "power_exchange"
    assert seeder.resource_name("IEX API Detail", seeder.MENU_TO_RESOURCE) == "iex_api_detail"
    assert seeder.resource_name("DAM Block Bid", seeder.MENU_TO_RESOURCE) == "dam_block_bid"
    assert seeder.resource_name("Users", seeder.MENU_TO_RESOURCE) == "users"


def test_unmapped_menu_names_fall_back_to_slugging():
    assert seeder.resource_name("OCF Limit Values", seeder.MENU_TO_RESOURCE) == "ocf_limit_values"
    assert seeder.resource_name("Price Groups", seeder.MENU_TO_RESOURCE) == "price_groups"


def test_guard_resources_in_main_are_all_grantable():
    """Every resource a guard asks for must be one the seeder can actually grant."""
    import ast

    main = pathlib.Path(__file__).resolve().parents[2] / "services/processing/app/main.py"
    tree = ast.parse(main.read_text())

    def literals(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.value
        elif isinstance(node, ast.Tuple):
            for element in node.elts:
                yield from literals(element)

    asked = {
        name
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "guard"
        and node.args
        for name in literals(node.args[0])
    }

    assert asked, "no guard(...) resources found in main.py"

    # The names the seeder can produce from the 54 menus in the live catalogue.
    grantable = {
        seeder.resource_name(name, seeder.MENU_TO_RESOURCE)
        for name in [
            "Dashboard", "Overview", "Management Snapshot", "DSM Overview", "Market Integrator",
            "Forecast", "Forecast Analysis", "Demand Forecast", "Supply", "Renewable Supply",
            "URS", "Supply Availability", "DAM", "DAM Scheduling", "DAM Block Bid",
            "DAM Single Bid", "DAM Bid Status", "DAM Trade Status", "DAM Scheduling Upload",
            "RTM", "RTM Scheduling", "RTM Block Bid", "RTM Single Bid", "RTM Bid Status",
            "RTM Trade Status", "Masters", "Plant Details", "Price Groups", "IEX API Detail",
            "Power Exchange", "Parameter", "Bid Market", "DAM Limit Values", "RTM Limit Values",
            "OCF Limit Values", "Reports", "Standard Reports", "Audit Log", "Administration",
            "Users", "Roles & Permissions", "Upload CSV", "DC SG Template", "SCADA Data",
            "Weather",
        ]
    }

    ungrantable = asked - grantable
    assert not ungrantable, (
        f"guards ask for resources no menu can grant: {sorted(ungrantable)} -- "
        "those routes would 403 for every non-admin, permanently"
    )
