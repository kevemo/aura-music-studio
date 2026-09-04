from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Literal

from PIL import Image, ImageChops, ImageEnhance, ImageFilter, ImageOps
from pydantic import BaseModel, Field

ImageEffectKind = Literal[
    "brightness",
    "contrast",
    "saturation",
    "sharpness",
    "gaussian_blur",
    "grayscale",
    "invert",
    "posterize",
    "autocontrast",
    "equalize",
    "solarize",
    "unsharp_mask",
    "emboss",
    "edge_enhance",
    "find_edges",
    "sepia",
    "vignette",
    "pixelate",
    "duotone",
]

_SAFE_PRESET_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
_SUPPORTED_INPUT = {".png", ".jpg", ".jpeg", ".webp"}
_SUPPORTED_OUTPUT = {".png", ".jpg", ".jpeg", ".webp"}
_MAX_DIMENSION = 16384
_MAX_PIXELS = 64_000_000


class ImageEffectNode(BaseModel):
    kind: ImageEffectKind
    enabled: bool = True
    mix: float = Field(default=1.0, ge=0.0, le=1.0)
    amount: float = Field(default=1.0, ge=0.0, le=4.0)
    radius: float = Field(default=2.0, ge=0.0, le=50.0)
    bits: int = Field(default=6, ge=1, le=8)


class ImageEffectGraph(BaseModel):
    schema_version: int = Field(default=1, ge=1, le=1)
    name: str = Field(default="Aura Image FX", min_length=1, max_length=120)
    nodes: list[ImageEffectNode] = Field(default_factory=list, max_length=16)

    def fingerprint(self) -> str:
        payload = json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


def _read_image(source: Path) -> Image.Image:
    if source.suffix.lower() not in _SUPPORTED_INPUT:
        raise ValueError("Executable image effects require PNG, JPEG or WebP input")
    if not source.is_file():
        raise FileNotFoundError(source)
    with Image.open(source) as opened:
        width, height = opened.size
        if width < 1 or height < 1:
            raise ValueError("Image input is empty")
        if width > _MAX_DIMENSION or height > _MAX_DIMENSION or width * height > _MAX_PIXELS:
            raise ValueError("Image dimensions exceed the executable-effects safety limit")
        opened.load()
        return opened.convert("RGBA")


def _blend(original: Image.Image, effected: Image.Image, mix: float) -> Image.Image:
    if mix <= 0.0:
        return original.copy()
    if mix >= 1.0:
        return effected
    return Image.blend(original, effected, mix)


def _preserve_alpha(rgb: Image.Image, alpha: Image.Image) -> Image.Image:
    rgba = rgb.convert("RGBA")
    rgba.putalpha(alpha)
    return rgba


def _rgb_effect(image: Image.Image, transform) -> Image.Image:
    alpha = image.getchannel("A")
    effected = transform(image.convert("RGB"))
    return _preserve_alpha(effected, alpha)


def _solarize_threshold(amount: float) -> int:
    strength = min(max(amount / 4.0, 0.0), 1.0)
    return round(255 * (1.0 - strength))


def _apply_vignette(image: Image.Image, amount: float) -> Image.Image:
    strength = min(max(amount / 4.0, 0.0), 1.0)
    if strength <= 0.0:
        return image.copy()
    gradient = Image.radial_gradient("L").resize(
        image.size,
        resample=Image.Resampling.BILINEAR,
    )
    edge_mask = gradient.point(lambda value: round(255 - (value * strength)))
    rgb_mask = Image.merge("RGB", (edge_mask, edge_mask, edge_mask))
    effected = ImageChops.multiply(image.convert("RGB"), rgb_mask)
    return _preserve_alpha(effected, image.getchannel("A"))


def _apply_pixelate(image: Image.Image, radius: float) -> Image.Image:
    block_size = max(2, round(radius))
    width, height = image.size
    reduced = image.resize(
        (max(1, width // block_size), max(1, height // block_size)),
        resample=Image.Resampling.BOX,
    )
    return reduced.resize((width, height), resample=Image.Resampling.NEAREST)


def _apply_node(image: Image.Image, node: ImageEffectNode) -> Image.Image:
    if not node.enabled or node.mix <= 0.0:
        return image
    original = image
    alpha = image.getchannel("A")
    if node.kind == "brightness":
        effected = ImageEnhance.Brightness(image).enhance(node.amount)
    elif node.kind == "contrast":
        effected = ImageEnhance.Contrast(image).enhance(node.amount)
    elif node.kind == "saturation":
        effected = ImageEnhance.Color(image).enhance(node.amount)
    elif node.kind == "sharpness":
        effected = ImageEnhance.Sharpness(image).enhance(node.amount)
    elif node.kind == "gaussian_blur":
        effected = image.filter(ImageFilter.GaussianBlur(radius=node.radius))
        effected.putalpha(alpha)
    elif node.kind == "grayscale":
        effected = _preserve_alpha(ImageOps.grayscale(image), alpha)
    elif node.kind == "invert":
        effected = _rgb_effect(image, ImageOps.invert)
    elif node.kind == "posterize":
        effected = _rgb_effect(image, lambda rgb: ImageOps.posterize(rgb, node.bits))
    elif node.kind == "autocontrast":
        effected = _rgb_effect(image, ImageOps.autocontrast)
    elif node.kind == "equalize":
        effected = _rgb_effect(image, ImageOps.equalize)
    elif node.kind == "solarize":
        threshold = _solarize_threshold(node.amount)
        effected = _rgb_effect(image, lambda rgb: ImageOps.solarize(rgb, threshold))
    elif node.kind == "unsharp_mask":
        effected = _rgb_effect(
            image,
            lambda rgb: rgb.filter(
                ImageFilter.UnsharpMask(
                    radius=node.radius,
                    percent=round(node.amount * 100),
                    threshold=3,
                )
            ),
        )
    elif node.kind == "emboss":
        effected = _rgb_effect(image, lambda rgb: rgb.filter(ImageFilter.EMBOSS))
    elif node.kind == "edge_enhance":
        effected = _rgb_effect(image, lambda rgb: rgb.filter(ImageFilter.EDGE_ENHANCE_MORE))
    elif node.kind == "find_edges":
        effected = _rgb_effect(image, lambda rgb: rgb.filter(ImageFilter.FIND_EDGES))
    elif node.kind == "sepia":
        effected = _rgb_effect(
            image,
            lambda rgb: ImageOps.colorize(
                ImageOps.grayscale(rgb),
                black="#24150D",
                white="#F4D8A6",
            ),
        )
    elif node.kind == "vignette":
        effected = _apply_vignette(image, node.amount)
    elif node.kind == "pixelate":
        effected = _apply_pixelate(image, node.radius)
        effected.putalpha(alpha)
    elif node.kind == "duotone":
        effected = _rgb_effect(
            image,
            lambda rgb: ImageOps.colorize(
                ImageOps.grayscale(rgb),
                black="#2A163B",
                white="#F5D98A",
            ),
        )
    else:  # pragma: no cover - pydantic rejects unknown effect kinds
        raise ValueError("Unsupported image effect node")
    return _blend(original, effected.convert("RGBA"), node.mix)


def render_image_effect_graph(
    source: str | Path,
    destination: str | Path,
    graph: ImageEffectGraph,
) -> dict:
    """Render an allowlisted editable image-effect graph into a real image file.

    The runtime accepts no commands, URLs, plugins, Python expressions or arbitrary effect
    identifiers. It performs local Pillow transforms only and preserves source alpha where the
    output format supports it.
    """
    src = Path(source)
    dst = Path(destination)
    suffix = dst.suffix.lower()
    if suffix not in _SUPPORTED_OUTPUT:
        raise ValueError("Effect render output must be PNG, JPEG or WebP")
    result = _read_image(src)
    for node in graph.nodes:
        result = _apply_node(result, node)

    dst.parent.mkdir(parents=True, exist_ok=True)
    temporary = dst.with_name(f".{dst.name}.tmp{dst.suffix}")
    save_image = result.convert("RGB") if suffix in {".jpg", ".jpeg"} else result
    format_name = "JPEG" if suffix in {".jpg", ".jpeg"} else suffix.removeprefix(".").upper()
    save_image.save(temporary, format=format_name)
    temporary.replace(dst)
    return {
        "rendered": True,
        "effect_graph_fingerprint": graph.fingerprint(),
        "node_count": len(graph.nodes),
        "width": result.width,
        "height": result.height,
        "mode": save_image.mode,
        "image_origin": "local_allowlisted_pixel_effects",
        "arbitrary_code_execution": False,
        "network_access": False,
    }


def save_image_effect_preset(
    directory: str | Path,
    preset_name: str,
    graph: ImageEffectGraph,
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
        json.dumps(graph.model_dump(mode="json"), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(target)
    return target


def load_image_effect_preset(
    directory: str | Path,
    preset_name: str,
) -> ImageEffectGraph:
    if not _SAFE_PRESET_NAME.fullmatch(preset_name):
        raise ValueError("Preset name contains unsupported characters")
    root = Path(directory).resolve()
    target = (root / f"{preset_name}.json").resolve()
    if target.parent != root or not target.is_file():
        raise FileNotFoundError(preset_name)
    return ImageEffectGraph.model_validate_json(target.read_text(encoding="utf-8"))


__all__ = [
    "ImageEffectGraph",
    "ImageEffectKind",
    "ImageEffectNode",
    "load_image_effect_preset",
    "render_image_effect_graph",
    "save_image_effect_preset",
]
