from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from .assets import AssetLibrary
from .revisions import create_revision
from .rights import RightsLedger, authorize_voice_profile
from .session import Clip, StudioSession
from .voice import convert_singing_voice


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class VoiceConversionCandidate(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    source_asset_id: str
    voice_profile_id: str
    candidate_path: str
    candidate_sha256: str
    similarity: float = Field(ge=0.0, le=1.0)
    pitch_shift: int = Field(ge=-24, le=24)
    state: Literal["ready", "committed"] = "ready"
    created_at: str = Field(default_factory=_now)
    committed_at: str | None = None
    committed_asset_id: str | None = None
    committed_track_id: str | None = None
    committed_clip_id: str | None = None
    pre_commit_revision_id: str | None = None
    metadata: dict = Field(default_factory=dict)


def _root(project: Path) -> Path:
    root = (project / "work" / "voice_conversion").resolve()
    project_root = project.resolve()
    if project_root not in root.parents:
        raise ValueError("Invalid voice conversion work root")
    root.mkdir(parents=True, exist_ok=True)
    return root


def _index(project: Path) -> Path:
    return _root(project) / "candidates.json"


def _read(project: Path) -> list[VoiceConversionCandidate]:
    path = _index(project)
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            return []
        return [VoiceConversionCandidate.model_validate(item) for item in raw if isinstance(item, dict)]
    except Exception:
        return []


def _write(project: Path, rows: list[VoiceConversionCandidate]) -> None:
    path = _index(project)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps([item.model_dump(mode="json") for item in rows], indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _save(project: Path, candidate: VoiceConversionCandidate) -> VoiceConversionCandidate:
    rows = [item for item in _read(project) if item.id != candidate.id]
    rows.append(candidate)
    _write(project, rows)
    return candidate


def get_candidate(project: Path, candidate_id: str) -> VoiceConversionCandidate:
    if not candidate_id or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for ch in candidate_id):
        raise ValueError("Invalid voice conversion candidate id")
    for item in _read(project):
        if item.id == candidate_id:
            return item
    raise KeyError(candidate_id)


def _candidate_file(project: Path, candidate: VoiceConversionCandidate) -> Path:
    root = project.resolve()
    path = (project / candidate.candidate_path).resolve()
    if root not in path.parents or not path.is_file():
        raise FileNotFoundError(candidate.candidate_path)
    return path


def generate_voice_conversion_candidate(
    project: Path,
    *,
    source_asset_id: str,
    voice_profile_id: str,
    similarity: float,
    pitch_shift: int,
) -> VoiceConversionCandidate:
    """Generate a private conversion candidate without mutating the authoritative DAW session."""
    project = project.resolve()
    library = AssetLibrary(project)
    source = library.get(source_asset_id)
    if source.kind != "audio":
        raise ValueError("Voice conversion requires an audio source asset")
    source_path = (project / source.path).resolve()
    if project not in source_path.parents or not source_path.is_file():
        raise FileNotFoundError(source.path)

    candidate_id = uuid4().hex
    audio_dir = _root(project) / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    output = audio_dir / f"{candidate_id}.wav"
    rights_root = project / ".aura_rights"
    path = convert_singing_voice(
        source_path,
        output,
        rights_root=rights_root,
        voice_profile_id=voice_profile_id,
        similarity=similarity,
        pitch_shift=pitch_shift,
    )
    resolved = Path(path).resolve()
    if resolved != output.resolve() or not resolved.is_file():
        raise RuntimeError("Voice conversion runtime produced an unexpected candidate path")

    digest = RightsLedger.sha256(resolved)
    candidate = VoiceConversionCandidate(
        id=candidate_id,
        source_asset_id=source_asset_id,
        voice_profile_id=voice_profile_id,
        candidate_path=str(resolved.relative_to(project)),
        candidate_sha256=digest,
        similarity=max(0.0, min(float(similarity), 1.0)),
        pitch_shift=max(-24, min(int(pitch_shift), 24)),
        metadata={
            "audition_required": True,
            "authoritative_daw_mutated": False,
            "consent_checked_at_generation": True,
            "audio_origin": "consent_gated_voice_conversion_candidate",
        },
    )
    return _save(project, candidate)


def commit_voice_conversion_candidate(
    project: Path,
    *,
    candidate_id: str,
    start_seconds: float = 0.0,
    target_track_id: str | None = None,
    track_name: str | None = None,
) -> VoiceConversionCandidate:
    """Re-authorize consent and commit an auditioned candidate as a real editable DAW asset."""
    project = project.resolve()
    candidate = get_candidate(project, candidate_id)
    if candidate.state != "ready":
        raise RuntimeError("Voice conversion candidate is already committed")

    # Consent/tenant authority can change after generation. Re-check immediately before the
    # project mutation so revocation or cross-tenant use fails closed.
    authorize_voice_profile(project / ".aura_rights", candidate.voice_profile_id, "voice_conversion")

    source = _candidate_file(project, candidate)
    if RightsLedger.sha256(source) != candidate.candidate_sha256:
        raise RuntimeError("Voice conversion candidate changed after generation; regenerate it")

    session_path = project / "aura_session.json"
    if not session_path.is_file():
        raise RuntimeError("A DAW session is required before committing a voice conversion")
    session = StudioSession.load(session_path)

    revision = create_revision(
        project,
        label=f"Before voice conversion commit {candidate.id[:8]}",
        reason="voice_conversion_commit",
        actor="Rhiannon",
        keep=200,
    )

    library = AssetLibrary(project)
    asset = library.ingest(
        source,
        kind="audio",
        rights_basis="generated_from_authorised_voice_conversion",
        attestation=(
            "This generated asset was created from a project source recording and a consent-gated "
            "Voice Profile. Source-performance rights and Voice Profile authority remain separately governed."
        ),
        tags=["voice_conversion", "generated_vocal", f"voice_profile:{candidate.voice_profile_id}"],
        notes=f"Committed from voice conversion candidate {candidate.id} after consent re-check.",
    )

    if target_track_id:
        track = session.find_track(target_track_id)
    else:
        track = session.add_track(track_name or "Converted Vocal", role="vocals")
    if track.role not in {"vocals", "backing_vocals", "other"}:
        raise ValueError("Voice conversion can only be committed to a vocal-compatible DAW track")

    duration = float(asset.analysis.get("duration_seconds") or 0.0)
    if duration <= 0.0:
        raise RuntimeError("Committed voice conversion asset has no valid duration")
    take_lane = max((clip.take_lane for clip in track.clips), default=-1) + 1
    clip = Clip(
        name=track_name or f"Converted Vocal — {candidate.id[:8]}",
        kind="audio",
        source=asset.path,
        start=max(0.0, float(start_seconds)),
        duration=duration,
        take_lane=take_lane,
        metadata={
            "real_audio": True,
            "committed": True,
            "generated": True,
            "voice_conversion": True,
            "voice_conversion_candidate_id": candidate.id,
            "source_asset_id": candidate.source_asset_id,
            "voice_profile_id": candidate.voice_profile_id,
            "consent_rechecked_at_commit": True,
            "asset_id": asset.id,
            "provenance": "consent_gated_voice_conversion",
        },
    )
    track.clips.append(clip)
    session.generation_history.append(
        {
            "action": "voice_conversion_commit",
            "candidate_id": candidate.id,
            "source_asset_id": candidate.source_asset_id,
            "voice_profile_id": candidate.voice_profile_id,
            "asset_id": asset.id,
            "track_id": track.id,
            "clip_id": clip.id,
            "pre_commit_revision_id": revision["id"],
            "consent_rechecked_at_commit": True,
        }
    )
    session.save(session_path)

    candidate.state = "committed"
    candidate.committed_at = _now()
    candidate.committed_asset_id = asset.id
    candidate.committed_track_id = track.id
    candidate.committed_clip_id = clip.id
    candidate.pre_commit_revision_id = str(revision["id"])
    candidate.metadata = {
        **candidate.metadata,
        "authoritative_daw_mutated": True,
        "consent_rechecked_at_commit": True,
        "committed_asset_path": asset.path,
    }
    return _save(project, candidate)


__all__ = [
    "VoiceConversionCandidate",
    "commit_voice_conversion_candidate",
    "generate_voice_conversion_candidate",
    "get_candidate",
]
