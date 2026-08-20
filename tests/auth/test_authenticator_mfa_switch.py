"""
`AUTHENTICATOR_MFA_ENABLED` — the switch for the authenticator-app enrolment flow this
branch adds.

Named for the method, not for MFA in general: e-mail 2FA is expected later and the two
have to be switchable independently.

The distinction these tests exist to protect: the switch gates **enrolment**, not the
**login challenge**. Accounts already enrolled on `dev` keep being challenged whichever
way it is set. Making `false` also suppress the challenge would silently drop a second
factor those users opted into — a security downgrade applied by a config default rather
than by a decision.
"""

import re


SOURCE_CONFIG = "services/auth/app/core/config.py"
SOURCE_AUTH = "services/auth/app/services/auth_service.py"
SOURCE_ROUTES = "services/auth/app/api/v1/routes/mfa.py"


class TestDefault:
    def test_defaults_to_off(self):
        """New capability ships inert; PTC turns it on when they have confirmed it."""
        src = open(SOURCE_CONFIG).read()
        m = re.search(r'AUTHENTICATOR_MFA_ENABLED[^\n]*os\.getenv\(\s*"AUTHENTICATOR_MFA_ENABLED"\s*,\s*"([^"]+)"', src)
        assert m, "switch is no longer read from the environment with a literal default"
        assert m.group(1).lower() == "false"

    def test_truthy_spellings_are_accepted(self):
        src = open(SOURCE_CONFIG).read()
        line = [l for l in src.split("\n") if "AUTHENTICATOR_MFA_ENABLED: bool" in l][0]
        for spelling in ('"true"', '"1"', '"yes"'):
            assert spelling in line, f"{spelling} should be accepted as on"


class TestItGatesEnrolmentOnly:
    def test_enrolment_route_checks_the_switch(self):
        src = open(SOURCE_ROUTES).read()
        enable_at = src.index('@router.post("/enable"')
        verify_at = src.index('@router.post("/verify"')
        body = src[enable_at:verify_at]
        assert "AUTHENTICATOR_MFA_ENABLED" in body

    def test_the_check_happens_before_any_secret_is_minted(self):
        """A later check could leave a half-finished enrolment and an orphaned secret."""
        src = open(SOURCE_ROUTES).read()
        enable_at = src.index('@router.post("/enable"')
        verify_at = src.index('@router.post("/verify"')
        body = src[enable_at:verify_at]
        assert body.index("AUTHENTICATOR_MFA_ENABLED") < body.index("mfa_controller")

    def test_login_is_NOT_gated_by_the_switch(self):
        """The load-bearing assertion. If this ever fails, turning the switch off has
        started suppressing challenges for accounts that are already enrolled."""
        src = open(SOURCE_AUTH).read()
        assert "AUTHENTICATOR_MFA_ENABLED" not in src, (
            "auth_service must not consult the enrolment switch — already-enrolled "
            "accounts keep their second factor regardless of it"
        )

    def test_both_login_gates_still_read_the_user_state(self):
        src = open(SOURCE_AUTH).read()
        assert src.count("user.mfa_enabled and user.mfa_setup_complete") == 2


class TestExistingEnrolmentSurvives:
    def test_the_switch_never_appears_on_a_line_that_writes_secrets(self):
        """Flipping it must not delete or rewrite enrolment data."""
        for path in (SOURCE_AUTH, SOURCE_ROUTES):
            for line in open(path).read().split("\n"):
                if "AUTHENTICATOR_MFA_ENABLED" in line:
                    assert "mfa_secret" not in line
                    assert "delete" not in line.lower()

    def test_legacy_plaintext_secrets_still_decrypt(self):
        """Accounts enrolled before this branch stored their secret in plaintext; the
        encryption work must pass those through rather than fail every code."""
        from libs.crypto import decrypt_secret
        assert decrypt_secret("JBSWY3DPEHPK3PXP") == "JBSWY3DPEHPK3PXP"


class TestClientCanHideTheOption:
    def test_status_exposes_method_availability(self):
        from services.auth.app.schemas.mfa import MFAStatusResponse
        assert "method_available" in MFAStatusResponse.model_fields

    def test_method_available_is_independent_of_per_user_state(self):
        """A user already enrolled sees enabled=True even while enrolment is closed."""
        from services.auth.app.schemas.mfa import MFAStatusResponse
        r = MFAStatusResponse(method_available=False, enabled=True,
                              setup_complete=True, backup_codes_remaining=3)
        assert r.method_available is False and r.enabled is True
