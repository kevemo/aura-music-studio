from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Callable
from datetime import datetime, timezone

from .shared_sky_battle_schema_1 import SCHEMA_SQL as SCHEMA_1
from .shared_sky_battle_schema_2 import SCHEMA_SQL as SCHEMA_2
from .shared_sky_battle_schema_3 import SCHEMA_SQL as SCHEMA_3
from .shared_sky_battle_schema_4 import SCHEMA_SQL as SCHEMA_4
from .shared_sky_battle_types import (
    ACTIVE_PARTICIPANT_STATES, AUTHORITY_ROLES, MAX_PARTICIPANTS, BattleDomainError,
    _bounded, _stable_json, iso, utc_now,
)

class BattleStoreBase:
    """Shared persistence, capability and audit infrastructure."""
    def __init__(
        self,
        db_path: str | os.PathLike[str],
        *,
        clock: Callable[[], datetime] = utc_now,
        transport_capacity: Callable[[str], int] | None = None,
        participant_eligibility: Callable[[str], tuple[bool, str]] | None = None,
        reconnect_grace_seconds: int = 45,
    ):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self.transport_capacity = transport_capacity or (
            lambda _live_session_id: int(os.getenv("SHARED_SKY_MULTIHOST_MAX_PARTICIPANTS", "0") or 0)
        )
        self.participant_eligibility = participant_eligibility or (lambda _user_id: (False, "Creator eligibility adapter is unavailable"))
        self.reconnect_grace_seconds = max(5, min(600, int(reconnect_grace_seconds)))
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path, timeout=15)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA busy_timeout=15000")
        return con

    def _now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def _init_schema(self) -> None:
        with self._connect() as con:
            con.executescript(SCHEMA_1)
            con.executescript(SCHEMA_2)
            con.executescript(SCHEMA_3)
            con.executescript(SCHEMA_4)

    def _live_session(self, con: sqlite3.Connection, live_session_id: str) -> sqlite3.Row:
        try:
            row = con.execute(
                "SELECT * FROM shared_sky_broadcasts WHERE id=?", (live_session_id,)
            ).fetchone()
        except sqlite3.OperationalError as exc:
            raise BattleDomainError(
                "capability_unavailable",
                "Canonical Shared Sky broadcast storage is unavailable",
                status_code=503,
            ) from exc
        if not row:
            raise BattleDomainError("live_session_not_found", "Shared Sky live session not found", status_code=404)
        return row

    def _live_is_active(self, row: sqlite3.Row) -> bool:
        return str(row["state"] or "") in {"ready", "starting", "live", "degraded", "reconnecting"}

    def _require_eligible(self, user_id: str) -> None:
        try:
            allowed, reason = self.participant_eligibility(user_id)
        except Exception as exc:
            raise BattleDomainError("capability_unavailable", "Creator eligibility could not be verified", status_code=503) from exc
        if not allowed:
            raise BattleDomainError("creator_ineligible", _bounded(reason, 500) or "Creator is not eligible for multi-host participation", status_code=403)

    def _capacity(self, live_session_id: str) -> int:
        try:
            transport_limit = int(self.transport_capacity(live_session_id))
        except Exception:
            transport_limit = 0
        if transport_limit <= 0:
            raise BattleDomainError(
                "capability_unavailable",
                "Current transport path has not confirmed multi-host capacity",
                status_code=503,
            )
        return max(1, min(MAX_PARTICIPANTS, transport_limit))

    def _participant(self, con: sqlite3.Connection, participant_id: str) -> sqlite3.Row:
        row = con.execute("SELECT * FROM shared_sky_participants WHERE id=?", (participant_id,)).fetchone()
        if not row:
            raise BattleDomainError("participant_not_found", "Participant not found", status_code=404)
        return row

    def _participant_for_user(self, con: sqlite3.Connection, live_session_id: str, user_id: str) -> sqlite3.Row | None:
        return con.execute(
            "SELECT * FROM shared_sky_participants WHERE live_session_id=? AND user_id=?",
            (live_session_id, user_id),
        ).fetchone()

    def _assert_authority(
        self,
        con: sqlite3.Connection,
        live_session_id: str,
        actor_user_id: str,
        *,
        roles: set[str] | None = None,
    ) -> sqlite3.Row | None:
        live = self._live_session(con, live_session_id)
        if str(live["user_id"]) == actor_user_id:
            return self._participant_for_user(con, live_session_id, actor_user_id)
        participant = self._participant_for_user(con, live_session_id, actor_user_id)
        allowed = roles or AUTHORITY_ROLES
        if participant and participant["join_state"] in ACTIVE_PARTICIPANT_STATES and participant["role"] in allowed:
            return participant
        raise BattleDomainError("unauthorised", "Action requires host, producer or moderator authority", status_code=403)

    def _audit(
        self,
        con: sqlite3.Connection,
        live_session_id: str,
        action: str,
        *,
        actor_user_id: str | None = None,
        battle_id: str | None = None,
        participant_id: str | None = None,
        details: dict | None = None,
        correlation_id: str = "",
    ) -> None:
        stamp = iso(self._now())
        con.execute(
            """INSERT INTO shared_sky_battle_audit
               (live_session_id,battle_id,participant_id,actor_user_id,action,details_json,correlation_id,created_at)
               VALUES(?,?,?,?,?,?,?,?)""",
            (
                live_session_id,
                battle_id,
                participant_id,
                actor_user_id,
                action,
                _stable_json(details or {}),
                _bounded(correlation_id, 160),
                stamp,
            ),
        )
        if battle_id:
            con.execute(
                "INSERT INTO shared_sky_battle_events(battle_id,event_type,participant_id,correlation_id,created_at) VALUES(?,?,?,?,?)",
                (battle_id, _bounded(action, 120), participant_id, _bounded(correlation_id, 160), stamp),
            )

    def _next_slot(self, con: sqlite3.Connection, live_session_id: str) -> int:
        capacity = self._capacity(live_session_id)
        used = {
            int(row[0])
            for row in con.execute(
                """SELECT slot_index FROM shared_sky_participants
                   WHERE live_session_id=? AND join_state IN ('lobby','connected','ready','live','reconnecting')
                     AND slot_index IS NOT NULL""",
                (live_session_id,),
            ).fetchall()
        }
        for slot in range(capacity):
            if slot not in used:
                return slot
        raise BattleDomainError("participant_capacity_reached", f"Participant capacity is {capacity}")
