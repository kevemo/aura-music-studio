from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from .acestep_api import AceStepClient
from .assets import AssetLibrary
from .instrument_catalog import InstrumentSwitch, defaults_for_genre, selection_prompt
from .lyrics import LyricRequest, generate_lyrics
from .project import ProjectWorkspace


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


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "upload"


def _normalized_source_role(role: str) -> str:
    return "keyboard" if role == "piano" else role


def build_around_upload(project: Path, request: BuildAroundRequest) -> dict:
    """Create a full real-audio production around one uploaded isolated performance.

    The uploaded vocal/instrument is treated as source conditioning, not merely a style reference.
    Aura asks ACE-Step Complete to generate only the missing role families selected by the member.
    """
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

    lyrics = request.lyrics.strip()
    if request.include_lead_vocal and not source_is_vocal and not lyrics and request.generate_lyrics_if_missing:
        lyrics = generate_lyrics(LyricRequest(
            concept=request.extra_direction or f"An original {request.genre} song built around an uploaded {request.source_role} performance",
            genre=request.genre,
            mood=request.mood,
            language=request.vocal_language,
        ))

    analysis = record.analysis or {}
    bpm = request.bpm or int(round(float(analysis.get("estimated_bpm") or 0))) or None
    key = request.key or analysis.get("dominant_pitch_class")

    source_instruction = (
        "Preserve the uploaded lead vocal's phrasing, timing and identity as the musical anchor; do not replace it."
        if source_is_vocal
        else f"Preserve the uploaded {request.source_role} performance as the musical anchor; do not regenerate or mask that part."
    )
    prompt = ". ".join(x for x in [
        f"Create a complete professional {request.genre} production",
        f"mood {request.mood}",
        source_instruction,
        f"Source preservation priority {request.source_preservation:.2f}",
        instruments_prompt,
        "All added instruments must sound performed and recorded, with human dynamics, realistic articulation and production-quality transients",
        "Arrange around the uploaded performance rather than fighting it; leave frequency and rhythmic space for the source",
        request.extra_direction.strip(),
    ] if x)

    model = __import__("os").getenv("AURA_ACESTEP_TRACK_MODEL", "acestep-v15-base")
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
    metadata = {
        "source_asset_id": record.id,
        "source_name": record.name,
        "source_role": request.source_role,
        "genre": request.genre,
        "mood": request.mood,
        "bpm": bpm,
        "key": key,
        "tracks_added": tracks,
        "instrument_switches": [x.model_dump() for x in switches],
        "lyrics_generated": bool(lyrics and not request.lyrics.strip()),
        "lyrics_used": bool(lyrics),
        "source_preservation": request.source_preservation,
        "engine": "ace-step-1.5-complete",
        "output": str(output),
    }
    workspace.save_json("build_around_last.json", metadata)
    return metadata
