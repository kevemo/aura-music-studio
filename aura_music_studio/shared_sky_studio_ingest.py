from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .esp_niche import require_esp_hub_member
from .shared_sky_chat2_studio_integration import chat2_studio_transport
from .shared_sky_control_room import StudioInvariantError, StudioTransportError, studio_repo
from .shared_sky_media_plane import IngestSessionCreate, media_plane
from .shared_sky_streaming_studios import shared_sky
from .shared_sky_transport_domain import transport

router = APIRouter(tags=["Shared Sky Studio Signed Ingest"])

_ACTIVE = {"starting", "live", "degraded", "reconnecting", "stopping"}


class StudioIngestIssueRequest(BaseModel):
    expected_studio_version: int = Field(ge=1)
    ttl_seconds: int = Field(default=900, ge=60, le=7200)


class StudioIngestRevokeRequest(BaseModel):
    expected_studio_version: int = Field(ge=1)


def _member(request: Request):
    return require_esp_hub_member(request)


def _raise(exc: Exception) -> None:
    if isinstance(exc, KeyError):
        raise HTTPException(404, "Shared Sky Studio/ingest resource not found") from exc
    if isinstance(exc, StudioTransportError):
        raise HTTPException(409, str(exc)) from exc
    if isinstance(exc, StudioInvariantError):
        raise HTTPException(409, str(exc)) from exc
    if isinstance(exc, (RuntimeError, ValueError)):
        raise HTTPException(503, str(exc)) from exc
    raise HTTPException(500, "Shared Sky Studio ingest operation failed") from exc


def _studio_session(user_id: str, session_id: str, expected_version: int | None = None) -> dict[str, Any]:
    session = studio_repo.get_session(user_id, session_id)
    if expected_version is not None and int(session["version"]) != int(expected_version):
        raise StudioInvariantError("Studio state changed; refresh before changing ingest credentials")
    broadcast_id = session.get("broadcast_id")
    if not broadcast_id:
        raise StudioInvariantError("Attach a Shared Sky broadcast before managing contribution ingest")
    broadcast = shared_sky.broadcast(user_id, str(broadcast_id))
    if str(broadcast.get("project_id")) != str(session["project_id"]):
        raise StudioInvariantError("Attached broadcast does not belong to this Studio project")
    return session


def _transport_state(user_id: str, broadcast_id: str) -> dict[str, Any]:
    payload = transport.status(user_id, broadcast_id)
    return dict(payload.get("session") or {})


def _require_inactive(user_id: str, broadcast_id: str) -> dict[str, Any]:
    session = _transport_state(user_id, broadcast_id)
    state = str(session.get("state") or "draft")
    if state in _ACTIVE:
        raise StudioTransportError(
            "Signed ingest credentials cannot be replaced or revoked while transport is active"
        )
    return session


def _metadata(user_id: str, broadcast_id: str, ingest_session_id: str | None) -> dict[str, Any] | None:
    if not ingest_session_id:
        return None
    with transport.connect() as con:
        row = con.execute(
            "SELECT id,broadcast_id,node_id,state,issued_at,expires_at,revoked_at,last_seen_at "
            "FROM shared_sky_ingest_sessions WHERE id=? AND user_id=? AND broadcast_id=?",
            (ingest_session_id, user_id, broadcast_id),
        ).fetchone()
    return dict(row) if row else None


def ingest_status(user_id: str, studio_session: dict[str, Any]) -> dict[str, Any]:
    broadcast_id = str(studio_session["broadcast_id"])
    transport_session = _transport_state(user_id, broadcast_id)
    ingest_id = transport_session.get("ingest_session_id")
    plane = media_plane.status()
    return {
        "authoritative": True,
        "attached": bool(ingest_id),
        "session": _metadata(user_id, broadcast_id, str(ingest_id) if ingest_id else None),
        "media_plane": {
            "signing_configured": bool(plane.get("signing_configured")),
            "ingest_base_configured": bool(plane.get("ingest_base_configured")),
            "healthy_nodes": int(plane.get("healthy_nodes") or 0),
            "media_termination_deployed": bool(plane.get("media_termination_deployed")),
        },
        "secret_recoverable": False,
        "truth_boundary": "An issued credential proves control-plane issuance only; media termination must be deployed separately.",
    }


def _configure_ingest(
    user_id: str,
    studio_session: dict[str, Any],
    ingest_session_id: str | None,
) -> dict[str, Any]:
    broadcast_id = str(studio_session["broadcast_id"])
    bound = chat2_studio_transport.bind(
        user_id,
        broadcast_id,
        str(studio_session["project_id"]),
        dict(studio_session.get("profile") or {}),
    )
    current = dict((bound.get("transport") or {}).get("session") or {})
    source = dict(bound.get("source") or {})
    if not source.get("id"):
        raise StudioTransportError("Chat 2 did not return a Studio programme source for ingest binding")
    return transport.configure(
        user_id,
        broadcast_id,
        source_id=str(source["id"]),
        internal_playback=bool(current.get("internal_playback", True)),
        rendition_profile=dict(current.get("rendition_profile") or {}),
        recording_enabled=bool(current.get("recording_enabled", False)),
        ingest_session_id=ingest_session_id,
    )


def issue_ingest(user_id: str, studio_session: dict[str, Any], ttl_seconds: int) -> dict[str, Any]:
    broadcast_id = str(studio_session["broadcast_id"])
    previous = _require_inactive(user_id, broadcast_id)
    previous_id = previous.get("ingest_session_id")
    issued = media_plane.create_session(
        user_id,
        IngestSessionCreate(broadcast_id=broadcast_id, ttl_seconds=ttl_seconds),
    )
    try:
        _configure_ingest(user_id, studio_session, str(issued["id"]))
    except Exception:
        try:
            media_plane.revoke(user_id, str(issued["id"]))
        finally:
            raise
    if previous_id and str(previous_id) != str(issued["id"]):
        try:
            media_plane.revoke(user_id, str(previous_id))
        except KeyError:
            pass
    shared_sky.event(
        user_id,
        broadcast_id,
        "studio_ingest_credential_issued",
        {"session_id": str(issued["id"]), "node_id": issued.get("node_id")},
    )
    return {
        "session": issued,
        "display_once": True,
        "secret_persisted_by_studio": False,
        "media_termination_deployed": bool(media_plane.status().get("media_termination_deployed")),
    }


def revoke_ingest(user_id: str, studio_session: dict[str, Any]) -> dict[str, Any]:
    broadcast_id = str(studio_session["broadcast_id"])
    current = _require_inactive(user_id, broadcast_id)
    ingest_id = current.get("ingest_session_id")
    if not ingest_id:
        return {"revoked": False, "already_detached": True}
    revoked = media_plane.revoke(user_id, str(ingest_id))
    try:
        _configure_ingest(user_id, studio_session, None)
    except Exception as exc:
        raise StudioTransportError(
            "Credential was revoked but Chat 2 could not detach the revoked ingest session; refresh transport state before continuing"
        ) from exc
    shared_sky.event(
        user_id,
        broadcast_id,
        "studio_ingest_credential_revoked",
        {"session_id": str(ingest_id)},
    )
    return {"revoked": True, "session": revoked}


@router.get("/shared-sky/studio/api/sessions/{session_id}/ingest")
def studio_ingest_status(session_id: str, request: Request):
    member, _ = _member(request)
    try:
        session = _studio_session(member.user_id, session_id)
        return ingest_status(member.user_id, session)
    except Exception as exc:
        _raise(exc)


@router.post("/shared-sky/studio/api/sessions/{session_id}/ingest")
def issue_studio_ingest(session_id: str, body: StudioIngestIssueRequest, request: Request):
    member, _ = _member(request)
    try:
        session = _studio_session(member.user_id, session_id, body.expected_studio_version)
        payload = issue_ingest(member.user_id, session, body.ttl_seconds)
        return JSONResponse(
            content=payload,
            headers={
                "Cache-Control": "no-store",
                "Pragma": "no-cache",
                "Referrer-Policy": "no-referrer",
            },
        )
    except Exception as exc:
        _raise(exc)


@router.post("/shared-sky/studio/api/sessions/{session_id}/ingest/revoke")
def revoke_studio_ingest(session_id: str, body: StudioIngestRevokeRequest, request: Request):
    member, _ = _member(request)
    try:
        session = _studio_session(member.user_id, session_id, body.expected_studio_version)
        return revoke_ingest(member.user_id, session)
    except Exception as exc:
        _raise(exc)


def install_shared_sky_studio_ingest(app: Any) -> None:
    existing = {
        (getattr(route, "path", ""), tuple(sorted(getattr(route, "methods", set()) or set())))
        for route in app.router.routes
    }
    for route in router.routes:
        signature = (
            getattr(route, "path", ""),
            tuple(sorted(getattr(route, "methods", set()) or set())),
        )
        if signature not in existing:
            app.router.routes.append(route)
            existing.add(signature)
    app.openapi_schema = None


__all__ = [
    "StudioIngestIssueRequest",
    "StudioIngestRevokeRequest",
    "ingest_status",
    "install_shared_sky_studio_ingest",
    "issue_ingest",
    "revoke_ingest",
    "router",
]
