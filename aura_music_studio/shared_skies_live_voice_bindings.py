from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .esp_niche import require_esp_hub_member
from .rights import RightsLedger, VoiceProfile, authorize_voice_profile
from .shared_sky_control_room import StudioConflict, StudioInvariantError, studio, studio_repo, utc_now
from .shared_skies_live_voice import project_voice_source
from .shared_skies_live_voice_profiles import VoicePurpose, _tenant_rights_root

router = APIRouter(tags=["Shared Skies LIVE Voice Bindings"])


class LiveVoiceBindingUpsert(BaseModel):
    chat2_project_name: str = Field(min_length=1, max_length=120)
    profile_id: str = Field(min_length=1, max_length=128)
    purpose: VoicePurpose = "speech"
    expected_binding_version: int | None = Field(default=None, ge=1)


class LiveVoiceBindingStore:
    """Persist only Chat 5 LIVE source→profile references, never Voice Profile/model data."""

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path, timeout=15)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA busy_timeout=5000")
        return con

    def _init_schema(self) -> None:
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS shared_sky_live_voice_bindings (
                    session_id TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    chat2_project_name TEXT NOT NULL,
                    profile_id TEXT NOT NULL,
                    purpose TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(session_id, source_id),
                    FOREIGN KEY(session_id) REFERENCES shared_sky_studio_sessions(id) ON DELETE CASCADE,
                    FOREIGN KEY(source_id) REFERENCES shared_sky_sources(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_shared_sky_live_voice_bindings_user_session
                ON shared_sky_live_voice_bindings(user_id, session_id, source_id);
                """
            )

    @staticmethod
    def _public(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        item = dict(row)
        return {
            "session_id": str(item["session_id"]),
            "source_id": str(item["source_id"]),
            "chat2_project_name": str(item["chat2_project_name"]),
            "profile_id": str(item["profile_id"]),
            "purpose": str(item["purpose"]),
            "version": int(item["version"]),
            "created_at": str(item["created_at"]),
            "updated_at": str(item["updated_at"]),
            "binding_authority": "Chat 5 server-authoritative LIVE source/profile reference",
            "profile_authority": "Chat 2 Voice House / RightsLedger",
            "profile_data_copied_into_chat5": False,
            "processor_runtime_attached": False,
            "real_time_processing_proven": False,
            "entitlement_evaluated_by_chat5": False,
            "client_entitlement_authority": False,
            "final_execution_reauthorisation_required": True,
        }

    def get(self, user_id: str, session_id: str, source_id: str) -> dict[str, Any]:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM shared_sky_live_voice_bindings WHERE user_id=? AND session_id=? AND source_id=?",
                (user_id, session_id, source_id),
            ).fetchone()
        if not row:
            raise KeyError(source_id)
        return self._public(row)

    def list(self, user_id: str, session_id: str) -> list[dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT * FROM shared_sky_live_voice_bindings WHERE user_id=? AND session_id=? ORDER BY source_id",
                (user_id, session_id),
            ).fetchall()
        return [self._public(row) for row in rows]

    def bind(
        self,
        user_id: str,
        session_id: str,
        source_id: str,
        chat2_project_name: str,
        profile_id: str,
        purpose: VoicePurpose,
        expected_version: int | None,
    ) -> dict[str, Any]:
        now = utc_now()
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            current = con.execute(
                "SELECT version FROM shared_sky_live_voice_bindings WHERE user_id=? AND session_id=? AND source_id=?",
                (user_id, session_id, source_id),
            ).fetchone()
            if current:
                current_version = int(current["version"])
                if expected_version is None or current_version != expected_version:
                    raise StudioConflict(
                        f"LIVE voice binding version conflict: expected {expected_version}, current {current_version}"
                    )
                cursor = con.execute(
                    """
                    UPDATE shared_sky_live_voice_bindings
                    SET chat2_project_name=?,profile_id=?,purpose=?,version=version+1,updated_at=?
                    WHERE user_id=? AND session_id=? AND source_id=? AND version=?
                    """,
                    (
                        chat2_project_name,
                        profile_id,
                        purpose,
                        now,
                        user_id,
                        session_id,
                        source_id,
                        expected_version,
                    ),
                )
                if cursor.rowcount != 1:
                    raise StudioConflict("LIVE voice binding changed in another operator session")
            else:
                if expected_version is not None:
                    raise StudioConflict("LIVE voice binding does not exist at the requested version")
                con.execute(
                    """
                    INSERT INTO shared_sky_live_voice_bindings(
                        session_id,source_id,user_id,chat2_project_name,profile_id,purpose,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?)
                    """,
                    (session_id, source_id, user_id, chat2_project_name, profile_id, purpose, now, now),
                )
        return self.get(user_id, session_id, source_id)

    def unbind(self, user_id: str, session_id: str, source_id: str, expected_version: int) -> dict[str, Any]:
        current = self.get(user_id, session_id, source_id)
        if int(current["version"]) != expected_version:
            raise StudioConflict(
                f"LIVE voice binding version conflict: expected {expected_version}, current {current['version']}"
            )
        with self._connect() as con:
            cursor = con.execute(
                "DELETE FROM shared_sky_live_voice_bindings WHERE user_id=? AND session_id=? AND source_id=? AND version=?",
                (user_id, session_id, source_id, expected_version),
            )
            if cursor.rowcount != 1:
                raise StudioConflict("LIVE voice binding changed in another operator session")
        return current


binding_store = LiveVoiceBindingStore(studio_repo.db_path)


def _owned_voice_source(user_id: str, session_id: str, source_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    session = studio_repo.get_session(user_id, session_id)
    source = studio.graph.source(user_id, source_id)
    if str(source.get("project_id") or "") != str(session.get("project_id") or ""):
        raise StudioInvariantError("Voice source does not belong to this Studio session project")
    if project_voice_source(source, "preview") is None:
        raise StudioInvariantError("Only an audio-bearing Shared Skies source can hold a LIVE voice binding")
    return session, source


def _authorised_profile_candidate(
    user_id: str,
    chat2_project_name: str,
    profile_id: str,
    purpose: VoicePurpose,
) -> VoiceProfile:
    rights_root = _tenant_rights_root(user_id, chat2_project_name)
    ledger = RightsLedger(rights_root)
    try:
        profile = ledger.get_voice(profile_id)
    except KeyError as exc:
        raise PermissionError("Voice Profile is unavailable and cannot be bound to LIVE.") from exc
    profile.assert_tenant(user_id)
    profile.assert_usable(purpose)
    return profile


def _binding_projection(user_id: str, binding: dict[str, Any]) -> dict[str, Any]:
    current_authorisation = "authorised"
    current_profile_name: str | None = None
    try:
        profile = _authorised_profile_candidate(
            user_id,
            str(binding["chat2_project_name"]),
            str(binding["profile_id"]),
            str(binding["purpose"]),  # type: ignore[arg-type]
        )
        current_profile_name = profile.name
    except (FileNotFoundError, PermissionError, ValueError):
        current_authorisation = "invalidated_or_unavailable"
    return {
        **binding,
        "profile_name": current_profile_name,
        "current_chat2_authorisation": current_authorisation,
        "binding_state": "authorised_reference_only" if current_authorisation == "authorised" else "invalidated",
        "processor_activation_allowed_by_binding_alone": False,
        "commercial_entitlement_state": "not_evaluated_chat6_authority",
    }


def bind_live_voice_profile(
    user_id: str,
    session_id: str,
    source_id: str,
    body: LiveVoiceBindingUpsert,
) -> dict[str, Any]:
    session, _source = _owned_voice_source(user_id, session_id, source_id)
    _authorised_profile_candidate(user_id, body.chat2_project_name, body.profile_id, body.purpose)
    binding = binding_store.bind(
        user_id,
        session_id,
        source_id,
        body.chat2_project_name,
        body.profile_id,
        body.purpose,
        body.expected_binding_version,
    )
    studio.graph.event(
        user_id,
        session.get("broadcast_id"),
        "studio_live_voice_profile_bound",
        {
            "session_id": session_id,
            "source_id": source_id,
            "profile_id": body.profile_id,
            "purpose": body.purpose,
            "binding_version": binding["version"],
        },
    )
    return _binding_projection(user_id, binding)


def live_voice_bindings(user_id: str, session_id: str) -> list[dict[str, Any]]:
    studio_repo.get_session(user_id, session_id)
    return [_binding_projection(user_id, item) for item in binding_store.list(user_id, session_id)]


def unbind_live_voice_profile(user_id: str, session_id: str, source_id: str, expected_version: int) -> dict[str, Any]:
    session, _source = _owned_voice_source(user_id, session_id, source_id)
    removed = binding_store.unbind(user_id, session_id, source_id, expected_version)
    studio.graph.event(
        user_id,
        session.get("broadcast_id"),
        "studio_live_voice_profile_unbound",
        {
            "session_id": session_id,
            "source_id": source_id,
            "profile_id": removed["profile_id"],
            "purpose": removed["purpose"],
            "binding_version": removed["version"],
        },
    )
    return removed


def authorize_live_voice_binding_for_execution(user_id: str, session_id: str, source_id: str) -> VoiceProfile:
    """Re-authorise the bound Chat 2 profile at the final voice-rights execution boundary.

    This helper deliberately does not check Chat 6 entitlement and does not attach/execute a processor.
    A future executable LIVE processor must call this immediately before processing and separately
    satisfy the applicable server-authoritative Chat 6 entitlement contract.
    """

    _owned_voice_source(user_id, session_id, source_id)
    binding = binding_store.get(user_id, session_id, source_id)
    rights_root = _tenant_rights_root(user_id, str(binding["chat2_project_name"]))
    return authorize_voice_profile(
        rights_root,
        str(binding["profile_id"]),
        str(binding["purpose"]),
    )


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, KeyError):
        return HTTPException(404, "Shared Skies LIVE voice binding, session or source was not found")
    if isinstance(exc, FileNotFoundError):
        return HTTPException(404, "Chat 2 Voice House project was not found")
    if isinstance(exc, PermissionError):
        return HTTPException(403, str(exc))
    if isinstance(exc, StudioConflict):
        return HTTPException(409, str(exc))
    if isinstance(exc, (StudioInvariantError, ValueError)):
        return HTTPException(400, str(exc))
    return HTTPException(400, "Shared Skies LIVE voice binding operation could not be completed")


@router.get("/shared-sky/studio/api/sessions/{session_id}/voice/bindings")
def get_live_voice_bindings(session_id: str, request: Request):
    member, _membership = require_esp_hub_member(request)
    try:
        return {
            "product": "Shared Skies Streaming Studios LIVE Voice",
            "session_id": session_id,
            "bindings": live_voice_bindings(str(member.user_id), session_id),
            "contract": {
                "binding_authority": "Chat 5 server-authoritative source/profile references",
                "profile_authority": "Chat 2",
                "processor_runtime_attached": False,
                "real_time_processing_proven": False,
                "final_execution_reauthorisation_required": True,
                "entitlement_authority": "Chat 6",
                "entitlement_evaluated_by_chat5": False,
            },
        }
    except Exception as exc:
        raise _http_error(exc) from exc


@router.put("/shared-sky/studio/api/sessions/{session_id}/voice/bindings/{source_id}")
def put_live_voice_binding(session_id: str, source_id: str, body: LiveVoiceBindingUpsert, request: Request):
    member, _membership = require_esp_hub_member(request)
    try:
        return bind_live_voice_profile(str(member.user_id), session_id, source_id, body)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.delete("/shared-sky/studio/api/sessions/{session_id}/voice/bindings/{source_id}")
def delete_live_voice_binding(session_id: str, source_id: str, expected_binding_version: int, request: Request):
    member, _membership = require_esp_hub_member(request)
    try:
        return {
            "removed": unbind_live_voice_profile(
                str(member.user_id), session_id, source_id, expected_binding_version
            ),
            "processor_stopped": False,
            "reason": "Binding references are not executable processors.",
        }
    except Exception as exc:
        raise _http_error(exc) from exc


def install_shared_skies_live_voice_bindings(app: Any) -> None:
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
    "LiveVoiceBindingStore",
    "LiveVoiceBindingUpsert",
    "authorize_live_voice_binding_for_execution",
    "bind_live_voice_profile",
    "binding_store",
    "delete_live_voice_binding",
    "get_live_voice_bindings",
    "install_shared_skies_live_voice_bindings",
    "live_voice_bindings",
    "put_live_voice_binding",
    "router",
    "unbind_live_voice_profile",
]
