from __future__ import annotations

import json
import shutil
import subprocess
from copy import deepcopy
from pathlib import Path
from typing import Any
from uuid import uuid4

from . import professional_video_effects_compositor as _effects_module
from . import professional_video_grouped_unified_compositor as _grouped_module
from . import professional_video_track_compositor as _track_module
from .professional_editor_renderer import EditorExportResult, EditorRenderError, EditorRenderUnsupported
from .professional_video_compositor import _finite, _keyframe_expr
from .professional_video_effects_compositor import _StateProxy
from .professional_video_grouped_unified_compositor import GroupedUnifiedAdvancedVideoCompositor
from .professional_universal_visual_video_compositor import (
    SUPPORTED_UNIVERSAL_VIDEO_EFFECTS,
    UniversalVisualVideoCompositor as _ItemUniversalVisualVideoCompositor,
    _clamp,
    _contract_type,
    _ff,
    _universal_visual_filter as _static_universal_visual_filter,
)


# Universal Video Studio automation deliberately starts with the controls that FFmpeg can evaluate
# per frame without approximating an authored curve. Mix is safe for every supported universal
# visual effect because it is evaluated by FFmpeg's blend expression. The basic grade's exposure,
# contrast and saturation are also safe because eq supports frame-evaluated expressions. Other
# animated parameters remain fail-closed until a renderer can execute their parameter semantics
# directly rather than interpolating already-rendered pictures.
_ANIMATABLE_UNIVERSAL_PARAMETERS: dict[str, frozenset[str]] = {
    "video.grade.basic": frozenset({"exposure", "contrast", "saturation"}),
    "video.fx.blur": frozenset(),
    "video.fx.vignette": frozenset(),
    "video.fx.film_grain": frozenset(),
    "video.fx.chromatic_aberration": frozenset(),
}


# The mature grouped compositor imported the legacy filter dispatcher by value in two modules.
# Install one deterministic process-wide dispatcher after those modules are loaded so namespaced
# universal contracts can execute at their native grouped-track stage. This is a startup adapter,
# not a per-render monkey patch: it is installed once and then remains immutable for the process.
_ORIGINAL_VIDEO_EFFECT_FILTER = getattr(
    _effects_module._video_effect_filter,
    "_aura_original_video_effect_filter",
    _effects_module._video_effect_filter,
)
_ORIGINAL_APPEND_TRACK_EFFECTS = getattr(
    _track_module._append_track_effects,
    "_aura_original_append_track_effects",
    _track_module._append_track_effects,
)


def _bounded_keyframe_points(
    effect: dict[str, Any],
    name: str,
    *,
    default: float,
    low: float,
    high: float,
) -> list[dict[str, Any]]:
    raw = (effect.get("keyframes") or {}).get(name) or []
    if not isinstance(raw, list):
        raise EditorRenderUnsupported(f"Universal video effect keyframes for {name} must be a list")
    clean: list[dict[str, Any]] = []
    for point in raw:
        if not isinstance(point, dict):
            continue
        clean.append(
            {
                "time": max(0.0, _finite(point.get("time"), 0.0)),
                "value": max(low, min(high, _finite(point.get("value"), default))),
                "interpolation": str(point.get("interpolation") or "linear").lower(),
            }
        )
    return clean


def _bounded_keyframe_expr(
    effect: dict[str, Any],
    name: str,
    *,
    default: float,
    low: float,
    high: float,
    variable: str,
) -> tuple[str, bool]:
    bounded_default = max(low, min(high, _finite(default, default)))
    points = _bounded_keyframe_points(effect, name, default=bounded_default, low=low, high=high)
    if not points:
        return _ff(bounded_default), False
    expression = _keyframe_expr(points, bounded_default, variable=variable)
    return f"min({_ff(high)},max({_ff(low)},({expression})))", True


def _validate_universal_effect_keyframes(effect: dict[str, Any]) -> None:
    kind = _contract_type(effect)
    keyframes = effect.get("keyframes") or {}
    if not isinstance(keyframes, dict):
        raise EditorRenderUnsupported(f"Universal video effect keyframes must be a mapping: {kind}")
    allowed = {"mix", *_ANIMATABLE_UNIVERSAL_PARAMETERS.get(kind, frozenset())}
    unsupported = sorted(
        name
        for name, points in keyframes.items()
        if points and str(name) not in allowed
    )
    if unsupported:
        raise EditorRenderUnsupported(
            f"Universal video effect keyframe path is not yet render-safe for {kind}: "
            + ", ".join(unsupported)
        )


def _animated_universal_visual_filter(effect: dict[str, Any], *, time_variable: str = "t") -> str:
    kind = _contract_type(effect)
    if kind not in SUPPORTED_UNIVERSAL_VIDEO_EFFECTS:
        raise EditorRenderUnsupported(f"Unsupported universal video visual effect: {kind or 'unnamed'}")
    _validate_universal_effect_keyframes(effect)

    parameter_keyframes = {
        str(name)
        for name, points in (effect.get("keyframes") or {}).items()
        if points and str(name) != "mix"
    }
    if not parameter_keyframes:
        static_effect = deepcopy(effect)
        static_effect["keyframes"] = {}
        return _static_universal_visual_filter(static_effect)

    if kind != "video.grade.basic":
        # Guarded above; retained to make the execution boundary explicit if the catalogue grows.
        raise EditorRenderUnsupported(f"Animated universal video parameters are not yet render-safe: {kind}")

    params = effect.get("parameters") or {}
    exposure, _ = _bounded_keyframe_expr(
        effect,
        "exposure",
        default=_finite(params.get("exposure"), 0.0),
        low=-5.0,
        high=5.0,
        variable=time_variable,
    )
    contrast_delta, _ = _bounded_keyframe_expr(
        effect,
        "contrast",
        default=_finite(params.get("contrast"), 0.0),
        low=-1.0,
        high=1.0,
        variable=time_variable,
    )
    saturation_delta, _ = _bounded_keyframe_expr(
        effect,
        "saturation",
        default=_finite(params.get("saturation"), 0.0),
        low=-1.0,
        high=3.0,
        variable=time_variable,
    )
    temperature = _clamp(params.get("temperature"), -1.0, 1.0)
    tint = _clamp(params.get("tint"), -1.0, 1.0)
    warmth = temperature * 0.24
    green = -tint * 0.20

    # eq evaluates these expressions for every frame. Temperature/tint stay static in this wave;
    # asking to keyframe either is rejected above rather than silently flattening the animation.
    brightness = f"(({exposure})*0.08)"
    contrast = f"(1+({contrast_delta}))"
    saturation = f"(1+({saturation_delta}))"
    return (
        f"eq=brightness='{brightness}':contrast='{contrast}':saturation='{saturation}':eval=frame,"
        f"colorbalance=rm={_ff(warmth)}:gm={_ff(green)}:bm={_ff(-warmth)}:"
        f"rh={_ff(warmth)}:gh={_ff(green)}:bh={_ff(-warmth)}:pl=1"
    )


def _universal_or_legacy_video_effect_filter(effect: dict[str, Any]) -> str:
    raw_type = str(effect.get("type") or "").strip().lower()
    if raw_type in SUPPORTED_UNIVERSAL_VIDEO_EFFECTS:
        return _animated_universal_visual_filter(effect, time_variable="t")
    if raw_type.startswith("video."):
        raise EditorRenderUnsupported(f"Unsupported universal video visual effect: {raw_type or 'unnamed'}")
    return _ORIGINAL_VIDEO_EFFECT_FILTER(effect)


setattr(
    _universal_or_legacy_video_effect_filter,
    "_aura_original_video_effect_filter",
    _ORIGINAL_VIDEO_EFFECT_FILTER,
)


def _append_universal_track_effects(
    filters: list[str],
    *,
    input_label: str,
    effects: list[dict[str, Any]],
    track_index: int,
) -> tuple[str, int]:
    """Append track effects while allowing real sequence-time universal automation."""

    current = input_label
    applied = 0
    for effect_index, effect in enumerate(effects or [], start=1):
        if not effect.get("enabled", True):
            continue
        kind = _contract_type(effect)
        if kind in SUPPORTED_UNIVERSAL_VIDEO_EFFECTS:
            effect_filter = _animated_universal_visual_filter(effect, time_variable="t")
            mix_expr, mix_animated = _bounded_keyframe_expr(
                effect,
                "mix",
                default=_finite(effect.get("mix"), 1.0),
                low=0.0,
                high=1.0,
                variable="T",
            )
        else:
            effect_filter = _universal_or_legacy_video_effect_filter(effect)
            mix_expr, mix_animated = _ff(max(0.0, min(1.0, _finite(effect.get("mix"), 1.0)))), False

        if not mix_animated and float(mix_expr) <= 0.0:
            continue
        applied += 1
        out = f"track{track_index}fx{effect_index}"
        if not mix_animated and float(mix_expr) >= 1.0:
            filters.append(f"[{current}]{effect_filter},format=rgba[{out}]")
            current = out
            continue

        dry = f"track{track_index}fx{effect_index}dry"
        wet = f"track{track_index}fx{effect_index}wet"
        wet_fx = f"track{track_index}fx{effect_index}wetfx"
        filters.append(f"[{current}]split=2[{dry}][{wet}]")
        filters.append(f"[{wet}]{effect_filter},format=rgba[{wet_fx}]")
        filters.append(
            f"[{dry}][{wet_fx}]blend=all_expr='A*(1-({mix_expr}))+B*({mix_expr})',format=rgba[{out}]"
        )
        current = out
    return current, applied


setattr(
    _append_universal_track_effects,
    "_aura_original_append_track_effects",
    _ORIGINAL_APPEND_TRACK_EFFECTS,
)


def install_universal_video_effect_dispatch() -> None:
    """Install universal Video Studio effects into all grouped-render filter call sites.

    The grouped renderer already has the required professional order:
    item composition -> whole-track effects -> track opacity -> track blend. Reusing that stage
    avoids the incorrect shortcut of applying an adjustment/track effect independently to every
    source clip. Installation is idempotent and preserves the legacy dispatcher for non-universal
    effects while adding bounded sequence-time automation for universal effects.
    """

    _effects_module._video_effect_filter = _universal_or_legacy_video_effect_filter
    _track_module._video_effect_filter = _universal_or_legacy_video_effect_filter
    _grouped_module._video_effect_filter = _universal_or_legacy_video_effect_filter
    _track_module._append_track_effects = _append_universal_track_effects


install_universal_video_effect_dispatch()


def _universal_visual_graph(
    effects: list[dict[str, Any]],
    *,
    filter_time_variable: str,
    blend_time_variable: str,
) -> tuple[str | None, str, list[str]]:
    """Build an ordered wet/dry item-effect graph with editor-time keyframe evaluation."""

    active = [effect for effect in effects or [] if effect.get("enabled", True)]
    if not active:
        return None, "0:v", []

    filters: list[str] = []
    current = "0:v"
    applied: list[str] = []
    stage = 0
    for effect in active:
        kind = _contract_type(effect)
        if kind not in SUPPORTED_UNIVERSAL_VIDEO_EFFECTS:
            continue
        effect_filter = _animated_universal_visual_filter(effect, time_variable=filter_time_variable)
        mix_expr, mix_animated = _bounded_keyframe_expr(
            effect,
            "mix",
            default=_finite(effect.get("mix"), 1.0),
            low=0.0,
            high=1.0,
            variable=blend_time_variable,
        )
        if not mix_animated and float(mix_expr) <= 0.0:
            continue
        if effect_filter == "null":
            continue

        stage += 1
        applied.append(kind)
        out = f"uvfx{stage}"
        if not mix_animated and float(mix_expr) >= 1.0:
            filters.append(f"[{current}]{effect_filter}[{out}]")
            current = out
            continue

        dry, wet, wet_fx = f"uvdry{stage}", f"uvwet{stage}", f"uvwetfx{stage}"
        filters.append(f"[{current}]split=2[{dry}][{wet}]")
        filters.append(f"[{wet}]{effect_filter}[{wet_fx}]")
        filters.append(
            f"[{dry}][{wet_fx}]blend=all_expr='A*(1-({mix_expr}))+B*({mix_expr})'[{out}]"
        )
        current = out

    if stage == 0:
        return None, "0:v", []
    filters.append(f"[{current}]format=yuv420p[uvout]")
    return ";".join(filters), "uvout", applied


class UniversalVisualVideoCompositor(_ItemUniversalVisualVideoCompositor):
    """Execute universal visual contracts at item and whole-track scope, including safe automation."""

    def _derive_universal_clip(
        self,
        item: dict[str, Any],
        effects: list[dict[str, Any]],
        derived_root: Path,
    ) -> tuple[str, list[str]]:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise EditorRenderUnsupported("FFmpeg is unavailable on this runtime")
        source = self._source(item.get("source_ref"))
        output = derived_root / f"{item['id']}.mp4"
        output.parent.mkdir(parents=True, exist_ok=True)
        source_in = max(0.0, _finite(item.get("source_in"), 0.0))
        speed = max(0.05, min(20.0, _finite(item.get("speed"), 1.0)))
        item_duration = max(0.01, _finite(item.get("duration"), 0.01))
        duration = item_duration * speed
        start = max(0.0, _finite(item.get("start"), 0.0))

        # Derived clips are rendered before the grouped compositor applies speed/reverse. Convert
        # derivative-local FFmpeg time back to the editor's sequence time so effect automation
        # remains aligned with the timeline in both normal and reversed clips.
        if item.get("reverse"):
            filter_time = f"({_ff(start + item_duration)}-t/{_ff(speed)})"
            blend_time = f"({_ff(start + item_duration)}-T/{_ff(speed)})"
        else:
            filter_time = f"({_ff(start)}+t/{_ff(speed)})"
            blend_time = f"({_ff(start)}+T/{_ff(speed)})"

        graph, label, applied = _universal_visual_graph(
            effects,
            filter_time_variable=filter_time,
            blend_time_variable=blend_time,
        )
        if graph is None or not applied:
            raise EditorRenderUnsupported("Universal visual derivative requested without an executable effect")

        args = [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{source_in:.8f}",
            "-t",
            f"{duration:.8f}",
            "-i",
            str(source),
            "-filter_complex",
            graph,
            "-map",
            f"[{label}]",
            "-map",
            "0:a?",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "16",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
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
            detail = (completed.stderr or "FFmpeg produced no visual derivative").strip().splitlines()[-1][:500]
            raise EditorRenderError(f"Universal visual effect pre-render failed: {detail}")
        return output.relative_to(self.project_dir).as_posix(), applied

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
        automated_effect_count = 0
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

                for effect in track_effects:
                    kind = _contract_type(effect)
                    if kind not in SUPPORTED_UNIVERSAL_VIDEO_EFFECTS:
                        continue
                    _animated_universal_visual_filter(effect, time_variable="t")
                    mix_expr, mix_animated = _bounded_keyframe_expr(
                        effect,
                        "mix",
                        default=_finite(effect.get("mix"), 1.0),
                        low=0.0,
                        high=1.0,
                        variable="T",
                    )
                    if not mix_animated and float(mix_expr) <= 0.0:
                        continue
                    applied_ids.append(kind)
                    applied_scopes.append("track")
                    track_effect_count += 1
                    automated_effect_count += int(bool(effect.get("keyframes")))

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
                    automated_effect_count += sum(bool(effect.get("keyframes")) for effect in universal)
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
                    "universal_visual_automated_effect_instances_executed": automated_effect_count,
                    "universal_visual_transient_derivatives": derivative_count,
                    "universal_visual_transient_derivatives_ephemeral": True,
                    "supported_universal_video_visual_effects": sorted(SUPPORTED_UNIVERSAL_VIDEO_EFFECTS),
                    "supported_universal_video_visual_effect_scopes": ["item", "track"],
                    "universal_visual_numeric_keyframes_supported": {
                        "all_supported_effects": ["mix"],
                        "video.grade.basic": ["exposure", "contrast", "saturation"],
                    },
                    "universal_visual_unsupported_keyframe_paths_fail_closed": True,
                    "universal_visual_effect_keyframes_fail_closed": False,
                    "universal_visual_track_effects_fail_closed": False,
                    "universal_visual_track_effects_applied_after_item_composition": True,
                    "universal_visual_track_effects_applied_before_track_opacity_and_blend": True,
                    "universal_visual_item_effect_keyframes_use_sequence_time": True,
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
    "_animated_universal_visual_filter",
    "_append_universal_track_effects",
    "_universal_or_legacy_video_effect_filter",
    "_universal_visual_graph",
]
