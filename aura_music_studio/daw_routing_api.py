from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .daw import (
    bounce_track,
    create_bus,
    crossfade_clips,
    delete_bus,
    freeze_track,
    load_session,
    public_session,
    remove_send,
    save_session,
    set_send,
    thaw_track,
)
from .plans import DEEP_REVISION_HISTORY, MULTITRACK_DAW
from .revisions import create_revision
from .tenant_storage import project_path

router = APIRouter(tags=["Aura DAW Routing"])


class CrossfadeRequest(BaseModel):
    left_clip_id: str = Field(min_length=1, max_length=128)
    right_clip_id: str = Field(min_length=1, max_length=128)
    duration: float = Field(gt=0.001, le=120.0)


class CreateBusRequest(BaseModel):
    name: str = Field(default="Aura Bus", min_length=1, max_length=100)
    preset: str = Field(default="clean", pattern="^(clean|reverb|delay)$")


class SendRequest(BaseModel):
    bus_track_id: str = Field(min_length=1, max_length=128)
    level_db: float = Field(default=-18.0, ge=-60.0, le=12.0)
    enabled: bool = True


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if not member:
        raise HTTPException(401, "Sign in required")
    if not member.plan.has(MULTITRACK_DAW):
        raise HTTPException(403, "DAW routing, crossfades and track freeze require Pro")
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
        create_revision(project, label=label, reason="daw_routing", actor="Studio member", keep=keep)
    except Exception:
        pass


def _track_result(track) -> dict:
    return {
        "id": track.id,
        "name": track.name,
        "role": track.role,
        "frozen": bool(track.metadata.get("frozen", False)),
        "effect_count": len(track.effects),
        "send_count": len(track.sends),
        "source_paths_exposed": False,
    }


@router.post("/projects/{project_name}/daw/crossfade")
def crossfade(project_name: str, body: CrossfadeRequest, request: Request):
    member = _member(request)
    project = _project(project_name)
    session = _session(project)
    _snapshot(project, member, "Before DAW crossfade")
    try:
        left, right = crossfade_clips(session, body.left_clip_id, body.right_clip_id, body.duration)
    except KeyError as exc:
        raise HTTPException(404, "Crossfade clip not found") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    save_session(project, session)
    return {
        "crossfaded": True,
        "duration": body.duration,
        "left": {"id": left.id, "start": left.start, "duration": left.duration, "fade_out": left.fade_out},
        "right": {"id": right.id, "start": right.start, "duration": right.duration, "fade_in": right.fade_in},
        "source_audio_modified": False,
    }


@router.post("/projects/{project_name}/daw/buses")
def add_bus(project_name: str, body: CreateBusRequest, request: Request):
    member = _member(request)
    project = _project(project_name)
    session = _session(project)
    _snapshot(project, member, "Before adding DAW bus")
    try:
        bus = create_bus(session, body.name, body.preset)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    save_session(project, session)
    return {"created": True, "bus": _track_result(bus), "preset": body.preset}


@router.delete("/projects/{project_name}/daw/buses/{bus_track_id}")
def remove_bus(project_name: str, bus_track_id: str, request: Request):
    member = _member(request)
    project = _project(project_name)
    session = _session(project)
    _snapshot(project, member, "Before removing DAW bus")
    try:
        removed = delete_bus(session, bus_track_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not removed:
        raise HTTPException(404, "Auxiliary bus not found")
    save_session(project, session)
    return {"deleted": True, "bus_track_id": bus_track_id, "dangling_sends_removed": True}


@router.post("/projects/{project_name}/daw/tracks/{track_id}/sends")
def upsert_send(project_name: str, track_id: str, body: SendRequest, request: Request):
    member = _member(request)
    project = _project(project_name)
    session = _session(project)
    _snapshot(project, member, "Before changing DAW send")
    try:
        send = set_send(
            session,
            track_id,
            body.bus_track_id,
            level_db=body.level_db,
            enabled=body.enabled,
        )
    except KeyError as exc:
        raise HTTPException(404, "Source track or bus not found") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    save_session(project, session)
    return send.model_dump()


@router.delete("/projects/{project_name}/daw/tracks/{track_id}/sends/{send_id}")
def delete_track_send(project_name: str, track_id: str, send_id: str, request: Request):
    member = _member(request)
    project = _project(project_name)
    session = _session(project)
    _snapshot(project, member, "Before removing DAW send")
    try:
        removed = remove_send(session, track_id, send_id)
    except KeyError as exc:
        raise HTTPException(404, "Source track not found") from exc
    if not removed:
        raise HTTPException(404, "Send not found")
    save_session(project, session)
    return {"deleted": True, "send_id": send_id}


@router.post("/projects/{project_name}/daw/tracks/{track_id}/freeze")
def freeze(project_name: str, track_id: str, request: Request):
    member = _member(request)
    project = _project(project_name)
    session = _session(project)
    _snapshot(project, member, "Before freezing DAW track")
    try:
        track = freeze_track(project, session, track_id)
    except KeyError as exc:
        raise HTTPException(404, "Track not found") from exc
    except (ValueError, RuntimeError, FileNotFoundError) as exc:
        raise HTTPException(400, str(exc)) from exc
    save_session(project, session)
    return {"frozen": True, "track": _track_result(track), "editable_state_preserved": True}


@router.post("/projects/{project_name}/daw/tracks/{track_id}/thaw")
def thaw(project_name: str, track_id: str, request: Request):
    member = _member(request)
    project = _project(project_name)
    session = _session(project)
    _snapshot(project, member, "Before thawing DAW track")
    try:
        track = thaw_track(session, track_id)
    except KeyError as exc:
        raise HTTPException(404, "Track not found") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    save_session(project, session)
    return {"frozen": False, "track": _track_result(track), "editable_state_restored": True}


@router.post("/projects/{project_name}/daw/tracks/{track_id}/bounce")
def bounce(project_name: str, track_id: str, request: Request):
    _member(request)
    project = _project(project_name)
    session = _session(project)
    try:
        output = bounce_track(project, session, track_id)
    except KeyError as exc:
        raise HTTPException(404, "Track not found") from exc
    except (ValueError, RuntimeError, FileNotFoundError) as exc:
        raise HTTPException(400, str(exc)) from exc
    relative = output.relative_to(project / "output").as_posix()
    encoded_project = quote(project_name, safe="")
    encoded_output = quote(relative, safe="/")
    return {
        "bounced": True,
        "path": relative,
        "stream_url": f"/projects/{encoded_project}/outputs/stream/{encoded_output}",
        "download_url": f"/projects/{encoded_project}/outputs/file/{encoded_output}",
        "session_mutated": False,
        "real_audio_only": True,
        "storage_path_exposed": False,
    }


@router.get("/projects/{project_name}/daw/routing")
def routing_state(project_name: str, request: Request):
    _member(request)
    return public_session(_session(_project(project_name)))
