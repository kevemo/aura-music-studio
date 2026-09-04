from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

from .aura_effect_system_creator import EffectNodeSpec, compile_effect_system, make_effect_system
from .creative_catalogue import get_catalogue_item

MAX_EFFECT_PROMPT_CHARS = 1200


@dataclass(frozen=True, slots=True)
class PromptIntent:
    catalogue_item_id: str
    aliases: tuple[str, ...]


_INTENTS: tuple[PromptIntent, ...] = (
    PromptIntent("music.fx.highpass", ("high pass", "high-pass", "low cut", "low-cut")),
    PromptIntent("music.fx.lowpass", ("low pass", "low-pass", "high cut", "high-cut")),
    PromptIntent("music.fx.compressor", ("compressor", "compress", "compression", "control dynamics")),
    PromptIntent("music.fx.limiter", ("limiter", "limit peaks", "peak limiter")),
    PromptIntent("music.fx.reverb", ("reverb", "ambience", "room sound", "more space")),
    PromptIntent("music.fx.delay", ("delay", "echo", "slapback")),
    PromptIntent("music.fx.saturation", ("saturation", "saturate", "warmth", "harmonic drive")),
    PromptIntent("music.fx.chorus", ("chorus", "thicken", "thickening")),
    PromptIntent("music.fx.stereo_width", ("stereo width", "widen", "wider", "stereo image")),
    PromptIntent("music.fx.gain", ("gain", "volume", "louder", "quieter")),
)


def _normalize_prompt(prompt: str) -> str:
    if not isinstance(prompt, str):
        raise ValueError("Effect-system prompt must be text")
    normalized = " ".join(prompt.strip().split())
    if not normalized:
        raise ValueError("Effect-system prompt is required")
    if len(normalized) > MAX_EFFECT_PROMPT_CHARS:
        raise ValueError(f"Effect-system prompt exceeds {MAX_EFFECT_PROMPT_CHARS} characters")
    if any(ord(ch) < 32 for ch in normalized):
        raise ValueError("Effect-system prompt contains unsupported control characters")
    return normalized


def _alias_pattern(alias: str) -> str:
    """Match an intent alias as a complete token/phrase, never as a substring.

    This keeps short aliases such as ``gain`` from matching unrelated words such as
    ``again`` while preserving multi-word and hyphenated catalogue phrases.
    """
    return rf"(?<!\w){re.escape(alias)}(?!\w)"


def _first_alias_position(text: str, aliases: tuple[str, ...]) -> int | None:
    positions: list[int] = []
    for alias in aliases:
        match = re.search(_alias_pattern(alias), text, flags=re.IGNORECASE)
        if match:
            positions.append(match.start())
    return min(positions) if positions else None


def _numeric_after_alias(text: str, aliases: tuple[str, ...], *, unit: str) -> float | None:
    for alias in aliases:
        match = re.search(
            rf"{_alias_pattern(alias)}[^0-9+-]{{0,28}}([+-]?\d+(?:\.\d+)?)\s*{re.escape(unit)}\b",
            text,
            flags=re.IGNORECASE,
        )
        if match:
            return float(match.group(1))
    return None


def _percent_after_alias(text: str, aliases: tuple[str, ...]) -> float | None:
    for alias in aliases:
        match = re.search(
            rf"{_alias_pattern(alias)}[^0-9]{{0,28}}(\d+(?:\.\d+)?)\s*%",
            text,
            flags=re.IGNORECASE,
        )
        if match:
            return float(match.group(1)) / 100.0
    return None


def _parameters_for(item_id: str, prompt: str) -> dict[str, Any]:
    lowered = prompt.casefold()
    params: dict[str, Any] = {}
    if item_id == "music.fx.highpass":
        value = _numeric_after_alias(lowered, ("high pass", "high-pass", "low cut", "low-cut"), unit="hz")
        if value is not None:
            params["hz"] = value
    elif item_id == "music.fx.lowpass":
        value = _numeric_after_alias(lowered, ("low pass", "low-pass", "high cut", "high-cut"), unit="hz")
        if value is not None:
            params["hz"] = value
    elif item_id == "music.fx.gain":
        value = _numeric_after_alias(lowered, ("gain", "volume"), unit="db")
        if value is not None:
            params["db"] = value
    elif item_id == "music.fx.reverb":
        value = _percent_after_alias(lowered, ("reverb", "ambience"))
        if value is not None:
            params["mix"] = value
    elif item_id == "music.fx.delay":
        value = _numeric_after_alias(lowered, ("delay", "echo", "slapback"), unit="ms")
        if value is not None:
            params["delay_ms"] = value
    elif item_id == "music.fx.stereo_width":
        value = _percent_after_alias(lowered, ("stereo width", "widen", "wider"))
        if value is not None:
            params["width"] = value * 2.0
    elif item_id == "music.fx.compressor":
        ratio = re.search(r"\b(\d+(?:\.\d+)?)\s*:\s*1\b", lowered)
        if ratio:
            params["ratio"] = float(ratio.group(1))
    return params


def compose_effect_system_from_prompt(
    prompt: str,
    *,
    system_id: str | None = None,
    name: str | None = None,
) -> dict[str, Any]:
    normalized = _normalize_prompt(prompt)
    lowered = normalized.casefold()

    matches: list[tuple[int, int, PromptIntent]] = []
    for order, intent in enumerate(_INTENTS):
        position = _first_alias_position(lowered, intent.aliases)
        if position is not None:
            matches.append((position, order, intent))
    matches.sort(key=lambda row: (row[0], row[1]))

    if not matches:
        raise ValueError("Prompt does not contain a currently supported executable music effect intent")

    seen_items: set[str] = set()
    nodes: list[EffectNodeSpec] = []
    required_entitlements: list[str] = []
    for _, _, intent in matches:
        if intent.catalogue_item_id in seen_items:
            continue
        seen_items.add(intent.catalogue_item_id)
        item = get_catalogue_item(intent.catalogue_item_id)
        nodes.append(
            EffectNodeSpec(
                id=f"fx{len(nodes) + 1:02d}",
                catalogue_item_id=item.id,
                parameters=_parameters_for(item.id, normalized),
            )
        )
        if item.entitlement != "core" and item.entitlement not in required_entitlements:
            required_entitlements.append(item.entitlement)

    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    resolved_id = system_id or f"aura.prompt.{digest[:16]}"
    resolved_name = (name or f"Aura Prompt Effect {digest[:8]}").strip()
    spec = make_effect_system(
        resolved_id,
        resolved_name,
        nodes,
        description=f"Bounded Aura effect system composed from prompt fingerprint {digest[:12]}.",
    )
    compiled = compile_effect_system(spec).public()
    compiled.update(
        {
            "prompt_fingerprint": digest,
            "prompt_length": len(normalized),
            "composer": "bounded_catalogue_intent_v1",
            "required_entitlement_bands": required_entitlements,
            "entitlement_verified": False,
            "preview_required_before_apply": True,
            "project_mutated": False,
            "arbitrary_command_execution": False,
        }
    )
    return compiled


__all__ = [
    "MAX_EFFECT_PROMPT_CHARS",
    "PromptIntent",
    "compose_effect_system_from_prompt",
]
