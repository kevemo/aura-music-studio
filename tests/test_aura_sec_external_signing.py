from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from aura_music_studio.aura_sec_command_signing import verify_signed_security_command
from aura_music_studio.aura_sec_external_signing import (
    ExternalCustodyEd25519CommandSigner,
    ExternalSigningEvidence,
    ExternalSigningRequest,
)
from aura_music_studio.aura_sec_protocol import ActionRisk, ActionType, SecurityCommand


DEVICE_ID = "device-external-custody-0001"
KEY_ID = "aura-sec-hsm-primary-2026"


def _command(*, nonce: str = "external-custody-nonce-0001") -> SecurityCommand:
    now = datetime.now(timezone.utc)
    return SecurityCommand(
        command_id="command-external-custody-0001",
        device_id=DEVICE_ID,
        action=ActionType.RUN_QUICK_SCAN,
        risk=ActionRisk.LOW_RISK,
        issued_at=now - timedelta(seconds=1),
        expires_at=now + timedelta(minutes=5),
        policy_version="policy-external-custody-1",
        nonce=nonce,
        parameters={},
    )


def _fixture():
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    seen: list[ExternalSigningRequest] = []

    def adapter(request: ExternalSigningRequest):
        seen.append(request)
        return ExternalSigningEvidence(
            signer_key_id=request.signer_key_id,
            key_algorithm=request.key_algorithm,
            public_key_fingerprint=request.public_key_fingerprint,
            payload_digest=request.payload_digest,
            provider_id="test-hsm-provider",
            provider_request_id=f"hsm-request-{len(seen):04d}",
            signature=private_key.sign(request.payload),
        )

    signer = ExternalCustodyEd25519CommandSigner(
        public_key,
        key_id=KEY_ID,
        signing_adapter=adapter,
    )
    return private_key, signer, seen


def test_external_custody_signer_round_trip_verifies_without_private_key_in_signer():
    _private_key, signer, seen = _fixture()
    signed = signer.sign_command(_command())

    assert signer.custody_mode == "external_non_exportable"
    assert not hasattr(signer, "_private_key")
    assert not hasattr(signer, "private_key")
    assert not hasattr(type(signer), "from_private_key_pem")
    assert len(seen) == 1
    assert seen[0].payload_digest == signed.payload_digest
    assert seen[0].payload == signed.canonical_signed_payload()

    proof = verify_signed_security_command(
        signed,
        trusted_public_keys={signer.key_id: signer.public_key_raw()},
        expected_device_id=DEVICE_ID,
    )
    assert proof.signer_key_id == KEY_ID
    assert proof.public_key_fingerprint == signer.public_key_fingerprint
    assert signer.last_verified_evidence is not None
    assert signer.last_verified_evidence.provider_id == "test-hsm-provider"
    assert signer.last_verified_evidence.provider_request_id == "hsm-request-0001"


def test_external_adapter_receives_only_public_identity_and_canonical_payload():
    private_key = Ed25519PrivateKey.generate()
    captured: list[ExternalSigningRequest] = []

    def adapter(request: ExternalSigningRequest):
        captured.append(request)
        assert set(request.__dict__) == {
            "signer_key_id",
            "key_algorithm",
            "public_key_fingerprint",
            "payload_digest",
            "payload",
        }
        return ExternalSigningEvidence(
            signer_key_id=request.signer_key_id,
            key_algorithm="ed25519",
            public_key_fingerprint=request.public_key_fingerprint,
            payload_digest=request.payload_digest,
            provider_id="isolated-signer",
            provider_request_id="isolated-request-001",
            signature=private_key.sign(request.payload),
        )

    signer = ExternalCustodyEd25519CommandSigner(
        private_key.public_key(),
        key_id=KEY_ID,
        signing_adapter=adapter,
    )
    signer.sign_command(_command())
    assert len(captured) == 1


def test_wrong_external_key_signature_is_rejected_before_signed_command_is_returned():
    private_key = Ed25519PrivateKey.generate()
    attacker = Ed25519PrivateKey.generate()

    def adapter(request: ExternalSigningRequest):
        return ExternalSigningEvidence(
            signer_key_id=request.signer_key_id,
            key_algorithm=request.key_algorithm,
            public_key_fingerprint=request.public_key_fingerprint,
            payload_digest=request.payload_digest,
            provider_id="test-hsm-provider",
            provider_request_id="wrong-key-request-001",
            signature=attacker.sign(request.payload),
        )

    signer = ExternalCustodyEd25519CommandSigner(
        private_key.public_key(),
        key_id=KEY_ID,
        signing_adapter=adapter,
    )
    with pytest.raises(PermissionError, match="signature verification failed"):
        signer.sign_command(_command())
    assert signer.last_verified_evidence is None


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("signer_key_id", "different-hsm-key", "unexpected key id"),
        ("key_algorithm", "rsa-pss-sha256", "unexpected key algorithm"),
        ("public_key_fingerprint", "0" * 64, "wrong key fingerprint"),
        ("payload_digest", "1" * 64, "different payload"),
        ("provider_id", "bad provider with spaces", "provider identity"),
        ("provider_request_id", "x", "request identity"),
    ],
)
def test_external_custody_metadata_must_match_exact_request(field, value, message):
    private_key = Ed25519PrivateKey.generate()

    def adapter(request: ExternalSigningRequest):
        evidence = ExternalSigningEvidence(
            signer_key_id=request.signer_key_id,
            key_algorithm=request.key_algorithm,
            public_key_fingerprint=request.public_key_fingerprint,
            payload_digest=request.payload_digest,
            provider_id="test-hsm-provider",
            provider_request_id="metadata-request-001",
            signature=private_key.sign(request.payload),
        )
        return replace(evidence, **{field: value})

    signer = ExternalCustodyEd25519CommandSigner(
        private_key.public_key(),
        key_id=KEY_ID,
        signing_adapter=adapter,
    )
    with pytest.raises(PermissionError, match=message):
        signer.sign_command(_command())
    assert signer.last_verified_evidence is None


def test_provider_exception_or_missing_evidence_fails_closed_and_clears_previous_evidence():
    private_key = Ed25519PrivateKey.generate()
    calls = 0

    def adapter(request: ExternalSigningRequest):
        nonlocal calls
        calls += 1
        if calls == 1:
            return ExternalSigningEvidence(
                signer_key_id=request.signer_key_id,
                key_algorithm=request.key_algorithm,
                public_key_fingerprint=request.public_key_fingerprint,
                payload_digest=request.payload_digest,
                provider_id="test-hsm-provider",
                provider_request_id="success-request-001",
                signature=private_key.sign(request.payload),
            )
        raise RuntimeError("simulated HSM outage")

    signer = ExternalCustodyEd25519CommandSigner(
        private_key.public_key(),
        key_id=KEY_ID,
        signing_adapter=adapter,
    )
    signer.sign_command(_command())
    assert signer.last_verified_evidence is not None

    with pytest.raises(PermissionError, match="provider failed closed"):
        signer.sign_command(_command(nonce="external-custody-nonce-0002"))
    assert signer.last_verified_evidence is None

    missing = ExternalCustodyEd25519CommandSigner(
        private_key.public_key(),
        key_id=KEY_ID,
        signing_adapter=lambda request: None,
    )
    with pytest.raises(PermissionError, match="no trusted signing evidence"):
        missing.sign_command(_command())


def test_invalid_public_key_or_adapter_configuration_fails_before_signing():
    private_key = Ed25519PrivateKey.generate()
    with pytest.raises(ValueError, match="32 raw bytes"):
        ExternalCustodyEd25519CommandSigner(
            b"too-short",
            key_id=KEY_ID,
            signing_adapter=lambda request: None,
        )
    with pytest.raises(TypeError, match="adapter must be callable"):
        ExternalCustodyEd25519CommandSigner(
            private_key.public_key(),
            key_id=KEY_ID,
            signing_adapter=None,  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="key id"):
        ExternalCustodyEd25519CommandSigner(
            private_key.public_key(),
            key_id="bad key id",
            signing_adapter=lambda request: None,
        )


def test_each_command_produces_distinct_provider_request_evidence():
    _private_key, signer, seen = _fixture()
    first = signer.sign_command(_command())
    first_evidence = signer.last_verified_evidence
    second = signer.sign_command(_command(nonce="external-custody-nonce-0003"))
    second_evidence = signer.last_verified_evidence

    assert first.payload_digest != second.payload_digest
    assert len(seen) == 2
    assert first_evidence is not None and second_evidence is not None
    assert first_evidence.provider_request_id == "hsm-request-0001"
    assert second_evidence.provider_request_id == "hsm-request-0002"
    assert first_evidence.payload_digest != second_evidence.payload_digest
