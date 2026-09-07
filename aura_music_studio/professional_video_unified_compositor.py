from __future__ import annotations

from typing import Any

from .professional_editor_renderer import EditorRenderUnsupported
from .professional_video_compositor import _SUPPORTED_VIDEO_ITEM_BLEND_MODES
from .professional_video_effects_compositor import (
    _SUPPORTED_STATIC_MASK_SHAPES,
    VideoItemEffectsCompositor,
    _colour_is_default,
    _crop_is_default,
    _video_effect_filter,
)


class UnifiedAdvancedVideoCompositor(VideoItemEffectsCompositor):
    """Render the currently safe professional Video item stack through one path.

    The effects/mask compositor already pre-renders supported item-local state into transient
    derivatives, then delegates final timeline composition to AdvancedVideoCompositor. That base
    compositor now understands item blend modes, so this subclass only widens the validation gate
    to the same explicit blend contract. Existing mask/effect truth boundaries remain unchanged.
    """

    def _validate_effect_state(self, state: dict[str, Any], sequence_id: str) -> None:
        sequences, tracks, items = self._branch_maps(state)
        sequence = sequences.get(sequence_id)
        if sequence is None:
            raise KeyError(sequence_id)

        for track_id in sequence.get("track_ids", []):
            track = tracks.get(track_id)
            if not track or not track.get("enabled", True):
                continue
            if track.get("effects"):
                raise EditorRenderUnsupported("Video track effects are not yet render-safe")
            if track.get("keyframes"):
                raise EditorRenderUnsupported("Video track keyframes are not yet render-safe")
            if track.get("blend_mode", "normal") != "normal":
                raise EditorRenderUnsupported(
                    "Non-normal video track blend modes require grouped track compositing and remain fail-closed"
                )

            for item_id in track.get("item_ids", []):
                item = items.get(item_id)
                if not item or not item.get("enabled", True):
                    continue

                blend_mode = str(item.get("blend_mode") or "normal").strip().lower()
                if blend_mode not in _SUPPORTED_VIDEO_ITEM_BLEND_MODES:
                    raise EditorRenderUnsupported(f"Video item blend mode is not render-safe: {blend_mode}")

                effects = [effect for effect in item.get("effects") or [] if effect.get("enabled", True)]
                masks = [mask for mask in item.get("masks") or [] if mask.get("enabled", True)]

                for effect in effects:
                    _video_effect_filter(effect)

                for mask in masks:
                    shape = str(mask.get("shape") or "").lower()
                    if shape not in _SUPPORTED_STATIC_MASK_SHAPES:
                        raise EditorRenderUnsupported(f"Unsupported static video mask shape: {shape or 'unnamed'}")
                    if mask.get("tracking"):
                        raise EditorRenderUnsupported("Tracked video masks are not yet render-safe")
                    if mask.get("keyframes"):
                        raise EditorRenderUnsupported("Keyframed video masks are not yet render-safe")

                if masks and not _crop_is_default(item):
                    raise EditorRenderUnsupported("Video masks combined with crop are not yet render-safe")
                if masks and effects and not _colour_is_default(item):
                    raise EditorRenderUnsupported(
                        "Video masks combined with item effects and colour adjustments are not yet render-safe"
                    )


__all__ = ["UnifiedAdvancedVideoCompositor"]
