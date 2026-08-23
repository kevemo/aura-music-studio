from __future__ import annotations

import json
import os
import re
from typing import Literal

import requests
from pydantic import BaseModel, Field


class ProducerAction(BaseModel):
    action: Literal[
        "generate_layer", "replace_region", "extend", "remix", "separate_stems", "create_harmony",
        "master", "change_mix", "add_effect", "analyze", "create_song", "clean_audio", "spatialize",
        "amp_tone", "video_sync", "comp_takes", "pitch_timing", "export", "unknown"
    ] = "unknown"
    track_role: str | None = None
    start_seconds: float | None = None
    end_seconds: float | None = None
    prompt: str = ""
    parameters: dict = Field(default_factory=dict)


class ProducerPlan(BaseModel):
    user_request: str
    interpretation: str
    actions: list[ProducerAction] = Field(default_factory=list)
    needs_confirmation: bool = False
    notes: list[str] = Field(default_factory=list)


TRACK_WORDS = {
    "drum": "drums", "drums": "drums", "bass": "bass", "guitar": "guitar", "piano": "piano",
    "keys": "keyboard", "keyboard": "keyboard", "string": "strings", "strings": "strings",
    "vocal": "vocals", "vocals": "vocals", "harmony": "backing_vocals", "harmonies": "backing_vocals",
    "percussion": "percussion", "synth": "synth", "choir": "backing_vocals",
}

PRODUCER_SYSTEM = """You are Aura, the autonomous AI producer inside Elevate Souls Productions' Live Sound Studio.
Convert each member request into safe, non-destructive Studio operations. Never choose MIDI, SoundFont or symbolic guide
audio as final music. Do not bypass membership, rights, consent or voice-profile checks. Return JSON matching the supplied
ProducerPlan schema. Prefer concrete track roles, time ranges and parameters only when supported by the request/context.
If a destructive or ambiguous edit lacks required detail, set needs_confirmation=true. Voice conversion is allowed only
when the session contains an approved consent/rights record for that voice profile."""


def _track_from_text(text: str) -> str | None:
    lower = text.lower()
    for word, role in TRACK_WORDS.items():
        if re.search(rf"\b{re.escape(word)}\b", lower):
            return role
    return None


def _parse_time_range(text: str) -> tuple[float | None, float | None]:
    def sec(token: str) -> float:
        if ":" in token:
            m, s = token.split(":", 1)
            return float(m) * 60 + float(s)
        return float(token)
    match = re.search(
        r"(\d+(?::\d+(?:\.\d+)?)?)\s*(?:-|to|–)\s*(\d+(?::\d+(?:\.\d+)?)?)\s*(?:s|sec|secs|seconds)?",
        text,
        re.I,
    )
    if match:
        return sec(match.group(1)), sec(match.group(2))
    return None, None


def rule_based_plan(request: str) -> ProducerPlan:
    text = request.strip()
    lower = text.lower()
    track = _track_from_text(text)
    start, end = _parse_time_range(text)
    actions: list[ProducerAction] = []

    if any(x in lower for x in ["replace", "repaint", "redo this section", "regenerate this section"]):
        actions.append(ProducerAction(action="replace_region", track_role=track, start_seconds=start, end_seconds=end, prompt=text))
    elif any(x in lower for x in ["extend", "make it longer", "add an outro", "add an intro"]):
        actions.append(ProducerAction(action="extend", track_role=track, prompt=text))
    elif any(x in lower for x in ["separate", "split stems", "stem split", "remove vocals", "instrumental from"]):
        actions.append(ProducerAction(action="separate_stems", track_role=track, prompt=text))
    elif any(x in lower for x in ["harmony", "harmonies", "backing vocal", "choir"]):
        actions.append(ProducerAction(action="create_harmony", track_role="backing_vocals", prompt=text))
    elif any(x in lower for x in ["denoise", "de-noise", "clean vocal", "clean audio", "remove noise", "remove hum", "repair audio", "spectral repair"]):
        actions.append(ProducerAction(action="clean_audio", track_role=track, prompt=text))
    elif any(x in lower for x in ["spatial", "binaural", "ambisonic", "3d audio", "3d mix", "surround"]):
        actions.append(ProducerAction(action="spatialize", track_role=track, prompt=text))
    elif any(x in lower for x in ["amp tone", "guitar amp", "tube amp", "cab tone", "neural amp"]):
        actions.append(ProducerAction(action="amp_tone", track_role=track or "guitar", prompt=text))
    elif any(x in lower for x in ["sync to video", "score this video", "music to video", "hit the cuts", "scene cuts"]):
        actions.append(ProducerAction(action="video_sync", prompt=text))
    elif any(x in lower for x in ["comp takes", "comp the vocal", "best takes", "take lanes"]):
        actions.append(ProducerAction(action="comp_takes", track_role=track, prompt=text))
    elif any(x in lower for x in ["fix timing", "quantize audio", "tighten timing", "pitch correct", "tune vocal", "autotune"]):
        actions.append(ProducerAction(action="pitch_timing", track_role=track, prompt=text))
    elif any(x in lower for x in ["master", "louder", "streaming level", "reference master"]):
        actions.append(ProducerAction(action="master", prompt=text))
    elif any(x in lower for x in ["remix", "restyle", "change genre"]):
        actions.append(ProducerAction(action="remix", prompt=text))
    elif any(x in lower for x in ["add reverb", "add delay", "compress", "eq ", "distortion", "effect"]):
        actions.append(ProducerAction(action="add_effect", track_role=track, prompt=text))
    elif any(x in lower for x in ["export", "download stems", "bandlab pack", "bounce", "render wav", "render flac"]):
        actions.append(ProducerAction(action="export", track_role=track, prompt=text))
    elif any(x in lower for x in ["make a song", "create a song", "new song", "write a song"]):
        actions.append(ProducerAction(action="create_song", prompt=text))
    elif any(x in lower for x in ["add ", "generate ", "make the chorus", "make verse", "bigger chorus", "more guitar", "more drums"]):
        actions.append(ProducerAction(action="generate_layer", track_role=track, start_seconds=start, end_seconds=end, prompt=text))
    else:
        actions.append(ProducerAction(action="analyze", track_role=track, prompt=text))

    needs = any(a.action == "replace_region" and (a.start_seconds is None or a.end_seconds is None) for a in actions)
    return ProducerPlan(
        user_request=text,
        interpretation="Aura mapped the request into non-destructive Live Sound Studio operations.",
        actions=actions,
        needs_confirmation=needs,
        notes=[
            "Final audio generation must use a neural, recorded or hybrid waveform engine; symbolic guides are control data only.",
            "Voice conversion/duplication requires an approved consent record and must never bypass the studio rights ledger.",
        ],
    )


def _ollama_plan(request: str, session_summary: dict | None = None) -> ProducerPlan:
    base = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
    model = os.getenv("AURA_OLLAMA_MODEL", "qwen3:4b")
    schema = ProducerPlan.model_json_schema()
    prompt = (
        "User request:\n" + request + "\n\nCurrent studio context:\n" +
        json.dumps(session_summary or {}, ensure_ascii=False) +
        "\n\nReturn only valid JSON matching this schema:\n" + json.dumps(schema)
    )
    response = requests.post(
        f"{base}/api/chat",
        json={
            "model": model,
            "stream": False,
            "format": "json",
            "messages": [
                {"role": "system", "content": PRODUCER_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            "options": {"temperature": 0.25},
        },
        timeout=120,
    )
    response.raise_for_status()
    content = response.json()["message"]["content"]
    return ProducerPlan.model_validate(json.loads(content))


def _external_plan(request: str, session_summary: dict | None = None) -> ProducerPlan:
    url = os.environ["AURA_PRODUCER_LLM_URL"]
    headers = {"Content-Type": "application/json"}
    if os.getenv("AURA_PRODUCER_LLM_KEY"):
        headers["Authorization"] = f"Bearer {os.environ['AURA_PRODUCER_LLM_KEY']}"
    payload = {
        "instruction": PRODUCER_SYSTEM,
        "request": request,
        "session": session_summary or {},
        "schema": ProducerPlan.model_json_schema(),
    }
    response = requests.post(url, json=payload, headers=headers, timeout=60)
    response.raise_for_status()
    data = response.json()
    if isinstance(data, dict) and "output" in data and isinstance(data["output"], str):
        data = json.loads(data["output"])
    return ProducerPlan.model_validate(data)


def llm_plan(request: str, session_summary: dict | None = None) -> ProducerPlan:
    """Offline-first producer brain: local Ollama -> optional external planner -> deterministic rules."""
    use_ollama = os.getenv("AURA_PRODUCER_USE_OLLAMA", "true").lower() in {"1", "true", "yes", "on"}
    if use_ollama:
        try:
            return _ollama_plan(request, session_summary)
        except Exception:
            pass
    if os.getenv("AURA_PRODUCER_LLM_URL"):
        try:
            return _external_plan(request, session_summary)
        except Exception:
            pass
    return rule_based_plan(request)
