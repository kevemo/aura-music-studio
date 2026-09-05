from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .daw import load_session, save_session
from .plans import AUTOMATION, DEEP_REVISION_HISTORY
from .revisions import create_revision
from .session import AutomationLane, AutomationPoint, normalize_automation_parameter
from .tenant_storage import project_path

router = APIRouter(tags=["Deep DAW Automation"])


class DeepAutomationRequest(BaseModel):
    parameter: str = Field(min_length=1, max_length=180)
    interpolation: Literal["hold", "linear", "smooth"] = "linear"
    points: list[dict] = Field(default_factory=list, max_length=4000)


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Sign in required")
    if not member.plan.has(AUTOMATION):
        raise HTTPException(403, "Deep DAW automation requires Pro")
    return member


def _project(project_name: str):
    try:
        return project_path(project_name, must_exist=True)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, "Project not found") from exc


def _snapshot(project, member, label: str) -> None:
    keep = 200 if member.plan.has(DEEP_REVISION_HISTORY) else 40
    try:
        create_revision(project, label=label, reason="deep_daw_automation", actor="Studio member", keep=keep)
    except Exception:
        # Automation remains editable even if optional history storage is temporarily unavailable.
        pass


def _validate_scope(track, parameter: str) -> str:
    canonical, _bounds = normalize_automation_parameter(parameter)
    parts = canonical.split(":")
    if len(parts) == 1:
        if canonical not in {"volume_db", "pan"}:
            raise ValueError("Unsupported track automation parameter")
        return canonical
    if len(parts) != 3:
        raise ValueError("Invalid scoped automation path")
    scope, resource_id, field = parts
    if scope == "clip":
        if field != "gain_db" or not any(clip.id == resource_id for clip in track.clips):
            raise ValueError("Clip automation target does not belong to this track")
    elif scope == "send":
        if field != "level_db" or not any(send.id == resource_id for send in track.sends):
            raise ValueError("Send automation target does not belong to this track")
    elif scope == "fx":
        if field != "mix" or not any(effect.id == resource_id for effect in track.effects):
            raise ValueError("Effect automation target does not belong to this track")
    else:
        raise ValueError("Unsupported scoped automation target")
    return canonical


def _catalog(track) -> dict:
    return {
        "track": [
            {"parameter": "volume_db", "label": "Track Volume", "unit": "dB", "min": -60.0, "max": 18.0},
            {"parameter": "pan", "label": "Track Pan", "unit": "balance", "min": -1.0, "max": 1.0},
        ],
        "clips": [
            {
                "id": clip.id,
                "name": clip.name,
                "parameter": f"clip:{clip.id}:gain_db",
                "label": f"{clip.name} · Clip Gain",
                "unit": "dB",
                "min": -60.0,
                "max": 18.0,
            }
            for clip in track.clips
            if clip.kind == "audio"
        ],
        "sends": [
            {
                "id": send.id,
                "bus_track_id": send.bus_track_id,
                "parameter": f"send:{send.id}:level_db",
                "label": "Send Level",
                "unit": "dB",
                "min": -60.0,
                "max": 12.0,
            }
            for send in track.sends
        ],
        "effects": [
            {
                "id": effect.id,
                "type": effect.type,
                "parameter": f"fx:{effect.id}:mix",
                "label": f"{effect.type.replace('_', ' ').title()} · Wet/Dry",
                "unit": "mix",
                "min": 0.0,
                "max": 1.0,
                "static_mix": effect.mix,
            }
            for effect in track.effects
        ],
        "interpolation": ["hold", "linear", "smooth"],
        "rendered_into_real_audio": True,
    }


@router.get("/projects/{project_name}/daw/tracks/{track_id}/automation-catalog")
def automation_catalog(project_name: str, track_id: str, request: Request):
    _member(request)
    project = _project(project_name)
    try:
        track = load_session(project).find_track(track_id)
    except (FileNotFoundError, KeyError) as exc:
        raise HTTPException(404, "DAW track not found") from exc
    return _catalog(track)


@router.put("/projects/{project_name}/daw/tracks/{track_id}/automation-v2")
def put_deep_automation(project_name: str, track_id: str, body: DeepAutomationRequest, request: Request):
    member = _member(request)
    project = _project(project_name)
    try:
        session = load_session(project)
        track = session.find_track(track_id)
        parameter = _validate_scope(track, body.parameter)
        lane = AutomationLane(
            parameter=parameter,
            interpolation=body.interpolation,
            points=[AutomationPoint(time=float(point["time"]), value=float(point["value"])) for point in body.points],
        )
    except (FileNotFoundError, KeyError) as exc:
        raise HTTPException(404, "DAW track not found") from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, str(exc)) from exc

    _snapshot(project, member, f"Before automating {parameter}")
    existing = next((value for value in track.automation if value.parameter == parameter), None)
    if existing is None:
        track.automation.append(lane)
    else:
        existing.points = lane.points
        existing.interpolation = lane.interpolation
        lane = existing
    session.touch()
    save_session(project, session)
    return {
        "lane": lane.model_dump(mode="json"),
        "catalog": _catalog(track),
        "rendered_into_real_audio": True,
        "source_audio_mutated": False,
    }


@router.delete("/projects/{project_name}/daw/tracks/{track_id}/automation-v2")
def delete_deep_automation(
    project_name: str,
    track_id: str,
    request: Request,
    parameter: str = Query(min_length=1, max_length=180),
):
    member = _member(request)
    project = _project(project_name)
    try:
        session = load_session(project)
        track = session.find_track(track_id)
        canonical = _validate_scope(track, parameter)
    except (FileNotFoundError, KeyError) as exc:
        raise HTTPException(404, "DAW track not found") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    lane = next((value for value in track.automation if value.parameter == canonical), None)
    if lane is None:
        raise HTTPException(404, "Automation lane not found")
    _snapshot(project, member, f"Before removing automation {canonical}")
    track.automation = [value for value in track.automation if value.parameter != canonical]
    session.touch()
    save_session(project, session)
    return {
        "deleted": True,
        "parameter": canonical,
        "catalog": _catalog(track),
        "source_audio_mutated": False,
    }


__all__ = ["DeepAutomationRequest", "_validate_scope", "router"]
