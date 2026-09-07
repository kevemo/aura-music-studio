from __future__ import annotations

import hashlib
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .aura_effect_system_creator import EffectSystemSpec
from .aura_effect_system_project import preview_project_effect_system
from .creative_effect_entitlements import CreativeEffectEntitlementStore, store as default_entitlement_store

MAX_EFFECT_PREVIEW_SOURCE_BYTES = 256 * 1024 * 1024
MAX_EFFECT_PREVIEW_OUTPUT_BYTES = 16 * 1024 * 1024
MAX_EFFECT_PREVIEW_SECONDS = 30
EFFECT_PREVIEW_RENDER_TIMEOUT_SECONDS = 45


def _project_source(project: Path, source_media_path: str | Path) -> Path:
    root = project.resolve()
    candidate = Path(source_media_path)
    if not candidate.is_absolute():
        candidate = root / candidate
    source = candidate.resolve()
    if source != root and root not in source.parents:
        raise ValueError("Effect-system preview source must remain inside the project")
    if not source.is_file():
        raise FileNotFoundError(source)
    if source.stat().st_size > MAX_EFFECT_PREVIEW_SOURCE_BYTES:
        raise ValueError("Effect-system preview source exceeds the resource budget")
    return source


def _preview_root(project: Path) -> Path:
    root = project.resolve() / "work" / "effect_system_preview_audio"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _unlink(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def render_effect_system_preview(
    project: Path,
    track_id: str,
    spec: EffectSystemSpec,
    *,
    user_id: str,
    source_media_path: str | Path,
    source_prompt_fingerprint: str | None = None,
    entitlement_store: CreativeEffectEntitlementStore = default_entitlement_store,
) -> dict[str, Any]:
    """Render a short server-controlled audio preview without mutating source media or DAW state."""
    project = project.resolve()
    source = _project_source(project, source_media_path)
    preview = preview_project_effect_system(
        project,
        track_id,
        spec,
        user_id=user_id,
        source_prompt_fingerprint=source_prompt_fingerprint,
        entitlement_store=entitlement_store,
    )

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("FFmpeg is unavailable for effect-system preview rendering")
    ffmpeg_path = Path(ffmpeg).resolve()
    if not ffmpeg_path.is_file():
        raise RuntimeError("Resolved FFmpeg executable is unavailable")

    output_root = _preview_root(project)
    track_key = hashlib.sha256(str(track_id).encode("utf-8")).hexdigest()[:16]
    final_path = output_root / f"{preview['fingerprint']}.{track_key}.wav"
    handle = tempfile.NamedTemporaryFile(prefix="render-", suffix=".wav.tmp", dir=output_root, delete=False)
    temporary_path = Path(handle.name)
    handle.close()

    argv = [
        str(ffmpeg_path),
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-i",
        str(source),
        "-t",
        str(MAX_EFFECT_PREVIEW_SECONDS),
        "-af",
        str(preview["ffmpeg_filter_chain"]),
        "-vn",
        "-acodec",
        "pcm_s16le",
        str(temporary_path),
    ]

    try:
        completed = subprocess.run(
            argv,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=EFFECT_PREVIEW_RENDER_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        _unlink(temporary_path)
        raise RuntimeError("Effect-system preview render exceeded the time budget") from exc
    except Exception:
        _unlink(temporary_path)
        raise

    if completed.returncode != 0:
        _unlink(temporary_path)
        raise RuntimeError("Effect-system preview render failed")
    if not temporary_path.is_file() or temporary_path.stat().st_size <= 0:
        _unlink(temporary_path)
        raise RuntimeError("Effect-system preview render produced no audio")
    if temporary_path.stat().st_size > MAX_EFFECT_PREVIEW_OUTPUT_BYTES:
        _unlink(temporary_path)
        raise RuntimeError("Effect-system preview output exceeds the resource budget")

    temporary_path.replace(final_path)
    relative_path = final_path.relative_to(project).as_posix()
    return {
        **preview,
        "rendered": True,
        "preview_audio_project_relative_path": relative_path,
        "preview_seconds_max": MAX_EFFECT_PREVIEW_SECONDS,
        "resource_budget": {
            "source_bytes_max": MAX_EFFECT_PREVIEW_SOURCE_BYTES,
            "output_bytes_max": MAX_EFFECT_PREVIEW_OUTPUT_BYTES,
            "render_seconds_max": MAX_EFFECT_PREVIEW_SECONDS,
            "wall_clock_timeout_seconds": EFFECT_PREVIEW_RENDER_TIMEOUT_SECONDS,
        },
        "source_media_mutated": False,
        "project_metadata_mutated": False,
        "server_selected_executable": True,
        "client_supplied_command_authority": False,
        "shell_execution": False,
    }


__all__ = [
    "EFFECT_PREVIEW_RENDER_TIMEOUT_SECONDS",
    "MAX_EFFECT_PREVIEW_OUTPUT_BYTES",
    "MAX_EFFECT_PREVIEW_SECONDS",
    "MAX_EFFECT_PREVIEW_SOURCE_BYTES",
    "render_effect_system_preview",
]
