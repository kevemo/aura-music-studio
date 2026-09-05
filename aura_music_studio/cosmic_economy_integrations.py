from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from .cosmic_economy import (
    BaselineGiftRiskEvaluator,
    CosmicEconomy,
    EconomyEligibilityDirectory,
    EconomyError,
    GiftRiskEvaluator,
    LiveSessionDirectory,
    UnavailableEconomyEligibilityDirectory,
    UnavailableLiveSessionDirectory,
    VerifiedPaymentEvent,
    _iso,
)


class IntegratedCosmicEconomy(CosmicEconomy):
    """Canonical runtime service with financial integration hardening.

    The underlying ledger/schema remains ``CosmicEconomy``. This subclass adds migration-safe
    global idempotency ownership, bounded server-side purchase/Gift request admission and the
    compatibility seams used while shared Chat 1/2/10 contracts land. Rate admission is stored
    in the same durable database so multiple workers sharing the canonical store cannot each
    maintain an independent in-memory allowance.
    """

    def _init_schema(self) -> None:
        super()._init_schema()
        with self._connect() as con:
            duplicate_purchase = con.execute(
                """SELECT idempotency_key, COUNT(DISTINCT user_id) AS owners
                   FROM coin_purchases GROUP BY idempotency_key HAVING owners > 1 LIMIT 1"""
            ).fetchone()
            duplicate_gift = con.execute(
                """SELECT idempotency_key, COUNT(DISTINCT sender_user_id) AS owners
                   FROM gift_transactions GROUP BY idempotency_key HAVING owners > 1 LIMIT 1"""
            ).fetchone()
            if duplicate_purchase or duplicate_gift:
                raise EconomyError(
                    "IDEMPOTENCY_MIGRATION_CONFLICT",
                    "Legacy economy data contains an idempotency key bound to multiple accounts; manual financial review is required.",
                    status_code=503,
                )
            con.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_coin_purchase_idempotency_global ON coin_purchases(idempotency_key)"
            )
            con.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_gift_idempotency_global ON gift_transactions(idempotency_key)"
            )
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS economy_rate_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    occurred_at_epoch INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_economy_rate_events_scope
                    ON economy_rate_events(user_id, action, occurred_at_epoch);

                CREATE TABLE IF NOT EXISTS economy_rate_idempotency (
                    user_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    occurred_at_epoch INTEGER NOT NULL,
                    PRIMARY KEY(user_id, action, idempotency_key)
                );
                CREATE INDEX IF NOT EXISTS idx_economy_rate_idempotency_time
                    ON economy_rate_idempotency(occurred_at_epoch);

                CREATE TABLE IF NOT EXISTS economy_operational_events (
                    id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    user_id TEXT,
                    details_json TEXT NOT NULL,
                    occurred_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_economy_operational_events_time
                    ON economy_operational_events(occurred_at DESC);
                CREATE INDEX IF NOT EXISTS idx_economy_operational_events_user
                    ON economy_operational_events(user_id, occurred_at DESC);
                """
            )

    @staticmethod
    def _configured_positive_int(name: str, default: int) -> int:
        raw = (os.getenv(name) or "").strip()
        if not raw:
            return default
        try:
            value = int(raw)
        except ValueError as exc:
            raise EconomyError(
                "ECONOMY_RATE_LIMIT_CONFIG_INVALID",
                f"{name} must be a positive integer.",
                status_code=503,
            ) from exc
        if value < 1:
            raise EconomyError(
                "ECONOMY_RATE_LIMIT_CONFIG_INVALID",
                f"{name} must be a positive integer.",
                status_code=503,
            )
        return value

    def _record_operational_event(
        self,
        *,
        event_type: str,
        user_id: str | None,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Persist non-financial evidence about rejected/blocked economy actions."""
        with self._connect() as con:
            con.execute(
                """INSERT INTO economy_operational_events
                   (id,event_type,user_id,details_json,occurred_at) VALUES (?,?,?,?,?)""",
                (
                    uuid4().hex,
                    event_type[:160],
                    user_id,
                    json.dumps(details or {}, sort_keys=True, separators=(",", ":")),
                    _iso(),
                ),
            )

    def operational_events(
        self,
        *,
        event_type: str | None = None,
        user_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        clauses: list[str] = []
        params: list[Any] = []
        if event_type:
            clauses.append("event_type=?")
            params.append(event_type)
        if user_id:
            clauses.append("user_id=?")
            params.append(user_id)
        sql = "SELECT * FROM economy_operational_events"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY occurred_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as con:
            rows = con.execute(sql, params).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["details"] = json.loads(item.pop("details_json"))
            result.append(item)
        return result

    def _consume_rate_limit(
        self,
        *,
        user_id: str,
        action: str,
        idempotency_key: str,
        limit_env: str,
        default_limit: int,
    ) -> None:
        """Atomically admit one new financial command for this account/action.

        The durable idempotency reservation is created in the same transaction as the rate event.
        Concurrent retries carrying the same key therefore share one admission slot even before
        the financial transaction itself has committed.
        """
        limit = self._configured_positive_int(limit_env, default_limit)
        window = self._configured_positive_int("LSS_ECONOMY_RATE_WINDOW_SECONDS", 60)
        now = int(time.time())
        cutoff = now - window
        with self._connect() as con:
            self._begin(con)
            con.execute(
                "DELETE FROM economy_rate_events WHERE occurred_at_epoch<=?",
                (cutoff,),
            )
            con.execute(
                "DELETE FROM economy_rate_idempotency WHERE occurred_at_epoch<=?",
                (cutoff,),
            )
            seen = con.execute(
                """SELECT 1 FROM economy_rate_idempotency
                   WHERE user_id=? AND action=? AND idempotency_key=?""",
                (user_id, action, idempotency_key),
            ).fetchone()
            if seen:
                con.commit()
                return
            rows = con.execute(
                """SELECT occurred_at_epoch FROM economy_rate_events
                   WHERE user_id=? AND action=? AND occurred_at_epoch>?
                   ORDER BY occurred_at_epoch ASC""",
                (user_id, action, cutoff),
            ).fetchall()
            if len(rows) >= limit:
                retry_after = max(1, int(rows[0]["occurred_at_epoch"]) + window - now + 1)
                con.execute(
                    """INSERT INTO economy_operational_events
                       (id,event_type,user_id,details_json,occurred_at) VALUES (?,?,?,?,?)""",
                    (
                        uuid4().hex,
                        "economy.rate_limit_blocked",
                        user_id,
                        json.dumps(
                            {
                                "action": action,
                                "retry_after_seconds": retry_after,
                                "limit": limit,
                                "window_seconds": window,
                            },
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                        _iso(),
                    ),
                )
                con.commit()
                raise EconomyError(
                    "RATE_LIMITED",
                    "Too many financial requests. Try again after the retry period.",
                    status_code=429,
                    details={"retry_after_seconds": retry_after, "action": action},
                )
            con.execute(
                "INSERT INTO economy_rate_events(user_id,action,occurred_at_epoch) VALUES (?,?,?)",
                (user_id, action, now),
            )
            con.execute(
                """INSERT INTO economy_rate_idempotency
                   (user_id,action,idempotency_key,occurred_at_epoch) VALUES (?,?,?,?)""",
                (user_id, action, idempotency_key, now),
            )
            con.commit()

    def create_purchase(self, *, user_id: str, idempotency_key: str, **kwargs: Any) -> dict:
        with self._connect() as con:
            existing = con.execute(
                "SELECT user_id FROM coin_purchases WHERE idempotency_key=?", (idempotency_key,)
            ).fetchone()
        if existing and existing["user_id"] != user_id:
            raise EconomyError(
                "IDEMPOTENCY_KEY_SCOPE_MISMATCH",
                "Idempotency key is already bound to another account.",
                status_code=409,
            )
        if not existing:
            self._consume_rate_limit(
                user_id=user_id,
                action="coin_purchase",
                idempotency_key=idempotency_key,
                limit_env="LSS_COIN_PURCHASE_RATE_LIMIT",
                default_limit=8,
            )
        try:
            return super().create_purchase(user_id=user_id, idempotency_key=idempotency_key, **kwargs)
        except sqlite3.IntegrityError as exc:
            with self._connect() as con:
                winner = con.execute(
                    "SELECT user_id FROM coin_purchases WHERE idempotency_key=?", (idempotency_key,)
                ).fetchone()
            if winner and winner["user_id"] != user_id:
                raise EconomyError(
                    "IDEMPOTENCY_KEY_SCOPE_MISMATCH",
                    "Idempotency key is already bound to another account.",
                    status_code=409,
                ) from exc
            raise

    def send_gift(self, *, sender_user_id: str, idempotency_key: str, **kwargs: Any) -> dict:
        with self._connect() as con:
            existing = con.execute(
                "SELECT sender_user_id FROM gift_transactions WHERE idempotency_key=?", (idempotency_key,)
            ).fetchone()
        if existing and existing["sender_user_id"] != sender_user_id:
            raise EconomyError(
                "IDEMPOTENCY_KEY_SCOPE_MISMATCH",
                "Idempotency key is already bound to another account.",
                status_code=409,
            )
        if not existing:
            self._consume_rate_limit(
                user_id=sender_user_id,
                action="gift_send",
                idempotency_key=idempotency_key,
                limit_env="LSS_GIFT_SEND_RATE_LIMIT",
                default_limit=120,
            )
        try:
            return super().send_gift(
                sender_user_id=sender_user_id, idempotency_key=idempotency_key, **kwargs
            )
        except EconomyError as exc:
            event_types = {
                "SPENDING_LIMIT_EXCEEDED": "economy.spending_limit_blocked",
                "PERSONAL_SPENDING_LIMIT_EXCEEDED": "economy.personal_spending_limit_blocked",
                "INSUFFICIENT_COIN_BALANCE": "economy.insufficient_balance",
                "RISK_REVIEW_REQUIRED": "economy.risk_hold",
                "GIFT_BLOCKED": "economy.risk_block",
                "COIN_ACCOUNT_RESTRICTED": "economy.account_restriction_block",
            }
            event_type = event_types.get(exc.code)
            if event_type:
                self._record_operational_event(
                    event_type=event_type,
                    user_id=sender_user_id,
                    details={"code": exc.code, **(exc.details or {})},
                )
            raise
        except sqlite3.IntegrityError as exc:
            with self._connect() as con:
                winner = con.execute(
                    "SELECT sender_user_id FROM gift_transactions WHERE idempotency_key=?", (idempotency_key,)
                ).fetchone()
            if winner and winner["sender_user_id"] != sender_user_id:
                raise EconomyError(
                    "IDEMPOTENCY_KEY_SCOPE_MISMATCH",
                    "Idempotency key is already bound to another account.",
                    status_code=409,
                ) from exc
            raise

    def apply_verified_payment_event(self, event: VerifiedPaymentEvent) -> dict:
        """Handle non-crediting terminal provider outcomes, delegate money events to core."""
        if event.event_type not in {"failed", "cancelled"}:
            return super().apply_verified_payment_event(event)
        if not event.verified:
            raise EconomyError(
                "INVALID_PAYMENT_WEBHOOK",
                "Payment event authenticity could not be verified.",
                status_code=401,
            )
        payload_hash = hashlib.sha256(
            json.dumps(
                {
                    "provider": event.provider,
                    "provider_event_id": event.provider_event_id,
                    "provider_payment_id": event.provider_payment_id,
                    "purchase_id": event.purchase_id,
                    "event_type": event.event_type,
                    "occurred_at": event.occurred_at,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        with self._connect() as con:
            self._begin(con)
            seen = con.execute(
                "SELECT purchase_id FROM payment_webhook_events WHERE provider=? AND provider_event_id=?",
                (event.provider, event.provider_event_id),
            ).fetchone()
            if seen:
                purchase = con.execute(
                    "SELECT * FROM coin_purchases WHERE id=?", (seen["purchase_id"],)
                ).fetchone()
                con.commit()
                return {
                    "idempotent_replay": True,
                    "purchase": dict(purchase) if purchase else None,
                }
            purchase = con.execute(
                "SELECT * FROM coin_purchases WHERE id=?", (event.purchase_id,)
            ).fetchone()
            if not purchase:
                raise EconomyError(
                    "PURCHASE_NOT_FOUND", "Coin purchase not found.", status_code=404
                )
            if purchase["provider"] != event.provider:
                raise EconomyError(
                    "PAYMENT_PROVIDER_MISMATCH",
                    "Payment provider does not match purchase.",
                    status_code=409,
                )
            if purchase["provider_payment_id"] and purchase["provider_payment_id"] != event.provider_payment_id:
                raise EconomyError(
                    "PAYMENT_REFERENCE_CONFLICT",
                    "Payment reference does not match purchase.",
                    status_code=409,
                )
            con.execute(
                """INSERT INTO payment_webhook_events
                   (provider,provider_event_id,event_type,purchase_id,received_at,payload_hash)
                   VALUES (?,?,?,?,?,?)""",
                (
                    event.provider,
                    event.provider_event_id,
                    event.event_type,
                    purchase["id"],
                    event.occurred_at,
                    payload_hash,
                ),
            )
            if purchase["status"] == "pending":
                con.execute(
                    "UPDATE coin_purchases SET status=? WHERE id=?",
                    (event.event_type, purchase["id"]),
                )
            purchase = con.execute(
                "SELECT * FROM coin_purchases WHERE id=?", (purchase["id"],)
            ).fetchone()
            con.commit()
        return {"idempotent_replay": False, "purchase": dict(purchase)}

    def reconcile(self) -> dict:
        """Extend core reconciliation with purchase/reversal linkage invariants."""
        base = super().reconcile()
        found = list(base["discrepancies"])
        with self._connect() as con:
            self._begin(con)
            purchases = con.execute(
                "SELECT * FROM coin_purchases WHERE status IN ('confirmed','refunded','chargeback')"
            ).fetchall()
            for purchase in purchases:
                credit = None
                if purchase["ledger_credit_id"]:
                    credit = con.execute(
                        "SELECT * FROM coin_ledger_entries WHERE id=?",
                        (purchase["ledger_credit_id"],),
                    ).fetchone()
                if not credit or int(credit["coin_delta"]) != int(purchase["coin_quantity"]):
                    found.append(
                        self._record_discrepancy_locked(
                            con,
                            "PURCHASE_LEDGER_CREDIT_MISMATCH",
                            purchase["id"],
                            {
                                "coin_delta": int(purchase["coin_quantity"]),
                                "ledger_credit_id": purchase["ledger_credit_id"],
                            },
                            {
                                "coin_delta": int(credit["coin_delta"]) if credit else None,
                                "ledger_credit_id": credit["id"] if credit else None,
                            },
                        )
                    )
                if purchase["status"] in {"refunded", "chargeback"}:
                    reversal = None
                    if purchase["reversal_ledger_id"]:
                        reversal = con.execute(
                            "SELECT * FROM coin_ledger_entries WHERE id=?",
                            (purchase["reversal_ledger_id"],),
                        ).fetchone()
                    if not reversal or int(reversal["coin_delta"]) != -int(purchase["coin_quantity"]):
                        found.append(
                            self._record_discrepancy_locked(
                                con,
                                "PURCHASE_REVERSAL_LEDGER_MISMATCH",
                                purchase["id"],
                                {
                                    "coin_delta": -int(purchase["coin_quantity"]),
                                    "reversal_ledger_id": purchase["reversal_ledger_id"],
                                },
                                {
                                    "coin_delta": int(reversal["coin_delta"]) if reversal else None,
                                    "reversal_ledger_id": reversal["id"] if reversal else None,
                                },
                            )
                        )
            reversed_gifts = con.execute(
                "SELECT * FROM gift_transactions WHERE status='reversed'"
            ).fetchall()
            for gift in reversed_gifts:
                reversal = None
                if gift["reversal_ledger_id"]:
                    reversal = con.execute(
                        "SELECT * FROM coin_ledger_entries WHERE id=?",
                        (gift["reversal_ledger_id"],),
                    ).fetchone()
                receipt = con.execute(
                    "SELECT * FROM creator_gift_receipts WHERE id=?",
                    (gift["creator_receipt_id"],),
                ).fetchone()
                if not reversal or int(reversal["coin_delta"]) != int(gift["total_coin_cost"]):
                    found.append(
                        self._record_discrepancy_locked(
                            con,
                            "GIFT_REVERSAL_LEDGER_MISMATCH",
                            gift["id"],
                            {"coin_delta": int(gift["total_coin_cost"])},
                            {"coin_delta": int(reversal["coin_delta"]) if reversal else None},
                        )
                    )
                if not receipt or receipt["status"] != "reversed":
                    found.append(
                        self._record_discrepancy_locked(
                            con,
                            "GIFT_REVERSAL_RECEIPT_MISMATCH",
                            gift["id"],
                            {"receipt_status": "reversed"},
                            {"receipt_status": receipt["status"] if receipt else None},
                        )
                    )
            con.commit()
        unique = {item["id"]: item for item in found}
        return {"ok": not unique, "discrepancies": list(unique.values())}


class EconomyIntegrationRegistry:
    """Narrow compatibility seam for Chats 1/2/10 until shared contracts land."""

    def __init__(self):
        self.live_sessions: LiveSessionDirectory = UnavailableLiveSessionDirectory()
        self.eligibility: EconomyEligibilityDirectory = UnavailableEconomyEligibilityDirectory()
        self.risk: GiftRiskEvaluator = BaselineGiftRiskEvaluator()

    def configure(
        self,
        *,
        live_sessions: LiveSessionDirectory | None = None,
        eligibility: EconomyEligibilityDirectory | None = None,
        risk: GiftRiskEvaluator | None = None,
    ) -> None:
        if live_sessions is not None:
            self.live_sessions = live_sessions
        if eligibility is not None:
            self.eligibility = eligibility
        if risk is not None:
            self.risk = risk

    def build(self, db_path: str | Path | None = None) -> IntegratedCosmicEconomy:
        # Lazy import avoids a module cycle while making personal limits part of the canonical
        # runtime service used by API and neighbouring chats.
        from .cosmic_economy_personal_limits import PersonalLimitCosmicEconomy

        return PersonalLimitCosmicEconomy(
            db_path,
            live_sessions=self.live_sessions,
            eligibility=self.eligibility,
            risk=self.risk,
        )


runtime_integrations = EconomyIntegrationRegistry()


def configure_economy_integrations(
    *,
    live_sessions: LiveSessionDirectory | None = None,
    eligibility: EconomyEligibilityDirectory | None = None,
    risk: GiftRiskEvaluator | None = None,
) -> None:
    runtime_integrations.configure(live_sessions=live_sessions, eligibility=eligibility, risk=risk)


def economy_service(db_path: str | Path | None = None) -> IntegratedCosmicEconomy:
    return runtime_integrations.build(db_path)
