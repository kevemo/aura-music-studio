from __future__ import annotations

import base64
import hashlib
import sqlite3
import secrets
from datetime import datetime, timedelta, timezone

import pytest

from aura_music_studio.accounts import AccountStore
from aura_music_studio.aura_sec_protocol import ActionRisk, ActionType
from aura_music_studio.aura_sec_store import AuraSecStore
from aura_music_studio.aura_sec_strong_reauth import (
    StrongReauthAssertion,
    VerifiedStrongReauthEvidence,
)

_TEST_SECRET = b"aura-sec-strong-reauth-test-secret-v1"
_SESSION_BINDING = "a" * 64
_CREDENTIAL_FP = "b" * 64


def _setup(tmp_path):
    accounts = AccountStore(tmp_path / "strong-reauth.sqlite3")
    signup = accounts.signup(
        "strong.reauth@example.test",
        "Strong Reauth Member",
        "secure-strong-reauth-password",
        "free",
    )
    accounts.decide_membership(signup.approval_token, "approve", "test-owner")
    security = AuraSecStore(accounts)
    security.activate_verified_purchase(
        signup.user_id,
        sku_id="security-test",
        payment_reference="strong-reauth-payment",
        device_limit=1,
        period_days=31,
        verified_by="test-billing-verifier",
    )
    device = security.enroll_attested_device(
        signup.user_id,
        display_name="Strong Reauth Test Device",
        platform="windows",
        architecture="x64",
        public_key_fingerprint="f" * 64,
    )
    action = security.propose_action(
        signup.user_id,
        device["id"],
        action_type=ActionType.REMOTE_WIPE.value,
        risk_class=ActionRisk.STRONG_REAUTH_REQUIRED.value,
        details={"reason": "strong reauth test fixture"},
    )
    return accounts, security, signup.user_id, device, action


def _assertion(
    user_id: str,
    action_id: str,
    device_id: str,
    *,
    nonce: str = "strong-reauth-challenge-0001",
    session_binding: str = _SESSION_BINDING,
    authentication_context: str = "webauthn",
    issued_at: datetime | None = None,
    expires_at: datetime | None = None,
) -> StrongReauthAssertion:
    issued = issued_at or datetime.now(timezone.utc)
    expiry = expires_at or issued + timedelta(seconds=120)
    return StrongReauthAssertion(
        user_id=user_id,
        action_id=action_id,
        device_id=device_id,
        issued_at=issued,
        expires_at=expiry,
        challenge_nonce=nonce,
        session_binding_digest=session_binding,
        authentication_context=authentication_context,
    )


def _proof(assertion: StrongReauthAssertion) -> str:
    raw = hashlib.sha256(_TEST_SECRET + assertion.canonical_payload()).digest()
    return base64.b64encode(raw).decode("ascii")


def _verifier(
    *,
    subject_user_id: str | None = None,
    action_id: str | None = None,
    device_id: str | None = None,
    session_binding: str | None = None,
    method: str | None = None,
    assurance: str = "aal2",
    credential_fingerprint: str = _CREDENTIAL_FP,
    evidence_digest: str | None = None,
):
    def verify(assertion: StrongReauthAssertion, proof: bytes):
        expected = hashlib.sha256(_TEST_SECRET + assertion.canonical_payload()).digest()
        if not secrets.compare_digest(proof, expected):
            return None
        return VerifiedStrongReauthEvidence(
            subject_user_id=subject_user_id or assertion.user_id,
            action_id=action_id or assertion.action_id,
            device_id=device_id or assertion.device_id,
            session_binding_digest=session_binding or assertion.session_binding_digest,
            verifier_id="test-strong-reauth-verifier",
            authentication_method=method or assertion.authentication_context,
            assurance_level=assurance,
            credential_fingerprint=credential_fingerprint,
            evidence_digest=evidence_digest or hashlib.sha256(assertion.canonical_payload()).hexdigest(),
        )

    return verify


def _approve(security, user_id, action, device, *, assertion=None, verifier=None, proof=None, now=None):
    assertion = assertion or _assertion(user_id, action["id"], device["id"])
    return security.approve_action_with_verified_reauth(
        user_id,
        action["id"],
        assertion,
        proof_b64=proof or _proof(assertion),
        evidence_verifier=verifier or _verifier(),
        now=now,
    )


def test_boolean_strong_reauth_flag_can_never_authorize_destructive_action(tmp_path):
    _accounts, security, user_id, _device, action = _setup(tmp_path)

    with pytest.raises(PermissionError, match="Verifier-backed strong re-authentication evidence"):
        security.approve_action(user_id, action["id"], strong_reauth_verified=False)
    with pytest.raises(PermissionError, match="Boolean strong re-authentication flags are not trusted"):
        security.approve_action(user_id, action["id"], strong_reauth_verified=True)

    assert security.get_action(user_id, action["id"])["status"] == "proposed"


def test_valid_verifier_backed_evidence_approves_action_and_returns_audit_identity(tmp_path):
    _accounts, security, user_id, device, action = _setup(tmp_path)
    approved = _approve(security, user_id, action, device)

    assert approved["status"] == "approved"
    assert approved["approved_at"]
    assert len(approved["strong_reauth_acceptance_id"]) == 32
    assert approved["strong_reauth_verifier"] == "test-strong-reauth-verifier"
    assert approved["strong_reauth_method"] == "webauthn"
    assert approved["strong_reauth_assurance"] == "aal2"


def test_missing_or_invalid_verifier_proof_fails_without_approving_or_consuming_challenge(tmp_path):
    _accounts, security, user_id, device, action = _setup(tmp_path)
    assertion = _assertion(user_id, action["id"], device["id"])

    with pytest.raises(PermissionError, match="trusted Aura Sec strong re-authentication verifier"):
        security.approve_action_with_verified_reauth(
            user_id,
            action["id"],
            assertion,
            proof_b64=_proof(assertion),
            evidence_verifier=None,
        )
    invalid = base64.b64encode(b"x" * 32).decode("ascii")
    with pytest.raises(PermissionError, match="evidence was not verified"):
        _approve(
            security,
            user_id,
            action,
            device,
            assertion=assertion,
            proof=invalid,
        )
    assert security.get_action(user_id, action["id"])["status"] == "proposed"

    approved = _approve(security, user_id, action, device, assertion=assertion)
    assert approved["status"] == "approved"


def test_assertion_is_bound_to_exact_user_action_device_and_session(tmp_path):
    _accounts, security, user_id, device, action = _setup(tmp_path)

    wrong_action = _assertion(user_id, "other-action-00000001", device["id"])
    with pytest.raises(PermissionError, match="different action"):
        _approve(security, user_id, action, device, assertion=wrong_action)

    wrong_device = _assertion(user_id, action["id"], "other-device-00000001")
    with pytest.raises(PermissionError, match="different device"):
        _approve(security, user_id, action, device, assertion=wrong_device)

    assertion = _assertion(user_id, action["id"], device["id"])
    with pytest.raises(PermissionError, match="session binding is incorrect"):
        _approve(
            security,
            user_id,
            action,
            device,
            assertion=assertion,
            verifier=_verifier(session_binding="c" * 64),
        )

    assert security.get_action(user_id, action["id"])["status"] == "proposed"


def test_verified_subject_action_device_method_and_digest_must_match_assertion(tmp_path):
    _accounts, security, user_id, device, action = _setup(tmp_path)
    assertion = _assertion(user_id, action["id"], device["id"])

    cases = [
        (_verifier(subject_user_id="different-user-00000001"), "subject is incorrect"),
        (_verifier(action_id="different-action-000001"), "action binding is incorrect"),
        (_verifier(device_id="different-device-000001"), "device binding is incorrect"),
        (_verifier(method="passkey"), "method does not match"),
        (_verifier(evidence_digest="d" * 64), "evidence digest"),
    ]
    for verifier, error in cases:
        with pytest.raises(PermissionError, match=error):
            _approve(
                security,
                user_id,
                action,
                device,
                assertion=assertion,
                verifier=verifier,
            )

    assert security.get_action(user_id, action["id"])["status"] == "proposed"


def test_expired_evidence_or_revoked_device_fails_closed(tmp_path):
    _accounts, security, user_id, device, action = _setup(tmp_path)
    now = datetime.now(timezone.utc)
    expired = _assertion(
        user_id,
        action["id"],
        device["id"],
        issued_at=now - timedelta(minutes=3),
        expires_at=now - timedelta(seconds=1),
    )
    with pytest.raises(PermissionError, match="proof has expired"):
        _approve(security, user_id, action, device, assertion=expired, now=now)

    fresh = _assertion(user_id, action["id"], device["id"], nonce="strong-reauth-challenge-0002")
    security.revoke_device(user_id, device["id"])
    with pytest.raises(PermissionError, match="Revoked Aura Sec device"):
        _approve(security, user_id, action, device, assertion=fresh)

    assert security.get_action(user_id, action["id"])["status"] == "proposed"


def test_assurance_must_be_aal2_or_aal3(tmp_path):
    _accounts, security, user_id, device, action = _setup(tmp_path)
    assertion = _assertion(user_id, action["id"], device["id"])
    with pytest.raises(PermissionError, match="assurance is insufficient"):
        _approve(
            security,
            user_id,
            action,
            device,
            assertion=assertion,
            verifier=_verifier(assurance="aal1"),
        )


def test_consumed_challenge_and_assertion_cannot_be_replayed(tmp_path):
    accounts, security, user_id, device, action = _setup(tmp_path)
    assertion = _assertion(user_id, action["id"], device["id"])
    approved = _approve(security, user_id, action, device, assertion=assertion)
    assert approved["status"] == "approved"

    # Adversarial state-reset fixture: even if application state is corrupted back to proposed,
    # the durable acceptance ledger must independently prevent captured proof replay.
    with sqlite3.connect(accounts.db_path) as con:
        con.execute(
            "UPDATE aura_sec_actions SET status='proposed',approved_at=NULL WHERE user_id=? AND id=?",
            (user_id, action["id"]),
        )

    with pytest.raises(PermissionError, match="proof was replayed"):
        _approve(security, user_id, action, device, assertion=assertion)
    assert security.get_action(user_id, action["id"])["status"] == "proposed"
