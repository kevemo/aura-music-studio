from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import quote

import soundfile as sf
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .assets import AssetLibrary
from .daw import (
    delete_clip,
    find_clip,
    load_session,
    move_clip,
    public_session,
    save_session,
    set_automation,
    set_clip_fades,
    set_clip_gain,
    set_loop,
    set_track_mix,
    split_clip,
    trim_clip,
    waveform_peaks,
)
from .daw_midi import (
    _public_clip as _midi_public_clip,
    _safe_midi_path,
    _save_document as _save_midi_document,
    read_midi_document,
)
from .midi_humanisation import MIDI_HUMANISATION_VERSION, MidiHumanizeRequest, humanize_midi_document
from .mixer import render_session
from .plans import AUDIO_TO_MIDI_CONTROL, AUTOMATION, BASIC_TIMELINE, DEEP_REVISION_HISTORY, MULTITRACK_DAW
from .revisions import create_revision
from .session import Clip
from .tenant_storage import project_path

router = APIRouter(tags=["Aura Visual DAW"])

AUDIO_ROLES = {
    "vocals", "backing_vocals", "drums", "bass", "guitar", "piano", "keyboard", "strings",
    "synth", "percussion", "brass", "woodwinds", "fx", "other",
}


class InitTimelineRequest(BaseModel):
    asset_id: str | None = None
    role: str = "other"


class MoveRequest(BaseModel):
    start: float = Field(ge=0.0, le=86400.0)


class TrimRequest(BaseModel):
    start: float = Field(ge=0.0, le=86400.0)
    duration: float = Field(gt=0.0, le=86400.0)
    source_offset: float = Field(ge=0.0, le=86400.0)


class SplitRequest(BaseModel):
    time: float = Field(gt=0.0, le=86400.0)


class FadeRequest(BaseModel):
    fade_in: float = Field(default=0.0, ge=0.0, le=120.0)
    fade_out: float = Field(default=0.0, ge=0.0, le=120.0)


class GainRequest(BaseModel):
    gain_db: float = Field(ge=-60.0, le=24.0)


class TrackMixRequest(BaseModel):
    volume_db: float | None = Field(default=None, ge=-60.0, le=18.0)
    pan: float | None = Field(default=None, ge=-1.0, le=1.0)
    mute: bool | None = None
    solo: bool | None = None


class LoopRequest(BaseModel):
    start: float | None = Field(default=None, ge=0.0, le=86400.0)
    end: float | None = Field(default=None, ge=0.0, le=86400.0)


class AutomationRequest(BaseModel):
    parameter: str = Field(min_length=1, max_length=80)
    points: list[dict] = Field(default_factory=list, max_length=2000)


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if not member:
        raise HTTPException(401, "Sign in required")
    if not member.plan.has(BASIC_TIMELINE):
        raise HTTPException(403, "The visual timeline unlocks on the Base membership tier")
    return member


def _project(name: str) -> Path:
    try:
        return project_path(name, must_exist=True)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, "Project not found") from exc


def _session(project: Path):
    try:
        return load_session(project)
    except FileNotFoundError as exc:
        raise HTTPException(404, "This project does not have a DAW session yet") from exc


def _snapshot(project: Path, member, label: str) -> None:
    if not (project / "aura_session.json").is_file():
        return
    keep = 200 if member.plan.has(DEEP_REVISION_HISTORY) else 40
    try:
        create_revision(project, label=label, reason="daw_edit", actor="Studio member", keep=keep)
    except Exception:
        # An edit must not destroy the session merely because revision storage is unavailable.
        pass


def _audio_asset(project: Path, asset_id: str):
    record = AssetLibrary(project).get(asset_id)
    if record.kind != "audio":
        raise HTTPException(400, "The DAW timeline can import real audio assets only")
    source = (project / record.path).resolve()
    root = project.resolve()
    if root not in source.parents or not source.is_file():
        raise HTTPException(400, "Audio asset is unavailable")
    return record, source


def _clip_payload(clip: Clip) -> dict:
    return {
        "id": clip.id,
        "name": clip.name,
        "start": clip.start,
        "duration": clip.duration,
        "source_offset": clip.source_offset,
        "gain_db": clip.gain_db,
        "fade_in": clip.fade_in,
        "fade_out": clip.fade_out,
        "take_lane": clip.take_lane,
    }


@router.post("/projects/{project_name}/daw/init")
def initialize_timeline(project_name: str, body: InitTimelineRequest, request: Request):
    member = _member(request)
    project = _project(project_name)
    session_file = project / "aura_session.json"
    session = load_session(project, create=True, name=project_name)

    if body.asset_id:
        non_master = [track for track in session.tracks if track.role != "master" and track.clips]
        if non_master and not member.plan.has(MULTITRACK_DAW):
            raise HTTPException(403, "Base supports one editable audio lane; multitrack import unlocks on Pro")
        record, source = _audio_asset(project, body.asset_id)
        role = body.role if body.role in AUDIO_ROLES else "other"
        # Prevent duplicate import of the same asset into the same session.
        for track in session.tracks:
            for clip in track.clips:
                if clip.metadata.get("asset_id") == record.id:
                    return public_session(session)
        info = sf.info(source)
        track = session.add_track(Path(record.name).stem or "Audio", role)
        track.clips.append(Clip(
            name=record.name,
            kind="audio",
            source=str(Path(record.path)),
            start=0.0,
            duration=float(info.frames / info.samplerate),
            metadata={"asset_id": record.id, "real_audio": True, "imported_to_daw": True},
        ))
        session.save(session_file)
    return public_session(session)


@router.post("/projects/{project_name}/daw/import/{asset_id}")
def import_audio_track(project_name: str, asset_id: str, body: InitTimelineRequest, request: Request):
    member = _member(request)
    if not member.plan.has(MULTITRACK_DAW):
        raise HTTPException(403, "Adding multiple independent DAW tracks requires Pro")
    project = _project(project_name)
    session = load_session(project, create=True, name=project_name)
    record, source = _audio_asset(project, asset_id)
    role = body.role if body.role in AUDIO_ROLES else "other"
    _snapshot(project, member, "Before importing DAW track")
    info = sf.info(source)
    track = session.add_track(Path(record.name).stem or "Audio", role)
    track.clips.append(Clip(
        name=record.name,
        kind="audio",
        source=str(Path(record.path)),
        start=0.0,
        duration=float(info.frames / info.samplerate),
        metadata={"asset_id": record.id, "real_audio": True, "imported_to_daw": True},
    ))
    save_session(project, session)
    return public_session(session)


@router.get("/projects/{project_name}/daw/session")
def daw_session(project_name: str, request: Request):
    _member(request)
    return public_session(_session(_project(project_name)))


@router.get("/projects/{project_name}/daw/clips/{clip_id}/waveform")
def clip_waveform(project_name: str, clip_id: str, request: Request, points: int = 900):
    _member(request)
    project = _project(project_name)
    session = _session(project)
    try:
        _, clip = find_clip(session, clip_id)
        return waveform_peaks(project, clip, points=points)
    except KeyError as exc:
        raise HTTPException(404, "Clip not found") from exc
    except (ValueError, PermissionError, FileNotFoundError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/projects/{project_name}/daw/clips/{clip_id}/move")
def move(project_name: str, clip_id: str, body: MoveRequest, request: Request):
    member = _member(request)
    project = _project(project_name)
    session = _session(project)
    _snapshot(project, member, "Before moving DAW clip")
    try:
        clip = move_clip(session, clip_id, body.start)
    except KeyError as exc:
        raise HTTPException(404, "Clip not found") from exc
    save_session(project, session)
    return _clip_payload(clip)


@router.post("/projects/{project_name}/daw/clips/{clip_id}/trim")
def trim(project_name: str, clip_id: str, body: TrimRequest, request: Request):
    member = _member(request)
    project = _project(project_name)
    session = _session(project)
    _snapshot(project, member, "Before trimming DAW clip")
    try:
        clip = trim_clip(
            project,
            session,
            clip_id,
            start=body.start,
            duration=body.duration,
            source_offset=body.source_offset,
        )
    except KeyError as exc:
        raise HTTPException(404, "Clip not found") from exc
    except (ValueError, PermissionError, FileNotFoundError) as exc:
        raise HTTPException(400, str(exc)) from exc
    save_session(project, session)
    return _clip_payload(clip)


@router.post("/projects/{project_name}/daw/clips/{clip_id}/split")
def split(project_name: str, clip_id: str, body: SplitRequest, request: Request):
    member = _member(request)
    project = _project(project_name)
    session = _session(project)
    _snapshot(project, member, "Before splitting DAW clip")
    try:
        left, right = split_clip(session, clip_id, body.time)
    except KeyError as exc:
        raise HTTPException(404, "Clip not found") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    save_session(project, session)
    return {"left": _clip_payload(left), "right": _clip_payload(right)}


@router.post("/projects/{project_name}/daw/clips/{clip_id}/fades")
def fades(project_name: str, clip_id: str, body: FadeRequest, request: Request):
    member = _member(request)
    project = _project(project_name)
    session = _session(project)
    _snapshot(project, member, "Before changing DAW fades")
    try:
        clip = set_clip_fades(session, clip_id, body.fade_in, body.fade_out)
    except KeyError as exc:
        raise HTTPException(404, "Clip not found") from exc
    save_session(project, session)
    return _clip_payload(clip)


@router.post("/projects/{project_name}/daw/clips/{clip_id}/gain")
def clip_gain(project_name: str, clip_id: str, body: GainRequest, request: Request):
    member = _member(request)
    project = _project(project_name)
    session = _session(project)
    _snapshot(project, member, "Before changing DAW clip gain")
    try:
        clip = set_clip_gain(session, clip_id, body.gain_db)
    except KeyError as exc:
        raise HTTPException(404, "Clip not found") from exc
    save_session(project, session)
    return _clip_payload(clip)


@router.delete("/projects/{project_name}/daw/clips/{clip_id}")
def remove_clip(project_name: str, clip_id: str, request: Request):
    member = _member(request)
    project = _project(project_name)
    session = _session(project)
    _snapshot(project, member, "Before deleting DAW clip")
    if not delete_clip(session, clip_id):
        raise HTTPException(404, "Clip not found")
    save_session(project, session)
    return {"deleted": True, "clip_id": clip_id}


@router.post("/projects/{project_name}/daw/tracks/{track_id}/mix")
def track_mix(project_name: str, track_id: str, body: TrackMixRequest, request: Request):
    member = _member(request)
    project = _project(project_name)
    session = _session(project)
    _snapshot(project, member, "Before changing DAW mixer")
    try:
        track = set_track_mix(
            session,
            track_id,
            volume_db=body.volume_db,
            pan=body.pan,
            mute=body.mute,
            solo=body.solo,
        )
    except KeyError as exc:
        raise HTTPException(404, "Track not found") from exc
    save_session(project, session)
    return {
        "id": track.id,
        "volume_db": track.volume_db,
        "pan": track.pan,
        "mute": track.mute,
        "solo": track.solo,
    }


@router.post("/projects/{project_name}/daw/loop")
def loop_region(project_name: str, body: LoopRequest, request: Request):
    member = _member(request)
    project = _project(project_name)
    session = _session(project)
    _snapshot(project, member, "Before changing DAW loop")
    try:
        set_loop(session, body.start, body.end)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    save_session(project, session)
    return {"loop_start": session.loop_start, "loop_end": session.loop_end}


@router.post("/projects/{project_name}/daw/tracks/{track_id}/automation")
def automation(project_name: str, track_id: str, body: AutomationRequest, request: Request):
    member = _member(request)
    if not member.plan.has(AUTOMATION):
        raise HTTPException(403, "Drawn DAW automation requires Pro")
    project = _project(project_name)
    session = _session(project)
    _snapshot(project, member, "Before editing DAW automation")
    try:
        lane = set_automation(session, track_id, body.parameter, body.points)
    except (KeyError, ValueError, TypeError) as exc:
        raise HTTPException(400, "Invalid automation lane") from exc
    save_session(project, session)
    return lane.model_dump()


@router.post("/projects/{project_name}/daw/midi/{clip_id}/humanize")
def humanize_midi_clip(project_name: str, clip_id: str, body: MidiHumanizeRequest, request: Request):
    member = _member(request)
    if not member.plan.has(AUDIO_TO_MIDI_CONTROL):
        raise HTTPException(403, "MIDI performance humanisation requires the Pro MIDI tools")
    project = _project(project_name)
    session = _session(project)
    try:
        track, clip = find_clip(session, clip_id)
    except KeyError as exc:
        raise HTTPException(404, "MIDI clip not found") from exc
    if clip.kind != "midi" or not clip.source:
        raise HTTPException(400, "Clip is not editable MIDI")
    try:
        source = _safe_midi_path(project, str(clip.source))
        document = read_midi_document(source)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(409, "MIDI file is unavailable") from exc

    _snapshot(project, member, f"Before humanising MIDI · {clip.name}")
    humanized = humanize_midi_document(document, body, bpm=session.bpm)
    _save_midi_document(project, session, clip, humanized)
    clip.metadata.update({
        "midi_humanized": True,
        "midi_humanisation_version": MIDI_HUMANISATION_VERSION,
        "midi_humanisation_seed": body.seed,
        "midi_humanisation_timing_ms": body.timing_ms,
        "midi_humanisation_velocity_range": body.velocity_range,
        "midi_humanisation_duration_percent": body.duration_percent,
    })
    session.generation_history.append({
        "action": "midi_humanize",
        "clip_id": clip.id,
        "track_id": track.id,
        "engine_version": MIDI_HUMANISATION_VERSION,
        "timing_ms": body.timing_ms,
        "velocity_range": body.velocity_range,
        "duration_percent": body.duration_percent,
        "seed": body.seed,
        "preserve_first_downbeat": body.preserve_first_downbeat,
    })
    save_session(project, session)
    return {
        "clip": _midi_public_clip(project_name, track, clip, humanized),
        "document": humanized.model_dump(mode="json"),
        "humanisation": {
            "engine_version": MIDI_HUMANISATION_VERSION,
            "timing_ms": body.timing_ms,
            "velocity_range": body.velocity_range,
            "duration_percent": body.duration_percent,
            "seed": body.seed,
            "preserve_first_downbeat": body.preserve_first_downbeat,
        },
        "symbolic_guide_only": True,
        "audio_rendered": False,
        "final_audio": False,
    }


@router.post("/projects/{project_name}/daw/render")
def render_daw_mix(project_name: str, request: Request):
    _member(request)
    project = _project(project_name)
    session = _session(project)
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", session.name).strip("._") or "Aura_Session"
    output = project / "output" / "daw" / f"{safe_name}_DAW_Mix.wav"
    try:
        render_session(session, project, output, project / "work" / "daw_mix")
    except Exception as exc:
        raise HTTPException(500, f"DAW render failed: {type(exc).__name__}: {exc}") from exc
    relative_output = output.relative_to(project / "output").as_posix()
    encoded_project = quote(project_name, safe="")
    encoded_output = quote(relative_output, safe="/")
    return {
        "rendered": True,
        "path": relative_output,
        "stream_url": f"/projects/{encoded_project}/outputs/stream/{encoded_output}",
        "download_url": f"/projects/{encoded_project}/outputs/file/{encoded_output}",
        "sample_rate": session.sample_rate,
        "real_audio_only": True,
        "storage_path_exposed": False,
    }