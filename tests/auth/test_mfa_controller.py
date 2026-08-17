"""
Unit tests for MFA Controller

Assertions that changed direction, and why:

* `test_enable_mfa_success` used to assert `mfa_enabled: True` on enrolment. The controller
  correctly writes `False` -- enrolment is not complete until the user proves they can
  produce a code -- so the test was pinning a contract the code never honoured.
* `test_generate_backup_codes` asserted 8-character codes. They are formatted
  `XXXX-XXXX-XXXX`, so 14 characters. The old generator produced *variable* length, which
  is the defect the new one fixes.
* The controller's collaborators are awaited, so `Mock()` cannot stand in for them;
  `AsyncMock` is used where the real object returns a coroutine.
"""

import re
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pyotp
import pytest
from fastapi import HTTPException

# Import after global mocks in conftest.py
from services.auth.app.controllers.mfa_controller import MFAController
from services.auth.app.schemas.mfa import (
    DisableMFARequest, EnableMFARequest, EnableMFAResponse, MFARecoveryRequest,
    VerifyMFARequest,
)
from services.auth.app.core.dto import UserDTO
from libs.crypto import decrypt_secret, encrypt_secret, is_encrypted


def _user_dto(**overrides) -> UserDTO:
    base = dict(
        id="507f1f77bcf86cd799439011",
        username="testuser",
        email="test@example.com",
        is_active=True,
        is_verified=True,
        roles=["user"],
        permissions=["read"],
        profile={"first_name": "Test", "last_name": "User"},
        created_at=datetime.now(),
        updated_at=datetime.now(),
        last_login=datetime.now(),
        login_attempts=0,
        locked_until=None,
        password_expires_at=datetime.now() + timedelta(days=90),
        mfa_enabled=False,
        mfa_setup_complete=False,
    )
    base.update(overrides)
    return UserDTO(**base)


class TestMFAController:
    """Test cases for MFA Controller"""

    @pytest.fixture
    def mfa_controller(self):
        """MFAController with async-aware mocks for its collaborators."""
        controller = MFAController()
        controller.user_model = MagicMock()
        controller.user_model.get_user_by_id = AsyncMock()
        controller.user_model.update_user = AsyncMock(return_value=True)
        controller.user_model.collection = MagicMock()
        controller.user_model.collection.find_one = AsyncMock(return_value={})
        controller.mfa_service = Mock()
        return controller

    @pytest.fixture
    def mock_user_dto(self):
        return _user_dto()

    @pytest.fixture
    def mock_user_with_mfa(self):
        return _user_dto(mfa_enabled=True, mfa_setup_complete=True)

    @pytest.fixture
    def enable_mfa_request(self):
        return EnableMFARequest()

    # ---------------------------------------------------------------- enable

    @pytest.mark.asyncio
    async def test_enable_mfa_success(self, mfa_controller, mock_user_dto, enable_mfa_request):
        """Enrolment stores the secret and leaves MFA *not* enabled until it is verified."""
        user_id = "507f1f77bcf86cd799439011"
        secret = "JBSWY3DPEHPK3PXP"
        qr_code = "data:image/png;base64,iVBORw0KGgo..."
        backup_codes = ["AAAA-BBBB-CCCC", "DDDD-EEEE-FFFF"]

        mfa_controller.user_model.get_user_by_id.return_value = mock_user_dto
        mfa_controller.mfa_service.generate_secret.return_value = secret
        mfa_controller.mfa_service.generate_qr_code.return_value = qr_code
        mfa_controller.mfa_service.get_remaining_time.return_value = 25
        mfa_controller.generate_backup_codes = Mock(return_value=backup_codes)

        result = await mfa_controller.enable_mfa(user_id, enable_mfa_request)

        assert isinstance(result, EnableMFAResponse)
        assert result.secret == secret
        assert result.qr_code == qr_code
        assert result.backup_codes == backup_codes
        assert result.remaining_time == 25

        written = mfa_controller.user_model.update_user.call_args[0][1]
        assert written["mfa_enabled"] is False
        assert written["mfa_setup_complete"] is False
        # Backup codes are stored hashed, never as issued.
        assert written["mfa_backup_codes"] != backup_codes
        assert all(":" in stored for stored in written["mfa_backup_codes"])

        mfa_controller.mfa_service.generate_qr_code.assert_called_once_with(
            secret, mock_user_dto.username
        )

    @pytest.mark.asyncio
    async def test_enable_mfa_stores_the_secret_encrypted(
        self, mfa_controller, mock_user_dto, enable_mfa_request
    ):
        """
        The whole point of item 4: a database read must not yield a working second factor.
        """
        secret = pyotp.random_base32()
        mfa_controller.user_model.get_user_by_id.return_value = mock_user_dto
        mfa_controller.mfa_service.generate_secret.return_value = secret
        mfa_controller.mfa_service.generate_qr_code.return_value = "qr"
        mfa_controller.mfa_service.get_remaining_time.return_value = 30

        await mfa_controller.enable_mfa("507f1f77bcf86cd799439011", enable_mfa_request)

        stored = mfa_controller.user_model.update_user.call_args[0][1]["mfa_secret"]
        assert stored != secret
        assert is_encrypted(stored)
        assert decrypt_secret(stored) == secret

    @pytest.mark.asyncio
    async def test_enable_mfa_user_not_found(self, mfa_controller, enable_mfa_request):
        mfa_controller.user_model.get_user_by_id.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            await mfa_controller.enable_mfa("nonexistent_user_id", enable_mfa_request)

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "User not found"
        mfa_controller.mfa_service.generate_secret.assert_not_called()

    @pytest.mark.asyncio
    async def test_enable_mfa_already_enabled(
        self, mfa_controller, mock_user_with_mfa, enable_mfa_request
    ):
        mfa_controller.user_model.get_user_by_id.return_value = mock_user_with_mfa

        with pytest.raises(HTTPException) as exc_info:
            await mfa_controller.enable_mfa("507f1f77bcf86cd799439011", enable_mfa_request)

        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == "MFA is already enabled"
        mfa_controller.user_model.update_user.assert_not_called()

    @pytest.mark.asyncio
    async def test_enable_mfa_no_password_verification(
        self, mfa_controller, mock_user_dto, enable_mfa_request
    ):
        """Enrolment authenticates by access token; no password is asked for or checked."""
        mfa_controller.user_model.get_user_by_id.return_value = mock_user_dto
        mfa_controller.mfa_service.generate_secret.return_value = "SECRET123"
        mfa_controller.mfa_service.generate_qr_code.return_value = "QR_CODE"
        mfa_controller.mfa_service.get_remaining_time.return_value = 30

        await mfa_controller.enable_mfa("507f1f77bcf86cd799439011", enable_mfa_request)

        assert not mfa_controller.user_model.authenticate_user.called

    # ---------------------------------------------------------------- backup codes

    def test_generate_backup_codes(self, mfa_controller):
        codes = mfa_controller.generate_backup_codes(count=5)

        assert len(codes) == 5
        for code in codes:
            assert isinstance(code, str)
            assert re.fullmatch(r"[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}", code), code
            assert code == code.upper()

    def test_generate_backup_codes_have_a_fixed_length(self, mfa_controller):
        """
        The old generator stripped `_` and `-` out of a urlsafe token and then truncated,
        so any token containing those characters produced a short code with a stub final
        group -- less entropy than intended, varying per code, with nothing to show for it.
        """
        codes = mfa_controller.generate_backup_codes(count=200)
        assert {len(code) for code in codes} == {14}
        assert all(len(group) == 4 for code in codes for group in code.split("-"))

    def test_generate_backup_codes_are_unique(self, mfa_controller):
        codes = mfa_controller.generate_backup_codes(count=200)
        assert len(set(codes)) == len(codes)

    def test_generate_backup_codes_default_count(self, mfa_controller):
        assert len(mfa_controller.generate_backup_codes()) == 8

    def test_backup_code_hashing_round_trip(self, mfa_controller):
        codes = mfa_controller.generate_backup_codes(count=3)
        hashed = mfa_controller.hash_backup_codes(codes)

        assert all(mfa_controller.verify_backup_code(c, h) for c, h in zip(codes, hashed))
        assert not mfa_controller.verify_backup_code("AAAA-BBBB-CCCC", hashed[0])
        assert not mfa_controller.verify_backup_code(codes[0], "not-a-hash")

    # ---------------------------------------------------------------- verify

    @pytest.mark.asyncio
    async def test_verify_mfa_checks_the_code_even_when_already_enabled(
        self, mfa_controller, mock_user_with_mfa
    ):
        """
        The defect: the controller short-circuited on `user.mfa_enabled` and returned
        `verified: true` **without looking at the code at all**, so any authenticated
        enrolled user got a pass for "000000".
        """
        mfa_controller.user_model.get_user_by_id.return_value = mock_user_with_mfa
        mfa_controller.user_model.collection.find_one.return_value = {
            "mfa_secret": encrypt_secret(pyotp.random_base32())
        }
        mfa_controller.mfa_service.verify_totp_with_rate_limit.return_value = {
            "verified": False, "rate_limited": False, "message": "bad code",
        }

        with pytest.raises(HTTPException) as exc_info:
            await mfa_controller.verify_mfa(
                "507f1f77bcf86cd799439011", VerifyMFARequest(code="000000")
            )

        assert exc_info.value.status_code == 403
        mfa_controller.mfa_service.verify_totp_with_rate_limit.assert_called_once()

    @pytest.mark.asyncio
    async def test_verify_mfa_completes_enrolment(self, mfa_controller, mock_user_dto):
        mfa_controller.user_model.get_user_by_id.return_value = mock_user_dto
        mfa_controller.user_model.collection.find_one.return_value = {
            "mfa_secret": encrypt_secret(pyotp.random_base32())
        }
        mfa_controller.mfa_service.verify_totp_with_rate_limit.return_value = {
            "verified": True, "rate_limited": False, "message": "ok",
        }

        result = await mfa_controller.verify_mfa(
            "507f1f77bcf86cd799439011", VerifyMFARequest(code="123456")
        )

        assert result.verified is True
        written = mfa_controller.user_model.update_user.call_args[0][1]
        assert written == {"mfa_setup_complete": True, "mfa_enabled": True}

    @pytest.mark.asyncio
    async def test_verify_mfa_decrypts_the_stored_secret(self, mfa_controller, mock_user_dto):
        secret = pyotp.random_base32()
        mfa_controller.user_model.get_user_by_id.return_value = mock_user_dto
        mfa_controller.user_model.collection.find_one.return_value = {
            "mfa_secret": encrypt_secret(secret)
        }
        mfa_controller.mfa_service.verify_totp_with_rate_limit.return_value = {
            "verified": True, "rate_limited": False, "message": "ok",
        }

        await mfa_controller.verify_mfa(
            "507f1f77bcf86cd799439011", VerifyMFARequest(code="123456")
        )

        passed_secret = mfa_controller.mfa_service.verify_totp_with_rate_limit.call_args[0][1]
        assert passed_secret == secret

    @pytest.mark.asyncio
    async def test_verify_mfa_accepts_a_legacy_plaintext_secret(
        self, mfa_controller, mock_user_dto
    ):
        """
        Backward compatibility, which is what keeps the deploy from locking people out:
        accounts enrolled before encryption hold plaintext base32 and must keep working.
        """
        secret = pyotp.random_base32()
        mfa_controller.user_model.get_user_by_id.return_value = mock_user_dto
        mfa_controller.user_model.collection.find_one.return_value = {"mfa_secret": secret}
        mfa_controller.mfa_service.verify_totp_with_rate_limit.return_value = {
            "verified": True, "rate_limited": False, "message": "ok",
        }

        await mfa_controller.verify_mfa(
            "507f1f77bcf86cd799439011", VerifyMFARequest(code="123456")
        )

        passed_secret = mfa_controller.mfa_service.verify_totp_with_rate_limit.call_args[0][1]
        assert passed_secret == secret

    @pytest.mark.asyncio
    async def test_verify_mfa_rate_limited_returns_429(self, mfa_controller, mock_user_dto):
        mfa_controller.user_model.get_user_by_id.return_value = mock_user_dto
        mfa_controller.user_model.collection.find_one.return_value = {
            "mfa_secret": encrypt_secret(pyotp.random_base32())
        }
        mfa_controller.mfa_service.verify_totp_with_rate_limit.return_value = {
            "verified": False, "rate_limited": True, "message": "Too many failed attempts.",
        }

        with pytest.raises(HTTPException) as exc_info:
            await mfa_controller.verify_mfa(
                "507f1f77bcf86cd799439011", VerifyMFARequest(code="123456")
            )

        assert exc_info.value.status_code == 429

    @pytest.mark.asyncio
    async def test_verify_mfa_without_a_secret(self, mfa_controller, mock_user_dto):
        mfa_controller.user_model.get_user_by_id.return_value = mock_user_dto
        mfa_controller.user_model.collection.find_one.return_value = {}

        with pytest.raises(HTTPException) as exc_info:
            await mfa_controller.verify_mfa(
                "507f1f77bcf86cd799439011", VerifyMFARequest(code="123456")
            )

        assert exc_info.value.status_code == 400

    # ---------------------------------------------------------------- disable

    @pytest.mark.asyncio
    async def test_disable_mfa_clears_every_field(self, mfa_controller, mock_user_with_mfa):
        mfa_controller.user_model.get_user_by_id.return_value = mock_user_with_mfa
        mfa_controller.user_model.collection.find_one.return_value = {
            "mfa_secret": encrypt_secret(pyotp.random_base32())
        }
        mfa_controller.mfa_service.verify_totp_with_rate_limit.return_value = {
            "verified": True, "rate_limited": False, "message": "ok",
        }

        result = await mfa_controller.disable_mfa(
            "507f1f77bcf86cd799439011", DisableMFARequest(code="123456")
        )

        assert result.disabled is True
        assert mfa_controller.user_model.update_user.call_args[0][1] == {
            "mfa_enabled": False,
            "mfa_secret": None,
            "mfa_setup_complete": False,
            "mfa_backup_codes": [],
        }

    @pytest.mark.asyncio
    async def test_disable_mfa_requires_a_valid_code(self, mfa_controller, mock_user_with_mfa):
        mfa_controller.user_model.get_user_by_id.return_value = mock_user_with_mfa
        mfa_controller.user_model.collection.find_one.return_value = {
            "mfa_secret": encrypt_secret(pyotp.random_base32())
        }
        mfa_controller.mfa_service.verify_totp_with_rate_limit.return_value = {
            "verified": False, "rate_limited": False, "message": "bad code",
        }

        with pytest.raises(HTTPException) as exc_info:
            await mfa_controller.disable_mfa(
                "507f1f77bcf86cd799439011", DisableMFARequest(code="000000")
            )

        assert exc_info.value.status_code == 401
        mfa_controller.user_model.update_user.assert_not_called()

    @pytest.mark.asyncio
    async def test_disable_mfa_when_not_enabled(self, mfa_controller, mock_user_dto):
        mfa_controller.user_model.get_user_by_id.return_value = mock_user_dto

        with pytest.raises(HTTPException) as exc_info:
            await mfa_controller.disable_mfa(
                "507f1f77bcf86cd799439011", DisableMFARequest(code="123456")
            )

        assert exc_info.value.status_code == 400

    # ---------------------------------------------------------------- status

    @pytest.mark.asyncio
    async def test_get_mfa_status(self, mfa_controller, mock_user_with_mfa):
        mfa_controller.user_model.get_user_by_id.return_value = mock_user_with_mfa
        mfa_controller.user_model.collection.find_one.return_value = {
            "mfa_backup_codes": ["a:b", "c:d", "e:f"]
        }

        status = await mfa_controller.get_mfa_status("507f1f77bcf86cd799439011")

        assert status.enabled is True
        assert status.setup_complete is True
        assert status.backup_codes_remaining == 3

    @pytest.mark.asyncio
    async def test_get_mfa_status_when_not_enrolled(self, mfa_controller, mock_user_dto):
        mfa_controller.user_model.get_user_by_id.return_value = mock_user_dto
        mfa_controller.user_model.collection.find_one.return_value = {}

        status = await mfa_controller.get_mfa_status("507f1f77bcf86cd799439011")

        assert status.enabled is False
        assert status.setup_complete is False
        assert status.backup_codes_remaining == 0

    @pytest.mark.asyncio
    async def test_get_mfa_status_user_not_found(self, mfa_controller):
        mfa_controller.user_model.get_user_by_id.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            await mfa_controller.get_mfa_status("507f1f77bcf86cd799439011")

        assert exc_info.value.status_code == 404

    # ---------------------------------------------------------------- recover

    @pytest.mark.asyncio
    async def test_recover_replaces_the_code_set_rather_than_growing_it(
        self, mfa_controller, mock_user_with_mfa
    ):
        """
        The old implementation appended eight new codes to the existing list, so every
        recovery grew it and codes the user had written off stayed valid indefinitely.
        """
        codes = mfa_controller.generate_backup_codes(count=8)
        hashed = mfa_controller.hash_backup_codes(codes)

        mfa_controller.user_model.get_user_by_id.return_value = mock_user_with_mfa
        mfa_controller.user_model.collection.find_one.return_value = {
            "mfa_backup_codes": list(hashed)
        }

        result = await mfa_controller.recover_mfa(
            "507f1f77bcf86cd799439011", MFARecoveryRequest(backup_code=codes[0])
        )

        assert result.recovered is True
        assert len(result.new_backup_codes) == 8
        written = mfa_controller.user_model.update_user.call_args[0][1]["mfa_backup_codes"]
        assert len(written) == 8
        # None of the old hashes survive, including the seven that were never used.
        assert not set(written) & set(hashed)
        # And the codes handed back are the ones now stored.
        assert all(
            any(mfa_controller.verify_backup_code(code, stored) for stored in written)
            for code in result.new_backup_codes
        )

    @pytest.mark.asyncio
    async def test_recover_rejects_an_unknown_code(self, mfa_controller, mock_user_with_mfa):
        hashed = mfa_controller.hash_backup_codes(
            mfa_controller.generate_backup_codes(count=3)
        )
        mfa_controller.user_model.get_user_by_id.return_value = mock_user_with_mfa
        mfa_controller.user_model.collection.find_one.return_value = {
            "mfa_backup_codes": hashed
        }

        result = await mfa_controller.recover_mfa(
            "507f1f77bcf86cd799439011", MFARecoveryRequest(backup_code="ZZZZ-ZZZZ-ZZZZ")
        )

        assert result.recovered is False
        mfa_controller.user_model.update_user.assert_not_called()

    @pytest.mark.asyncio
    async def test_recover_requires_mfa_to_be_enabled(self, mfa_controller, mock_user_dto):
        mfa_controller.user_model.get_user_by_id.return_value = mock_user_dto

        with pytest.raises(HTTPException) as exc_info:
            await mfa_controller.recover_mfa(
                "507f1f77bcf86cd799439011", MFARecoveryRequest(backup_code="AAAA-BBBB-CCCC")
            )

        assert exc_info.value.status_code == 400

    # ---------------------------------------------------------------- admin

    @pytest.mark.asyncio
    async def test_emergency_disable_clears_mfa(self, mfa_controller, mock_user_with_mfa):
        """
        This endpoint returned HTTP 500 unconditionally: a missing `await` made `admin_user`
        a coroutine, and the rate-limiter import was rooted at the wrong package.
        """
        admin = _user_dto(id="507f1f77bcf86cd799439099", username="adminuser")
        mfa_controller.user_model.get_user_by_id.side_effect = [mock_user_with_mfa, admin]
        mfa_controller.user_model.collection.find_one.return_value = {
            "mfa_secret": "x", "mfa_backup_codes": ["a:b"]
        }

        with patch(
            "services.auth.app.services.mfa_rate_limiter.mfa_rate_limiter"
        ) as limiter:
            result = await mfa_controller.emergency_disable_mfa(
                "507f1f77bcf86cd799439011", admin.id, "lost phone"
            )

        assert result["disabled"] is True
        assert result["admin_user"] == "adminuser"
        assert result["target_user"] == "testuser"
        assert mfa_controller.user_model.update_user.call_args[0][1] == {
            "mfa_enabled": False,
            "mfa_secret": None,
            "mfa_setup_complete": False,
            "mfa_backup_codes": [],
        }
        limiter.reset_user.assert_called_once_with("507f1f77bcf86cd799439011")

    @pytest.mark.asyncio
    async def test_emergency_disable_helps_a_half_enrolled_account(self, mfa_controller):
        """
        A secret written but never confirmed is exactly the state a lost phone leaves
        behind. Gating on `mfa_enabled` alone refused to clear it.
        """
        target = _user_dto(mfa_enabled=False, mfa_setup_complete=False)
        admin = _user_dto(id="507f1f77bcf86cd799439099", username="adminuser")
        mfa_controller.user_model.get_user_by_id.side_effect = [target, admin]
        mfa_controller.user_model.collection.find_one.return_value = {"mfa_secret": "abc"}

        with patch("services.auth.app.services.mfa_rate_limiter.mfa_rate_limiter"):
            result = await mfa_controller.emergency_disable_mfa(
                "507f1f77bcf86cd799439011", admin.id, "lost phone mid-setup"
            )

        assert result["disabled"] is True

    @pytest.mark.asyncio
    async def test_emergency_disable_on_an_account_with_no_mfa(self, mfa_controller):
        target = _user_dto()
        admin = _user_dto(id="507f1f77bcf86cd799439099", username="adminuser")
        mfa_controller.user_model.get_user_by_id.side_effect = [target, admin]
        mfa_controller.user_model.collection.find_one.return_value = {}

        with pytest.raises(HTTPException) as exc_info:
            await mfa_controller.emergency_disable_mfa(
                "507f1f77bcf86cd799439011", admin.id, "nothing to do"
            )

        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_emergency_disable_unknown_target(self, mfa_controller):
        mfa_controller.user_model.get_user_by_id.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            await mfa_controller.emergency_disable_mfa("missing", "admin", "reason")

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_emergency_disable_unknown_admin(self, mfa_controller, mock_user_with_mfa):
        mfa_controller.user_model.get_user_by_id.side_effect = [mock_user_with_mfa, None]

        with pytest.raises(HTTPException) as exc_info:
            await mfa_controller.emergency_disable_mfa(
                "507f1f77bcf86cd799439011", "missing-admin", "reason"
            )

        assert exc_info.value.status_code == 404
        mfa_controller.user_model.update_user.assert_not_called()


class TestEnableMFARequestSchema:
    def test_enable_mfa_request_has_no_required_fields(self):
        assert isinstance(EnableMFARequest(), EnableMFARequest)
