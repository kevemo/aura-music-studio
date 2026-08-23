from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

from .assets import AssetLibrary
from .plans import UPLOAD_AUDIO
from .tenant_storage import project_path

router = APIRouter(tags=["Studio Recording"])

ALLOWED_ROLES = {
    "vocals", "backing_vocals", "guitar", "bass", "drums", "piano", "keyboard",
    "synth", "strings", "brass", "woodwinds", "percussion", "other",
}


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


def _safe_name(value: str) -> str:
    clean = "".join(ch if ch.isalnum() or ch in " _-." else "_" for ch in (value or "Recording"))
    return clean.strip(" .")[:100] or "Recording"


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
    role = role.strip().lower()
    if role not in ALLOWED_ROLES:
        raise HTTPException(400, "Invalid recording role")
    if len((rights_confirmation or "").strip()) < 10:
        raise HTTPException(400, "Recording rights confirmation is required")
    if not shutil.which("ffmpeg"):
        raise HTTPException(503, "ffmpeg is required to normalize browser recordings")

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
        if source.stat().st_size < 256:
            raise HTTPException(400, "Recording is empty")

        wav_name = _safe_name(title) + ".wav"
        normalized = tmp / wav_name
        try:
            subprocess.run([
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-i", str(source),
                "-vn", "-ac", "2", "-ar", "48000", "-c:a", "pcm_s24le",
                str(normalized),
            ], check=True, timeout=300)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise HTTPException(400, f"Could not decode this browser recording: {type(exc).__name__}") from exc

        record = AssetLibrary(project).ingest(
            normalized,
            kind="audio",
            rights_basis="user_recorded_or_authorized",
            attestation=rights_confirmation,
            tags=["studio_recording", role],
            notes=(notes or "")[:2000],
        )
    return {
        "asset": record.model_dump(),
        "normalized_format": "WAV PCM 24-bit / 48 kHz",
        "source_role": role,
        "ready_for_build_around": True,
        "dry_recording": True,
    }
