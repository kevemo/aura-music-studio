from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator


class RightsRecord(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    asset_name: str
    asset_sha256: str | None = None
    rights_basis: str
    user_attestation: str
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class VoiceProfile(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    name: str
    owner_label: str
    reference_files: list[str] = Field(default_factory=list)
    consent_confirmed: bool = False
    consent_statement: str = ""
    verification_phrase: str | None = None
    verification_recording: str | None = None
    allowed_uses: list[str] = Field(default_factory=lambda: ["singing", "backing_harmony", "voice_conversion"])
    similarity_limit: float = Field(default=1.0, ge=0.0, le=1.0)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_consent_text(self):
        if self.consent_confirmed and len(self.consent_statement.strip()) < 10:
            raise ValueError("A meaningful consent statement is required before enabling a Voice Profile.")
        return self

    def assert_usable(self, purpose: str) -> None:
        if not self.consent_confirmed:
            raise PermissionError("Voice Profile is locked until consent is confirmed.")
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
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
