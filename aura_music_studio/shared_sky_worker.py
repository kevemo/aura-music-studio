from __future__ import annotations

import os
import socket
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from .shared_sky_relay import SharedSkyRelayError
from .shared_sky_security import SharedSkyVaultError
from .shared_sky_streaming_studios import BroadcastCreate, SharedSkyStore, shared_sky


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _parse_time(value: str | None) -> datetime | None:
    raw = (value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class WorkerSettings:
    enabled: bool
    poll_seconds: float
    lease_seconds: int
    max_attempts: int
    retry_seconds: int

    @classmethod
    def from_env(cls) -> "WorkerSettings":
        def _bool(name: str, default: str = "0") -> bool:
            return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}

        def _int(name: str, default: int, low: int, high: int) -> int:
            try:
                value = int(os.getenv(name, str(default)))
            except ValueError:
                value = default
            return max(low, min(high, value))

        try:
            poll = float(os.getenv("SHARED_SKY_SCHEDULER_POLL_SECONDS", "5"))
        except ValueError:
            poll = 5.0
        return cls(
            enabled=_bool("SHARED_SKY_SCHEDULER_ENABLED"),
            poll_seconds=max(1.0, min(60.0, poll)),
            lease_seconds=_int("SHARED_SKY_SCHEDULER_LEASE_SECONDS", 90, 15, 900),
            max_attempts=_int("SHARED_SKY_SCHEDULER_MAX_ATTEMPTS", 3, 1, 20),
            retry_seconds=_int("SHARED_SKY_SCHEDULER_RETRY_SECONDS", 30, 5, 3600),
        )


class SharedSkyWorker:
    """Durable schedule executor for Shared Sky Streaming Studios.

    The web app persists schedules. This worker claims due schedules with a database
    lease, creates one broadcast, and asks the existing Shared Sky control plane to run
    its normal fail-closed preflight/start sequence. Multiple worker processes may poll
    the same SQLite database without deliberately double-starting the same schedule.

    The worker remains disabled unless ``SHARED_SKY_SCHEDULER_ENABLED`` is explicitly
    set. A scheduler cannot manufacture a contribution ingest feed, platform permission,
    or a pre-recorded playout engine; those conditions continue to fail closed.
    """

    def __init__(
        self,
        store: SharedSkyStore | None = None,
        *,
        settings: WorkerSettings | None = None,
        worker_id: str | None = None,
    ):
        self.store = store or shared_sky
        self.db_path = self.store.db_path
        self.settings = settings or WorkerSettings.from_env()
        self.worker_id = worker_id or f"{socket.gethostname()}-{os.getpid()}-{uuid4().hex[:8]}"
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path, timeout=15)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _column_names(self, con: sqlite3.Connection, table: str) -> set[str]:
        return {str(row[1]) for row in con.execute(f"PRAGMA table_info({table})").fetchall()}

    def _init_schema(self) -> None:
        with self._connect() as con:
            columns = self._column_names(con, "shared_sky_schedules")
            additions = {
                "claimed_by": "TEXT",
                "claim_expires_at": "TEXT",
                "attempt_count": "INTEGER NOT NULL DEFAULT 0",
                "next_attempt_at": "TEXT",
                "last_attempt_at": "TEXT",
                "last_error": "TEXT NOT NULL DEFAULT ''",
                "broadcast_id": "TEXT",
                "started_at": "TEXT",
                "completed_at": "TEXT",
            }
            for name, definition in additions.items():
                if name not in columns:
                    con.execute(f"ALTER TABLE shared_sky_schedules ADD COLUMN {name} {definition}")
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS shared_sky_worker_heartbeats (
                    worker_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    last_claimed_schedule_id TEXT,
                    last_error TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_shared_sky_schedule_claim
                ON shared_sky_schedules(state, start_at, next_attempt_at, claim_expires_at);
                """
            )

    def heartbeat(
        self,
        *,
        status: str = "idle",
        schedule_id: str | None = None,
        error: str = "",
        now: datetime | None = None,
    ) -> None:
        stamp = _iso(now or _utc_now())
        with self._connect() as con:
            con.execute(
                """
                INSERT INTO shared_sky_worker_heartbeats(
                    worker_id,status,last_seen_at,last_claimed_schedule_id,last_error
                ) VALUES(?,?,?,?,?)
                ON CONFLICT(worker_id) DO UPDATE SET
                    status=excluded.status,
                    last_seen_at=excluded.last_seen_at,
                    last_claimed_schedule_id=excluded.last_claimed_schedule_id,
                    last_error=excluded.last_error
                """,
                (self.worker_id, status[:32], stamp, schedule_id, error[:1000]),
            )

    def worker_health(self, *, stale_after_seconds: int = 180) -> list[dict]:
        cutoff = _utc_now() - timedelta(seconds=max(30, stale_after_seconds))
        with self._connect() as con:
            rows = con.execute(
                "SELECT * FROM shared_sky_worker_heartbeats ORDER BY last_seen_at DESC LIMIT 100"
            ).fetchall()
        output: list[dict] = []
        for row in rows:
            item = dict(row)
            seen = _parse_time(item["last_seen_at"])
            item["healthy"] = bool(seen and seen >= cutoff)
            output.append(item)
        return output

    def _claimable(self, row: sqlite3.Row, now: datetime) -> bool:
        state = str(row["state"] or "")
        if state not in {"scheduled", "retry"}:
            return False
        start_at = _parse_time(row["start_at"])
        if not start_at or start_at > now:
            return False
        next_attempt = _parse_time(row["next_attempt_at"])
        if next_attempt and next_attempt > now:
            return False
        claim_expires = _parse_time(row["claim_expires_at"])
        if row["claimed_by"] and claim_expires and claim_expires > now:
            return False
        return int(row["attempt_count"] or 0) < self.settings.max_attempts

    def claim_due_schedule(self, *, now: datetime | None = None) -> dict | None:
        current = (now or _utc_now()).astimezone(timezone.utc)
        expires = current + timedelta(seconds=self.settings.lease_seconds)
        with self._connect() as con:
            con.isolation_level = None
            con.execute("BEGIN IMMEDIATE")
            rows = con.execute(
                """
                SELECT * FROM shared_sky_schedules
                WHERE state IN ('scheduled','retry')
                ORDER BY start_at ASC, id ASC
                LIMIT 200
                """
            ).fetchall()
            selected = next((row for row in rows if self._claimable(row, current)), None)
            if not selected:
                con.execute("COMMIT")
                return None
            schedule_id = str(selected["id"])
            con.execute(
                """
                UPDATE shared_sky_schedules
                SET state='starting', claimed_by=?, claim_expires_at=?,
                    attempt_count=attempt_count+1, last_attempt_at=?, updated_at=?
                WHERE id=? AND state IN ('scheduled','retry')
                """,
                (
                    self.worker_id,
                    _iso(expires),
                    _iso(current),
                    _iso(current),
                    schedule_id,
                ),
            )
            if con.total_changes != 1:
                con.execute("ROLLBACK")
                return None
            row = con.execute(
                "SELECT * FROM shared_sky_schedules WHERE id=?", (schedule_id,)
            ).fetchone()
            con.execute("COMMIT")
        return dict(row) if row else None

    def _schedule_destinations(self, schedule: dict) -> list[str]:
        import json

        try:
            value = json.loads(schedule.get("destination_ids_json") or "[]")
        except (TypeError, ValueError):
            return []
        if not isinstance(value, list):
            return []
        return [str(item) for item in value if str(item).strip()][:50]

    def _release_for_retry(self, schedule: dict, error: Exception, now: datetime) -> None:
        attempts = int(schedule.get("attempt_count") or 0)
        terminal = attempts >= self.settings.max_attempts
        new_state = "failed" if terminal else "retry"
        retry_at = None if terminal else _iso(now + timedelta(seconds=self.settings.retry_seconds))
        with self._connect() as con:
            con.execute(
                """
                UPDATE shared_sky_schedules
                SET state=?, claimed_by=NULL, claim_expires_at=NULL,
                    next_attempt_at=?, last_error=?, updated_at=?,
                    completed_at=CASE WHEN ?='failed' THEN ? ELSE completed_at END
                WHERE id=? AND claimed_by=?
                """,
                (
                    new_state,
                    retry_at,
                    str(error)[:1000],
                    _iso(now),
                    new_state,
                    _iso(now),
                    schedule["id"],
                    self.worker_id,
                ),
            )
        self.heartbeat(status=new_state, schedule_id=str(schedule["id"]), error=str(error), now=now)

    def _complete(self, schedule: dict, broadcast_id: str, now: datetime) -> None:
        with self._connect() as con:
            con.execute(
                """
                UPDATE shared_sky_schedules
                SET state='live', claimed_by=NULL, claim_expires_at=NULL,
                    next_attempt_at=NULL, last_error='', broadcast_id=?,
                    started_at=?, completed_at=?, updated_at=?
                WHERE id=? AND claimed_by=?
                """,
                (
                    broadcast_id,
                    _iso(now),
                    _iso(now),
                    _iso(now),
                    schedule["id"],
                    self.worker_id,
                ),
            )
        self.heartbeat(status="live", schedule_id=str(schedule["id"]), now=now)

    def execute_claimed(self, schedule: dict, *, now: datetime | None = None) -> dict:
        current = (now or _utc_now()).astimezone(timezone.utc)
        if str(schedule.get("claimed_by") or "") != self.worker_id:
            raise RuntimeError("Shared Sky worker cannot execute a schedule it does not own")
        if str(schedule.get("mode") or "live") != "live":
            error = RuntimeError(
                "Pre-recorded Shared Sky playout is not enabled until the dedicated media playout worker is deployed"
            )
            self._release_for_retry(schedule, error, current)
            return {"ok": False, "schedule_id": schedule["id"], "error": str(error)}

        broadcast_id = str(schedule.get("broadcast_id") or "").strip()
        try:
            if broadcast_id:
                broadcast = self.store.broadcast(str(schedule["user_id"]), broadcast_id)
            else:
                broadcast = self.store.create_broadcast(
                    str(schedule["user_id"]),
                    BroadcastCreate(
                        project_id=str(schedule["project_id"]),
                        title=str(schedule["title"]),
                        destination_ids=self._schedule_destinations(schedule),
                        passthrough=True,
                    ),
                )
                broadcast_id = str(broadcast["id"])
                with self._connect() as con:
                    con.execute(
                        "UPDATE shared_sky_schedules SET broadcast_id=?, updated_at=? WHERE id=?",
                        (broadcast_id, _iso(current), schedule["id"]),
                    )
            if str(broadcast.get("state")) != "live":
                self.store.start_broadcast(str(schedule["user_id"]), broadcast_id)
            self._complete(schedule, broadcast_id, current)
            return {"ok": True, "schedule_id": schedule["id"], "broadcast_id": broadcast_id}
        except (KeyError, ValueError, RuntimeError, SharedSkyRelayError, SharedSkyVaultError) as exc:
            self._release_for_retry(schedule, exc, current)
            return {
                "ok": False,
                "schedule_id": schedule["id"],
                "broadcast_id": broadcast_id or None,
                "error": str(exc),
            }

    def run_once(self, *, now: datetime | None = None) -> dict:
        current = (now or _utc_now()).astimezone(timezone.utc)
        self.heartbeat(status="polling", now=current)
        schedule = self.claim_due_schedule(now=current)
        if not schedule:
            self.heartbeat(status="idle", now=current)
            return {"claimed": False}
        self.heartbeat(status="starting", schedule_id=str(schedule["id"]), now=current)
        result = self.execute_claimed(schedule, now=current)
        return {"claimed": True, **result}

    def run_forever(self) -> None:
        if not self.settings.enabled:
            raise RuntimeError(
                "Shared Sky scheduler is disabled. Set SHARED_SKY_SCHEDULER_ENABLED=1 only after the relay/ingest runtime has been validated."
            )
        self.heartbeat(status="starting")
        while True:
            try:
                self.run_once()
            except KeyboardInterrupt:
                self.heartbeat(status="stopped")
                return
            except Exception as exc:
                self.heartbeat(status="error", error=str(exc))
            time.sleep(self.settings.poll_seconds)


def run_worker() -> None:
    SharedSkyWorker().run_forever()


__all__ = ["SharedSkyWorker", "WorkerSettings", "run_worker"]
