from __future__ import annotations

import math
from typing import Any

import numpy as np
from PIL import Image, ImageFilter

from . import professional_image_compositor as image_compositor
from . import professional_universal_image_compositor as universal_compositor
from . import visual_effect_catalogue as catalogue
from . import visual_effect_catalogue_hardening as hardening


LEGACY_VISUAL_EFFECT_IDS = frozenset(
    {
        "image.light.glow",
        "image.light.bloom",
        "image.light.shimmer",
    }
)

_LEGACY_SOURCE_PROVENANCE = {
    "image.light.glow": (
        "AuraCoreAI Deployment/backend/modules/models/Aura/aura.model.glow.shader.js",
        "AuraCoreAI Deployment/backend/modules/models/Aura/aura.harmonic.pulse.weaver.js",
    ),
    "image.light.bloom": (
        "AuraCoreAI Complete avatar glow/light concepts",
        "AuraCoreAI Deployment visual/mood/pulse source",
    ),
    "image.light.shimmer": (
        "AuraCoreAI Deployment/backend/modules/models/Aura/aura.motion.shimmer.field.js",
    ),
}

_ORIGINAL_APPLY_EFFECT = image_compositor._apply_effect
_INSTALLED = False


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _clamp(value: Any, low: float, high: float, default: float) -> float:
    return max(low, min(high, _finite(value, default)))


def _effect_type(effect: dict[str, Any]) -> str:
    return str(effect.get("type") or "").strip().lower().replace("-", "_").replace(" ", "_")


def _hex_rgb(value: Any) -> tuple[float, float, float]:
    text = str(value or "").strip()
    if text.startswith("#"):
        text = text[1:]
    if len(text) == 3:
        text = "".join(character * 2 for character in text)
    if len(text) != 6 or any(character not in "0123456789abcdefABCDEF" for character in text):
        raise image_compositor.EditorRenderUnsupported("Shimmer colour must be a #RRGGBB or #RGB colour")
    return tuple(int(text[index : index + 2], 16) / 255.0 for index in (0, 2, 4))


def _rgba_from_rgb(rgb: Image.Image, alpha: Image.Image) -> Image.Image:
    result = rgb.convert("RGBA")
    result.putalpha(alpha)
    return result


def _screen_arrays(base: np.ndarray, light: np.ndarray) -> np.ndarray:
    return 1.0 - (1.0 - np.clip(base, 0.0, 1.0)) * (1.0 - np.clip(light, 0.0, 1.0))


def _apply_glow(rgb: Image.Image, *, radius: float, intensity: float) -> Image.Image:
    base = np.asarray(rgb.convert("RGB"), dtype=np.float32) / 255.0
    blurred = rgb.convert("RGB").filter(ImageFilter.GaussianBlur(radius=radius))
    halo = np.asarray(blurred, dtype=np.float32) / 255.0
    rendered = _screen_arrays(base, halo * intensity)
    return Image.fromarray(np.clip(rendered * 255.0, 0, 255).astype(np.uint8), mode="RGB")


def _apply_bloom(
    rgb: Image.Image,
    *,
    threshold: float,
    radius: float,
    intensity: float,
) -> Image.Image:
    base = np.asarray(rgb.convert("RGB"), dtype=np.float32) / 255.0
    luminance = base[..., 0] * 0.2126 + base[..., 1] * 0.7152 + base[..., 2] * 0.0722
    denominator = max(1e-6, 1.0 - threshold)
    highlight_weight = np.clip((luminance - threshold) / denominator, 0.0, 1.0)[..., None]
    highlights = np.clip(base * highlight_weight * 255.0, 0, 255).astype(np.uint8)
    blurred = Image.fromarray(highlights, mode="RGB").filter(ImageFilter.GaussianBlur(radius=radius))
    bloom = np.asarray(blurred, dtype=np.float32) / 255.0
    rendered = _screen_arrays(base, bloom * intensity)
    return Image.fromarray(np.clip(rendered * 255.0, 0, 255).astype(np.uint8), mode="RGB")


def _apply_shimmer(
    rgb: Image.Image,
    *,
    position: float,
    width: float,
    angle: float,
    intensity: float,
    colour: tuple[float, float, float],
) -> Image.Image:
    base = np.asarray(rgb.convert("RGB"), dtype=np.float32) / 255.0
    height, width_px = base.shape[:2]
    x = np.linspace(-0.5, 0.5, max(1, width_px), dtype=np.float32)
    y = np.linspace(-0.5, 0.5, max(1, height), dtype=np.float32)
    xx, yy = np.meshgrid(x, y)
    theta = math.radians(angle)
    axis = xx * math.cos(theta) + yy * math.sin(theta)
    centre = position - 0.5
    sigma = max(0.005, width / 2.355)
    band = np.exp(-0.5 * ((axis - centre) / sigma) ** 2)[..., None]
    tint = np.asarray(colour, dtype=np.float32).reshape((1, 1, 3))
    light = np.clip(band * intensity * tint, 0.0, 1.0)
    rendered = _screen_arrays(base, light)
    return Image.fromarray(np.clip(rendered * 255.0, 0, 255).astype(np.uint8), mode="RGB")


def apply_legacy_visual_effect(image: Image.Image, effect: dict[str, Any], time: float) -> Image.Image:
    """Execute recovered visual concepts as bounded current Pillow processors.

    The historical Aura modules are provenance/requirements input only. Their JavaScript/shader
    code is never executed or evaluated here.
    """
    if not effect.get("enabled", True):
        return image

    state = image_compositor._effect_state_at_time(effect, time)
    effect_type = _effect_type(state)
    if effect_type not in {"glow", "bloom", "light_sweep"}:
        return _ORIGINAL_APPLY_EFFECT(image, effect, time)

    params = state.get("parameters") or {}
    dry = image.convert("RGBA")
    alpha = dry.getchannel("A")
    rgb = dry.convert("RGB")

    if effect_type == "glow":
        wet_rgb = _apply_glow(
            rgb,
            radius=_clamp(params.get("radius"), 0.0, 80.0, 8.0),
            intensity=_clamp(params.get("intensity"), 0.0, 2.0, 0.55),
        )
    elif effect_type == "bloom":
        wet_rgb = _apply_bloom(
            rgb,
            threshold=_clamp(params.get("threshold"), 0.0, 1.0, 0.7),
            radius=_clamp(params.get("radius"), 0.0, 120.0, 18.0),
            intensity=_clamp(params.get("intensity"), 0.0, 3.0, 0.8),
        )
    else:
        wet_rgb = _apply_shimmer(
            rgb,
            position=_clamp(params.get("position"), 0.0, 1.0, 0.5),
            width=_clamp(params.get("width"), 0.01, 1.0, 0.18),
            angle=_clamp(params.get("angle"), -180.0, 180.0, 20.0),
            intensity=_clamp(params.get("intensity"), 0.0, 2.0, 0.45),
            colour=_hex_rgb(params.get("color", "#ffffff")),
        )

    wet = _rgba_from_rgb(wet_rgb, alpha)
    return image_compositor._mix_rgba(dry, wet, _finite(state.get("mix"), 1.0))


def _register_effect(effect_id: str, spec: catalogue.EffectSpec) -> None:
    existing = catalogue.EFFECTS.get(effect_id)
    if existing is not None and existing != spec:
        raise RuntimeError(f"Refusing to replace an existing visual effect contract: {effect_id}")
    catalogue.EFFECTS.setdefault(effect_id, spec)


def _register_keyword(keyword: str, effect_id: str) -> None:
    existing_base = catalogue._KEYWORDS.get(keyword)
    if existing_base is not None and existing_base != effect_id:
        raise RuntimeError(f"Refusing to replace existing visual-effect keyword: {keyword}")
    catalogue._KEYWORDS.setdefault(keyword, effect_id)

    media_map = hardening._MEDIA_KEYWORDS.setdefault(keyword, {})
    existing_hardened = media_map.get("image")
    if existing_hardened is not None and existing_hardened != effect_id:
        raise RuntimeError(f"Refusing to replace existing hardened visual-effect keyword: {keyword}")
    media_map.setdefault("image", effect_id)


def install_legacy_visual_effects() -> None:
    """Register bounded recovered-resource derivatives exactly once."""
    global _INSTALLED
    if _INSTALLED:
        return

    _register_effect(
        "image.light.glow",
        catalogue.EffectSpec(
            "image.light.glow",
            "Glow",
            "light",
            ("image",),
            "editor_effect",
            "glow",
            {
                "radius": catalogue._p_float(8.0, 0.0, 80.0),
                "intensity": catalogue._p_float(0.55, 0.0, 2.0),
            },
            scopes=("item", "track"),
        ),
    )
    _register_effect(
        "image.light.bloom",
        catalogue.EffectSpec(
            "image.light.bloom",
            "Bloom",
            "light",
            ("image",),
            "editor_effect",
            "bloom",
            {
                "threshold": catalogue._p_float(0.7, 0.0, 1.0),
                "radius": catalogue._p_float(18.0, 0.0, 120.0),
                "intensity": catalogue._p_float(0.8, 0.0, 3.0),
            },
            scopes=("item", "track"),
        ),
    )
    _register_effect(
        "image.light.shimmer",
        catalogue.EffectSpec(
            "image.light.shimmer",
            "Shimmer / Light Sweep",
            "light",
            ("image",),
            "editor_effect",
            "light_sweep",
            {
                "position": catalogue._p_float(0.5, 0.0, 1.0),
                "width": catalogue._p_float(0.18, 0.01, 1.0),
                "angle": catalogue._p_float(20.0, -180.0, 180.0),
                "intensity": catalogue._p_float(0.45, 0.0, 2.0),
                "color": catalogue._p_color("#ffffff"),
            },
            scopes=("item", "track"),
        ),
    )

    _register_keyword("glow", "image.light.glow")
    _register_keyword("bloom", "image.light.bloom")
    _register_keyword("shimmer", "image.light.shimmer")
    _register_keyword("light sweep", "image.light.shimmer")

    # The existing advanced and universal image compositors both resolve their normal effect path
    # through a module-level dispatcher. Extend that bounded dispatcher; do not create a parallel
    # renderer or expose legacy JavaScript/shader execution.
    image_compositor._apply_effect = apply_legacy_visual_effect
    universal_compositor._apply_effect = apply_legacy_visual_effect
    _INSTALLED = True


def legacy_visual_effect_provenance(effect_id: str) -> tuple[str, ...]:
    return tuple(_LEGACY_SOURCE_PROVENANCE.get(effect_id, ()))


__all__ = [
    "LEGACY_VISUAL_EFFECT_IDS",
    "apply_legacy_visual_effect",
    "install_legacy_visual_effects",
    "legacy_visual_effect_provenance",
]
