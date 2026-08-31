from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .accounts import AccountStore
from .aura_sec_protocol import ActionRisk
from .aura_sec_store import AuraSecStore

_HEX_256 = re.compile(r"^[0-9a-f]{64}$")
_ALLOWED_METHODS = {"webauthn", "passkey", "hardware_key", "idp_mfa"}
_ALLOWED_ASSURANCE = {"aal2", "aal3"}


def _utc(value: datetime | None = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("Aura Sec strong re-authentication timestamps must be timezone-aware")
    return current.astimezone(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return _utc(value).isoformat()


def _hash_nonce(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class StrongReauthAssertion(BaseModel):
    """Short-lived action-bound assertion whose proof is verified by a trusted adapter."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1] = 1
    user_id: str = Field(min_length=16, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    action_id: str = Field(min_length=16, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    device_id: str = Field(min_length=16, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    issued_at: datetime
    expires_at: datetime
    challenge_nonce: str = Field(min_length=16, max_length=256, pattern=r"^[A-Za-z0-9._~-]+$")
    session_binding_digest: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-fA-F]{64}$")
    authentication_context: Literal["webauthn", "passkey", "hardware_key", "idp_mfa"]

    @field_validator("issued_at", "expires_at")
    @classmethod
    def timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("timezone-aware timestamp required")
        return value.astimezone(timezone.utc)

    @field_validator("session_binding_digest")
    @classmethod
    def canonical_digest(cls, value: str) -> str:
        return value.lower()

    @model_validator(mode="after")
    def validity_window(self):
        if self.expires_at <= self.issued_at:
            raise ValueError("strong re-authentication expiry must be after issuance")
        if (self.expires_at - self.issued_at).total_seconds() > 180:
            raise ValueError("strong re-authentication validity window cannot exceed three minutes")
        return self

    def canonical_payload(self) -> bytes:
        payload = self.model_dump(mode="json")
        return (
            "AURA-SEC-STRONG-REAUTH-V1\n"
            + json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
            + "\n"
        ).encode("utf-8")


@dataclass(frozen=True)
class VerifiedStrongReauthEvidence:
    subject_user_id: str
    action_id: str
    device_id: str
    session_binding_digest: str
    verifier_id: str
    authentication_method: str
    assurance_level: str
    credential_fingerprint: str
    evidence_digest: str


StrongReauthEvidenceVerifier = Callable[
    [StrongReauthAssertion, bytes], VerifiedStrongReauthEvidence | None
]


@dataclass(frozen=True)
class AcceptedStrongReauth:
    acceptance_id: str
    action_id: str
    verifier_id: str
    authentication_method: str
    assurance_level: str
    credential_fingerprint: str
    evidence_digest: str
    verified_at: datetime


class AuraSecStrongReauthGate:
    """Verify and consume strong re-authentication proof exactly once per challenge.

    This gate has no boolean trust shortcut. A trusted adapter must validate the external
    authenticator evidence (for example WebAuthn/passkey/hardware-key/IdP MFA) and return a
    subject/action/device/session-bound result. Canonical assertion digest and challenge nonce
    are persisted before approval so captured proof cannot be silently replayed later.
    """

    def __init__(
        self,
        accounts: AccountStore | None = None,
        security: AuraSecStore | None = None,
    ):
        self.accounts = accounts or AccountStore()
        self.security = security or AuraSecStore(self.accounts)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.accounts.db_path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _init_schema(self) -> None:
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS aura_sec_strong_reauth_acceptance (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    action_id TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    challenge_nonce_hash TEXT NOT NULL UNIQUE,
                    assertion_digest TEXT NOT NULL UNIQUE,
                    verifier_id TEXT NOT NULL,
                    authentication_method TEXT NOT NULL,
                    assurance_level TEXT NOT NULL,
                    credential_fingerprint TEXT NOT NULL,
                    verified_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY(action_id) REFERENCES aura_sec_actions(id) ON DELETE CASCADE,
                    FOREIGN KEY(device_id) REFERENCES aura_sec_devices(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_aura_sec_strong_reauth_action
                ON aura_sec_strong_reauth_acceptance(action_id, verified_at);
                """
            )

    @staticmethod
    def _decode_proof(proof_b64: str) -> bytes:
        try:
            proof = base64.b64decode((proof_b64 or "").strip(), validate=True)
        except Exception as exc:
            raise PermissionError("Aura Sec strong re-authentication proof is not valid base64") from exc
        if not 32 <= len(proof) <= 8192:
            raise PermissionError("Aura Sec strong re-authentication proof length is invalid")
        return proof

    def verify_for_action(
        self,
        user_id: str,
        action_id: str,
        assertion: StrongReauthAssertion,
        *,
        proof_b64: str,
        evidence_verifier: StrongReauthEvidenceVerifier | None,
        expected_session_binding_digest: str | None = None,
        now: datetime | None = None,
    ) -> AcceptedStrongReauth:
        if evidence_verifier is None:
            raise PermissionError("A trusted Aura Sec strong re-authentication verifier is required")
        if not isinstance(assertion, StrongReauthAssertion):
            assertion = StrongReauthAssertion.model_validate(assertion)

        if expected_session_binding_digest is not None:
            expected_session = expected_session_binding_digest.strip().lower()
            if not _HEX_256.fullmatch(expected_session):
                raise PermissionError("A trusted Aura Sec session binding digest is invalid")
            if not secrets.compare_digest(assertion.session_binding_digest, expected_session):
                raise PermissionError("Strong re-authentication proof is bound to a different authenticated session")

        action = self.security.get_action(user_id, action_id)
        if action.get("status") != "proposed":
            raise ValueError("Only proposed Aura Sec actions can receive strong re-authentication")
        if action.get("risk_class") != ActionRisk.STRONG_REAUTH_REQUIRED.value:
            raise ValueError("This Aura Sec action does not require strong re-authentication")

        if not secrets.compare_digest(assertion.user_id, user_id):
            raise PermissionError("Strong re-authentication subject does not match the action owner")
        if not secrets.compare_digest(assertion.action_id, action_id):
            raise PermissionError("Strong re-authentication proof targets a different action")
        if not secrets.compare_digest(assertion.device_id, str(action["device_id"])):
            raise PermissionError("Strong re-authentication proof targets a different device")

        device = self.security.get_device(user_id, action["device_id"])
        if device.get("status") == "revoked" or device.get("revoked_at"):
            raise PermissionError("Revoked Aura Sec device cannot receive strong-re-authenticated actions")

        current = _utc(now)
        if assertion.issued_at > current + timedelta(seconds=60):
            raise PermissionError("Aura Sec strong re-authentication issuance is too far in the future")
        if current >= assertion.expires_at:
            raise PermissionError("Aura Sec strong re-authentication proof has expired")

        proof = self._decode_proof(proof_b64)
        try:
            verified = evidence_verifier(assertion, proof)
        except Exception as exc:
            raise PermissionError("Aura Sec strong re-authentication verifier failed closed") from exc
        if not isinstance(verified, VerifiedStrongReauthEvidence):
            raise PermissionError("Aura Sec strong re-authentication evidence was not verified")

        if not secrets.compare_digest(verified.subject_user_id, user_id):
            raise PermissionError("Verified strong re-authentication subject is incorrect")
        if not secrets.compare_digest(verified.action_id, action_id):
            raise PermissionError("Verified strong re-authentication action binding is incorrect")
        if not secrets.compare_digest(verified.device_id, assertion.device_id):
            raise PermissionError("Verified strong re-authentication device binding is incorrect")
        verified_session = (verified.session_binding_digest or "").strip().lower()
        if not _HEX_256.fullmatch(verified_session) or not secrets.compare_digest(
            verified_session, assertion.session_binding_digest
        ):
            raise PermissionError("Verified strong re-authentication session binding is incorrect")

        verifier_id = (verified.verifier_id or "").strip()
        method = (verified.authentication_method or "").strip().lower()
        assurance = (verified.assurance_level or "").strip().lower()
        fingerprint = (verified.credential_fingerprint or "").strip().lower()
        evidence_digest = (verified.evidence_digest or "").strip().lower()
        if not verifier_id or len(verifier_id) > 160:
            raise PermissionError("Trusted strong re-authentication verifier identity is required")
        if method not in _ALLOWED_METHODS:
            raise PermissionError("Unsupported Aura Sec strong re-authentication method")
        if not secrets.compare_digest(method, assertion.authentication_context):
            raise PermissionError("Verified strong re-authentication method does not match the requested context")
        if assurance not in _ALLOWED_ASSURANCE:
            raise PermissionError("Aura Sec strong re-authentication assurance is insufficient")
        if not _HEX_256.fullmatch(fingerprint):
            raise PermissionError("Aura Sec strong re-authentication credential fingerprint is invalid")

        payload = assertion.canonical_payload()
        expected_digest = hashlib.sha256(payload).hexdigest()
        if not _HEX_256.fullmatch(evidence_digest) or not secrets.compare_digest(
            evidence_digest, expected_digest
        ):
            raise PermissionError("Strong re-authentication evidence digest does not match the assertion")

        acceptance_id = uuid4().hex
        nonce_hash = _hash_nonce(assertion.challenge_nonce)
        with self._connect() as con:
            try:
                con.execute(
                    """INSERT INTO aura_sec_strong_reauth_acceptance
                       (id,user_id,action_id,device_id,challenge_nonce_hash,assertion_digest,
                        verifier_id,authentication_method,assurance_level,credential_fingerprint,verified_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        acceptance_id,
                        user_id,
                        action_id,
                        assertion.device_id,
                        nonce_hash,
                        expected_digest,
                        verifier_id,
                        method,
                        assurance,
                        fingerprint,
                        _iso(current),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise PermissionError("Aura Sec strong re-authentication proof was replayed") from exc

        return AcceptedStrongReauth(
            acceptance_id=acceptance_id,
            action_id=action_id,
            verifier_id=verifier_id,
            authentication_method=method,
            assurance_level=assurance,
            credential_fingerprint=fingerprint,
            evidence_digest=expected_digest,
            verified_at=current,
        )


__all__ = [
    "AcceptedStrongReauth",
    "AuraSecStrongReauthGate",
    "StrongReauthAssertion",
    "StrongReauthEvidenceVerifier",
    "VerifiedStrongReauthEvidence",
]
