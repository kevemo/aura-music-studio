from __future__ import annotations

import math
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4


@dataclass(frozen=True)
class RenderBudget:
    daily_units: int
    burst_jobs: int
    max_request_units: int


_DEFAULTS = {
    "free": RenderBudget(daily_units=8, burst_jobs=4, max_request_units=4),
    "base": RenderBudget(daily_units=48, burst_jobs=12, max_request_units=16),
    "pro": RenderBudget(daily_units=192, burst_jobs=30, max_request_units=64),
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat()


def render_units(kind: str, *, width: int, height: int, frames: int) -> int:
    """Estimate relative GPU load in 1024x1024 image / 121-frame video units."""
    pixels = max(1, int(width)) * max(1, int(height))
    base_pixels = 1024 * 1024
    if kind == "video":
        return max(1, math.ceil((pixels * max(1, int(frames))) / (base_pixels * 121)))
    return max(1, math.ceil(pixels / base_pixels))


def budget_for(plan_id: str) -> RenderBudget:
    key = (plan_id or "").strip().lower()
    if key not in _DEFAULTS:
        raise PermissionError("Unknown membership plan cannot submit external render work")
    default = _DEFAULTS[key]
    prefix = f"CREATIVE_RENDER_{key.upper()}_"

    def value(name: str, fallback: int) -> int:
        raw = os.getenv(prefix + name)
        if raw is None:
            return fallback
        try:
            parsed = int(raw)
        except ValueError as exc:
            raise RuntimeError(f"Invalid {prefix + name} configuration") from exc
        if parsed < 1:
            raise RuntimeError(f"Invalid {prefix + name} configuration")
        return parsed

    return RenderBudget(
        daily_units=value("DAILY_UNITS", default.daily_units),
        burst_jobs=value("BURST_JOBS", default.burst_jobs),
        max_request_units=value("MAX_REQUEST_UNITS", default.max_request_units),
    )


class CreativeRenderResourceStore:
    """Atomic fair-use ledger for expensive external image/video renderer submissions."""

    def __init__(self, db_path: str | Path | None = None):
        configured = db_path or os.getenv("LSS_DB_PATH") or "data/live_sound_studio.sqlite3"
        self.db_path = Path(configured)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _init_schema(self) -> None:
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS creative_render_resource_events (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    plan_id TEXT NOT NULL,
                    project_name TEXT NOT NULL,
                    directive_id TEXT NOT NULL,
                    media_kind TEXT NOT NULL,
                    units INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_creative_render_resource_user_time
                    ON creative_render_resource_events(user_id, created_at DESC);
                """
            )

    def reserve(
        self,
        *,
        user_id: str,
        plan_id: str,
        project_name: str,
        directive_id: str,
        media_kind: str,
        width: int,
        height: int,
        frames: int,
        now: datetime | None = None,
    ) -> dict:
        if not user_id:
            raise PermissionError("Authenticated member identity is required")
        budget = budget_for(plan_id)
        units = render_units(media_kind, width=width, height=height, frames=frames)
        if units > budget.max_request_units:
            raise PermissionError(
                f"Render request exceeds the {plan_id} fair-use per-request compute ceiling ({units}>{budget.max_request_units} units)"
            )

        current = now or _utcnow()
        day_start = current.replace(hour=0, minute=0, second=0, microsecond=0)
        burst_start = current - timedelta(minutes=10)
        reservation_id = uuid4().hex

        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            daily = con.execute(
                "SELECT COALESCE(SUM(units),0) AS n FROM creative_render_resource_events WHERE user_id=? AND created_at>=?",
                (user_id, _iso(day_start)),
            ).fetchone()["n"]
            burst = con.execute(
                "SELECT COUNT(*) AS n FROM creative_render_resource_events WHERE user_id=? AND created_at>=?",
                (user_id, _iso(burst_start)),
            ).fetchone()["n"]
            if int(burst) >= budget.burst_jobs:
                raise PermissionError("Creative renderer burst limit reached; retry after the rolling 10-minute window clears")
            if int(daily) + units > budget.daily_units:
                raise PermissionError("Daily creative renderer fair-use compute allowance has been reached")
            con.execute(
                """INSERT INTO creative_render_resource_events
                   (id,user_id,plan_id,project_name,directive_id,media_kind,units,created_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    reservation_id,
                    user_id,
                    plan_id,
                    project_name,
                    directive_id,
                    media_kind,
                    units,
                    _iso(current),
                ),
            )

        return {
            "reservation_id": reservation_id,
            "units": units,
            "daily_units_limit": budget.daily_units,
            "daily_units_used": int(daily) + units,
            "burst_jobs_limit": budget.burst_jobs,
            "max_request_units": budget.max_request_units,
            "window": "utc_day_and_rolling_10_minutes",
            "billing_charge": False,
            "grants_esp_role_or_permission": False,
        }

    def cancel(self, reservation_id: str, *, user_id: str) -> bool:
        with self._connect() as con:
            cursor = con.execute(
                "DELETE FROM creative_render_resource_events WHERE id=? AND user_id=?",
                (reservation_id, user_id),
            )
        return cursor.rowcount == 1


store = CreativeRenderResourceStore()

__all__ = ["CreativeRenderResourceStore", "RenderBudget", "budget_for", "render_units", "store"]
