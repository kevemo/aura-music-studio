from __future__ import annotations

import json
import shutil
from copy import deepcopy
from typing import Any
from uuid import uuid4

from . import professional_video_effects_compositor as _effects_module
from . import professional_video_grouped_unified_compositor as _grouped_module
from . import professional_video_track_compositor as _track_module
from .professional_editor_renderer import EditorExportResult, EditorRenderUnsupported
from .professional_video_effects_compositor import _StateProxy
from .professional_video_grouped_unified_compositor import GroupedUnifiedAdvancedVideoCompositor
from .professional_universal_visual_video_compositor import (
    SUPPORTED_UNIVERSAL_VIDEO_EFFECTS,
    UniversalVisualVideoCompositor as _ItemUniversalVisualVideoCompositor,
    _contract_type,
    _universal_visual_filter,
)


# The mature grouped compositor imported the legacy filter dispatcher by value in two modules.
# Install one deterministic process-wide dispatcher after those modules are loaded so namespaced
# universal contracts can execute at their native grouped-track stage. This is a startup adapter,
# not a per-render monkey patch: it is installed once and then remains immutable for the process.
_ORIGINAL_VIDEO_EFFECT_FILTER = getattr(
    _effects_module._video_effect_filter,
    "_aura_original_video_effect_filter",
    _effects_module._video_effect_filter,
)


def _universal_or_legacy_video_effect_filter(effect: dict[str, Any]) -> str:
    raw_type = str(effect.get("type") or "").strip().lower()
    if raw_type in SUPPORTED_UNIVERSAL_VIDEO_EFFECTS:
        return _universal_visual_filter(effect)
    if raw_type.startswith("video."):
        raise EditorRenderUnsupported(f"Unsupported universal video visual effect: {raw_type or 'unnamed'}")
    return _ORIGINAL_VIDEO_EFFECT_FILTER(effect)


setattr(
    _universal_or_legacy_video_effect_filter,
    "_aura_original_video_effect_filter",
    _ORIGINAL_VIDEO_EFFECT_FILTER,
)


def install_universal_video_effect_dispatch() -> None:
    """Install universal Video Studio effects into all grouped-render filter call sites.

    The grouped renderer already has the required professional order:
    item composition -> whole-track effects -> track opacity -> track blend. Reusing that stage
    avoids the incorrect shortcut of applying an adjustment/track effect independently to every
    source clip. Installation is idempotent and does not widen the supported effect catalogue.
    """

    _effects_module._video_effect_filter = _universal_or_legacy_video_effect_filter
    _track_module._video_effect_filter = _universal_or_legacy_video_effect_filter
    _grouped_module._video_effect_filter = _universal_or_legacy_video_effect_filter


install_universal_video_effect_dispatch()


class UniversalVisualVideoCompositor(_ItemUniversalVisualVideoCompositor):
    """Execute universal visual contracts at both item and whole-track scope.

    Item-local effects retain the established project-confined transient derivative path. Track
    effects remain attached to the authored track so the grouped FFmpeg compositor executes them
    only after every item on that track has been composed. Source assets and persisted editor state
    remain unchanged. Effect-parameter keyframes intentionally remain fail-closed until the next
    automation wave can render their interpolation exactly rather than approximating it silently.
    """

    def render_video_advanced(self, sequence_id: str) -> EditorExportResult:
        real_store = self.store
        original_state = real_store.public_state()
        state = deepcopy(original_state)
        sequences, tracks, items = self._branch_maps(state)
        sequence = sequences.get(sequence_id)
        if sequence is None:
            raise KeyError(sequence_id)
        if sequence.get("kind") != "video":
            raise ValueError("Universal visual compositor requires a video sequence")

        derived_root = self.project_dir / "work" / "editor_universal_visual_effects" / uuid4().hex
        applied_ids: list[str] = []
        applied_scopes: list[str] = []
        original_refs: set[str] = set()
        derivative_count = 0
        track_effect_count = 0
        try:
            for track_id in sequence.get("track_ids", []):
                track = tracks.get(track_id)
                if not track or not track.get("enabled", True):
                    continue

                track_effects = [effect for effect in track.get("effects") or [] if effect.get("enabled", True)]
                unknown_track_contracts = sorted(
                    {
                        _contract_type(effect)
                        for effect in track_effects
                        if _contract_type(effect).startswith("video.")
                        and _contract_type(effect) not in SUPPORTED_UNIVERSAL_VIDEO_EFFECTS
                    }
                )
                if unknown_track_contracts:
                    raise EditorRenderUnsupported(
                        "Unsupported universal video visual effect: " + ", ".join(unknown_track_contracts)
                    )

                # Validate the exact universal contracts here before delegation. In particular,
                # effect keyframes still fail closed rather than being flattened to one value.
                for effect in track_effects:
                    kind = _contract_type(effect)
                    if kind not in SUPPORTED_UNIVERSAL_VIDEO_EFFECTS:
                        continue
                    _universal_visual_filter(effect)
                    mix = max(0.0, min(1.0, float(effect.get("mix", 1.0) or 0.0)))
                    if mix <= 0.0:
                        continue
                    applied_ids.append(kind)
                    applied_scopes.append("track")
                    track_effect_count += 1

                for item_id in track.get("item_ids", []):
                    item = items.get(item_id)
                    if not item or not item.get("enabled", True):
                        continue
                    effects = list(item.get("effects") or [])
                    universal = [
                        effect
                        for effect in effects
                        if effect.get("enabled", True) and _contract_type(effect) in SUPPORTED_UNIVERSAL_VIDEO_EFFECTS
                    ]
                    unknown_namespaced = sorted(
                        {
                            _contract_type(effect)
                            for effect in effects
                            if effect.get("enabled", True)
                            and _contract_type(effect).startswith("video.")
                            and _contract_type(effect) not in SUPPORTED_UNIVERSAL_VIDEO_EFFECTS
                        }
                    )
                    if unknown_namespaced:
                        raise EditorRenderUnsupported(
                            "Unsupported universal video visual effect: " + ", ".join(unknown_namespaced)
                        )
                    if not universal:
                        continue
                    if item.get("kind") != "video_clip":
                        raise EditorRenderUnsupported(
                            f"Universal video visual effects require a video clip, not {item.get('kind')}"
                        )
                    source_ref = str(item.get("source_ref") or "").strip()
                    if source_ref:
                        original_refs.add(source_ref)
                    derived_ref, applied = self._derive_universal_clip(item, universal, derived_root)
                    item["source_ref"] = derived_ref
                    item["source_in"] = 0.0
                    item["effects"] = [effect for effect in effects if effect not in universal]
                    applied_ids.extend(applied)
                    applied_scopes.extend(["item"] * len(applied))
                    derivative_count += 1

            delegate = GroupedUnifiedAdvancedVideoCompositor(self.project_dir)
            delegate.store = _StateProxy(state)  # type: ignore[assignment]
            result = delegate.render_video_advanced(sequence_id)

            metadata_path = self.project_dir / result.metadata_ref
            payload = json.loads(metadata_path.read_text(encoding="utf-8"))
            inherited_refs = {
                str(value)
                for value in payload.get("source_refs") or []
                if str(value).strip() and not str(value).startswith("work/editor_universal_visual_effects/")
            }
            inherited_refs.update(original_refs)
            payload.update(
                {
                    "universal_visual_video_compositor": True,
                    "universal_visual_effect_contracts_executed": sorted(set(applied_ids)),
                    "universal_visual_effect_instances_executed": len(applied_ids),
                    "universal_visual_effect_scopes_executed": sorted(set(applied_scopes)),
                    "universal_visual_track_effect_instances_executed": track_effect_count,
                    "universal_visual_transient_derivatives": derivative_count,
                    "universal_visual_transient_derivatives_ephemeral": True,
                    "supported_universal_video_visual_effects": sorted(SUPPORTED_UNIVERSAL_VIDEO_EFFECTS),
                    "supported_universal_video_visual_effect_scopes": ["item", "track"],
                    "universal_visual_effect_keyframes_fail_closed": True,
                    "universal_visual_track_effects_fail_closed": False,
                    "universal_visual_track_effects_applied_after_item_composition": True,
                    "universal_visual_track_effects_applied_before_track_opacity_and_blend": True,
                    "source_refs": sorted(inherited_refs),
                    "original_source_refs": sorted(inherited_refs),
                    "source_media_mutated": False,
                }
            )
            metadata_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            return result.model_copy(update={"renderer": "ffmpeg-universal-scoped-visual-video-compositor"})
        finally:
            self.store = real_store
            shutil.rmtree(derived_root, ignore_errors=True)


__all__ = [
    "UniversalVisualVideoCompositor",
    "install_universal_video_effect_dispatch",
    "_universal_or_legacy_video_effect_filter",
]
