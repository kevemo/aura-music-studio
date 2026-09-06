from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .tenant_storage import project_path

router = APIRouter(tags=["Rhiannon Voice References"])


class HistoricalVoiceReference(BaseModel):
    """Non-cloning reference metadata recovered from the legacy Aura archive.

    This is deliberately not a VoiceProfile and contains no model/embedding/reference-audio
    filesystem path. Identity-replicating use requires a separately created canonical consent-
    gated Voice Profile and must never be inferred from this registry record.
    """

    id: str = Field(min_length=1, max_length=120)
    display_name: str = Field(min_length=1, max_length=160)
    source_asset_id: str = Field(min_length=1, max_length=160)
    source_file_name: str = Field(min_length=1, max_length=200)
    source_collection: str = Field(min_length=1, max_length=240)
    recovered_date: str
    duration_seconds: float = Field(gt=0.0, le=3600.0)
    channels: int = Field(ge=1, le=32)
    sample_rate_hz: int = Field(ge=8000, le=384000)
    container: str = Field(min_length=1, max_length=32)
    approximate_bitrate_kbps: int | None = Field(default=None, ge=1, le=10000)
    provenance_state: Literal["documented_deep_scan"] = "documented_deep_scan"
    rights_status: Literal["not_established_for_identity_replication"] = "not_established_for_identity_replication"
    consent_status: Literal["not_established_for_identity_replication"] = "not_established_for_identity_replication"
    training_eligible: Literal[False] = False
    identity_replication_allowed: Literal[False] = False
    private: Literal[True] = True
    raw_audio_exposed: Literal[False] = False
    completed_voice_profile: Literal[False] = False
    allowed_reference_uses: list[str] = Field(default_factory=list, max_length=16)
    notes: str = Field(default="", max_length=600)


LEGACY_RHIANNON_AURA_VOICE_REFERENCE = HistoricalVoiceReference(
    id="legacy-rhiannon-aura-voice-preview-20260906",
    display_name="Legacy Rhiannon/Aura Voice Reference",
    source_asset_id="drive:1Pr6aDmyqHmnQv2_9QT14_wbPhkLMmt7T",
    source_file_name="Rhiannon_Legacy_Aura_Voice_Preview_REFERENCE.mp3",
    source_collection="2026-09-06 — Legacy Aura → Rhiannon Source Resources & ZIP Deep Scan",
    recovered_date="2026-09-06",
    duration_seconds=20.323,
    channels=1,
    sample_rate_hz=44100,
    container="mp3",
    approximate_bitrate_kbps=192,
    allowed_reference_uses=[
        "voice_character_analysis",
        "historical_identity_comparison",
        "voice_quality_target_analysis",
        "pronunciation_style_analysis",
        "rhiannon_presentation_planning",
        "timing_viseme_testing_where_lawful",
    ],
    notes=(
        "Historical reference only. Do not use for training, adaptation, cloning or other "
        "identity-replicating processing unless ownership, consent, provenance, rights and "
        "permitted use are established through the canonical Voice House consent workflow."
    ),
)


def historical_voice_references() -> list[HistoricalVoiceReference]:
    return [LEGACY_RHIANNON_AURA_VOICE_REFERENCE.model_copy(deep=True)]


@router.get("/projects/{project_name}/voice-house/historical-references")
def list_historical_voice_references(project_name: str):
    """Return private non-training reference metadata for an authenticated tenant project."""
    try:
        project_path(project_name, must_exist=True)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, "Project not found") from exc
    return {
        "references": [item.model_dump(mode="json") for item in historical_voice_references()],
        "reference_only": True,
        "identity_replication_requires_separate_voice_profile": True,
        "raw_audio_exposed": False,
    }
