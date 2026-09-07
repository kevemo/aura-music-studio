from __future__ import annotations

import json
import math
from copy import deepcopy
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageOps

from .professional_editor_renderer import (
    _IMAGE_SUFFIXES,
    EditorExportResult,
    EditorRenderError,
    EditorRenderUnsupported,
    ProfessionalEditorRenderer,
)


_SUPPORTED_KEYFRAME_PATHS = {
    "opacity",
    "transform.x",
    "transform.y",
    "transform.scale_x",
    "transform.scale_y",
    "transform.rotation",
    "crop.left",
    "crop.top",
    "crop.right",
    "crop.bottom",
    "color.exposure",
    "color.brightness",
    "color.contrast",
    "color.saturation",
    "color.gamma",
}


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _keyframe_value(points: list[dict], time: float, default: float) -> float:
    clean = []
    for point in points or []:
        if not isinstance(point, dict):
            continue
        point_time = _finite(point.get("time"), 0.0)
        value = _finite(point.get("value"), default)
        clean.append((max(0.0, point_time), value, str(point.get("interpolation") or "linear").lower()))
    if not clean:
        return default
    clean.sort(key=lambda row: row[0])
    if time <= clean[0][0]:
        return clean[0][1]
    if time >= clean[-1][0]:
        return clean[-1][1]
    for index in range(len(clean) - 1):
        left_time, left_value, interpolation = clean[index]
        right_time, right_value, _ = clean[index + 1]
        if left_time <= time <= right_time:
            if interpolation == "hold" or right_time <= left_time:
                return left_value
            phase = (time - left_time) / (right_time - left_time)
            if interpolation in {"smooth", "bezier"}:
                phase = phase * phase * (3.0 - 2.0 * phase)
            return left_value + (right_value - left_value) * phase
    return default


def _set_path(payload: dict[str, Any], path: str, value: float) -> None:
    if path == "opacity":
        payload["opacity"] = value
        return
    head, field = path.split(".", 1)
    mapping = payload.setdefault(head, {})
    if not isinstance(mapping, dict):
        mapping = {}
        payload[head] = mapping
    mapping[field] = value


def _state_at_time(payload: dict[str, Any], time: float) -> dict[str, Any]:
    result = deepcopy(payload)
    for path, points in (payload.get("keyframes") or {}).items():
        if path not in _SUPPORTED_KEYFRAME_PATHS:
            raise EditorRenderUnsupported(f"Image keyframe path is not yet render-safe: {path}")
        if path == "opacity":
            default = _finite(payload.get("opacity"), 1.0)
        else:
            head, field = path.split(".", 1)
            default = _finite((payload.get(head) or {}).get(field), 0.0)
        _set_path(result, path, _keyframe_value(points, time, default))
    return result


def _effect_state_at_time(effect: dict[str, Any], time: float) -> dict[str, Any]:
    result = deepcopy(effect)
    parameters = result.setdefault("parameters", {})
    for name, points in (effect.get("keyframes") or {}).items():
        if name == "mix":
            result["mix"] = _keyframe_value(points, time, _finite(effect.get("mix"), 1.0))
            continue
        default = _finite((effect.get("parameters") or {}).get(name), 0.0)
        parameters[name] = _keyframe_value(points, time, default)
    return result


def _mix_rgba(dry: Image.Image, wet: Image.Image, mix: float) -> Image.Image:
    value = max(0.0, min(1.0, _finite(mix, 1.0)))
    if value <= 0:
        return dry
    if value >= 1:
        return wet
    return Image.blend(dry.convert("RGBA"), wet.convert("RGBA"), value)


def _apply_effect(image: Image.Image, effect: dict[str, Any], time: float) -> Image.Image:
    if not effect.get("enabled", True):
        return image
    state = _effect_state_at_time(effect, time)
    effect_type = str(state.get("type") or "").strip().lower().replace("-", "_").replace(" ", "_")
    params = state.get("parameters") or {}
    dry = image.convert("RGBA")
    alpha = dry.getchannel("A")
    rgb = dry.convert("RGB")

    if effect_type in {"blur", "gaussian_blur"}:
        wet_rgb = rgb.filter(ImageFilter.GaussianBlur(radius=max(0.0, min(250.0, _finite(params.get("radius"), 4.0)))))
    elif effect_type in {"sharpen", "unsharp", "unsharp_mask"}:
        radius = max(0.1, min(50.0, _finite(params.get("radius"), 2.0)))
        percent = max(0, min(500, int(_finite(params.get("percent"), 150))))
        threshold = max(0, min(255, int(_finite(params.get("threshold"), 3))))
        wet_rgb = rgb.filter(ImageFilter.UnsharpMask(radius=radius, percent=percent, threshold=threshold))
    elif effect_type in {"grayscale", "greyscale", "black_and_white", "bw"}:
        wet_rgb = ImageOps.grayscale(rgb).convert("RGB")
    elif effect_type == "invert":
        wet_rgb = ImageOps.invert(rgb)
    elif effect_type == "sepia":
        array = np.asarray(rgb, dtype=np.float32)
        matrix = np.array(
            [[0.393, 0.769, 0.189], [0.349, 0.686, 0.168], [0.272, 0.534, 0.131]],
            dtype=np.float32,
        )
        transformed = np.clip(array @ matrix.T, 0, 255).astype(np.uint8)
        wet_rgb = Image.fromarray(transformed, mode="RGB")
    elif effect_type == "brightness":
        wet_rgb = ImageEnhance.Brightness(rgb).enhance(max(0.0, _finite(params.get("factor"), 1.15)))
    elif effect_type == "contrast":
        wet_rgb = ImageEnhance.Contrast(rgb).enhance(max(0.0, _finite(params.get("factor"), 1.15)))
    elif effect_type in {"saturation", "color"}:
        wet_rgb = ImageEnhance.Color(rgb).enhance(max(0.0, _finite(params.get("factor"), 1.15)))
    elif effect_type == "vignette":
        strength = max(0.0, min(1.0, _finite(params.get("strength"), 0.45)))
        yy, xx = np.mgrid[0:rgb.height, 0:rgb.width]
        cx, cy = (rgb.width - 1) / 2.0, (rgb.height - 1) / 2.0
        nx = (xx - cx) / max(1.0, cx)
        ny = (yy - cy) / max(1.0, cy)
        radius = np.sqrt(nx * nx + ny * ny)
        factor = np.clip(1.0 - strength * np.clip(radius, 0.0, 1.0) ** 1.7, 0.0, 1.0)[..., None]
        wet_rgb = Image.fromarray(np.clip(np.asarray(rgb, dtype=np.float32) * factor, 0, 255).astype(np.uint8), mode="RGB")
    elif effect_type == "pixelate":
        block = max(1, min(256, int(_finite(params.get("block"), 12))))
        small = rgb.resize((max(1, rgb.width // block), max(1, rgb.height // block)), Image.Resampling.BILINEAR)
        wet_rgb = small.resize(rgb.size, Image.Resampling.NEAREST)
    else:
        raise EditorRenderUnsupported(f"Unsupported image effect: {effect_type or 'unnamed'}")

    wet = wet_rgb.convert("RGBA")
    wet.putalpha(alpha)
    return _mix_rgba(dry, wet, _finite(state.get("mix"), 1.0))


def _mask_shape(size: tuple[int, int], mask: dict[str, Any]) -> Image.Image:
    width, height = size
    points = list(mask.get("points") or [])
    shape = str(mask.get("shape") or "rectangle").lower()
    required = 2 if shape in {"rectangle", "ellipse"} else 3
    if len(points) < required:
        raise EditorRenderUnsupported(f"{shape.title()} mask requires at least {required} control points")
    coords = [
        (
            max(0, min(width, round(_finite(point[0]) * width))),
            max(0, min(height, round(_finite(point[1]) * height))),
        )
        for point in points
    ]
    layer = Image.new("L", size, 0)
    draw = ImageDraw.Draw(layer)
    if shape in {"rectangle", "ellipse"}:
        xs = [point[0] for point in coords]
        ys = [point[1] for point in coords]
        box = (min(xs), min(ys), max(xs), max(ys))
        if shape == "rectangle":
            draw.rectangle(box, fill=255)
        else:
            draw.ellipse(box, fill=255)
    elif shape in {"polygon", "path"}:
        draw.polygon(coords, fill=255)
    else:
        raise EditorRenderUnsupported(f"Unsupported image mask shape: {shape}")

    expansion = max(-31, min(31, round(_finite(mask.get("expansion"), 0.0))))
    if expansion:
        kernel = abs(expansion) * 2 + 1
        layer = layer.filter(ImageFilter.MaxFilter(kernel) if expansion > 0 else ImageFilter.MinFilter(kernel))
    feather = max(0.0, min(250.0, _finite(mask.get("feather"), 0.0)))
    if feather > 0:
        layer = layer.filter(ImageFilter.GaussianBlur(radius=feather))
    opacity = max(0.0, min(1.0, _finite(mask.get("opacity"), 1.0)))
    if opacity < 1.0:
        layer = layer.point(lambda value: round(value * opacity))
    if mask.get("inverted"):
        layer = ImageOps.invert(layer)
    return layer


def _apply_masks(image: Image.Image, masks: list[dict]) -> Image.Image:
    enabled = [mask for mask in masks or [] if mask.get("enabled", True)]
    if not enabled:
        return image
    combined: Image.Image | None = None
    for mask in enabled:
        shape = _mask_shape(image.size, mask)
        mode = str(mask.get("mode") or "add").lower()
        if combined is None:
            combined = ImageOps.invert(shape) if mode == "subtract" else shape
        elif mode == "add":
            combined = ImageChops.lighter(combined, shape)
        elif mode == "subtract":
            combined = ImageChops.subtract(combined, shape)
        elif mode == "intersect":
            combined = ImageChops.multiply(combined, shape)
        else:
            raise EditorRenderUnsupported(f"Unsupported mask mode: {mode}")
    result = image.convert("RGBA")
    if combined is not None:
        result.putalpha(ImageChops.multiply(result.getchannel("A"), combined))
    return result


def _blend_operation(base: Image.Image, layer: Image.Image, mode: str) -> Image.Image:
    mode = str(mode or "normal").lower()
    base = base.convert("RGBA")
    layer = layer.convert("RGBA")
    if mode == "normal":
        return Image.alpha_composite(base, layer)

    left, right = base.convert("RGB"), layer.convert("RGB")
    operations = {
        "multiply": ImageChops.multiply,
        "screen": ImageChops.screen,
        "overlay": getattr(ImageChops, "overlay", None),
        "soft_light": getattr(ImageChops, "soft_light", None),
        "hard_light": getattr(ImageChops, "hard_light", None),
        "darken": ImageChops.darker,
        "lighten": ImageChops.lighter,
        "difference": ImageChops.difference,
    }
    operation = operations.get(mode)
    if operation is None:
        raise EditorRenderUnsupported(f"Blend mode is not supported by this Pillow runtime: {mode}")
    blended_rgb = operation(left, right)
    blended_layer = blended_rgb.convert("RGBA")
    blended_layer.putalpha(layer.getchannel("A"))
    blended = Image.alpha_composite(base, blended_layer)
    normal = Image.alpha_composite(base, layer)
    # Transparent backdrop should reveal the source normally; opaque backdrop receives the blend.
    return Image.composite(blended, normal, base.getchannel("A"))


class AdvancedImageCompositor(ProfessionalEditorRenderer):
    """Pillow compositor for masks, blend modes, effects and keyframed still-frame export."""

    def _layer_canvas(self, sequence: dict[str, Any], track: dict[str, Any], item: dict[str, Any], time: float) -> tuple[Image.Image, list[str]]:
        item = _state_at_time(item, time)
        track = _state_at_time(track, time)
        source_refs: list[str] = []
        if item["kind"] == "text":
            layer = self._render_text_layer(item)
        elif item["kind"] == "image_layer":
            source = self._source(item.get("source_ref"))
            if source.suffix.lower() not in _IMAGE_SUFFIXES:
                raise EditorRenderUnsupported("Image sequence contains a non-image source")
            source_refs.append(str(source.relative_to(self.project_dir)))
            with Image.open(source) as opened:
                layer = opened.convert("RGBA")
        else:
            return Image.new("RGBA", (int(sequence["width"]), int(sequence["height"])), (0, 0, 0, 0)), source_refs

        layer = self._apply_crop(layer, item.get("crop") or {})
        layer = self._apply_colour(layer, item.get("color") or {})
        for effect in item.get("effects") or []:
            layer = _apply_effect(layer, effect, time)
        layer = _apply_masks(layer, item.get("masks") or [])
        layer, (x_offset, y_offset) = self._apply_transform(layer, item, {**track, "opacity": 1.0})
        full = Image.new("RGBA", (int(sequence["width"]), int(sequence["height"])), (0, 0, 0, 0))
        x = (full.width - layer.width) // 2 + x_offset
        y = (full.height - layer.height) // 2 + y_offset
        full.alpha_composite(layer, (x, y))
        return full, source_refs

    def render_image_advanced(
        self,
        sequence_id: str,
        *,
        format: Literal["png", "webp", "jpeg"] = "png",
        quality: int = 92,
        frame_time: float = 0.0,
    ) -> EditorExportResult:
        state = self.store.public_state()
        sequences, tracks, items = self._branch_maps(state)
        sequence = sequences.get(sequence_id)
        if sequence is None:
            raise KeyError(sequence_id)
        if sequence["kind"] != "image":
            raise EditorRenderError("Advanced image compositor requires an image sequence")
        self._validate_sequence(sequence)
        frame_time = max(0.0, min(float(sequence.get("duration") or 1.0), _finite(frame_time, 0.0)))

        canvas = Image.new(
            "RGBA",
            (int(sequence["width"]), int(sequence["height"])),
            self._hex_rgba(sequence.get("background")),
        )
        source_refs: list[str] = []
        for track_id in sequence.get("track_ids", []):
            raw_track = tracks.get(track_id)
            if not raw_track or not raw_track.get("enabled", True) or not raw_track.get("visible", True):
                continue
            track = _state_at_time(raw_track, frame_time)
            track_canvas = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
            for item_id in track.get("item_ids", []):
                item = items.get(item_id)
                if not item or not item.get("enabled", True) or not item.get("visible", True):
                    continue
                layer, refs = self._layer_canvas(sequence, track, item, frame_time)
                source_refs.extend(refs)
                track_canvas = _blend_operation(track_canvas, layer, str(item.get("blend_mode") or "normal"))
            for effect in track.get("effects") or []:
                track_canvas = _apply_effect(track_canvas, effect, frame_time)
            track_opacity = max(0.0, min(1.0, _finite(track.get("opacity"), 1.0)))
            if track_opacity < 1.0:
                track_canvas.putalpha(track_canvas.getchannel("A").point(lambda value: round(value * track_opacity)))
            canvas = _blend_operation(canvas, track_canvas, str(track.get("blend_mode") or "normal"))

        extension = "jpg" if format == "jpeg" else format
        filename = f"{sequence_id}_{uuid4().hex[:12]}.{extension}"
        output = self._output(filename)
        temporary = output.with_name(output.name + ".part")
        save_args: dict[str, Any] = {}
        rendered = canvas
        if format == "jpeg":
            rendered = canvas.convert("RGB")
            save_args["quality"] = max(1, min(100, int(quality)))
        elif format == "webp":
            save_args["quality"] = max(1, min(100, int(quality)))
        rendered.save(temporary, format="JPEG" if format == "jpeg" else format.upper(), **save_args)
        temporary.replace(output)
        result = self._record_result(sequence, state, output, format, "pillow-advanced-rgba-compositor", source_refs)
        metadata = self.project_dir / result.metadata_ref
        try:
            payload = json.loads(metadata.read_text(encoding="utf-8"))
            payload.update({
                "advanced_compositor": True,
                "frame_time": frame_time,
                "supports_masks": True,
                "supports_effects": True,
                "supports_blend_modes": True,
                "supports_numeric_keyframes": True,
            })
            metadata.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        except Exception:
            pass
        return result


__all__ = ["AdvancedImageCompositor"]
