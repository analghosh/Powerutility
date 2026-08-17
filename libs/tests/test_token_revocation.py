"""
Session revocation (VAPT F9): a password reset must end the sessions it was meant to end.

Before this, resetting a password -- the thing an administrator does when they believe an
account is compromised -- invalidated nothing. EPMS issues a 30-minute access token and a
seven-day refresh token, and a reset touched neither. Whoever held the refresh token could
keep minting fresh access tokens for a week after the password changed.

Every token now carries the `token_version` it was minted with. Validation rejects any
token behind the account's current value, and because every service validates every
request against the auth service, revocation lands on the next request rather than at
token expiry.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from libs.database.mongo_client_factory import MongoClientFactory

with patch.object(MongoClientFactory, "get_client", return_value=MagicMock()):
    from services.auth.app.services.auth_service import AuthService
    from services.auth.app.core.dto import AuthUserDTO, UserDTO
    from services.auth.app.repository.user_repository import UserRepository

current = AuthService._token_version_is_current


def _user(version=0):
    return SimpleNamespace(username="trader1", token_version=version, is_active=True)


# --- the comparison -------------------------------------------------------


def test_a_token_on_the_current_version_is_accepted():
    assert current({"token_version": 3}, _user(3)) is True


def test_a_token_behind_the_account_is_rejected():
    assert current({"token_version": 2}, _user(3)) is False


def test_a_token_ahead_of_the_account_is_accepted():
    # Can only happen mid-write; refusing here would log out the person who just changed
    # their own password, which is the opposite of the intent.
    assert current({"token_version": 4}, _user(3)) is True


def test_tokens_minted_before_this_field_existed_keep_working():
    # Deploying must not log anybody out. No claim means version 0, which matches every
    # account that has never had one bumped.
    assert current({}, _user(0)) is True


def test_an_old_token_is_rejected_once_the_account_is_bumped():
    assert current({}, _user(1)) is False


def test_a_missing_account_version_is_treated_as_zero():
    assert current({"token_version": 0}, SimpleNamespace(username="u")) is True


@pytest.mark.parametrize("junk", ["", "abc", None, [], {}])
def test_a_malformed_claim_fails_closed(junk):
    assert current({"token_version": junk}, _user(2)) is False


def test_a_string_digit_is_still_compared_numerically():
    assert current({"token_version": "3"}, _user(3)) is True
    assert current({"token_version": "2"}, _user(3)) is False


# --- what gets revoked ----------------------------------------------------


def test_the_revoking_field_set_is_the_one_we_intend():
    # Changing a profile or an email must NOT log the person out; changing their rights or
    # switching them off must.
    assert UserRepository._SESSION_REVOKING_FIELDS == {"roles", "permissions", "is_active"}


@pytest.mark.parametrize("field", ["roles", "permissions", "is_active"])
def test_rights_changes_are_revoking(field):
    assert field in UserRepository._SESSION_REVOKING_FIELDS


@pytest.mark.parametrize("field", ["profile", "email", "is_verified", "last_login"])
def test_harmless_edits_are_not_revoking(field):
    assert field not in UserRepository._SESSION_REVOKING_FIELDS


# --- the claim reaches the token ------------------------------------------


def _dto(version=7):
    return UserDTO(
        id="u1", username="trader1", email=None, is_active=True, is_verified=False,
        roles=["operator"], permissions=["dam_block_bid:read"], profile={},
        created_at=None, updated_at=None, last_login=None, login_attempts=0,
        locked_until=None, password_expires_at=None, mfa_enabled=False,
        mfa_setup_complete=False, token_version=version, accessible_menus=[],
    )


def test_the_version_is_carried_into_the_jwt_payload():
    # Tokens are built from to_dict(); a missing key here means the claim never ships and
    # the check silently passes for everyone.
    assert _dto(7).to_dict()["token_version"] == 7


def test_the_login_dto_carries_it_too():
    # Login builds its token from AuthUserDTO, not UserDTO.
    assert AuthUserDTO.from_user_dto(_dto(7)).to_dict()["token_version"] == 7


def test_an_account_without_the_field_reads_as_zero():
    doc = {"_id": "1", "username": "u", "created_at": None}
    assert UserDTO.from_db_document(doc).token_version == 0


def test_a_null_in_the_database_reads_as_zero():
    doc = {"_id": "1", "username": "u", "created_at": None, "token_version": None}
    assert UserDTO.from_db_document(doc).token_version == 0
