from __future__ import annotations

import re
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from .content_safety import enforce_creation_policy
from .lyrics import LyricRequest, generate_lyrics
from .presets import get_preset, preset_dict
from .song_dna import create_song_dna


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
    preferred_engines: list[str] = Field(default_factory=lambda: [
        "acestep_api", "local_acestep", "muser", "deapi", "eleven_music", "mureka", "acestep_space", "yue"
    ])
    extra_prompt: str = ""


def build_song_project(request: CreateSongRequest, projects_root: Path) -> Path:
    enforce_creation_policy(
        request.title,
        request.concept,
        request.lyrics,
        request.extra_prompt,
        context="Music creation",
    )
    slug = re.sub(r"[^a-z0-9]+", "-", request.title.lower()).strip("-") or "new-song"
    project = projects_root / slug
    input_dir = project / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    preset = get_preset(request.genre)
    instruments = request.instruments or list(preset.instruments)

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
        enforce_creation_policy(lyrics, context="Generated lyrics")
    if lyrics:
        (input_dir / "lyrics.txt").write_text(lyrics, encoding="utf-8")

    style_bits = [request.genre, request.subgenre, request.mood]
    style_bits += [
        "release-grade finished record with ultra-real performed-sounding instruments, never General MIDI-like final audio",
        "instruments: " + ", ".join(instruments),
        f"energy {request.energy:.2f}",
        f"arrangement: {preset.arrangement}",
        f"drums: {preset.drum_style}",
        f"bass: {preset.bass_style}",
        f"vocal production: {preset.vocal_style}",
        f"mix: {preset.mix_notes}",
        f"typical genre tempo range {preset.default_bpm[0]}-{preset.default_bpm[1]} BPM",
        "commercial studio production with believable human timing, dynamics, articulation and transients",
        "natural note attacks, decays, room or amp tails and performance variation appropriate to each instrument",
        "stable instrument identity across the arrangement with intentional fills and transitions rather than random timbre changes",
        "clean frequency separation, controlled low end, clear lead focus and professional stereo depth",
        "avoid AI warble, metallic ringing, phasey doubling, smeared transients, lyric corruption and abrupt voice or instrument identity shifts",
        "finish as a professional mixed and mastered track while retaining an editable multitrack project underneath",
    ]
    if request.vocal_mode == "instrumental":
        style_bits += ["instrumental only", "no lead vocal"]
    elif request.vocal_mode == "approved_voice":
        style_bits += [
            "render lead vocal through selected consent-approved Aura Voice Profile",
            "preserve intelligible lyrics, natural breathing, emotional phrasing and stable vocal identity across sections",
        ]
    else:
        style_bits += [
            "natural expressive lead vocal",
            "clear intelligible lyrics, believable breath and phrasing, emotional dynamics and stable vocal identity across sections",
        ]
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
            "quality_retries": 3,
            "minimum_quality_score": 0.72,
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
            "mood": request.mood,
            "language": request.language,
            "vocal_mode": request.vocal_mode,
            "voice_profile_id": request.voice_profile_id,
            "seed": request.seed,
            "release_quality_standard": "release_grade_editable_master",
            "targeted_editing": {
                "lyrics": True,
                "sections": True,
                "instrument_replacement": True,
                "voice_replacement": True,
                "remix": True,
                "remaster": True,
            },
        },
        "aura_create_request": request.model_dump(),
    }
    (project / "project.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True), encoding="utf-8")

    # Every new generated song starts with an editable representation. The stereo master is
    # a render of this project, not the only surviving form of the music.
    create_song_dna(
        project,
        project_name=slug,
        title=request.title,
        genre=request.genre,
        mood=request.mood,
        language=request.language,
        bpm=request.bpm,
        key=request.key,
        meter=request.meter,
        target_duration_seconds=request.duration_seconds,
        structure=request.structure,
        lyrics=lyrics,
        instruments=instruments,
        vocal_mode=request.vocal_mode,
        voice_profile_id=request.voice_profile_id,
        master_profile=manifest["mix"],
        metadata={
            "source": "create_song_request",
            "reference_audio": request.reference_audio,
            "reference_strength": request.reference_strength,
            "seed": request.seed,
        },
    )
    return project
