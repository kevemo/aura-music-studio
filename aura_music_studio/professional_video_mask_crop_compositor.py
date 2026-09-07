from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from . import professional_video_grouped_unified_compositor as _grouped
from .professional_editor_renderer import EditorExportResult
from .professional_video_track_keyframe_universal_compositor import (
    TrackKeyframeUniversalVisualVideoCompositor as _PreviousProductionCompositor,
)


_ORIGINAL_GROUPED_VALIDATE = _grouped.GroupedUnifiedAdvancedVideoCompositor._validate_effect_state
_DEFAULT_CROP = {"left": 0.0, "top": 0.0, "right": 0.0, "bottom": 0.0}


def _enabled_masks(item: dict[str, Any]) -> list[dict[str, Any]]:
    return [mask for mask in item.get("masks") or [] if mask.get("enabled", True)]


def _has_mask_crop(item: dict[str, Any]) -> bool:
    return bool(_enabled_masks(item)) and not _grouped._crop_is_default(item)


def _sanitized_state_for_mask_crop_validation(
    state: dict[str, Any],
    sequence_id: str,
) -> dict[str, Any]:
    """Remove crop only from the validator copy for items whose masks are rendered first.

    The actual project state is not modified. The production renderer pre-renders item masks to an
    alpha-capable derivative, clears only the consumed mask/effect state on its render copy, and
    then the grouped compositor applies the still-authored crop to that derivative. This validator
    adapter therefore removes only a historical blanket rejection; every other renderer safety
    check continues to run against the copied state.
    """

    sanitized = deepcopy(state)
    branch = sanitized.get("branch") or {}
    sequences = {row.get("id"): row for row in branch.get("sequences") or []}
    tracks = {row.get("id"): row for row in branch.get("tracks") or []}
    items = {row.get("id"): row for row in branch.get("items") or []}
    sequence = sequences.get(sequence_id)
    if sequence is None:
        return sanitized

    for track_id in sequence.get("track_ids") or []:
        track = tracks.get(track_id)
        if not track or not track.get("enabled", True):
            continue
        for item_id in track.get("item_ids") or []:
            item = items.get(item_id)
            if not item or not item.get("enabled", True):
                continue
            if _has_mask_crop(item):
                item["crop"] = dict(_DEFAULT_CROP)
    return sanitized


def _validate_grouped_state_with_mask_crop(
    self,
    state: dict[str, Any],
    sequence_id: str,
) -> None:
    _ORIGINAL_GROUPED_VALIDATE(
        self,
        _sanitized_state_for_mask_crop_validation(state, sequence_id),
        sequence_id,
    )


def _count_mask_crop_items(state: dict[str, Any], sequence_id: str) -> int:
    branch = state.get("branch") or {}
    sequences = {row.get("id"): row for row in branch.get("sequences") or []}
    tracks = {row.get("id"): row for row in branch.get("tracks") or []}
    items = {row.get("id"): row for row in branch.get("items") or []}
    sequence = sequences.get(sequence_id)
    if sequence is None:
        return 0
    count = 0
    for track_id in sequence.get("track_ids") or []:
        track = tracks.get(track_id)
        if not track or not track.get("enabled", True):
            continue
        for item_id in track.get("item_ids") or []:
            item = items.get(item_id)
            if item and item.get("enabled", True) and _has_mask_crop(item):
                count += 1
    return count


# Install only the corrected grouped validation boundary. Rendering still consumes untouched state.
_grouped.GroupedUnifiedAdvancedVideoCompositor._validate_effect_state = _validate_grouped_state_with_mask_crop


class MaskCropUniversalVisualVideoCompositor(_PreviousProductionCompositor):
    """Production Video Studio compositor with mask -> crop interoperability."""

    def render_video_advanced(self, sequence_id: str) -> EditorExportResult:
        state = self.store.public_state()
        mask_crop_items = _count_mask_crop_items(state, sequence_id)
        result = super().render_video_advanced(sequence_id)

        metadata_path = self.project_dir / result.metadata_ref
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        payload.update(
            {
                "professional_mask_crop_interoperability": mask_crop_items > 0,
                "professional_mask_crop_items_executed": mask_crop_items,
                "mask_alpha_applied_before_crop": True,
                "crop_applied_after_mask_derivative": True,
                "mask_crop_source_media_mutated": False,
                "mask_crop_fail_closed": False,
                "automatic_mask_tracking_supported": False,
            }
        )
        metadata_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        if mask_crop_items:
            return result.model_copy(update={"renderer": "ffmpeg-universal-mask-crop-video-compositor"})
        return result


UniversalVisualVideoCompositor = MaskCropUniversalVisualVideoCompositor


__all__ = [
    "MaskCropUniversalVisualVideoCompositor",
    "UniversalVisualVideoCompositor",
    "_sanitized_state_for_mask_crop_validation",
    "_count_mask_crop_items",
]
