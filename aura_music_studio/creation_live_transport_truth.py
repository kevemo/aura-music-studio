from __future__ import annotations

from typing import Any

from fastapi import Request

from . import creation_live as cl


_PATCHED = False
_ISSUE_FIELDS = ("code", "scope", "destination_id", "message")
_ACTIVE_TRANSPORT_STATES = {"starting", "live", "degraded", "reconnecting", "stopping"}


def _safe_text(value: Any, *, limit: int) -> str:
    clean = " ".join(str(value or "").replace("\x00", " ").split()).strip()
    return clean[:limit]


def _safe_issue(value: Any) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    safe: dict[str, str] = {}
    for field in _ISSUE_FIELDS:
        if field not in value or value.get(field) in (None, ""):
            continue
        limit = 500 if field == "message" else 160
        safe[field] = _safe_text(value.get(field), limit=limit)
    return safe or None


def _safe_issues(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    safe: list[dict[str, str]] = []
    for item in value[:50]:
        projected = _safe_issue(item)
        if projected:
            safe.append(projected)
    return safe


def safe_preflight_payload(result: Any, *, available: bool = True) -> dict[str, Any]:
    """Project Chat 2 preflight into the browser-safe Chat 7 contract.

    Chat 2 may add provider/debug fields over time.  Chat 7 deliberately exposes only the
    readiness boolean, bounded safe blocker/warning fields and a correlation ID.  Trace IDs,
    destination provider payloads, endpoints, credentials and future nested fields are never
    forwarded implicitly.
    """
    if not isinstance(result, dict):
        return {
            "available": available,
            "ready": None,
            "state": "preflight_unavailable" if available else "compatibility_pending",
            "blocking_errors": [],
            "warnings": [],
        }
    raw_ready = result.get("ready")
    ready = raw_ready if isinstance(raw_ready, bool) else None
    state = "ready" if ready is True else "blocked" if ready is False else "not_validated"
    correlation = _safe_text(result.get("correlation_id"), limit=160)
    payload: dict[str, Any] = {
        "available": available,
        "ready": ready,
        "state": state,
        "blocking_errors": _safe_issues(result.get("blocking_errors")),
        "warnings": _safe_issues(result.get("warnings")),
    }
    if correlation:
        payload["correlation_id"] = correlation
    return payload


def safe_chat2_preflight(user_id: str, broadcast_id: str) -> dict[str, Any]:
    """Run Chat 2's canonical attach-time preflight and return only the safe projection."""
    try:
        from .shared_sky_transport_domain import transport
    except ImportError:
        return safe_preflight_payload(None, available=False)
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
    return safe_preflight_payload(result)


def _safe_status_snapshot(
    status: Any,
    *,
    registered_source_id: str | None,
) -> dict[str, Any]:
    if not isinstance(status, dict):
        return {
            "available": True,
            "ready": None,
            "state": "status_unavailable",
            "blocking_errors": [],
            "warnings": [],
            "transport_state": "unknown",
            "registered_source": bool(registered_source_id),
            "transport_source_selected": False,
            "transport_uses_this_source": False,
        }
    session = status.get("session") if isinstance(status.get("session"), dict) else {}
    validation = session.get("validation") if isinstance(session.get("validation"), dict) else {}
    if validation:
        result = safe_preflight_payload(validation)
        result["snapshot"] = "last_validation"
    else:
        result = {
            "available": True,
            "ready": None,
            "state": "not_validated",
            "blocking_errors": [],
            "warnings": [],
            "snapshot": "transport_status",
        }
    transport_state = _safe_text(session.get("state"), limit=80) or "unknown"
    health_state = _safe_text(session.get("health_state"), limit=80)
    reason = _safe_text(session.get("last_reason_code"), limit=80)
    selected_source = _safe_text(session.get("source_id"), limit=180)
    registered = _safe_text(registered_source_id, limit=180)
    result.update(
        {
            "transport_state": transport_state,
            "registered_source": bool(registered),
            "transport_source_selected": bool(selected_source),
            "transport_uses_this_source": bool(registered and selected_source and registered == selected_source),
            "active_transport_session": transport_state in _ACTIVE_TRANSPORT_STATES,
        }
    )
    if health_state:
        result["health_state"] = health_state
    if reason:
        result["last_reason_code"] = reason
    return result


def safe_chat2_status(
    user_id: str,
    broadcast_id: str,
    *,
    registered_source_id: str | None,
) -> dict[str, Any]:
    """Read persisted Chat 2 validation/session truth without rerunning preflight from GET."""
    try:
        from .shared_sky_transport_domain import transport
    except ImportError:
        payload = safe_preflight_payload(None, available=False)
        payload.update(
            {
                "transport_state": "compatibility_pending",
                "registered_source": bool(registered_source_id),
                "transport_source_selected": False,
                "transport_uses_this_source": False,
                "active_transport_session": False,
            }
        )
        return payload
    try:
        status = transport.status(user_id, broadcast_id)
    except (KeyError, ValueError, RuntimeError):
        return {
            "available": True,
            "ready": None,
            "state": "status_unavailable",
            "blocking_errors": [],
            "warnings": [],
            "transport_state": "unavailable",
            "registered_source": bool(registered_source_id),
            "transport_source_selected": False,
            "transport_uses_this_source": False,
            "active_transport_session": False,
        }
    return _safe_status_snapshot(status, registered_source_id=registered_source_id)


def authoritative_source_status(project_name: str, source_adapter_id: str, request: Request):
    """Return source registration, persisted transport truth and Programme truth separately."""
    result = cl.source_status(project_name, source_adapter_id, request)
    user_id = cl._user_id(cl._member(request))
    try:
        item = cl.creation_live_store.get(user_id, source_adapter_id)
    except KeyError:
        return result
    broadcast_id = _safe_text(item.get("broadcast_id"), limit=180)
    if broadcast_id:
        transport = safe_chat2_status(
            user_id,
            broadcast_id,
            registered_source_id=item.get("transport_source_id"),
        )
    else:
        transport = {
            "available": False,
            "ready": None,
            "state": "broadcast_not_selected",
            "blocking_errors": [],
            "warnings": [],
            "transport_state": "not_selected",
            "registered_source": bool(item.get("transport_source_id")),
            "transport_source_selected": False,
            "transport_uses_this_source": False,
            "active_transport_session": False,
        }
    result["transport_preflight"] = transport
    result["readiness_truth"] = {
        "source_registration": str(item.get("source_status") or "not_registered"),
        "transport": transport.get("state"),
        "programme": (result.get("authoritative_live") or {}).get("programme_state", "unknown"),
        "on_air": bool((result.get("authoritative_live") or {}).get("on_air")),
    }
    result["truth_note"] = (
        "Registration, transport validation and exact-source Programme state are separate. "
        "Transport readiness or an active Shared Sky session does not prove this project source is ON AIR."
    )
    return result


def _harden_ui(script: str) -> str:
    if "Transport preflight:" in script:
        return script
    old = (
        "function renderStatus(){const box=$('clStatus');if(!box)return;const s=state.selected,live=state.status?.authoritative_live;"
        "if(!s){box.textContent='Select a source.';return}box.innerHTML=`<b>${esc(s.safe_display_name)}</b>"
        "<div>Rights: ${esc(s.rights?.state||'unknown')} · Source: ${esc(state.status?.source_status||s.live_source_registration_state||'not registered')}</div>"
        "<div>Session: ${esc(live?.session_state||'not selected')} · Programme: ${esc(live?.programme_state||'unknown')} · <b>${live?.on_air?'ON AIR':'NOT CONFIRMED ON AIR'}</b></div>"
        "<small>${esc((s.rights?.messages||[]).join(' '))}</small>`}"
    )
    new = (
        "function renderStatus(){const box=$('clStatus');if(!box)return;const s=state.selected,live=state.status?.authoritative_live,pf=state.status?.transport_preflight||{};"
        "if(!s){box.textContent='Select a source.';return}const blockers=Array.isArray(pf.blocking_errors)?pf.blocking_errors:[];"
        "const blockerText=blockers.slice(0,5).map(x=>String(x.code||'transport_blocked')).join(', ');"
        "const transportState=pf.state||((state.status?.source_status||s.live_source_registration_state)?'not checked':'not selected');"
        "const sourceUse=pf.transport_uses_this_source===true?'this source selected':pf.transport_source_selected===true?'different source selected':pf.registered_source===true?'registered, not selected':'not selected';"
        "box.innerHTML=`<b>${esc(s.safe_display_name)}</b>"
        "<div>Rights: ${esc(s.rights?.state||'unknown')} · Registration: ${esc(state.status?.source_status||s.live_source_registration_state||'not registered')}</div>"
        "<div>Transport preflight: <b>${esc(transportState)}</b> · ${esc(sourceUse)}</div>"
        "${blockerText?`<div style=\"font-size:.7rem;color:#ffb2be\">Transport blockers: ${esc(blockerText)}</div>`:''}"
        "<div>Session: ${esc(live?.session_state||'not selected')} · Programme: ${esc(live?.programme_state||'unknown')} · <b>${live?.on_air?'ON AIR':'NOT CONFIRMED ON AIR'}</b></div>"
        "<small>${esc((s.rights?.messages||[]).join(' '))}</small>`}"
    )
    return script.replace(old, new)


def install_creation_live_transport_truth() -> None:
    """Install browser-safe Chat 2 truth projection without changing Chat 2/3 ownership."""
    global _PATCHED
    if _PATCHED:
        return
    from . import creation_live_authority as authority

    authority._safe_chat2_preflight = safe_chat2_preflight
    cl.LIVE_UI_SCRIPT = _harden_ui(cl.LIVE_UI_SCRIPT)
    cl.creation_live_transport_truth_installed = True
    _PATCHED = True


__all__ = [
    "authoritative_source_status",
    "install_creation_live_transport_truth",
    "safe_chat2_preflight",
    "safe_chat2_status",
    "safe_preflight_payload",
]
