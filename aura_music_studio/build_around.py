from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Literal

import soundfile as sf
from pydantic import BaseModel, Field

from .acestep_api import AceStepClient
from .assets import AssetLibrary
from .automix import apply_automix
from .instrument_catalog import InstrumentSwitch, defaults_for_genre, get_type, selection_prompt
from .lyrics import LyricRequest, generate_lyrics
from .mixer import render_session
from .project import ProjectWorkspace
from .session import Clip, StudioSession


SourceRole = Literal[
    "vocals", "guitar", "bass", "drums", "keyboard", "piano", "synth", "strings",
    "brass", "woodwinds", "percussion", "other"
]


class BuildAroundRequest(BaseModel):
    asset_id: str
    source_role: SourceRole
    genre: str = "pop"
    mood: str = "uplifting"
    instrument_switches: list[InstrumentSwitch] = Field(default_factory=list)
    include_lead_vocal: bool = True
    include_backing_vocals: bool = True
    lyrics: str = ""
    generate_lyrics_if_missing: bool = True
    vocal_language: str = "en"
    bpm: int | None = Field(default=None, ge=40, le=240)
    key: str | None = None
    meter: str = "4"
    source_preservation: float = Field(default=0.9, ge=0.0, le=1.0)
    extra_direction: str = ""
    output_mode: Literal["complete_mix", "multitrack"] = "complete_mix"
    automix_after_generation: bool = True


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "upload"


def _normalized_source_role(role: str) -> str:
    return "keyboard" if role == "piano" else role


def _duration(path: Path) -> float:
    info = sf.info(path)
    return float(info.frames / info.samplerate)


def _relative(project: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(project.resolve()))
    except ValueError:
        return str(path.resolve())


def _lyrics_for_request(request: BuildAroundRequest, source_is_vocal: bool) -> tuple[str, bool]:
    lyrics = request.lyrics.strip()
    generated = False
    if request.include_lead_vocal and not source_is_vocal and not lyrics and request.generate_lyrics_if_missing:
        lyrics = generate_lyrics(LyricRequest(
            concept=request.extra_direction or f"An original {request.genre} song built around an uploaded {request.source_role} performance",
            genre=request.genre,
            mood=request.mood,
            language=request.vocal_language,
        ))
        generated = bool(lyrics)
    return lyrics, generated


def _base_prompt(request: BuildAroundRequest, source_is_vocal: bool, instruments_prompt: str) -> str:
    source_instruction = (
        "Preserve the uploaded lead vocal's phrasing, timing and identity as the musical anchor; do not replace it."
        if source_is_vocal
        else f"Preserve the uploaded {request.source_role} performance as the musical anchor; do not regenerate or mask that part."
    )
    return ". ".join(x for x in [
        f"Create a complete professional {request.genre} production",
        f"mood {request.mood}",
        source_instruction,
        f"Source preservation priority {request.source_preservation:.2f}",
        instruments_prompt,
        "All added instruments must sound performed and recorded, with human dynamics, realistic articulation and production-quality transients",
        "Arrange around the uploaded performance rather than fighting it; leave frequency and rhythmic space for the source",
        request.extra_direction.strip(),
    ] if x)


def _prepare(project: Path, request: BuildAroundRequest):
    workspace = ProjectWorkspace(project)
    library = AssetLibrary(project)
    record = library.get(request.asset_id)
    if record.kind != "audio":
        raise ValueError("Build Around Upload requires an audio asset")
    source = project / record.path
    if not source.exists():
        raise FileNotFoundError(source)

    switches = request.instrument_switches or defaults_for_genre(request.genre)
    instruments_prompt, tracks = selection_prompt(switches)
    source_role = _normalized_source_role(request.source_role)
    tracks = [track for track in tracks if track != source_role]
    source_is_vocal = source_role == "vocals"
    if request.include_lead_vocal and not source_is_vocal and "vocals" not in tracks:
        tracks.append("vocals")
    if request.include_backing_vocals and "backing_vocals" not in tracks:
        tracks.append("backing_vocals")
    if source_is_vocal:
        tracks = [track for track in tracks if track != "vocals"]
    if not tracks:
        raise ValueError("No missing instrument/vocal roles are enabled")

    lyrics, lyrics_generated = _lyrics_for_request(request, source_is_vocal)
    analysis = record.analysis or {}
    bpm = request.bpm or int(round(float(analysis.get("estimated_bpm") or 0))) or None
    key = request.key or analysis.get("dominant_pitch_class")
    prompt = _base_prompt(request, source_is_vocal, instruments_prompt)
    return workspace, record, source, switches, tracks, source_is_vocal, lyrics, lyrics_generated, bpm, key, prompt


def _complete_mix(project: Path, request: BuildAroundRequest) -> dict:
    (
        workspace, record, source, switches, tracks, source_is_vocal, lyrics,
        lyrics_generated, bpm, key, prompt,
    ) = _prepare(project, request)
    model = os.getenv("AURA_ACESTEP_TRACK_MODEL", "acestep-v15-base")
    rendered = AceStepClient().complete(
        source,
        workspace.work_dir / "build_around",
        tracks=tracks,
        prompt=prompt,
        lyrics=lyrics,
        model=model,
        bpm=bpm,
        key=key,
        meter=request.meter,
        language=request.vocal_language,
    )
    output = workspace.output_dir / f"Aura_Built_Around_{_slug(Path(record.name).stem)}.wav"
    shutil.copy2(rendered, output)
    return {
        "source_asset_id": record.id,
        "source_name": record.name,
        "source_role": request.source_role,
        "genre": request.genre,
        "mood": request.mood,
        "bpm": bpm,
        "key": key,
        "tracks_added": tracks,
        "instrument_switches": [x.model_dump() for x in switches],
        "lyrics_generated": lyrics_generated,
        "lyrics_used": bool(lyrics),
        "source_preservation": request.source_preservation,
        "output_mode": "complete_mix",
        "engine": "ace-step-1.5-complete",
        "output": str(output),
    }


def _load_or_create_session(project: Path, name: str, bpm: int | None, key: str | None, meter: str) -> StudioSession:
    session_path = project / "aura_session.json"
    if session_path.exists():
        session = StudioSession.load(session_path)
    else:
        session = StudioSession(name=name)
        session.add_track("Master", "master")
    if bpm:
        session.bpm = float(bpm)
    if key:
        session.key = key
    session.meter = meter if "/" in meter else f"{meter}/4"
    return session


def _append_audio_track(session: StudioSession, project: Path, *, name: str, role: str, source: Path, metadata: dict) -> str:
    track = session.add_track(name, role if role in {
        "vocals", "backing_vocals", "drums", "bass", "guitar", "piano", "keyboard", "strings",
        "synth", "percussion", "brass", "woodwinds", "fx", "other"
    } else "other")
    track.clips.append(Clip(
        name=name,
        kind="audio",
        source=_relative(project, source),
        start=0.0,
        duration=_duration(source),
        take_lane=0,
        metadata={"real_audio": True, **metadata},
    ))
    return track.id


def _multitrack(project: Path, request: BuildAroundRequest) -> dict:
    (
        workspace, record, source, switches, tracks, source_is_vocal, lyrics,
        lyrics_generated, bpm, key, prompt,
    ) = _prepare(project, request)
    client = AceStepClient()
    model = os.getenv("AURA_ACESTEP_TRACK_MODEL", "acestep-v15-base")
    session = _load_or_create_session(project, f"Build Around {Path(record.name).stem}", bpm, key, request.meter)

    source_session_role = request.source_role if request.source_role != "other" else "other"
    source_track_id = _append_audio_track(
        session, project,
        name=f"Source — {Path(record.name).stem}",
        role=source_session_role,
        source=source,
        metadata={"anchor": True, "asset_id": record.id, "source_role": request.source_role},
    )

    generated: list[dict] = []
    stem_root = workspace.work_dir / "build_around_stems"
    stem_root.mkdir(parents=True, exist_ok=True)

    # Generate every enabled instrument switch independently, including multiple guitars/synths.
    for index, switch in enumerate(switches, 1):
        if not switch.enabled:
            continue
        item = get_type(switch.family, switch.type_id)
        ace_role = "keyboard" if item.family == "keyboard" else item.family
        if ace_role == _normalized_source_role(request.source_role):
            continue
        part_prompt = ". ".join(x for x in [
            f"Add only this supporting part to the existing {request.genre} performance: {item.prompt}",
            f"Prominence {switch.prominence:.2f}",
            switch.custom_direction,
            "Do not duplicate the source performance; play a complementary arrangement with realistic human articulation",
            request.extra_direction,
        ] if x)
        stem = client.add_track(
            source,
            stem_root / f"{index:02d}_{item.id}",
            track=ace_role,
            prompt=part_prompt,
            model=model,
        )
        role = item.family
        track_id = _append_audio_track(
            session, project,
            name=item.label,
            role=role,
            source=stem,
            metadata={"generated": True, "engine": "ace-step-1.5-lego", "instrument_type": item.id},
        )
        generated.append({"track_id": track_id, "role": role, "type": item.id, "path": str(stem)})

    if request.include_lead_vocal and not source_is_vocal:
        vocal_complete = client.complete(
            source,
            stem_root / "lead_vocal_complete",
            tracks=["vocals"],
            prompt=(
                f"Add a natural expressive {request.genre} lead singer around the uploaded {request.source_role}. "
                "Keep the uploaded instrument untouched and make the vocal performance fit its timing and harmony. "
                + request.extra_direction
            ),
            lyrics=lyrics,
            model=model,
            bpm=bpm,
            key=key,
            meter=request.meter,
            language=request.vocal_language,
        )
        vocal_stem = client.extract_track(vocal_complete, stem_root / "lead_vocal_isolated", track="vocals", model=model)
        track_id = _append_audio_track(
            session, project,
            name="Aura Lead Vocal",
            role="vocals",
            source=vocal_stem,
            metadata={"generated": True, "engine": "ace-step-1.5-complete+extract", "lyrics_used": bool(lyrics)},
        )
        generated.append({"track_id": track_id, "role": "vocals", "path": str(vocal_stem)})

    if request.include_backing_vocals:
        backing = client.add_track(
            source,
            stem_root / "backing_vocals",
            track="backing_vocals",
            prompt=(
                "Generate only tasteful supportive backing harmonies and wordless ooh/aah textures. "
                "Leave space for the lead and strengthen choruses/final build. " + request.extra_direction
            ),
            model=model,
        )
        track_id = _append_audio_track(
            session, project,
            name="Aura Backing Vocals",
            role="backing_vocals",
            source=backing,
            metadata={"generated": True, "engine": "ace-step-1.5-lego"},
        )
        generated.append({"track_id": track_id, "role": "backing_vocals", "path": str(backing)})

    mix_report = None
    if request.automix_after_generation:
        mix_report = apply_automix(session, project, genre=request.genre, intensity=.8)
    session_path = project / "aura_session.json"
    session.generation_history.append({
        "action": "build_around_multitrack",
        "source_asset_id": record.id,
        "source_track_id": source_track_id,
        "generated_tracks": generated,
    })
    session.save(session_path)

    mix_output = workspace.output_dir / f"Aura_BuildAround_Multitrack_{_slug(Path(record.name).stem)}.wav"
    render_session(session, project, mix_output)
    return {
        "source_asset_id": record.id,
        "source_name": record.name,
        "source_role": request.source_role,
        "genre": request.genre,
        "mood": request.mood,
        "bpm": bpm,
        "key": key,
        "output_mode": "multitrack",
        "source_track_id": source_track_id,
        "generated_tracks": generated,
        "session": str(session_path),
        "automix": mix_report,
        "lyrics_generated": lyrics_generated,
        "lyrics_used": bool(lyrics),
        "engine": "ace-step-1.5-lego/complete/extract",
        "output": str(mix_output),
    }


def build_around_upload(project: Path, request: BuildAroundRequest) -> dict:
    """Create a full production around an uploaded vocal/instrument.

    Base-friendly `complete_mix` returns one completed real-audio production. Pro `multitrack`
    generates independent editable neural stems, registers them in Aura's DAW session and renders
    an initial AutoMix while preserving every source/generated track separately.
    """
    metadata = _multitrack(project, request) if request.output_mode == "multitrack" else _complete_mix(project, request)
    ProjectWorkspace(project).save_json("build_around_last.json", metadata)
    return metadata
