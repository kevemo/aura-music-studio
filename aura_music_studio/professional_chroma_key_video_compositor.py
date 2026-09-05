from __future__ import annotations

import json
import shutil
import subprocess
from copy import deepcopy
from pathlib import Path
from typing import Any

from . import professional_universal_scoped_visual_video_compositor as _scoped
from .professional_editor_renderer import EditorExportResult, EditorRenderError, EditorRenderUnsupported
from .professional_video_chroma_key import CHROMA_KEY_EFFECT, chain_requires_alpha, chroma_key_filter
from .professional_video_compositor import _finite


# Extend the already-established scoped universal dispatcher at startup rather than creating a
# parallel Video Studio effect stack. Existing dispatcher functions resolve these module globals at
# call time, so item validation, grouped-track validation and whole-track effect execution all see
# the same augmented contract after this module is imported by the production render API.
_BASE_SUPPORTED = _scoped.SUPPORTED_UNIVERSAL_VIDEO_EFFECTS
_BASE_STATIC_FILTER = _scoped._static_universal_visual_filter
_scoped.SUPPORTED_UNIVERSAL_VIDEO_EFFECTS = frozenset({*_BASE_SUPPORTED, CHROMA_KEY_EFFECT})
_scoped._ANIMATABLE_UNIVERSAL_PARAMETERS[CHROMA_KEY_EFFECT] = frozenset()


def _static_filter_with_chroma(effect: dict[str, Any]) -> str:
    kind = str(effect.get("type") or "").strip().lower()
    if kind == CHROMA_KEY_EFFECT:
        return chroma_key_filter(effect)
    return _BASE_STATIC_FILTER(effect)


_scoped._static_universal_visual_filter = _static_filter_with_chroma


def _alpha_aware_universal_visual_graph(
    effects: list[dict[str, Any]],
    *,
    filter_time_variable: str,
    blend_time_variable: str,
) -> tuple[str | None, str, list[str]]:
    """Build the existing ordered wet/dry graph while retaining alpha when keying is present."""

    active = [effect for effect in effects or [] if effect.get("enabled", True)]
    if not active:
        return None, "0:v", []

    filters: list[str] = []
    current = "0:v"
    applied: list[str] = []
    stage = 0
    for effect in active:
        kind = str(effect.get("type") or "").strip().lower()
        if kind not in _scoped.SUPPORTED_UNIVERSAL_VIDEO_EFFECTS:
            continue
        effect_filter = _scoped._animated_universal_visual_filter(
            effect,
            time_variable=filter_time_variable,
        )
        mix_expr, mix_animated = _scoped._bounded_keyframe_expr(
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
    output_format = "rgba" if CHROMA_KEY_EFFECT in applied else "yuv420p"
    filters.append(f"[{current}]format={output_format}[uvout]")
    return ";".join(filters), "uvout", applied


_scoped._universal_visual_graph = _alpha_aware_universal_visual_graph
# Re-install the established process-wide dispatch after extending its contract. This remains
# idempotent and retains the original legacy dispatcher for non-namespaced effects.
_scoped.install_universal_video_effect_dispatch()


class ChromaKeyUniversalVisualVideoCompositor(_scoped.UniversalVisualVideoCompositor):
    """Scoped universal Video Studio renderer with genuine alpha-preserving chroma key support."""

    def _derive_universal_clip(
        self,
        item: dict[str, Any],
        effects: list[dict[str, Any]],
        derived_root: Path,
    ) -> tuple[str, list[str]]:
        if not chain_requires_alpha(effects):
            return super()._derive_universal_clip(item, effects, derived_root)

        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise EditorRenderUnsupported("FFmpeg is unavailable on this runtime")
        source = self._source(item.get("source_ref"))
        source_in = max(0.0, _finite(item.get("source_in"), 0.0))
        speed = max(0.05, min(20.0, _finite(item.get("speed"), 1.0)))
        item_duration = max(0.01, _finite(item.get("duration"), 0.01))
        duration = item_duration * speed
        start = max(0.0, _finite(item.get("start"), 0.0))

        # The derivative is generated before the grouped compositor handles speed/reverse. Keep
        # effect automation tied to authored sequence time exactly as the existing scoped renderer.
        if item.get("reverse"):
            filter_time = f"({_scoped._ff(start + item_duration)}-t/{_scoped._ff(speed)})"
            blend_time = f"({_scoped._ff(start + item_duration)}-T/{_scoped._ff(speed)})"
        else:
            filter_time = f"({_scoped._ff(start)}+t/{_scoped._ff(speed)})"
            blend_time = f"({_scoped._ff(start)}+T/{_scoped._ff(speed)})"

        graph, label, applied = _alpha_aware_universal_visual_graph(
            effects,
            filter_time_variable=filter_time,
            blend_time_variable=blend_time,
        )
        if graph is None or CHROMA_KEY_EFFECT not in applied:
            raise EditorRenderUnsupported("Alpha-preserving chroma derivative requested without an executable keyer")

        # qtrle is a native FFmpeg lossless QuickTime codec with an ARGB pixel format. It avoids
        # the transparency loss that would occur if the keyed derivative were encoded as the
        # normal H.264/yuv420p transient. The MOV remains project-local and is deleted after the
        # grouped final MP4 has been rendered.
        output = derived_root / f"{item['id']}.mov"
        output.parent.mkdir(parents=True, exist_ok=True)
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
            detail = (completed.stderr or "FFmpeg produced no alpha-preserving chroma derivative").strip().splitlines()[-1][:500]
            raise EditorRenderError(f"Chroma key pre-render failed: {detail}")
        return output.relative_to(self.project_dir).as_posix(), applied

    def render_video_advanced(self, sequence_id: str) -> EditorExportResult:
        result = super().render_video_advanced(sequence_id)
        metadata_path = self.project_dir / result.metadata_ref
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        contracts = set(payload.get("universal_visual_effect_contracts_executed") or [])
        keyed = CHROMA_KEY_EFFECT in contracts
        alpha_derivatives = 0
        if keyed:
            # Item-local keyed effects use the alpha-preserving MOV path. Whole-track keying runs
            # directly in the grouped RGBA graph and therefore needs no derivative.
            scopes = set(payload.get("universal_visual_effect_scopes_executed") or [])
            if "item" in scopes:
                alpha_derivatives = sum(
                    1
                    for ref in (payload.get("transient_source_refs") or [])
                    if str(ref).lower().endswith(".mov")
                )
                # Older grouped metadata does not expose transient refs. In that case the scoped
                # derivative count is still truthful evidence that at least one item derivative ran.
                if alpha_derivatives == 0:
                    alpha_derivatives = min(
                        int(payload.get("universal_visual_transient_derivatives") or 0),
                        int(payload.get("universal_visual_effect_instances_executed") or 0),
                    )
        payload.update(
            {
                "professional_chroma_key_compositor": keyed,
                "professional_chroma_key_contract": CHROMA_KEY_EFFECT,
                "professional_chroma_key_alpha_preserved": keyed,
                "professional_chroma_key_rgb_colorkey": True,
                "professional_chroma_key_green_blue_despill": True,
                "professional_chroma_key_custom_color": True,
                "professional_chroma_key_keyframed_mix_supported": True,
                "professional_chroma_key_parameter_keyframes_fail_closed": True,
                "professional_chroma_key_alpha_derivatives": alpha_derivatives,
                "professional_chroma_key_track_scope_uses_grouped_rgba": True,
                "professional_chroma_key_source_media_mutated": False,
            }
        )
        metadata_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        if keyed:
            return result.model_copy(update={"renderer": "ffmpeg-universal-chroma-key-video-compositor"})
        return result


# Production API imports this alias so existing code and tests that refer to
# UniversalVisualVideoCompositor keep a stable constructor name.
UniversalVisualVideoCompositor = ChromaKeyUniversalVisualVideoCompositor


__all__ = [
    "ChromaKeyUniversalVisualVideoCompositor",
    "UniversalVisualVideoCompositor",
    "CHROMA_KEY_EFFECT",
    "_alpha_aware_universal_visual_graph",
]
