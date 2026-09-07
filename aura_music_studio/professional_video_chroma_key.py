from __future__ import annotations

import math
import re
from typing import Any

from .professional_editor_renderer import EditorRenderUnsupported

CHROMA_KEY_EFFECT = "video.key.chroma"
_HEX_COLOR = re.compile(r"^(?:#|0x)?([0-9a-fA-F]{6})(?:[0-9a-fA-F]{2})?$")


def _finite(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _clamp(value: Any, low: float, high: float, default: float) -> float:
    return max(low, min(high, _finite(value, default)))


def _ff(value: float) -> str:
    return f"{float(value):.8f}".rstrip("0").rstrip(".") or "0"


def _screen(params: dict[str, Any]) -> str:
    value = str(params.get("screen") or "green").strip().lower().replace("screen", "")
    if value in {"green", "blue", "custom"}:
        return value
    raise EditorRenderUnsupported("Chroma key screen must be green, blue or custom")


def _key_color(params: dict[str, Any], screen: str) -> str:
    default = "#00ff00" if screen == "green" else "#0000ff" if screen == "blue" else ""
    raw = str(params.get("color") or default).strip()
    aliases = {
        "green": "00ff00",
        "lime": "00ff00",
        "blue": "0000ff",
    }
    compact = aliases.get(raw.lower())
    if compact is None:
        match = _HEX_COLOR.fullmatch(raw)
        if not match:
            raise EditorRenderUnsupported(
                "Chroma key color must be green, blue, #RRGGBB, 0xRRGGBB or an 8-digit hex color"
            )
        compact = match.group(1)
    return "0x" + compact.lower()


def chroma_key_filter(effect: dict[str, Any]) -> str:
    """Compile a bounded chroma-key + optional despill chain that preserves alpha.

    FFmpeg colorkey operates in RGB and writes transparency into the alpha channel. Despill is
    applied afterwards with alpha modification disabled, so reflected screen colour can be reduced
    without replacing the keyer's matte. The downstream compositor must keep this chain in RGBA.
    """

    params = effect.get("parameters") or {}
    if not isinstance(params, dict):
        raise EditorRenderUnsupported("Chroma key parameters must be a mapping")
    screen = _screen(params)
    color = _key_color(params, screen)
    similarity = _clamp(params.get("similarity"), 0.00001, 1.0, 0.12)
    blend = _clamp(params.get("blend"), 0.0, 1.0, 0.08)
    despill = _clamp(params.get("despill"), 0.0, 1.0, 0.5 if screen in {"green", "blue"} else 0.0)
    expand = _clamp(params.get("despill_expand"), 0.0, 1.0, 0.08)

    filters = [
        "format=rgba",
        f"colorkey=color={color}:similarity={_ff(similarity)}:blend={_ff(blend)}",
    ]
    if screen in {"green", "blue"} and despill > 1e-8:
        filters.append(
            f"despill=type={screen}:mix={_ff(despill)}:expand={_ff(expand)}:alpha=0"
        )
    filters.append("format=rgba")
    return ",".join(filters)


def effect_requires_alpha(effect: dict[str, Any]) -> bool:
    if not effect.get("enabled", True):
        return False
    if str(effect.get("type") or "").strip().lower() != CHROMA_KEY_EFFECT:
        return False
    if _clamp(effect.get("mix"), 0.0, 1.0, 1.0) > 1e-8:
        return True

    # A base mix of zero can still become visible later in the timeline. Inspect authored mix
    # keyframes before choosing the transient codec so an animated keyer can never be flattened by
    # the ordinary yuv420p derivative path merely because its first/static value is dry.
    keyframes = effect.get("keyframes") or {}
    if not isinstance(keyframes, dict):
        return False
    points = keyframes.get("mix") or []
    if not isinstance(points, list):
        return False
    return any(
        isinstance(point, dict)
        and _clamp(point.get("value"), 0.0, 1.0, 0.0) > 1e-8
        for point in points
    )


def chain_requires_alpha(effects: list[dict[str, Any]] | None) -> bool:
    return any(effect_requires_alpha(effect) for effect in effects or [])


__all__ = [
    "CHROMA_KEY_EFFECT",
    "chroma_key_filter",
    "effect_requires_alpha",
    "chain_requires_alpha",
]
