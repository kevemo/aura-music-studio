from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from uuid import uuid4


class ProviderBudgetExceeded(RuntimeError):
    """Raised before a provider call when an operator hard budget would be exceeded."""


def _governance():
    # Import lazily so importing the package does not initialise the operational cost database.
    from . import provider_cost_governance

    return provider_cost_governance


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat()


def _enforcement_mode() -> str:
    mode = (os.getenv("AURA_PROVIDER_COST_ENFORCEMENT") or "warning").strip().lower()
    if mode not in {"warning", "hard"}:
        raise RuntimeError("AURA_PROVIDER_COST_ENFORCEMENT must be 'warning' or 'hard'")
    return mode


def _reservation_ttl_seconds() -> int:
    raw = (os.getenv("AURA_PROVIDER_COST_RESERVATION_TTL_SECONDS") or "900").strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError("AURA_PROVIDER_COST_RESERVATION_TTL_SECONDS must be an integer") from exc
    return max(60, min(value, 3600))


def _connect() -> sqlite3.Connection:
    governance = _governance()
    con = sqlite3.connect(governance.store.db_path, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    return con


def _ensure_schema(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS provider_cost_reservations (
            id TEXT PRIMARY KEY,
            provider TEXT NOT NULL,
            service TEXT NOT NULL,
            operation TEXT NOT NULL,
            reserved_minor INTEGER NOT NULL CHECK(reserved_minor >= 0),
            currency TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_provider_cost_reservation_expiry
            ON provider_cost_reservations(expires_at);
        """
    )


def _effective_spend(con: sqlite3.Connection, start: datetime, currency: str) -> int:
    row = con.execute(
        """SELECT SUM(CASE WHEN actual_cost_minor IS NOT NULL THEN actual_cost_minor
                           WHEN estimated_cost_minor IS NOT NULL THEN estimated_cost_minor ELSE 0 END)
           AS spend_minor
           FROM provider_cost_events WHERE created_at>=? AND currency=?""",
        (_iso(start), currency),
    ).fetchone()
    return int(row["spend_minor"] or 0) if row else 0


def _active_reservations(con: sqlite3.Connection, start: datetime, currency: str, now: datetime) -> int:
    row = con.execute(
        """SELECT SUM(reserved_minor) AS reserved_minor
           FROM provider_cost_reservations
           WHERE created_at>=? AND expires_at>? AND currency=?""",
        (_iso(start), _iso(now), currency),
    ).fetchone()
    return int(row["reserved_minor"] or 0) if row else 0


def reserve_provider_budget(*, provider: str, service: str, operation: str) -> str | None:
    """Atomically reserve estimated provider spend when hard enforcement is enabled.

    Warning mode deliberately preserves the existing non-blocking behaviour. Hard mode fails
    closed for unpriced work and serialises budget checks with SQLite ``BEGIN IMMEDIATE`` so
    concurrent render requests cannot independently consume the same remaining budget.
    """

    if _enforcement_mode() != "hard":
        return None

    governance = _governance()
    estimate = governance.configured_estimate_minor(provider, service, operation)
    if estimate is None:
        raise ProviderBudgetExceeded(
            "Provider hard-budget enforcement requires an operator-configured cost estimate"
        )

    daily_budget = governance._configured_budget_minor("daily")
    monthly_budget = governance._configured_budget_minor("monthly")
    if daily_budget is None and monthly_budget is None:
        raise ProviderBudgetExceeded(
            "Provider hard-budget enforcement requires a configured daily or monthly budget"
        )

    now = _utcnow()
    currency = governance._currency()
    windows = {
        "daily": (now.replace(hour=0, minute=0, second=0, microsecond=0), daily_budget),
        "monthly": (now.replace(day=1, hour=0, minute=0, second=0, microsecond=0), monthly_budget),
    }
    reservation_id = uuid4().hex
    expires_at = now + timedelta(seconds=_reservation_ttl_seconds())

    with _connect() as con:
        _ensure_schema(con)
        con.execute("BEGIN IMMEDIATE")
        con.execute("DELETE FROM provider_cost_reservations WHERE expires_at<=?", (_iso(now),))
        for window_name, (start, budget) in windows.items():
            if budget is None:
                continue
            committed = _effective_spend(con, start, currency)
            reserved = _active_reservations(con, start, currency, now)
            projected = committed + reserved + estimate
            if projected > budget:
                raise ProviderBudgetExceeded(
                    f"Provider {window_name} hard budget would be exceeded by this submission"
                )
        con.execute(
            """INSERT INTO provider_cost_reservations
               (id,provider,service,operation,reserved_minor,currency,created_at,expires_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                reservation_id,
                governance._label(provider),
                governance._label(service),
                governance._label(operation, fallback="render"),
                estimate,
                currency,
                _iso(now),
                _iso(expires_at),
            ),
        )
    return reservation_id


def release_provider_budget(reservation_id: str) -> None:
    if not reservation_id:
        return
    with _connect() as con:
        _ensure_schema(con)
        con.execute("DELETE FROM provider_cost_reservations WHERE id=?", (reservation_id,))


def install_provider_budget_enforcement() -> None:
    """Install an opt-in pre-submission hard-budget boundary around ComfyUI renders."""

    from .creative_renderers import ComfyUIRenderer

    original = ComfyUIRenderer.submit
    if getattr(original, "__provider_budget_enforced__", False):
        return

    def budgeted_submit(self, variables):
        values = variables if isinstance(variables, dict) else {}
        operation = str(values.get("operation") or "render")
        reservation_id = reserve_provider_budget(
            provider="comfyui",
            service=self.kind,
            operation=operation,
        )
        try:
            submission = original(self, variables)
        except Exception:
            if reservation_id:
                release_provider_budget(reservation_id)
            raise

        if reservation_id:
            recorded = False
            try:
                governance = _governance()
                unit_name, units = governance._renderer_units(self.kind, values)
                governance.store.record_submission(
                    provider=submission.provider,
                    service=self.kind,
                    operation=operation,
                    job_ref=submission.prompt_id,
                    user_ref=str(values.get("user_id") or "") or None,
                    project_ref=str(values.get("project_name") or "") or None,
                    media_kind=self.kind,
                    unit_name=unit_name,
                    units=units,
                    source="hard_budget_renderer_submission",
                )
                recorded = True
            except Exception:
                # The provider has already accepted this job. Do not turn success into a retryable
                # member error that could duplicate spend. Keep the reservation until TTL expiry
                # so budget capacity remains conservatively withheld while metering recovers.
                recorded = False
            if recorded:
                release_provider_budget(reservation_id)
        return submission

    budgeted_submit.__provider_budget_enforced__ = True  # type: ignore[attr-defined]
    budgeted_submit.__wrapped__ = original  # type: ignore[attr-defined]
    ComfyUIRenderer.submit = budgeted_submit  # type: ignore[assignment]


__all__ = [
    "ProviderBudgetExceeded",
    "install_provider_budget_enforcement",
    "release_provider_budget",
    "reserve_provider_budget",
]
