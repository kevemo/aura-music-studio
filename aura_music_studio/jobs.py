from __future__ import annotations

import json
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from .accounts import AccountStore
from .pipeline import AuraPipeline
from .request_context import reset_current_user_id, set_current_user_id
from .tenant_storage import project_path


def _now_dt() -> datetime:
    return datetime.now(timezone.utc)


def _now() -> str:
    return _now_dt().isoformat()


class StudioJobQueue:
    """SQLite-WAL durable queue for studio work.

    The queue is intentionally broker-free for the current self-host topology, but still provides
    bounded payload admission, active-job de-duplication, optional stable idempotency keys,
    atomic leases, stale-worker recovery and an explicit dead-letter state. Dead-letter replay is
    fail-closed until the caller confirms that any externally consequential operation is safe to
    repeat under its domain idempotency contract.
    """

    def __init__(self, store: AccountStore | None = None):
        self.store = store or AccountStore()
        self.db_path = self.store.db_path
        self._init_schema()

    def _connect(self):
        con = sqlite3.connect(self.db_path, timeout=30)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA busy_timeout=30000")
        return con

    def _init_schema(self) -> None:
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS studio_jobs (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    project_name TEXT NOT NULL,
                    job_type TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued',
                    priority INTEGER NOT NULL DEFAULT 10,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    worker_id TEXT,
                    payload_json TEXT,
                    result_json TEXT,
                    error TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    idempotency_key TEXT,
                    dead_lettered_at TEXT,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_studio_jobs_queue
                ON studio_jobs(status, priority DESC, created_at ASC);
                CREATE INDEX IF NOT EXISTS idx_studio_jobs_dead_letter
                ON studio_jobs(status, dead_lettered_at ASC);
                """
            )
            columns = {row["name"] for row in con.execute("PRAGMA table_info(studio_jobs)").fetchall()}
            if "payload_json" not in columns:
                con.execute("ALTER TABLE studio_jobs ADD COLUMN payload_json TEXT")
            if "idempotency_key" not in columns:
                con.execute("ALTER TABLE studio_jobs ADD COLUMN idempotency_key TEXT")
            if "dead_lettered_at" not in columns:
                con.execute("ALTER TABLE studio_jobs ADD COLUMN dead_lettered_at TEXT")
            con.execute(
                """CREATE UNIQUE INDEX IF NOT EXISTS idx_studio_jobs_idempotency
                   ON studio_jobs(user_id, job_type, idempotency_key)
                   WHERE idempotency_key IS NOT NULL"""
            )

    @staticmethod
    def _payload_json(payload: dict | None) -> str | None:
        if payload is None:
            return None
        encoded = json.dumps(payload, default=str, separators=(",", ":"))
        limit = max(1024, int(os.getenv("LSS_JOB_MAX_PAYLOAD_BYTES", "262144")))
        if len(encoded.encode("utf-8")) > limit:
            raise ValueError(f"Job payload exceeds the configured {limit}-byte admission limit")
        return encoded

    def submit(
        self,
        user_id: str,
        project_name: str,
        *,
        job_type: str = "produce",
        priority: int = 10,
        payload: dict | None = None,
        idempotency_key: str | None = None,
    ) -> dict:
        job_id = uuid4().hex
        payload_json = self._payload_json(payload)
        idem = (idempotency_key or "").strip() or None
        if idem and len(idem) > 200:
            raise ValueError("Job idempotency key must be 200 characters or fewer")

        with self._connect() as con:
            if idem:
                existing = con.execute(
                    """SELECT * FROM studio_jobs
                       WHERE user_id=? AND job_type=? AND idempotency_key=?
                       ORDER BY created_at DESC LIMIT 1""",
                    (user_id, job_type, idem),
                ).fetchone()
                if existing:
                    return dict(existing)

            existing = con.execute(
                """SELECT * FROM studio_jobs
                   WHERE user_id=? AND project_name=? AND job_type=? AND status IN ('queued','running')
                   ORDER BY created_at DESC LIMIT 1""",
                (user_id, project_name, job_type),
            ).fetchone()
            if existing:
                return dict(existing)

            con.execute(
                """INSERT INTO studio_jobs
                   (id,user_id,project_name,job_type,status,priority,created_at,payload_json,idempotency_key)
                   VALUES (?,?,?,?, 'queued', ?, ?, ?, ?)""",
                (job_id, user_id, project_name, job_type, int(priority), _now(), payload_json, idem),
            )
            row = con.execute("SELECT * FROM studio_jobs WHERE id=?", (job_id,)).fetchone()
        return dict(row)

    def get(self, job_id: str, *, user_id: str | None = None) -> dict | None:
        with self._connect() as con:
            if user_id:
                row = con.execute("SELECT * FROM studio_jobs WHERE id=? AND user_id=?", (job_id, user_id)).fetchone()
            else:
                row = con.execute("SELECT * FROM studio_jobs WHERE id=?", (job_id,)).fetchone()
        return dict(row) if row else None

    def list_for_user(self, user_id: str, limit: int = 50) -> list[dict]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT * FROM studio_jobs WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
                (user_id, max(1, min(int(limit), 200))),
            ).fetchall()
        return [dict(row) for row in rows]

    def summary(self) -> dict:
        with self._connect() as con:
            rows = con.execute("SELECT status, COUNT(*) AS count FROM studio_jobs GROUP BY status").fetchall()
            oldest = con.execute(
                "SELECT created_at FROM studio_jobs WHERE status='queued' ORDER BY created_at ASC LIMIT 1"
            ).fetchone()
            oldest_dlq = con.execute(
                "SELECT dead_lettered_at FROM studio_jobs WHERE status='dead_letter' ORDER BY dead_lettered_at ASC LIMIT 1"
            ).fetchone()
        return {
            "counts": {row["status"]: int(row["count"]) for row in rows},
            "oldest_queued_at": oldest["created_at"] if oldest else None,
            "oldest_dead_lettered_at": oldest_dlq["dead_lettered_at"] if oldest_dlq else None,
            "backend": "sqlite_wal",
            "payload_jobs": True,
            "remote_node_leases": True,
            "dead_letter_supported": True,
            "bounded_payloads": True,
        }

    def requeue_stale(self, *, stale_after_seconds: int = 10_800, max_attempts: int = 3) -> int:
        cutoff = (_now_dt() - timedelta(seconds=max(60, stale_after_seconds))).isoformat()
        now = _now()
        max_attempts = max(1, int(max_attempts))
        with self._connect() as con:
            dead_lettered = con.execute(
                """UPDATE studio_jobs
                   SET status='dead_letter', completed_at=?, dead_lettered_at=?, worker_id=NULL,
                       error='Worker lease expired after maximum retries'
                   WHERE status='running' AND started_at<? AND attempts>=?""",
                (now, now, cutoff, max_attempts),
            ).rowcount
            recovered = con.execute(
                """UPDATE studio_jobs
                   SET status='queued', started_at=NULL, worker_id=NULL, completed_at=NULL,
                       error='Recovered after stale worker lease'
                   WHERE status='running' AND started_at<? AND attempts<?""",
                (cutoff, max_attempts),
            ).rowcount
        return int(recovered + dead_lettered)

    def retry_dead_letter(self, job_id: str, *, idempotency_verified: bool = False) -> bool:
        """Requeue a dead-lettered job only after external side-effect safety is verified."""
        if not idempotency_verified:
            return False
        with self._connect() as con:
            cur = con.execute(
                """UPDATE studio_jobs
                   SET status='queued', started_at=NULL, completed_at=NULL, dead_lettered_at=NULL,
                       worker_id=NULL, error='Authorised retry after dead-letter idempotency verification'
                   WHERE id=? AND status='dead_letter'""",
                (job_id,),
            )
        return cur.rowcount == 1

    def resolve_dead_letter(self, job_id: str, *, reason: str) -> bool:
        reason = (reason or "").strip()
        if not reason:
            raise ValueError("A dead-letter resolution reason is required")
        with self._connect() as con:
            cur = con.execute(
                """UPDATE studio_jobs SET status='resolved', completed_at=?, worker_id=NULL, error=?
                   WHERE id=? AND status='dead_letter'""",
                (_now(), reason[:8000], job_id),
            )
        return cur.rowcount == 1

    def _claim_query(self, worker_id: str, where_sql: str = "", params: tuple = ()) -> dict | None:
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                f"""SELECT * FROM studio_jobs WHERE status='queued' {where_sql}
                    ORDER BY priority DESC, created_at ASC LIMIT 1""",
                params,
            ).fetchone()
            if not row:
                con.commit()
                return None
            cur = con.execute(
                """UPDATE studio_jobs SET status='running', started_at=?, worker_id=?, attempts=attempts+1,
                       completed_at=NULL, dead_lettered_at=NULL
                   WHERE id=? AND status='queued'""",
                (_now(), worker_id, row["id"]),
            )
            if cur.rowcount != 1:
                con.rollback()
                return None
            claimed = con.execute("SELECT * FROM studio_jobs WHERE id=?", (row["id"],)).fetchone()
            con.commit()
        return dict(claimed) if claimed else None

    def claim_next(self, worker_id: str) -> dict | None:
        self.requeue_stale()
        return self._claim_query(worker_id)

    def claim_next_for_job_types(self, worker_id: str, job_types: list[str]) -> dict | None:
        """Atomically lease only jobs this remote ESP node can execute."""
        clean = sorted({str(x).strip() for x in job_types if str(x).strip()})
        if not clean:
            return None
        stale = max(60, int(os.getenv("LSS_NODE_LEASE_SECONDS", "3600")))
        self.requeue_stale(stale_after_seconds=stale)
        placeholders = ",".join("?" for _ in clean)
        return self._claim_query(worker_id, f"AND job_type IN ({placeholders})", tuple(clean))

    def renew_owned(self, job_id: str, worker_id: str) -> bool:
        """Refresh a running job lease without changing attempts or ownership."""
        with self._connect() as con:
            cur = con.execute(
                "UPDATE studio_jobs SET started_at=? WHERE id=? AND status='running' AND worker_id=?",
                (_now(), job_id, worker_id),
            )
        return cur.rowcount == 1

    def complete(self, job_id: str, result: dict) -> None:
        with self._connect() as con:
            con.execute(
                """UPDATE studio_jobs
                   SET status='completed', completed_at=?, result_json=?, error=NULL, dead_lettered_at=NULL
                   WHERE id=?""",
                (_now(), json.dumps(result, default=str), job_id),
            )

    def complete_owned(self, job_id: str, worker_id: str, result: dict) -> bool:
        """Complete a remotely leased job only when the submitting node still owns its lease."""
        with self._connect() as con:
            cur = con.execute(
                """UPDATE studio_jobs
                   SET status='completed', completed_at=?, result_json=?, error=NULL, dead_lettered_at=NULL
                   WHERE id=? AND status='running' AND worker_id=?""",
                (_now(), json.dumps(result, default=str), job_id, worker_id),
            )
        return cur.rowcount == 1

    def fail(self, job_id: str, error: str) -> None:
        with self._connect() as con:
            con.execute(
                "UPDATE studio_jobs SET status='failed', completed_at=?, error=? WHERE id=?",
                (_now(), error[:8000], job_id),
            )

    def fail_owned(self, job_id: str, worker_id: str, error: str) -> bool:
        with self._connect() as con:
            cur = con.execute(
                """UPDATE studio_jobs SET status='failed', completed_at=?, error=?
                   WHERE id=? AND status='running' AND worker_id=?""",
                (_now(), error[:8000], job_id, worker_id),
            )
        return cur.rowcount == 1

    def cancel_queued(self, job_id: str, user_id: str) -> bool:
        with self._connect() as con:
            cur = con.execute(
                "UPDATE studio_jobs SET status='cancelled', completed_at=? WHERE id=? AND user_id=? AND status='queued'",
                (_now(), job_id, user_id),
            )
        return cur.rowcount > 0


class AuraJobWorker:
    def __init__(self, queue: StudioJobQueue | None = None, *, worker_id: str = "aura-worker"):
        self.queue = queue or StudioJobQueue()
        self.store = self.queue.store
        self.worker_id = worker_id

    @staticmethod
    def _payload(job: dict) -> dict:
        raw = job.get("payload_json")
        if not raw:
            return {}
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("Job payload must be a JSON object")
        return value

    def run_job(self, job: dict) -> dict:
        context = set_current_user_id(job["user_id"])
        try:
            project = project_path(job["project_name"], must_exist=True)
            job_type = job["job_type"]
            if job_type == "produce":
                result = AuraPipeline(project).run()
            elif job_type == "build_around":
                from .build_around import BuildAroundRequest, build_around_upload
                result = build_around_upload(project, BuildAroundRequest.model_validate(self._payload(job)))
            elif job_type.startswith("engineering:"):
                from .engineering_jobs import run_engineering_job
                result = run_engineering_job(project, self._payload(job))
            elif job_type == "editor_render":
                from .professional_editor_render_jobs import run_editor_render_job
                result = run_editor_render_job(
                    project,
                    self._payload(job),
                    user_id=job["user_id"],
                    account_store=self.store,
                )
            else:
                raise ValueError(f"Unsupported job type: {job_type}")

            if job_type in {"produce", "build_around"}:
                try:
                    self.store.record_regeneration(job["user_id"], job["project_name"])
                except Exception:
                    pass
            return result
        finally:
            reset_current_user_id(context)

    def run_once(self) -> dict | None:
        job = self.queue.claim_next(self.worker_id)
        if not job:
            return None
        try:
            result = self.run_job(job)
            self.queue.complete(job["id"], result)
            return {"job_id": job["id"], "status": "completed"}
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            self.queue.fail(job["id"], message)
            return {"job_id": job["id"], "status": "failed", "error": message}

    def serve_forever(self, poll_seconds: float = 2.0) -> None:
        while True:
            result = self.run_once()
            if result is None:
                time.sleep(max(0.2, poll_seconds))
