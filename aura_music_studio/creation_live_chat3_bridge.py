from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import Request

from . import creation_live as cl
from .creation_live_authority import authoritative_attach as _authoritative_attach
from .shared_sky_streaming_studios import SourceCreate, SourceUpdate


_PATCHED = False
_ORIGINAL_DETACH = cl.detach
_ACTIVE_ON_AIR_STATES = {"live", "degraded", "reconnecting"}


def _safe_text(value: Any, limit: int = 180) -> str:
    return " ".join(str(value or "").replace("\x00", " ").split()).strip()[:limit]


def canonical_chat3_source_type(descriptor: dict[str, Any]) -> str:
    """Map Chat 7 media semantics onto Chat 3's canonical scene-source vocabulary."""
    if descriptor.get("privacy_classification") == "advanced_workspace":
        return "presentation"
    media_kind = str(descriptor.get("media_kind") or "").lower()
    if media_kind == "audio":
        return "audio"
    if media_kind in {"video", "audiovisual"}:
        return "video"
    if media_kind == "still-or-slideshow":
        return "image"
    return "presentation"


def _find_studio_session(
    user_id: str,
    shared_sky_project_id: str | None,
    broadcast_id: str | None,
) -> dict[str, Any] | None:
    """Find, but never create, the current Chat 3 session for this creator/project.

    Chat 3 currently exposes get_session(session_id) but not a non-mutating project lookup.
    This compatibility seam reads only the stable session identity from Chat 3's canonical table,
    then delegates parsing/ownership checks back to StudioRepository.get_session().
    """
    project_id = _safe_text(shared_sky_project_id)
    if not project_id:
        return None
    try:
        from .shared_sky_control_room import studio_repo
    except ImportError:
        return None
    query = (
        "SELECT id FROM shared_sky_studio_sessions "
        "WHERE user_id=? AND project_id=? AND broadcast_id=? ORDER BY updated_at DESC LIMIT 1"
        if broadcast_id
        else "SELECT id FROM shared_sky_studio_sessions WHERE user_id=? AND project_id=? ORDER BY updated_at DESC LIMIT 1"
    )
    params: tuple[Any, ...] = (user_id, project_id, broadcast_id) if broadcast_id else (user_id, project_id)
    try:
        with sqlite3.connect(studio_repo.db_path, timeout=5) as con:
            row = con.execute(query, params).fetchone()
    except sqlite3.Error:
        return None
    if not row:
        return None
    try:
        return studio_repo.get_session(user_id, str(row[0]))
    except (KeyError, ValueError, sqlite3.Error):
        return None


def _programme_match(session: dict[str, Any], adapter_id: str) -> dict[str, Any] | None:
    snapshot = session.get("programme_snapshot") if isinstance(session.get("programme_snapshot"), dict) else {}
    sources = snapshot.get("sources") if isinstance(snapshot.get("sources"), list) else []
    for source in sources:
        if not isinstance(source, dict):
            continue
        config = source.get("config") if isinstance(source.get("config"), dict) else {}
        if str(config.get("creation_live_adapter_id") or "") == adapter_id:
            return source
        if str(source.get("id") or "") == adapter_id:
            return source
    return None


def programme_truth(user_id: str, item: dict[str, Any]) -> dict[str, Any]:
    """Return exact-source Programme truth from Chat 3's immutable committed snapshot."""
    broadcast_id = _safe_text(item.get("broadcast_id"))
    if not broadcast_id:
        return {
            "session_state": "not_selected",
            "on_air": False,
            "programme_state": "not_selected",
            "source": "chat3_programme_snapshot",
        }
    try:
        broadcast = cl.shared_sky.broadcast(user_id, broadcast_id)
    except KeyError:
        return {
            "session_state": "ended_or_unavailable",
            "on_air": False,
            "programme_state": "unavailable",
            "source": "chat3_programme_snapshot",
        }

    session = _find_studio_session(
        user_id,
        item.get("shared_sky_project_id") or broadcast.get("project_id"),
        broadcast_id,
    )
    session_state = _safe_text(broadcast.get("state"), 80) or "unknown"
    if session is None:
        return {
            "session_state": session_state,
            "on_air": False,
            "programme_state": "control_room_not_open",
            "source": "chat3_programme_snapshot",
        }
    match = _programme_match(session, str(item.get("source_adapter_id") or ""))
    if match is None:
        return {
            "session_state": session_state,
            "on_air": False,
            "programme_state": "not_on_programme",
            "studio_session_id": session.get("id"),
            "programme_scene_id": session.get("programme_scene_id"),
            "source": "chat3_programme_snapshot",
        }
    visible = bool(match.get("visible", True))
    on_air = bool(visible and session_state in _ACTIVE_ON_AIR_STATES)
    return {
        "session_state": session_state,
        "on_air": on_air,
        "programme_state": "on_programme" if visible else "programme_hidden",
        "studio_session_id": session.get("id"),
        "programme_scene_id": session.get("programme_scene_id"),
        "programme_source_id": match.get("id"),
        "source": "chat3_programme_snapshot",
    }


def _safe_chat3_config(item: dict[str, Any]) -> dict[str, Any]:
    descriptor = item.get("descriptor") if isinstance(item.get("descriptor"), dict) else {}
    rights = descriptor.get("rights") if isinstance(descriptor.get("rights"), dict) else {}
    capabilities = descriptor.get("capabilities") if isinstance(descriptor.get("capabilities"), dict) else {}
    adapter_id = str(item.get("source_adapter_id") or descriptor.get("source_adapter_id") or "")
    config = {
        "privacy": "programme_safe",
        "creation_live_adapter_id": adapter_id,
        "creation_live_schema_version": descriptor.get("schema_version", 1),
        "creation_live_source_type": _safe_text(descriptor.get("source_type"), 80),
        "media_kind": _safe_text(descriptor.get("media_kind"), 80),
        "preview_endpoint": f"/creation-live/projects/{item['project_name']}/sources/{adapter_id}/media"
        if descriptor.get("preview_kind") == "media"
        else None,
        "capabilities": {
            key: bool(capabilities.get(key))
            for key in ("audio", "video", "still", "version_pin", "full_workspace")
            if key in capabilities
        },
        "provenance": {
            "creative_project_id": item.get("project_name"),
            "public_version_id": descriptor.get("public_version_id"),
            "rights_state": _safe_text(rights.get("state"), 40) or "unknown",
            "correlation_id": _safe_text(descriptor.get("correlation_id"), 160),
        },
        "capture_mode": "browser_permission_required"
        if descriptor.get("privacy_classification") == "advanced_workspace"
        else "creation_live_safe_output",
    }
    return {key: value for key, value in config.items() if value is not None}


def _matching_graph_sources(user_id: str, item: dict[str, Any]) -> list[dict[str, Any]]:
    project_id = _safe_text(item.get("shared_sky_project_id"))
    adapter_id = str(item.get("source_adapter_id") or "")
    if not project_id or not adapter_id:
        return []
    try:
        project = cl.shared_sky.project(user_id, project_id)
    except KeyError:
        return []
    matches: list[dict[str, Any]] = []
    for scene in project.get("scenes", []):
        for source in scene.get("sources", []):
            config = source.get("config") if isinstance(source.get("config"), dict) else {}
            if str(config.get("creation_live_adapter_id") or "") == adapter_id:
                matches.append(source)
    return matches


def register_preview_source(user_id: str, item: dict[str, Any]) -> dict[str, Any]:
    """Idempotently materialise an attached Chat 7 source in Chat 3's current Preview scene."""
    if str(item.get("source_status") or "") not in {"registered", "ready"}:
        return {"available": True, "state": "source_not_attached", "registered": False}
    descriptor = item.get("descriptor") if isinstance(item.get("descriptor"), dict) else {}
    rights = descriptor.get("rights") if isinstance(descriptor.get("rights"), dict) else {}
    if rights.get("state") == "blocked":
        return {"available": True, "state": "project_rights_blocked", "registered": False}
    session = _find_studio_session(user_id, item.get("shared_sky_project_id"), item.get("broadcast_id"))
    if session is None:
        return {"available": True, "state": "control_room_not_open", "registered": False}
    preview_scene_id = _safe_text(session.get("preview_scene_id"))
    if not preview_scene_id:
        return {"available": True, "state": "preview_scene_not_selected", "registered": False, "studio_session_id": session.get("id")}
    try:
        scene = cl.shared_sky.scene(user_id, preview_scene_id)
    except KeyError:
        return {"available": True, "state": "preview_scene_unavailable", "registered": False, "studio_session_id": session.get("id")}
    if str(scene.get("project_id") or "") != str(item.get("shared_sky_project_id") or ""):
        return {"available": True, "state": "control_room_project_mismatch", "registered": False}
    if item.get("broadcast_id") and session.get("broadcast_id") and item.get("broadcast_id") != session.get("broadcast_id"):
        return {"available": True, "state": "control_room_session_mismatch", "registered": False}

    adapter_id = str(item.get("source_adapter_id") or "")
    for source in scene.get("sources", []):
        config = source.get("config") if isinstance(source.get("config"), dict) else {}
        if str(config.get("creation_live_adapter_id") or "") == adapter_id:
            if not bool(source.get("visible", True)):
                source = cl.shared_sky.update_source(user_id, source["id"], SourceUpdate(visible=True))
            return {
                "available": True,
                "state": "ready",
                "registered": True,
                "reused": True,
                "studio_session_id": session.get("id"),
                "scene_id": preview_scene_id,
                "chat3_source_id": source.get("id"),
                "source_type": source.get("source_type"),
            }

    source = cl.shared_sky.create_source(
        user_id,
        preview_scene_id,
        SourceCreate(
            source_type=canonical_chat3_source_type(descriptor),
            name=_safe_text(descriptor.get("safe_display_name"), 120) or "Creation project source",
            config=_safe_chat3_config(item),
            visible=True,
            locked=False,
            z_index=len(scene.get("sources", [])),
        ),
    )
    return {
        "available": True,
        "state": "ready",
        "registered": True,
        "reused": False,
        "studio_session_id": session.get("id"),
        "scene_id": preview_scene_id,
        "chat3_source_id": source.get("id"),
        "source_type": source.get("source_type"),
    }


def hide_graph_sources(user_id: str, item: dict[str, Any]) -> dict[str, Any]:
    """Hide future Chat 3 graph use on detach without rewriting committed Programme truth."""
    matches = _matching_graph_sources(user_id, item)
    hidden = 0
    for source in matches:
        if bool(source.get("visible", True)):
            cl.shared_sky.update_source(user_id, source["id"], SourceUpdate(visible=False))
            hidden += 1
    truth = programme_truth(user_id, item)
    return {
        "available": True,
        "state": "graph_hidden" if matches else "no_control_room_source",
        "matched_sources": len(matches),
        "hidden_sources": hidden,
        "programme_snapshot_unchanged": True,
        "authoritative_live": truth,
        "truth_note": (
            "Chat 3 scene-graph occurrences are hidden for future composition. An already committed Programme snapshot is immutable and remains authoritative until Chat 3 CUT/TRANSITION changes it."
        ),
    }


def authoritative_attach_with_chat3(project_name: str, source_adapter_id: str, body: cl.AttachRequest, request: Request):
    result = _authoritative_attach(project_name, source_adapter_id, body, request)
    user_id = cl._user_id(cl._member(request))
    try:
        item = cl.creation_live_store.get(user_id, source_adapter_id)
    except KeyError:
        return result
    registration = register_preview_source(user_id, item)
    result["control_room_registration"] = registration
    result["authoritative_live"] = programme_truth(user_id, item)
    if registration.get("registered"):
        result["truth_note"] = (
            "The safe project source is registered in Chat 3 Preview. It is not ON AIR until Chat 3 commits a Programme snapshot containing this exact adapter and the Shared Sky session is live."
        )
    return result


def detach_with_chat3(project_name: str, source_adapter_id: str, body: cl.DetachRequest, request: Request):
    result = _ORIGINAL_DETACH(project_name, source_adapter_id, body, request)
    user_id = cl._user_id(cl._member(request))
    try:
        item = cl.creation_live_store.get(user_id, source_adapter_id)
    except KeyError:
        return result
    propagation = hide_graph_sources(user_id, item)
    result["control_room_detach"] = propagation
    result["authoritative_live"] = propagation["authoritative_live"]
    return result


def install_creation_live_chat3_bridge() -> None:
    """Install exact Chat 3 Programme truth and detach propagation before route composition."""
    global _PATCHED
    if _PATCHED:
        return
    cl._programme_truth = programme_truth
    cl.detach = detach_with_chat3
    cl.creation_live_chat3_bridge_installed = True
    _PATCHED = True


__all__ = [
    "authoritative_attach_with_chat3",
    "canonical_chat3_source_type",
    "hide_graph_sources",
    "install_creation_live_chat3_bridge",
    "programme_truth",
    "register_preview_source",
]
