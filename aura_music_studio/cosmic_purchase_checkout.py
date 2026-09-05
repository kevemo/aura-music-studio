from __future__ import annotations

from typing import Any

from .cosmic_economy import EconomyError, VerifiedPaymentEvent, _iso
from .cosmic_economy_command_idempotency import CommandBoundCosmicEconomy


class CheckoutBoundCosmicEconomy(CommandBoundCosmicEconomy):
    """Persists provider checkout identity so purchase retries do not create new checkouts."""

    def _init_schema(self) -> None:
        super()._init_schema()
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS coin_purchase_checkouts (
                    purchase_id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    provider_payment_id TEXT NOT NULL UNIQUE,
                    checkout_url TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(purchase_id) REFERENCES coin_purchases(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_coin_purchase_checkouts_provider
                    ON coin_purchase_checkouts(provider, provider_payment_id);
                """
            )

    def get_purchase_checkout(self, purchase_id: str) -> dict[str, Any] | None:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM coin_purchase_checkouts WHERE purchase_id=?",
                (purchase_id,),
            ).fetchone()
        return dict(row) if row else None

    def bind_purchase_checkout(
        self,
        purchase_id: str,
        *,
        provider: str,
        provider_payment_id: str,
        checkout_url: str,
        status: str = "pending",
    ) -> dict[str, Any]:
        provider = (provider or "").strip().lower()
        provider_payment_id = (provider_payment_id or "").strip()
        checkout_url = (checkout_url or "").strip()
        status = (status or "pending").strip().lower()
        if not provider or not provider_payment_id or not checkout_url:
            raise EconomyError(
                "INVALID_PAYMENT_CHECKOUT",
                "Provider checkout requires provider, payment reference and checkout URL.",
                status_code=502,
            )
        with self._connect() as con:
            self._begin(con)
            purchase = con.execute(
                "SELECT * FROM coin_purchases WHERE id=?",
                (purchase_id,),
            ).fetchone()
            if not purchase:
                raise EconomyError("PURCHASE_NOT_FOUND", "Coin purchase not found.", status_code=404)
            if purchase["provider"] != provider:
                raise EconomyError(
                    "PAYMENT_PROVIDER_MISMATCH",
                    "Checkout provider does not match purchase provider.",
                    status_code=409,
                )
            existing = con.execute(
                "SELECT * FROM coin_purchase_checkouts WHERE purchase_id=?",
                (purchase_id,),
            ).fetchone()
            if existing:
                if (
                    existing["provider"] != provider
                    or existing["provider_payment_id"] != provider_payment_id
                    or existing["checkout_url"] != checkout_url
                ):
                    raise EconomyError(
                        "PAYMENT_CHECKOUT_CONFLICT",
                        "Purchase is already bound to a different provider checkout.",
                        status_code=409,
                    )
                con.commit()
                return {"purchase": dict(purchase), "checkout": dict(existing), "idempotent_replay": True}
            if purchase["provider_payment_id"] and purchase["provider_payment_id"] != provider_payment_id:
                raise EconomyError(
                    "PAYMENT_REFERENCE_CONFLICT",
                    "Purchase is already bound to another provider payment.",
                    status_code=409,
                )
            other = con.execute(
                """SELECT purchase_id FROM coin_purchase_checkouts
                   WHERE provider_payment_id=?""",
                (provider_payment_id,),
            ).fetchone()
            if other and other["purchase_id"] != purchase_id:
                raise EconomyError(
                    "PAYMENT_REFERENCE_REUSED",
                    "Provider payment reference is already bound to another purchase.",
                    status_code=409,
                )
            now = _iso()
            con.execute(
                "UPDATE coin_purchases SET provider_payment_id=? WHERE id=?",
                (provider_payment_id, purchase_id),
            )
            con.execute(
                """INSERT INTO coin_purchase_checkouts
                   (purchase_id,provider,provider_payment_id,checkout_url,status,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    purchase_id,
                    provider,
                    provider_payment_id,
                    checkout_url,
                    status,
                    now,
                    now,
                ),
            )
            purchase = con.execute(
                "SELECT * FROM coin_purchases WHERE id=?",
                (purchase_id,),
            ).fetchone()
            checkout = con.execute(
                "SELECT * FROM coin_purchase_checkouts WHERE purchase_id=?",
                (purchase_id,),
            ).fetchone()
            con.commit()
        return {"purchase": dict(purchase), "checkout": dict(checkout), "idempotent_replay": False}

    def apply_verified_payment_event(self, event: VerifiedPaymentEvent) -> dict:
        result = super().apply_verified_payment_event(event)
        purchase = result.get("purchase") if isinstance(result, dict) else None
        if not purchase:
            return result
        purchase_id = purchase.get("id")
        status = purchase.get("status")
        if not purchase_id or not status:
            return result
        with self._connect() as con:
            con.execute(
                """UPDATE coin_purchase_checkouts
                   SET status=?,updated_at=? WHERE purchase_id=?""",
                (str(status), _iso(), purchase_id),
            )
        checkout = self.get_purchase_checkout(purchase_id)
        if checkout:
            result = dict(result)
            result["checkout"] = checkout
        return result
