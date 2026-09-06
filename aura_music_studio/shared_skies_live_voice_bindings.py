from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import tenant_storage
from .esp_niche import require_esp_hub_member
from .request_context import current_user_id
from .rights import RightsLedger, VoiceProfile
from .shared_skies_live_voice import _AUDIO_BEARING_SOURCE_TYPES
from .shared_skies_live_voice_profiles import VoicePurpose
from .shared_sky_control_room import StudioConflict, StudioInvariantError, studio, studio_repo

router = APIRouter(tags=["Shared Skies LIVE Voice Bindings"])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class LiveVoiceBindingUpsert(BaseModel):
    chat2_project_name: str = Field(min_length=1, max_length=160)
    profile_id: str = Field(min_length=1, max_length=128)
    purpose: VoicePurpose = "speech"
    expected_version: int | None = Field(default=None, ge=1)


class LiveVoiceBindingDelete(BaseModel):
    expected_version: int = Field(ge=1)


class LiveVoiceBindingStore:
    """Persist only stable Chat 5 LIVE -> Chat 2 profile references.

    This table is deliberately not a Voice Profile database. It contains no reference audio,
    embeddings, model/provider configuration, consent evidence or commercial entitlement state.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
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
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    chat2_project_name TEXT NOT NULL,
                    profile_id TEXT NOT NULL,
                    purpose TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(user_id, session_id, source_id),
                    FOREIGN KEY(session_id) REFERENCES shared_sky_studio_sessions(id) ON DELETE CASCADE,
                    FOREIGN KEY(source_id) REFERENCES shared_sky_sources(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_shared_sky_live_voice_bindings_session
                    ON shared_sky_live_voice_bindings(user_id, session_id, updated_at DESC);
                """
            )

    def bindings(self, user_id: str, session_id: str) -> list[dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT * FROM shared_sky_live_voice_bindings WHERE user_id=? AND session_id=? ORDER BY updated_at DESC, source_id",
                (user_id, session_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def binding(self, user_id: str, session_id: str, source_id: str) -> dict[str, Any]:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM shared_sky_live_voice_bindings WHERE user_id=? AND session_id=? AND source_id=?",
                (user_id, session_id, source_id),
            ).fetchone()
        if not row:
            raise KeyError(source_id)
        return dict(row)

    def upsert(
        self,
        user_id: str,
        session_id: str,
        source_id: str,
        body: LiveVoiceBindingUpsert,
    ) -> dict[str, Any]:
        now = _now()
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                "SELECT * FROM shared_sky_live_voice_bindings WHERE user_id=? AND session_id=? AND source_id=?",
                (user_id, session_id, source_id),
            ).fetchone()
            if row:
                if body.expected_version is None:
                    raise StudioConflict("expected_version is required to replace an existing LIVE voice binding")
                if int(row["version"]) != body.expected_version:
                    raise StudioConflict(
                        f"LIVE voice binding version conflict: expected {body.expected_version}, current {row['version']}"
                    )
                cursor = con.execute(
                    """
                    UPDATE shared_sky_live_voice_bindings
                    SET chat2_project_name=?,profile_id=?,purpose=?,version=version+1,updated_at=?
                    WHERE user_id=? AND session_id=? AND source_id=? AND version=?
                    """,
                    (
                        body.chat2_project_name.strip(),
                        body.profile_id.strip(),
                        body.purpose,
                        now,
                        user_id,
                        session_id,
                        source_id,
                        body.expected_version,
                    ),
                )
                if cursor.rowcount != 1:
                    raise StudioConflict("LIVE voice binding changed in another operator session")
            else:
                if body.expected_version is not None:
                    raise StudioConflict("LIVE voice binding does not exist at the expected version")
                con.execute(
                    """
                    INSERT INTO shared_sky_live_voice_bindings(
                        id,user_id,session_id,source_id,chat2_project_name,profile_id,purpose,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        uuid4().hex,
                        user_id,
                        session_id,
                        source_id,
                        body.chat2_project_name.strip(),
                        body.profile_id.strip(),
                        body.purpose,
                        now,
                        now,
                    ),
                )
        return self.binding(user_id, session_id, source_id)

    def delete(self, user_id: str, session_id: str, source_id: str, expected_version: int) -> dict[str, Any]:
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                "SELECT * FROM shared_sky_live_voice_bindings WHERE user_id=? AND session_id=? AND source_id=?",
                (user_id, session_id, source_id),
            ).fetchone()
            if not row:
                raise KeyError(source_id)
            if int(row["version"]) != expected_version:
                raise StudioConflict(
                    f"LIVE voice binding version conflict: expected {expected_version}, current {row['version']}"
                )
            cursor = con.execute(
                "DELETE FROM shared_sky_live_voice_bindings WHERE user_id=? AND session_id=? AND source_id=? AND version=?",
                (user_id, session_id, source_id, expected_version),
            )
            if cursor.rowcount != 1:
                raise StudioConflict("LIVE voice binding changed in another operator session")
        return dict(row)


class LiveVoiceBindingService:
    def __init__(self, store: LiveVoiceBindingStore):
        self.store = store

    @staticmethod
    def _rights_root(user_id: str, project_name: str) -> Path:
        active_user = current_user_id()
        if not active_user or str(active_user) != str(user_id):
            raise PermissionError("Authenticated tenant context does not match the LIVE member.")
        project = tenant_storage.project_path(project_name, must_exist=True)
        return project / ".aura_rights"

    @staticmethod
    def _source_context(user_id: str, session_id: str, source_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        session = studio_repo.get_session(user_id, session_id)
        source = studio.graph.source(user_id, source_id)
        if source.get("project_id") != session.get("project_id"):
            raise StudioInvariantError("LIVE voice source does not belong to this Studio session project")
        if str(source.get("source_type") or "") not in _AUDIO_BEARING_SOURCE_TYPES:
            raise StudioInvariantError("LIVE Voice Profile bindings require an audio-bearing source")
        return session, source

    def _authorise_reference(
        self,
        user_id: str,
        chat2_project_name: str,
        profile_id: str,
        purpose: VoicePurpose,
    ) -> VoiceProfile:
        rights_root = self._rights_root(user_id, chat2_project_name)
        ledger = RightsLedger(rights_root)
        try:
            profile = ledger.get_voice(profile_id)
        except KeyError as exc:
            raise PermissionError("Voice Profile is unavailable and cannot be bound to LIVE") from exc
        profile.assert_tenant(user_id)
        profile.assert_usable(purpose)
        return profile

    @staticmethod
    def _profile_projection(profile: VoiceProfile | None) -> dict[str, Any] | None:
        if profile is None:
            return None
        return {
            "profile_id": profile.id,
            "profile_name": profile.name,
            "owner": profile.owner_label,
            "consent_state": profile.verification_state,
            "consent_confirmed": bool(profile.consent_confirmed),
            "active": bool(profile.active),
            "available_modes": sorted({str(item) for item in profile.allowed_uses if str(item).strip()}),
        }

    def _projection(
        self,
        row: dict[str, Any],
        *,
        currently_authorised: bool,
        profile: VoiceProfile | None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        return {
            "binding_id": row["id"],
            "session_id": row["session_id"],
            "source_id": row["source_id"],
            "chat2_project_name": row["chat2_project_name"],
            "profile_id": row["profile_id"],
            "purpose": row["purpose"],
            "version": int(row["version"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "binding_state": "reference_bound",
            "currently_authorised": currently_authorised,
            "authorisation_state": "authorised" if currently_authorised else "unavailable_or_revoked",
            "authorisation_reason": reason if not currently_authorised else None,
            "profile": self._profile_projection(profile),
            "profile_authority": "Chat 2 Voice House / RightsLedger",
            "chat5_profile_database": False,
            "processor_runtime_attached": False,
            "processor_activation_allowed": False,
            "real_time_processing_proven": False,
            "final_execution_reauthorisation_required": True,
            "final_execution_authority": "Chat 2 authorize_voice_profile",
            "entitlement_authority": "Chat 6",
            "entitlement_evaluated_by_chat5": False,
            "client_entitlement_authority": False,
            "generic_source_config_is_authority": False,
            "raw_reference_files_exposed": False,
            "model_or_provider_secrets_exposed": False,
        }

    def bind(
        self,
        user_id: str,
        session_id: str,
        source_id: str,
        body: LiveVoiceBindingUpsert,
    ) -> dict[str, Any]:
        self._source_context(user_id, session_id, source_id)
        profile = self._authorise_reference(user_id, body.chat2_project_name, body.profile_id, body.purpose)
        row = self.store.upsert(user_id, session_id, source_id, body)
        return self._projection(row, currently_authorised=True, profile=profile)

    def list_bindings(self, user_id: str, session_id: str) -> list[dict[str, Any]]:
        studio_repo.get_session(user_id, session_id)
        projected: list[dict[str, Any]] = []
        for row in self.store.bindings(user_id, session_id):
            try:
                self._source_context(user_id, session_id, str(row["source_id"]))
                profile = self._authorise_reference(
                    user_id,
                    str(row["chat2_project_name"]),
                    str(row["profile_id"]),
                    str(row["purpose"]),
                )
                projected.append(self._projection(row, currently_authorised=True, profile=profile))
            except (FileNotFoundError, KeyError, PermissionError, StudioInvariantError, ValueError):
                projected.append(
                    self._projection(
                        row,
                        currently_authorised=False,
                        profile=None,
                        reason="Binding is no longer currently authorised by the current Chat 2 profile state",
                    )
                )
        return projected

    def unbind(self, user_id: str, session_id: str, source_id: str, expected_version: int) -> dict[str, Any]:
        # Unbinding must remain available even if the profile was revoked after binding.
        self._source_context(user_id, session_id, source_id)
        row = self.store.delete(user_id, session_id, source_id, expected_version)
        return {
            "removed": True,
            "session_id": session_id,
            "source_id": source_id,
            "binding_id": row["id"],
            "processor_runtime_changed": False,
            "programme_state_changed": False,
        }


voice_binding_store = LiveVoiceBindingStore(studio_repo.db_path)
voice_binding_service = LiveVoiceBindingService(voice_binding_store)


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, FileNotFoundError):
        return HTTPException(404, "Chat 2 Voice House project was not found")
    if isinstance(exc, KeyError):
        return HTTPException(404, "Shared Skies LIVE voice binding resource was not found")
    if isinstance(exc, PermissionError):
        return HTTPException(403, str(exc))
    if isinstance(exc, StudioConflict):
        return HTTPException(409, str(exc))
    if isinstance(exc, (StudioInvariantError, ValueError)):
        return HTTPException(400, str(exc))
    return HTTPException(500, "Shared Skies LIVE voice binding operation failed")


@router.get("/shared-sky/studio/api/sessions/{session_id}/voice/bindings")
def list_live_voice_bindings(session_id: str, request: Request):
    member, _membership = require_esp_hub_member(request)
    try:
        return {
            "product": "Shared Skies Streaming Studios LIVE Voice",
            "session_id": session_id,
            "bindings": voice_binding_service.list_bindings(str(member.user_id), session_id),
            "execution_contract": {
                "binding_is_processor_activation": False,
                "final_chat2_reauthorisation_required": True,
                "chat6_entitlement_required_where_applicable": True,
                "real_time_processing_proven": False,
            },
        }
    except Exception as exc:
        raise _http_error(exc) from exc


@router.put("/shared-sky/studio/api/sessions/{session_id}/voice/bindings/{source_id}")
def put_live_voice_binding(
    session_id: str,
    source_id: str,
    body: LiveVoiceBindingUpsert,
    request: Request,
):
    member, _membership = require_esp_hub_member(request)
    try:
        return {
            "binding": voice_binding_service.bind(str(member.user_id), session_id, source_id, body),
            "processor_activated": False,
            "programme_changed": False,
        }
    except Exception as exc:
        raise _http_error(exc) from exc


@router.delete("/shared-sky/studio/api/sessions/{session_id}/voice/bindings/{source_id}")
def delete_live_voice_binding(
    session_id: str,
    source_id: str,
    body: LiveVoiceBindingDelete,
    request: Request,
):
    member, _membership = require_esp_hub_member(request)
    try:
        return voice_binding_service.unbind(str(member.user_id), session_id, source_id, body.expected_version)
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
    "LiveVoiceBindingDelete",
    "LiveVoiceBindingService",
    "LiveVoiceBindingStore",
    "LiveVoiceBindingUpsert",
    "install_shared_skies_live_voice_bindings",
    "list_live_voice_bindings",
    "put_live_voice_binding",
    "delete_live_voice_binding",
    "router",
    "voice_binding_service",
    "voice_binding_store",
]
