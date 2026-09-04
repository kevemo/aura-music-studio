from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Annotated, Literal, Mapping, Union

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont
from pydantic import BaseModel, Field, model_validator

from .executable_image_effects import ImageEffectGraph, _apply_node

LayerBlendMode = Literal["normal", "multiply", "screen"]
MaskKind = Literal["rectangle", "ellipse"]
GradientDirection = Literal["horizontal", "vertical"]
PatternKind = Literal["checker", "stripes", "dots"]
ShapeKind = Literal["rectangle", "ellipse"]

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SAFE_PRESET_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
_HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}(?:[0-9A-Fa-f]{2})?$")
_SUPPORTED_IMAGES = {".png", ".jpg", ".jpeg", ".webp"}
_MAX_CANVAS_DIMENSION = 4096
_MAX_LAYER_DIMENSION = 4096
_MAX_LAYER_PIXELS = 16_000_000
_MAX_ASSET_BYTES = 64_000_000
_MAX_PATTERN_CELLS = 100_000


def _rgba(value: str) -> tuple[int, int, int, int]:
    if not _HEX_COLOR.fullmatch(value):
        raise ValueError("Colours must use #RRGGBB or #RRGGBBAA notation")
    payload = value[1:]
    if len(payload) == 6:
        payload += "FF"
    values = tuple(int(payload[index : index + 2], 16) for index in range(0, 8, 2))
    return values[0], values[1], values[2], values[3]


def _valid_id(value: str, *, label: str) -> str:
    if not _SAFE_ID.fullmatch(value):
        raise ValueError(f"{label} contains unsupported characters")
    return value


def _bounded_layer_area(width: int, height: int) -> None:
    if width * height > _MAX_LAYER_PIXELS:
        raise ValueError("Image design layer exceeds the pixel safety limit")


class LayerMask(BaseModel):
    kind: MaskKind = "rectangle"
    x: int = Field(default=0, ge=-_MAX_LAYER_DIMENSION, le=_MAX_LAYER_DIMENSION)
    y: int = Field(default=0, ge=-_MAX_LAYER_DIMENSION, le=_MAX_LAYER_DIMENSION)
    width: int = Field(default=1, ge=1, le=_MAX_LAYER_DIMENSION)
    height: int = Field(default=1, ge=1, le=_MAX_LAYER_DIMENSION)
    feather: float = Field(default=0.0, ge=0.0, le=64.0)
    invert: bool = False


class ImageLayerBase(BaseModel):
    id: str = Field(min_length=1, max_length=128)
    kind: str
    x: int = Field(default=0, ge=-_MAX_CANVAS_DIMENSION, le=_MAX_CANVAS_DIMENSION)
    y: int = Field(default=0, ge=-_MAX_CANVAS_DIMENSION, le=_MAX_CANVAS_DIMENSION)
    opacity: float = Field(default=1.0, ge=0.0, le=1.0)
    visible: bool = True
    blend: LayerBlendMode = "normal"
    mask: LayerMask | None = None

    @model_validator(mode="after")
    def validate_layer_id(self):
        _valid_id(self.id, label="Layer id")
        return self


class RasterLayer(ImageLayerBase):
    kind: Literal["raster"] = "raster"
    asset_id: str = Field(min_length=1, max_length=128)
    width: int | None = Field(default=None, ge=1, le=_MAX_LAYER_DIMENSION)
    height: int | None = Field(default=None, ge=1, le=_MAX_LAYER_DIMENSION)
    effects: ImageEffectGraph | None = None

    @model_validator(mode="after")
    def validate_raster_layer(self):
        _valid_id(self.asset_id, label="Asset id")
        if (self.width is None) != (self.height is None):
            raise ValueError("Raster width and height must either both be set or both be omitted")
        if self.width is not None and self.height is not None:
            _bounded_layer_area(self.width, self.height)
        return self


class SolidLayer(ImageLayerBase):
    kind: Literal["solid"] = "solid"
    width: int = Field(ge=1, le=_MAX_LAYER_DIMENSION)
    height: int = Field(ge=1, le=_MAX_LAYER_DIMENSION)
    color: str = "#FFFFFFFF"

    @model_validator(mode="after")
    def validate_solid_layer(self):
        _rgba(self.color)
        _bounded_layer_area(self.width, self.height)
        return self


class GradientLayer(ImageLayerBase):
    kind: Literal["gradient"] = "gradient"
    width: int = Field(ge=1, le=_MAX_LAYER_DIMENSION)
    height: int = Field(ge=1, le=_MAX_LAYER_DIMENSION)
    start_color: str = "#000000FF"
    end_color: str = "#FFFFFFFF"
    direction: GradientDirection = "horizontal"

    @model_validator(mode="after")
    def validate_gradient_layer(self):
        _rgba(self.start_color)
        _rgba(self.end_color)
        _bounded_layer_area(self.width, self.height)
        return self


class PatternLayer(ImageLayerBase):
    kind: Literal["pattern"] = "pattern"
    width: int = Field(ge=1, le=_MAX_LAYER_DIMENSION)
    height: int = Field(ge=1, le=_MAX_LAYER_DIMENSION)
    pattern: PatternKind = "checker"
    foreground: str = "#FFFFFFFF"
    background: str = "#00000000"
    cell_size: int = Field(default=24, ge=8, le=512)

    @model_validator(mode="after")
    def validate_pattern_layer(self):
        _rgba(self.foreground)
        _rgba(self.background)
        _bounded_layer_area(self.width, self.height)
        cells = math.ceil(self.width / self.cell_size) * math.ceil(self.height / self.cell_size)
        if cells > _MAX_PATTERN_CELLS:
            raise ValueError("Pattern density exceeds the image design safety limit")
        return self


class ShapeLayer(ImageLayerBase):
    kind: Literal["shape"] = "shape"
    shape: ShapeKind = "rectangle"
    width: int = Field(ge=1, le=_MAX_LAYER_DIMENSION)
    height: int = Field(ge=1, le=_MAX_LAYER_DIMENSION)
    fill: str = "#FFFFFFFF"
    outline: str = "#00000000"
    stroke_width: int = Field(default=0, ge=0, le=128)

    @model_validator(mode="after")
    def validate_shape_layer(self):
        _rgba(self.fill)
        _rgba(self.outline)
        _bounded_layer_area(self.width, self.height)
        return self


class TextLayer(ImageLayerBase):
    kind: Literal["text"] = "text"
    text: str = Field(min_length=1, max_length=2000)
    width: int = Field(default=600, ge=16, le=_MAX_LAYER_DIMENSION)
    color: str = "#FFFFFFFF"
    font_family: Literal["aura_sans"] = "aura_sans"
    font_size: int = Field(default=48, ge=8, le=256)
    align: Literal["left", "center", "right"] = "left"
    padding: int = Field(default=8, ge=0, le=256)
    line_spacing: int = Field(default=4, ge=0, le=128)

    @model_validator(mode="after")
    def validate_text_layer(self):
        _rgba(self.color)
        if self.padding * 2 >= self.width:
            raise ValueError("Text padding leaves no drawable width")
        return self


ImageDesignLayer = Annotated[
    Union[RasterLayer, SolidLayer, GradientLayer, PatternLayer, ShapeLayer, TextLayer],
    Field(discriminator="kind"),
]


class ImageDesignDocument(BaseModel):
    schema_version: int = Field(default=1, ge=1, le=1)
    name: str = Field(default="Aura Image Design", min_length=1, max_length=120)
    width: int = Field(default=1024, ge=64, le=_MAX_CANVAS_DIMENSION)
    height: int = Field(default=1024, ge=64, le=_MAX_CANVAS_DIMENSION)
    background: str = "#00000000"
    layers: list[ImageDesignLayer] = Field(default_factory=list, max_length=64)

    @model_validator(mode="after")
    def validate_document(self):
        _rgba(self.background)
        ids = [layer.id for layer in self.layers]
        if len(ids) != len(set(ids)):
            raise ValueError("Image design layer ids must be unique")
        return self

    def fingerprint(self) -> str:
        payload = json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_asset(path: Path) -> tuple[Image.Image, str]:
    if path.suffix.lower() not in _SUPPORTED_IMAGES:
        raise ValueError("Image design raster assets require PNG, JPEG or WebP")
    if not path.is_file():
        raise FileNotFoundError(path)
    if path.stat().st_size > _MAX_ASSET_BYTES:
        raise ValueError("Image design raster asset exceeds the byte safety limit")
    digest = _sha256_file(path)
    with Image.open(path) as opened:
        width, height = opened.size
        if width < 1 or height < 1:
            raise ValueError("Image design raster asset is empty")
        if width > _MAX_LAYER_DIMENSION or height > _MAX_LAYER_DIMENSION:
            raise ValueError("Image design raster asset dimensions exceed the safety limit")
        _bounded_layer_area(width, height)
        opened.load()
        return opened.convert("RGBA"), digest


def _apply_effect_graph(image: Image.Image, graph: ImageEffectGraph) -> Image.Image:
    result = image.convert("RGBA")
    for node in graph.nodes:
        result = _apply_node(result, node)
    return result


def _linear_gradient(layer: GradientLayer) -> Image.Image:
    start = _rgba(layer.start_color)
    end = _rgba(layer.end_color)
    if layer.direction == "horizontal":
        strip = Image.new("RGBA", (layer.width, 1))
        pixels = strip.load()
        denominator = max(1, layer.width - 1)
        for x in range(layer.width):
            position = x / denominator
            pixels[x, 0] = tuple(
                round(start[channel] + (end[channel] - start[channel]) * position)
                for channel in range(4)
            )
        return strip.resize((layer.width, layer.height), resample=Image.Resampling.NEAREST)

    strip = Image.new("RGBA", (1, layer.height))
    pixels = strip.load()
    denominator = max(1, layer.height - 1)
    for y in range(layer.height):
        position = y / denominator
        pixels[0, y] = tuple(
            round(start[channel] + (end[channel] - start[channel]) * position)
            for channel in range(4)
        )
    return strip.resize((layer.width, layer.height), resample=Image.Resampling.NEAREST)


def _pattern(layer: PatternLayer) -> Image.Image:
    image = Image.new("RGBA", (layer.width, layer.height), _rgba(layer.background))
    draw = ImageDraw.Draw(image)
    foreground = _rgba(layer.foreground)
    cell = layer.cell_size
    if layer.pattern == "checker":
        for y in range(0, layer.height, cell):
            for x in range(0, layer.width, cell):
                if ((x // cell) + (y // cell)) % 2 == 0:
                    draw.rectangle(
                        (x, y, min(layer.width - 1, x + cell - 1), min(layer.height - 1, y + cell - 1)),
                        fill=foreground,
                    )
    elif layer.pattern == "stripes":
        for x in range(-layer.height, layer.width, cell * 2):
            draw.polygon(
                (
                    (x, 0),
                    (x + cell, 0),
                    (x + cell + layer.height, layer.height),
                    (x + layer.height, layer.height),
                ),
                fill=foreground,
            )
    else:
        radius = max(1, cell // 4)
        for y in range(cell // 2, layer.height, cell):
            for x in range(cell // 2, layer.width, cell):
                draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=foreground)
    return image


def _wrap_text(text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    scratch = Image.new("RGBA", (1, 1))
    draw = ImageDraw.Draw(scratch)
    lines: list[str] = []
    for paragraph in text.splitlines() or [""]:
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        current = words[0]
        for word in words[1:]:
            candidate = f"{current} {word}"
            if draw.textbbox((0, 0), candidate, font=font)[2] <= max_width:
                current = candidate
            else:
                lines.append(current)
                current = word
        lines.append(current)
    return lines


def _text_layer(layer: TextLayer) -> Image.Image:
    font = ImageFont.load_default(size=layer.font_size)
    usable_width = layer.width - (layer.padding * 2)
    lines = _wrap_text(layer.text, font, usable_width)
    scratch = Image.new("RGBA", (1, 1))
    scratch_draw = ImageDraw.Draw(scratch)
    probe = scratch_draw.textbbox((0, 0), "Ag", font=font)
    line_height = max(1, probe[3] - probe[1])
    height = (
        layer.padding * 2
        + line_height * len(lines)
        + layer.line_spacing * max(0, len(lines) - 1)
    )
    if height > _MAX_LAYER_DIMENSION or layer.width * height > _MAX_LAYER_PIXELS:
        raise ValueError("Rendered text layer exceeds the image design safety limit")
    image = Image.new("RGBA", (layer.width, max(1, height)), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    y = layer.padding
    for line in lines:
        bounds = draw.textbbox((0, 0), line, font=font)
        line_width = max(0, bounds[2] - bounds[0])
        if layer.align == "center":
            x = (layer.width - line_width) // 2
        elif layer.align == "right":
            x = layer.width - layer.padding - line_width
        else:
            x = layer.padding
        draw.text((x, y - bounds[1]), line, font=font, fill=_rgba(layer.color))
        y += line_height + layer.line_spacing
    return image


def _shape(layer: ShapeLayer) -> Image.Image:
    image = Image.new("RGBA", (layer.width, layer.height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    box = (0, 0, layer.width - 1, layer.height - 1)
    drawing = draw.rectangle if layer.shape == "rectangle" else draw.ellipse
    drawing(box, fill=_rgba(layer.fill), outline=_rgba(layer.outline), width=layer.stroke_width)
    return image


def _apply_mask(image: Image.Image, mask: LayerMask) -> Image.Image:
    matte = Image.new("L", image.size, 0)
    draw = ImageDraw.Draw(matte)
    box = (mask.x, mask.y, mask.x + mask.width - 1, mask.y + mask.height - 1)
    drawing = draw.rectangle if mask.kind == "rectangle" else draw.ellipse
    drawing(box, fill=255)
    if mask.feather > 0:
        matte = matte.filter(ImageFilter.GaussianBlur(mask.feather))
    if mask.invert:
        matte = ImageChops.invert(matte)
    result = image.copy()
    result.putalpha(ImageChops.multiply(result.getchannel("A"), matte))
    return result


def _apply_opacity(image: Image.Image, opacity: float) -> Image.Image:
    if opacity >= 1.0:
        return image
    result = image.copy()
    result.putalpha(result.getchannel("A").point(lambda value: round(value * opacity)))
    return result


def _blend_layer(base: Image.Image, layer_image: Image.Image, layer: ImageLayerBase) -> Image.Image:
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    overlay.alpha_composite(layer_image, dest=(layer.x, layer.y))
    if layer.blend == "normal":
        return Image.alpha_composite(base, overlay)
    base_rgb = base.convert("RGB")
    overlay_rgb = overlay.convert("RGB")
    blended_rgb = (
        ImageChops.multiply(base_rgb, overlay_rgb)
        if layer.blend == "multiply"
        else ImageChops.screen(base_rgb, overlay_rgb)
    )
    blended_rgb = Image.composite(blended_rgb, overlay_rgb, base.getchannel("A"))
    blended = blended_rgb.convert("RGBA")
    blended.putalpha(overlay.getchannel("A"))
    return Image.alpha_composite(base, blended)


def _render_layer(
    layer: ImageDesignLayer,
    assets: Mapping[str, str | Path],
    asset_digests: dict[str, str],
    effect_fingerprints: dict[str, str],
) -> Image.Image:
    if isinstance(layer, RasterLayer):
        if layer.asset_id not in assets:
            raise KeyError(f"No server-bound raster asset is available for id: {layer.asset_id}")
        image, digest = _read_asset(Path(assets[layer.asset_id]))
        asset_digests[layer.asset_id] = digest
        if layer.width is not None and layer.height is not None:
            image = image.resize((layer.width, layer.height), resample=Image.Resampling.LANCZOS)
        if layer.effects is not None:
            image = _apply_effect_graph(image, layer.effects)
            effect_fingerprints[layer.id] = layer.effects.fingerprint()
    elif isinstance(layer, SolidLayer):
        image = Image.new("RGBA", (layer.width, layer.height), _rgba(layer.color))
    elif isinstance(layer, GradientLayer):
        image = _linear_gradient(layer)
    elif isinstance(layer, PatternLayer):
        image = _pattern(layer)
    elif isinstance(layer, ShapeLayer):
        image = _shape(layer)
    elif isinstance(layer, TextLayer):
        image = _text_layer(layer)
    else:
        raise ValueError("Unsupported image design layer")
    if layer.mask is not None:
        image = _apply_mask(image, layer.mask)
    return _apply_opacity(image, layer.opacity)


def render_image_design_document(
    document: ImageDesignDocument,
    destination: str | Path,
    *,
    assets: Mapping[str, str | Path] | None = None,
) -> dict:
    """Render one bounded editable Image Designer document into a real image."""
    asset_map = assets or {}
    canvas = Image.new("RGBA", (document.width, document.height), _rgba(document.background))
    asset_digests: dict[str, str] = {}
    effect_fingerprints: dict[str, str] = {}
    rendered_layer_ids: list[str] = []
    skipped_layer_ids: list[str] = []
    for layer in document.layers:
        if not layer.visible or layer.opacity <= 0:
            skipped_layer_ids.append(layer.id)
            continue
        layer_image = _render_layer(layer, asset_map, asset_digests, effect_fingerprints)
        canvas = _blend_layer(canvas, layer_image, layer)
        rendered_layer_ids.append(layer.id)

    target = Path(destination)
    suffix = target.suffix.lower()
    if suffix not in _SUPPORTED_IMAGES:
        raise ValueError("Image design output must be PNG, JPEG or WebP")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp{target.suffix}")
    output = canvas.convert("RGB") if suffix in {".jpg", ".jpeg"} else canvas
    format_name = "JPEG" if suffix in {".jpg", ".jpeg"} else suffix.removeprefix(".").upper()
    output.save(temporary, format=format_name)
    temporary.replace(target)
    return {
        "rendered": True,
        "document_fingerprint": document.fingerprint(),
        "rendered_layer_ids": rendered_layer_ids,
        "skipped_layer_ids": skipped_layer_ids,
        "asset_digests": asset_digests,
        "effect_graph_fingerprints": effect_fingerprints,
        "width": document.width,
        "height": document.height,
        "mode": output.mode,
        "image_origin": "local_allowlisted_image_design_compositor",
        "asset_reference_policy": "server_bound_ids_only",
        "typography_font_policy": "pillow_default_aura_sans_only",
        "arbitrary_code_execution": False,
        "network_access": False,
    }


def save_image_design_preset(
    directory: str | Path,
    preset_name: str,
    document: ImageDesignDocument,
) -> Path:
    if not _SAFE_PRESET_NAME.fullmatch(preset_name):
        raise ValueError("Preset name contains unsupported characters")
    root = Path(directory).resolve()
    root.mkdir(parents=True, exist_ok=True)
    target = (root / f"{preset_name}.json").resolve()
    if target.parent != root:
        raise ValueError("Preset path escapes its library")
    temporary = root / f".{target.name}.tmp"
    temporary.write_text(
        json.dumps(document.model_dump(mode="json"), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(target)
    return target


def load_image_design_preset(directory: str | Path, preset_name: str) -> ImageDesignDocument:
    if not _SAFE_PRESET_NAME.fullmatch(preset_name):
        raise ValueError("Preset name contains unsupported characters")
    root = Path(directory).resolve()
    target = (root / f"{preset_name}.json").resolve()
    if target.parent != root or not target.is_file():
        raise FileNotFoundError(preset_name)
    return ImageDesignDocument.model_validate_json(target.read_text(encoding="utf-8"))


__all__ = [
    "GradientLayer",
    "ImageDesignDocument",
    "ImageDesignLayer",
    "LayerMask",
    "PatternLayer",
    "RasterLayer",
    "ShapeLayer",
    "SolidLayer",
    "TextLayer",
    "load_image_design_preset",
    "render_image_design_document",
    "save_image_design_preset",
]
