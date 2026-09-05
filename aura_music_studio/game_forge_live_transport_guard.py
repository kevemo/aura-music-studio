from __future__ import annotations

from fastapi import APIRouter, Request

from .game_forge_api import _creator
from .game_forge_live_integration import (
    EmergencyHideRequest,
    PromoteLiveVersionRequest,
    TransitionLiveSourceRequest,
    _load_state,
    _member_identity,
    _owned_source,
    detach_game_live_source,
    emergency_hide_game_live_source,
    promote_game_live_version,
    transition_game_live_source,
)
from .game_forge_shared_sky_transport import _set_programme_source_state


router = APIRouter(tags=["Game Forge Shared Sky Live Transport Guard"])


def _sync_bound_programme_source(
    *,
    user_id: str,
    game_id: str,
    source_adapter_id: str,
    reason_code: str,
    force_not_ready: bool = False,
) -> dict | None:
    """Synchronise only an already-bound canonical Chat 2 programme source.

    The underlying live mutation remains owned by game_forge_live_integration and
    transport execution remains owned by Chat 2. This guard closes the compatibility
    gap where the older Game Forge API could hide/detach a source while leaving its
    canonical programme-source record marked ready.
    """
    state = _load_state(game_id)
    source = _owned_source(state, source_adapter_id)
    return _set_programme_source_state(
        user_id,
        source,
        force_not_ready=force_not_ready,
        reason_code=reason_code,
    )


def _with_transport(payload: dict, programme: dict | None) -> dict:
    return {
        **payload,
        "shared_sky_programme_source": programme,
        "transport_source_state": programme.get("state") if programme else "unbound",
        "transport_state_synchronised": programme is not None,
        "transport_owned_by_chat_2": True,
    }


@router.patch("/api/game-forge/games/{game_id}/live/sources/{source_adapter_id}/presentation")
def guarded_transition_game_live_source(
    game_id: str,
    source_adapter_id: str,
    body: TransitionLiveSourceRequest,
    request: Request,
):
    member = _creator(request)
    user_id = _member_identity(member)
    payload = transition_game_live_source(game_id, source_adapter_id, body, request)
    programme = _sync_bound_programme_source(
        user_id=user_id,
        game_id=game_id,
        source_adapter_id=source_adapter_id,
        reason_code=("game_forge_brb" if body.presentation_mode == "brb" else "game_forge_presentation_ready"),
        force_not_ready=False,
    )
    return _with_transport(payload, programme)


@router.post("/api/game-forge/games/{game_id}/live/sources/{source_adapter_id}/promote-version")
def guarded_promote_game_live_version(
    game_id: str,
    source_adapter_id: str,
    body: PromoteLiveVersionRequest,
    request: Request,
):
    member = _creator(request)
    user_id = _member_identity(member)
    payload = promote_game_live_version(game_id, source_adapter_id, body, request)
    programme = _sync_bound_programme_source(
        user_id=user_id,
        game_id=game_id,
        source_adapter_id=source_adapter_id,
        reason_code="game_forge_version_promoted",
        force_not_ready=False,
    )
    return _with_transport(payload, programme)


@router.post("/api/game-forge/games/{game_id}/live/sources/{source_adapter_id}/emergency-hide")
def guarded_emergency_hide_game_live_source(
    game_id: str,
    source_adapter_id: str,
    body: EmergencyHideRequest,
    request: Request,
):
    member = _creator(request)
    user_id = _member_identity(member)
    payload = emergency_hide_game_live_source(game_id, source_adapter_id, body, request)
    programme = _sync_bound_programme_source(
        user_id=user_id,
        game_id=game_id,
        source_adapter_id=source_adapter_id,
        reason_code=("game_forge_source_revoked" if body.revoke else "game_forge_emergency_hide"),
        force_not_ready=True,
    )
    return _with_transport(payload, programme)


@router.delete("/api/game-forge/games/{game_id}/live/sources/{source_adapter_id}")
def guarded_detach_game_live_source(game_id: str, source_adapter_id: str, request: Request):
    member = _creator(request)
    user_id = _member_identity(member)
    payload = detach_game_live_source(game_id, source_adapter_id, request)
    programme = _sync_bound_programme_source(
        user_id=user_id,
        game_id=game_id,
        source_adapter_id=source_adapter_id,
        reason_code="game_forge_source_detached",
        force_not_ready=True,
    )
    return _with_transport(payload, programme)


__all__ = [
    "guarded_detach_game_live_source",
    "guarded_emergency_hide_game_live_source",
    "guarded_promote_game_live_version",
    "guarded_transition_game_live_source",
    "router",
]
