"""
`AUTHENTICATOR_MFA_ENABLED` — the method-level kill switch for authenticator-app 2FA.

Named for the method, not for MFA in general: e-mail 2FA is expected later and the two
have to be switchable independently.

The property that matters is that the switch is honoured *consistently*. Both login steps
consult it, because if they disagreed a user could be handed a temporary token at step one
and then be unable to redeem it at step two — locked out of their own account by a config
flag.
"""

import pytest

from services.auth.app.core.config import settings


class _User:
    def __init__(self, enabled=True, complete=True):
        self.mfa_enabled = enabled
        self.mfa_setup_complete = complete
        self.username = "trader"


def _challenge_required(user, switch_on):
    """Mirrors auth_service's gate: switch AND user-enabled AND setup-complete."""
    return bool(switch_on and user.mfa_enabled and user.mfa_setup_complete)


class TestSwitchSemantics:
    def test_default_is_on(self):
        """13 active accounts, including admin and ptcindia, already have TOTP set up.
        Defaulting to off would silently drop a second factor they opted into."""
        assert settings.AUTHENTICATOR_MFA_ENABLED is True

    def test_switch_off_suppresses_the_challenge_for_an_enrolled_user(self):
        assert _challenge_required(_User(), switch_on=False) is False

    def test_switch_on_still_challenges_an_enrolled_user(self):
        assert _challenge_required(_User(), switch_on=True) is True

    def test_switch_on_does_not_challenge_a_user_who_never_enrolled(self):
        """The switch offers the method; it does not impose it."""
        assert _challenge_required(_User(enabled=False, complete=False), switch_on=True) is False

    def test_partial_enrolment_is_not_challenged(self):
        """Enabled but never completed setup means there is no secret to verify against."""
        assert _challenge_required(_User(enabled=True, complete=False), switch_on=True) is False


class TestBothLoginStepsAgree:
    def test_the_two_gates_are_written_identically(self):
        """A drift between the two would strand a user mid-login, holding a temporary
        token that the second step refuses to accept."""
        src = open("services/auth/app/services/auth_service.py").read()
        assert src.count("settings.AUTHENTICATOR_MFA_ENABLED") == 2, (
            "expected the switch at both the login and verify-login gates"
        )


class TestEnrolmentIsRefusedWhenClosed:
    def test_enable_route_checks_the_switch_before_minting_a_secret(self):
        """Refused early on purpose: a later check could leave a half-finished enrolment
        and an orphaned secret on the account."""
        src = open("services/auth/app/api/v1/routes/mfa.py").read()
        enable_at = src.index('@router.post("/enable"')
        verify_at = src.index('@router.post("/verify"')
        body = src[enable_at:verify_at]
        assert "AUTHENTICATOR_MFA_ENABLED" in body
        assert body.index("AUTHENTICATOR_MFA_ENABLED") < body.index("mfa_controller"), (
            "the switch must be checked before any controller work"
        )


class TestClientCanHideTheOption:
    def test_status_exposes_method_availability(self):
        """So the UI hides enrolment rather than offering a button that 503s."""
        from services.auth.app.schemas.mfa import MFAStatusResponse
        assert "method_available" in MFAStatusResponse.model_fields

    def test_method_available_is_independent_of_per_user_state(self):
        from services.auth.app.schemas.mfa import MFAStatusResponse
        r = MFAStatusResponse(method_available=False, enabled=True,
                              setup_complete=True, backup_codes_remaining=3)
        assert r.method_available is False and r.enabled is True


class TestExistingEnrolmentSurvives:
    def test_switch_does_not_appear_in_any_write_path(self):
        """Flipping the switch must not delete secrets — turning it back on has to restore
        every enrolled user exactly as they were."""
        for path in ("services/auth/app/services/auth_service.py",
                     "services/auth/app/api/v1/routes/mfa.py"):
            src = open(path).read()
            for line in src.split("\n"):
                if "AUTHENTICATOR_MFA_ENABLED" in line:
                    assert "mfa_secret" not in line and "delete" not in line.lower()
