from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from . import creation_live as cl

router = APIRouter(prefix="/creation-live", tags=["Creation Studios Go Live & Create"])

_TERMINAL = {"ended", "failed", "cancelled", "canceled"}
_MARKER_SESSION_STATES = {"starting", "live", "degraded", "reconnecting"}
_AUTHORITY_SIGNATURES = {
    ("/creation-live/projects/{project_name}/sources/{source_adapter_id}/attach", frozenset({"POST"})),
    ("/creation-live/projects/{project_name}/markers", frozenset({"POST"})),
    ("/creation-live/projects/{project_name}/returns", frozenset({"POST"})),
}


def _item(user_id: str, project_name: str, source_adapter_id: str) -> dict[str, Any]:
    try:
        item = cl.creation_live_store.get(user_id, source_adapter_id)
    except KeyError as exc:
        raise HTTPException(404, "Creation LIVE source not found") from exc
    if item["project_name"] != project_name:
        raise HTTPException(404, "Creation LIVE source not found")
    return item


def _current_broadcast(user_id: str, broadcast_id: str) -> dict[str, Any]:
    try:
        return cl.shared_sky.broadcast(user_id, broadcast_id)
    except KeyError as exc:
        raise HTTPException(
            409,
            {"code": "live_session_ended", "message": "The linked Shared Sky session is unavailable."},
        ) from exc


def _safe_chat2_preflight(user_id: str, broadcast_id: str) -> dict[str, Any]:
    try:
        from .shared_sky_transport_domain import transport
    except ImportError:
        return {
            "available": False,
            "ready": None,
            "state": "compatibility_pending",
            "blocking_errors": [],
            "warnings": [],
        }
    try:
        result = transport.preflight(user_id, broadcast_id)
    except (KeyError, ValueError, RuntimeError):
        return {
            "available": True,
            "ready": False,
            "state": "preflight_unavailable",
            "blocking_errors": [{"code": "transport_preflight_unavailable"}],
            "warnings": [],
        }
    blocking = result.get("blocking_errors") if isinstance(result, dict) else None
    warnings = result.get("warnings") if isinstance(result, dict) else None
    return {
        "available": True,
        "ready": bool(result.get("ready")) if isinstance(result, dict) and "ready" in result else None,
        "state": "ready" if isinstance(result, dict) and result.get("ready") is True else "blocked",
        "blocking_errors": blocking if isinstance(blocking, list) else [],
        "warnings": warnings if isinstance(warnings, list) else [],
        "correlation_id": result.get("correlation_id") if isinstance(result, dict) else None,
    }


def _refresh_source_preflight(user_id: str, project_name: str, item: dict[str, Any]) -> dict[str, Any]:
    sources = cl.discover_sources(user_id, project_name, item["studio_type"])
    current = next(
        (source for source in sources if source.get("source_adapter_id") == item["source_adapter_id"]),
        None,
    )
    if current is None:
        raise HTTPException(
            409,
            {
                "code": "source_not_ready",
                "message": "The selected project output is no longer an eligible LIVE source. Select a current source.",
            },
        )
    rights = current.get("rights") or {}
    if rights.get("state") == "blocked":
        raise HTTPException(
            403,
            {
                "code": "project_rights_blocked",
                "message": "Current project rights/privacy state blocks this source from LIVE output.",
                "reasons": rights.get("codes") if isinstance(rights.get("codes"), list) else [],
            },
        )
    return current


@router.post("/projects/{project_name}/sources/{source_adapter_id}/attach")
def authoritative_attach(
    project_name: str,
    source_adapter_id: str,
    body: cl.AttachRequest,
    request: Request,
):
    """Revalidate current source eligibility before executing the canonical Chat 7 attach."""
    user_id = cl._user_id(cl._member(request))
    cl._project(project_name)
    item = _item(user_id, project_name, source_adapter_id)
    refreshed = _refresh_source_preflight(user_id, project_name, item)

    if body.broadcast_id:
        broadcast = _current_broadcast(user_id, body.broadcast_id)
        state = str(broadcast.get("state") or "").lower()
        if state in _TERMINAL:
            raise HTTPException(
                409,
                {"code": "live_session_ended", "message": "The selected Shared Sky session has ended."},
            )

    result = cl.attach(project_name, source_adapter_id, body, request)
    result["current_source_preflight"] = {
        "source_adapter_id": source_adapter_id,
        "version": refreshed.get("version"),
        "rights_state": (refreshed.get("rights") or {}).get("state"),
        "privacy": refreshed.get("privacy_classification"),
    }
    result["transport_preflight"] = (
        _safe_chat2_preflight(user_id, body.broadcast_id)
        if body.broadcast_id
        else {
            "available": False,
            "ready": None,
            "state": "broadcast_not_selected",
            "blocking_errors": [],
            "warnings": [],
        }
    )
    result["truth_note"] = (
        "Source eligibility was revalidated immediately before attachment. Transport preflight is reported separately; "
        "neither source registration nor preflight alone proves this source is ON AIR."
    )
    return result


@router.post("/projects/{project_name}/markers")
def authoritative_marker(project_name: str, body: cl.MarkerRequest, request: Request):
    """Accept LIVE markers only for the source's currently linked active Shared Sky session."""
    user_id = cl._user_id(cl._member(request))
    cl._project(project_name)
    item = _item(user_id, project_name, body.source_adapter_id)
    if item["source_status"] not in {"registered", "ready"}:
        raise HTTPException(
            409,
            {"code": "source_not_ready", "message": "Attach the project source to an active Shared Sky session before adding LIVE markers."},
        )
    linked = str(item.get("broadcast_id") or "").strip()
    if not linked or linked != body.live_session_id:
        raise HTTPException(
            409,
            {"code": "marker_session_mismatch", "message": "Marker session does not match the project source's authoritative live-session link."},
        )
    broadcast = _current_broadcast(user_id, linked)
    state = str(broadcast.get("state") or "").lower()
    if state not in _MARKER_SESSION_STATES:
        raise HTTPException(
            409,
            {"code": "live_session_ended", "message": "LIVE markers require an active Shared Sky session."},
        )
    result = cl.add_marker(project_name, body, request)
    result["session_state"] = state
    result["authority"] = "shared_sky_live_session"
    return result


def _recording_truth(user_id: str, live_session_id: str, recording_id: str) -> dict[str, Any]:
    try:
        from .shared_sky_transport_domain import transport
    except ImportError:
        return {"available": False, "state": "compatibility_pending", "recording": None}
    try:
        status = transport.status(user_id, live_session_id)
    except (KeyError, ValueError, RuntimeError):
        return {"available": True, "state": "session_unavailable", "recording": None}
    recordings = status.get("recordings") if isinstance(status, dict) else None
    if not isinstance(recordings, list):
        recordings = []
    recording = next((item for item in recordings if str(item.get("id") or "") == recording_id), None)
    if recording is None:
        return {"available": True, "state": "recording_not_found", "recording": None}
    safe = {
        "id": str(recording.get("id") or ""),
        "kind": str(recording.get("kind") or ""),
        "state": str(recording.get("state") or "unknown").lower(),
        "asset_id": recording.get("asset_id"),
        "size_bytes": recording.get("size_bytes"),
        "duration_ms": recording.get("duration_ms"),
        "reason_code": str(recording.get("reason_code") or "")[:80],
        "updated_at": recording.get("updated_at"),
    }
    return {"available": True, "state": safe["state"], "recording": safe}


def _processing_state(authority: dict[str, Any], requested: str) -> str:
    if not authority.get("available"):
        return requested
    recording = authority.get("recording")
    if not isinstance(recording, dict):
        return "processing"
    state = str(recording.get("state") or "").lower()
    asset_id = str(recording.get("asset_id") or "").strip()
    if state in {"failed"}:
        return "failed"
    if state in {"incomplete", "interrupted"}:
        return "incomplete"
    if state in {"recovered"}:
        return "recovered" if asset_id else "processing"
    if state in {"ready", "completed", "available"}:
        return "ready" if asset_id else "processing"
    return "processing"


@router.post("/projects/{project_name}/returns")
def authoritative_return(project_name: str, body: cl.ReturnAssetRequest, request: Request):
    """Use Chat 2 recording truth for returned media state whenever the transport contract exists."""
    user_id = cl._user_id(cl._member(request))
    cl._project(project_name)
    source = _item(user_id, project_name, body.source_adapter_id)
    if source["studio_type"] != body.studio_type:
        raise HTTPException(
            409,
            {"code": "return_source_mismatch", "message": "Returned asset does not match the originating project/studio source."},
        )
    linked = str(source.get("broadcast_id") or "").strip()
    if linked and linked != body.live_session_id:
        raise HTTPException(
            409,
            {"code": "return_session_mismatch", "message": "Returned asset live session does not match the source provenance."},
        )

    authority = _recording_truth(user_id, body.live_session_id, body.recording_id)
    if authority.get("available") and authority.get("state") == "recording_not_found":
        raise HTTPException(
            404,
            {"code": "recording_not_found", "message": "Chat 2 has no recording with this ID for the creator/session."},
        )
    if authority.get("available") and authority.get("state") == "session_unavailable":
        raise HTTPException(
            409,
            {"code": "recording_asset_processing", "message": "Authoritative recording session state is unavailable."},
        )

    authoritative_recording = authority.get("recording") if isinstance(authority.get("recording"), dict) else None
    authoritative_asset_id = authoritative_recording.get("asset_id") if authoritative_recording else None
    if body.asset_id and authoritative_asset_id and body.asset_id != authoritative_asset_id:
        raise HTTPException(
            409,
            {"code": "return_asset_mismatch", "message": "Client asset ID does not match the authoritative recording asset."},
        )

    effective = body.model_copy(
        update={
            "processing_state": _processing_state(authority, body.processing_state),
            "asset_id": authoritative_asset_id if authority.get("available") else body.asset_id,
        }
    )
    result = cl.import_return(project_name, effective, request)
    result["recording_authority"] = {
        "source": "chat2" if authority.get("available") else "compatibility_pending",
        "state": authority.get("state"),
        "recording": authoritative_recording,
    }
    if not authority.get("available"):
        result["imported"] = False
        result["truth_note"] = (
            "Chat 2 recording authority is not merged in this branch. Provenance may be retained, but no returned media is treated as authoritatively ready."
        )
    else:
        result["truth_note"] = (
            "Returned recording state and asset ID were reconciled against Chat 2. Binary materialisation still requires the canonical tenant-safe media resolver."
        )
    return result


def _signature(route: Any) -> tuple[str, frozenset[str]] | None:
    path = getattr(route, "path", None)
    methods = getattr(route, "methods", None)
    if not isinstance(path, str) or not methods:
        return None
    return path, frozenset(str(method).upper() for method in methods)


def install_creation_live_authority_routes(app: Any) -> None:
    """Mount guarded consequential routes before the base Chat 7 router, regardless of call order."""
    authority_modules = {__name__}
    kept = []
    found_authority = set()
    for route in app.router.routes:
        signature = _signature(route)
        if signature not in _AUTHORITY_SIGNATURES:
            kept.append(route)
            continue
        endpoint = getattr(route, "endpoint", None)
        if getattr(endpoint, "__module__", None) in authority_modules:
            kept.append(route)
            found_authority.add(signature)
        # Base/older copies of these exact consequential routes are intentionally replaced.
    if len(kept) != len(app.router.routes):
        app.router.routes[:] = kept
    if found_authority != _AUTHORITY_SIGNATURES:
        app.include_router(router)
    app.state.creation_live_authority_routes_installed = True


__all__ = [
    "authoritative_attach",
    "authoritative_marker",
    "authoritative_return",
    "install_creation_live_authority_routes",
    "router",
]
