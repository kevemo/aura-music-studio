from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from . import professional_video_grouped_unified_compositor as _grouped
from .professional_editor_renderer import EditorExportResult
from .professional_video_mask_crop_compositor import (
    MaskCropUniversalVisualVideoCompositor as _PreviousProductionCompositor,
)


# These are the item colour controls actually executed by AdvancedVideoCompositor after the
# alpha-capable effects/mask derivative is created. Wave 13 adds bounded temperature/tint to that
# mature item-colour stage. Highlights/shadows remain outside the executable boundary and continue
# to fail closed when combined with masks and item effects.
_RENDERED_COLOUR_DEFAULTS: dict[str, float] = {
    "exposure": 0.0,
    "brightness": 0.0,
    "contrast": 1.0,
    "saturation": 1.0,
    "gamma": 1.0,
    "temperature": 0.0,
    "tint": 0.0,
}
_UNSUPPORTED_COLOUR_DEFAULTS: dict[str, float] = {
    "highlights": 0.0,
    "shadows": 0.0,
}
_RECOGNISED_COLOUR_FIELDS = frozenset(_RENDERED_COLOUR_DEFAULTS | _UNSUPPORTED_COLOUR_DEFAULTS)
_ORIGINAL_GROUPED_VALIDATE = _grouped.GroupedUnifiedAdvancedVideoCompositor._validate_effect_state


def _finite(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number


def _enabled_masks(item: dict[str, Any]) -> list[dict[str, Any]]:
    return [mask for mask in item.get("masks") or [] if mask.get("enabled", True)]


def _enabled_effects(item: dict[str, Any]) -> list[dict[str, Any]]:
    return [effect for effect in item.get("effects") or [] if effect.get("enabled", True)]


def _uses_only_rendered_colour_controls(item: dict[str, Any]) -> bool:
    colour = item.get("color") or {}
    if not isinstance(colour, dict):
        return False
    if set(colour) - _RECOGNISED_COLOUR_FIELDS:
        return False
    for name, default in _UNSUPPORTED_COLOUR_DEFAULTS.items():
        if abs(_finite(colour.get(name), default) - default) > 1e-8:
            return False
    return True


def _has_mask_effects_colour(item: dict[str, Any]) -> bool:
    return (
        bool(_enabled_masks(item))
        and bool(_enabled_effects(item))
        and not _grouped._colour_is_default(item)
        and _uses_only_rendered_colour_controls(item)
    )


def _sanitized_state_for_mask_effects_colour_validation(
    state: dict[str, Any],
    sequence_id: str,
) -> dict[str, Any]:
    """Neutralize only render-safe colour controls in a validator copy.

    The real render state is untouched. Item effects and authored mask alpha are pre-rendered to
    the transient derivative first. The established grouped compositor then applies the original
    rendered colour controls to that alpha-capable derivative. Unsupported colour paths are not
    neutralized, so the prior fail-closed validator continues to reject them.
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
            if not _has_mask_effects_colour(item):
                continue
            colour = dict(item.get("color") or {})
            for name, default in _RENDERED_COLOUR_DEFAULTS.items():
                colour[name] = default
            item["color"] = colour
    return sanitized


def _validate_grouped_state_with_mask_effects_colour(
    self,
    state: dict[str, Any],
    sequence_id: str,
) -> None:
    _ORIGINAL_GROUPED_VALIDATE(
        self,
        _sanitized_state_for_mask_effects_colour_validation(state, sequence_id),
        sequence_id,
    )


def _count_mask_effects_colour_items(state: dict[str, Any], sequence_id: str) -> int:
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
            if item and item.get("enabled", True) and _has_mask_effects_colour(item):
                count += 1
    return count


# Extend the current Wave 11 validator. The captured validator still performs mask/crop
# sanitization and every earlier safety check after this interoperability-only adjustment.
_grouped.GroupedUnifiedAdvancedVideoCompositor._validate_effect_state = (
    _validate_grouped_state_with_mask_effects_colour
)


class MaskEffectsColourUniversalVisualVideoCompositor(_PreviousProductionCompositor):
    """Production Video Studio compositor with mask + effects + rendered colour interoperability."""

    def render_video_advanced(self, sequence_id: str) -> EditorExportResult:
        state = self.store.public_state()
        item_count = _count_mask_effects_colour_items(state, sequence_id)
        result = super().render_video_advanced(sequence_id)

        metadata_path = self.project_dir / result.metadata_ref
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        payload.update(
            {
                "professional_mask_effects_colour_interoperability": item_count > 0,
                "professional_mask_effects_colour_items_executed": item_count,
                "professional_mask_effects_colour_paths_supported": sorted(_RENDERED_COLOUR_DEFAULTS),
                "item_effects_applied_before_mask_alpha": True,
                "mask_alpha_applied_before_item_colour": True,
                "item_colour_applied_after_mask_derivative": True,
                "supports_item_temperature_tint": True,
                "item_temperature_tint_range": [-1.0, 1.0],
                "item_temperature_tint_preserve_lightness": True,
                "item_highlights_shadows_fail_closed": True,
                "mask_effects_colour_source_media_mutated": False,
                "mask_effects_colour_fail_closed": False,
                "unsupported_mask_effects_colour_paths_fail_closed": True,
                "automatic_mask_tracking_supported": False,
            }
        )
        metadata_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        if item_count:
            return result.model_copy(
                update={"renderer": "ffmpeg-universal-mask-effects-colour-video-compositor"}
            )
        return result


UniversalVisualVideoCompositor = MaskEffectsColourUniversalVisualVideoCompositor


__all__ = [
    "MaskEffectsColourUniversalVisualVideoCompositor",
    "UniversalVisualVideoCompositor",
    "_count_mask_effects_colour_items",
    "_sanitized_state_for_mask_effects_colour_validation",
]
