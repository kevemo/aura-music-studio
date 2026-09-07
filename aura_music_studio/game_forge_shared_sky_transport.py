from __future__ import annotations

import json
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from .game_forge_api import _creator
from .game_forge_live_integration import (
    GameForgeSafeLiveSource,
    _correlation_id,
    _error,
    _game,
    _load_state,
    _member_identity,
    _now,
    _owned_source,
    _save_state,
)
from .shared_sky_streaming_studios import shared_sky
from .shared_sky_transport_domain import transport


router = APIRouter(tags=["Game Forge Shared Sky Transport"])


class BindGameTransportRequest(BaseModel):
    internal_playback: bool = True
    recording_enabled: bool = False
    rendition_profile: Literal[
        "source_native",
        "landscape_1080p",
        "portrait_1080p",
        "landscape_720p",
    ] = "source_native"


class SyncGameTransportRequest(BaseModel):
    force_not_ready: bool = False
    reason_code: str = Field(default="game_forge_state_sync", min_length=1, max_length=80)


_RENDITION_PROFILES: dict[str, dict] = {
    "source_native": {"mode": "source_native"},
    "landscape_1080p": {"mode": "fixed", "width": 1920, "height": 1080, "fps": 30},
    "portrait_1080p": {"mode": "fixed", "width": 1080, "height": 1920, "fps": 30},
    "landscape_720p": {"mode": "fixed", "width": 1280, "height": 720, "fps": 30},
}


def _transport_capabilities(source: GameForgeSafeLiveSource) -> dict:
    """Bounded source metadata safe for Chat 2 persistence/preflight.

    Never place source code, filesystem paths, project documents, credentials, logs,
    destination configuration, or arbitrary editor state in the transport record.
    """
    return {
        "schema_version": source.schema_version,
        "studio_type": "game_forge",
        "game_forge_source_adapter_id": source.source_adapter_id,
        "game_forge_source_type": source.source_type,
        "media_kind": source.media_kind,
        "aspect_profile": source.aspect_profile,
        "project_version": source.project_version,
        "build_id": source.build_id,
        "privacy_classification": source.privacy_classification,
        "audience_visibility": source.audience_visibility,
        "rights_readiness": source.rights_readiness,
        "presentation_mode": source.presentation_mode,
        "capture_scope": source.inclusion_manifest.capture_scope,
        "approved_surfaces": list(source.inclusion_manifest.approved_surfaces),
        "whole_window_capture": False,
        "credentials_included": False,
        "source_code_payload_included": False,
        "revoked": source.revoked,
        "health": source.health,
        "correlation_id": source.correlation_id,
    }


def _broadcast_for_source(user_id: str, source: GameForgeSafeLiveSource) -> dict:
    try:
        broadcast = shared_sky.broadcast(user_id, source.live_session_id)
    except KeyError as exc:
        raise _error(
            404,
            "live_session_ended",
            "The canonical Shared Sky broadcast for this Game Forge source was not found",
            source.correlation_id,
        ) from exc
    if broadcast.get("state") in {"ended", "failed", "cancelled"}:
        raise _error(
            409,
            "live_session_ended",
            "The canonical Shared Sky broadcast has already ended",
            source.correlation_id,
        )
    return broadcast


def _transport_state(source: GameForgeSafeLiveSource, *, force_not_ready: bool = False) -> Literal["ready", "failed"]:
    if force_not_ready or source.revoked or source.status != "active" or source.health != "ready":
        return "failed"
    return "ready"


def _existing_programme_source(user_id: str, source: GameForgeSafeLiveSource) -> dict | None:
    source_id = str(source.shared_sky_source_ref or "").strip()
    if not source_id:
        return None
    try:
        item = transport.source(user_id, source_id)
    except KeyError:
        return None
    if item.get("source_type") != "game_project" or item.get("source_ref") != source.source_adapter_id:
        raise _error(
            409,
            "live_source_privacy_blocked",
            "Stored Shared Sky programme-source identity does not match the Game Forge safe source",
            source.correlation_id,
        )
    return item


def _register_programme_source(user_id: str, source: GameForgeSafeLiveSource) -> tuple[dict, dict, bool]:
    broadcast = _broadcast_for_source(user_id, source)
    existing = _existing_programme_source(user_id, source)
    if existing is not None:
        if existing.get("project_id") != broadcast.get("project_id"):
            raise _error(
                409,
                "live_source_privacy_blocked",
                "Stored Game Forge programme source belongs to a different Shared Sky project",
                source.correlation_id,
            )
        return broadcast, existing, True

    try:
        programme = transport.register_source(
            user_id,
            str(broadcast["project_id"]),
            "game_project",
            source.source_adapter_id,
            state=_transport_state(source),
            capabilities=_transport_capabilities(source),
        )
    except KeyError as exc:
        raise _error(
            404,
            "live_session_ended",
            "The Shared Sky project attached to this broadcast no longer exists",
            source.correlation_id,
        ) from exc
    source.shared_sky_source_ref = str(programme["id"])
    source.updated_at = _now()
    return broadcast, programme, False


def _set_programme_source_state(
    user_id: str,
    source: GameForgeSafeLiveSource,
    *,
    force_not_ready: bool = False,
    reason_code: str = "game_forge_state_sync",
) -> dict | None:
    programme = _existing_programme_source(user_id, source)
    if programme is None:
        return None
    state = _transport_state(source, force_not_ready=force_not_ready)
    capabilities = _transport_capabilities(source)
    capabilities["state_reason_code"] = reason_code[:80]

    # Chat 2 owns transport execution. Chat 8 only synchronises the canonical source record
    # after proving ownership through transport.source(); no destination/relay/session fields
    # are written here.
    with transport.connect() as con:
        con.execute(
            "UPDATE shared_sky_programme_sources SET state=?,capabilities_json=?,updated_at=? "
            "WHERE id=? AND user_id=? AND source_type='game_project' AND source_ref=?",
            (
                state,
                json.dumps(capabilities, separators=(",", ":"), sort_keys=True),
                _now(),
                programme["id"],
                user_id,
                source.source_adapter_id,
            ),
        )
    return transport.source(user_id, str(programme["id"]))


@router.post("/api/game-forge/games/{game_id}/live/sources/{source_adapter_id}/transport-bind")
def bind_game_live_transport(
    game_id: str,
    source_adapter_id: str,
    body: BindGameTransportRequest,
    request: Request,
):
    member = _creator(request)
    user_id = _member_identity(member)
    _game(game_id)
    state = _load_state(game_id)
    source = _owned_source(state, source_adapter_id)
    if source.revoked or source.status != "active" or source.health != "ready":
        raise _error(409, "live_source_not_ready", "Only an active, ready Game Forge source can bind to transport")

    broadcast, programme, replay = _register_programme_source(user_id, source)
    _save_state(state)
    try:
        status = transport.configure(
            user_id,
            str(broadcast["id"]),
            source_id=str(programme["id"]),
            internal_playback=body.internal_playback,
            rendition_profile={
                **_RENDITION_PROFILES[body.rendition_profile],
                "source_kind": "game_project",
                "game_forge_source_adapter_id": source.source_adapter_id,
                "project_version": source.project_version,
                "build_id": source.build_id,
            },
            recording_enabled=body.recording_enabled,
            ingest_session_id=None,
        )
    except (KeyError, ValueError) as exc:
        raise _error(409, "live_source_not_ready", "Shared Sky transport could not bind the Game Forge programme source") from exc

    return {
        "source_adapter_id": source.source_adapter_id,
        "shared_sky_programme_source": programme,
        "shared_sky_broadcast_id": str(broadcast["id"]),
        "shared_sky_project_id": str(broadcast["project_id"]),
        "transport": status,
        "idempotent_source_registration": replay,
        "transport_owned_by_chat_2": True,
        "composition_owned_by_chat_3": True,
        "destination_credentials_stored_by_game_forge": False,
        "whole_window_capture": False,
    }


@router.post("/api/game-forge/games/{game_id}/live/sources/{source_adapter_id}/transport-sync")
def sync_game_live_transport(
    game_id: str,
    source_adapter_id: str,
    body: SyncGameTransportRequest,
    request: Request,
):
    member = _creator(request)
    user_id = _member_identity(member)
    _game(game_id)
    state = _load_state(game_id)
    source = _owned_source(state, source_adapter_id)
    source.correlation_id = _correlation_id(request)
    source.updated_at = _now()
    programme = _set_programme_source_state(
        user_id,
        source,
        force_not_ready=body.force_not_ready,
        reason_code=body.reason_code,
    )
    _save_state(state)
    return {
        "source_adapter_id": source.source_adapter_id,
        "shared_sky_programme_source": programme,
        "transport_source_state": programme.get("state") if programme else "unbound",
        "transport_owned_by_chat_2": True,
    }


@router.post("/api/game-forge/games/{game_id}/live/sources/{source_adapter_id}/transport-emergency-hide")
def emergency_hide_game_live_transport(
    game_id: str,
    source_adapter_id: str,
    request: Request,
):
    member = _creator(request)
    user_id = _member_identity(member)
    _game(game_id)
    state = _load_state(game_id)
    source = _owned_source(state, source_adapter_id)
    source.presentation_mode = "brb"
    source.status = "hidden"
    source.health = "not_ready"
    source.correlation_id = _correlation_id(request)
    source.updated_at = _now()
    programme = _set_programme_source_state(
        user_id,
        source,
        force_not_ready=True,
        reason_code="game_forge_emergency_hide",
    )
    _save_state(state)
    return {
        "source": source.model_dump(mode="json"),
        "shared_sky_programme_source": programme,
        "brb_requested": True,
        "transport_preflight_blocked": bool(programme),
        "project_deleted": False,
        "autosave_terminated": False,
        "playtest_build_deleted": False,
        "destination_credentials_stored_by_game_forge": False,
    }


__all__ = [
    "BindGameTransportRequest",
    "SyncGameTransportRequest",
    "bind_game_live_transport",
    "emergency_hide_game_live_transport",
    "router",
    "sync_game_live_transport",
]
