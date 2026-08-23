from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

from .plans import UPLOAD_AUDIO
from .recording_core import ingest_recording, normalize_capture, safe_recording_name, validate_recording_role
from .tenant_storage import project_path

router = APIRouter(tags=["Studio Recording"])


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if not member:
        raise HTTPException(401, "Sign in required")
    if not member.plan.has(UPLOAD_AUDIO):
        raise HTTPException(403, "Browser recording unlocks on Base")
    return member


def _project(name: str) -> Path:
    try:
        return project_path(name, must_exist=True)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, "Project not found") from exc


@router.post("/projects/{project_name}/recordings")
async def save_recording(
    project_name: str,
    request: Request,
    audio: UploadFile = File(...),
    role: str = Form("vocals"),
    title: str = Form("Studio Recording"),
    notes: str = Form(""),
    rights_confirmation: str = Form("I confirm this is my recording or I have permission to use it."),
):
    _member(request)
    project = _project(project_name)
    try:
        role = validate_recording_role(role)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if len((rights_confirmation or "").strip()) < 10:
        raise HTTPException(400, "Recording rights confirmation is required")

    suffix = Path(audio.filename or "recording.webm").suffix.lower() or ".webm"
    with tempfile.TemporaryDirectory(prefix="lss-recording-") as tmp_dir:
        tmp = Path(tmp_dir)
        source = tmp / f"capture{suffix}"
        with source.open("wb") as handle:
            total = 0
            while chunk := await audio.read(1024 * 1024):
                total += len(chunk)
                if total > 250 * 1024 * 1024:
                    raise HTTPException(413, "Recording is too large")
                handle.write(chunk)
        normalized = tmp / (safe_recording_name(title) + ".wav")
        try:
            normalize_capture(source, normalized)
            record = ingest_recording(
                project,
                normalized,
                role=role,
                notes=notes,
                rights_confirmation=rights_confirmation,
            )
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(400 if isinstance(exc, ValueError) else 503, str(exc)) from exc

    return {
        "asset": record.model_dump(),
        "normalized_format": "WAV PCM 24-bit / 48 kHz",
        "source_role": role,
        "ready_for_build_around": True,
        "dry_recording": True,
    }
