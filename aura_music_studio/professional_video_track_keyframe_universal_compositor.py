from __future__ import annotations

import json
import math
import shutil
import subprocess
from copy import deepcopy
from pathlib import Path
from typing import Any
from uuid import uuid4

from . import professional_video_grouped_unified_compositor as _grouped
from . import professional_video_track_keyframe_compositor as _track_keyframes
from .professional_editor_renderer import EditorExportResult, EditorRenderError, EditorRenderUnsupported
from .professional_universal_scoped_visual_video_compositor import (
    SUPPORTED_UNIVERSAL_VIDEO_EFFECTS,
    _contract_type,
    _validate_universal_effect_keyframes,
)
from .professional_video_effects_compositor import _StateProxy


_ORIGINAL_GROUPED_VALIDATE = _track_keyframes._validate_grouped_state_with_track_opacity
_ORIGINAL_TRACK_VALIDATE = _track_keyframes.TrackOpacityKeyframedGroupedTrackVideoCompositor._validate_video_state
_ORIGINAL_TRACK_RENDER = _track_keyframes.TrackOpacityKeyframedGroupedTrackVideoCompositor.render_video_advanced
_RENDERED_ITEM_COLOUR_DEFAULTS: dict[str, float] = {
    "exposure": 0.0,
    "brightness": 0.0,
    "contrast": 1.0,
    "saturation": 1.0,
    "gamma": 1.0,
    "temperature": 0.0,
    "tint": 0.0,
}


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _validate_track_effect_automation(effect: dict[str, Any]) -> None:
    """Preserve the mature universal track-effect automation contract.

    Wave 9 adds track-opacity automation. It must not regress the already executable universal
    track-effect keyframes for ``video.grade.basic`` parameters and universal effect ``mix``.
    Unsupported effect/keyframe paths remain fail-closed through the established universal
    validator rather than being silently flattened.
    """

    keyframes = effect.get("keyframes") or {}
    if not keyframes:
        return
    kind = _contract_type(effect)
    if kind not in SUPPORTED_UNIVERSAL_VIDEO_EFFECTS:
        raise EditorRenderUnsupported(
            f"Keyframed video track effect is not render-safe: {kind or str(effect.get('type') or 'unnamed')}"
        )
    _validate_universal_effect_keyframes(effect)


def _sanitized_state_for_wave9_validation(state: dict[str, Any], sequence_id: str) -> dict[str, Any]:
    sanitized = deepcopy(state)
    branch = sanitized.get("branch") or {}
    sequences = {row.get("id"): row for row in branch.get("sequences") or []}
    tracks = {row.get("id"): row for row in branch.get("tracks") or []}
    sequence = sequences.get(sequence_id)
    if sequence is None:
        return sanitized

    for track_id in sequence.get("track_ids") or []:
        track = tracks.get(track_id)
        if not track or not track.get("enabled", True):
            continue
        for effect in track.get("effects") or []:
            if not effect.get("enabled", True) or not effect.get("keyframes"):
                continue
            _validate_track_effect_automation(effect)
            # The Wave 9 validator contains an older blanket rejection. Strip only from the
            # validation copy after the mature universal validator has accepted the authored
            # automation. Rendering still receives the untouched original state/keyframes.
            effect["keyframes"] = {}
    return sanitized


def _sanitized_tracks_for_wave9_validation(sequence: dict[str, Any], tracks: dict) -> dict:
    sanitized = deepcopy(tracks)
    for track_id in sequence.get("track_ids") or []:
        track = sanitized.get(track_id)
        if not track or not track.get("enabled", True):
            continue
        for effect in track.get("effects") or []:
            if not effect.get("enabled", True) or not effect.get("keyframes"):
                continue
            _validate_track_effect_automation(effect)
            effect["keyframes"] = {}
    return sanitized


def _validate_grouped_state_with_universal_track_automation(
    self,
    state: dict[str, Any],
    sequence_id: str,
) -> None:
    _ORIGINAL_GROUPED_VALIDATE(
        self,
        _sanitized_state_for_wave9_validation(state, sequence_id),
        sequence_id,
    )


def _validate_video_state_with_universal_track_automation(
    self,
    sequence: dict[str, Any],
    tracks: dict,
    items: dict,
) -> None:
    sanitized_items = deepcopy(items)
    for item in sanitized_items.values():
        if not item or not item.get("enabled", True):
            continue
        colour = item.get("color") or {}
        if (
            abs(_finite(colour.get("highlights"), 0.0)) > 1e-8
            or abs(_finite(colour.get("shadows"), 0.0)) > 1e-8
        ):
            raise EditorRenderUnsupported("Video item highlights/shadows are not yet render-safe")
    _ORIGINAL_TRACK_VALIDATE(
        self,
        sequence,
        _sanitized_tracks_for_wave9_validation(sequence, tracks),
        sanitized_items,
    )


def _item_colour_filter(colour: dict[str, Any]) -> str:
    brightness = _finite(colour.get("brightness"), 0.0) + _finite(colour.get("exposure"), 0.0) * 0.1
    contrast = max(0.0, _finite(colour.get("contrast"), 1.0))
    saturation = max(0.0, _finite(colour.get("saturation"), 1.0))
    gamma = max(0.05, _finite(colour.get("gamma"), 1.0))
    temperature = max(-1.0, min(1.0, _finite(colour.get("temperature"), 0.0)))
    tint = max(-1.0, min(1.0, _finite(colour.get("tint"), 0.0)))
    warmth = temperature * 0.24
    green = -tint * 0.20
    filters = [
        "eq="
        f"brightness={max(-1.0, min(1.0, brightness)):.8f}:"
        f"contrast={contrast:.8f}:saturation={saturation:.8f}:gamma={gamma:.8f}"
    ]
    if abs(temperature) > 1e-8 or abs(tint) > 1e-8:
        filters.append(
            "colorbalance="
            f"rm={warmth:.8f}:gm={green:.8f}:bm={-warmth:.8f}:"
            f"rh={warmth:.8f}:gh={green:.8f}:bh={-warmth:.8f}:pl=1"
        )
    return ",".join(filters)


def _needs_colour_derivative(item: dict[str, Any]) -> bool:
    if item.get("kind") not in {"video_clip", "image_layer"}:
        return False
    colour = item.get("color") or {}
    return (
        abs(_finite(colour.get("temperature"), 0.0)) > 1e-8
        or abs(_finite(colour.get("tint"), 0.0)) > 1e-8
    )


def _derive_item_colour(self, item: dict[str, Any], root: Path) -> str:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise EditorRenderUnsupported("FFmpeg is unavailable on this runtime")
    source = self._source(item.get("source_ref"))
    root.mkdir(parents=True, exist_ok=True)
    filter_chain = _item_colour_filter(item.get("color") or {})
    if item.get("kind") == "image_layer":
        output = root / f"{item['id']}.png"
        args = [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-vf",
            filter_chain + ",format=rgba",
            "-frames:v",
            "1",
            str(output),
        ]
    else:
        output = root / f"{item['id']}.mov"
        args = [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-map",
            "0:v:0",
            "-map",
            "0:a?",
            "-vf",
            filter_chain + ",format=argb",
            "-c:v",
            "qtrle",
            "-pix_fmt",
            "argb",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            str(output),
        ]
    completed = subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=self.timeout_seconds,
        check=False,
    )
    if completed.returncode != 0 or not output.is_file() or output.stat().st_size <= 0:
        output.unlink(missing_ok=True)
        detail = (completed.stderr or "FFmpeg produced no item colour derivative").strip().splitlines()
        message = detail[-1][:500] if detail else "FFmpeg produced no item colour derivative"
        raise EditorRenderError(f"Video item colour pre-render failed: {message}")
    return output.relative_to(self.project_dir).as_posix()


def _state_with_wave13_item_colour_derivatives(self, state: dict[str, Any], sequence_id: str, root: Path) -> tuple[dict[str, Any], int]:
    rendered = deepcopy(state)
    branch = rendered.get("branch") or {}
    sequences = {row.get("id"): row for row in branch.get("sequences") or []}
    tracks = {row.get("id"): row for row in branch.get("tracks") or []}
    items = {row.get("id"): row for row in branch.get("items") or []}
    sequence = sequences.get(sequence_id)
    if sequence is None:
        return rendered, 0
    count = 0
    for track_id in sequence.get("track_ids") or []:
        track = tracks.get(track_id)
        if not track or not track.get("enabled", True):
            continue
        for item_id in track.get("item_ids") or []:
            item = items.get(item_id)
            if not item or not item.get("enabled", True):
                continue
            colour = item.get("color") or {}
            if (
                abs(_finite(colour.get("highlights"), 0.0)) > 1e-8
                or abs(_finite(colour.get("shadows"), 0.0)) > 1e-8
            ):
                raise EditorRenderUnsupported("Video item highlights/shadows are not yet render-safe")
            if not _needs_colour_derivative(item):
                continue
            item["source_ref"] = _derive_item_colour(self, item, root)
            neutral = dict(colour)
            for name, default in _RENDERED_ITEM_COLOUR_DEFAULTS.items():
                neutral[name] = default
            item["color"] = neutral
            count += 1
    return rendered, count


def _mark_track_effect_keyframe_truth(result: EditorExportResult, project_dir, *, colour_derivatives: int = 0) -> EditorExportResult:
    metadata = project_dir / result.metadata_ref
    payload = json.loads(metadata.read_text(encoding="utf-8"))
    payload["keyframed_track_effects_fail_closed"] = False
    payload["supported_keyframed_track_effects_preserved"] = True
    payload["unsupported_track_effect_keyframes_fail_closed"] = True
    if colour_derivatives:
        payload["wave13_item_colour_derivatives"] = colour_derivatives
        payload["wave13_item_colour_derivatives_alpha_preserving"] = True
        payload["wave13_item_colour_source_media_mutated"] = False
    metadata.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return result


def _render_track_opacity_with_truthful_track_effect_metadata(self, sequence_id: str) -> EditorExportResult:
    real_store = self.store
    original_state = real_store.public_state()
    derived_root = self.project_dir / "work" / "editor_video_wave13_colour" / uuid4().hex
    colour_derivatives = 0
    try:
        rendered_state, colour_derivatives = _state_with_wave13_item_colour_derivatives(
            self,
            original_state,
            sequence_id,
            derived_root,
        )
        self.store = _StateProxy(rendered_state)  # type: ignore[assignment]
        result = _ORIGINAL_TRACK_RENDER(self, sequence_id)
        return _mark_track_effect_keyframe_truth(
            result,
            self.project_dir,
            colour_derivatives=colour_derivatives,
        )
    finally:
        self.store = real_store
        shutil.rmtree(derived_root, ignore_errors=True)


# Install the corrected validation boundary process-wide. The grouped renderer continues to use
# Wave 9's opacity stage, while supported universal track-effect automation remains executable.
_track_keyframes.TrackOpacityKeyframedGroupedTrackVideoCompositor._validate_video_state = (
    _validate_video_state_with_universal_track_automation
)
_track_keyframes.TrackOpacityKeyframedGroupedTrackVideoCompositor.render_video_advanced = (
    _render_track_opacity_with_truthful_track_effect_metadata
)
_grouped.GroupedUnifiedAdvancedVideoCompositor._validate_effect_state = (
    _validate_grouped_state_with_universal_track_automation
)


class TrackKeyframeUniversalVisualVideoCompositor(
    _track_keyframes.TrackKeyframeUniversalVisualVideoCompositor
):
    """Wave 9 track-opacity compositor without regressing universal track-effect automation."""

    def render_video_advanced(self, sequence_id: str) -> EditorExportResult:
        result = super().render_video_advanced(sequence_id)
        return _mark_track_effect_keyframe_truth(result, self.project_dir)


UniversalVisualVideoCompositor = TrackKeyframeUniversalVisualVideoCompositor
TrackOpacityKeyframedGroupedTrackVideoCompositor = (
    _track_keyframes.TrackOpacityKeyframedGroupedTrackVideoCompositor
)
_track_opacity_keyframes = _track_keyframes._track_opacity_keyframes


__all__ = [
    "TrackKeyframeUniversalVisualVideoCompositor",
    "TrackOpacityKeyframedGroupedTrackVideoCompositor",
    "UniversalVisualVideoCompositor",
    "_track_opacity_keyframes",
]
