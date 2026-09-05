from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from .cosmic_economy import EconomyError, _iso
from .cosmic_economy_command_idempotency import CommandBoundCosmicEconomy


class PersonalLimitCosmicEconomy(CommandBoundCosmicEconomy):
    """Adds member-controlled lower spending caps without weakening platform policy."""

    def _init_schema(self) -> None:
        super()._init_schema()
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS personal_spending_limits (
                    user_id TEXT PRIMARY KEY,
                    daily_hard_limit INTEGER,
                    weekly_hard_limit INTEGER,
                    monthly_hard_limit INTEGER,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS personal_spending_limit_changes (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    previous_json TEXT NOT NULL,
                    new_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_personal_spending_limit_changes_user_time
                    ON personal_spending_limit_changes(user_id, created_at DESC);
                """
            )

    @staticmethod
    def _validate_personal_limit(value: int | None) -> None:
        if value is None:
            return
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise EconomyError(
                "INVALID_PERSONAL_SPENDING_LIMIT",
                "Personal spending limits must be non-negative integer Coin quantities or null.",
            )

    def set_personal_spending_limits(
        self,
        user_id: str,
        *,
        daily_hard_limit: int | None = None,
        weekly_hard_limit: int | None = None,
        monthly_hard_limit: int | None = None,
    ) -> dict[str, Any]:
        values = {
            "daily": daily_hard_limit,
            "weekly": weekly_hard_limit,
            "monthly": monthly_hard_limit,
        }
        for value in values.values():
            self._validate_personal_limit(value)

        correlation_id = uuid4().hex
        with self._connect() as con:
            self._begin(con)
            platform = con.execute(
                "SELECT * FROM account_spending_limits WHERE user_id=?",
                (user_id,),
            ).fetchone()
            if platform:
                for period, value in values.items():
                    platform_limit = platform[f"{period}_hard_limit"]
                    if (
                        value is not None
                        and platform_limit is not None
                        and value > int(platform_limit)
                    ):
                        raise EconomyError(
                            "PERSONAL_LIMIT_EXCEEDS_PLATFORM_LIMIT",
                            "A personal limit cannot exceed the current platform hard limit.",
                            status_code=409,
                            details={
                                "period": period,
                                "platform_limit_coins": int(platform_limit),
                                "requested_personal_limit_coins": value,
                            },
                        )
            previous = con.execute(
                "SELECT * FROM personal_spending_limits WHERE user_id=?",
                (user_id,),
            ).fetchone()
            now = _iso()
            con.execute(
                """INSERT INTO personal_spending_limits
                   (user_id,daily_hard_limit,weekly_hard_limit,monthly_hard_limit,updated_at)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(user_id) DO UPDATE SET
                     daily_hard_limit=excluded.daily_hard_limit,
                     weekly_hard_limit=excluded.weekly_hard_limit,
                     monthly_hard_limit=excluded.monthly_hard_limit,
                     updated_at=excluded.updated_at""",
                (
                    user_id,
                    daily_hard_limit,
                    weekly_hard_limit,
                    monthly_hard_limit,
                    now,
                ),
            )
            row = con.execute(
                "SELECT * FROM personal_spending_limits WHERE user_id=?",
                (user_id,),
            ).fetchone()
            con.execute(
                """INSERT INTO personal_spending_limit_changes
                   (id,user_id,previous_json,new_json,created_at) VALUES (?,?,?,?,?)""",
                (
                    uuid4().hex,
                    user_id,
                    json.dumps(dict(previous) if previous else {}, sort_keys=True),
                    json.dumps(dict(row), sort_keys=True),
                    now,
                ),
            )
            self._enqueue_locked(
                con,
                "economy.personal_spending_limits_changed",
                "coin_account",
                user_id,
                {
                    "daily_hard_limit": daily_hard_limit,
                    "weekly_hard_limit": weekly_hard_limit,
                    "monthly_hard_limit": monthly_hard_limit,
                },
                correlation_id=correlation_id,
            )
            con.commit()
        return dict(row)

    def personal_spending_limits(self, user_id: str) -> dict[str, Any] | None:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM personal_spending_limits WHERE user_id=?",
                (user_id,),
            ).fetchone()
        return dict(row) if row else None

    def personal_spending_limit_history(self, user_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        with self._connect() as con:
            rows = con.execute(
                """SELECT * FROM personal_spending_limit_changes
                   WHERE user_id=? ORDER BY created_at DESC LIMIT ?""",
                (user_id, limit),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["previous"] = json.loads(item.pop("previous_json"))
            item["new"] = json.loads(item.pop("new_json"))
            result.append(item)
        return result

    def _check_spending_locked(self, con, user_id: str, account_id: str, new_cost: int) -> None:
        super()._check_spending_locked(con, user_id, account_id, new_cost)
        personal = con.execute(
            "SELECT * FROM personal_spending_limits WHERE user_id=?",
            (user_id,),
        ).fetchone()
        if not personal:
            return
        totals = self._spend_totals_locked(con, account_id, datetime.now(timezone.utc))
        for period in ("daily", "weekly", "monthly"):
            hard = personal[f"{period}_hard_limit"]
            if hard is not None and totals[period] + new_cost > int(hard):
                raise EconomyError(
                    "PERSONAL_SPENDING_LIMIT_EXCEEDED",
                    f"Your personal {period} Cosmic Creation Coin spending limit would be exceeded.",
                    status_code=403,
                    details={
                        "period": period,
                        "personal_limit_coins": int(hard),
                        "spent_coins": totals[period],
                        "attempted_coins": new_cost,
                    },
                )

    def spending_state(self, user_id: str) -> dict[str, Any]:
        state = super().spending_state(user_id)
        personal = self.personal_spending_limits(user_id)
        state["personal_limits"] = personal
        effective: dict[str, int | None] = {}
        remaining: dict[str, int | None] = {}
        platform = state.get("limits") or {}
        spent = state.get("spent") or {}
        for period in ("daily", "weekly", "monthly"):
            platform_limit = platform.get(f"{period}_hard_limit")
            personal_limit = personal.get(f"{period}_hard_limit") if personal else None
            candidates = [
                int(value)
                for value in (platform_limit, personal_limit)
                if value is not None
            ]
            hard = min(candidates) if candidates else None
            effective[period] = hard
            remaining[period] = (
                max(0, hard - int(spent.get(period, 0))) if hard is not None else None
            )
        state["effective_hard_limits"] = effective
        state["remaining_hard_limit"] = remaining
        return state
