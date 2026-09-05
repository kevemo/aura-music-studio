from __future__ import annotations

import importlib
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from . import shared_sky_live_community as live
from .owner_identity import owner_session_authorized


router = APIRouter(tags=["Shared Sky Battle Viewer Bridge"])


class Chat6BattleDisplayAdapter:
    """Read-only projection of Chat 6 viewer-safe Battle state.

    The adapter is deliberately unable to discover Battles through private Chat 6 tables. It only
    activates when Chat 6 publishes an explicit ``viewer_live_battle(live_session_id)`` viewer
    compatibility seam, either at module level or on the canonical Battle store.
    """

    def __init__(self, viewer_live_battle: Any):
        self.viewer_live_battle = viewer_live_battle

    def state(self, broadcast_id: str, viewer_user_id: str | None) -> dict:
        del viewer_user_id
        try:
            snapshot = self.viewer_live_battle(broadcast_id)
        except Exception:
            return {
                "available": False,
                "reason": "battle_snapshot_unavailable",
                "source": "chat6_viewer_bridge",
            }
        if not isinstance(snapshot, dict) or not snapshot:
            return {
                "available": False,
                "reason": "no_active_battle",
                "source": "chat6_viewer_bridge",
            }
        battle = snapshot.get("battle") if isinstance(snapshot.get("battle"), dict) else {}
        if str(battle.get("live_session_id") or "") != broadcast_id:
            return {
                "available": False,
                "reason": "battle_live_session_mismatch",
                "source": "chat6_viewer_bridge",
            }
        participants = snapshot.get("participants")
        teams = snapshot.get("teams")
        scores = snapshot.get("scores")
        return {
            "available": True,
            "battle_id": battle.get("id"),
            "status": battle.get("status"),
            "mode": battle.get("mode"),
            "participants": list(participants) if isinstance(participants, list) else [],
            "teams": list(teams) if isinstance(teams, list) else [],
            "scores": dict(scores) if isinstance(scores, dict) else {},
            "score_version": int(snapshot.get("score_version") or 0),
            "event_cursor": int(snapshot.get("event_cursor") or 0),
            "remaining_ms": snapshot.get("remaining_ms"),
            "current_round": snapshot.get("current_round"),
            "result": snapshot.get("result"),
            "source": "chat6_viewer_live_battle",
        }


def install_chat6_battle_viewer_bridge() -> dict[str, Any]:
    """Register Chat 6 viewer projection only when its explicit LIVE lookup exists.

    A future Chat 6 module may exist while its deployment/capacity configuration is still invalid.
    Discovery must fail closed without preventing the rest of Shared Sky Watch from starting.
    """

    try:
        battle_api = importlib.import_module(f"{__package__}.shared_sky_battle_api")
    except ModuleNotFoundError:
        return {
            "state": "pending",
            "reason": "chat6_viewer_live_battle_lookup_unavailable",
        }
    except Exception:
        return {
            "state": "degraded",
            "reason": "chat6_battle_module_unavailable",
        }

    viewer_live_battle = getattr(battle_api, "viewer_live_battle", None)
    source = "shared_sky_battle_api.viewer_live_battle"
    if not callable(viewer_live_battle):
        battle_store = getattr(battle_api, "battle_store", None)
        viewer_live_battle = getattr(battle_store, "viewer_live_battle", None)
        source = "shared_sky_battle_api.battle_store.viewer_live_battle"
    if not callable(viewer_live_battle):
        return {
            "state": "pending",
            "reason": "chat6_viewer_live_battle_lookup_unavailable",
        }

    try:
        live.register_battle_display_adapter(Chat6BattleDisplayAdapter(viewer_live_battle))
        return {
            "state": "registered",
            "source": source,
            "authority": "chat6_read_only",
        }
    except Exception:
        return {
            "state": "degraded",
            "reason": "chat6_battle_viewer_bridge_registration_failed",
        }


def _access_or_raise(broadcast_id: str, request: Request) -> str | None:
    member = live.optional_member(request)
    viewer_user_id = member.user_id if member else None
    try:
        decision = live.community.access(
            broadcast_id,
            viewer_user_id,
            direct=True,
            owner=owner_session_authorized(request),
        )
    except KeyError as exc:
        raise HTTPException(404, "Shared Sky LIVE not found") from exc
    if not decision.allowed:
        raise HTTPException(403, decision.reason)
    return viewer_user_id


@router.get("/shared-sky/live/api/watch/{broadcast_id}/battle-display")
def refresh_battle_display(broadcast_id: str, request: Request):
    viewer_user_id = _access_or_raise(broadcast_id, request)
    try:
        return live._battle_adapter.state(broadcast_id, viewer_user_id)
    except Exception as exc:
        raise HTTPException(503, "Shared Sky Battle display is unavailable") from exc


__all__ = [
    "router",
    "Chat6BattleDisplayAdapter",
    "install_chat6_battle_viewer_bridge",
    "refresh_battle_display",
]
