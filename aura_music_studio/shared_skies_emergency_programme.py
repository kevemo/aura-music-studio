from __future__ import annotations

from copy import deepcopy
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .esp_niche import require_esp_hub_member
from .shared_sky_control_room import (
    StudioConflict,
    StudioInvariantError,
    StudioTransportError,
    studio,
    studio_repo,
    validate_no_secrets,
)

router = APIRouter(tags=["Shared Skies Studio Emergency Controls"])


class EmergencyHideRequest(BaseModel):
    expected_version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=300)


def build_emergency_hidden_snapshot(
    programme_snapshot: dict[str, Any], source_id: str
) -> tuple[dict[str, Any], bool]:
    """Return a safe Programme snapshot with exactly one source hidden.

    The immutable committed Programme snapshot is copied rather than mutating Preview/project
    state. This lets an operator remove a compromised or unsafe source from Programme immediately
    while preserving the normal Preview composition for later repair. No provider credential,
    source URL or private project material is introduced by this transformation.
    """

    clean_source_id = str(source_id or "").strip()
    if not clean_source_id:
        raise StudioInvariantError("A Programme source ID is required")
    if not isinstance(programme_snapshot, dict) or not programme_snapshot.get("scene"):
        raise StudioInvariantError("No committed Programme snapshot is available")

    snapshot = deepcopy(programme_snapshot)
    sources = snapshot.get("sources")
    if not isinstance(sources, list):
        raise StudioInvariantError("Committed Programme snapshot has no source list")

    matched = False
    changed = False
    for source in sources:
        if not isinstance(source, dict) or str(source.get("id") or "") != clean_source_id:
            continue
        matched = True
        if bool(source.get("visible", True)):
            source["visible"] = False
            changed = True
        break
    if not matched:
        raise StudioInvariantError("Programme source is not present in the committed snapshot")
    validate_no_secrets(snapshot)
    return snapshot, changed


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, KeyError):
        return HTTPException(404, "Shared Skies Studio session was not found")
    if isinstance(exc, StudioConflict):
        return HTTPException(409, "Studio state changed in another operator session")
    if isinstance(exc, StudioInvariantError):
        return HTTPException(409, str(exc))
    if isinstance(exc, StudioTransportError):
        return HTTPException(409, "Programme update was rejected by authoritative transport")
    return HTTPException(400, "Emergency Programme action could not be completed")


@router.post("/shared-sky/studio/api/sessions/{session_id}/emergency/hide-source/{source_id}")
def emergency_hide_programme_source(
    session_id: str,
    source_id: str,
    body: EmergencyHideRequest,
    request: Request,
):
    member, _ = require_esp_hub_member(request)
    user_id = str(member.user_id)
    try:
        current = studio_repo.get_session(user_id, session_id)
        if int(current.get("version") or 0) != body.expected_version:
            raise StudioConflict("Studio state changed in another operator session")
        snapshot, changed = build_emergency_hidden_snapshot(
            current.get("programme_snapshot") or {}, source_id
        )
        if not changed:
            return {
                **studio.hydrate(user_id, current),
                "emergency": {
                    "action": "hide_source",
                    "source_id": source_id,
                    "already_hidden": True,
                    "authoritative_programme_commit": False,
                },
            }

        correlation_id = f"emergency-{uuid4().hex}"
        commit = studio.transport.commit_programme(
            user_id,
            current.get("broadcast_id"),
            snapshot,
            correlation_id,
        )
        if not commit.accepted:
            raise StudioTransportError("Authoritative transport rejected emergency Programme update")

        updated = studio_repo.complete_programme(
            user_id,
            session_id,
            body.expected_version,
            snapshot,
            commit.__dict__,
        )
        studio.graph.event(
            user_id,
            current.get("broadcast_id"),
            "studio_emergency_source_hidden",
            {
                "session_id": session_id,
                "source_id": source_id,
                "reason": body.reason.strip(),
                "correlation_id": correlation_id,
            },
        )
        return {
            **studio.hydrate(user_id, updated),
            "emergency": {
                "action": "hide_source",
                "source_id": source_id,
                "already_hidden": False,
                "authoritative_programme_commit": bool(commit.authoritative),
                "correlation_id": correlation_id,
            },
        }
    except Exception as exc:
        raise _http_error(exc) from exc


def install_shared_skies_emergency_programme(app: Any) -> None:
    existing = {
        (
            str(getattr(route, "path", "")),
            tuple(sorted(str(method).upper() for method in (getattr(route, "methods", set()) or set()))),
        )
        for route in app.router.routes
    }
    for route in tuple(router.routes):
        signature = (
            str(getattr(route, "path", "")),
            tuple(sorted(str(method).upper() for method in (getattr(route, "methods", set()) or set()))),
        )
        if signature in existing:
            continue
        app.router.routes.append(route)
        existing.add(signature)


__all__ = [
    "EmergencyHideRequest",
    "build_emergency_hidden_snapshot",
    "emergency_hide_programme_source",
    "install_shared_skies_emergency_programme",
    "router",
]
