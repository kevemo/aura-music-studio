from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from .cosmic_economy import (
    BaselineGiftRiskEvaluator,
    CosmicEconomy,
    EconomyEligibilityDirectory,
    EconomyError,
    GiftRiskEvaluator,
    LiveSessionDirectory,
    UnavailableEconomyEligibilityDirectory,
    UnavailableLiveSessionDirectory,
)


class IntegratedCosmicEconomy(CosmicEconomy):
    """Canonical runtime service with cross-account idempotency hardening.

    The underlying ledger/schema remains CosmicEconomy. This subclass adds a migration-safe
    global uniqueness invariant for each money-moving command family so a client retry key
    can never migrate between accounts. If legacy duplicate keys are found, initialization
    fails closed instead of deleting or rewriting financial history.
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
        try:
            return super().send_gift(
                sender_user_id=sender_user_id, idempotency_key=idempotency_key, **kwargs
            )
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
        return IntegratedCosmicEconomy(
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
