from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import soundfile as sf

from .assets import AssetLibrary, AssetRecord


ALLOWED_RECORDING_ROLES = {
    "vocals", "backing_vocals", "guitar", "bass", "drums", "piano", "keyboard",
    "synth", "strings", "brass", "woodwinds", "percussion", "other",
}


def safe_recording_name(value: str) -> str:
    clean = "".join(ch if ch.isalnum() or ch in " _-." else "_" for ch in (value or "Recording"))
    return clean.strip(" .")[:100] or "Recording"


def validate_recording_role(role: str) -> str:
    clean = (role or "").strip().lower()
    if clean not in ALLOWED_RECORDING_ROLES:
        raise ValueError("Invalid recording role")
    return clean


def normalize_capture(source: Path, output: Path) -> Path:
    """Decode a browser/device capture into the Studio's canonical dry recording format."""
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg is required to normalize browser recordings")
    source = source.resolve()
    output = output.resolve()
    if not source.is_file() or source.stat().st_size < 256:
        raise ValueError("Recording is empty")
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-i", str(source), "-vn", "-ac", "2", "-ar", "48000", "-c:a", "pcm_s24le",
                str(output),
            ],
            check=True,
            timeout=300,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"Could not decode this browser recording: {type(exc).__name__}") from exc
    info = sf.info(output)
    if info.samplerate != 48000 or info.channels != 2 or info.frames <= 0:
        raise RuntimeError("Normalized Studio recording did not meet the 48 kHz stereo contract")
    return output


def ingest_recording(
    project: Path,
    normalized_wav: Path,
    *,
    role: str,
    notes: str = "",
    rights_confirmation: str,
    extra_tags: list[str] | None = None,
) -> AssetRecord:
    role = validate_recording_role(role)
    if len((rights_confirmation or "").strip()) < 10:
        raise ValueError("Recording rights confirmation is required")
    tags = ["studio_recording", "dry_recording", role, *(extra_tags or [])]
    return AssetLibrary(project).ingest(
        normalized_wav,
        kind="audio",
        rights_basis="user_recorded_or_authorized",
        attestation=rights_confirmation,
        tags=list(dict.fromkeys(tags)),
        notes=(notes or "")[:2000],
    )
