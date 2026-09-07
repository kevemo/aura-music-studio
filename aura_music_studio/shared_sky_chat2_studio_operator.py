from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .esp_niche import require_esp_hub_member
from .shared_sky_chat2_studio_integration import chat2_studio_transport
from .shared_sky_control_room import StudioConflict, StudioInvariantError, StudioTransportError, studio_repo
from .shared_sky_streaming_studios import shared_sky
from .shared_sky_transport_domain import (
    OperationInProgress,
    PreflightBlocked,
    TransportRateLimited,
    transport,
)

router = APIRouter(tags=["Shared Sky Studio Transport Operator"])


class BroadcastAttachRequest(BaseModel):
    broadcast_id: str = Field(min_length=1, max_length=160)
    expected_studio_version: int = Field(ge=1)


class TransportActionRequest(BaseModel):
    expected_studio_version: int = Field(ge=1)
    idempotency_key: str = Field(min_length=8, max_length=200)


class DestinationRetryRequest(TransportActionRequest):
    destination_id: str = Field(min_length=1, max_length=160)


class MarkerRequest(BaseModel):
    offset_ms: int = Field(ge=0, le=172_800_000)
    label: str = Field(default="", max_length=240)
    marker_type: Literal["highlight", "chapter", "clip", "replay"] = "highlight"


def _member(request: Request):
    return require_esp_hub_member(request)


def _session(user_id: str, session_id: str, expected_version: int | None = None) -> dict:
    session = studio_repo.get_session(user_id, session_id)
    if expected_version is not None and int(session["version"]) != expected_version:
        raise StudioConflict(
            f"Studio version conflict: expected {expected_version}, current {session['version']}"
        )
    return session


def _broadcast_id(session: dict) -> str:
    value = str(session.get("broadcast_id") or "").strip()
    if not value:
        raise StudioInvariantError("This Studio session is not attached to a broadcast")
    return value


def _raise(exc: Exception) -> None:
    if isinstance(exc, KeyError):
        raise HTTPException(404, "Shared Sky Studio, broadcast or destination not found") from exc
    if isinstance(exc, StudioConflict):
        raise HTTPException(409, str(exc)) from exc
    if isinstance(exc, PreflightBlocked):
        raise HTTPException(409, detail={"code": "preflight_blocked", **exc.result}) from exc
    if isinstance(exc, OperationInProgress):
        raise HTTPException(409, str(exc)) from exc
    if isinstance(exc, TransportRateLimited):
        raise HTTPException(429, str(exc), headers={"Retry-After": str(exc.retry_after)}) from exc
    if isinstance(exc, (StudioInvariantError, ValueError)):
        raise HTTPException(400, str(exc)) from exc
    if isinstance(exc, (StudioTransportError, RuntimeError)):
        raise HTTPException(503, str(exc)) from exc
    raise HTTPException(500, "Shared Sky Studio transport operation failed") from exc


@router.post("/shared-sky/studio/api/sessions/{session_id}/broadcast")
def attach_broadcast(session_id: str, body: BroadcastAttachRequest, request: Request):
    member, _ = _member(request)
    try:
        session = _session(member.user_id, session_id, body.expected_studio_version)
        broadcast = shared_sky.broadcast(member.user_id, body.broadcast_id)
        if broadcast.get("project_id") != session["project_id"]:
            raise StudioInvariantError("Broadcast belongs to a different Shared Sky project")
        transport_status = transport.status(member.user_id, body.broadcast_id)
        state = str((transport_status.get("session") or {}).get("state") or "draft")
        if state in {"starting", "live", "degraded", "reconnecting", "stopping"}:
            current = str(session.get("broadcast_id") or "")
            if current and current != body.broadcast_id:
                raise StudioInvariantError("Cannot replace this Studio broadcast while transport is active")
        updated = studio_repo._mutate(
            member.user_id,
            session_id,
            body.expected_studio_version,
            {
                "broadcast_id": body.broadcast_id,
                "last_transport_state": {
                    "state": state,
                    "attached": True,
                    "authoritative": True,
                },
            },
        )
        return {
            "session": updated,
            "transport": chat2_studio_transport.status(member.user_id, body.broadcast_id),
        }
    except Exception as exc:
        _raise(exc)


@router.post("/shared-sky/studio/api/sessions/{session_id}/transport/start")
def start_transport(session_id: str, body: TransportActionRequest, request: Request):
    member, _ = _member(request)
    try:
        session = _session(member.user_id, session_id, body.expected_studio_version)
        broadcast_id = _broadcast_id(session)
        prepared = chat2_studio_transport.preflight(
            member.user_id,
            broadcast_id,
            str(session["project_id"]),
            dict(session.get("profile") or {}),
        )
        preflight = dict(prepared.get("preflight") or {})
        if not bool(preflight.get("ready")):
            raise PreflightBlocked(preflight)
        result = transport.start(member.user_id, broadcast_id, body.idempotency_key)
        shared_sky.event(
            member.user_id,
            broadcast_id,
            "studio_transport_start",
            {"session_id": session_id},
        )
        return {"preflight": preflight, **result}
    except Exception as exc:
        _raise(exc)


@router.post("/shared-sky/studio/api/sessions/{session_id}/transport/stop")
def stop_transport(session_id: str, body: TransportActionRequest, request: Request):
    member, _ = _member(request)
    try:
        session = _session(member.user_id, session_id, body.expected_studio_version)
        broadcast_id = _broadcast_id(session)
        result = transport.stop(member.user_id, broadcast_id, body.idempotency_key)
        shared_sky.event(
            member.user_id,
            broadcast_id,
            "studio_transport_stop",
            {"session_id": session_id},
        )
        return result
    except Exception as exc:
        _raise(exc)


@router.post("/shared-sky/studio/api/sessions/{session_id}/transport/retry-destination")
def retry_transport_destination(
    session_id: str, body: DestinationRetryRequest, request: Request
):
    member, _ = _member(request)
    try:
        session = _session(member.user_id, session_id, body.expected_studio_version)
        broadcast_id = _broadcast_id(session)
        return transport.retry_destination(
            member.user_id,
            broadcast_id,
            body.destination_id,
            body.idempotency_key,
        )
    except Exception as exc:
        _raise(exc)


@router.post("/shared-sky/studio/api/sessions/{session_id}/markers")
def create_marker(session_id: str, body: MarkerRequest, request: Request):
    member, _ = _member(request)
    try:
        session = _session(member.user_id, session_id)
        broadcast_id = _broadcast_id(session)
        marker = transport.add_highlight_marker(
            member.user_id,
            broadcast_id,
            offset_ms=body.offset_ms,
            label=body.label,
            marker_type=body.marker_type,
        )
        shared_sky.event(
            member.user_id,
            broadcast_id,
            "studio_marker_created",
            {"session_id": session_id, "marker_id": marker.get("id")},
        )
        return {"marker": marker, "authoritative": True}
    except Exception as exc:
        _raise(exc)


@router.get("/shared-sky/studio/api/sessions/{session_id}/markers")
def list_markers(session_id: str, request: Request):
    member, _ = _member(request)
    try:
        session = _session(member.user_id, session_id)
        broadcast_id = _broadcast_id(session)
        return {
            "markers": transport.highlight_markers(member.user_id, broadcast_id),
            "authoritative": True,
        }
    except Exception as exc:
        _raise(exc)


def install_chat2_studio_operator(app) -> None:
    existing = {getattr(route, "path", "") for route in app.router.routes}
    marker = "/shared-sky/studio/api/sessions/{session_id}/transport/start"
    if marker not in existing:
        app.include_router(router)


__all__ = [
    "BroadcastAttachRequest",
    "DestinationRetryRequest",
    "MarkerRequest",
    "TransportActionRequest",
    "install_chat2_studio_operator",
    "router",
]
