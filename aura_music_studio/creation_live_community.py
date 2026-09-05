from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from . import creation_live as cl

router = APIRouter(prefix="/creation-live", tags=["Creation Studios Go Live & Create"])

_COMMUNITY_SIGNATURE = (
    "/creation-live/projects/{project_name}/community",
    frozenset({"GET"}),
)
_SOURCE_REVALIDATE_SECONDS = 30


def _signature(route: Any) -> tuple[str, frozenset[str]] | None:
    path = getattr(route, "path", None)
    methods = getattr(route, "methods", None)
    if not isinstance(path, str) or not methods:
        return None
    return path, frozenset(str(method).upper() for method in methods)


def _active_project_source(user_id: str, project_name: str) -> dict[str, Any] | None:
    """Resolve the newest attached project contribution without accepting a client session ID."""
    with cl.creation_live_store.connect() as con:
        row = con.execute(
            """
            SELECT source_adapter_id
              FROM creation_live_sources
             WHERE user_id=? AND project_name=?
               AND source_status IN ('registered','ready')
               AND broadcast_id IS NOT NULL
             ORDER BY updated_at DESC
             LIMIT 1
            """,
            (user_id, project_name),
        ).fetchone()
    if not row:
        return None
    try:
        return cl.creation_live_store.get(user_id, str(row["source_adapter_id"]))
    except KeyError:
        return None


def _needs_source_revalidation(item: dict[str, Any], *, now: datetime | None = None) -> bool:
    """Bound project/rights discovery work while keeping long LIVE permissions reasonably fresh."""
    stamp = str(item.get("updated_at") or "").strip()
    if not stamp:
        return True
    try:
        updated = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return True
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    return (current.astimezone(timezone.utc) - updated.astimezone(timezone.utc)).total_seconds() >= _SOURCE_REVALIDATE_SECONDS


def _safe_chat(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    allowed = (
        "id",
        "sender_user_id",
        "reply_to_id",
        "body",
        "created_at",
        "updated_at",
        "pinned",
        "deleted",
        "moderation_state",
    )
    return [{key: item.get(key) for key in allowed if key in item} for item in messages[-100:]]


def _safe_reactions(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    safe: dict[str, int] = {}
    for key, raw in value.items():
        try:
            amount = int(raw)
        except (TypeError, ValueError):
            continue
        if amount >= 0:
            safe[str(key)[:40]] = amount
    return safe


def _display_state(value: Any, *, unavailable_reason: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"available": False, "reason": unavailable_reason}
    return dict(value)


@router.get("/projects/{project_name}/community")
def authoritative_community_panel(project_name: str, request: Request):
    """Read merged Chat 4 community truth beside the creative editor without mutating the project."""
    member = cl._member(request)
    user_id = cl._user_id(member)
    cl._project(project_name)
    source = _active_project_source(user_id, project_name)
    if source is None:
        return {
            "available": False,
            "state": "project_source_not_attached",
            "project_id": project_name,
            "project_mutated": False,
            "truth_note": "Attach this project to a Shared Sky session before loading LIVE community state.",
        }

    # The drawer polls community frequently, but source/rights discovery is intentionally bounded
    # to at most once every 30 seconds. This catches permission/privacy/source changes during long
    # LIVE sessions without turning the project filesystem into a high-frequency polling backend.
    if _needs_source_revalidation(source):
        previous_source_id = source["source_adapter_id"]
        try:
            cl.discover_sources(user_id, project_name, source["studio_type"])
        except (FileNotFoundError, OSError, ValueError):
            return {
                "available": False,
                "state": "source_revalidation_unavailable",
                "project_id": project_name,
                "source_adapter_id": previous_source_id,
                "project_mutated": False,
                "truth_note": "Current project source authority could not be refreshed. No new LIVE readiness is being claimed.",
            }
        source = _active_project_source(user_id, project_name)
        if source is None:
            return {
                "available": False,
                "state": "source_revoked_after_revalidation",
                "project_id": project_name,
                "source_adapter_id": previous_source_id,
                "project_mutated": False,
                "truth_note": "Fresh project source/rights discovery no longer authorises this contribution, so Chat 7 revoked its source handle. This does not prove the Shared Sky Programme has been cut; use the authoritative control room/emergency hide when available.",
            }

    broadcast_id = str(source.get("broadcast_id") or "").strip()
    if not broadcast_id:
        return {
            "available": False,
            "state": "live_session_not_selected",
            "project_id": project_name,
            "source_adapter_id": source["source_adapter_id"],
            "project_mutated": False,
        }
    try:
        broadcast = cl.shared_sky.broadcast(user_id, broadcast_id)
    except KeyError:
        return {
            "available": False,
            "state": "live_session_unavailable",
            "project_id": project_name,
            "source_adapter_id": source["source_adapter_id"],
            "live_session_id": broadcast_id,
            "project_mutated": False,
        }

    authoritative_state = str(broadcast.get("state") or "unknown").lower()
    if authoritative_state != "live":
        return {
            "available": False,
            "state": "live_session_not_live",
            "authoritative_state": authoritative_state,
            "project_id": project_name,
            "source_adapter_id": source["source_adapter_id"],
            "live_session_id": broadcast_id,
            "project_mutated": False,
            "truth_note": "Community metrics are not fabricated before Shared Sky declares the session LIVE.",
        }

    try:
        from .shared_sky_live_community import community
    except ImportError:
        return {
            "available": False,
            "state": "community_contract_unavailable",
            "project_id": project_name,
            "source_adapter_id": source["source_adapter_id"],
            "live_session_id": broadcast_id,
            "project_mutated": False,
        }

    try:
        detail = community.detail(broadcast_id, request, user_id)
        messages = community.chat_history(broadcast_id, limit=100)
    except PermissionError as exc:
        raise HTTPException(
            403,
            {"code": "community_access_denied", "message": "Shared Sky community access is not permitted for this session."},
        ) from exc
    except KeyError as exc:
        raise HTTPException(
            404,
            {"code": "live_session_ended", "message": "Shared Sky LIVE community state is no longer available."},
        ) from exc

    live = detail.get("broadcast") if isinstance(detail.get("broadcast"), dict) else {}
    return {
        "available": True,
        "state": "live",
        "project_id": project_name,
        "source_adapter_id": source["source_adapter_id"],
        "live_session_id": broadcast_id,
        "broadcast": {
            "id": live.get("id"),
            "title": live.get("title"),
            "state": live.get("state"),
            "authoritative_state": live.get("authoritative_state"),
            "started_at": live.get("started_at"),
        },
        "viewer_count": int(detail.get("viewer_count") or 0),
        "count_definition": detail.get("count_definition"),
        "chat_settings": detail.get("chat_settings") if isinstance(detail.get("chat_settings"), dict) else {},
        "chat": _safe_chat(messages),
        "reactions": _safe_reactions(detail.get("reactions")),
        "gift_display": _display_state(detail.get("gift_display"), unavailable_reason="gift_contract_unavailable"),
        "battle_display": _display_state(detail.get("battle_display"), unavailable_reason="battle_contract_unavailable"),
        "canonical_actions": {
            "chat": f"/shared-sky/live/api/watch/{broadcast_id}/chat",
            "events": f"/shared-sky/live/api/watch/{broadcast_id}/events",
            "polls": f"/shared-sky/live/api/watch/{broadcast_id}/polls",
            "qa": f"/shared-sky/live/api/watch/{broadcast_id}/qa",
        },
        "project_mutated": False,
        "financial_mutation": False,
        "battle_score_mutation": False,
        "truth_note": "Viewer/community/Gift/Battle display state comes from Chat 4 and its registered owning adapters; incoming community activity never edits this creative project automatically.",
    }


def install_creation_live_community_route(app: Any) -> None:
    """Replace the old compatibility GET and install its read-only creator-side UI."""
    from .creation_live_ui_community import install_creation_live_community_ui

    install_creation_live_community_ui()

    kept = []
    found = False
    for route in app.router.routes:
        if _signature(route) != _COMMUNITY_SIGNATURE:
            kept.append(route)
            continue
        endpoint = getattr(route, "endpoint", None)
        if getattr(endpoint, "__module__", None) == __name__:
            kept.append(route)
            found = True
    if len(kept) != len(app.router.routes):
        app.router.routes[:] = kept
    if not found:
        app.include_router(router)
    app.state.creation_live_community_route_installed = True


__all__ = [
    "authoritative_community_panel",
    "install_creation_live_community_route",
    "router",
]
