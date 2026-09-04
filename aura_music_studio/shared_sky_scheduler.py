from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request

from .owner_identity import owner_session_authorized
from .shared_sky_relay import SharedSkyRelayError
from .shared_sky_security import SharedSkyVaultError
from .shared_sky_streaming_studios import BroadcastCreate, SharedSkyStore, shared_sky

router = APIRouter(tags=["Shared Sky Streaming Studios Scheduler"])


class SharedSkySchedulerError(RuntimeError):
    pass


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_instant(value: str) -> datetime:
    clean = (value or "").strip()
    if clean.endswith("Z"):
        clean = clean[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(clean)
    except ValueError as exc:
        raise SharedSkySchedulerError("Schedule start_at must be a valid ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SharedSkySchedulerError("Schedule start_at must include a timezone offset")
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class SchedulerHealth:
    enabled: bool
    poll_seconds: int
    runtime_mode: str = "dedicated-worker"


class SharedSkyScheduler:
    """Server-authoritative due-schedule executor for Shared Sky.

    A row is atomically claimed before any broadcast is created, preventing two workers
    from starting the same scheduled production. Only live schedules are executable in
    this slice. Pre-recorded schedules fail closed until the media playout worker has a
    validated source-asset contract.
    """

    def __init__(
        self,
        store: SharedSkyStore | None = None,
        *,
        enabled: bool | None = None,
        poll_seconds: int | None = None,
    ):
        self.store = store or shared_sky
        if enabled is None:
            enabled = os.getenv("SHARED_SKY_SCHEDULER_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}
        if poll_seconds is None:
            raw = os.getenv("SHARED_SKY_SCHEDULER_POLL_SECONDS", "15").strip()
            try:
                poll_seconds = int(raw)
            except ValueError:
                poll_seconds = 15
        self.enabled = bool(enabled)
        self.poll_seconds = max(5, min(int(poll_seconds), 300))
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with self.store._connect() as con:
            columns = {str(row[1]) for row in con.execute("PRAGMA table_info(shared_sky_schedules)").fetchall()}
            additions = {
                "broadcast_id": "TEXT",
                "last_error": "TEXT NOT NULL DEFAULT ''",
                "claimed_at": "TEXT",
                "completed_at": "TEXT",
            }
            for name, definition in additions.items():
                if name not in columns:
                    con.execute(f"ALTER TABLE shared_sky_schedules ADD COLUMN {name} {definition}")

    def health(self) -> SchedulerHealth:
        return SchedulerHealth(enabled=self.enabled, poll_seconds=self.poll_seconds)

    def _mark(self, schedule_id: str, state: str, *, broadcast_id: str | None = None, error: str = "") -> None:
        now = _utc_now().isoformat()
        completed_at = now if state in {"started", "failed", "cancelled"} else None
        with self.store._connect() as con:
            con.execute(
                "UPDATE shared_sky_schedules SET state=?,broadcast_id=COALESCE(?,broadcast_id),last_error=?,completed_at=?,updated_at=? WHERE id=?",
                (state, broadcast_id, error[:500], completed_at, now, schedule_id),
            )

    def claim_due(self, *, now: datetime | None = None, limit: int = 20) -> list[dict]:
        instant = (now or _utc_now()).astimezone(timezone.utc)
        claimed: list[dict] = []
        invalid: list[tuple[str, str]] = []
        with self.store._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            rows = con.execute(
                "SELECT * FROM shared_sky_schedules WHERE state='scheduled' ORDER BY start_at,id LIMIT ?",
                (max(1, min(int(limit), 100)),),
            ).fetchall()
            for row in rows:
                item = dict(row)
                try:
                    due_at = _parse_instant(str(item["start_at"]))
                except SharedSkySchedulerError as exc:
                    invalid.append((str(item["id"]), str(exc)))
                    continue
                if due_at > instant:
                    continue
                claimed_at = _utc_now().isoformat()
                updated = con.execute(
                    "UPDATE shared_sky_schedules SET state='starting',claimed_at=?,last_error='',updated_at=? WHERE id=? AND state='scheduled'",
                    (claimed_at, claimed_at, item["id"]),
                ).rowcount
                if updated == 1:
                    item["state"] = "starting"
                    item["claimed_at"] = claimed_at
                    claimed.append(item)
            for schedule_id, error in invalid:
                con.execute(
                    "UPDATE shared_sky_schedules SET state='failed',last_error=?,completed_at=?,updated_at=? WHERE id=? AND state='scheduled'",
                    (error[:500], _utc_now().isoformat(), _utc_now().isoformat(), schedule_id),
                )
        return claimed

    def run_due(self, *, now: datetime | None = None, limit: int = 20) -> dict:
        if not self.enabled:
            raise SharedSkySchedulerError("Shared Sky scheduler is disabled by deployment configuration")

        claimed = self.claim_due(now=now, limit=limit)
        started: list[str] = []
        failed: list[dict] = []

        for schedule in claimed:
            schedule_id = str(schedule["id"])
            user_id = str(schedule["user_id"])
            if schedule.get("mode") != "live":
                message = "Pre-recorded Shared Sky schedules require the dedicated media playout worker"
                self._mark(schedule_id, "failed", error=message)
                failed.append({"schedule_id": schedule_id, "error": message})
                continue

            destination_ids = schedule.get("destination_ids_json")
            if isinstance(destination_ids, str):
                try:
                    import json
                    destination_ids = json.loads(destination_ids)
                except Exception:
                    destination_ids = []
            if not isinstance(destination_ids, list):
                destination_ids = []

            try:
                broadcast = self.store.create_broadcast(
                    user_id,
                    BroadcastCreate(
                        project_id=str(schedule["project_id"]),
                        title=str(schedule["title"]),
                        destination_ids=[str(value) for value in destination_ids],
                        passthrough=True,
                    ),
                )
                broadcast_id = str(broadcast["id"])
                self._mark(schedule_id, "starting", broadcast_id=broadcast_id)
                self.store.start_broadcast(user_id, broadcast_id)
                self._mark(schedule_id, "started", broadcast_id=broadcast_id)
                started.append(schedule_id)
            except (KeyError, ValueError, SharedSkyRelayError, SharedSkyVaultError) as exc:
                message = str(exc) or exc.__class__.__name__
                self._mark(schedule_id, "failed", error=message)
                failed.append({"schedule_id": schedule_id, "error": message[:500]})

        return {"claimed": len(claimed), "started": len(started), "failed": failed, "schedule_ids": started}


def _owner(request: Request) -> None:
    if not owner_session_authorized(request):
        raise HTTPException(401, "Owner authentication required")


scheduler = SharedSkyScheduler()


@router.get("/owner/shared-sky/api/scheduler/status")
def scheduler_status(request: Request):
    _owner(request)
    return {"scheduler": scheduler.health().__dict__}


@router.post("/owner/shared-sky/api/scheduler/run-due")
def scheduler_run_due(request: Request):
    _owner(request)
    try:
        return scheduler.run_due()
    except SharedSkySchedulerError as exc:
        raise HTTPException(503, str(exc)) from exc


__all__ = [
    "router",
    "SharedSkyScheduler",
    "SharedSkySchedulerError",
    "SchedulerHealth",
    "scheduler",
]
