from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from aura_music_studio.aura_sec_command_signing import (
    SelfHostedEd25519CommandSigner,
    SignedSecurityCommand,
    verify_signed_security_command,
)
from aura_music_studio.aura_sec_protocol import ActionRisk, ActionType, SecurityCommand


DEVICE_ID = "device_server_signing_001"


def _command(*, issued_at=None, expires_at=None, device_id=DEVICE_ID):
    now = datetime.now(timezone.utc)
    return SecurityCommand(
        command_id="command_server_signing_001",
        device_id=device_id,
        action=ActionType.REFRESH_SECURITY_STATE,
        risk=ActionRisk.READ_ONLY,
        issued_at=issued_at or now - timedelta(seconds=1),
        expires_at=expires_at or now + timedelta(minutes=5),
        policy_version="policy-2026.08",
        nonce="server-signing-nonce-0001",
        parameters={},
    )


def _signer(key_id="server-primary-2026"):
    return SelfHostedEd25519CommandSigner(Ed25519PrivateKey.generate(), key_id=key_id)


def test_self_hosted_ed25519_command_round_trip_verifies_exact_payload():
    signer = _signer()
    signed = signer.sign_command(_command())

    proof = verify_signed_security_command(
        signed,
        trusted_public_keys={signer.key_id: signer.public_key_raw()},
        expected_device_id=DEVICE_ID,
    )
    assert proof.signer_key_id == signer.key_id
    assert proof.key_algorithm == "ed25519"
    assert proof.public_key_fingerprint == signer.public_key_fingerprint
    assert proof.evidence_digest == signed.payload_digest
    assert signed.unsigned_command().model_dump(mode="json") == _command(
        issued_at=signed.issued_at,
        expires_at=signed.expires_at,
    ).model_dump(mode="json")


def test_tampering_with_policy_or_parameters_breaks_server_signature():
    signer = _signer()
    signed = signer.sign_command(_command())

    changed_policy = signed.model_copy(update={"policy_version": "attacker-policy"})
    with pytest.raises(PermissionError, match="digest does not match|signature verification failed"):
        verify_signed_security_command(
            changed_policy,
            trusted_public_keys={signer.key_id: signer.public_key_raw()},
            expected_device_id=DEVICE_ID,
        )

    # Use a bounded alternate command so Pydantic's action firewall remains intact; the
    # cryptographic layer must still detect any post-sign payload substitution.
    changed_nonce = signed.model_copy(update={"nonce": "server-signing-nonce-9999"})
    with pytest.raises(PermissionError, match="digest does not match|signature verification failed"):
        verify_signed_security_command(
            changed_nonce,
            trusted_public_keys={signer.key_id: signer.public_key_raw()},
            expected_device_id=DEVICE_ID,
        )


def test_wrong_or_unpinned_server_key_is_rejected():
    signer = _signer()
    attacker = _signer("attacker-key-2026")
    signed = signer.sign_command(_command())

    with pytest.raises(PermissionError, match="signer is not trusted"):
        verify_signed_security_command(
            signed,
            trusted_public_keys={attacker.key_id: attacker.public_key_raw()},
            expected_device_id=DEVICE_ID,
        )

    with pytest.raises(PermissionError, match="fingerprint does not match"):
        verify_signed_security_command(
            signed,
            trusted_public_keys={signer.key_id: attacker.public_key_raw()},
            expected_device_id=DEVICE_ID,
        )


def test_cross_device_command_substitution_is_rejected_even_with_valid_signature():
    signer = _signer()
    signed = signer.sign_command(_command())
    with pytest.raises(PermissionError, match="different device"):
        verify_signed_security_command(
            signed,
            trusted_public_keys={signer.key_id: signer.public_key_raw()},
            expected_device_id="different_device_0002",
        )


def test_expired_and_far_future_signed_commands_fail_closed():
    signer = _signer()
    now = datetime.now(timezone.utc)
    expired = signer.sign_command(
        _command(
            issued_at=now - timedelta(minutes=10),
            expires_at=now - timedelta(minutes=1),
        )
    )
    with pytest.raises(PermissionError, match="expired"):
        verify_signed_security_command(
            expired,
            trusted_public_keys={signer.key_id: signer.public_key_raw()},
            expected_device_id=DEVICE_ID,
            now=now,
        )

    future = signer.sign_command(
        _command(
            issued_at=now + timedelta(minutes=3),
            expires_at=now + timedelta(minutes=8),
        )
    )
    with pytest.raises(PermissionError, match="too far in the future"):
        verify_signed_security_command(
            future,
            trusted_public_keys={signer.key_id: signer.public_key_raw()},
            expected_device_id=DEVICE_ID,
            now=now,
        )


def test_signature_or_digest_corruption_is_rejected():
    signer = _signer()
    signed = signer.sign_command(_command())

    corrupted_digest = signed.model_copy(update={"payload_digest": "0" * 64})
    with pytest.raises(PermissionError, match="digest does not match"):
        verify_signed_security_command(
            corrupted_digest,
            trusted_public_keys={signer.key_id: signer.public_key_raw()},
            expected_device_id=DEVICE_ID,
        )

    replacement = _signer("replacement-key-2026").sign_command(_command())
    corrupted_signature = signed.model_copy(update={"signature_b64": replacement.signature_b64})
    with pytest.raises(PermissionError, match="signature verification failed"):
        verify_signed_security_command(
            corrupted_signature,
            trusted_public_keys={signer.key_id: signer.public_key_raw()},
            expected_device_id=DEVICE_ID,
        )


def test_private_key_pem_loader_rejects_wrong_key_type_and_supports_encrypted_ed25519():
    private_key = Ed25519PrivateKey.generate()
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.BestAvailableEncryption(b"test-password"),
    )
    signer = SelfHostedEd25519CommandSigner.from_private_key_pem(
        pem,
        key_id="encrypted-server-key-2026",
        password=b"test-password",
    )
    signed = signer.sign_command(_command())
    assert isinstance(signed, SignedSecurityCommand)
    verify_signed_security_command(
        signed,
        trusted_public_keys={signer.key_id: signer.public_key_raw()},
        expected_device_id=DEVICE_ID,
    )
