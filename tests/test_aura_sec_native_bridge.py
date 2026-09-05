from __future__ import annotations

import base64
import hashlib
import inspect
import secrets
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from aura_music_studio.accounts import AccountStore
from aura_music_studio.aura_sec_command_signing import (
    SelfHostedEd25519CommandSigner,
    SignedSecurityCommand,
    verify_signed_security_command,
)
from aura_music_studio.aura_sec_command_store import AuraSecCommandStore
from aura_music_studio.aura_sec_native_bridge import (
    AuraSecNativeBridge,
    NativeCommandPoll,
    VerifiedNativePollSignature,
)
from aura_music_studio.aura_sec_protocol import ActionRisk, ActionType
from aura_music_studio.aura_sec_store import AuraSecStore


_TEST_SIGNING_SECRET = b"aura-sec-native-poll-test-secret-v1"
_SERVER_SIGNER = SelfHostedEd25519CommandSigner(
    Ed25519PrivateKey.generate(),
    key_id="test-server-command-2026",
)


def _setup(tmp_path, *, details=None, command_signer=_SERVER_SIGNER):
    accounts = AccountStore(tmp_path / "aura-sec-native-bridge.sqlite3")
    signup = accounts.signup(
        "native.bridge@example.test",
        "Native Bridge Member",
        "secure-native-bridge-password",
        "free",
    )
    accounts.decide_membership(signup.approval_token, "approve", "test-owner")
    security = AuraSecStore(accounts)
    security.activate_verified_purchase(
        signup.user_id,
        sku_id="security-test",
        payment_reference="payment-native-bridge",
        device_limit=1,
        period_days=31,
        verified_by="test-verifier",
    )
    device = security.enroll_attested_device(
        signup.user_id,
        display_name="Windows Test PC",
        platform="windows",
        architecture="x64",
        public_key_fingerprint="d" * 64,
    )
    action = security.propose_action(
        signup.user_id,
        device["id"],
        action_type=ActionType.QUARANTINE_OBJECT.value,
        risk_class=ActionRisk.CONFIRMATION_REQUIRED.value,
        details=details
        or {
            "summary": "human-readable incident description",
            "command_parameters": {"object_id": "object-verified-001"},
        },
    )
    security.approve_action(signup.user_id, action["id"])
    commands = AuraSecCommandStore(accounts, security)
    bridge = AuraSecNativeBridge(
        accounts,
        security,
        commands,
        command_signer=command_signer,
    )
    return signup.user_id, security, device, action, bridge


def _poll(device_id: str, *, sequence=1, nonce="native-poll-nonce-0001", policy_version="policy-1"):
    now = datetime.now(timezone.utc)
    return NativeCommandPoll(
        device_id=device_id,
        sequence=sequence,
        issued_at=now - timedelta(seconds=1),
        expires_at=now + timedelta(seconds=60),
        agent_version="0.1.0",
        policy_version=policy_version,
        session_nonce=nonce,
    )


def _signature_for(poll: NativeCommandPoll) -> str:
    signature = hashlib.sha256(_TEST_SIGNING_SECRET + poll.signed_payload()).digest()
    return base64.b64encode(signature).decode("ascii")


def _verifier(fingerprint: str, payload: bytes, signature: bytes):
    expected = hashlib.sha256(_TEST_SIGNING_SECRET + payload).digest()
    if not secrets.compare_digest(signature, expected):
        return None
    return VerifiedNativePollSignature(
        public_key_fingerprint=fingerprint,
        verifier_id="test-native-poll-verifier",
        key_algorithm="p256",
        evidence_digest=hashlib.sha256(payload).hexdigest(),
    )


def _poll_command(bridge, user_id, poll, *, verifier=_verifier, signature=None):
    return bridge.poll_verified_command(
        user_id,
        poll,
        signature_b64=signature or _signature_for(poll),
        signature_verifier=verifier,
    )


def test_native_poll_has_no_boolean_signature_verification_escape_hatch(tmp_path):
    user_id, _security, device, _action, bridge = _setup(tmp_path)
    parameters = inspect.signature(bridge.poll_verified_command).parameters
    assert "signature_verified" not in parameters
    assert "signature_b64" in parameters
    assert "signature_verifier" in parameters

    poll = _poll(device["id"])
    with pytest.raises(PermissionError, match="trusted Aura Sec native poll signature verifier"):
        bridge.poll_verified_command(
            user_id,
            poll,
            signature_b64=_signature_for(poll),
            signature_verifier=None,
        )


def test_invalid_native_poll_signature_is_rejected_before_replay_state_advances(tmp_path):
    user_id, _security, device, _action, bridge = _setup(tmp_path)
    poll = _poll(device["id"])
    invalid_signature = base64.b64encode(b"x" * 32).decode("ascii")
    with pytest.raises(PermissionError, match="signature was not verified"):
        _poll_command(bridge, user_id, poll, signature=invalid_signature)

    # A valid attempt with the same sequence must still succeed because failed proof did
    # not mutate replay state.
    result = _poll_command(bridge, user_id, poll)
    assert result["command"] is not None


def test_verified_poll_key_must_match_enrolled_device_identity(tmp_path):
    user_id, _security, device, _action, bridge = _setup(tmp_path)
    poll = _poll(device["id"])

    def wrong_key_verifier(fingerprint, payload, signature):
        verified = _verifier(fingerprint, payload, signature)
        assert verified is not None
        return VerifiedNativePollSignature(
            public_key_fingerprint="e" * 64,
            verifier_id=verified.verifier_id,
            key_algorithm=verified.key_algorithm,
            evidence_digest=verified.evidence_digest,
        )

    with pytest.raises(PermissionError, match="does not match the enrolled device identity"):
        _poll_command(bridge, user_id, poll, verifier=wrong_key_verifier)


def test_verified_poll_evidence_digest_must_cover_exact_canonical_payload(tmp_path):
    user_id, _security, device, _action, bridge = _setup(tmp_path)
    poll = _poll(device["id"])

    def wrong_digest_verifier(fingerprint, payload, signature):
        verified = _verifier(fingerprint, payload, signature)
        assert verified is not None
        return VerifiedNativePollSignature(
            public_key_fingerprint=verified.public_key_fingerprint,
            verifier_id=verified.verifier_id,
            key_algorithm=verified.key_algorithm,
            evidence_digest="f" * 64,
        )

    with pytest.raises(PermissionError, match="evidence digest does not match"):
        _poll_command(bridge, user_id, poll, verifier=wrong_digest_verifier)


def test_signature_is_bound_to_poll_policy_sequence_nonce_and_timestamps(tmp_path):
    user_id, _security, device, _action, bridge = _setup(tmp_path)
    original = _poll(device["id"])
    signature = _signature_for(original)
    changed = original.model_copy(update={"policy_version": "policy-2"})

    with pytest.raises(PermissionError, match="signature was not verified"):
        _poll_command(bridge, user_id, changed, signature=signature)


def test_verified_poll_issues_one_preapproved_bounded_and_server_signed_command(tmp_path):
    user_id, _security, device, action, bridge = _setup(tmp_path)
    poll = _poll(device["id"])
    result = _poll_command(bridge, user_id, poll)
    command = result["command"]
    assert command["action"] == "quarantine_object"
    assert command["approval_id"] == action["id"]
    assert command["parameters"] == {"object_id": "object-verified-001"}
    assert "summary" not in command["parameters"]
    assert command["key_algorithm"] == "ed25519"
    assert command["signer_key_id"] == "test-server-command-2026"
    assert len(command["public_key_fingerprint"]) == 64
    assert len(command["payload_digest"]) == 64
    assert command["signature_b64"]

    signed = SignedSecurityCommand.model_validate(command)
    proof = verify_signed_security_command(
        signed,
        trusted_public_keys={_SERVER_SIGNER.key_id: _SERVER_SIGNER.public_key_raw()},
        expected_device_id=device["id"],
    )
    assert proof.signer_key_id == _SERVER_SIGNER.key_id
    assert proof.evidence_digest == command["payload_digest"]

    assert result["member_browser_route_exposed"] is False
    assert result["verification"]["verifier_id"] == "test-native-poll-verifier"
    assert result["verification"]["key_algorithm"] == "p256"
    assert result["verification"]["evidence_digest"] == hashlib.sha256(poll.signed_payload()).hexdigest()
    assert "signature" not in result["verification"]
    assert "public_key_fingerprint" not in result["verification"]


def test_bridge_refuses_to_issue_unsigned_server_command(tmp_path):
    user_id, _security, device, _action, bridge = _setup(tmp_path, command_signer=None)
    with pytest.raises(PermissionError, match="server command signer is required"):
        _poll_command(bridge, user_id, _poll(device["id"]))

    # The unsigned path failed before command persistence. After an operator configures the
    # signer, a later authenticated sequence can still issue the original approved action.
    bridge.command_signer = _SERVER_SIGNER
    second = _poll(device["id"], sequence=2, nonce="native-poll-nonce-0002")
    result = _poll_command(bridge, user_id, second)
    assert result["command"]["signature_b64"]


def test_native_poll_sequence_replay_is_rejected(tmp_path):
    user_id, _security, device, _action, bridge = _setup(tmp_path)
    poll = _poll(device["id"])
    _poll_command(bridge, user_id, poll)
    with pytest.raises(PermissionError, match="sequence|replayed"):
        _poll_command(bridge, user_id, poll)


def test_next_verified_poll_redelivers_exact_same_signed_command_after_issue(tmp_path):
    user_id, _security, device, _action, bridge = _setup(tmp_path)
    first_poll = _poll(device["id"])
    first = _poll_command(bridge, user_id, first_poll)
    second = _poll(device["id"], sequence=2, nonce="native-poll-nonce-0002")
    result = _poll_command(bridge, user_id, second)

    assert result["command"] is not None
    assert result["command"] == first["command"]
    assert result["command"]["command_id"] == first["command"]["command_id"]
    assert result["command"]["signature_b64"] == first["command"]["signature_b64"]
    assert result["verification"]["evidence_digest"] == hashlib.sha256(second.signed_payload()).hexdigest()
    assert "redelivered unchanged" in result["truth"]


def test_unregistered_or_smuggled_command_parameters_fail_closed(tmp_path):
    user_id, _security, device, _action, bridge = _setup(
        tmp_path,
        details={
            "command_parameters": {
                "object_id": "object-verified-001",
                "shell": "powershell -enc not-allowed",
            }
        },
    )
    with pytest.raises(ValueError, match="requires only"):
        _poll_command(bridge, user_id, _poll(device["id"]))


def test_revoked_device_cannot_poll_for_commands(tmp_path):
    user_id, security, device, _action, bridge = _setup(tmp_path)
    security.revoke_device(user_id, device["id"])
    poll = _poll(device["id"])
    with pytest.raises(PermissionError, match="Revoked"):
        _poll_command(bridge, user_id, poll)
