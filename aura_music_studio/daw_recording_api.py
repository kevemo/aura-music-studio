from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Literal

import soundfile as sf
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

from .daw import load_session, public_session, save_session
from .plans import BASIC_TIMELINE, DEEP_REVISION_HISTORY, MULTITRACK_DAW, TAKE_LANES
from .recording_core import ingest_recording, normalize_capture, safe_recording_name, validate_recording_role
from .revisions import create_revision
from .session import Clip, StudioSession, Track
from .tenant_storage import project_path

router = APIRouter(tags=["Aura DAW Recording"])

RecordingMode = Literal["append", "new_take", "punch"]


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if not member:
        raise HTTPException(401, "Sign in required")
    if not member.plan.has(BASIC_TIMELINE):
        raise HTTPException(403, "Recording directly into the DAW unlocks on Base")
    return member


def _project(name: str) -> Path:
    try:
        return project_path(name, must_exist=True)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, "Project not found") from exc


def _non_master(session: StudioSession) -> list[Track]:
    return [track for track in session.tracks if track.role != "master"]


def _track(session: StudioSession, track_id: str | None) -> Track | None:
    if not track_id:
        return None
    try:
        track = session.find_track(track_id)
    except KeyError as exc:
        raise HTTPException(404, "Target DAW track not found") from exc
    if track.role == "master":
        raise HTTPException(400, "Audio cannot be recorded directly onto the master bus")
    return track


def _snapshot(project: Path, member, label: str) -> None:
    if not (project / "aura_session.json").is_file():
        return
    keep = 200 if member.plan.has(DEEP_REVISION_HISTORY) else 40
    try:
        create_revision(project, label=label, reason="daw_recording", actor="Studio member", keep=keep)
    except Exception:
        pass


def _next_take_lane(track: Track) -> int:
    return max((clip.take_lane for clip in track.clips if clip.kind == "audio"), default=-1) + 1


def _base_track_guard(member, session: StudioSession, target: Track | None) -> Track | None:
    tracks = _non_master(session)
    if member.plan.has(MULTITRACK_DAW):
        return target
    if len(tracks) > 1:
        raise HTTPException(403, "This project contains a Pro multitrack session. Upgrade to Pro to edit or record into it.")
    if target is not None and tracks and target.id != tracks[0].id:
        raise HTTPException(403, "Base can record only into its single editable timeline lane")
    return target or (tracks[0] if tracks else None)


@router.post("/projects/{project_name}/daw/recording")
async def record_into_daw(
    project_name: str,
    request: Request,
    audio: UploadFile = File(...),
    role: str = Form("vocals"),
    title: str = Form("DAW Take"),
    timeline_start: float = Form(0.0),
    track_id: str = Form(""),
    mode: str = Form("append"),
    punch_end: float | None = Form(default=None),
    notes: str = Form(""),
    rights_confirmation: str = Form("I confirm this is my recording or I have permission to use it."),
):
    member = _member(request)
    project = _project(project_name)
    try:
        role = validate_recording_role(role)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if mode not in {"append", "new_take", "punch"}:
        raise HTTPException(400, "Invalid DAW recording mode")
    start = max(0.0, min(float(timeline_start), 86400.0))
    if mode in {"new_take", "punch"} and not member.plan.has(TAKE_LANES):
        raise HTTPException(403, "Punch recording and alternate take lanes require Pro")
    if mode == "punch":
        if punch_end is None or float(punch_end) <= start:
            raise HTTPException(400, "Punch recording requires an end time after the punch start")
        if float(punch_end) - start > 3600:
            raise HTTPException(400, "Punch region is too long")

    session = load_session(project, create=True, name=project_name)
    target = _base_track_guard(member, session, _track(session, track_id.strip() or None))
    if mode in {"new_take", "punch"} and target is None:
        raise HTTPException(400, "Choose the track that should receive this take")

    suffix = Path(audio.filename or "recording.webm").suffix.lower() or ".webm"
    with tempfile.TemporaryDirectory(prefix="lss-daw-recording-") as tmp_dir:
        tmp = Path(tmp_dir)
        capture = tmp / f"capture{suffix}"
        total = 0
        with capture.open("wb") as handle:
            while chunk := await audio.read(1024 * 1024):
                total += len(chunk)
                if total > 250 * 1024 * 1024:
                    raise HTTPException(413, "Recording is too large")
                handle.write(chunk)
        normalized = tmp / (safe_recording_name(title) + ".wav")
        try:
            normalize_capture(capture, normalized)
            record = ingest_recording(
                project,
                normalized,
                role=role,
                notes=notes,
                rights_confirmation=rights_confirmation,
                extra_tags=["daw_recording", mode],
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from exc

    source = project / record.path
    info = sf.info(source)
    captured_duration = float(info.frames / info.samplerate)
    duration = captured_duration
    if mode == "punch" and punch_end is not None:
        duration = min(duration, float(punch_end) - start)
    if duration <= 0.01:
        raise HTTPException(400, "Captured take is too short")

    _snapshot(project, member, f"Before DAW {mode} recording")
    if target is None:
        target = session.add_track(safe_recording_name(title), role)
    lane = _next_take_lane(target) if mode in {"new_take", "punch"} else 0
    clip = Clip(
        name=safe_recording_name(title) if lane == 0 else f"{safe_recording_name(title)} — Take {lane + 1}",
        kind="audio",
        source=record.path,
        start=start,
        duration=duration,
        source_offset=0.0,
        take_lane=lane,
        metadata={
            "asset_id": record.id,
            "real_audio": True,
            "dry_recording": True,
            "recorded_in_daw": True,
            "recording_mode": mode,
            "punch_start": start if mode == "punch" else None,
            "punch_end": float(punch_end) if mode == "punch" and punch_end is not None else None,
        },
    )
    target.clips.append(clip)
    save_session(project, session)
    return {
        "saved": True,
        "asset_id": record.id,
        "track_id": target.id,
        "clip_id": clip.id,
        "take_lane": lane,
        "timeline_start": clip.start,
        "duration": clip.duration,
        "mode": mode,
        "normalized_format": "WAV PCM 24-bit / 48 kHz",
        "dry_recording": True,
        "source_path_exposed": False,
        "session": public_session(session),
    }
