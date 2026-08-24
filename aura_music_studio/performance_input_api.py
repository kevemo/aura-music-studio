from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from .content_safety import enforce_creation_policy
from .performance_inputs import (
    PerformanceInputKind,
    analyse_performance_input,
    apply_input_to_project,
    load_manifest,
    register_input,
)
from .tenant_storage import project_path

router = APIRouter(tags=["Music Performance Inputs"])

_ALLOWED_AUDIO = {".wav", ".flac", ".mp3", ".m4a", ".ogg", ".aac", ".aiff", ".aif"}


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
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in _ALLOWED_AUDIO:
        raise HTTPException(415, "Upload WAV, FLAC, MP3, M4A, OGG, AAC or AIFF audio")

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
        item = analyse_performance_input(
            project,
            source_ref=source_ref,
            kind=kind,
            label=label or Path(file.filename or "performance").stem,
            intent=intent,
            input_id=input_id,
        )
        item.metadata.update({
            "original_filename": Path(file.filename or "upload").name[:240],
            "uploaded_bytes": size,
            "rights_confirmed": True,
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
        "next_step": "Review the detected rhythm/melody guide, then apply it to the project so Aura uses it as a generation anchor.",
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
