from __future__ import annotations

import json
import shutil
from copy import deepcopy
from typing import Any
from uuid import uuid4

from .professional_editor_renderer import EditorExportResult, EditorRenderUnsupported
from .professional_video_compositor import _SUPPORTED_VIDEO_ITEM_BLEND_MODES
from .professional_video_effects_compositor import (
    _SUPPORTED_STATIC_MASK_SHAPES,
    _SUPPORTED_VIDEO_EFFECTS,
    _StateProxy,
    _colour_is_default,
    _crop_is_default,
    _finite,
    _video_effect_filter,
)
from .professional_video_track_compositor import (
    GroupedTrackVideoCompositor,
    _SUPPORTED_VIDEO_TRACK_BLEND_MODES,
)
from .professional_video_unified_compositor import UnifiedAdvancedVideoCompositor


class GroupedUnifiedAdvancedVideoCompositor(UnifiedAdvancedVideoCompositor):
    """Render safe item-local state first, then compose complete visual tracks as groups."""

    def _validate_effect_state(self, state: dict[str, Any], sequence_id: str) -> None:
        sequences, tracks, items = self._branch_maps(state)
        sequence = sequences.get(sequence_id)
        if sequence is None:
            raise KeyError(sequence_id)

        for track_id in sequence.get("track_ids", []):
            track = tracks.get(track_id)
            if not track or not track.get("enabled", True):
                continue
            track_effects = [effect for effect in track.get("effects") or [] if effect.get("enabled", True)]
            for effect in track_effects:
                _video_effect_filter(effect)
            if track.get("keyframes"):
                raise EditorRenderUnsupported("Video track keyframes require the next grouped-track automation stage")
            track_blend = str(track.get("blend_mode") or "normal").strip().lower()
            if track_blend not in _SUPPORTED_VIDEO_TRACK_BLEND_MODES:
                raise EditorRenderUnsupported(f"Video track blend mode is not render-safe: {track_blend}")

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

    def render_video_advanced(self, sequence_id: str) -> EditorExportResult:
        real_store = self.store
        original_state = real_store.public_state()
        self._validate_effect_state(original_state, sequence_id)
        state = deepcopy(original_state)
        sequences, tracks, items = self._branch_maps(state)
        sequence = sequences.get(sequence_id)
        if sequence is None:
            raise KeyError(sequence_id)

        derived_root = self.project_dir / "work" / "editor_video_grouped_effects" / uuid4().hex
        original_refs: set[str] = set()
        derived_count = 0
        masked_count = 0
        try:
            for track_id in sequence.get("track_ids", []):
                track = tracks.get(track_id)
                if not track or not track.get("enabled", True):
                    continue
                for item_id in track.get("item_ids", []):
                    item = items.get(item_id)
                    if not item or not item.get("enabled", True):
                        continue
                    effects = [effect for effect in item.get("effects") or [] if effect.get("enabled", True)]
                    masks = [mask for mask in item.get("masks") or [] if mask.get("enabled", True)]
                    pan = max(-1.0, min(1.0, _finite((item.get("audio") or {}).get("pan"), 0.0)))
                    source_ref = str(item.get("source_ref") or "").strip()
                    if source_ref:
                        original_refs.add(source_ref)

                    if item.get("kind") in {"image_layer", "text"} and (effects or masks):
                        item["source_ref"] = self._derive_still(item, derived_root)
                        item["kind"] = "image_layer"
                        item["effects"] = []
                        item["masks"] = []
                        derived_count += 1
                        masked_count += int(bool(masks))
                    elif item.get("kind") == "video_clip" and (effects or masks or abs(pan) > 1e-8):
                        item["source_ref"] = self._derive_video(item, derived_root, force_stereo=abs(pan) > 1e-8)
                        item["source_in"] = 0.0
                        item["effects"] = []
                        item["masks"] = []
                        derived_count += 1
                        masked_count += int(bool(masks))
                    elif item.get("kind") == "audio_clip" and abs(pan) > 1e-8:
                        item["source_ref"] = self._derive_audio(item, derived_root)
                        item["source_in"] = 0.0
                        derived_count += 1
                    elif effects or masks:
                        raise EditorRenderUnsupported(
                            f"Effects or masks are not render-safe for video item kind: {item.get('kind')}"
                        )

            delegate = GroupedTrackVideoCompositor(self.project_dir)
            delegate.store = _StateProxy(state)  # type: ignore[assignment]
            result = delegate.render_video_advanced(sequence_id)

            metadata = self.project_dir / result.metadata_ref
            payload = json.loads(metadata.read_text(encoding="utf-8"))
            payload.update({
                "grouped_unified_advanced_video_compositor": True,
                "video_item_effects_compositor": True,
                "supported_video_item_effects": sorted(_SUPPORTED_VIDEO_EFFECTS),
                "supports_static_video_masks": True,
                "supported_static_mask_shapes": sorted(_SUPPORTED_STATIC_MASK_SHAPES),
                "supports_mask_feather_expansion": True,
                "tracked_or_keyframed_masks_fail_closed": True,
                "transient_derivatives": derived_count,
                "transient_mask_derivatives": masked_count,
                "transient_derivatives_ephemeral": True,
                "original_source_refs": sorted(original_refs),
                "source_refs": sorted(original_refs),
                "mono_pan_stereo_normalization": True,
                "supports_item_blend_modes": sorted(_SUPPORTED_VIDEO_ITEM_BLEND_MODES),
                "supports_track_blend_modes": sorted(_SUPPORTED_VIDEO_TRACK_BLEND_MODES),
                "supports_track_effects": sorted(_SUPPORTED_VIDEO_EFFECTS),
                "track_effects_applied_before_opacity_and_blend": True,
                "track_opacity_applied_after_item_composition": True,
                "track_opacity_applied_after_track_effects": True,
                "track_effects_fail_closed": False,
                "keyframed_track_effects_fail_closed": True,
                "track_keyframes_fail_closed": True,
                "source_media_mutated": False,
                "unsupported_state_fails_closed": True,
            })
            metadata.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            if masked_count:
                return result.model_copy(update={"renderer": "ffmpeg-grouped-unified-mask-compositor"})
            if derived_count:
                return result.model_copy(update={"renderer": "ffmpeg-grouped-unified-effects-compositor"})
            return result
        finally:
            self.store = real_store
            shutil.rmtree(derived_root, ignore_errors=True)


__all__ = ["GroupedUnifiedAdvancedVideoCompositor"]