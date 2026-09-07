from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .accounts import AccountStore

TIER2_PLAN_ID = "base"
UNLIMITED_PRO_PLAN_ID = "pro"
TIER2_DAILY_LIMIT = 5

ELIGIBLE_OPERATIONS = frozenset(
    {
        "music_create",
        "music_edit",
        "video_create",
        "video_edit",
        "game_create",
        "game_edit",
    }
)
_ACTIVE_STATES = ("reserved", "completed")


@dataclass(frozen=True)
class Tier2Admission:
    reservation_id: str | None
    user_id: str
    plan_id: str
    operation: str
    request_key: str
    utc_day: str
    state: str
    limit: int | None
    used: int | None
    remaining: int | None
    unlimited: bool
    membership_effect: str = "none"
    esp_role_effect: str = "none"

    def to_dict(self) -> dict:
        return asdict(self)


class Tier2DailyMeter:
    """Atomic server-side admission for Tier 2's cross-studio daily allowance.

    This meter deliberately does not decide Creation Coin prices or mutate membership/ESP roles.
    Callers must perform normal request validation and safety checks before reserving a slot, then
    release the reservation when an eligible provider/job submission definitively fails.
    """

    def __init__(self, db_path: str | Path | None = None):
        if db_path is None:
            db_path = AccountStore().db_path
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path, timeout=30)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _init_schema(self) -> None:
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS tier2_daily_operations (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    utc_day TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    request_key TEXT NOT NULL,
                    state TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(user_id, utc_day, request_key)
                );
                CREATE INDEX IF NOT EXISTS idx_tier2_daily_operations_count
                    ON tier2_daily_operations(user_id, utc_day, state);
                """
            )

    @staticmethod
    def _normalize_operation(operation: str) -> str:
        value = str(operation or "").strip().lower()
        if value not in ELIGIBLE_OPERATIONS:
            raise ValueError("Unsupported Tier 2 eligible operation")
        return value

    @staticmethod
    def _normalize_request_key(request_key: str) -> str:
        value = str(request_key or "").strip()
        if not value or len(value) > 180:
            raise ValueError("A bounded idempotency request key is required")
        return value

    @staticmethod
    def _now(now: datetime | None = None) -> datetime:
        value = now or datetime.now(timezone.utc)
        if value.tzinfo is None:
            raise ValueError("Tier 2 metering requires a timezone-aware timestamp")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _used(con: sqlite3.Connection, user_id: str, utc_day: str) -> int:
        row = con.execute(
            """SELECT COUNT(*) AS n FROM tier2_daily_operations
               WHERE user_id=? AND utc_day=? AND state IN ('reserved','completed')""",
            (user_id, utc_day),
        ).fetchone()
        return int(row["n"] if row else 0)

    def usage(self, user_id: str, plan_id: str, *, now: datetime | None = None) -> dict:
        uid = str(user_id or "").strip()
        if not uid:
            raise ValueError("Authenticated user id is required")
        plan = str(plan_id or "").strip().lower()
        utc_day = self._now(now).date().isoformat()
        if plan == UNLIMITED_PRO_PLAN_ID:
            return {
                "plan": plan,
                "limit": None,
                "used": None,
                "remaining": None,
                "unlimited": True,
                "timezone": "UTC",
                "membership_effect": "none",
                "esp_role_effect": "none",
            }
        if plan != TIER2_PLAN_ID:
            return {
                "plan": plan,
                "limit": 0,
                "used": 0,
                "remaining": 0,
                "unlimited": False,
                "timezone": "UTC",
                "requires_separate_entitlement": True,
                "membership_effect": "none",
                "esp_role_effect": "none",
            }
        with self._connect() as con:
            used = self._used(con, uid, utc_day)
        return {
            "plan": plan,
            "limit": TIER2_DAILY_LIMIT,
            "used": used,
            "remaining": max(0, TIER2_DAILY_LIMIT - used),
            "unlimited": False,
            "timezone": "UTC",
            "membership_effect": "none",
            "esp_role_effect": "none",
        }

    def reserve(
        self,
        user_id: str,
        plan_id: str,
        operation: str,
        request_key: str,
        *,
        now: datetime | None = None,
    ) -> Tier2Admission:
        uid = str(user_id or "").strip()
        if not uid:
            raise ValueError("Authenticated user id is required")
        plan = str(plan_id or "").strip().lower()
        op = self._normalize_operation(operation)
        key = self._normalize_request_key(request_key)
        instant = self._now(now)
        utc_day = instant.date().isoformat()
        iso = instant.isoformat()

        if plan == UNLIMITED_PRO_PLAN_ID:
            return Tier2Admission(
                reservation_id=None,
                user_id=uid,
                plan_id=plan,
                operation=op,
                request_key=key,
                utc_day=utc_day,
                state="unlimited",
                limit=None,
                used=None,
                remaining=None,
                unlimited=True,
            )
        if plan != TIER2_PLAN_ID:
            raise PermissionError(
                "This membership does not receive Tier 2 daily admission; use its separate entitlement path"
            )

        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            existing = con.execute(
                """SELECT * FROM tier2_daily_operations
                   WHERE user_id=? AND utc_day=? AND request_key=?""",
                (uid, utc_day, key),
            ).fetchone()
            if existing is not None:
                if existing["operation"] != op:
                    raise ValueError("Idempotency request key is already bound to another operation")
                if existing["state"] == "released":
                    used = self._used(con, uid, utc_day)
                    if used >= TIER2_DAILY_LIMIT:
                        raise PermissionError("Tier 2 daily eligible-operation allowance has been reached")
                    con.execute(
                        "UPDATE tier2_daily_operations SET state='reserved', updated_at=? WHERE id=?",
                        (iso, existing["id"]),
                    )
                    used += 1
                    state = "reserved"
                else:
                    used = self._used(con, uid, utc_day)
                    state = str(existing["state"])
                return Tier2Admission(
                    reservation_id=str(existing["id"]),
                    user_id=uid,
                    plan_id=plan,
                    operation=op,
                    request_key=key,
                    utc_day=utc_day,
                    state=state,
                    limit=TIER2_DAILY_LIMIT,
                    used=used,
                    remaining=max(0, TIER2_DAILY_LIMIT - used),
                    unlimited=False,
                )

            used = self._used(con, uid, utc_day)
            if used >= TIER2_DAILY_LIMIT:
                raise PermissionError("Tier 2 daily eligible-operation allowance has been reached")
            reservation_id = uuid4().hex
            con.execute(
                """INSERT INTO tier2_daily_operations
                   (id,user_id,utc_day,operation,request_key,state,created_at,updated_at)
                   VALUES (?,?,?,?,?,'reserved',?,?)""",
                (reservation_id, uid, utc_day, op, key, iso, iso),
            )
            used += 1
            return Tier2Admission(
                reservation_id=reservation_id,
                user_id=uid,
                plan_id=plan,
                operation=op,
                request_key=key,
                utc_day=utc_day,
                state="reserved",
                limit=TIER2_DAILY_LIMIT,
                used=used,
                remaining=max(0, TIER2_DAILY_LIMIT - used),
                unlimited=False,
            )

    def _transition(self, user_id: str, reservation_id: str, target: str) -> dict:
        uid = str(user_id or "").strip()
        rid = str(reservation_id or "").strip()
        if not uid or not rid:
            raise ValueError("User and reservation ids are required")
        if target not in {"completed", "released"}:
            raise ValueError("Unsupported Tier 2 reservation transition")
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                "SELECT * FROM tier2_daily_operations WHERE id=? AND user_id=?",
                (rid, uid),
            ).fetchone()
            if row is None:
                raise KeyError("Tier 2 reservation not found")
            current = str(row["state"])
            if current == target:
                return dict(row)
            if current != "reserved":
                raise ValueError(f"Cannot transition Tier 2 reservation from {current} to {target}")
            con.execute(
                "UPDATE tier2_daily_operations SET state=?, updated_at=? WHERE id=? AND user_id=?",
                (target, now, rid, uid),
            )
            updated = con.execute(
                "SELECT * FROM tier2_daily_operations WHERE id=? AND user_id=?",
                (rid, uid),
            ).fetchone()
            return dict(updated)

    def complete(self, user_id: str, reservation_id: str) -> dict:
        return self._transition(user_id, reservation_id, "completed")

    def release(self, user_id: str, reservation_id: str) -> dict:
        return self._transition(user_id, reservation_id, "released")


__all__ = [
    "ELIGIBLE_OPERATIONS",
    "TIER2_DAILY_LIMIT",
    "Tier2Admission",
    "Tier2DailyMeter",
]
