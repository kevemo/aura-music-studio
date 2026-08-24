from __future__ import annotations

import json
import re
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, model_validator

from .lyrics import LyricRequest, generate_lyrics
from .presets import get_preset, preset_dict
from .song_languages import SongLyricAdapter, pronunciation_prompt, resolve_song_language


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
    # Independent from the site/Aura conversation locale. Accepts BCP-47 tags or names such as Spanish.
    language: str = "en"
    lyrics_source_language: str | None = "auto"
    adapt_supplied_lyrics_to_song_language: bool = True
    structure: str = "Verse 1, Pre-Chorus, Chorus, Verse 2, Pre-Chorus, Chorus, Bridge, Final Chorus, Outro"
    vocal_mode: str = "ai_vocal"  # instrumental | ai_vocal | approved_voice
    vocal_gender: str | None = None
    voice_profile_id: str | None = None
    voice_similarity: float = Field(default=0.82, ge=0.0, le=1.0)
    voice_pitch_shift: int = Field(default=0, ge=-24, le=24)
    reference_audio: str | None = None
    reference_strength: float = Field(default=0.65, ge=0.0, le=1.0)
    seed: int | None = None
    preferred_engines: list[str] = Field(default_factory=lambda: [
        "acestep_api", "local_acestep", "muser", "deapi", "eleven_music", "mureka", "acestep_space", "yue"
    ])
    extra_prompt: str = ""

    @model_validator(mode="after")
    def validate_voice_mode(self):
        if self.vocal_mode not in {"instrumental", "ai_vocal", "approved_voice"}:
            raise ValueError("vocal_mode must be instrumental, ai_vocal, or approved_voice")
        if self.vocal_mode == "approved_voice" and not self.voice_profile_id:
            raise ValueError("approved_voice requires a consent-approved voice_profile_id")
        return self


def build_song_project(request: CreateSongRequest, projects_root: Path) -> Path:
    slug = re.sub(r"[^a-z0-9]+", "-", request.title.lower()).strip("-") or "new-song"
    project = projects_root / slug
    input_dir = project / "input"
    work_dir = project / "work"
    input_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    preset = get_preset(request.genre)
    instruments = request.instruments or list(preset.instruments)
    song_language = resolve_song_language(request.language)

    lyrics = request.lyrics.strip()
    language_report = {
        "song_language": song_language.to_dict(),
        "lyrics_source_language": request.lyrics_source_language,
        "adapted": False,
        "provider": "source",
    }
    if request.generate_lyrics and not lyrics:
        lyrics = generate_lyrics(LyricRequest(
            concept=request.concept or request.title,
            genre=" ".join(x for x in (request.genre, request.subgenre) if x),
            mood=request.mood,
            language=f"{song_language.english_name} ({song_language.native_name})",
            structure=request.structure,
            duration_minutes=request.duration_seconds / 60.0,
            extra=(request.extra_prompt + "\n" + pronunciation_prompt(song_language)).strip(),
        ))
        language_report.update({"provider": "lyrics_generator", "generated_in_target_language": True})
    elif lyrics and request.adapt_supplied_lyrics_to_song_language and request.vocal_mode != "instrumental":
        adapted = SongLyricAdapter().adapt(
            lyrics,
            target=song_language,
            source_language=request.lyrics_source_language,
        )
        lyrics = adapted["lyrics"]
        language_report.update({"adapted": True, "provider": adapted["provider"]})

    if lyrics:
        (input_dir / "lyrics.txt").write_text(lyrics, encoding="utf-8")
    (work_dir / "song_language.json").write_text(
        json.dumps(language_report, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    style_bits = [request.genre, request.subgenre, request.mood]
    style_bits += [
        "real performed-sounding instruments, not General MIDI",
        "instruments: " + ", ".join(instruments),
        f"energy {request.energy:.2f}",
        f"arrangement: {preset.arrangement}",
        f"drums: {preset.drum_style}",
        f"bass: {preset.bass_style}",
        f"vocal production: {preset.vocal_style}",
        f"mix: {preset.mix_notes}",
        f"typical genre tempo range {preset.default_bpm[0]}-{preset.default_bpm[1]} BPM",
        "commercial studio production with believable human dynamics and transients",
        pronunciation_prompt(song_language),
    ]
    if request.vocal_mode == "instrumental":
        style_bits += ["instrumental only", "no lead vocal"]
    elif request.vocal_mode == "approved_voice":
        style_bits += [
            "generate a clean expressive guide lead vocal in the selected song language",
            "final lead vocal must be converted through the selected consent-approved Aura Voice Profile",
            "preserve the approved singer's recognizable timbre while retaining target-language pronunciation",
        ]
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
        "target_duration_seconds": request.duration_seconds,
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
            "require_real_audio": True,
            "allow_symbolic_guide_as_final": False,
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
            "mastering_preset": preset.master_preset,
            "target_lufs": -14.0,
            "true_peak_db": -1.0,
            "vocal_space": request.vocal_mode != "instrumental",
            "backing_vocals_db": -8.0,
            "lead_guitar_db": -9.0,
            "separation_mode": "six_stems",
            "export_mp3": True,
            "export_wav": True,
            "export_flac": True,
            "export_stems": True,
            "export_translation_report": True,
        },
        "project_dna": {
            "genre_preset": preset_dict(request.genre),
            "requested_instruments": instruments,
            "structure": request.structure,
            "energy": request.energy,
            "language": song_language.english_name,
            "song_locale": song_language.locale,
            "song_language_code": song_language.language_code,
            "ace_vocal_language": song_language.ace_vocal_language,
            "ace_direct_language_support": song_language.ace_direct_support,
            "vocal_mode": request.vocal_mode,
            "voice_profile_id": request.voice_profile_id,
            "voice_similarity": request.voice_similarity,
            "voice_pitch_shift": request.voice_pitch_shift,
            "seed": request.seed,
        },
        "aura_create_request": request.model_dump(),
    }
    (project / "project.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    return project
