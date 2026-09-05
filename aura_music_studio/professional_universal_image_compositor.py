from __future__ import annotations

import json
from typing import Any, Literal
from uuid import uuid4

import numpy as np
from PIL import Image, ImageOps

from .professional_editor_renderer import (
    _IMAGE_SUFFIXES,
    EditorExportResult,
    EditorRenderError,
    EditorRenderUnsupported,
)
from .professional_image_compositor import (
    AdvancedImageCompositor,
    _apply_effect,
    _apply_masks,
    _blend_operation,
    _effect_state_at_time,
    _mix_rgba,
    _state_at_time,
)


SUPPORTED_UNIVERSAL_IMAGE_EFFECTS = frozenset(
    {
        "image.filter.cinematic",
        "image.filter.duotone",
    }
)


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if np.isfinite(number) else default


def _clamp(value: Any, low: float, high: float, default: float = 0.0) -> float:
    return max(low, min(high, _finite(value, default)))


def _effect_type(effect: dict[str, Any]) -> str:
    return str(effect.get("type") or "").strip().lower().replace("-", "_").replace(" ", "_")


def _hex_rgb(value: Any, *, field: str) -> tuple[int, int, int]:
    text = str(value or "").strip()
    if text.startswith("#"):
        text = text[1:]
    if len(text) == 3:
        text = "".join(char * 2 for char in text)
    if len(text) != 6 or any(char not in "0123456789abcdefABCDEF" for char in text):
        raise EditorRenderUnsupported(f"{field} must be a #RRGGBB or #RGB colour")
    return tuple(int(text[index : index + 2], 16) for index in (0, 2, 4))


def _cinematic_filter(rgb: Image.Image, strength: float) -> Image.Image:
    value = _clamp(strength, 0.0, 1.0, 0.7)
    if value <= 0.0:
        return rgb.copy()

    array = np.asarray(rgb.convert("RGB"), dtype=np.float32) / 255.0
    # Original bounded S-curve: deepen lower mids and lift upper mids without hard clipping.
    tone = array + value * 0.80 * (array - 0.5) * array * (1.0 - array)
    tone = np.clip(tone, 0.0, 1.0)

    # Preserve colour in the mids/highlights while gently reducing saturation in deep shadows.
    luminance = (
        tone[..., 0] * 0.2126
        + tone[..., 1] * 0.7152
        + tone[..., 2] * 0.0722
    )[..., None]
    shadow_weight = np.clip((0.38 - luminance) / 0.38, 0.0, 1.0)
    saturation = 1.0 - 0.18 * value * shadow_weight
    graded = luminance + (tone - luminance) * saturation
    return Image.fromarray(np.clip(graded * 255.0, 0, 255).astype(np.uint8), mode="RGB")


def _apply_universal_image_effect(image: Image.Image, effect: dict[str, Any], time: float) -> Image.Image:
    if not effect.get("enabled", True):
        return image
    state = _effect_state_at_time(effect, time)
    kind = _effect_type(state)
    if kind not in SUPPORTED_UNIVERSAL_IMAGE_EFFECTS:
        raise EditorRenderUnsupported(f"Unsupported universal image effect: {kind or 'unnamed'}")

    params = state.get("parameters") or {}
    dry = image.convert("RGBA")
    alpha = dry.getchannel("A")
    rgb = dry.convert("RGB")

    if kind == "image.filter.cinematic":
        wet_rgb = _cinematic_filter(rgb, _clamp(params.get("strength"), 0.0, 1.0, 0.7))
    elif kind == "image.filter.duotone":
        shadow = _hex_rgb(params.get("shadow", "#111111"), field="Duotone shadow")
        highlight = _hex_rgb(params.get("highlight", "#f2c86f"), field="Duotone highlight")
        wet_rgb = ImageOps.colorize(ImageOps.grayscale(rgb), black=shadow, white=highlight)
    else:  # pragma: no cover - guarded above, retained for fail-closed clarity.
        raise EditorRenderUnsupported(f"Unsupported universal image effect: {kind}")

    wet = wet_rgb.convert("RGBA")
    wet.putalpha(alpha)
    return _mix_rgba(dry, wet, _finite(state.get("mix"), 1.0))


class UniversalImageCompositor(AdvancedImageCompositor):
    """Execute shared Image Designer filters at item and whole-track scope."""

    def __init__(self, project_dir):
        super().__init__(project_dir)
        self._universal_image_effects_executed: list[str] = []
        self._universal_image_effect_scopes_executed: list[str] = []

    def _apply_scoped_effect(
        self,
        image: Image.Image,
        effect: dict[str, Any],
        time: float,
        *,
        scope: Literal["item", "track"],
    ) -> Image.Image:
        kind = _effect_type(effect)
        if kind in SUPPORTED_UNIVERSAL_IMAGE_EFFECTS:
            rendered = _apply_universal_image_effect(image, effect, time)
            if effect.get("enabled", True):
                self._universal_image_effects_executed.append(kind)
                self._universal_image_effect_scopes_executed.append(scope)
            return rendered
        if kind.startswith("image."):
            raise EditorRenderUnsupported(f"Unsupported universal image effect: {kind}")
        return _apply_effect(image, effect, time)

    def _layer_canvas(
        self,
        sequence: dict[str, Any],
        track: dict[str, Any],
        item: dict[str, Any],
        time: float,
    ) -> tuple[Image.Image, list[str]]:
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
            return (
                Image.new("RGBA", (int(sequence["width"]), int(sequence["height"])), (0, 0, 0, 0)),
                source_refs,
            )

        layer = self._apply_crop(layer, item.get("crop") or {})
        layer = self._apply_colour(layer, item.get("color") or {})
        for effect in item.get("effects") or []:
            layer = self._apply_scoped_effect(layer, effect, time, scope="item")
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
        """Render an image sequence while executing universal filters at both supported scopes.

        This mirrors the established advanced compositor's branch, mask, transform, blend and
        export semantics, but routes both item and track effect stacks through the universal
        dispatcher. The source project/media remain immutable; only a new export and its metadata
        are written.
        """
        state = self.store.public_state()
        sequences, tracks, items = self._branch_maps(state)
        sequence = sequences.get(sequence_id)
        if sequence is None:
            raise KeyError(sequence_id)
        if sequence["kind"] != "image":
            raise EditorRenderError("Universal image compositor requires an image sequence")
        self._validate_sequence(sequence)
        frame_time = max(0.0, min(float(sequence.get("duration") or 1.0), _finite(frame_time, 0.0)))

        self._universal_image_effects_executed = []
        self._universal_image_effect_scopes_executed = []
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
                track_canvas = self._apply_scoped_effect(track_canvas, effect, frame_time, scope="track")
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

        result = self._record_result(
            sequence,
            state,
            output,
            format,
            "pillow-universal-image-compositor",
            source_refs,
        )
        metadata_path = self.project_dir / result.metadata_ref
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        payload.update(
            {
                "advanced_compositor": True,
                "frame_time": frame_time,
                "supports_masks": True,
                "supports_effects": True,
                "supports_blend_modes": True,
                "supports_numeric_keyframes": True,
                "universal_image_compositor": True,
                "universal_image_effect_contracts_executed": sorted(set(self._universal_image_effects_executed)),
                "universal_image_effect_instances_executed": len(self._universal_image_effects_executed),
                "universal_image_effect_scopes_executed": sorted(set(self._universal_image_effect_scopes_executed)),
                "supported_universal_image_effects": sorted(SUPPORTED_UNIVERSAL_IMAGE_EFFECTS),
                "supported_universal_image_effect_scopes": ["item", "track"],
                "source_media_mutated": False,
            }
        )
        metadata_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return result.model_copy(update={"renderer": "pillow-universal-image-compositor"})


__all__ = [
    "SUPPORTED_UNIVERSAL_IMAGE_EFFECTS",
    "UniversalImageCompositor",
    "_apply_universal_image_effect",
    "_cinematic_filter",
    "_hex_rgb",
]
