from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from .cosmic_economy import EconomyError, VerifiedPaymentEvent, _fingerprint, _iso
from .cosmic_purchase_checkout import CheckoutBoundCosmicEconomy


class PersonalLimitCosmicEconomy(CheckoutBoundCosmicEconomy):
    """Canonical runtime safety layer for member caps and recipient Gift controls."""

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

                CREATE TABLE IF NOT EXISTS creator_gift_controls (
                    creator_recipient_id TEXT PRIMARY KEY,
                    receiving_enabled INTEGER NOT NULL DEFAULT 1 CHECK(receiving_enabled IN (0,1)),
                    reason TEXT,
                    updated_at TEXT NOT NULL,
                    updated_by TEXT NOT NULL
                );

                CREATE TRIGGER IF NOT EXISTS trg_gift_transaction_recipient_receiving_enabled
                BEFORE INSERT ON gift_transactions
                WHEN COALESCE(
                    (SELECT receiving_enabled FROM creator_gift_controls
                     WHERE creator_recipient_id=NEW.recipient_creator_id),
                    1
                ) = 0
                BEGIN
                    SELECT RAISE(ABORT, 'CREATOR_GIFT_RECEIVING_DISABLED');
                END;
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

    def set_creator_gift_receiving(
        self,
        creator_recipient_id: str,
        *,
        enabled: bool,
        actor: str,
        reason: str,
    ) -> dict[str, Any]:
        creator_recipient_id = (creator_recipient_id or "").strip()
        reason = (reason or "").strip()
        if not creator_recipient_id:
            raise EconomyError("INVALID_CREATOR_RECIPIENT", "Creator recipient ID is required.")
        if len(reason) < 3:
            raise EconomyError(
                "INVALID_CREATOR_GIFT_CONTROL",
                "Gift receiving availability change requires a reason.",
            )
        correlation_id = uuid4().hex
        now = _iso()
        with self._connect() as con:
            self._begin(con)
            con.execute(
                """INSERT INTO creator_gift_controls
                   (creator_recipient_id,receiving_enabled,reason,updated_at,updated_by)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(creator_recipient_id) DO UPDATE SET
                     receiving_enabled=excluded.receiving_enabled,
                     reason=excluded.reason,
                     updated_at=excluded.updated_at,
                     updated_by=excluded.updated_by""",
                (creator_recipient_id, int(enabled), reason, now, actor[:160]),
            )
            self._enqueue_locked(
                con,
                "economy.creator_gift_receiving_changed",
                "creator_recipient",
                creator_recipient_id,
                {
                    "creator_recipient_id": creator_recipient_id,
                    "receiving_enabled": enabled,
                    "reason": reason,
                    "actor": actor[:160],
                },
                correlation_id=correlation_id,
            )
            row = con.execute(
                "SELECT * FROM creator_gift_controls WHERE creator_recipient_id=?",
                (creator_recipient_id,),
            ).fetchone()
            con.commit()
        return dict(row)

    def creator_gift_receiving_state(self, creator_recipient_id: str) -> dict[str, Any]:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM creator_gift_controls WHERE creator_recipient_id=?",
                (creator_recipient_id,),
            ).fetchone()
        if row:
            return dict(row)
        return {
            "creator_recipient_id": creator_recipient_id,
            "receiving_enabled": 1,
            "reason": None,
            "updated_at": None,
            "updated_by": None,
        }

    def apply_verified_payment_event(self, event: VerifiedPaymentEvent) -> dict:
        """Enforce event identity and a fail-closed provider payment state machine."""
        if not event.verified:
            return super().apply_verified_payment_event(event)

        payload_hash = _fingerprint(
            {
                "provider": event.provider,
                "provider_event_id": event.provider_event_id,
                "provider_payment_id": event.provider_payment_id,
                "purchase_id": event.purchase_id,
                "event_type": event.event_type,
                "occurred_at": event.occurred_at,
            }
        )
        known_events = {
            "confirmed",
            "failed",
            "cancelled",
            "refunded",
            "chargeback",
            "dispute_won",
        }
        with self._connect() as con:
            seen = con.execute(
                """SELECT event_type,purchase_id,payload_hash FROM payment_webhook_events
                   WHERE provider=? AND provider_event_id=?""",
                (event.provider, event.provider_event_id),
            ).fetchone()
            purchase = con.execute(
                "SELECT id,user_id,status FROM coin_purchases WHERE id=?",
                (event.purchase_id,),
            ).fetchone()
            prior_chargeback = None
            prior_dispute_won = None
            if purchase:
                prior_chargeback = con.execute(
                    """SELECT 1 FROM payment_webhook_events
                       WHERE purchase_id=? AND event_type='chargeback' LIMIT 1""",
                    (event.purchase_id,),
                ).fetchone()
                prior_dispute_won = con.execute(
                    """SELECT 1 FROM payment_webhook_events
                       WHERE purchase_id=? AND event_type='dispute_won' LIMIT 1""",
                    (event.purchase_id,),
                ).fetchone()

        if seen:
            if seen["payload_hash"] != payload_hash:
                self._record_operational_event(
                    event_type="economy.payment_event_id_conflict",
                    user_id=purchase["user_id"] if purchase else None,
                    details={
                        "provider": event.provider,
                        "provider_event_id": event.provider_event_id,
                        "stored_purchase_id": seen["purchase_id"],
                        "presented_purchase_id": event.purchase_id,
                        "presented_event_type": event.event_type,
                    },
                )
                raise EconomyError(
                    "PAYMENT_EVENT_ID_REUSED",
                    "Payment provider event ID was reused with different financial data.",
                    status_code=409,
                    details={
                        "provider": event.provider,
                        "provider_event_id": event.provider_event_id,
                    },
                )
            return super().apply_verified_payment_event(event)

        if event.event_type not in known_events or not purchase:
            return super().apply_verified_payment_event(event)

        allowed = {
            "pending": {"confirmed", "failed", "cancelled"},
            "confirmed": {"confirmed", "refunded", "chargeback"},
            "failed": {"failed"},
            "cancelled": {"cancelled"},
            "refunded": {"refunded"},
            "chargeback": {"chargeback", "dispute_won"},
        }
        current_status = str(purchase["status"])
        if event.event_type not in allowed.get(current_status, set()):
            self._record_operational_event(
                event_type="economy.payment_state_conflict",
                user_id=purchase["user_id"],
                details={
                    "purchase_id": event.purchase_id,
                    "current_status": current_status,
                    "presented_event_type": event.event_type,
                    "provider_event_id": event.provider_event_id,
                },
            )
            raise EconomyError(
                "PAYMENT_STATE_CONFLICT",
                "Payment event is not valid for the purchase's current state.",
                status_code=409,
                details={
                    "purchase_id": event.purchase_id,
                    "current_status": current_status,
                    "event_type": event.event_type,
                },
            )

        if (
            current_status == "confirmed"
            and event.event_type == "chargeback"
            and prior_chargeback
            and prior_dispute_won
        ):
            self._record_operational_event(
                event_type="economy.payment_dispute_cycle_review",
                user_id=purchase["user_id"],
                details={
                    "purchase_id": event.purchase_id,
                    "provider_event_id": event.provider_event_id,
                },
            )
            raise EconomyError(
                "PAYMENT_DISPUTE_CYCLE_REQUIRES_REVIEW",
                "A second chargeback after dispute recovery requires manual financial review.",
                status_code=409,
                details={"purchase_id": event.purchase_id},
            )

        return super().apply_verified_payment_event(event)

    def send_gift(self, *, sender_user_id: str, idempotency_key: str, **kwargs: Any) -> dict:
        try:
            return super().send_gift(
                sender_user_id=sender_user_id,
                idempotency_key=idempotency_key,
                **kwargs,
            )
        except sqlite3.IntegrityError as exc:
            if "CREATOR_GIFT_RECEIVING_DISABLED" in str(exc):
                creator_recipient_id = kwargs.get("recipient_creator_id")
                self._record_operational_event(
                    event_type="economy.creator_receiving_block",
                    user_id=sender_user_id,
                    details={"creator_recipient_id": creator_recipient_id},
                )
                raise EconomyError(
                    "CREATOR_GIFT_RECEIVING_DISABLED",
                    "This creator is not currently accepting Shared Sky LIVE Gifts.",
                    status_code=403,
                    details={"creator_recipient_id": creator_recipient_id},
                ) from exc
            raise
