from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

_ACTIVE_STATES = frozenset({"reserved", "charged", "queued", "running"})
_TERMINAL_STATES = frozenset({"completed", "failed", "refunded"})
_ALLOWED_TRANSITIONS = {
    "reserved": frozenset({"charged", "queued", "failed"}),
    "charged": frozenset({"queued", "refunded"}),
    "queued": frozenset({"running", "completed", "failed"}),
    "running": frozenset({"completed", "failed"}),
    "completed": frozenset(),
    "failed": frozenset(),
    "refunded": frozenset(),
}


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(value: str, label: str, *, limit: int = 240) -> str:
    clean = (value or "").strip()
    if not clean:
        raise ValueError(f"{label} is required")
    if len(clean) > limit:
        raise ValueError(f"{label} is too long")
    return clean


@dataclass(frozen=True)
class RenderAttempt:
    attempt_id: str
    user_id: str
    project_name: str
    directive_id: str
    state: str
    charge_reference: str
    refund_reference: str
    charge_amount: int
    charge_ledger_id: str | None
    provider_prompt_id: str | None
    provider_status: str | None
    created_at: str
    updated_at: str


class ActiveRenderAttemptError(RuntimeError):
    """Raised when a render target already has a durable active admission."""

    def __init__(self, attempt: RenderAttempt):
        super().__init__("A render is already in progress for this directive")
        self.attempt = attempt


class RenderAttemptStore:
    """Durable cross-process admission ledger for creative renderer submissions.

    The partial unique index is the concurrency boundary: at most one attempt for the same
    member/project/directive may be in an active state. The ledger deliberately does not try to
    infer provider acceptance after an ambiguous crash; such attempts remain active/fail-closed
    until there is durable evidence that the matching provider job reached a terminal state.
    """

    def __init__(self, db_path: str | Path | None = None):
        configured = db_path or os.getenv("LSS_DB_PATH") or "data/live_sound_studio.sqlite3"
        self.db_path = Path(configured)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path, timeout=30)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _init_schema(self) -> None:
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS creative_render_attempts (
                    attempt_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    project_name TEXT NOT NULL,
                    directive_id TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN (
                        'reserved','charged','queued','running','completed','failed','refunded'
                    )),
                    charge_reference TEXT NOT NULL UNIQUE,
                    refund_reference TEXT NOT NULL UNIQUE,
                    charge_amount INTEGER NOT NULL DEFAULT 0 CHECK(charge_amount >= 0),
                    charge_ledger_id TEXT,
                    provider_prompt_id TEXT,
                    provider_status TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE UNIQUE INDEX IF NOT EXISTS uq_creative_render_attempt_active
                    ON creative_render_attempts(user_id, project_name, directive_id)
                    WHERE state IN ('reserved','charged','queued','running');

                CREATE INDEX IF NOT EXISTS idx_creative_render_attempt_target_created
                    ON creative_render_attempts(
                        user_id, project_name, directive_id, created_at DESC, attempt_id DESC
                    );
                """
            )

    @staticmethod
    def _row(row: sqlite3.Row | None) -> RenderAttempt | None:
        if row is None:
            return None
        return RenderAttempt(
            attempt_id=str(row["attempt_id"]),
            user_id=str(row["user_id"]),
            project_name=str(row["project_name"]),
            directive_id=str(row["directive_id"]),
            state=str(row["state"]),
            charge_reference=str(row["charge_reference"]),
            refund_reference=str(row["refund_reference"]),
            charge_amount=int(row["charge_amount"]),
            charge_ledger_id=(str(row["charge_ledger_id"]) if row["charge_ledger_id"] else None),
            provider_prompt_id=(str(row["provider_prompt_id"]) if row["provider_prompt_id"] else None),
            provider_status=(str(row["provider_status"]) if row["provider_status"] else None),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    def get(self, attempt_id: str) -> RenderAttempt | None:
        attempt_id = _clean(attempt_id, "Render attempt id", limit=64)
        with self._connect() as con:
            return self._row(
                con.execute(
                    "SELECT * FROM creative_render_attempts WHERE attempt_id=?", (attempt_id,)
                ).fetchone()
            )

    def active(self, user_id: str, project_name: str, directive_id: str) -> RenderAttempt | None:
        user_id = _clean(user_id, "Member user id", limit=128)
        project_name = _clean(project_name, "Project name", limit=240)
        directive_id = _clean(directive_id, "Directive id", limit=128)
        with self._connect() as con:
            return self._row(
                con.execute(
                    """SELECT * FROM creative_render_attempts
                       WHERE user_id=? AND project_name=? AND directive_id=?
                         AND state IN ('reserved','charged','queued','running')
                       ORDER BY created_at DESC, attempt_id DESC LIMIT 1""",
                    (user_id, project_name, directive_id),
                ).fetchone()
            )

    def reserve(self, user_id: str, project_name: str, directive_id: str) -> RenderAttempt:
        user_id = _clean(user_id, "Member user id", limit=128)
        project_name = _clean(project_name, "Project name", limit=240)
        directive_id = _clean(directive_id, "Directive id", limit=128)
        attempt_id = uuid4().hex
        charge_reference = f"creative-render:{attempt_id}:charge"
        refund_reference = f"creative-render:{attempt_id}:refund"
        now = _iso()

        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            existing = con.execute(
                """SELECT * FROM creative_render_attempts
                   WHERE user_id=? AND project_name=? AND directive_id=?
                     AND state IN ('reserved','charged','queued','running')
                   LIMIT 1""",
                (user_id, project_name, directive_id),
            ).fetchone()
            if existing is not None:
                raise ActiveRenderAttemptError(self._row(existing))
            try:
                con.execute(
                    """INSERT INTO creative_render_attempts
                       (attempt_id,user_id,project_name,directive_id,state,charge_reference,
                        refund_reference,charge_amount,created_at,updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (
                        attempt_id,
                        user_id,
                        project_name,
                        directive_id,
                        "reserved",
                        charge_reference,
                        refund_reference,
                        0,
                        now,
                        now,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                existing = con.execute(
                    """SELECT * FROM creative_render_attempts
                       WHERE user_id=? AND project_name=? AND directive_id=?
                         AND state IN ('reserved','charged','queued','running')
                       LIMIT 1""",
                    (user_id, project_name, directive_id),
                ).fetchone()
                if existing is not None:
                    raise ActiveRenderAttemptError(self._row(existing)) from exc
                raise
            row = con.execute(
                "SELECT * FROM creative_render_attempts WHERE attempt_id=?", (attempt_id,)
            ).fetchone()
            attempt = self._row(row)
            if attempt is None:
                raise RuntimeError("Render attempt reservation was not persisted")
            return attempt

    def _transition(
        self,
        attempt_id: str,
        target_state: str,
        *,
        charge_amount: int | None = None,
        charge_ledger_id: str | None = None,
        provider_prompt_id: str | None = None,
        provider_status: str | None = None,
    ) -> RenderAttempt:
        attempt_id = _clean(attempt_id, "Render attempt id", limit=64)
        if target_state not in _ALLOWED_TRANSITIONS:
            raise ValueError("Unsupported render attempt state")

        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                "SELECT * FROM creative_render_attempts WHERE attempt_id=?", (attempt_id,)
            ).fetchone()
            current = self._row(row)
            if current is None:
                raise KeyError("Render attempt not found")
            if current.state == target_state:
                return current
            if target_state not in _ALLOWED_TRANSITIONS[current.state]:
                raise ValueError(f"Invalid render attempt transition: {current.state} -> {target_state}")

            amount = current.charge_amount if charge_amount is None else int(charge_amount)
            if amount < 0:
                raise ValueError("Render charge amount cannot be negative")
            ledger_id = current.charge_ledger_id if charge_ledger_id is None else charge_ledger_id
            prompt_id = current.provider_prompt_id if provider_prompt_id is None else provider_prompt_id
            status = current.provider_status if provider_status is None else provider_status
            now = _iso()
            con.execute(
                """UPDATE creative_render_attempts
                   SET state=?,charge_amount=?,charge_ledger_id=?,provider_prompt_id=?,
                       provider_status=?,updated_at=? WHERE attempt_id=?""",
                (target_state, amount, ledger_id, prompt_id, status, now, attempt_id),
            )
            updated = self._row(
                con.execute(
                    "SELECT * FROM creative_render_attempts WHERE attempt_id=?", (attempt_id,)
                ).fetchone()
            )
            if updated is None:
                raise RuntimeError("Render attempt transition was not persisted")
            return updated

    def mark_charged(self, attempt_id: str, amount: int, ledger_id: str | None) -> RenderAttempt:
        amount = int(amount)
        if amount <= 0:
            raise ValueError("Render charge amount must be positive")
        return self._transition(
            attempt_id,
            "charged",
            charge_amount=amount,
            charge_ledger_id=(ledger_id or "").strip() or None,
        )

    def mark_queued(self, attempt_id: str, provider_prompt_id: str | None) -> RenderAttempt:
        return self._transition(
            attempt_id,
            "queued",
            provider_prompt_id=(provider_prompt_id or "").strip() or None,
            provider_status="queued",
        )

    def mark_running(self, attempt_id: str) -> RenderAttempt:
        return self._transition(attempt_id, "running", provider_status="running")

    def mark_completed(self, attempt_id: str) -> RenderAttempt:
        return self._transition(attempt_id, "completed", provider_status="completed")

    def mark_failed(self, attempt_id: str) -> RenderAttempt:
        return self._transition(attempt_id, "failed", provider_status="failed")

    def mark_refunded(self, attempt_id: str) -> RenderAttempt:
        return self._transition(attempt_id, "refunded", provider_status="submission_failed")


__all__ = [
    "ActiveRenderAttemptError",
    "RenderAttempt",
    "RenderAttemptStore",
]
