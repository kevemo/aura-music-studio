from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any
from uuid import uuid4

from .professional_editor import EditorItem, EditorSequence, EditorTrack, ProfessionalEditorStore
from .professional_editor_source_security import normalize_project_source_ref

MAX_RENDER_WIDTH = 3840
MAX_RENDER_HEIGHT = 2160
MAX_RENDER_FPS = 60.0
MAX_RENDER_DURATION_SECONDS = 3600.0
MAX_RENDER_ITEMS = 64
MAX_SOURCE_BYTES = 12 * 1024 * 1024 * 1024

_VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
_AUDIO_SUFFIXES = {".wav", ".mp3", ".flac", ".m4a", ".aac", ".ogg", ".opus"}
_DEFAULT_TRANSFORM = {
    "x": 0.0,
    "y": 0.0,
    "scale_x": 1.0,
    "scale_y": 1.0,
    "rotation": 0.0,
    "anchor_x": 0.5,
    "anchor_y": 0.5,
}
_DEFAULT_CROP = {"left": 0.0, "top": 0.0, "right": 0.0, "bottom": 0.0}
_DEFAULT_COLOR = {
    "exposure": 0.0,
    "contrast": 1.0,
    "saturation": 1.0,
    "brightness": 0.0,
    "gamma": 1.0,
    "temperature": 0.0,
    "tint": 0.0,
    "highlights": 0.0,
    "shadows": 0.0,
}


def _close_mapping(actual: dict[str, Any], expected: dict[str, float]) -> bool:
    for key, expected_value in expected.items():
        try:
            if abs(float(actual.get(key, expected_value)) - expected_value) > 1e-6:
                return False
        except (TypeError, ValueError):
            return False
    return True


def _validate_supported_item(item: EditorItem) -> None:
    if item.reverse:
        raise ValueError("Professional Editor export does not yet support reverse playback")
    if abs(float(item.speed) - 1.0) > 1e-6:
        raise ValueError("Professional Editor export does not yet support speed changes")
    if item.effects or item.masks or item.keyframes:
        raise ValueError("Professional Editor export cannot silently omit effects, masks or keyframes")
    if item.blend_mode != "normal" or abs(float(item.opacity) - 1.0) > 1e-6:
        raise ValueError("Professional Editor export does not yet support item blend/opacity changes")
    if not _close_mapping(item.transform, _DEFAULT_TRANSFORM):
        raise ValueError("Professional Editor export does not yet support transforms")
    if not _close_mapping(item.crop, _DEFAULT_CROP):
        raise ValueError("Professional Editor export does not yet support crop changes")
    if not _close_mapping(item.color, _DEFAULT_COLOR):
        raise ValueError("Professional Editor export does not yet support color changes")


def _source_path(project_dir: Path, item: EditorItem, suffixes: set[str]) -> Path:
    source_ref = normalize_project_source_ref(project_dir, item.source_ref)
    if not source_ref:
        raise ValueError(f"Timeline item {item.id} has no project media source")
    root = project_dir.resolve()
    source = (root / source_ref).resolve(strict=True)
    try:
        source.relative_to(root)
    except ValueError as exc:
        raise ValueError("Timeline media resolves outside the member project") from exc
    if not source.is_file():
        raise ValueError(f"Timeline media is not a regular file: {source_ref}")
    if source.suffix.lower() not in suffixes:
        raise ValueError(f"Unsupported timeline media type: {source.suffix.lower() or 'no extension'}")
    if source.stat().st_size > MAX_SOURCE_BYTES:
        raise ValueError("Timeline media exceeds the per-source render size limit")
    return source


def _active_timeline(store: ProfessionalEditorStore, payload: dict[str, Any]):
    project = store.load()
    branch_id = str(payload.get("branch_id") or project.active_branch_id)
    branch = store._branch(project, branch_id)
    sequence_id = str(payload.get("sequence_id") or "")
    sequence = store._sequence(branch, sequence_id)
    expected_updated_at = str(payload.get("sequence_updated_at") or "")
    if expected_updated_at and sequence.updated_at != expected_updated_at:
        raise ValueError("Editor sequence changed after render submission; submit a fresh render")
    return branch, sequence


def validate_editor_render_request(project_dir: Path, payload: dict[str, Any]) -> dict[str, Any]:
    """Validate a render snapshot without exposing host paths or executing FFmpeg."""
    store = ProfessionalEditorStore(project_dir)
    branch, sequence = _active_timeline(store, payload)
    if sequence.kind != "video":
        raise ValueError("Professional Editor MP4 export requires a video sequence")
    if sequence.width > MAX_RENDER_WIDTH or sequence.height > MAX_RENDER_HEIGHT:
        raise ValueError("Render resolution exceeds the 3840x2160 production limit")
    if sequence.fps > MAX_RENDER_FPS:
        raise ValueError("Render frame rate exceeds the 60 fps production limit")
    if sequence.duration > MAX_RENDER_DURATION_SECONDS:
        raise ValueError("Render duration exceeds the 60 minute production limit")

    tracks_by_id = {track.id: track for track in branch.tracks}
    items_by_id = {item.id: item for item in branch.items}
    tracks: list[EditorTrack] = []
    items: list[tuple[EditorTrack, EditorItem]] = []
    for track_id in sequence.track_ids:
        track = tracks_by_id.get(track_id)
        if not track or not track.enabled:
            continue
        if track.effects or track.keyframes or track.blend_mode != "normal" or abs(float(track.opacity) - 1.0) > 1e-6:
            raise ValueError("Professional Editor export cannot silently omit track effects, keyframes or blending")
        tracks.append(track)
        for item_id in track.item_ids:
            item = items_by_id.get(item_id)
            if not item or not item.enabled:
                continue
            if item.start >= sequence.duration:
                continue
            items.append((track, item))
    if len(items) > MAX_RENDER_ITEMS:
        raise ValueError(f"Render contains more than {MAX_RENDER_ITEMS} active timeline items")

    visual_count = 0
    audio_count = 0
    for track, item in items:
        if item.kind in {"video_clip", "image_layer"}:
            if not track.visible or not item.visible:
                continue
            _validate_supported_item(item)
            _source_path(project_dir, item, _VIDEO_SUFFIXES if item.kind == "video_clip" else _IMAGE_SUFFIXES)
            visual_count += 1
        elif item.kind == "audio_clip":
            if track.muted or bool(item.audio.get("muted", False)):
                continue
            if item.effects or item.keyframes:
                raise ValueError("Professional Editor export cannot silently omit audio effects or keyframes")
            _source_path(project_dir, item, _AUDIO_SUFFIXES)
            audio_count += 1
        else:
            raise ValueError(f"Professional Editor export does not yet support {item.kind}")

    if visual_count == 0:
        raise ValueError("Video export requires at least one visible video or image item")
    return {
        "branch_id": branch.id,
        "sequence_id": sequence.id,
        "sequence_updated_at": sequence.updated_at,
        "width": sequence.width,
        "height": sequence.height,
        "fps": sequence.fps,
        "duration": sequence.duration,
        "visual_items": visual_count,
        "audio_items": audio_count,
    }


def _ffmpeg_command(project_dir: Path, payload: dict[str, Any], output_path: Path) -> list[str]:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("FFmpeg is not installed on this render worker")

    store = ProfessionalEditorStore(project_dir)
    branch, sequence = _active_timeline(store, payload)
    validate_editor_render_request(project_dir, payload)
    tracks_by_id = {track.id: track for track in branch.tracks}
    items_by_id = {item.id: item for item in branch.items}

    command = [
        ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i",
        f"color=c={sequence.background or '#000000'}:s={sequence.width}x{sequence.height}:r={sequence.fps}:d={sequence.duration}",
    ]
    visual_inputs: list[tuple[int, EditorItem]] = []
    audio_inputs: list[tuple[int, EditorItem]] = []
    next_input = 1

    for track_id in sequence.track_ids:
        track = tracks_by_id.get(track_id)
        if not track or not track.enabled:
            continue
        for item_id in track.item_ids:
            item = items_by_id.get(item_id)
            if not item or not item.enabled or item.start >= sequence.duration:
                continue
            clip_duration = min(float(item.duration), float(sequence.duration) - float(item.start))
            if clip_duration <= 0:
                continue
            if item.kind == "video_clip" and track.visible and item.visible:
                source = _source_path(project_dir, item, _VIDEO_SUFFIXES)
                command += ["-ss", f"{item.source_in:.6f}", "-t", f"{clip_duration:.6f}", "-i", str(source)]
                visual_inputs.append((next_input, item))
                next_input += 1
            elif item.kind == "image_layer" and track.visible and item.visible:
                source = _source_path(project_dir, item, _IMAGE_SUFFIXES)
                command += ["-loop", "1", "-t", f"{clip_duration:.6f}", "-i", str(source)]
                visual_inputs.append((next_input, item))
                next_input += 1
            elif item.kind == "audio_clip" and not track.muted and not bool(item.audio.get("muted", False)):
                source = _source_path(project_dir, item, _AUDIO_SUFFIXES)
                command += ["-ss", f"{item.source_in:.6f}", "-t", f"{clip_duration:.6f}", "-i", str(source)]
                audio_inputs.append((next_input, item))
                next_input += 1

    filters: list[str] = ["[0:v]setpts=PTS-STARTPTS[canvas0]"]
    current = "canvas0"
    for position, (input_index, item) in enumerate(visual_inputs, start=1):
        clip_duration = min(float(item.duration), float(sequence.duration) - float(item.start))
        prepared = f"visual{position}"
        output = f"canvas{position}"
        filters.append(
            f"[{input_index}:v]scale={sequence.width}:{sequence.height}:force_original_aspect_ratio=decrease,"
            f"pad={sequence.width}:{sequence.height}:(ow-iw)/2:(oh-ih)/2:color=black,"
            f"trim=duration={clip_duration:.6f},setpts=PTS-STARTPTS[{prepared}]"
        )
        filters.append(
            f"[{current}][{prepared}]overlay=0:0:enable='between(t,{item.start:.6f},{item.start + clip_duration:.6f})'[{output}]"
        )
        current = output

    audio_labels: list[str] = []
    for position, (input_index, item) in enumerate(audio_inputs, start=1):
        clip_duration = min(float(item.duration), float(sequence.duration) - float(item.start))
        label = f"audio{position}"
        gain_db = float(item.audio.get("gain_db", 0.0) or 0.0)
        delay_ms = max(0, int(round(float(item.start) * 1000.0)))
        filters.append(
            f"[{input_index}:a]atrim=duration={clip_duration:.6f},asetpts=PTS-STARTPTS,"
            f"volume={gain_db:.3f}dB,adelay={delay_ms}|{delay_ms}[{label}]"
        )
        audio_labels.append(label)

    if audio_labels:
        joined = "".join(f"[{label}]" for label in audio_labels)
        filters.append(f"{joined}amix=inputs={len(audio_labels)}:normalize=0:duration=longest[aout]")

    quality = str(payload.get("quality") or "standard")
    crf = {"draft": "28", "standard": "23", "high": "18"}.get(quality)
    if crf is None:
        raise ValueError("Invalid editor render quality")

    command += ["-filter_complex", ";".join(filters), "-map", f"[{current}]", "-c:v", "libx264", "-preset", "medium", "-crf", crf, "-pix_fmt", "yuv420p"]
    if audio_labels and bool(payload.get("include_audio", True)):
        command += ["-map", "[aout]", "-c:a", "aac", "-b:a", "192k"]
    else:
        command += ["-an"]
    command += ["-t", f"{sequence.duration:.6f}", "-movflags", "+faststart", str(output_path)]
    return command


def run_editor_render_job(project_dir: Path, payload: dict[str, Any]) -> dict[str, Any]:
    """Render a bounded Professional Editor sequence and return only project-relative output metadata."""
    validated = validate_editor_render_request(project_dir, payload)
    output_dir = project_dir / "output" / "professional_editor"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"Pulsar_Editor_{uuid4().hex}.mp4"
    command = _ffmpeg_command(project_dir, payload, output_path)
    timeout_seconds = max(300, min(7200, int(float(validated["duration"]) * 4.0) + 120))
    try:
        subprocess.run(command, check=True, timeout=timeout_seconds, capture_output=True, text=True)
    except subprocess.TimeoutExpired as exc:
        output_path.unlink(missing_ok=True)
        raise RuntimeError("Professional Editor render exceeded the worker time limit") from exc
    except subprocess.CalledProcessError as exc:
        output_path.unlink(missing_ok=True)
        detail = (exc.stderr or "FFmpeg render failed").strip()[-2000:]
        raise RuntimeError(f"Professional Editor render failed: {detail}") from exc
    if not output_path.is_file() or output_path.stat().st_size <= 0:
        output_path.unlink(missing_ok=True)
        raise RuntimeError("Professional Editor render did not produce a valid output file")
    output_ref = output_path.relative_to(project_dir.resolve()).as_posix()
    return {
        "kind": "professional_editor_render",
        "format": "mp4",
        "output_ref": output_ref,
        "sequence_id": validated["sequence_id"],
        "width": validated["width"],
        "height": validated["height"],
        "fps": validated["fps"],
        "duration": validated["duration"],
        "audio_included": bool(payload.get("include_audio", True)) and validated["audio_items"] > 0,
    }


__all__ = [
    "MAX_RENDER_WIDTH",
    "MAX_RENDER_HEIGHT",
    "MAX_RENDER_FPS",
    "MAX_RENDER_DURATION_SECONDS",
    "MAX_RENDER_ITEMS",
    "validate_editor_render_request",
    "run_editor_render_job",
]
