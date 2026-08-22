from __future__ import annotations

import re
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from .lyrics import LyricRequest, generate_lyrics


class CreateSongRequest(BaseModel):
    title: str
    concept: str = ""
    lyrics: str = ""
    generate_lyrics: bool = False
    genre: str = "pop"
    subgenre: str = ""
    mood: str = "uplifting"
    instruments: list[str] = Field(default_factory=list)
    energy: float = Field(default=0.7, ge=0.0, le=1.0)
    bpm: float | None = None
    key: str | None = None
    meter: str = "4/4"
    duration_seconds: int = Field(default=210, ge=10, le=600)
    language: str = "English"
    structure: str = "Verse 1, Pre-Chorus, Chorus, Verse 2, Pre-Chorus, Chorus, Bridge, Final Chorus, Outro"
    vocal_mode: str = "ai_vocal"  # instrumental | ai_vocal | approved_voice
    vocal_gender: str | None = None
    voice_profile_id: str | None = None
    reference_audio: str | None = None
    reference_strength: float = Field(default=0.65, ge=0.0, le=1.0)
    seed: int | None = None
    preferred_engines: list[str] = Field(default_factory=lambda: ["local_acestep", "muser", "acestep_space", "eleven_music", "mureka", "yue", "deapi"])
    extra_prompt: str = ""


def build_song_project(request: CreateSongRequest, projects_root: Path) -> Path:
    slug = re.sub(r"[^a-z0-9]+", "-", request.title.lower()).strip("-") or "new-song"
    project = projects_root / slug
    input_dir = project / "input"
    input_dir.mkdir(parents=True, exist_ok=True)

    lyrics = request.lyrics.strip()
    if request.generate_lyrics and not lyrics:
        lyrics = generate_lyrics(LyricRequest(
            concept=request.concept or request.title,
            genre=" ".join(x for x in (request.genre, request.subgenre) if x),
            mood=request.mood,
            language=request.language,
            structure=request.structure,
            duration_minutes=request.duration_seconds / 60.0,
            extra=request.extra_prompt,
        ))
    if lyrics:
        (input_dir / "lyrics.txt").write_text(lyrics, encoding="utf-8")

    style_bits = [request.genre, request.subgenre, request.mood]
    if request.instruments:
        style_bits.append("instruments: " + ", ".join(request.instruments))
    style_bits.append(f"energy {request.energy:.2f}")
    style_bits.append("commercial studio production")
    if request.vocal_mode == "instrumental":
        style_bits += ["instrumental only", "no lead vocal"]
    elif request.vocal_mode == "approved_voice":
        style_bits.append("render lead vocal through selected approved Aura Voice Profile")
    else:
        style_bits.append("natural expressive lead vocal")
    if request.extra_prompt:
        style_bits.append(request.extra_prompt)

    manifest = {
        "project_name": slug,
        "title": request.title,
        "mode": "original",
        "rights_confirmed": True,
        "tempo_bpm": request.bpm,
        "meter": request.meter,
        "key": request.key,
        "reference_audio": request.reference_audio,
        "lyrics_file": "input/lyrics.txt" if lyrics else None,
        "prompt": ". ".join(x for x in style_bits if x),
        "renderer": {
            "preferred": request.preferred_engines,
            "model": "acestep-v15-xl-turbo",
            "cover_strength": request.reference_strength,
            "duration_limit_seconds": request.duration_seconds,
            "max_attempts_per_host": 3,
            "retry_seconds": 45,
            "quality_retries": 2,
            "minimum_quality_score": 0.55,
        },
        "production": {
            "realistic_drums": True,
            "fingered_bass": True,
            "acoustic_guitar": True,
            "electric_rhythm_guitars": True,
            "piano": True,
            "synths": True,
            "strings": True,
            "percussion": True,
            "original_single_note_countermelody": False,
            "wordless_backing_harmonies": request.vocal_mode != "instrumental",
            "leave_center_for_lead_vocal": request.vocal_mode != "instrumental",
        },
        "mix": {
            "target_lufs": -14.0,
            "true_peak_db": -1.0,
            "vocal_space": request.vocal_mode != "instrumental",
            "backing_vocals_db": -8.0,
            "lead_guitar_db": -9.0,
            "export_mp3": True,
            "export_wav": True,
            "export_stems": True,
        },
        "aura_create_request": request.model_dump(),
    }
    (project / "project.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return project
