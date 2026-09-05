from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from enum import Enum
from hashlib import sha256
import json
import math
from pathlib import Path
import sqlite3
from typing import Any, Iterator
from uuid import uuid4

from .events import EventEnvelope


SCHEMA_VERSION = 1


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_json_value(value: Any, *, path: str = "$") -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"non-finite number is not valid JSON at {path}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_json_value(item, path=f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"JSON object key must be a string at {path}")
            _validate_json_value(item, path=f"{path}.{key}")
        return
    raise TypeError(f"unsupported non-JSON value at {path}: {type(value).__name__}")


def _canonical_json(payload: Any) -> str:
    _validate_json_value(payload)
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def canonical_request_hash(payload: Any) -> str:
    """Hash only deterministic JSON request values; reject lossy coercion."""

    return sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _parse_aware_iso(value: str, *, field: str) -> datetime:
    """Parse persisted ISO timestamps without silently accepting naive values."""

    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must contain a timezone-aware ISO timestamp")
    return parsed.astimezone(timezone.utc)


class IdempotencyDisposition(str, Enum):
    NEW = "new"
    REPLAY = "replay"
    IN_PROGRESS = "in_progress"


class IdempotencyConflictError(RuntimeError):
    pass


class IdempotencyLeaseLostError(RuntimeError):
    """Raised when a worker no longer owns an in-progress idempotency claim."""


class SharedPersistence:
    """Additive SQLite foundation for cross-domain idempotency and event outbox."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        self._is_memory = self.db_path == ":memory:"
        self._connect_target = self.db_path
        self._connect_uri = False
        self._memory_anchor: sqlite3.Connection | None = None
        if self._is_memory:
            self._connect_target = (
                f"file:esp_shared_{uuid4().hex}?mode=memory&cache=shared"
            )
            self._connect_uri = True
            # A shared in-memory SQLite database exists only while at least one
            # connection remains open. Keep an anchor for this object's lifetime.
            self._memory_anchor = self._new_connection()

    def _new_connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self._connect_target,
            timeout=30,
            isolation_level=None,
            uri=self._connect_uri,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        if not self._is_memory:
            connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _connect(self) -> sqlite3.Connection:
        return self._new_connection()

    def close(self) -> None:
        anchor = self._memory_anchor
        self._memory_anchor = None
        if anchor is not None:
            anchor.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def initialize(self) -> None:
        connection = self._connect()
        try:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS shared_schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS shared_idempotency (
                    idempotency_key TEXT PRIMARY KEY,
                    request_hash TEXT NOT NULL,
                    correlation_id TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('in_progress','completed')),
                    response_status INTEGER,
                    response_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS shared_event_outbox (
                    event_id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    aggregate_type TEXT NOT NULL,
                    aggregate_id TEXT NOT NULL,
                    correlation_id TEXT NOT NULL,
                    idempotency_key TEXT,
                    event_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    published_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_shared_outbox_pending
                ON shared_event_outbox(published_at, created_at);
                """
            )
            connection.execute(
                "INSERT OR IGNORE INTO shared_schema_migrations(version, applied_at) VALUES (?, ?)",
                (SCHEMA_VERSION, utc_now_iso()),
            )
        finally:
            connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def claim_idempotency(
        self,
        *,
        idempotency_key: str,
        request_hash: str,
        correlation_id: str,
        reclaim_stale_after: timedelta | None = None,
    ) -> tuple[IdempotencyDisposition, dict[str, Any] | None]:
        """Claim a key, optionally recovering a same-request stale worker lease.

        ``reclaim_stale_after`` is a trusted server policy. ``None`` keeps the
        fail-closed default: an unfinished claim remains in progress forever
        until an operator/domain policy explicitly opts into stale recovery.
        """

        if reclaim_stale_after is not None:
            if not isinstance(reclaim_stale_after, timedelta):
                raise TypeError("reclaim_stale_after must be a datetime.timedelta")
            if reclaim_stale_after <= timedelta(0):
                raise ValueError("reclaim_stale_after must be greater than zero")

        now_datetime = datetime.now(timezone.utc)
        now = now_datetime.isoformat()
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM shared_idempotency WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if row is None:
                connection.execute(
                    """
                    INSERT INTO shared_idempotency(
                        idempotency_key, request_hash, correlation_id, state,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, 'in_progress', ?, ?)
                    """,
                    (idempotency_key, request_hash, correlation_id, now, now),
                )
                return IdempotencyDisposition.NEW, None
            if row["request_hash"] != request_hash:
                raise IdempotencyConflictError(
                    "idempotency key was already used for a different request"
                )
            if row["state"] == "completed":
                response = json.loads(row["response_json"]) if row["response_json"] else None
                return IdempotencyDisposition.REPLAY, {
                    "status": row["response_status"],
                    "body": response,
                    "correlation_id": row["correlation_id"],
                }
            if reclaim_stale_after is not None:
                updated_at = _parse_aware_iso(
                    row["updated_at"],
                    field="shared_idempotency.updated_at",
                )
                if now_datetime - updated_at >= reclaim_stale_after:
                    connection.execute(
                        """
                        UPDATE shared_idempotency
                        SET correlation_id=?, response_status=NULL, response_json=NULL,
                            updated_at=?
                        WHERE idempotency_key=? AND state='in_progress'
                        """,
                        (correlation_id, now, idempotency_key),
                    )
                    return IdempotencyDisposition.NEW, None
            return IdempotencyDisposition.IN_PROGRESS, None

    def complete_idempotency(
        self,
        *,
        idempotency_key: str,
        request_hash: str,
        correlation_id: str,
        response_status: int,
        response_body: Any,
    ) -> None:
        """Complete a claim only while ``correlation_id`` still owns its lease."""

        with self.transaction() as connection:
            row = connection.execute(
                """
                SELECT request_hash, correlation_id, state
                FROM shared_idempotency
                WHERE idempotency_key = ?
                """,
                (idempotency_key,),
            ).fetchone()
            if row is None:
                raise KeyError("idempotency key has not been claimed")
            if row["request_hash"] != request_hash:
                raise IdempotencyConflictError(
                    "idempotency key was already used for a different request"
                )
            # Completion is itself idempotent. Once the first valid owner stores
            # the replay result, later workers must never replace that result.
            if row["state"] == "completed":
                return
            if row["correlation_id"] != correlation_id:
                raise IdempotencyLeaseLostError(
                    "idempotency claim was reclaimed by another worker"
                )

            # Replay state is part of the shared contract too. Reject arbitrary
            # Python objects rather than silently stringifying them into a response.
            response_json = _canonical_json(response_body)
            cursor = connection.execute(
                """
                UPDATE shared_idempotency
                SET state='completed', response_status=?, response_json=?, updated_at=?
                WHERE idempotency_key=? AND state='in_progress' AND correlation_id=?
                """,
                (
                    int(response_status),
                    response_json,
                    utc_now_iso(),
                    idempotency_key,
                    correlation_id,
                ),
            )
            if cursor.rowcount != 1:
                raise IdempotencyLeaseLostError(
                    "idempotency claim is no longer owned by this worker"
                )

    def enqueue_event(
        self,
        event: EventEnvelope,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        values = (
            event.event_id,
            event.type,
            event.subject_type,
            event.subject_id,
            event.correlation_id,
            event.idempotency_key,
            event.model_dump_json(),
            utc_now_iso(),
        )
        sql = """
            INSERT INTO shared_event_outbox(
                event_id, event_type, aggregate_type, aggregate_id,
                correlation_id, idempotency_key, event_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """
        if connection is not None:
            connection.execute(sql, values)
            return
        with self.transaction() as owned:
            owned.execute(sql, values)

    def pending_outbox(self, *, limit: int = 100) -> list[dict[str, Any]]:
        if limit < 1 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000")
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT event_id, event_json, created_at
                FROM shared_event_outbox
                WHERE published_at IS NULL
                ORDER BY created_at, event_id
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            connection.close()

    def mark_outbox_published(self, event_id: str) -> None:
        with self.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE shared_event_outbox
                SET published_at = ?
                WHERE event_id = ? AND published_at IS NULL
                """,
                (utc_now_iso(), event_id),
            )
            if cursor.rowcount == 0:
                row = connection.execute(
                    "SELECT published_at FROM shared_event_outbox WHERE event_id = ?",
                    (event_id,),
                ).fetchone()
                if row is None:
                    raise KeyError(f"unknown outbox event {event_id!r}")
