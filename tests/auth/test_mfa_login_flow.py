"""
The two-step MFA login: `/auth/login` -> `mfa_required` -> `/auth/verify-login`.

None of this had any coverage, which is how a flow with a 500-producing rate limiter, a
plaintext-only secret read and a discarded single-use check stayed shipped. Everything here
drives `AuthService.login` / `AuthService.verify_login` directly with the repository and the
token store mocked, so it tests the branching rather than the database.
"""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pyotp
import pytest

from services.auth.app.services.auth_service import AuthService
from services.auth.app.services.mfa_rate_limiter import MFARateLimiter
from services.auth.app.core.dto import UserDTO
from libs.crypto import encrypt_secret

REQUEST_INFO = {"ip_address": "127.0.0.1", "user_agent": "pytest"}
USER_ID = "507f1f77bcf86cd799439011"


def _user(**overrides) -> UserDTO:
    base = dict(
        id=USER_ID,
        username="mfauser",
        email="mfa@example.com",
        is_active=True,
        is_verified=True,
        roles=["user"],
        permissions=["dashboard:read"],
        profile={"first_name": "MFA", "last_name": "User"},
        created_at=datetime.now(),
        updated_at=datetime.now(),
        last_login=datetime.now(),
        login_attempts=0,
        locked_until=None,
        password_expires_at=datetime.now() + timedelta(days=90),
        mfa_enabled=True,
        mfa_setup_complete=True,
    )
    base.update(overrides)
    return UserDTO(**base)


@pytest.fixture
def limiter(monkeypatch):
    limiter = MFARateLimiter(connect=False)
    monkeypatch.setattr(
        "services.auth.app.services.mfa_rate_limiter.mfa_rate_limiter", limiter
    )
    return limiter


@pytest.fixture
def secret():
    return pyotp.random_base32()


@pytest.fixture
def auth_service(secret):
    """AuthService with the repository, audit log and token blacklist mocked out."""
    service = AuthService()
    service.user_repo = MagicMock()
    service.user_repo.authenticate_user = AsyncMock()
    service.user_repo.get_user_by_id = AsyncMock()
    service.user_repo.get_user_with_permissions = AsyncMock()
    service.user_repo.get_user_with_permissions_by_id = AsyncMock()
    service.user_repo.update_user = AsyncMock(return_value=True)
    service.user_repo.find_one = AsyncMock(
        return_value={"mfa_secret": encrypt_secret(secret), "mfa_backup_codes": []}
    )
    service.audit_logger = MagicMock()
    return service


class TestLoginBranch:
    @pytest.mark.asyncio
    async def test_an_mfa_user_gets_a_temporary_token_and_no_session(self, auth_service):
        auth_service.user_repo.authenticate_user.return_value = _user()

        result = await auth_service.login("mfauser", "pw", REQUEST_INFO)

        assert result["mfa_required"] is True
        assert result["temporary_token"]
        assert result["expires_in"] == 300
        # Crucially, no access token: the password alone must not produce a session.
        assert "tokens" not in result

    @pytest.mark.asyncio
    async def test_a_non_mfa_user_logs_straight_in(self, auth_service):
        user = _user(mfa_enabled=False, mfa_setup_complete=False)
        auth_service.user_repo.authenticate_user.return_value = user
        auth_service.user_repo.get_user_with_permissions.return_value = user

        result = await auth_service.login("plainuser", "pw", REQUEST_INFO)

        assert "tokens" in result
        assert result["tokens"]["access_token"]
        assert "temporary_token" not in result

    @pytest.mark.asyncio
    async def test_a_half_enrolled_user_is_not_prompted(self, auth_service):
        """
        `mfa_enabled` without `mfa_setup_complete` means the secret was generated and never
        confirmed. Prompting would lock the user out of an account they never finished
        securing.
        """
        user = _user(mfa_enabled=True, mfa_setup_complete=False)
        auth_service.user_repo.authenticate_user.return_value = user
        auth_service.user_repo.get_user_with_permissions.return_value = user

        result = await auth_service.login("halfuser", "pw", REQUEST_INFO)

        assert "tokens" in result

    @pytest.mark.asyncio
    async def test_bad_credentials_return_nothing(self, auth_service):
        auth_service.user_repo.authenticate_user.return_value = None
        assert await auth_service.login("mfauser", "wrong", REQUEST_INFO) is None


class TestVerifyLoginTotp:
    @pytest.mark.asyncio
    async def test_a_correct_code_issues_tokens(self, auth_service, limiter, secret):
        user = _user()
        auth_service.user_repo.authenticate_user.return_value = user
        auth_service.user_repo.get_user_with_permissions_by_id.return_value = user
        login = await auth_service.login("mfauser", "pw", REQUEST_INFO)

        with patch(
            "services.auth.app.services.redis_blacklist.redis_blacklist_service"
            ".invalidate_temporary_token", return_value=True
        ):
            result = await auth_service.verify_login(
                login["temporary_token"], pyotp.TOTP(secret).now(), REQUEST_INFO
            )

        assert result is not None
        assert result["tokens"]["access_token"]
        assert result["tokens"]["refresh_token"]
        # The audit trail needs the id; without it every MFA login logged "unknown".
        assert result["user"]["id"] == USER_ID

    @pytest.mark.asyncio
    async def test_a_wrong_code_is_refused(self, auth_service, limiter, secret):
        user = _user()
        auth_service.user_repo.authenticate_user.return_value = user
        auth_service.user_repo.get_user_with_permissions_by_id.return_value = user
        login = await auth_service.login("mfauser", "pw", REQUEST_INFO)

        assert await auth_service.verify_login(
            login["temporary_token"], "000000", REQUEST_INFO
        ) is None

    @pytest.mark.asyncio
    async def test_no_code_at_all_is_refused(self, auth_service, limiter):
        user = _user()
        auth_service.user_repo.authenticate_user.return_value = user
        auth_service.user_repo.get_user_with_permissions_by_id.return_value = user
        login = await auth_service.login("mfauser", "pw", REQUEST_INFO)

        assert await auth_service.verify_login(
            login["temporary_token"], None, REQUEST_INFO
        ) is None

    @pytest.mark.asyncio
    async def test_a_junk_temporary_token_is_refused(self, auth_service, limiter):
        assert await auth_service.verify_login("not-a-token", "123456", REQUEST_INFO) is None

    @pytest.mark.asyncio
    async def test_a_legacy_plaintext_secret_still_logs_in(self, auth_service, limiter, secret):
        """The compatibility guarantee: nobody is locked out by the encryption deploy."""
        auth_service.user_repo.find_one.return_value = {
            "mfa_secret": secret, "mfa_backup_codes": []
        }
        user = _user()
        auth_service.user_repo.authenticate_user.return_value = user
        auth_service.user_repo.get_user_with_permissions_by_id.return_value = user
        login = await auth_service.login("mfauser", "pw", REQUEST_INFO)

        with patch(
            "services.auth.app.services.redis_blacklist.redis_blacklist_service"
            ".invalidate_temporary_token", return_value=True
        ):
            result = await auth_service.verify_login(
                login["temporary_token"], pyotp.TOTP(secret).now(), REQUEST_INFO
            )

        assert result is not None
        assert result["tokens"]["access_token"]

    @pytest.mark.asyncio
    async def test_six_consecutive_successful_logins(self, auth_service, limiter, secret):
        """
        The end-to-end regression for the success-counting bug, at the service layer.

        On the running service this sequence returned 200, 200, 200, 200, 401, 401 -- a
        *correct* code refused, with "Invalid temporary token or TOTP code", for thirty
        minutes.

        A different code each round: a used code is now burnt, so replaying one would be
        refused for an unrelated and correct reason.
        """
        user = _user()
        auth_service.user_repo.authenticate_user.return_value = user
        auth_service.user_repo.get_user_with_permissions_by_id.return_value = user
        totp = pyotp.TOTP(secret)

        with patch(
            "services.auth.app.services.redis_blacklist.redis_blacklist_service"
            ".invalidate_temporary_token", return_value=True
        ):
            for attempt in range(6):
                login = await auth_service.login("mfauser", "pw", REQUEST_INFO)
                assert login["mfa_required"] is True
                code = totp.at(2_000_000 + attempt * 30)
                with patch.object(
                    auth_service.jwt_service, "verify_temporary_token",
                    return_value={"user_id": USER_ID, "username": "mfauser"},
                ):
                    with patch(
                        "services.auth.app.services.mfa_service.mfa_service.verify_totp",
                        return_value=True,
                    ):
                        result = await auth_service.verify_login(
                            login["temporary_token"], code, REQUEST_INFO
                        )
                assert result is not None, f"login {attempt + 1} of 6 was refused"

        assert limiter.is_locked_out(USER_ID) is False

    @pytest.mark.asyncio
    async def test_the_same_code_cannot_log_in_twice(self, auth_service, limiter, secret):
        """
        Observed on the running service: one code authenticated four separate logins,
        because pyotp has no replay protection and nothing recorded used codes.
        """
        user = _user()
        auth_service.user_repo.authenticate_user.return_value = user
        auth_service.user_repo.get_user_with_permissions_by_id.return_value = user
        code = pyotp.TOTP(secret).now()

        with patch(
            "services.auth.app.services.redis_blacklist.redis_blacklist_service"
            ".invalidate_temporary_token", return_value=True
        ):
            first_login = await auth_service.login("mfauser", "pw", REQUEST_INFO)
            first = await auth_service.verify_login(
                first_login["temporary_token"], code, REQUEST_INFO
            )
            second_login = await auth_service.login("mfauser", "pw", REQUEST_INFO)
            second = await auth_service.verify_login(
                second_login["temporary_token"], code, REQUEST_INFO
            )

        assert first is not None
        assert second is None


class TestVerifyLoginBackupCode:
    @pytest.mark.asyncio
    async def test_a_valid_backup_code_logs_in_and_is_consumed(self, auth_service, limiter):
        from services.auth.app.controllers.mfa_controller import mfa_controller

        codes = mfa_controller.generate_backup_codes(count=3)
        hashed = mfa_controller.hash_backup_codes(codes)
        auth_service.user_repo.find_one.return_value = {
            "mfa_secret": encrypt_secret(pyotp.random_base32()),
            "mfa_backup_codes": list(hashed),
        }
        user = _user()
        auth_service.user_repo.authenticate_user.return_value = user
        auth_service.user_repo.get_user_with_permissions_by_id.return_value = user
        login = await auth_service.login("mfauser", "pw", REQUEST_INFO)

        with patch(
            "services.auth.app.services.redis_blacklist.redis_blacklist_service"
            ".invalidate_temporary_token", return_value=True
        ):
            result = await auth_service.verify_login(
                login["temporary_token"], None, REQUEST_INFO, backup_code=codes[0]
            )

        assert result is not None
        assert result["tokens"]["access_token"]

        remaining = auth_service.user_repo.update_user.call_args[0][1]["mfa_backup_codes"]
        assert len(remaining) == 2
        assert hashed[0] not in remaining

    @pytest.mark.asyncio
    async def test_an_invalid_backup_code_is_refused(self, auth_service, limiter):
        from services.auth.app.controllers.mfa_controller import mfa_controller

        auth_service.user_repo.find_one.return_value = {
            "mfa_secret": encrypt_secret(pyotp.random_base32()),
            "mfa_backup_codes": mfa_controller.hash_backup_codes(
                mfa_controller.generate_backup_codes(count=2)
            ),
        }
        user = _user()
        auth_service.user_repo.authenticate_user.return_value = user
        auth_service.user_repo.get_user_with_permissions_by_id.return_value = user
        login = await auth_service.login("mfauser", "pw", REQUEST_INFO)

        result = await auth_service.verify_login(
            login["temporary_token"], None, REQUEST_INFO, backup_code="ZZZZ-ZZZZ-ZZZZ"
        )

        assert result is None
        auth_service.user_repo.update_user.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_backup_code_cannot_be_used_twice(self, auth_service, limiter):
        from services.auth.app.controllers.mfa_controller import mfa_controller

        codes = mfa_controller.generate_backup_codes(count=2)
        hashed = mfa_controller.hash_backup_codes(codes)
        stored = list(hashed)
        auth_service.user_repo.find_one.return_value = {
            "mfa_secret": encrypt_secret(pyotp.random_base32()),
            "mfa_backup_codes": stored,
        }
        user = _user()
        auth_service.user_repo.authenticate_user.return_value = user
        auth_service.user_repo.get_user_with_permissions_by_id.return_value = user

        with patch(
            "services.auth.app.services.redis_blacklist.redis_blacklist_service"
            ".invalidate_temporary_token", return_value=True
        ):
            first_login = await auth_service.login("mfauser", "pw", REQUEST_INFO)
            first = await auth_service.verify_login(
                first_login["temporary_token"], None, REQUEST_INFO, backup_code=codes[0]
            )
            # find_one returns the same list object, which verify_login mutated -- the same
            # thing a re-read from Mongo would show.
            second_login = await auth_service.login("mfauser", "pw", REQUEST_INFO)
            second = await auth_service.verify_login(
                second_login["temporary_token"], None, REQUEST_INFO, backup_code=codes[0]
            )

        assert first is not None
        assert second is None


class TestTemporaryTokenSingleUse:
    @pytest.mark.asyncio
    async def test_the_temporary_token_is_burnt_after_use(self, auth_service, limiter, secret):
        user = _user()
        auth_service.user_repo.authenticate_user.return_value = user
        auth_service.user_repo.get_user_with_permissions_by_id.return_value = user
        login = await auth_service.login("mfauser", "pw", REQUEST_INFO)

        with patch(
            "services.auth.app.services.redis_blacklist.redis_blacklist_service"
            ".invalidate_temporary_token", return_value=True
        ) as invalidate:
            await auth_service.verify_login(
                login["temporary_token"], pyotp.TOTP(secret).now(), REQUEST_INFO
            )

        invalidate.assert_called_once()
        assert invalidate.call_args[0][0] == login["temporary_token"]

    @pytest.mark.asyncio
    async def test_a_failed_invalidation_is_reported_but_does_not_break_login(
        self, auth_service, limiter, secret
    ):
        """
        The trade-off, stated in code and pinned here: a degraded token store is logged as a
        security event and the login still completes. Refusing would turn a Redis outage
        into a total authentication outage for every MFA user, and the replay it would
        prevent still needs an unused TOTP code -- which is itself single-use now.
        """
        user = _user()
        auth_service.user_repo.authenticate_user.return_value = user
        auth_service.user_repo.get_user_with_permissions_by_id.return_value = user
        login = await auth_service.login("mfauser", "pw", REQUEST_INFO)

        with patch(
            "services.auth.app.services.redis_blacklist.redis_blacklist_service"
            ".invalidate_temporary_token", return_value=False
        ):
            result = await auth_service.verify_login(
                login["temporary_token"], pyotp.TOTP(secret).now(), REQUEST_INFO
            )

        assert result is not None
        events = [
            call.kwargs.get("event_type")
            for call in auth_service.audit_logger.log_security_event.call_args_list
        ]
        assert "temporary_token_invalidation_degraded" in events
