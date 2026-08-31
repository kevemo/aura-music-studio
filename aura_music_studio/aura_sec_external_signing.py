from __future__ import annotations

import base64
import hashlib
import re
import secrets
from dataclasses import dataclass
from typing import Callable

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .aura_sec_command_signing import (
    SignedSecurityCommand,
    canonical_server_command_payload,
    public_key_fingerprint,
)
from .aura_sec_protocol import SecurityCommand

_HEX_256 = re.compile(r"^[0-9a-f]{64}$")
_KEY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,79}$")
_PROVIDER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{1,127}$")
_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{2,255}$")


@dataclass(frozen=True)
class ExternalSigningRequest:
    """Canonical signing request sent to a non-exportable external key-custody adapter."""

    signer_key_id: str
    key_algorithm: str
    public_key_fingerprint: str
    payload_digest: str
    payload: bytes


@dataclass(frozen=True)
class ExternalSigningEvidence:
    """Structured evidence returned by a trusted HSM/KMS/signing-service adapter."""

    signer_key_id: str
    key_algorithm: str
    public_key_fingerprint: str
    payload_digest: str
    provider_id: str
    provider_request_id: str
    signature: bytes


ExternalSigningAdapter = Callable[[ExternalSigningRequest], ExternalSigningEvidence | None]


@dataclass(frozen=True)
class VerifiedExternalSigningEvidence:
    signer_key_id: str
    key_algorithm: str
    public_key_fingerprint: str
    payload_digest: str
    provider_id: str
    provider_request_id: str


class ExternalCustodyEd25519CommandSigner:
    """Server-command signer whose private key never enters the Aura Sec process.

    Deployment code pins the public Ed25519 key and supplies an adapter backed by a remote HSM,
    KMS, hardware appliance or isolated signing service. The adapter receives only the canonical
    payload and public metadata. Aura Sec independently verifies every returned signature against
    the pinned public key before creating a ``SignedSecurityCommand``.

    This class deliberately has no private-key constructor, PEM loader, key-generation path or
    private-key serialization method. It establishes a fail-closed custody contract; it does not
    claim that any particular production HSM/KMS provider is configured.
    """

    def __init__(
        self,
        public_key: Ed25519PublicKey | bytes,
        *,
        key_id: str,
        signing_adapter: ExternalSigningAdapter,
    ):
        key_id = str(key_id or "").strip()
        if not _KEY_ID.fullmatch(key_id):
            raise ValueError("Invalid Aura Sec external signer key id")
        if not callable(signing_adapter):
            raise TypeError("Aura Sec external signing adapter must be callable")
        if isinstance(public_key, Ed25519PublicKey):
            parsed = public_key
        elif isinstance(public_key, (bytes, bytearray)):
            raw = bytes(public_key)
            if len(raw) != 32:
                raise ValueError("Aura Sec external Ed25519 public key must be 32 raw bytes")
            try:
                parsed = Ed25519PublicKey.from_public_bytes(raw)
            except Exception as exc:
                raise ValueError("Aura Sec external Ed25519 public key is invalid") from exc
        else:
            raise TypeError("Aura Sec external signer requires an Ed25519 public key")

        self.key_id = key_id
        self.public_key = parsed
        self.public_key_fingerprint = public_key_fingerprint(parsed)
        self._signing_adapter = signing_adapter
        self._last_verified_evidence: VerifiedExternalSigningEvidence | None = None

    def public_key_raw(self) -> bytes:
        from cryptography.hazmat.primitives import serialization

        return self.public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )

    @property
    def custody_mode(self) -> str:
        return "external_non_exportable"

    @property
    def last_verified_evidence(self) -> VerifiedExternalSigningEvidence | None:
        return self._last_verified_evidence

    @staticmethod
    def _validate_provider_metadata(evidence: ExternalSigningEvidence) -> tuple[str, str]:
        provider_id = str(evidence.provider_id or "").strip()
        request_id = str(evidence.provider_request_id or "").strip()
        if not _PROVIDER_ID.fullmatch(provider_id):
            raise PermissionError("Aura Sec external signer provider identity is invalid")
        if not _REQUEST_ID.fullmatch(request_id):
            raise PermissionError("Aura Sec external signer request identity is invalid")
        return provider_id, request_id

    def _verify_evidence(
        self,
        request: ExternalSigningRequest,
        evidence: ExternalSigningEvidence | None,
    ) -> VerifiedExternalSigningEvidence:
        if not isinstance(evidence, ExternalSigningEvidence):
            raise PermissionError("Aura Sec external signer returned no trusted signing evidence")

        provider_id, request_id = self._validate_provider_metadata(evidence)
        evidence_key_id = str(evidence.signer_key_id or "").strip()
        evidence_algorithm = str(evidence.key_algorithm or "").strip().lower()
        evidence_fingerprint = str(evidence.public_key_fingerprint or "").strip().lower()
        evidence_digest = str(evidence.payload_digest or "").strip().lower()

        if not secrets.compare_digest(evidence_key_id, request.signer_key_id):
            raise PermissionError("Aura Sec external signer used an unexpected key id")
        if evidence_algorithm != "ed25519" or not secrets.compare_digest(
            evidence_algorithm, request.key_algorithm
        ):
            raise PermissionError("Aura Sec external signer used an unexpected key algorithm")
        if not _HEX_256.fullmatch(evidence_fingerprint) or not secrets.compare_digest(
            evidence_fingerprint, request.public_key_fingerprint
        ):
            raise PermissionError("Aura Sec external signer returned the wrong key fingerprint")
        if not _HEX_256.fullmatch(evidence_digest) or not secrets.compare_digest(
            evidence_digest, request.payload_digest
        ):
            raise PermissionError("Aura Sec external signer evidence is bound to a different payload")

        signature = evidence.signature
        if not isinstance(signature, (bytes, bytearray)) or len(signature) != 64:
            raise PermissionError("Aura Sec external Ed25519 signature length is invalid")
        try:
            self.public_key.verify(bytes(signature), request.payload)
        except InvalidSignature as exc:
            raise PermissionError("Aura Sec external signer signature verification failed") from exc
        except Exception as exc:
            raise PermissionError("Aura Sec external signer verification failed closed") from exc

        return VerifiedExternalSigningEvidence(
            signer_key_id=request.signer_key_id,
            key_algorithm="ed25519",
            public_key_fingerprint=request.public_key_fingerprint,
            payload_digest=request.payload_digest,
            provider_id=provider_id,
            provider_request_id=request_id,
        )

    def sign_command(self, command: SecurityCommand) -> SignedSecurityCommand:
        if not isinstance(command, SecurityCommand):
            command = SecurityCommand.model_validate(command)
        payload = canonical_server_command_payload(
            command,
            signer_key_id=self.key_id,
            public_key_fingerprint_hex=self.public_key_fingerprint,
        )
        digest = hashlib.sha256(payload).hexdigest()
        request = ExternalSigningRequest(
            signer_key_id=self.key_id,
            key_algorithm="ed25519",
            public_key_fingerprint=self.public_key_fingerprint,
            payload_digest=digest,
            payload=payload,
        )
        self._last_verified_evidence = None
        try:
            evidence = self._signing_adapter(request)
        except Exception as exc:
            raise PermissionError("Aura Sec external signing provider failed closed") from exc
        verified = self._verify_evidence(request, evidence)
        self._last_verified_evidence = verified

        if not isinstance(evidence, ExternalSigningEvidence):
            raise PermissionError("Aura Sec external signer evidence changed unexpectedly")
        return SignedSecurityCommand(
            **command.model_dump(),
            signing_schema_version=1,
            signer_key_id=self.key_id,
            key_algorithm="ed25519",
            public_key_fingerprint=self.public_key_fingerprint,
            payload_digest=digest,
            signature_b64=base64.b64encode(bytes(evidence.signature)).decode("ascii"),
        )


__all__ = [
    "ExternalCustodyEd25519CommandSigner",
    "ExternalSigningAdapter",
    "ExternalSigningEvidence",
    "ExternalSigningRequest",
    "VerifiedExternalSigningEvidence",
]
