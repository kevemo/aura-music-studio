from __future__ import annotations

from types import SimpleNamespace

import pytest
from webauthn.helpers import bytes_to_base64url
from webauthn.helpers.exceptions import InvalidAuthenticationResponse

from aura_music_studio.accounts import AccountStore
import aura_music_studio.aura_sec_passkeys as passkeys_module
from aura_music_studio.aura_sec_passkeys import AuraSecPasskeyService
from aura_music_studio.aura_sec_protocol import ActionRisk, ActionType
from aura_music_studio.aura_sec_store import AuraSecStore


PASSWORD = "passkey-enrolment-password"
CREDENTIAL_BYTES = b"aura-sec-test-credential-id"
PUBLIC_KEY_BYTES = b"test-cose-public-key-material"


def _enum(value):
    return SimpleNamespace(value=value)


def _setup(tmp_path, monkeypatch):
    monkeypatch.setenv("LSS_PUBLIC_BASE_URL", "https://security.example.test")
    accounts = AccountStore(tmp_path / "aura-sec-passkeys.sqlite3")
    signup = accounts.signup(
        "passkey.member@example.test",
        "Passkey Member",
        PASSWORD,
        "free",
    )
    accounts.decide_membership(signup.approval_token, "approve", "test-owner")
    session = accounts.create_session(signup.user_id)
    security = AuraSecStore(accounts)
    security.activate_verified_purchase(
        signup.user_id,
        sku_id="aura-sec-test",
        payment_reference="payment-passkey-test",
        device_limit=1,
        period_days=31,
        verified_by="test-verifier",
    )
    device = security.enroll_attested_device(
        signup.user_id,
        display_name="Passkey Test PC",
        platform="windows",
        architecture="x64",
        public_key_fingerprint="f" * 64,
    )
    action = security.propose_action(
        signup.user_id,
        device["id"],
        action_type=ActionType.REMOTE_WIPE.value,
        risk_class=ActionRisk.STRONG_REAUTH_REQUIRED.value,
        details={"command_parameters": {}},
    )
    return accounts, security, AuraSecPasskeyService(accounts, security), signup.user_id, session, action


def _verified_registration():
    return SimpleNamespace(
        credential_id=CREDENTIAL_BYTES,
        credential_public_key=PUBLIC_KEY_BYTES,
        sign_count=0,
        aaguid="00000000-0000-0000-0000-000000000000",
        fmt=_enum("none"),
        credential_device_type=_enum("multi_device"),
        credential_backed_up=True,
        user_verified=True,
    )


def _verified_authentication():
    return SimpleNamespace(
        credential_id=CREDENTIAL_BYTES,
        new_sign_count=1,
        credential_device_type=_enum("multi_device"),
        credential_backed_up=True,
        user_verified=True,
    )


def _register(service, user_id, session, monkeypatch):
    begin = service.begin_registration(
        user_id,
        session_token=session,
        password=PASSWORD,
        label="Studio passkey",
    )
    monkeypatch.setattr(
        passkeys_module,
        "verify_registration_response",
        lambda **_kwargs: _verified_registration(),
    )
    completed = service.complete_registration(
        user_id,
        session_token=session,
        ceremony_id=begin["ceremony_id"],
        credential_response={"response": {"transports": ["internal"]}},
        label="Studio passkey",
    )
    return begin, completed


def test_passkey_registration_requires_existing_account_reauthentication(tmp_path, monkeypatch):
    _accounts, _security, service, user_id, session, _action = _setup(tmp_path, monkeypatch)
    with pytest.raises(PermissionError, match="password"):
        service.begin_registration(
            user_id,
            session_token=session,
            password="wrong-password-value",
        )


def test_registration_options_require_user_verification_and_store_no_private_key(tmp_path, monkeypatch):
    _accounts, _security, service, user_id, session, _action = _setup(tmp_path, monkeypatch)
    begin, completed = _register(service, user_id, session, monkeypatch)

    assert begin["public_key"]["rp"]["id"] == "security.example.test"
    assert begin["public_key"]["authenticatorSelection"]["userVerification"] == "required"
    assert begin["private_key_received_by_server"] is False
    assert begin["biometric_data_received_by_server"] is False
    assert completed["registered"] is True
    assert completed["credential"]["user_verified"] is True
    assert completed["private_key_received_by_server"] is False
    assert service.has_active_credential(user_id) is True
    assert service.list_credentials(user_id)[0]["label"] == "Studio passkey"


def test_insecure_non_local_public_origin_is_rejected(tmp_path, monkeypatch):
    _accounts, _security, service, user_id, session, _action = _setup(tmp_path, monkeypatch)
    monkeypatch.setenv("LSS_PUBLIC_BASE_URL", "http://security.example.test")
    with pytest.raises(RuntimeError, match="HTTPS"):
        service.begin_registration(
            user_id,
            session_token=session,
            password=PASSWORD,
        )


def test_high_risk_assertion_creates_short_lived_action_bound_evidence(tmp_path, monkeypatch):
    accounts, _security, service, user_id, session, action = _setup(tmp_path, monkeypatch)
    _register(service, user_id, session, monkeypatch)

    begin = service.begin_action_verification(user_id, action["id"], session_token=session)
    assert begin["user_verification_required"] is True
    assert begin["command_issued"] is False
    assert begin["public_key"]["userVerification"] == "required"
    assert begin["public_key"]["allowCredentials"][0]["id"] == bytes_to_base64url(CREDENTIAL_BYTES)

    monkeypatch.setattr(
        passkeys_module,
        "verify_authentication_response",
        lambda **_kwargs: _verified_authentication(),
    )
    verified = service.complete_action_verification(
        user_id,
        action["id"],
        session_token=session,
        ceremony_id=begin["ceremony_id"],
        credential_response={"id": bytes_to_base64url(CREDENTIAL_BYTES)},
    )
    assert verified["verified"] is True
    assert verified["method"] == "webauthn"
    assert verified["one_time"] is True
    assert verified["command_issued"] is False

    consumed = service.consume_action_evidence(
        user_id,
        action["id"],
        session_token=session,
        evidence_id=verified["evidence_id"],
    )
    assert consumed["verified"] is True
    assert consumed["method"] == "webauthn"
    assert consumed["consumed"] is True

    with pytest.raises(PermissionError, match="already been used"):
        service.consume_action_evidence(
            user_id,
            action["id"],
            session_token=session,
            evidence_id=verified["evidence_id"],
        )

    other_session = accounts.create_session(user_id)
    second = service.begin_action_verification(user_id, action["id"], session_token=session)
    monkeypatch.setattr(
        passkeys_module,
        "verify_authentication_response",
        lambda **_kwargs: _verified_authentication(),
    )
    second_verified = service.complete_action_verification(
        user_id,
        action["id"],
        session_token=session,
        ceremony_id=second["ceremony_id"],
        credential_response={"id": bytes_to_base64url(CREDENTIAL_BYTES)},
    )
    with pytest.raises(PermissionError, match="another session"):
        service.consume_action_evidence(
            user_id,
            action["id"],
            session_token=other_session,
            evidence_id=second_verified["evidence_id"],
        )


def test_invalid_authenticator_assertion_fails_closed_and_spends_ceremony(tmp_path, monkeypatch):
    _accounts, _security, service, user_id, session, action = _setup(tmp_path, monkeypatch)
    _register(service, user_id, session, monkeypatch)
    begin = service.begin_action_verification(user_id, action["id"], session_token=session)

    def reject(**_kwargs):
        raise InvalidAuthenticationResponse("bad assertion")

    monkeypatch.setattr(passkeys_module, "verify_authentication_response", reject)
    with pytest.raises(PermissionError, match="verification failed"):
        service.complete_action_verification(
            user_id,
            action["id"],
            session_token=session,
            ceremony_id=begin["ceremony_id"],
            credential_response={"id": bytes_to_base64url(CREDENTIAL_BYTES)},
        )
    with pytest.raises(PermissionError, match="already been used"):
        service.complete_action_verification(
            user_id,
            action["id"],
            session_token=session,
            ceremony_id=begin["ceremony_id"],
            credential_response={"id": bytes_to_base64url(CREDENTIAL_BYTES)},
        )
