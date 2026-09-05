from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .chord_intelligence import ChordEvent, parse_chord_symbol, validate_progression
from .daw import load_session, save_session
from .daw_midi import MidiDocument, MidiNote, write_midi_document
from .plans import AUDIO_TO_MIDI_CONTROL, DEEP_REVISION_HISTORY
from .revisions import create_revision
from .session import Clip
from .song_dna import SongDNAStore
from .tenant_storage import project_path

router = APIRouter(tags=["Chord MIDI"])

_NOTE_TO_PC = {
    "C": 0, "B#": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3,
    "E": 4, "Fb": 4, "E#": 5, "F": 5, "F#": 6, "Gb": 6, "G": 7,
    "G#": 8, "Ab": 8, "A": 9, "A#": 10, "Bb": 10, "B": 11, "Cb": 11,
}


class ChordMidiGuideRequest(BaseModel):
    name: str = Field(default="Chord Guide", min_length=1, max_length=160)
    track_id: str | None = Field(default=None, max_length=80)
    velocity: int = Field(default=88, ge=1, le=127)
    root_octave: int = Field(default=4, ge=1, le=7)
    channel: int = Field(default=0, ge=0, le=15)


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Sign in required")
    if not member.plan.has(AUDIO_TO_MIDI_CONTROL):
        raise HTTPException(403, "Chord-to-MIDI requires the editable MIDI / piano-roll entitlement")
    return member


def _project(name: str) -> Path:
    try:
        return project_path(name, must_exist=True)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, "Project not found") from exc


def _progression(project: Path) -> tuple[list[ChordEvent], int]:
    store = SongDNAStore(project)
    if not store.path.is_file():
        raise HTTPException(409, "Initialize Song DNA before creating a chord MIDI guide")
    dna = store.load()
    raw = dna.metadata.get("chord_progression") or []
    if not isinstance(raw, list):
        raise HTTPException(409, "Song DNA chord progression is invalid")
    try:
        events = validate_progression([ChordEvent.model_validate(item) for item in raw])
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    if not events:
        raise HTTPException(409, "Add at least one chord before creating a MIDI guide")
    return events, dna.version


def _quality_intervals(quality: str) -> list[int]:
    if quality.startswith("dim"):
        intervals = [0, 3, 6]
    elif quality == "aug":
        intervals = [0, 4, 8]
    elif quality == "sus2":
        intervals = [0, 2, 7]
    elif quality == "sus4":
        intervals = [0, 5, 7]
    elif quality.startswith("m") and not quality.startswith("maj"):
        intervals = [0, 3, 7]
    else:
        intervals = [0, 4, 7]

    if quality in {"7", "9", "m7", "m9"}:
        intervals.append(10)
    elif quality in {"maj7", "maj9"}:
        intervals.append(11)
    elif quality == "dim7":
        intervals.append(9)
    elif quality in {"6", "m6"}:
        intervals.append(9)

    if quality in {"9", "m9", "maj9", "add9"}:
        intervals.append(14)
    return sorted(set(intervals))


def chord_pitches(symbol: str, *, root_octave: int = 4) -> list[int]:
    root, quality, bass = parse_chord_symbol(symbol)
    root_pitch = (root_octave + 1) * 12 + _NOTE_TO_PC[root]
    pitches = [root_pitch + interval for interval in _quality_intervals(quality)]
    if bass:
        bass_pitch = root_pitch - 12 + ((_NOTE_TO_PC[bass] - _NOTE_TO_PC[root]) % 12)
        while bass_pitch >= root_pitch:
            bass_pitch -= 12
        pitches.insert(0, bass_pitch)
    return [max(0, min(127, pitch)) for pitch in sorted(set(pitches))]


def progression_to_midi_document(
    events: list[ChordEvent], *, bpm: float, name: str, velocity: int, root_octave: int, channel: int
) -> MidiDocument:
    beat_rate = max(20.0, min(400.0, float(bpm or 120.0))) / 60.0
    notes: list[MidiNote] = []
    for event in validate_progression(events):
        start_beat = round(float(event.start_seconds) * beat_rate, 6)
        duration_beats = max(0.001, round((float(event.end_seconds) - float(event.start_seconds)) * beat_rate, 6))
        for pitch in chord_pitches(event.symbol, root_octave=root_octave):
            notes.append(
                MidiNote(
                    pitch=pitch,
                    start_beat=start_beat,
                    duration_beats=duration_beats,
                    velocity=velocity,
                    channel=channel,
                )
            )
    return MidiDocument(notes=notes, name=name)


@router.post("/projects/{project_name}/song-dna/chords/midi-guide")
def create_chord_midi_guide(project_name: str, body: ChordMidiGuideRequest, request: Request):
    member = _member(request)
    project = _project(project_name)
    try:
        session = load_session(project)
    except FileNotFoundError as exc:
        raise HTTPException(409, "Create or open the DAW session before generating a chord MIDI guide") from exc

    events, song_dna_version = _progression(project)
    keep = 200 if member.plan.has(DEEP_REVISION_HISTORY) else 40
    revision = create_revision(
        project,
        label="Before chord-to-MIDI guide",
        reason="chord_midi_generation",
        actor="Studio member",
        keep=keep,
    )
    document = progression_to_midi_document(
        events,
        bpm=session.bpm,
        name=body.name,
        velocity=body.velocity,
        root_octave=body.root_octave,
        channel=body.channel,
    )
    source = project / "input" / "midi" / "chord_guides" / f"{uuid4().hex}.mid"
    write_midi_document(source, document, bpm=session.bpm)

    if body.track_id:
        try:
            track = session.find_track(body.track_id)
        except KeyError as exc:
            source.unlink(missing_ok=True)
            raise HTTPException(404, "MIDI target track not found") from exc
        if track.role != "midi":
            source.unlink(missing_ok=True)
            raise HTTPException(400, "Chord MIDI guides can only be added to a MIDI track")
    else:
        track = session.add_track(body.name[:100], "midi")

    duration_seconds = max(event.end_seconds for event in events)
    clip = Clip(
        id=uuid4().hex,
        name=body.name[:160],
        kind="midi",
        source=source.relative_to(project).as_posix(),
        start=0.0,
        duration=float(duration_seconds),
        metadata={
            "midi_editor": True,
            "symbolic_guide_only": True,
            "chord_guide": True,
            "song_dna_version": song_dna_version,
            "chord_event_ids": [event.id for event in events],
            "revision_before_generation": revision["id"],
        },
    )
    track.clips.append(clip)
    session.touch()
    save_session(project, session)
    return {
        "track_id": track.id,
        "clip_id": clip.id,
        "midi_created": True,
        "symbolic_guide_only": True,
        "audio_rendered": False,
        "note_count": len(document.notes),
        "chord_event_count": len(events),
        "revision_id": revision["id"],
        "song_dna_version": song_dna_version,
        "piano_roll_url": f"/midi-editor?project={project_name}&clip={clip.id}",
        "detail": "Created a real editable MIDI chord guide from the Song DNA chord map. No audio render was claimed or performed.",
    }


__all__ = ["ChordMidiGuideRequest", "chord_pitches", "progression_to_midi_document", "router"]
