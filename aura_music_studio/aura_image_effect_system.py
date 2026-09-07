from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from .executable_image_effects import (
    ImageEffectGraph,
    ImageEffectNode,
    load_image_effect_preset,
    render_image_effect_graph,
    save_image_effect_preset,
)

_MAX_PROMPT_LENGTH = 1200
_MAX_PROMPT_NODES = 16
_SAFE_PRESET_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
_PERCENT = re.compile(r"(?<!\d)(\d{1,3})(?:\s*%)")
_NUMBER = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)(?![\d.])")
_PROTECTED_AND = "__AURA_IMAGE_AND__"

# Natural-language text may only select one of the executable local Pillow transforms already
# enforced by executable_image_effects.py. It cannot introduce code, URLs, plugins or commands.
_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("brightness", ("brightness", "brighten", "brighter", "darken", "darker")),
    ("contrast", ("contrast",)),
    ("saturation", ("saturation", "saturate", "colour intensity", "color intensity", "desaturate")),
    ("sharpness", ("sharpness", "sharpen", "sharper")),
    ("gaussian_blur", ("gaussian blur", "blur", "soften", "softer")),
    ("grayscale", ("grayscale", "greyscale", "black and white", "black & white", "monochrome")),
    ("invert", ("invert", "negative")),
    ("posterize", ("posterize", "posterise")),
)


def _prompt_fingerprint(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def _bounded_prompt(prompt: str) -> str:
    clean = " ".join(str(prompt or "").strip().split())
    if not clean:
        raise ValueError("Image effect prompt is required")
    if len(clean) > _MAX_PROMPT_LENGTH:
        raise ValueError(f"Image effect prompt exceeds {_MAX_PROMPT_LENGTH} characters")
    return clean


def _sentences(prompt: str) -> list[str]:
    # A plain conjunction is a useful effect separator ("brightness and contrast"), but
    # "black and white" is itself one allowlisted grayscale alias and must stay intact.
    protected = re.sub(
        r"\bblack\s+and\s+white\b",
        f"black {_PROTECTED_AND} white",
        prompt,
        flags=re.IGNORECASE,
    )
    parts = re.split(
        r"[;,]|\band\s+then\b|\bthen\b|\band\b",
        protected,
        flags=re.IGNORECASE,
    )
    return [part.replace(_PROTECTED_AND, "and").strip() for part in parts if part.strip()]


def _contains_alias(text: str, alias: str) -> bool:
    return re.search(rf"(?<![A-Za-z0-9_]){re.escape(alias)}(?![A-Za-z0-9_])", text, flags=re.IGNORECASE) is not None


def _strength(text: str, *, default: float = 1.0) -> float:
    percent = _PERCENT.search(text)
    if percent:
        return max(0.0, min(4.0, int(percent.group(1)) / 100.0))
    number = _NUMBER.search(text)
    if number:
        return max(0.0, min(4.0, float(number.group(1))))
    lowered = text.casefold()
    if any(word in lowered for word in ("slight", "subtle", "gentle", "little")):
        return 0.8 if default == 1.0 else default * 0.6
    if any(word in lowered for word in ("strong", "heavy", "intense", "dramatic")):
        return 1.6 if default == 1.0 else min(4.0, default * 1.8)
    return default


def _node(kind: str, text: str) -> ImageEffectNode:
    lowered = text.casefold()
    if kind == "brightness":
        amount = _strength(text)
        if "darken" in lowered or "darker" in lowered:
            amount = 0.7 if amount == 1.0 else max(0.0, min(1.0, amount))
        elif amount == 1.0:
            amount = 1.2
        return ImageEffectNode(kind=kind, amount=amount)
    if kind == "contrast":
        amount = _strength(text)
        return ImageEffectNode(kind=kind, amount=1.2 if amount == 1.0 else amount)
    if kind == "saturation":
        amount = _strength(text)
        if "desaturate" in lowered:
            amount = 0.6 if amount == 1.0 else max(0.0, min(1.0, amount))
        elif amount == 1.0:
            amount = 1.2
        return ImageEffectNode(kind=kind, amount=amount)
    if kind == "sharpness":
        amount = _strength(text)
        return ImageEffectNode(kind=kind, amount=1.5 if amount == 1.0 else amount)
    if kind == "gaussian_blur":
        radius = _strength(text, default=2.0)
        return ImageEffectNode(kind=kind, radius=max(0.0, min(50.0, radius)))
    if kind == "posterize":
        number = _NUMBER.search(text)
        bits = int(float(number.group(1))) if number else 6
        return ImageEffectNode(kind=kind, bits=max(1, min(8, bits)))
    return ImageEffectNode(kind=kind)


def compose_image_effect_system(prompt: str, *, name: str = "Aura Image FX") -> dict[str, Any]:
    """Convert bounded natural-language image instructions into an executable typed graph."""
    clean = _bounded_prompt(prompt)
    nodes: list[ImageEffectNode] = []
    unsupported: list[str] = []

    for sentence in _sentences(clean):
        matched = False
        for kind, aliases in _ALIASES:
            if any(_contains_alias(sentence, alias) for alias in aliases):
                nodes.append(_node(kind, sentence))
                matched = True
                break
        if not matched:
            unsupported.append(sentence)

    if unsupported:
        raise ValueError("Unsupported image effect instruction: " + "; ".join(unsupported[:3]))
    if not nodes:
        raise ValueError("Prompt did not select a supported executable image effect")
    if len(nodes) > _MAX_PROMPT_NODES:
        raise ValueError(f"Image effect prompt exceeds {_MAX_PROMPT_NODES} executable nodes")

    graph = ImageEffectGraph(name=str(name or "Aura Image FX").strip()[:120] or "Aura Image FX", nodes=nodes)
    return {
        "graph": graph.model_dump(mode="json"),
        "fingerprint": graph.fingerprint(),
        "prompt_fingerprint": _prompt_fingerprint(clean),
        "backend_executable": True,
        "editable_graph": True,
        "runtime": "local_allowlisted_pillow",
        "arbitrary_code_execution": False,
        "network_access": False,
        "project_mutated": False,
    }


def preview_image_effect_system(
    source: str | Path,
    destination: str | Path,
    graph: ImageEffectGraph | dict[str, Any],
) -> dict[str, Any]:
    spec = graph if isinstance(graph, ImageEffectGraph) else ImageEffectGraph.model_validate(graph)
    evidence = render_image_effect_graph(source, destination, spec)
    return {
        **evidence,
        "preview": True,
        "preview_token": spec.fingerprint(),
        "editable_graph": True,
        "source_media_mutated": False,
    }


def save_reusable_image_effect_system(
    directory: str | Path,
    preset_name: str,
    graph: ImageEffectGraph | dict[str, Any],
    *,
    expected_fingerprint: str,
) -> dict[str, Any]:
    """Persist a reusable graph only when it matches the exact previewed fingerprint."""
    clean_name = str(preset_name or "").strip()
    if not _SAFE_PRESET_NAME.fullmatch(clean_name):
        raise ValueError("Preset name contains unsupported characters")
    spec = graph if isinstance(graph, ImageEffectGraph) else ImageEffectGraph.model_validate(graph)
    current = spec.fingerprint()
    expected = str(expected_fingerprint or "").strip().casefold()
    if len(expected) != 64 or any(ch not in "0123456789abcdef" for ch in expected):
        raise ValueError("Expected fingerprint must be a SHA-256 hex digest")
    if current.casefold() != expected:
        raise RuntimeError("Image effect graph changed after preview; preview the current graph again before save")
    save_image_effect_preset(directory, clean_name, spec)
    return {
        "saved": True,
        "preset_name": clean_name,
        "fingerprint": current,
        "path_exposed": False,
        "private_reusable_preset": True,
        "marketplace_published": False,
        "sale_enabled": False,
        "source_media_mutated": False,
    }


def load_reusable_image_effect_system(directory: str | Path, preset_name: str) -> dict[str, Any]:
    graph = load_image_effect_preset(directory, preset_name)
    return {
        "preset_name": str(preset_name),
        "graph": graph.model_dump(mode="json"),
        "fingerprint": graph.fingerprint(),
        "private_reusable_preset": True,
        "marketplace_published": False,
        "sale_enabled": False,
        "editable_graph": True,
    }


__all__ = [
    "compose_image_effect_system",
    "load_reusable_image_effect_system",
    "preview_image_effect_system",
    "save_reusable_image_effect_system",
]
