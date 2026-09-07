from __future__ import annotations

import json
import math
import shutil
import subprocess
from copy import deepcopy
from pathlib import Path
from typing import Any
from uuid import uuid4

from .professional_editor_renderer import EditorExportResult, EditorRenderError, EditorRenderUnsupported
from .professional_video_effects_compositor import _StateProxy
from .professional_video_grouped_unified_compositor import GroupedUnifiedAdvancedVideoCompositor


SUPPORTED_UNIVERSAL_VIDEO_EFFECTS = frozenset(
    {
        "video.grade.basic",
        "video.fx.blur",
        "video.fx.vignette",
        "video.fx.film_grain",
        "video.fx.chromatic_aberration",
    }
)


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _clamp(value: Any, low: float, high: float, default: float = 0.0) -> float:
    return max(low, min(high, _finite(value, default)))


def _contract_type(effect: dict[str, Any]) -> str:
    return str(effect.get("type") or "").strip().lower()


def _ff(value: float) -> str:
    return f"{float(value):.8f}".rstrip("0").rstrip(".") or "0"


def _universal_visual_filter(effect: dict[str, Any]) -> str:
    """Translate one namespaced visual contract to a bounded FFmpeg filter chain.

    These names mirror the shared Universal Creative Library contract without importing or
    duplicating that registry. Chat 3 owns only executable renderer semantics here.
    """

    kind = _contract_type(effect)
    if kind not in SUPPORTED_UNIVERSAL_VIDEO_EFFECTS:
        raise EditorRenderUnsupported(f"Unsupported universal video visual effect: {kind or 'unnamed'}")
    if effect.get("keyframes"):
        raise EditorRenderUnsupported(f"Keyframed universal video effect is not yet render-safe: {kind}")

    params = effect.get("parameters") or {}
    if kind == "video.grade.basic":
        exposure = _clamp(params.get("exposure"), -5.0, 5.0)
        contrast_delta = _clamp(params.get("contrast"), -1.0, 1.0)
        saturation_delta = _clamp(params.get("saturation"), -1.0, 3.0)
        temperature = _clamp(params.get("temperature"), -1.0, 1.0)
        tint = _clamp(params.get("tint"), -1.0, 1.0)

        # The catalogue controls are normalized creative controls, not claims of physical-camera
        # units. Exposure maps conservatively to FFmpeg EQ brightness while contrast/saturation
        # are converted from delta controls to multiplicative factors. Temperature/tint use
        # bounded red/green/blue balance with preserve-lightness enabled.
        brightness = _clamp(exposure * 0.08, -0.4, 0.4)
        contrast = _clamp(1.0 + contrast_delta, 0.0, 2.0, 1.0)
        saturation = _clamp(1.0 + saturation_delta, 0.0, 4.0, 1.0)
        warmth = temperature * 0.24
        green = -tint * 0.20
        return (
            f"eq=brightness={_ff(brightness)}:contrast={_ff(contrast)}:saturation={_ff(saturation)},"
            f"colorbalance=rm={_ff(warmth)}:gm={_ff(green)}:bm={_ff(-warmth)}:"
            f"rh={_ff(warmth)}:gh={_ff(green)}:bh={_ff(-warmth)}:pl=1"
        )

    if kind == "video.fx.blur":
        radius = _clamp(params.get("radius", params.get("sigma")), 0.0, 100.0, 4.0)
        if radius <= 1e-8:
            return "null"
        return f"gblur=sigma={_ff(radius)}"

    if kind == "video.fx.vignette":
        amount = _clamp(params.get("amount"), -1.0, 1.0, 0.25)
        feather = _clamp(params.get("feather"), 0.0, 1.0, 0.6)
        if abs(amount) <= 1e-8:
            return "null"
        # FFmpeg's vignette primitive exposes a lens angle in [0, PI/2] and forward/backward
        # modes. Map our normalized amount/feather controls to that bounded domain; backward is
        # the explicit brightening/reverse-vignette mode.
        strength = abs(amount) * (1.0 - feather * 0.35)
        divisor = max(4.0, 24.0 - 19.0 * strength)
        mode = "forward" if amount > 0 else "backward"
        return f"vignette=angle=PI/{_ff(divisor)}:mode={mode}:eval=init"

    if kind == "video.fx.film_grain":
        amount = _clamp(params.get("amount"), 0.0, 1.0, 0.15)
        size = _clamp(params.get("size"), 0.25, 8.0, 1.0)
        if amount <= 1e-8:
            return "null"
        # FFmpeg noise strength is documented as 0..100. Keep the original effect deliberately
        # bounded and vary seed by authored grain size so a saved project remains deterministic.
        strength = max(1, min(45, round(amount * 45.0)))
        seed = 123457 + max(0, min(775, round((size - 0.25) * 100.0)))
        return f"noise=alls={strength}:allf=t+u:all_seed={seed}"

    if kind == "video.fx.chromatic_aberration":
        offset = max(0, min(50, round(_clamp(params.get("offset_px"), 0.0, 50.0, 2.0))))
        if offset == 0:
            return "null"
        return f"rgbashift=rh={offset}:bh={-offset}:edge=smear"

    raise EditorRenderUnsupported(f"Unsupported universal video visual effect: {kind}")


def _universal_visual_graph(effects: list[dict[str, Any]]) -> tuple[str | None, str, list[str]]:
    """Build an ordered wet/dry graph for supported namespaced effects."""

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
        effect_filter = _universal_visual_filter(effect)
        mix = _clamp(effect.get("mix"), 0.0, 1.0, 1.0)
        if mix <= 0.0 or effect_filter == "null":
            continue
        stage += 1
        applied.append(kind)
        if mix >= 1.0:
            out = f"uvfx{stage}"
            filters.append(f"[{current}]{effect_filter}[{out}]")
            current = out
            continue
        dry, wet, wet_fx, out = (
            f"uvdry{stage}",
            f"uvwet{stage}",
            f"uvwetfx{stage}",
            f"uvfx{stage}",
        )
        filters.append(f"[{current}]split=2[{dry}][{wet}]")
        filters.append(f"[{wet}]{effect_filter}[{wet_fx}]")
        filters.append(
            f"[{dry}][{wet_fx}]blend=all_expr='A*{1.0-mix:.8f}+B*{mix:.8f}'[{out}]"
        )
        current = out

    if stage == 0:
        return None, "0:v", []
    filters.append(f"[{current}]format=yuv420p[uvout]")
    return ";".join(filters), "uvout", applied


class UniversalVisualVideoCompositor(GroupedUnifiedAdvancedVideoCompositor):
    """Execute shared namespaced Video Studio visual contracts before grouped composition.

    The shared Universal Creative Library remains the discovery/source-of-truth contract. This
    renderer consumes those IDs without editing the registry, creates only project-local transient
    derivatives, delegates all remaining timeline/mask/blend semantics to the established grouped
    compositor, and deletes transient media after the genuine final MP4 has been produced.
    """

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
        duration = max(0.01, _finite(item.get("duration"), 0.01) * speed)
        graph, label, applied = _universal_visual_graph(effects)
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
            raise EditorRenderError("Universal visual compositor requires a video sequence")

        derived_root = self.project_dir / "work" / "editor_universal_visual_effects" / uuid4().hex
        applied_ids: list[str] = []
        original_refs: set[str] = set()
        derivative_count = 0
        try:
            for track_id in sequence.get("track_ids", []):
                track = tracks.get(track_id)
                if not track or not track.get("enabled", True):
                    continue
                track_contracts = [
                    effect
                    for effect in track.get("effects") or []
                    if effect.get("enabled", True) and _contract_type(effect).startswith("video.")
                ]
                unsupported_track_contracts = [
                    _contract_type(effect)
                    for effect in track_contracts
                    if _contract_type(effect) in SUPPORTED_UNIVERSAL_VIDEO_EFFECTS
                ]
                if unsupported_track_contracts:
                    raise EditorRenderUnsupported(
                        "Universal visual contract effects are item-local in this renderer wave; "
                        "apply them to a clip rather than a whole track: " + ", ".join(sorted(set(unsupported_track_contracts)))
                    )

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
                    unknown_namespaced = [
                        _contract_type(effect)
                        for effect in effects
                        if effect.get("enabled", True)
                        and _contract_type(effect).startswith("video.")
                        and _contract_type(effect) not in SUPPORTED_UNIVERSAL_VIDEO_EFFECTS
                    ]
                    if unknown_namespaced:
                        raise EditorRenderUnsupported(
                            "Unsupported universal video visual effect: " + ", ".join(sorted(set(unknown_namespaced)))
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
                    "universal_visual_transient_derivatives": derivative_count,
                    "universal_visual_transient_derivatives_ephemeral": True,
                    "supported_universal_video_visual_effects": sorted(SUPPORTED_UNIVERSAL_VIDEO_EFFECTS),
                    "universal_visual_effect_keyframes_fail_closed": True,
                    "universal_visual_track_effects_fail_closed": True,
                    "source_refs": sorted(inherited_refs),
                    "original_source_refs": sorted(inherited_refs),
                    "source_media_mutated": False,
                }
            )
            metadata_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            return result.model_copy(update={"renderer": "ffmpeg-universal-visual-video-compositor"})
        finally:
            self.store = real_store
            shutil.rmtree(derived_root, ignore_errors=True)


__all__ = [
    "SUPPORTED_UNIVERSAL_VIDEO_EFFECTS",
    "UniversalVisualVideoCompositor",
    "_universal_visual_filter",
    "_universal_visual_graph",
]
