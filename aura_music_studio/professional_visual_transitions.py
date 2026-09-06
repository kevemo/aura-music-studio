from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable

from . import visual_effect_catalogue as catalogue
from . import visual_effect_catalogue_hardening as hardening
from .professional_editor_renderer import EditorRenderUnsupported


TRANSITION_EFFECT_IDS = frozenset(
    {
        "video.transition.fade_in",
        "video.transition.fade_out",
        "video.transition.cross_dissolve",
    }
)

TRANSITION_RUNTIME_TYPES = frozenset(
    {
        "transition_fade_in",
        "transition_fade_out",
        "transition_cross_dissolve",
    }
)

_MIN_DURATION = 0.05
_MAX_DURATION = 30.0
_INSTALLED = False


@dataclass(frozen=True)
class TransitionEnvelope:
    direction: str
    start: float
    duration: float
    transition_type: str
    peer_item_id: str | None = None


def _finite(value: Any, *, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a finite number") from exc
    if not math.isfinite(number):
        raise ValueError(f"{field} must be a finite number")
    return number


def _runtime_type(effect: dict[str, Any]) -> str:
    return str(effect.get("type") or "").strip().lower().replace("-", "_").replace(" ", "_")


def is_transition_effect(effect: dict[str, Any]) -> bool:
    return _runtime_type(effect) in TRANSITION_RUNTIME_TYPES


def _duration(effect: dict[str, Any], item: dict[str, Any]) -> float:
    parameters = effect.get("parameters") or {}
    if set(parameters) - {"duration"}:
        raise ValueError("Visual transition accepts only the bounded duration parameter")
    duration = _finite(parameters.get("duration", 0.5), field="transition duration")
    if duration < _MIN_DURATION or duration > _MAX_DURATION:
        raise ValueError(f"transition duration must be between {_MIN_DURATION:g} and {_MAX_DURATION:g} seconds")
    item_duration = _finite(item.get("duration"), field="item duration")
    if duration > item_duration + 1e-9:
        raise ValueError("transition duration cannot exceed the timeline item duration")
    return duration


def validate_transition_effect(effect: dict[str, Any], item: dict[str, Any]) -> float:
    runtime_type = _runtime_type(effect)
    if runtime_type not in TRANSITION_RUNTIME_TYPES:
        raise ValueError("Unknown visual transition runtime")
    mix = _finite(effect.get("mix", 1.0), field="transition mix")
    if abs(mix - 1.0) > 1e-9:
        raise ValueError("Visual transitions execute directly and require mix=1")
    if effect.get("keyframes"):
        raise ValueError("Visual transitions use bounded duration and do not accept effect keyframes")
    return _duration(effect, item)


def validate_transition_apply(
    state: dict[str, Any],
    target_id: str,
    *,
    runtime_type: str,
    parameters: dict[str, Any],
    mix: float,
    keyframes: dict[str, Any],
) -> None:
    branch = state.get("branch") or {}
    items = {str(row.get("id")): row for row in branch.get("items", []) if row.get("id")}
    tracks = {str(row.get("id")): row for row in branch.get("tracks", []) if row.get("id")}
    target = items.get(target_id)
    if target is None:
        raise KeyError(target_id)
    if target.get("kind") != "video_clip":
        raise ValueError("Visual transitions require a video_clip editor item")

    candidate = {
        "type": runtime_type,
        "enabled": True,
        "mix": mix,
        "parameters": parameters,
        "keyframes": keyframes,
    }
    validate_transition_effect(candidate, target)

    existing = [
        effect
        for effect in target.get("effects") or []
        if effect.get("enabled", True) and is_transition_effect(effect)
    ]
    if existing:
        raise ValueError("A video item may have only one active visual transition")

    track = tracks.get(str(target.get("track_id") or ""))
    if track is None:
        raise ValueError("Visual transition target must belong to a current video track")
    ordered = [items[item_id] for item_id in track.get("item_ids", []) if item_id in items]
    ordered.append({**target, "effects": [candidate]})
    deduped: dict[str, dict[str, Any]] = {}
    for item in ordered:
        deduped[str(item.get("id"))] = item
    build_transition_envelopes(deduped.values())


def _eligible_visual_items(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        [
            item
            for item in items
            if item.get("enabled", True)
            and item.get("visible", True)
            and item.get("kind") in {"video_clip", "image_layer", "text"}
        ],
        key=lambda item: (_finite(item.get("start"), field="item start"), str(item.get("id") or "")),
    )


def build_transition_envelopes(items: Iterable[dict[str, Any]]) -> dict[str, list[TransitionEnvelope]]:
    """Resolve bounded transition effects to absolute timeline alpha envelopes.

    Cross dissolve lives on the incoming clip. The outgoing peer is inferred from the immediately
    preceding visible item on the same track; users never provide raw peer ids or FFmpeg filters.
    """
    ordered = _eligible_visual_items(items)
    envelopes: dict[str, list[TransitionEnvelope]] = defaultdict(list)

    for index, item in enumerate(ordered):
        active = [
            effect
            for effect in item.get("effects") or []
            if effect.get("enabled", True) and is_transition_effect(effect)
        ]
        if len(active) > 1:
            raise ValueError("A video item may have only one active visual transition")
        if not active:
            continue

        effect = active[0]
        duration = validate_transition_effect(effect, item)
        runtime_type = _runtime_type(effect)
        item_id = str(item.get("id") or "")
        start = _finite(item.get("start"), field="item start")
        item_duration = _finite(item.get("duration"), field="item duration")

        if runtime_type == "transition_fade_in":
            envelopes[item_id].append(TransitionEnvelope("in", start, duration, runtime_type))
            continue
        if runtime_type == "transition_fade_out":
            envelopes[item_id].append(
                TransitionEnvelope("out", start + item_duration - duration, duration, runtime_type)
            )
            continue

        if index == 0:
            raise ValueError("Cross dissolve requires a preceding visible timeline item")
        outgoing = ordered[index - 1]
        outgoing_id = str(outgoing.get("id") or "")
        outgoing_start = _finite(outgoing.get("start"), field="outgoing item start")
        outgoing_duration = _finite(outgoing.get("duration"), field="outgoing item duration")
        outgoing_end = outgoing_start + outgoing_duration
        if outgoing_start >= start - 1e-9:
            raise ValueError("Cross dissolve incoming item must start after its outgoing item")
        overlap = outgoing_end - start
        if overlap + 1e-9 < duration:
            raise ValueError("Cross dissolve requires timeline overlap at least as long as its duration")
        if duration > outgoing_duration + 1e-9:
            raise ValueError("Cross dissolve duration cannot exceed the outgoing item duration")

        envelopes[outgoing_id].append(
            TransitionEnvelope("out", start, duration, runtime_type, peer_item_id=item_id)
        )
        envelopes[item_id].append(
            TransitionEnvelope("in", start, duration, runtime_type, peer_item_id=outgoing_id)
        )

    return dict(envelopes)


def append_transition_filters(
    chain: str,
    envelopes: Iterable[TransitionEnvelope],
    *,
    ff,
) -> str:
    for envelope in envelopes:
        direction = "in" if envelope.direction == "in" else "out"
        chain += (
            f"fade=t={direction}:st={ff(envelope.start)}:"
            f"d={ff(envelope.duration)}:alpha=1,"
        )
    return chain


def _register_effect(effect_id: str, spec: catalogue.EffectSpec) -> None:
    existing = catalogue.EFFECTS.get(effect_id)
    if existing is not None and existing != spec:
        raise RuntimeError(f"Refusing to replace an existing visual transition contract: {effect_id}")
    catalogue.EFFECTS.setdefault(effect_id, spec)


def _register_keyword(keyword: str, effect_id: str) -> None:
    existing = catalogue._KEYWORDS.get(keyword)
    if existing is not None and existing != effect_id:
        raise RuntimeError(f"Refusing to replace existing visual-effect keyword: {keyword}")
    catalogue._KEYWORDS.setdefault(keyword, effect_id)

    media_map = hardening._MEDIA_KEYWORDS.setdefault(keyword, {})
    hardened = media_map.get("video")
    if hardened is not None and hardened != effect_id:
        raise RuntimeError(f"Refusing to replace existing hardened transition keyword: {keyword}")
    media_map.setdefault("video", effect_id)


def install_professional_visual_transitions() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    duration = {"duration": catalogue._p_float(0.5, _MIN_DURATION, _MAX_DURATION)}
    _register_effect(
        "video.transition.fade_in",
        catalogue.EffectSpec(
            "video.transition.fade_in",
            "Fade In",
            "transition",
            ("video",),
            "editor_effect",
            "transition_fade_in",
            duration,
            supports_keyframes=False,
            scopes=("item",),
        ),
    )
    _register_effect(
        "video.transition.fade_out",
        catalogue.EffectSpec(
            "video.transition.fade_out",
            "Fade Out",
            "transition",
            ("video",),
            "editor_effect",
            "transition_fade_out",
            duration,
            supports_keyframes=False,
            scopes=("item",),
        ),
    )
    _register_effect(
        "video.transition.cross_dissolve",
        catalogue.EffectSpec(
            "video.transition.cross_dissolve",
            "Cross Dissolve",
            "transition",
            ("video",),
            "editor_effect",
            "transition_cross_dissolve",
            duration,
            supports_keyframes=False,
            scopes=("item",),
        ),
    )

    _register_keyword("fade in", "video.transition.fade_in")
    _register_keyword("fade out", "video.transition.fade_out")
    _register_keyword("cross dissolve", "video.transition.cross_dissolve")
    _register_keyword("crossfade", "video.transition.cross_dissolve")
    _INSTALLED = True


def is_transition_effect_id(effect_id: str) -> bool:
    return str(effect_id or "") in TRANSITION_EFFECT_IDS


__all__ = [
    "TRANSITION_EFFECT_IDS",
    "TRANSITION_RUNTIME_TYPES",
    "TransitionEnvelope",
    "append_transition_filters",
    "build_transition_envelopes",
    "install_professional_visual_transitions",
    "is_transition_effect",
    "is_transition_effect_id",
    "validate_transition_apply",
    "validate_transition_effect",
]
