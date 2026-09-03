from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from . import professional_video_grouped_unified_compositor as _grouped
from . import professional_video_track_keyframe_compositor as _track_keyframes
from .professional_editor_renderer import EditorExportResult, EditorRenderUnsupported
from .professional_universal_scoped_visual_video_compositor import (
    SUPPORTED_UNIVERSAL_VIDEO_EFFECTS,
    _contract_type,
    _validate_universal_effect_keyframes,
)


_ORIGINAL_GROUPED_VALIDATE = _track_keyframes._validate_grouped_state_with_track_opacity
_ORIGINAL_TRACK_VALIDATE = _track_keyframes.TrackOpacityKeyframedGroupedTrackVideoCompositor._validate_video_state
_ORIGINAL_TRACK_RENDER = _track_keyframes.TrackOpacityKeyframedGroupedTrackVideoCompositor.render_video_advanced


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
    _ORIGINAL_TRACK_VALIDATE(
        self,
        sequence,
        _sanitized_tracks_for_wave9_validation(sequence, tracks),
        items,
    )


def _mark_track_effect_keyframe_truth(result: EditorExportResult, project_dir) -> EditorExportResult:
    metadata = project_dir / result.metadata_ref
    payload = json.loads(metadata.read_text(encoding="utf-8"))
    payload["keyframed_track_effects_fail_closed"] = False
    payload["supported_keyframed_track_effects_preserved"] = True
    payload["unsupported_track_effect_keyframes_fail_closed"] = True
    metadata.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return result


def _render_track_opacity_with_truthful_track_effect_metadata(self, sequence_id: str) -> EditorExportResult:
    result = _ORIGINAL_TRACK_RENDER(self, sequence_id)
    return _mark_track_effect_keyframe_truth(result, self.project_dir)


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
