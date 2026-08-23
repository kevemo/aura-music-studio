from __future__ import annotations

import json
import os
import re

import requests
from pydantic import BaseModel, Field

from .session import Effect


SAFE_TYPES = {
    "gain", "eq", "highpass", "lowpass", "compressor", "limiter", "gate", "deesser",
    "reverb", "delay", "distortion", "saturation", "exciter", "chorus", "flanger", "phaser",
    "tremolo", "pitch_shift", "doubler", "stereo_width",
}


class FxDesignRequest(BaseModel):
    description: str = Field(min_length=3, max_length=1500)
    category: str = "creative"
    max_effects: int = Field(default=8, ge=1, le=12)


class FxDesignResult(BaseModel):
    description: str
    category: str
    effects: list[Effect]
    source: str
    notes: list[str] = Field(default_factory=list)


def _validate_effects(items: list[dict], max_effects: int) -> list[Effect]:
    result = []
    for item in items[:max_effects]:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("type") or "").strip().lower()
        if kind not in SAFE_TYPES:
            continue
        params = item.get("parameters") if isinstance(item.get("parameters"), dict) else {}
        # Effect's Pydantic model validates the allowed processor type. Keep chains declarative.
        result.append(Effect(type=kind, parameters=params))
    if not result:
        raise ValueError("Aura FX Designer produced no valid safe processors")
    return result


def _ollama_design(request: FxDesignRequest) -> FxDesignResult:
    base = (os.getenv("OLLAMA_BASE_URL") or "").rstrip("/")
    if not base:
        raise RuntimeError("OLLAMA_BASE_URL is not configured")
    model = os.getenv("AURA_OLLAMA_MODEL", "qwen3:4b")
    system = (
        "You are Aura FX Designer for a professional audio workstation. Return JSON only. "
        "Create a practical insert chain from this exact whitelist: " + ", ".join(sorted(SAFE_TYPES)) + ". "
        "Do not name or imitate proprietary commercial presets. Do not output shell commands, plugin paths, code, "
        "custom_safe_chain or convolution. Use conservative numeric settings suitable for real audio. "
        "Schema: {\"effects\":[{\"type\":\"...\",\"parameters\":{...}}],\"notes\":[\"...\"]}."
    )
    prompt = (
        f"Category: {request.category}\nDesired sound: {request.description}\n"
        f"Maximum processors: {request.max_effects}. Build the chain in sensible signal-flow order."
    )
    response = requests.post(
        f"{base}/api/chat",
        json={
            "model": model,
            "stream": False,
            "format": "json",
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "options": {"temperature": .35},
        },
        timeout=90,
    )
    response.raise_for_status()
    content = response.json().get("message", {}).get("content", "")
    payload = json.loads(content)
    effects = _validate_effects(payload.get("effects", []), request.max_effects)
    notes = [str(x)[:300] for x in payload.get("notes", []) if str(x).strip()][:8]
    return FxDesignResult(
        description=request.description,
        category=request.category,
        effects=effects,
        source="local_ollama",
        notes=notes,
    )


def _fallback_design(request: FxDesignRequest) -> FxDesignResult:
    """Deterministic safe fallback when the local reasoning model is offline."""
    text = request.description.lower()
    category = request.category.lower()
    effects: list[Effect] = []

    if category in {"vocal", "vocals"}:
        effects += [
            Effect(type="highpass", parameters={"hz": 80}),
            Effect(type="deesser", parameters={"frequency_hz": 6500, "reduction_db": 3.5}),
            Effect(type="compressor", parameters={"threshold_db": -20, "ratio": 2.4, "attack_ms": 15, "release_ms": 140}),
        ]
    elif category == "guitar":
        effects += [Effect(type="highpass", parameters={"hz": 70})]
    elif category == "bass":
        effects += [Effect(type="highpass", parameters={"hz": 32}), Effect(type="compressor", parameters={"threshold_db": -20, "ratio": 3.0})]
    elif category == "drums":
        effects += [Effect(type="compressor", parameters={"threshold_db": -17, "ratio": 2.5, "attack_ms": 25, "release_ms": 130})]

    if any(x in text for x in ["warm", "analog", "tape", "vintage"]):
        effects.append(Effect(type="saturation", parameters={"drive": 1.25}))
    if any(x in text for x in ["bright", "air", "crisp", "presence"]):
        effects.append(Effect(type="exciter", parameters={"amount": 2.0, "frequency_hz": 7500}))
    if any(x in text for x in ["wide", "double", "doubled"]):
        effects.append(Effect(type="doubler", parameters={"delay_ms": 20, "mix": .14, "width": 1.3}))
    if any(x in text for x in ["chorus", "lush", "shimmer"]):
        effects.append(Effect(type="chorus", parameters={"delay_ms": 18, "decay": .32, "rate_hz": .55, "depth": 2.8}))
    if "flang" in text:
        effects.append(Effect(type="flanger", parameters={"delay_ms": 2.0, "depth_ms": 2.8, "feedback": 12, "rate_hz": .4}))
    if "phas" in text:
        effects.append(Effect(type="phaser", parameters={"rate_hz": .35, "decay": .45}))
    if "tremolo" in text:
        effects.append(Effect(type="tremolo", parameters={"rate_hz": 4.0, "depth": .45}))
    if any(x in text for x in ["distort", "grit", "drive", "crunch"]):
        effects.append(Effect(type="distortion", parameters={"drive": 1.8}))
    if any(x in text for x in ["delay", "echo", "slap"]):
        ms = 115 if "slap" in text else 320
        effects.append(Effect(type="delay", parameters={"delay_ms": ms, "feedback": .18}))
    if any(x in text for x in ["reverb", "room", "plate", "hall", "space", "ambient"]):
        mix = .28 if any(x in text for x in ["hall", "ambient", "space"]) else .14
        effects.append(Effect(type="reverb", parameters={"predelay_ms": 35, "mix": mix}))
    if any(x in text for x in ["lofi", "lo-fi", "telephone", "phone"]):
        effects += [Effect(type="highpass", parameters={"hz": 250}), Effect(type="lowpass", parameters={"hz": 6500})]

    if not effects:
        effects = [Effect(type="eq", parameters={"low_db": 0, "mid_db": .5, "mid_hz": 2200, "high_db": .5})]
    effects = effects[: request.max_effects]
    return FxDesignResult(
        description=request.description,
        category=request.category,
        effects=effects,
        source="deterministic_fallback",
        notes=["Local reasoning model was unavailable; Aura used the safe built-in FX grammar."],
    )


def design_fx(request: FxDesignRequest) -> FxDesignResult:
    if os.getenv("AURA_PRODUCER_USE_OLLAMA", "true").lower() in {"1", "true", "yes", "on"}:
        try:
            return _ollama_design(request)
        except Exception:
            pass
    return _fallback_design(request)


def safe_slug(text: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (value[:60] or "aura-fx")
