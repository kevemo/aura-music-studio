from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .assets import AssetLibrary
from .harmony import HarmonySpec, audio_vocal_to_midi, generate_harmony_midis, render_harmony_voice
from .layers import generate_complementary_layer
from .project import ProjectWorkspace
from .rights import RightsLedger
from .tenant_storage import project_path
from .voice import convert_singing_voice

router = APIRouter(tags=["Aura Vocals & Harmony"])


class VoiceConvertRequest(BaseModel):
    source_asset_id: str
    voice_profile_id: str
    similarity: float = Field(default=0.8, ge=0.0, le=1.0)
    pitch_shift: int = Field(default=0, ge=-24, le=24)


class HarmonyRequest(BaseModel):
    source_asset_id: str
    mode: str = "contextual"  # contextual | scored
    key: str = "C Major"
    voices: list[str] = Field(default_factory=lambda: ["third_above", "third_below"])
    voice_profile_id: str | None = None
    prompt: str = "Natural supportive backing harmonies, blended behind the lead vocal."


def _project(name: str) -> Path:
    try:
        return project_path(name, must_exist=True)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, "Project not found") from exc


def _audio_asset(project: Path, asset_id: str):
    library = AssetLibrary(project)
    try:
        asset = library.get(asset_id)
    except KeyError as exc:
        raise HTTPException(404, "Audio asset not found") from exc
    if asset.kind != "audio":
        raise HTTPException(400, "This operation requires an audio asset")
    return asset


@router.get("/projects/{project_name}/voices")
def list_voice_profiles(project_name: str):
    project = _project(project_name)
    ledger = RightsLedger(project / ".aura_rights")
    # Do not expose raw reference file locations to the browser.
    return [
        {
            "id": profile.id,
            "name": profile.name,
            "owner_label": profile.owner_label,
            "consent_confirmed": profile.consent_confirmed,
            "allowed_uses": profile.allowed_uses,
            "similarity_limit": profile.similarity_limit,
            "created_at": profile.created_at,
        }
        for profile in ledger.list_voices()
    ]


@router.post("/projects/{project_name}/voice-convert")
def voice_convert(project_name: str, request: VoiceConvertRequest):
    project = _project(project_name)
    source = _audio_asset(project, request.source_asset_id)
    rights_root = project / ".aura_rights"
    ledger = RightsLedger(rights_root)
    try:
        profile = ledger.get_voice(request.voice_profile_id)
    except KeyError as exc:
        raise HTTPException(404, "Aura Voice Profile not found") from exc
    try:
        profile.assert_usable("voice_conversion")
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc

    output = project / "output" / "voices" / f"{Path(source.name).stem}_{profile.id[:8]}_converted.wav"
    try:
        path = convert_singing_voice(
            project / source.path,
            output,
            rights_root=rights_root,
            voice_profile_id=profile.id,
            similarity=request.similarity,
            pitch_shift=request.pitch_shift,
        )
    except PermissionError as exc:
        # Consent may be withdrawn after request admission but before synthesis begins.
        raise HTTPException(403, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(503, f"Voice conversion unavailable: {type(exc).__name__}: {exc}") from exc
    return {
        "path": str(path),
        "voice_profile_id": profile.id,
        "voice_owner": profile.owner_label,
        "consent_checked": True,
        "consent_checked_at_execution": True,
        "audio_origin": "consent_gated_voice_conversion",
    }


@router.post("/projects/{project_name}/harmonies")
def harmonies(project_name: str, request: HarmonyRequest):
    project = _project(project_name)
    source = _audio_asset(project, request.source_asset_id)
    source_path = project / source.path
    mode = request.mode.strip().lower()

    if mode == "contextual":
        workspace = ProjectWorkspace(project)
        try:
            output = generate_complementary_layer(
                source_path,
                workspace,
                track="backing_vocals",
                prompt=(
                    request.prompt
                    + " Use tasteful thirds, fifths and octave support appropriate to the harmony. "
                    + f"Target tonal centre: {request.key}. Keep the lead vocal space clear."
                ),
            )
        except Exception as exc:
            raise HTTPException(503, f"Contextual harmony generation unavailable: {type(exc).__name__}: {exc}") from exc
        return {
            "mode": "contextual",
            "stems": {"backing_vocals": str(output)},
            "audio_origin": "neural_real_audio",
        }

    if mode != "scored":
        raise HTTPException(400, "Harmony mode must be contextual or scored")

    lyrics = project / "input" / "lyrics.txt"
    if not lyrics.exists():
        raise HTTPException(400, "Scored harmony rendering requires project lyrics in input/lyrics.txt")

    rights_root = project / ".aura_rights"
    if request.voice_profile_id:
        ledger = RightsLedger(rights_root)
        try:
            profile = ledger.get_voice(request.voice_profile_id)
            profile.assert_usable("backing_harmony")
        except KeyError as exc:
            raise HTTPException(404, "Aura Voice Profile not found") from exc
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc

    control_dir = project / "work" / "harmony_control"
    lead_midi = control_dir / "lead_scan.mid"
    try:
        audio_vocal_to_midi(source_path, lead_midi)
        midi_parts = generate_harmony_midis(
            lead_midi,
            control_dir,
            HarmonySpec(key=request.key, voices=tuple(request.voices)),
        )
        stems = {}
        for role, midi in midi_parts.items():
            target = project / "output" / "harmonies" / f"harmony_{role}.wav"
            stems[role] = str(
                render_harmony_voice(
                    midi,
                    lyrics,
                    target,
                    rights_root=rights_root if request.voice_profile_id else None,
                    voice_profile_id=request.voice_profile_id,
                )
            )
    except PermissionError as exc:
        # Re-check each synthesis invocation so revoke-after-admission fails closed.
        raise HTTPException(403, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(503, f"Scored harmony rendering unavailable: {type(exc).__name__}: {exc}") from exc

    return {
        "mode": "scored",
        "stems": stems,
        "control_midi": str(lead_midi),
        "control_midi_is_final_audio": False,
        "audio_origin": "rendered_singing_synthesis",
        "consent_checked": bool(request.voice_profile_id),
        "consent_checked_at_execution": bool(request.voice_profile_id),
    }
