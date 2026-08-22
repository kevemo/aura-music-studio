from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Literal

import requests
from pydantic import BaseModel, Field


class ProducerAction(BaseModel):
    action: Literal[
        "generate_layer", "replace_region", "extend", "remix", "separate_stems", "create_harmony",
        "master", "change_mix", "add_effect", "analyze", "create_song", "unknown"
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
    "percussion": "percussion", "synth": "synth",
}


def _track_from_text(text: str) -> str | None:
    lower = text.lower()
    for word, role in TRACK_WORDS.items():
        if re.search(rf"\b{re.escape(word)}\b", lower):
            return role
    return None


def _parse_time_range(text: str) -> tuple[float | None, float | None]:
    # Supports e.g. 1:20-1:35, 80-95 seconds, from 80 to 95 seconds.
    def sec(token: str) -> float:
        if ":" in token:
            m, s = token.split(":", 1)
            return float(m) * 60 + float(s)
        return float(token)
    match = re.search(r"(\d+(?::\d+(?:\.\d+)?)?)\s*(?:-|to|–)\s*(\d+(?::\d+(?:\.\d+)?)?)\s*(?:s|sec|secs|seconds)?", text, re.I)
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
    elif any(x in lower for x in ["master", "louder", "streaming level", "reference master"]):
        actions.append(ProducerAction(action="master", prompt=text))
    elif any(x in lower for x in ["remix", "restyle", "change genre"]):
        actions.append(ProducerAction(action="remix", prompt=text))
    elif any(x in lower for x in ["add reverb", "add delay", "compress", "eq ", "distortion", "effect"]):
        actions.append(ProducerAction(action="add_effect", track_role=track, prompt=text))
    elif any(x in lower for x in ["make a song", "create a song", "new song", "write a song"]):
        actions.append(ProducerAction(action="create_song", prompt=text))
    elif any(x in lower for x in ["add ", "generate ", "make the chorus", "make verse", "bigger chorus", "more guitar", "more drums"]):
        actions.append(ProducerAction(action="generate_layer", track_role=track, start_seconds=start, end_seconds=end, prompt=text))
    else:
        actions.append(ProducerAction(action="analyze", track_role=track, prompt=text))

    needs = any(a.action == "replace_region" and (a.start_seconds is None or a.end_seconds is None) for a in actions)
    return ProducerPlan(
        user_request=text,
        interpretation="Aura mapped the request into non-destructive studio operations.",
        actions=actions,
        needs_confirmation=needs,
        notes=["Final audio generation must use a neural/recorded/hybrid audio engine; symbolic guides are control data only."],
    )


def llm_plan(request: str, session_summary: dict | None = None) -> ProducerPlan:
    """Optional external LLM planner. Falls back to the deterministic producer grammar.

    Configure AURA_PRODUCER_LLM_URL and optionally AURA_PRODUCER_LLM_KEY. The endpoint must return
    JSON matching ProducerPlan. This keeps Aura model-agnostic and avoids hard-wiring one chat provider.
    """
    url = os.getenv("AURA_PRODUCER_LLM_URL")
    if not url:
        return rule_based_plan(request)
    headers = {"Content-Type": "application/json"}
    if os.getenv("AURA_PRODUCER_LLM_KEY"):
        headers["Authorization"] = f"Bearer {os.environ['AURA_PRODUCER_LLM_KEY']}"
    payload = {
        "instruction": "Return only an Aura Music Studio ProducerPlan JSON. Never route final music to MIDI/SoundFont audio.",
        "request": request,
        "session": session_summary or {},
        "schema": ProducerPlan.model_json_schema(),
    }
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=60)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, dict) and "output" in data and isinstance(data["output"], str):
            data = json.loads(data["output"])
        return ProducerPlan.model_validate(data)
    except Exception:
        return rule_based_plan(request)
