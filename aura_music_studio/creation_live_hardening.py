from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import HTTPException

_IN_PROGRESS = json.dumps({"_creation_live_state": "in_progress"}, separators=(",", ":"))
_TERMINAL_BROADCAST_STATES = {"ended", "failed", "cancelled", "canceled"}
_ACTIVE_BROADCAST_STATES = {"starting", "live", "degraded", "reconnecting", "stopping"}
_PATCHED = False
_ORIGINAL_GET: Callable[..., dict] | None = None
_ORIGINAL_MUTATE: Callable[..., dict] | None = None
_ORIGINAL_IDEMPOTENT: Callable[..., Any] | None = None
_ORIGINAL_DISCOVER: Callable[..., list[dict]] | None = None
_ORIGINAL_API_ERROR: Callable[[Exception], Any] | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _request_hash(request: dict[str, Any]) -> str:
    payload = json.dumps(request, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _revoke_item(store: Any, item: dict[str, Any]) -> dict[str, Any]:
    if item.get("source_status") in {"revoked", "detached"}:
        return item
    descriptor = dict(item["descriptor"])
    version = int(item["version"]) + 1
    stamp = _now()
    descriptor.update(
        {
            "version": version,
            "updated_at": stamp,
            "live_source_registration_state": "revoked",
            "health": "revoked",
            "revoked_at": stamp,
        }
    )
    with store.connect() as con:
        result = con.execute(
            """
            UPDATE creation_live_sources
               SET descriptor_json=?,source_status='revoked',active_editor_instance_id=NULL,
                   version=?,updated_at=?,revoked_at=?
             WHERE source_adapter_id=? AND user_id=? AND version=?
            """,
            (
                json.dumps(descriptor, separators=(",", ":")),
                version,
                stamp,
                stamp,
                item["source_adapter_id"],
                item["user_id"],
                item["version"],
            ),
        )
    if result.rowcount != 1:
        assert _ORIGINAL_GET is not None
        return _ORIGINAL_GET(store, item["user_id"], item["source_adapter_id"])
    assert _ORIGINAL_GET is not None
    return _ORIGINAL_GET(store, item["user_id"], item["source_adapter_id"])


def hardened_get(store: Any, user_id: str, source_adapter_id: str) -> dict[str, Any]:
    """Return canonical source state after fail-closed expiry/session reconciliation.

    Discovery handles expire normally. Once a source is attached to an *active* authoritative
    Shared Sky session, that session becomes the lease: a long legitimate broadcast must not be
    cut off merely because the original discovery TTL elapsed. Draft/configuring sessions do not
    bypass expiry, and missing/terminal sessions revoke immediately.
    """
    assert _ORIGINAL_GET is not None
    item = _ORIGINAL_GET(store, user_id, source_adapter_id)
    if item.get("source_status") in {"revoked", "detached"}:
        return item

    broadcast_id = str(item.get("broadcast_id") or "").strip()
    if broadcast_id:
        from .creation_live import shared_sky

        try:
            broadcast = shared_sky.broadcast(user_id, broadcast_id)
        except KeyError:
            return _revoke_item(store, item)
        state = str(broadcast.get("state") or "").lower()
        if state in _TERMINAL_BROADCAST_STATES:
            return _revoke_item(store, item)
        if state in _ACTIVE_BROADCAST_STATES:
            return item

    expires_at = _parse_timestamp((item.get("descriptor") or {}).get("expires_at"))
    if expires_at is not None and expires_at <= datetime.now(timezone.utc):
        return _revoke_item(store, item)
    return item


def hardened_mutate(
    store: Any,
    user_id: str,
    source_adapter_id: str,
    *,
    expected_version: int | None,
    editor_instance_id: str | None,
    **changes: Any,
) -> dict[str, Any]:
    """Prevent a stale/revoked source handle from being silently reactivated."""
    assert _ORIGINAL_MUTATE is not None
    item = hardened_get(store, user_id, source_adapter_id)
    requested_status = str(changes.get("source_status") or item.get("source_status") or "")
    if item.get("source_status") == "revoked" and requested_status not in {"revoked", "detached"}:
        raise RuntimeError("source_revoked")
    return _ORIGINAL_MUTATE(
        store,
        user_id,
        source_adapter_id,
        expected_version=expected_version,
        editor_instance_id=editor_instance_id,
        **changes,
    )


def hardened_idempotent(
    store: Any,
    user_id: str,
    operation: str,
    key: str,
    source_adapter_id: str,
    request: dict[str, Any],
    execute: Callable[[], Any],
):
    """Reserve an idempotency key before side effects so concurrent attaches cannot duplicate work."""
    request_hash = _request_hash(request)

    with store.connect() as con:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute(
            """
            SELECT request_hash,response_json
              FROM creation_live_idempotency
             WHERE user_id=? AND operation=? AND idempotency_key=?
            """,
            (user_id, operation, key),
        ).fetchone()
        if row:
            if row["request_hash"] != request_hash:
                raise RuntimeError("idempotency_key_reused_with_different_request")
            if row["response_json"] == _IN_PROGRESS:
                raise RuntimeError("operation_in_progress")
            return json.loads(row["response_json"])
        con.execute(
            "INSERT INTO creation_live_idempotency VALUES(?,?,?,?,?,?,?)",
            (user_id, operation, key, source_adapter_id, request_hash, _IN_PROGRESS, _now()),
        )

    try:
        result = execute()
    except Exception:
        with store.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            con.execute(
                """
                DELETE FROM creation_live_idempotency
                 WHERE user_id=? AND operation=? AND idempotency_key=?
                   AND request_hash=? AND response_json=?
                """,
                (user_id, operation, key, request_hash, _IN_PROGRESS),
            )
        raise

    encoded = json.dumps(result, separators=(",", ":"))
    with store.connect() as con:
        con.execute("BEGIN IMMEDIATE")
        updated = con.execute(
            """
            UPDATE creation_live_idempotency
               SET response_json=?
             WHERE user_id=? AND operation=? AND idempotency_key=?
               AND request_hash=? AND response_json=?
            """,
            (encoded, user_id, operation, key, request_hash, _IN_PROGRESS),
        )
        if updated.rowcount != 1:
            row = con.execute(
                """
                SELECT request_hash,response_json
                  FROM creation_live_idempotency
                 WHERE user_id=? AND operation=? AND idempotency_key=?
                """,
                (user_id, operation, key),
            ).fetchone()
            if row and row["request_hash"] == request_hash and row["response_json"] != _IN_PROGRESS:
                return json.loads(row["response_json"])
            raise RuntimeError("idempotency_conflict")
    return result


def _revive_rediscovered_sources(
    store: Any,
    user_id: str,
    project_name: str,
    studio_type: str,
    descriptors: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Reconcile the durable registry with the current allow-list and rights state.

    Sources that disappear or become rights/privacy blocked are revoked fail-closed. A previously
    revoked source is only reissued after a fresh discovery proves it exists and is no longer
    blocked; stale Shared Sky/editor linkage is removed during reissue.
    """
    by_id = {str(item.get("source_adapter_id")): item for item in descriptors}
    active_ids = set(by_id)
    stamp = _now()

    with store.connect() as con:
        rows = con.execute(
            """
            SELECT * FROM creation_live_sources
             WHERE user_id=? AND project_name=? AND studio_type=?
            """,
            (user_id, project_name, studio_type),
        ).fetchall()
        for row in rows:
            item = dict(row)
            source_id = item["source_adapter_id"]
            status = item["source_status"]
            if source_id not in active_ids:
                if status not in {"detached", "revoked"}:
                    descriptor = json.loads(item["descriptor_json"])
                    descriptor.update(
                        {
                            "version": int(item["version"]) + 1,
                            "updated_at": stamp,
                            "live_source_registration_state": "revoked",
                            "health": "revoked",
                            "revoked_at": stamp,
                        }
                    )
                    con.execute(
                        """
                        UPDATE creation_live_sources
                           SET descriptor_json=?,source_status='revoked',active_editor_instance_id=NULL,
                               version=version+1,updated_at=?,revoked_at=?
                         WHERE source_adapter_id=? AND user_id=?
                        """,
                        (
                            json.dumps(descriptor, separators=(",", ":")),
                            stamp,
                            stamp,
                            source_id,
                            user_id,
                        ),
                    )
                continue

            current = by_id[source_id]
            rights_state = str((current.get("rights") or {}).get("state") or "unknown")
            if rights_state == "blocked" and status not in {"detached", "revoked"}:
                descriptor = dict(current)
                descriptor.update(
                    {
                        "version": int(item["version"]) + 1,
                        "updated_at": stamp,
                        "live_source_registration_state": "revoked",
                        "health": "revoked",
                        "revoked_at": stamp,
                    }
                )
                con.execute(
                    """
                    UPDATE creation_live_sources
                       SET descriptor_json=?,source_status='revoked',active_editor_instance_id=NULL,
                           version=version+1,updated_at=?,revoked_at=?
                     WHERE source_adapter_id=? AND user_id=?
                    """,
                    (json.dumps(descriptor, separators=(",", ":")), stamp, stamp, source_id, user_id),
                )
                continue

            if status == "revoked" and rights_state != "blocked":
                descriptor = dict(current)
                descriptor.update(
                    {
                        "version": int(item["version"]) + 1,
                        "updated_at": stamp,
                        "live_source_registration_state": "discovered",
                        "health": "available",
                        "shared_sky_project_id": None,
                        "shared_sky_broadcast_id": None,
                        "shared_sky_source_id": None,
                        "revoked_at": None,
                    }
                )
                con.execute(
                    """
                    UPDATE creation_live_sources
                       SET descriptor_json=?,source_status='discovered',shared_sky_project_id=NULL,
                           broadcast_id=NULL,transport_source_id=NULL,active_editor_instance_id=NULL,
                           version=version+1,updated_at=?,revoked_at=NULL
                     WHERE source_adapter_id=? AND user_id=?
                    """,
                    (json.dumps(descriptor, separators=(",", ":")), stamp, source_id, user_id),
                )

    refreshed: list[dict[str, Any]] = []
    for source_id in active_ids:
        try:
            refreshed.append(hardened_get(store, user_id, source_id)["descriptor"])
        except KeyError:
            continue
    order = {str(item.get("source_adapter_id")): index for index, item in enumerate(descriptors)}
    refreshed.sort(key=lambda item: order.get(str(item.get("source_adapter_id")), len(order)))
    return refreshed


def hardened_discover_sources(user_id: str, project_name: str, studio_type: str) -> list[dict[str, Any]]:
    """Reconcile durable source rows with the project's current allow-listed source set."""
    assert _ORIGINAL_DISCOVER is not None
    from .creation_live import creation_live_store

    descriptors = _ORIGINAL_DISCOVER(user_id, project_name, studio_type)
    return _revive_rediscovered_sources(
        creation_live_store,
        user_id,
        project_name,
        studio_type,
        descriptors,
    )


def hardened_api_error(exc: Exception):
    code = str(exc)
    if code == "operation_in_progress":
        raise HTTPException(
            409,
            {
                "code": code,
                "message": "An identical live-source operation is already in progress. Refresh source state before retrying.",
            },
        ) from exc
    if code == "source_revoked":
        raise HTTPException(
            409,
            {
                "code": code,
                "message": "This live-source handle has expired or was revoked. Rediscover the current project source before attaching again.",
            },
        ) from exc
    assert _ORIGINAL_API_ERROR is not None
    return _ORIGINAL_API_ERROR(exc)


def _harden_ui(script: str) -> str:
    capture_old = "try{state.previewStream=await navigator.mediaDevices.getDisplayMedia({video:true,audio:false});const v=document.createElement('video');"
    capture_new = (
        "try{if(state.previewStream){state.previewStream.getTracks().forEach(t=>t.stop());state.previewStream=null;}"
        "state.previewStream=await navigator.mediaDevices.getDisplayMedia({video:true,audio:false});"
        "const previewStream=state.previewStream;previewStream.getTracks().forEach(t=>{t.onended=()=>{"
        "if(state.previewStream===previewStream){state.previewStream=null;box.replaceChildren();"
        "msg('Workspace preview ended. It is no longer available for attachment.',true);}}});"
        "const v=document.createElement('video');"
    )
    script = script.replace(capture_old, capture_new)

    preview_old = "async function preview(){const s=state.selected;if(!s)return msg('Select a source first.',true);const box=$('clPreview');box.replaceChildren();"
    preview_new = preview_old + "if(state.previewStream){state.previewStream.getTracks().forEach(t=>t.stop());state.previewStream=null;}"
    script = script.replace(preview_old, preview_new)

    source_change_old = "state.selected=state.sources.find(s=>s.source_adapter_id===x.value);$('clWorkspaceRow').style.display="
    source_change_new = "state.selected=state.sources.find(s=>s.source_adapter_id===x.value);if(state.previewStream){state.previewStream.getTracks().forEach(t=>t.stop());state.previewStream=null;const p=$('clPreview');if(p)p.replaceChildren();}$('clWorkspaceRow').style.display="
    script = script.replace(source_change_old, source_change_new)

    attach_old = (
        "state.selected=data.source;state.status=data;renderStatus();"
        "msg(data.transport?.available?'Source registered with Shared Sky transport.':"
        "'Source safely prepared; Chat 2 transport registry is pending merge. No LIVE success is being claimed.')"
    )
    attach_new = (
        "state.selected=data.source;state.status=data;renderStatus();"
        "const pf=data.transport_preflight||{};const blockers=Array.isArray(pf.blocking_errors)?pf.blocking_errors:[];"
        "if(pf.state==='broadcast_not_selected'){msg('Source registered with Shared Sky transport. Select a broadcast to run transport preflight. Programme remains NOT CONFIRMED ON AIR.')}"
        "else if(pf.available&&pf.ready===true){msg('Source registered. Transport preflight is ready. Programme remains NOT CONFIRMED ON AIR until Shared Sky control-room authority confirms this exact source.')}"
        "else if(pf.available&&pf.ready===false){const reasons=blockers.slice(0,5).map(x=>String(x.code||x.message||'transport_blocked')).join(', ');msg('Source registered, but transport is not ready'+(reasons?': '+reasons:'')+'. Programme is NOT CONFIRMED ON AIR.',true)}"
        "else{msg('Source is safely registered/prepared, but transport readiness is unavailable. No LIVE or ON-AIR success is being claimed.',true)}"
    )
    script = script.replace(attach_old, attach_new)

    from .creation_live_ui_community import harden_community_ui

    return harden_community_ui(script)


def install_creation_live_hardening() -> None:
    """Install additive Chat 7 lifecycle hardening exactly once per Python process."""
    global _PATCHED, _ORIGINAL_GET, _ORIGINAL_MUTATE, _ORIGINAL_IDEMPOTENT
    global _ORIGINAL_DISCOVER, _ORIGINAL_API_ERROR
    if _PATCHED:
        return

    from . import creation_live as cl

    _ORIGINAL_GET = cl.CreationLiveStore.get
    _ORIGINAL_MUTATE = cl.CreationLiveStore.mutate
    _ORIGINAL_IDEMPOTENT = cl.CreationLiveStore.idempotent
    _ORIGINAL_DISCOVER = cl.discover_sources
    _ORIGINAL_API_ERROR = cl._api_error

    cl.CreationLiveStore.get = hardened_get
    cl.CreationLiveStore.mutate = hardened_mutate
    cl.CreationLiveStore.idempotent = hardened_idempotent
    cl.discover_sources = hardened_discover_sources
    cl._api_error = hardened_api_error
    cl.LIVE_UI_SCRIPT = _harden_ui(cl.LIVE_UI_SCRIPT)
    cl.creation_live_hardening_installed = True
    cl.creation_live_community_ui_installed = "Shared Sky community" in cl.LIVE_UI_SCRIPT
    _PATCHED = True


__all__ = [
    "hardened_api_error",
    "hardened_discover_sources",
    "hardened_get",
    "hardened_idempotent",
    "hardened_mutate",
    "install_creation_live_hardening",
]
