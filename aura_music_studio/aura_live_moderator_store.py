from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Literal

from .aura_live_moderator import AuraModeratorAuthorization, ModerationMode

EventType = Literal[
    "authorization_saved",
    "authorization_revoked",
    "provider_write_enabled",
    "provider_write_disabled",
    "moderation_decision",
    "human_escalation",
]

_SECRET_MARKERS = (
    "password",
    "passwd",
    "cookie",
    "session",
    "token",
    "secret",
    "credential",
    "authorization",
    "private_key",
)


@dataclass(frozen=True)
class StoredAuraModeratorAuthorization:
    user_id: str
    authorization: AuraModeratorAuthorization
    provider_approval_ref: str | None
    updated_at: datetime
    updated_by: str


@dataclass(frozen=True)
class AuraLiveModerationAuditEvent:
    event_id: str
    user_id: str
    event_type: EventType
    actor: str
    created_at: datetime
    metadata: dict[str, Any]
    previous_hash: str | None
    event_hash: str


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC).isoformat()


def _bounded_identity(value: str, *, field: str) -> str:
    clean = str(value or "").strip()
    if not clean or len(clean) > 160 or "\n" in clean or "\r" in clean:
        raise ValueError(f"{field} is invalid")
    return clean


def _sanitize_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    value = dict(metadata or {})
    if len(value) > 24:
        raise ValueError("audit metadata has too many fields")
    for key, item in value.items():
        normalized_key = str(key).strip().lower()
        if not normalized_key or len(normalized_key) > 80:
            raise ValueError("audit metadata key is invalid")
        if any(marker in normalized_key for marker in _SECRET_MARKERS):
            raise ValueError("TikTok/session secrets must not be stored in moderation audit metadata")
        if isinstance(item, (dict, list, tuple, set)):
            encoded = json.dumps(item, sort_keys=True, separators=(",", ":"), default=str)
        else:
            encoded = str(item)
        if len(encoded) > 1000:
            raise ValueError("audit metadata value is too large")
    return value


def _canonical_event_payload(
    *,
    event_id: str,
    user_id: str,
    event_type: EventType,
    actor: str,
    created_at: datetime,
    metadata: dict[str, Any],
    previous_hash: str | None,
) -> bytes:
    document = {
        "actor": actor,
        "created_at": _iso(created_at),
        "event_id": event_id,
        "event_type": event_type,
        "metadata": metadata,
        "previous_hash": previous_hash,
        "user_id": user_id,
    }
    return (
        "AURA-LIVE-MODERATION-AUDIT-V1\n"
        + json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    ).encode("utf-8")


class AuraLiveModeratorStore:
    """Durable creator authorization and append-only moderation audit evidence.

    The store deliberately persists no TikTok password, cookie, session token or private API
    credential. Provider write enablement is a separate reviewed state requiring an opaque
    approval reference; actual TikTok writes remain gated later by the approved connector
    capability boundary in ``AuraLiveModerator``.
    """

    def __init__(self, database: str | Path):
        self.database = str(database)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _ensure_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS aura_live_moderator_authorizations (
                    user_id TEXT PRIMARY KEY,
                    creator_handle TEXT NOT NULL,
                    creator_consent INTEGER NOT NULL CHECK (creator_consent IN (0, 1)),
                    moderator_assignment_confirmed INTEGER NOT NULL CHECK (moderator_assignment_confirmed IN (0, 1)),
                    mode TEXT NOT NULL,
                    provider_write_enabled INTEGER NOT NULL CHECK (provider_write_enabled IN (0, 1)),
                    provider_approval_ref TEXT,
                    updated_at TEXT NOT NULL,
                    updated_by TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS aura_live_moderation_audit (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    user_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    previous_hash TEXT,
                    event_hash TEXT NOT NULL UNIQUE
                );

                CREATE INDEX IF NOT EXISTS idx_aura_live_moderation_audit_user_sequence
                    ON aura_live_moderation_audit(user_id, sequence DESC);
                """
            )

    def save_creator_authorization(
        self,
        *,
        user_id: str,
        authorization: AuraModeratorAuthorization,
        actor: str,
        now: datetime | None = None,
    ) -> StoredAuraModeratorAuthorization:
        user_id = _bounded_identity(user_id, field="user_id")
        actor = _bounded_identity(actor, field="actor")
        timestamp = now or _utcnow()

        # Creator-facing authorization can never self-enable a provider write path. Provider
        # write approval has a separate reviewed method below.
        creator_state = authorization.model_copy(update={"provider_write_enabled": False})

        with self._connect() as connection:
            existing = connection.execute(
                "SELECT provider_write_enabled, provider_approval_ref FROM aura_live_moderator_authorizations WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            provider_write_enabled = bool(existing["provider_write_enabled"]) if existing else False
            provider_approval_ref = str(existing["provider_approval_ref"]) if existing and existing["provider_approval_ref"] else None

            if provider_write_enabled and (
                not creator_state.creator_consent
                or not creator_state.moderator_assignment_confirmed
                or creator_state.mode is ModerationMode.ADVISORY
            ):
                provider_write_enabled = False
                provider_approval_ref = None

            persisted = creator_state.model_copy(update={"provider_write_enabled": provider_write_enabled})
            connection.execute(
                """
                INSERT INTO aura_live_moderator_authorizations (
                    user_id, creator_handle, creator_consent, moderator_assignment_confirmed,
                    mode, provider_write_enabled, provider_approval_ref, updated_at, updated_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    creator_handle = excluded.creator_handle,
                    creator_consent = excluded.creator_consent,
                    moderator_assignment_confirmed = excluded.moderator_assignment_confirmed,
                    mode = excluded.mode,
                    provider_write_enabled = excluded.provider_write_enabled,
                    provider_approval_ref = excluded.provider_approval_ref,
                    updated_at = excluded.updated_at,
                    updated_by = excluded.updated_by
                """,
                (
                    user_id,
                    persisted.creator_handle,
                    int(persisted.creator_consent),
                    int(persisted.moderator_assignment_confirmed),
                    persisted.mode.value,
                    int(persisted.provider_write_enabled),
                    provider_approval_ref,
                    _iso(timestamp),
                    actor,
                ),
            )
            self._append_event(
                connection,
                user_id=user_id,
                event_type="authorization_saved",
                actor=actor,
                created_at=timestamp,
                metadata={
                    "creator_handle": persisted.creator_handle,
                    "creator_consent": persisted.creator_consent,
                    "moderator_assignment_confirmed": persisted.moderator_assignment_confirmed,
                    "mode": persisted.mode.value,
                    "provider_write_enabled": persisted.provider_write_enabled,
                },
            )
        return StoredAuraModeratorAuthorization(user_id, persisted, provider_approval_ref, timestamp, actor)

    def set_provider_write_enabled(
        self,
        *,
        user_id: str,
        enabled: bool,
        actor: str,
        approval_ref: str | None = None,
        now: datetime | None = None,
    ) -> StoredAuraModeratorAuthorization:
        user_id = _bounded_identity(user_id, field="user_id")
        actor = _bounded_identity(actor, field="actor")
        timestamp = now or _utcnow()
        approval = _bounded_identity(approval_ref or "", field="approval_ref") if enabled else None

        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM aura_live_moderator_authorizations WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            if row is None:
                raise KeyError("Aura LIVE Moderator authorization not found")
            authorization = self._authorization_from_row(row)
            if enabled:
                if not authorization.creator_consent:
                    raise ValueError("provider writes require creator consent")
                if not authorization.moderator_assignment_confirmed:
                    raise ValueError("provider writes require confirmed moderator assignment")
                if authorization.mode is ModerationMode.ADVISORY:
                    raise ValueError("provider writes cannot be enabled in advisory mode")
            updated = authorization.model_copy(update={"provider_write_enabled": bool(enabled)})
            connection.execute(
                """
                UPDATE aura_live_moderator_authorizations
                SET provider_write_enabled = ?, provider_approval_ref = ?, updated_at = ?, updated_by = ?
                WHERE user_id = ?
                """,
                (int(enabled), approval, _iso(timestamp), actor, user_id),
            )
            self._append_event(
                connection,
                user_id=user_id,
                event_type="provider_write_enabled" if enabled else "provider_write_disabled",
                actor=actor,
                created_at=timestamp,
                metadata={"approval_reference_present": bool(approval)},
            )
        return StoredAuraModeratorAuthorization(user_id, updated, approval, timestamp, actor)

    def revoke(self, *, user_id: str, actor: str, now: datetime | None = None) -> None:
        user_id = _bounded_identity(user_id, field="user_id")
        actor = _bounded_identity(actor, field="actor")
        timestamp = now or _utcnow()
        with self._connect() as connection:
            deleted = connection.execute(
                "DELETE FROM aura_live_moderator_authorizations WHERE user_id = ?", (user_id,)
            ).rowcount
            if not deleted:
                raise KeyError("Aura LIVE Moderator authorization not found")
            self._append_event(
                connection,
                user_id=user_id,
                event_type="authorization_revoked",
                actor=actor,
                created_at=timestamp,
                metadata={"provider_write_enabled": False},
            )

    def get(self, user_id: str) -> StoredAuraModeratorAuthorization | None:
        user_id = _bounded_identity(user_id, field="user_id")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM aura_live_moderator_authorizations WHERE user_id = ?", (user_id,)
            ).fetchone()
        if row is None:
            return None
        return StoredAuraModeratorAuthorization(
            user_id=user_id,
            authorization=self._authorization_from_row(row),
            provider_approval_ref=str(row["provider_approval_ref"]) if row["provider_approval_ref"] else None,
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
            updated_by=str(row["updated_by"]),
        )

    def record_moderation_event(
        self,
        *,
        user_id: str,
        event_type: Literal["moderation_decision", "human_escalation"],
        actor: str,
        metadata: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> AuraLiveModerationAuditEvent:
        user_id = _bounded_identity(user_id, field="user_id")
        actor = _bounded_identity(actor, field="actor")
        timestamp = now or _utcnow()
        with self._connect() as connection:
            return self._append_event(
                connection,
                user_id=user_id,
                event_type=event_type,
                actor=actor,
                created_at=timestamp,
                metadata=metadata or {},
            )

    def recent_events(self, user_id: str, *, limit: int = 50) -> list[AuraLiveModerationAuditEvent]:
        user_id = _bounded_identity(user_id, field="user_id")
        limit = max(1, min(int(limit), 200))
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT event_id, user_id, event_type, actor, created_at, metadata_json,
                       previous_hash, event_hash
                FROM aura_live_moderation_audit
                WHERE user_id = ?
                ORDER BY sequence DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
        return [self._event_from_row(row) for row in rows]

    def verify_audit_chain(self, user_id: str) -> bool:
        user_id = _bounded_identity(user_id, field="user_id")
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT event_id, user_id, event_type, actor, created_at, metadata_json,
                       previous_hash, event_hash
                FROM aura_live_moderation_audit
                WHERE user_id = ?
                ORDER BY sequence ASC
                """,
                (user_id,),
            ).fetchall()
        previous: str | None = None
        for row in rows:
            event = self._event_from_row(row)
            if event.previous_hash != previous:
                return False
            expected = hashlib.sha256(
                _canonical_event_payload(
                    event_id=event.event_id,
                    user_id=event.user_id,
                    event_type=event.event_type,
                    actor=event.actor,
                    created_at=event.created_at,
                    metadata=event.metadata,
                    previous_hash=event.previous_hash,
                )
            ).hexdigest()
            if expected != event.event_hash:
                return False
            previous = event.event_hash
        return True

    def _append_event(
        self,
        connection: sqlite3.Connection,
        *,
        user_id: str,
        event_type: EventType,
        actor: str,
        created_at: datetime,
        metadata: dict[str, Any],
    ) -> AuraLiveModerationAuditEvent:
        metadata = _sanitize_metadata(metadata)
        last = connection.execute(
            "SELECT event_hash FROM aura_live_moderation_audit WHERE user_id = ? ORDER BY sequence DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        previous_hash = str(last["event_hash"]) if last else None
        event_id = str(uuid.uuid4())
        payload = _canonical_event_payload(
            event_id=event_id,
            user_id=user_id,
            event_type=event_type,
            actor=actor,
            created_at=created_at,
            metadata=metadata,
            previous_hash=previous_hash,
        )
        event_hash = hashlib.sha256(payload).hexdigest()
        connection.execute(
            """
            INSERT INTO aura_live_moderation_audit (
                event_id, user_id, event_type, actor, created_at, metadata_json,
                previous_hash, event_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                user_id,
                event_type,
                actor,
                _iso(created_at),
                json.dumps(metadata, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
                previous_hash,
                event_hash,
            ),
        )
        return AuraLiveModerationAuditEvent(
            event_id=event_id,
            user_id=user_id,
            event_type=event_type,
            actor=actor,
            created_at=created_at,
            metadata=metadata,
            previous_hash=previous_hash,
            event_hash=event_hash,
        )

    @staticmethod
    def _authorization_from_row(row: sqlite3.Row) -> AuraModeratorAuthorization:
        return AuraModeratorAuthorization(
            creator_handle=str(row["creator_handle"]),
            creator_consent=bool(row["creator_consent"]),
            moderator_assignment_confirmed=bool(row["moderator_assignment_confirmed"]),
            mode=ModerationMode(str(row["mode"])),
            provider_write_enabled=bool(row["provider_write_enabled"]),
        )

    @staticmethod
    def _event_from_row(row: sqlite3.Row) -> AuraLiveModerationAuditEvent:
        return AuraLiveModerationAuditEvent(
            event_id=str(row["event_id"]),
            user_id=str(row["user_id"]),
            event_type=str(row["event_type"]),  # type: ignore[arg-type]
            actor=str(row["actor"]),
            created_at=datetime.fromisoformat(str(row["created_at"])),
            metadata=json.loads(str(row["metadata_json"])),
            previous_hash=str(row["previous_hash"]) if row["previous_hash"] else None,
            event_hash=str(row["event_hash"]),
        )


__all__ = [
    "AuraLiveModerationAuditEvent",
    "AuraLiveModeratorStore",
    "StoredAuraModeratorAuthorization",
]
