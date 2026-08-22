from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass

import requests


@dataclass
class LyricRequest:
    concept: str
    genre: str = "pop"
    mood: str = "uplifting"
    language: str = "English"
    structure: str = "Verse 1, Pre-Chorus, Chorus, Verse 2, Pre-Chorus, Chorus, Bridge, Final Chorus, Outro"
    perspective: str = "first person"
    rhyme_style: str = "natural"
    vocal_style: str = "singable"
    duration_minutes: float = 3.5
    explicit: bool = False
    extra: str = ""


SYSTEM = """You are Aura's songwriting engine. Write ORIGINAL lyrics only.
Never reproduce or continue lyrics from existing copyrighted songs. Never imitate a named living artist's exact lyrical style.
You may use broad genre conventions. Prioritize singability, clear section labels, coherent story, memorable but original hooks,
and natural syllable density. Return only the requested lyrics unless asked for analysis."""


def _prompt(req: LyricRequest) -> str:
    return f"""Write a complete original song.
Concept: {req.concept}
Genre: {req.genre}
Mood: {req.mood}
Language: {req.language}
Perspective: {req.perspective}
Target structure: {req.structure}
Rhyme approach: {req.rhyme_style}
Vocal writing: {req.vocal_style}
Approximate duration: {req.duration_minutes:.1f} minutes
Explicit language allowed: {req.explicit}
Additional direction: {req.extra or 'none'}

Use section tags like [Verse 1], [Pre-Chorus], [Chorus], [Bridge], [Outro].
Make the chorus memorable without relying on stock clichés. Keep line lengths practical for singing."""


def generate_lyrics(req: LyricRequest) -> str:
    provider = os.getenv("AURA_LLM_PROVIDER", "auto").lower()
    if provider in {"auto", "openai_compatible"} and os.getenv("AURA_LLM_BASE_URL"):
        try:
            return _openai_compatible(req)
        except Exception:
            if provider != "auto":
                raise
    if provider in {"auto", "ollama"}:
        try:
            return _ollama(req)
        except Exception:
            if provider != "auto":
                raise
    return scaffold_lyrics(req)


def _openai_compatible(req: LyricRequest) -> str:
    base = os.environ["AURA_LLM_BASE_URL"].rstrip("/")
    key = os.getenv("AURA_LLM_API_KEY", "")
    model = os.getenv("AURA_LLM_MODEL", "gpt-4.1-mini")
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": _prompt(req)},
        ],
        "temperature": 0.9,
    }
    r = requests.post(f"{base}/chat/completions", headers=headers, json=payload, timeout=120)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()


def _ollama(req: LyricRequest) -> str:
    base = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
    model = os.getenv("AURA_OLLAMA_MODEL", "qwen3:8b")
    payload = {
        "model": model,
        "stream": False,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": _prompt(req)},
        ],
        "options": {"temperature": 0.9},
    }
    r = requests.post(f"{base}/api/chat", json=payload, timeout=180)
    r.raise_for_status()
    return r.json()["message"]["content"].strip()


def scaffold_lyrics(req: LyricRequest) -> str:
    """Offline fallback: a useful section scaffold without pretending to be an LLM composition."""
    sections = [x.strip() for x in req.structure.split(",") if x.strip()]
    lines = [f"# Lyric writing scaffold — {req.concept}", ""]
    for section in sections:
        lines += [f"[{section}]", f"<Write original {req.mood} {req.genre} lines about: {req.concept}>", ""]
    return "\n".join(lines).strip()


def parse_sections(lyrics: str) -> list[dict]:
    current = {"name": "Unlabeled", "lines": []}
    sections = []
    for raw in lyrics.splitlines():
        line = raw.rstrip()
        match = re.match(r"^\s*\[([^\]]+)\]\s*$", line)
        if match:
            if current["lines"]:
                sections.append(current)
            current = {"name": match.group(1).strip(), "lines": []}
        elif line.strip():
            current["lines"].append(line)
    if current["lines"]:
        sections.append(current)
    return sections


def lyric_metrics(lyrics: str) -> dict:
    sections = parse_sections(lyrics)
    words = re.findall(r"\b[\w'-]+\b", lyrics)
    sung_lines = [line for s in sections for line in s["lines"]]
    return {
        "sections": sections,
        "word_count": len(words),
        "line_count": len(sung_lines),
        "average_words_per_line": (len(words) / len(sung_lines)) if sung_lines else 0.0,
    }
