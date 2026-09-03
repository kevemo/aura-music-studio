from __future__ import annotations

import os
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from .assets import AssetLibrary
from .build_around_release import (
    BuildAroundRequest,
    build_around_upload,
    install_release_gated_build_around,
)
from .content_safety import enforce_creation_policy
from .groove_engine import load_groove_template, groove_template_from_performance_input
from .performance_generation import generate_from_performance_guide
from .performance_inputs import (
    PerformanceInputKind,
    analyse_performance_input,
    apply_input_to_project,
    get_input,
    load_manifest,
    register_input,
)
from .tempo_engine import load_tempo_map, tempo_map_from_performance_input
from .tenant_storage import project_path

# The app imports this module during startup. Patch legacy dynamic Build Around imports so
# queued jobs executed in the web/worker process also use the release-gated implementation.
install_release_gated_build_around()

router = APIRouter(tags=["Music Performance Inputs"])

# Keep this aligned with AssetLibrary's real-audio ingest support so every accepted upload
# receives a rights/provenance record as well as performance analysis.
_ALLOWED_AUDIO = {".wav", ".flac", ".mp3", ".m4a", ".ogg", ".aac"}
SourceRole = Literal[
    "vocals", "guitar", "bass", "drums", "keyboard", "piano", "synth", "strings",
    "brass", "woodwinds", "percussion", "other"
]


class GenerateFromPerformanceRequest(BaseModel):
    genre: str = "pop"
    mood: str = "uplifting"
    source_role: SourceRole = "other"
    roles: list[str] = Field(default_factory=lambda: ["drums", "bass", "guitar", "keyboard", "synth"])
    lyrics: str = ""
    include_lead_vocal: bool = False
    include_backing_vocals: bool = False
    extra_direction: str = ""
    bpm: float | None = Field(default=None, ge=30.0, le=300.0)
    key: str | None = None
    meter: str = "4/4"
    output_mode: Literal["complete_mix", "multitrack"] = "multitrack"
    automix: bool = True


def _project(name: str) -> Path:
    try:
        return project_path(name, must_exist=True)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, "Project not found") from exc


def _max_bytes() -> int:
    raw = os.getenv("AURA_PERFORMANCE_INPUT_MAX_MB", "100")
    try:
        mb = max(1, min(2048, int(raw)))
    except Exception:
        mb = 100
    return mb * 1024 * 1024


@router.get("/projects/{project_name}/performance-inputs")
def list_performance_inputs(project_name: str):
    project = _project(project_name)
    manifest = load_manifest(project)
    return {
        "inputs": [item.model_dump(mode="json") for item in manifest.inputs],
        "supported_kinds": ["rhythm", "beatbox", "hum", "melody", "instrument", "voice_memo", "reference_audio"],
        "symbolic_policy": "MIDI/transcription is a guide/edit layer only; final music must remain real rendered audio.",
        "generation_modes": {
            "instrument": "Preserve the uploaded performance as a real-audio anchor and build an editable production around it.",
            "rhythm_beatbox_hum_melody": "Use the upload as neural timing/melody conditioning while keeping the raw guide muted in the final mix by default.",
            "reference_audio": "Reference-only. It is not automatically copied into generated release audio.",
        },
        "smart_warp": {
            "tempo_maps": True,
            "variable_tempo": True,
            "natural_performance_follow": True,
            "real_audio_conform": True,
            "expensive_render_path": "shared background engineering queue",
        },
        "groove_timing": {
            "reference_groove_extraction": True,
            "sixteenth_note_profile": True,
            "swing_detection": True,
            "apply_to": ["drums", "bass", "guitar", "piano", "percussion", "other"],
            "deterministic_humanisation": True,
            "pitch_preserving_real_audio": True,
            "expensive_render_path": "shared background engineering queue",
        },
    }


@router.post("/projects/{project_name}/performance-inputs")
async def upload_performance_input(
    project_name: str,
    kind: PerformanceInputKind = Form(...),
    label: str = Form(""),
    intent: str = Form(""),
    rights_confirmed: bool = Form(...),
    file: UploadFile = File(...),
):
    if not rights_confirmed:
        raise HTTPException(400, "Confirm that you own or have permission to use this performance/reference audio")
    enforce_creation_policy(label, intent, context="Music performance input")
    project = _project(project_name)
    original_name = Path(file.filename or "upload").name[:240]
    suffix = Path(original_name).suffix.lower()
    if suffix not in _ALLOWED_AUDIO:
        raise HTTPException(415, "Upload WAV, FLAC, MP3, M4A, OGG or AAC audio")

    input_id = f"guide_{uuid4().hex}"
    target_dir = project / "input" / "performance_guides"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{input_id}{suffix}"
    limit = _max_bytes()
    size = 0
    try:
        with target.open("wb") as handle:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, f"Performance input exceeds the configured {limit // (1024 * 1024)} MB limit")
                handle.write(chunk)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    finally:
        await file.close()

    source_ref = str(target.relative_to(project)).replace("\\", "/")
    try:
        # AssetLibrary gives the uploaded real audio a SHA-256 identity plus an explicit
        # rights record. PerformanceInput then adds timing/MIDI analysis on top of it.
        asset = AssetLibrary(project).ingest(
            target,
            kind="audio",
            rights_basis="user_owned_or_licensed",
            attestation="I confirm I own or have permission to use this performance/reference audio in this project.",
            tags=["performance_input", kind],
            notes=(intent or "")[:1000],
        )
        item = analyse_performance_input(
            project,
            source_ref=source_ref,
            kind=kind,
            label=label or Path(original_name).stem,
            intent=intent,
            input_id=input_id,
        )
        item.metadata.update({
            "original_filename": original_name,
            "uploaded_bytes": size,
            "rights_confirmed": True,
            "asset_id": asset.id,
            "rights_record_id": asset.rights_record_id,
            "asset_sha256": asset.sha256,
        })
        register_input(project, item)
    except HTTPException:
        target.unlink(missing_ok=True)
        raise
    except Exception as exc:
        target.unlink(missing_ok=True)
        raise HTTPException(422, f"Unable to analyse performance input: {type(exc).__name__}: {exc}") from exc

    return {
        "input": item.model_dump(mode="json"),
        "asset": asset.model_dump(mode="json"),
        "next_step": "Review the detected rhythm/melody guide, build tempo or groove maps when natural timing matters, apply it to the project, then generate real editable audio around it.",
    }


@router.post("/projects/{project_name}/performance-inputs/{input_id}/tempo-map")
def analyse_performance_tempo_map(project_name: str, input_id: str):
    project = _project(project_name)
    try:
        item = get_input(project, input_id)
    except KeyError as exc:
        raise HTTPException(404, "Performance input not found") from exc
    if not item.rights_confirmed or not item.metadata.get("rights_record_id"):
        raise HTTPException(409, "This performance input does not have a complete rights/provenance record")
    try:
        tempo_map, tempo_map_ref = tempo_map_from_performance_input(project, item)
        item.metadata.update(
            {
                "tempo_map_ref": tempo_map_ref,
                "tempo_map_id": tempo_map.id,
                "tempo_mode": "variable" if tempo_map.variable else "fixed_detected",
                "tempo_map_engine": tempo_map.analysis_engine,
            }
        )
        register_input(project, item)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(422, str(exc)) from exc
    return {
        "performance_input_id": item.id,
        "tempo_map_ref": tempo_map_ref,
        "tempo_map": tempo_map.model_dump(mode="json"),
        "source_paths_exposed": False,
        "destructive_source_edit": False,
        "next_step": "Submit an engineering smart_warp job with this performance input as the target to conform another project audio asset to the natural timing.",
    }


@router.get("/projects/{project_name}/performance-inputs/{input_id}/tempo-map")
def get_performance_tempo_map(project_name: str, input_id: str):
    project = _project(project_name)
    try:
        item = get_input(project, input_id)
    except KeyError as exc:
        raise HTTPException(404, "Performance input not found") from exc
    ref = str(item.metadata.get("tempo_map_ref") or "").strip()
    if not ref:
        raise HTTPException(404, "This performance input does not have a persisted tempo map")
    try:
        tempo_map = load_tempo_map(project, ref)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(409, "The stored tempo map is unavailable") from exc
    return {
        "performance_input_id": item.id,
        "tempo_map_ref": ref,
        "tempo_map": tempo_map.model_dump(mode="json"),
        "source_paths_exposed": False,
    }


@router.post("/projects/{project_name}/performance-inputs/{input_id}/groove-template")
def analyse_performance_groove_template(project_name: str, input_id: str):
    project = _project(project_name)
    try:
        item = get_input(project, input_id)
    except KeyError as exc:
        raise HTTPException(404, "Performance input not found") from exc
    if not item.rights_confirmed or not item.metadata.get("rights_record_id"):
        raise HTTPException(409, "This performance input does not have a complete rights/provenance record")
    try:
        template, template_ref = groove_template_from_performance_input(project, item)
        item.metadata.update(
            {
                "groove_template_ref": template_ref,
                "groove_template_id": template.id,
                "groove_engine": template.analysis_engine,
                "groove_swing_ratio": template.swing_ratio,
                "groove_timing_variation_ms": template.timing_variation_ms,
            }
        )
        register_input(project, item)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(422, str(exc)) from exc
    return {
        "performance_input_id": item.id,
        "groove_template_ref": template_ref,
        "groove_template": template.model_dump(mode="json"),
        "source_paths_exposed": False,
        "destructive_source_edit": False,
        "final_audio_generated": False,
        "next_step": "Submit an engineering groove_follow job to apply this performance feel to a project drum, bass, guitar, piano or percussion audio asset.",
    }


@router.get("/projects/{project_name}/performance-inputs/{input_id}/groove-template")
def get_performance_groove_template(project_name: str, input_id: str):
    project = _project(project_name)
    try:
        item = get_input(project, input_id)
    except KeyError as exc:
        raise HTTPException(404, "Performance input not found") from exc
    ref = str(item.metadata.get("groove_template_ref") or "").strip()
    if not ref:
        raise HTTPException(404, "This performance input does not have a persisted groove template")
    try:
        template = load_groove_template(project, ref)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(409, "The stored groove template is unavailable") from exc
    return {
        "performance_input_id": item.id,
        "groove_template_ref": ref,
        "groove_template": template.model_dump(mode="json"),
        "source_paths_exposed": False,
    }


@router.post("/projects/{project_name}/performance-inputs/{input_id}/apply")
def apply_performance_input(project_name: str, input_id: str):
    project = _project(project_name)
    try:
        item = apply_input_to_project(project, input_id)
    except KeyError as exc:
        raise HTTPException(404, "Performance input not found") from exc
    except FileNotFoundError as exc:
        raise HTTPException(409, "This project has no generation manifest to apply the guide to") from exc
    except Exception as exc:
        raise HTTPException(500, f"Unable to apply performance guide: {type(exc).__name__}: {exc}") from exc
    return {
        "input": item.model_dump(mode="json"),
        "generation_context": item.generation_context,
        "detail": "The performance guide is now part of the editable project DNA and generation prompt context.",
    }


@router.post("/projects/{project_name}/performance-inputs/{input_id}/generate")
def generate_from_performance_input(
    project_name: str,
    input_id: str,
    request: GenerateFromPerformanceRequest,
):
    enforce_creation_policy(
        request.genre,
        request.mood,
        request.lyrics,
        request.extra_direction,
        context="Generate music from performance input",
    )
    project = _project(project_name)
    try:
        item = get_input(project, input_id)
    except KeyError as exc:
        raise HTTPException(404, "Performance input not found") from exc
    if not item.rights_confirmed or not item.metadata.get("rights_record_id"):
        raise HTTPException(409, "This performance input does not have a complete rights/provenance record")
    try:
        if item.status != "applied":
            item = apply_input_to_project(project, item.id)

        if item.kind == "instrument":
            asset_id = str(item.metadata.get("asset_id") or "")
            if not asset_id:
                raise RuntimeError("Instrument performance is missing its Asset Library record")
            result = build_around_upload(
                project,
                BuildAroundRequest(
                    asset_id=asset_id,
                    source_role=request.source_role,
                    genre=request.genre,
                    mood=request.mood,
                    include_lead_vocal=request.include_lead_vocal,
                    include_backing_vocals=request.include_backing_vocals,
                    lyrics=request.lyrics,
                    generate_lyrics_if_missing=request.include_lead_vocal and not bool(request.lyrics.strip()),
                    bpm=round(request.bpm) if request.bpm else (round(item.detected_bpm) if item.detected_bpm else None),
                    key=request.key or item.pitch_class_hint,
                    meter=request.meter.split("/")[0],
                    extra_direction=(item.generation_context + " " + request.extra_direction).strip(),
                    output_mode=request.output_mode,
                    automix_after_generation=request.automix,
                ),
            )
            result["performance_input_id"] = item.id
            result["source_preserved_as_real_audio"] = True
            result["symbolic_guide_used_as_final_audio"] = False
            return result

        if item.kind in {"rhythm", "beatbox", "hum", "melody", "voice_memo"}:
            return generate_from_performance_guide(
                project,
                item,
                genre=request.genre,
                mood=request.mood,
                roles=request.roles,
                lyrics=request.lyrics,
                include_lead_vocal=request.include_lead_vocal,
                include_backing_vocals=request.include_backing_vocals,
                extra_direction=request.extra_direction,
                bpm=request.bpm,
                key=request.key,
                meter=request.meter,
                automix=request.automix,
            )

        raise RuntimeError(
            "Reference audio is a style/reference input, not a direct Build Around source. Choose a rhythm, hum, melody or owned instrument performance for generation."
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except (RuntimeError, FileNotFoundError, KeyError) as exc:
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, f"Performance-guided generation failed: {type(exc).__name__}: {exc}") from exc
