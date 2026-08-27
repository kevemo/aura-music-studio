from __future__ import annotations

import json
import math
import shutil
import subprocess
from copy import deepcopy
from pathlib import Path
from typing import Any
from uuid import uuid4

from PIL import Image

from .professional_editor_renderer import EditorExportResult, EditorRenderError, EditorRenderUnsupported
from .professional_image_compositor import _apply_effect, _apply_masks
from .professional_video_compositor import AdvancedVideoCompositor

_SUPPORTED_VIDEO_EFFECTS = {
    "blur",
    "gaussian_blur",
    "sharpen",
    "unsharp",
    "unsharp_mask",
    "grayscale",
    "greyscale",
    "black_and_white",
    "bw",
    "invert",
    "sepia",
    "brightness",
    "contrast",
    "saturation",
    "color",
}
_SUPPORTED_STATIC_MASK_SHAPES = {"rectangle", "ellipse", "polygon", "path"}


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _effect_type(effect: dict[str, Any]) -> str:
    return str(effect.get("type") or "").strip().lower().replace("-", "_").replace(" ", "_")


def _video_effect_filter(effect: dict[str, Any]) -> str:
    kind = _effect_type(effect)
    params = effect.get("parameters") or {}
    if kind not in _SUPPORTED_VIDEO_EFFECTS:
        raise EditorRenderUnsupported(f"Unsupported video item effect: {kind or 'unnamed'}")
    if effect.get("keyframes"):
        raise EditorRenderUnsupported(f"Keyframed video effect is not yet render-safe: {kind}")

    if kind in {"blur", "gaussian_blur"}:
        sigma = max(0.0, min(100.0, _finite(params.get("radius", params.get("sigma")), 4.0)))
        return f"gblur=sigma={sigma:.6f}"
    if kind in {"sharpen", "unsharp", "unsharp_mask"}:
        amount = max(-2.0, min(5.0, _finite(params.get("amount"), 1.0)))
        return f"unsharp=5:5:{amount:.6f}:5:5:0"
    if kind in {"grayscale", "greyscale", "black_and_white", "bw"}:
        return "hue=s=0"
    if kind == "invert":
        return "negate"
    if kind == "sepia":
        return "colorchannelmixer=.393:.769:.189:0:.349:.686:.168:0:.272:.534:.131"
    if kind == "brightness":
        factor = max(0.0, min(3.0, _finite(params.get("factor"), 1.15)))
        return f"eq=brightness={max(-1.0, min(1.0, factor - 1.0)):.6f}"
    if kind == "contrast":
        factor = max(0.0, min(3.0, _finite(params.get("factor"), 1.15)))
        return f"eq=contrast={factor:.6f}"
    if kind in {"saturation", "color"}:
        factor = max(0.0, min(3.0, _finite(params.get("factor"), 1.15)))
        return f"eq=saturation={factor:.6f}"
    raise EditorRenderUnsupported(f"Unsupported video item effect: {kind}")


def _video_effect_graph(effects: list[dict[str, Any]]) -> tuple[str | None, str]:
    """Return an FFmpeg filter_complex body and final video label for sequential wet/dry effects."""
    active = [effect for effect in effects or [] if effect.get("enabled", True)]
    if not active:
        return None, "0:v"
    filters: list[str] = []
    current = "0:v"
    stage = 0
    for effect in active:
        kind_filter = _video_effect_filter(effect)
        mix = max(0.0, min(1.0, _finite(effect.get("mix"), 1.0)))
        if mix <= 0.0:
            continue
        stage += 1
        if mix >= 1.0:
            out = f"fx{stage}"
            filters.append(f"[{current}]{kind_filter}[{out}]")
            current = out
            continue
        dry, wet, wet_fx, out = f"dry{stage}", f"wet{stage}", f"wetfx{stage}", f"fx{stage}"
        filters.append(f"[{current}]split=2[{dry}][{wet}]")
        filters.append(f"[{wet}]{kind_filter}[{wet_fx}]")
        filters.append(
            f"[{dry}][{wet_fx}]blend=all_expr='A*{1.0-mix:.8f}+B*{mix:.8f}'[{out}]"
        )
        current = out
    if stage == 0:
        return None, "0:v"
    filters.append(f"[{current}]format=yuv420p[vout]")
    return ";".join(filters), "vout"


def _colour_is_default(item: dict[str, Any]) -> bool:
    colour = item.get("color") or {}
    zeros = ("exposure", "brightness", "temperature", "tint", "highlights", "shadows")
    ones = ("contrast", "saturation", "gamma")
    return all(abs(_finite(colour.get(name), 0.0)) <= 1e-8 for name in zeros) and all(
        abs(_finite(colour.get(name), 1.0) - 1.0) <= 1e-8 for name in ones
    )


def _crop_is_default(item: dict[str, Any]) -> bool:
    crop = item.get("crop") or {}
    return all(abs(_finite(crop.get(name), 0.0)) <= 1e-8 for name in ("left", "top", "right", "bottom"))


class _StateProxy:
    def __init__(self, state: dict[str, Any]):
        self.state = state

    def public_state(self) -> dict[str, Any]:
        return deepcopy(self.state)


class VideoItemEffectsCompositor(AdvancedVideoCompositor):
    """Pre-render render-safe item effects, static masks and audio layout, then delegate.

    Derived files live only under the current project work directory and are deleted after export.
    Original source references are retained in export provenance metadata.
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
                raise EditorRenderUnsupported("Video track blend modes are not yet render-safe")
            for item_id in track.get("item_ids", []):
                item = items.get(item_id)
                if not item or not item.get("enabled", True):
                    continue
                if item.get("blend_mode", "normal") != "normal":
                    raise EditorRenderUnsupported("Video item blend modes are not yet render-safe")
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

    def _derive_still(self, item: dict[str, Any], derived_root: Path) -> str:
        kind = item.get("kind")
        if kind == "text":
            image = self._render_text_layer(item).convert("RGBA")
        else:
            source = self._source(item.get("source_ref"))
            with Image.open(source) as opened:
                image = opened.convert("RGBA")
        for effect in item.get("effects") or []:
            image = _apply_effect(image, effect, 0.0)
        image = _apply_masks(image, list(item.get("masks") or []))
        output = derived_root / f"{item['id']}.png"
        output.parent.mkdir(parents=True, exist_ok=True)
        image.save(output, format="PNG")
        return output.relative_to(self.project_dir).as_posix()

    def _video_dimensions(self, source: Path, derived_root: Path) -> tuple[int, int]:
        ffprobe = shutil.which("ffprobe")
        if ffprobe:
            completed = subprocess.run(
                [
                    ffprobe,
                    "-v",
                    "error",
                    "-select_streams",
                    "v:0",
                    "-show_entries",
                    "stream=width,height",
                    "-of",
                    "json",
                    str(source),
                ],
                capture_output=True,
                text=True,
                timeout=min(self.timeout_seconds, 60.0),
                check=False,
            )
            if completed.returncode == 0:
                try:
                    streams = json.loads(completed.stdout or "{}").get("streams") or []
                    width = int(streams[0].get("width"))
                    height = int(streams[0].get("height"))
                    if width > 0 and height > 0:
                        return width, height
                except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError):
                    pass

        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise EditorRenderUnsupported("FFmpeg is unavailable on this runtime")
        preview = derived_root / f"probe_{uuid4().hex}.png"
        completed = subprocess.run(
            [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(source), "-frames:v", "1", str(preview)],
            capture_output=True,
            text=True,
            timeout=min(self.timeout_seconds, 60.0),
            check=False,
        )
        if completed.returncode != 0 or not preview.is_file():
            preview.unlink(missing_ok=True)
            raise EditorRenderError("Unable to determine video dimensions for mask rendering")
        try:
            with Image.open(preview) as opened:
                return opened.width, opened.height
        finally:
            preview.unlink(missing_ok=True)

    def _mask_asset(self, item: dict[str, Any], source: Path, derived_root: Path) -> Path:
        width, height = self._video_dimensions(source, derived_root)
        opaque = Image.new("RGBA", (width, height), (255, 255, 255, 255))
        alpha = _apply_masks(opaque, list(item.get("masks") or [])).getchannel("A")
        path = derived_root / f"{item['id']}_mask.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        alpha.save(path, format="PNG")
        return path

    def _derive_video(self, item: dict[str, Any], derived_root: Path, *, force_stereo: bool) -> str:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise EditorRenderUnsupported("FFmpeg is unavailable on this runtime")
        source = self._source(item.get("source_ref"))
        masks = [mask for mask in item.get("masks") or [] if mask.get("enabled", True)]
        suffix = ".mov" if masks else ".mp4"
        output = derived_root / f"{item['id']}{suffix}"
        output.parent.mkdir(parents=True, exist_ok=True)
        source_in = max(0.0, _finite(item.get("source_in"), 0.0))
        duration = max(0.01, _finite(item.get("duration"), 0.01) * max(0.05, min(20.0, _finite(item.get("speed"), 1.0))))
        graph, label = _video_effect_graph(list(item.get("effects") or []))
        args = [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-ss", f"{source_in:.8f}", "-t", f"{duration:.8f}", "-i", str(source),
        ]
        filters: list[str] = []
        if graph:
            filters.append(graph)
        if masks:
            mask_asset = self._mask_asset(item, source, derived_root)
            args += ["-loop", "1", "-framerate", "240", "-t", f"{duration:.8f}", "-i", str(mask_asset)]
            current = label if graph else "0:v"
            filters.extend(
                [
                    f"[{current}]format=rgba[maskbase]",
                    "[1:v]format=gray[maskalpha]",
                    "[maskbase][maskalpha]alphamerge,format=argb[vfinal]",
                ]
            )
            label = "vfinal"
        if filters:
            args += ["-filter_complex", ";".join(filters), "-map", f"[{label}]"]
        else:
            args += ["-map", "0:v:0"]
        args += ["-map", "0:a?"]
        if masks:
            args += ["-c:v", "qtrle", "-pix_fmt", "argb"]
        else:
            args += ["-c:v", "libx264", "-preset", "fast", "-crf", "16", "-pix_fmt", "yuv420p"]
        if force_stereo:
            args += ["-c:a", "aac", "-b:a", "192k", "-ac", "2"]
        else:
            args += ["-c:a", "aac", "-b:a", "192k"]
        args += ["-movflags", "+faststart", str(output)]
        completed = subprocess.run(args, capture_output=True, text=True, timeout=self.timeout_seconds, check=False)
        if completed.returncode != 0 or not output.is_file():
            output.unlink(missing_ok=True)
            detail = (completed.stderr or "FFmpeg produced no derived clip").strip().splitlines()[-1][:500]
            raise EditorRenderError(f"Video effect/mask pre-render failed: {detail}")
        return output.relative_to(self.project_dir).as_posix()

    def _derive_audio(self, item: dict[str, Any], derived_root: Path) -> str:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise EditorRenderUnsupported("FFmpeg is unavailable on this runtime")
        source = self._source(item.get("source_ref"))
        output = derived_root / f"{item['id']}.wav"
        output.parent.mkdir(parents=True, exist_ok=True)
        source_in = max(0.0, _finite(item.get("source_in"), 0.0))
        duration = max(0.01, _finite(item.get("duration"), 0.01) * max(0.05, min(20.0, _finite(item.get("speed"), 1.0))))
        completed = subprocess.run(
            [
                ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                "-ss", f"{source_in:.8f}", "-t", f"{duration:.8f}", "-i", str(source),
                "-vn", "-ac", "2", "-c:a", "pcm_f32le", str(output),
            ],
            capture_output=True,
            text=True,
            timeout=self.timeout_seconds,
            check=False,
        )
        if completed.returncode != 0 or not output.is_file():
            output.unlink(missing_ok=True)
            detail = (completed.stderr or "FFmpeg produced no stereo derivative").strip().splitlines()[-1][:500]
            raise EditorRenderError(f"Audio pan preparation failed: {detail}")
        return output.relative_to(self.project_dir).as_posix()

    def render_video_advanced(self, sequence_id: str) -> EditorExportResult:
        real_store = self.store
        original_state = real_store.public_state()
        self._validate_effect_state(original_state, sequence_id)
        state = deepcopy(original_state)
        sequences, tracks, items = self._branch_maps(state)
        sequence = sequences.get(sequence_id)
        if sequence is None:
            raise KeyError(sequence_id)

        derived_root = self.project_dir / "work" / "editor_video_effects" / uuid4().hex
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

            self.store = _StateProxy(state)  # type: ignore[assignment]
            result = super().render_video_advanced(sequence_id)
            metadata = self.project_dir / result.metadata_ref
            payload = json.loads(metadata.read_text(encoding="utf-8"))
            payload.update({
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
                "source_media_mutated": False,
                "unsupported_state_fails_closed": True,
            })
            metadata.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            if masked_count:
                result = result.model_copy(update={"renderer": "ffmpeg-video-item-effects-mask-compositor"})
            elif derived_count:
                result = result.model_copy(update={"renderer": "ffmpeg-video-item-effects-compositor"})
            return result
        finally:
            self.store = real_store
            shutil.rmtree(derived_root, ignore_errors=True)


__all__ = [
    "VideoItemEffectsCompositor",
    "_SUPPORTED_VIDEO_EFFECTS",
    "_SUPPORTED_STATIC_MASK_SHAPES",
    "_video_effect_filter",
    "_video_effect_graph",
]
