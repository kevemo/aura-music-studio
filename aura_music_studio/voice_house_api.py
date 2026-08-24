from __future__ import annotations

import json
import re
import secrets
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from .rights import RightsLedger, RightsRecord, VoiceProfile
from .speech import AuraSpeechService
from .tenant_storage import project_path
from .voice import analyze_voice_sample

router = APIRouter(tags=["Voice House"])

_ALLOWED_AUDIO = {".wav", ".flac", ".mp3", ".m4a", ".ogg", ".aac", ".webm"}
_ALLOWED_USES = {"singing", "backing_harmony", "voice_conversion", "speech", "dubbing"}
_CHALLENGE_WORDS = [
    "aurora", "cosmos", "frequency", "lantern", "harmony", "violet", "river", "silver",
    "studio", "pulsar", "melody", "ember", "orbit", "canvas", "signal", "starlight",
]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).isoformat()


def _project(name: str) -> Path:
    try:
        return project_path(name, must_exist=True)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, "Project not found") from exc


def _challenge_path(project: Path) -> Path:
    root = project / ".aura_rights"
    root.mkdir(parents=True, exist_ok=True)
    return root / "voice_challenges.json"


def _read_challenges(project: Path) -> list[dict]:
    path = _challenge_path(project)
    if not path.is_file():
        return []
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, list) else []
    except Exception:
        return []


def _write_challenges(project: Path, rows: list[dict]) -> None:
    path = _challenge_path(project)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(rows[-100:], indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def _normalize(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (text or "").lower()))


def _verification_score(expected: str, actual: str) -> float:
    a = _normalize(expected)
    b = _normalize(actual)
    if not a or not b:
        return 0.0
    sequence = SequenceMatcher(None, a, b).ratio()
    expected_words = set(a.split())
    actual_words = set(b.split())
    overlap = len(expected_words & actual_words) / max(1, len(expected_words))
    return max(0.0, min(1.0, sequence * 0.65 + overlap * 0.35))


def _challenge(project: Path, challenge_id: str) -> tuple[list[dict], dict]:
    rows = _read_challenges(project)
    row = next((item for item in rows if item.get("id") == challenge_id), None)
    if not row:
        raise ValueError("Voice verification challenge not found")
    if row.get("used_at"):
        raise ValueError("Voice verification challenge has already been used")
    if str(row.get("expires_at") or "") <= _iso():
        raise ValueError("Voice verification challenge has expired")
    return rows, row


async def _save_upload(file: UploadFile, target: Path, *, maximum_mb: int = 100) -> int:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in _ALLOWED_AUDIO:
        raise HTTPException(415, "Voice samples must be WAV, FLAC, MP3, M4A, OGG, AAC or WebM audio")
    target = target.with_suffix(suffix)
    target.parent.mkdir(parents=True, exist_ok=True)
    maximum = maximum_mb * 1024 * 1024
    size = 0
    try:
        with target.open("wb") as handle:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > maximum:
                    raise HTTPException(413, f"Voice sample exceeds the {maximum_mb} MB limit")
                handle.write(chunk)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    finally:
        await file.close()
    return size


class RevokeVoiceRequest(BaseModel):
    reason: str = Field(default="Consent withdrawn", max_length=1000)


@router.post("/projects/{project_name}/voice-house/challenge")
def create_voice_challenge(project_name: str):
    project = _project(project_name)
    words = [secrets.choice(_CHALLENGE_WORDS) for _ in range(6)]
    nonce = secrets.randbelow(9000) + 1000
    phrase = "Aura voice consent " + " ".join(words) + f" {nonce}"
    now = _now()
    row = {
        "id": uuid4().hex,
        "phrase": phrase,
        "created_at": _iso(now),
        "expires_at": _iso(now + timedelta(minutes=30)),
        "used_at": None,
    }
    rows = [item for item in _read_challenges(project) if str(item.get("expires_at") or "") > _iso(now - timedelta(days=1))]
    rows.append(row)
    _write_challenges(project, rows)
    return {
        "challenge_id": row["id"],
        "phrase": phrase,
        "expires_at": row["expires_at"],
        "instruction": "Record yourself clearly reading this exact phrase. The recording is retained with the voice profile as consent evidence.",
    }


@router.post("/projects/{project_name}/voice-house/profiles")
async def create_voice_house_profile(
    project_name: str,
    challenge_id: str = Form(...),
    name: str = Form(...),
    owner_label: str = Form(...),
    consent_statement: str = Form(...),
    allowed_uses: str = Form("singing,backing_harmony,voice_conversion"),
    similarity_limit: float = Form(0.8),
    consent_confirmed: bool = Form(...),
    verification_recording: UploadFile = File(...),
    reference_files: list[UploadFile] = File(...),
):
    if not consent_confirmed:
        raise HTTPException(400, "Voice Profile creation requires explicit consent confirmation")
    statement = (consent_statement or "").strip()
    if len(statement) < 20:
        raise HTTPException(400, "Provide a meaningful voice consent statement of at least 20 characters")
    if not reference_files:
        raise HTTPException(400, "Upload at least one clean voice reference sample")
    uses = [value.strip().lower() for value in allowed_uses.split(",") if value.strip()]
    if not uses or any(value not in _ALLOWED_USES for value in uses):
        raise HTTPException(400, f"Allowed uses must come from {sorted(_ALLOWED_USES)}")

    project = _project(project_name)
    try:
        challenge_rows, challenge = _challenge(project, challenge_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc

    profile_id = uuid4().hex
    sample_root = project / "input" / "voice_profiles" / profile_id
    saved_refs: list[Path] = []
    evidence: Path | None = None
    ledger = RightsLedger(project / ".aura_rights")
    try:
        for index, upload in enumerate(reference_files, 1):
            suffix = Path(upload.filename or "sample.wav").suffix.lower()
            target = sample_root / f"reference_{index:02d}{suffix}"
            await _save_upload(upload, target.with_suffix(""))
            # _save_upload restores the source suffix after with_suffix("").
            saved = target
            if not saved.is_file():
                candidates = list(sample_root.glob(f"reference_{index:02d}.*"))
                if not candidates:
                    raise RuntimeError("Voice reference upload was not saved")
                saved = candidates[0]
            saved_refs.append(saved.resolve())
            ledger.add_rights_record(
                RightsRecord(
                    asset_name=Path(upload.filename or saved.name).name,
                    asset_sha256=ledger.sha256(saved),
                    rights_basis="voice_owner_consent_or_authorized_voice_license",
                    user_attestation=statement,
                )
            )

        evidence_suffix = Path(verification_recording.filename or "verification.wav").suffix.lower()
        evidence_target = sample_root / f"consent_verification{evidence_suffix}"
        await _save_upload(verification_recording, evidence_target.with_suffix(""))
        evidence = evidence_target
        if not evidence.is_file():
            candidates = list(sample_root.glob("consent_verification.*"))
            if not candidates:
                raise RuntimeError("Verification recording was not saved")
            evidence = candidates[0]
        ledger.add_rights_record(
            RightsRecord(
                asset_name=Path(verification_recording.filename or evidence.name).name,
                asset_sha256=ledger.sha256(evidence),
                rights_basis="voice_consent_verification_recording",
                user_attestation=statement,
            )
        )

        verification_state = "attested"
        verification_method = "recorded_phrase_plus_consent_attestation"
        verification_confidence = None
        transcript = None
        try:
            transcript = AuraSpeechService().transcribe(evidence)
            verification_confidence = _verification_score(challenge["phrase"], transcript)
            if verification_confidence >= 0.86:
                verification_state = "verified"
                verification_method = "local_stt_phrase_match"
            else:
                verification_state = "failed"
                verification_method = "local_stt_phrase_mismatch"
        except RuntimeError:
            # No STT engine configured: retain the recording and typed consent as evidence,
            # but never mislabel it as biometric/high-confidence verification.
            verification_state = "attested"

        requested_similarity = max(0.0, min(1.0, float(similarity_limit)))
        effective_similarity = requested_similarity if verification_state == "verified" else min(requested_similarity, 0.8)
        analysis = {str(path): analyze_voice_sample(path) for path in saved_refs}
        profile = VoiceProfile(
            id=profile_id,
            name=(name or "Voice Profile").strip()[:120],
            owner_label=(owner_label or "Voice owner").strip()[:160],
            reference_files=[str(path) for path in saved_refs],
            consent_confirmed=True,
            consent_statement=statement,
            verification_phrase=challenge["phrase"],
            verification_recording=str(evidence.resolve()),
            verification_state=verification_state,
            verification_method=verification_method,
            verification_confidence=verification_confidence,
            allowed_uses=uses,
            similarity_limit=effective_similarity,
            metadata={
                "voice_scan": analysis,
                "challenge_id": challenge_id,
                "verification_transcript": transcript,
                "requested_similarity_limit": requested_similarity,
                "reference_count": len(saved_refs),
            },
        )
        ledger.save_voice(profile)
        challenge["used_at"] = _iso()
        challenge["profile_id"] = profile.id
        _write_challenges(project, challenge_rows)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(422, f"Unable to create Voice Profile: {type(exc).__name__}: {exc}") from exc

    return {
        "voice_profile": {
            "id": profile.id,
            "name": profile.name,
            "owner_label": profile.owner_label,
            "verification_state": profile.verification_state,
            "verification_method": profile.verification_method,
            "verification_confidence": profile.verification_confidence,
            "allowed_uses": profile.allowed_uses,
            "similarity_limit": profile.similarity_limit,
            "active": profile.active,
        },
        "consent_recorded": True,
        "raw_reference_paths_exposed": False,
        "detail": (
            "Voice ownership phrase verified through the configured local STT engine."
            if profile.verification_state == "verified"
            else "Consent evidence is stored as an attestation. Configure local STT and re-verification for high-confidence phrase matching."
        ),
    }


@router.post("/projects/{project_name}/voice-house/profiles/{profile_id}/revoke")
def revoke_voice_house_profile(project_name: str, profile_id: str, request: RevokeVoiceRequest):
    project = _project(project_name)
    ledger = RightsLedger(project / ".aura_rights")
    try:
        profile = ledger.revoke_voice(profile_id, request.reason)
    except KeyError as exc:
        raise HTTPException(404, "Voice Profile not found") from exc
    return {
        "id": profile.id,
        "verification_state": profile.verification_state,
        "active": profile.active,
        "revoked_at": profile.revoked_at,
        "detail": "Voice Profile revoked. Downstream singing/conversion/harmony calls now fail closed for this profile.",
    }


@router.get("/projects/{project_name}/voice-house/profiles")
def voice_house_profiles(project_name: str):
    project = _project(project_name)
    ledger = RightsLedger(project / ".aura_rights")
    return {
        "profiles": [
            {
                "id": profile.id,
                "name": profile.name,
                "owner_label": profile.owner_label,
                "verification_state": profile.verification_state,
                "verification_method": profile.verification_method,
                "verification_confidence": profile.verification_confidence,
                "allowed_uses": profile.allowed_uses,
                "similarity_limit": profile.similarity_limit,
                "active": profile.active,
                "created_at": profile.created_at,
                "updated_at": profile.updated_at,
                "revoked_at": profile.revoked_at,
            }
            for profile in ledger.list_voices()
        ],
        "raw_reference_paths_exposed": False,
    }
