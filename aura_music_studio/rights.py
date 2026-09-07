from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator

from .request_context import current_user_id


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RightsRecord(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    asset_name: str
    asset_sha256: str | None = None
    rights_basis: str
    user_attestation: str
    created_at: str = Field(default_factory=_now)


VoiceSubjectRelationship = Literal["self", "other_authorized_person", "legacy_unspecified"]


class VoiceProfile(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    name: str
    owner_label: str
    reference_files: list[str] = Field(default_factory=list)
    consent_confirmed: bool = False
    consent_statement: str = ""
    consent_recorded_at: str | None = None
    subject_relationship: VoiceSubjectRelationship = "legacy_unspecified"
    created_by_user_id: str | None = None
    tenant_user_id: str | None = None
    verification_phrase: str | None = None
    verification_recording: str | None = None
    verification_state: Literal["unverified", "attested", "verified", "failed", "revoked"] = "unverified"
    verification_method: str | None = None
    verification_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    allowed_uses: list[str] = Field(default_factory=lambda: ["singing", "backing_harmony", "voice_conversion"])
    similarity_limit: float = Field(default=0.8, ge=0.0, le=1.0)
    version: int = Field(default=1, ge=1)
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)
    last_used_at: str | None = None
    revoked_at: str | None = None
    revoked_reason: str | None = None
    metadata: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_consent_text(self):
        if self.consent_confirmed and len(self.consent_statement.strip()) < 10:
            raise ValueError("A meaningful consent statement is required before enabling a Voice Profile.")
        if self.consent_confirmed and not self.consent_recorded_at:
            # Backwards-compatible migration for profiles created before explicit consent timestamps.
            object.__setattr__(self, "consent_recorded_at", self.created_at)
        if self.subject_relationship == "legacy_unspecified" and self.metadata.get("challenge_id"):
            # The current Voice House challenge explicitly instructs the submitting subject to
            # record themselves. Older challenge-created rows predate this typed field.
            object.__setattr__(self, "subject_relationship", "self")
        if self.revoked_at:
            object.__setattr__(self, "verification_state", "revoked")
            object.__setattr__(self, "consent_confirmed", False)
            return self
        # Backwards-compatible migration: older profiles stored a meaningful signed/typed
        # consent statement but predate the explicit verification_state field. Treat that
        # evidence as an attestation, not biometric/high-confidence verification.
        if self.consent_confirmed and self.verification_state == "unverified":
            object.__setattr__(self, "verification_state", "attested")
            if not self.verification_method:
                object.__setattr__(self, "verification_method", "consent_statement_attestation")
        if self.verification_state == "verified" and not self.consent_confirmed:
            raise ValueError("A Voice Profile cannot be verified before consent is confirmed.")
        # A transcript match proves only that the challenge phrase was spoken. It does not
        # prove the speaker is the same person as every reference file. High-confidence
        # `verified` state is reserved for a method that also compares speaker identity or
        # an explicit trusted owner-review workflow.
        verified_methods = {"speaker_verification_plus_phrase_match", "trusted_owner_review"}
        if self.verification_state == "verified" and self.verification_method not in verified_methods:
            object.__setattr__(self, "verification_state", "attested")
            if self.verification_method:
                self.metadata.setdefault("phrase_verification_method", self.verification_method)
        if self.verification_state != "verified" and self.similarity_limit > 0.8:
            object.__setattr__(self, "similarity_limit", 0.8)
        return self

    @property
    def active(self) -> bool:
        return bool(self.consent_confirmed and not self.revoked_at and self.verification_state != "revoked")

    def assert_tenant(self, user_id: str | None) -> None:
        """Reject cross-user use when the profile carries an authoritative tenant binding."""
        if self.tenant_user_id and user_id and self.tenant_user_id != user_id:
            raise PermissionError("Voice Profile belongs to another tenant and cannot be accessed.")

    def assert_usable(self, purpose: str) -> None:
        if self.revoked_at or self.verification_state == "revoked":
            raise PermissionError("Voice Profile consent has been revoked and the profile is disabled.")
        if not self.consent_confirmed:
            raise PermissionError("Voice Profile is locked until consent is confirmed.")
        if self.verification_state not in {"attested", "verified"}:
            raise PermissionError("Voice Profile requires a valid consent/verification state before use.")
        if purpose not in self.allowed_uses:
            raise PermissionError(f"Voice Profile is not approved for {purpose!r}.")


class RightsLedger:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.rights_path = self.root / "rights.json"
        self.voices_path = self.root / "voices.json"

    @staticmethod
    def sha256(path: Path) -> str:
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()

    def add_rights_record(self, record: RightsRecord) -> RightsRecord:
        data = self._read_list(self.rights_path)
        data.append(record.model_dump())
        self._write_list(self.rights_path, data)
        return record

    def save_voice(self, profile: VoiceProfile) -> VoiceProfile:
        user_id = current_user_id()
        if user_id:
            if profile.tenant_user_id and profile.tenant_user_id != user_id:
                raise PermissionError("Voice Profile belongs to another tenant and cannot be modified.")
            if not profile.tenant_user_id:
                profile.tenant_user_id = user_id
            if not profile.created_by_user_id:
                profile.created_by_user_id = user_id
        profile.updated_at = _now()
        data = self._read_list(self.voices_path)
        data = [x for x in data if x.get("id") != profile.id]
        data.append(profile.model_dump())
        self._write_list(self.voices_path, data)
        return profile

    def get_voice(self, profile_id: str) -> VoiceProfile:
        for item in self._read_list(self.voices_path):
            if item.get("id") == profile_id:
                return VoiceProfile.model_validate(item)
        raise KeyError(profile_id)

    def list_voices(self) -> list[VoiceProfile]:
        return [VoiceProfile.model_validate(x) for x in self._read_list(self.voices_path)]

    def rename_voice(self, profile_id: str, name: str) -> VoiceProfile:
        profile = self.get_voice(profile_id)
        cleaned = (name or "").strip()
        if not cleaned:
            raise ValueError("Voice Profile name cannot be empty")
        profile.name = cleaned[:120]
        profile.version += 1
        return self.save_voice(profile)

    def revoke_voice(self, profile_id: str, reason: str = "Consent withdrawn") -> VoiceProfile:
        profile = self.get_voice(profile_id)
        if not profile.revoked_at:
            profile.revoked_at = _now()
        profile.revoked_reason = (reason or "Consent withdrawn").strip()[:1000]
        profile.verification_state = "revoked"
        profile.consent_confirmed = False
        profile.version += 1
        return self.save_voice(profile)

    def mark_voice_used(self, profile_id: str) -> VoiceProfile:
        profile = self.get_voice(profile_id)
        profile.last_used_at = _now()
        return self.save_voice(profile)

    def delete_voice(self, profile_id: str) -> VoiceProfile:
        """Remove profile metadata from the active ledger and return the deleted record.

        Callers that own reference/model artefacts must delete those separately before reporting
        complete erasure. This method intentionally does not accept arbitrary filesystem paths.
        """
        profile = self.get_voice(profile_id)
        profile.assert_tenant(current_user_id())
        data = [x for x in self._read_list(self.voices_path) if x.get("id") != profile_id]
        self._write_list(self.voices_path, data)
        return profile

    @staticmethod
    def _read_list(path: Path) -> list[dict]:
        if not path.exists():
            return []
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, list) else []
        except Exception:
            return []

    @staticmethod
    def _write_list(path: Path, data: list[dict]) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        tmp.replace(path)


def authorize_voice_profile(rights_root: Path, profile_id: str, purpose: str) -> VoiceProfile:
    """Reload and authorize a Voice Profile at the final execution boundary.

    Callers must pass the authoritative project `.aura_rights` directory rather than
    relying on a profile object serialized or loaded earlier in a queued workflow.
    """
    ledger = RightsLedger(Path(rights_root))
    try:
        profile = ledger.get_voice(profile_id)
    except KeyError as exc:
        raise PermissionError("Voice Profile is unavailable and cannot be used.") from exc
    profile.assert_tenant(current_user_id())
    profile.assert_usable(purpose)
    return ledger.mark_voice_used(profile.id)
