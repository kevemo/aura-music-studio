from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .marketplace_settlement import MarketplaceSettlementStore, OWNER_CATALOGUE_PRINCIPALS


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _currency(value: str) -> str:
    currency = (value or "").strip().upper()
    if len(currency) != 3 or not currency.isalpha():
        raise ValueError("Marketplace order requires a three-letter currency")
    return currency


class MarketplaceOrderStore:
    """Immutable server-authoritative marketplace checkout bindings.

    A browser or provider webhook is never authoritative for tenant, publication, payee,
    ownership provenance, price, or currency. Those facts are snapshotted here before a
    provider checkout is created. Provider events may prove payment of that snapshot, but
    cannot rewrite it.
    """

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _init_schema(self) -> None:
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS marketplace_orders (
                    id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    provider_checkout_reference TEXT UNIQUE,
                    tenant_id TEXT NOT NULL,
                    buyer_user_id TEXT NOT NULL,
                    publication_id TEXT NOT NULL,
                    publication_revision TEXT NOT NULL,
                    creator_user_id TEXT,
                    gross_minor INTEGER NOT NULL CHECK(gross_minor > 0),
                    currency TEXT NOT NULL,
                    esp_owned INTEGER NOT NULL CHECK(esp_owned IN (0,1)),
                    catalogue_owner TEXT,
                    created_at TEXT NOT NULL,
                    checkout_bound_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_marketplace_orders_tenant_buyer
                    ON marketplace_orders(tenant_id, buyer_user_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_marketplace_orders_publication
                    ON marketplace_orders(tenant_id, publication_id, created_at DESC);
                """
            )

    def create_order(
        self,
        *,
        provider: str,
        tenant_id: str,
        buyer_user_id: str,
        publication_id: str,
        publication_revision: str,
        gross_minor: int,
        currency: str,
        creator_user_id: str | None,
        esp_owned: bool = False,
        catalogue_owner: str | None = None,
    ) -> dict[str, Any]:
        provider = (provider or "").strip().lower()
        tenant_id = (tenant_id or "").strip()
        buyer_user_id = (buyer_user_id or "").strip()
        publication_id = (publication_id or "").strip()
        publication_revision = (publication_revision or "").strip()
        creator_user_id = (creator_user_id or "").strip() or None
        gross_minor = int(gross_minor)
        currency = _currency(currency)
        owner = (catalogue_owner or "").strip().lower() or None

        if not provider or not tenant_id or not buyer_user_id or not publication_id or not publication_revision:
            raise ValueError("Marketplace order requires provider, tenant, buyer and immutable publication provenance")
        if gross_minor <= 0:
            raise ValueError("Marketplace order gross amount must be positive")

        if esp_owned:
            if creator_user_id is not None:
                raise ValueError("ESP-owned marketplace order cannot name a creator payee")
            if owner not in OWNER_CATALOGUE_PRINCIPALS:
                raise ValueError("ESP-owned marketplace order requires Mary/Kev provenance")
        else:
            if creator_user_id is None:
                raise ValueError("Creator marketplace order requires a creator payee")
            if owner is not None:
                raise ValueError("Creator marketplace order cannot claim ESP catalogue ownership")

        order_id = uuid4().hex
        with self._connect() as con:
            con.execute(
                """INSERT INTO marketplace_orders
                   (id,provider,tenant_id,buyer_user_id,publication_id,publication_revision,
                    creator_user_id,gross_minor,currency,esp_owned,catalogue_owner,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    order_id,
                    provider,
                    tenant_id,
                    buyer_user_id,
                    publication_id,
                    publication_revision,
                    creator_user_id,
                    gross_minor,
                    currency,
                    int(bool(esp_owned)),
                    owner,
                    _iso(),
                ),
            )
            row = con.execute("SELECT * FROM marketplace_orders WHERE id=?", (order_id,)).fetchone()
        return dict(row)

    def bind_provider_checkout(self, *, order_id: str, provider_checkout_reference: str) -> dict[str, Any]:
        order_id = (order_id or "").strip()
        reference = (provider_checkout_reference or "").strip()
        if not order_id or not reference:
            raise ValueError("Marketplace checkout binding requires order and provider reference")

        with self._connect() as con:
            row = con.execute("SELECT * FROM marketplace_orders WHERE id=?", (order_id,)).fetchone()
            if not row:
                raise ValueError("Unknown marketplace order")
            existing = str(row["provider_checkout_reference"] or "")
            if existing:
                if existing != reference:
                    raise ValueError("Marketplace order is already bound to a different provider checkout")
                return dict(row)
            try:
                con.execute(
                    """UPDATE marketplace_orders
                       SET provider_checkout_reference=?, checkout_bound_at=?
                       WHERE id=? AND provider_checkout_reference IS NULL""",
                    (reference, _iso(), order_id),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("Provider checkout reference is already bound to another marketplace order") from exc
            row = con.execute("SELECT * FROM marketplace_orders WHERE id=?", (order_id,)).fetchone()
        return dict(row)

    def verified_payment_snapshot(
        self,
        *,
        order_id: str,
        provider: str,
        provider_checkout_reference: str,
        tenant_id: str,
        buyer_user_id: str,
        gross_minor: int,
        currency: str,
    ) -> dict[str, Any]:
        """Return trusted commercial facts only when provider evidence matches exactly."""

        order_id = (order_id or "").strip()
        provider = (provider or "").strip().lower()
        reference = (provider_checkout_reference or "").strip()
        tenant_id = (tenant_id or "").strip()
        buyer_user_id = (buyer_user_id or "").strip()
        gross_minor = int(gross_minor)
        currency = _currency(currency)

        with self._connect() as con:
            row = con.execute("SELECT * FROM marketplace_orders WHERE id=?", (order_id,)).fetchone()
        if not row:
            raise ValueError("Unknown marketplace order")
        snapshot = dict(row)

        expected = {
            "provider": provider,
            "provider_checkout_reference": reference,
            "tenant_id": tenant_id,
            "buyer_user_id": buyer_user_id,
            "gross_minor": gross_minor,
            "currency": currency,
        }
        if any(snapshot.get(key) != value for key, value in expected.items()):
            raise ValueError("Provider payment evidence does not match immutable marketplace order")
        if not snapshot.get("provider_checkout_reference"):
            raise ValueError("Marketplace order has no provider checkout binding")
        return snapshot


def record_verified_marketplace_payment(
    *,
    orders: MarketplaceOrderStore,
    settlements: MarketplaceSettlementStore,
    order_id: str,
    provider: str,
    provider_checkout_reference: str,
    provider_order_reference: str,
    tenant_id: str,
    buyer_user_id: str,
    gross_minor: int,
    provider_fee_minor: int,
    currency: str,
) -> dict[str, Any]:
    """Bridge verified provider evidence to settlement without trusting event metadata.

    The caller must obtain ``provider_fee_minor`` from provider-verified evidence. This helper
    intentionally has no parameters for publication, creator payee, or ESP ownership; those
    commercial facts can only come from the immutable server-side order snapshot.
    """

    provider_order_reference = (provider_order_reference or "").strip()
    if not provider_order_reference:
        raise ValueError("Verified marketplace payment requires a provider order reference")

    snapshot = orders.verified_payment_snapshot(
        order_id=order_id,
        provider=provider,
        provider_checkout_reference=provider_checkout_reference,
        tenant_id=tenant_id,
        buyer_user_id=buyer_user_id,
        gross_minor=gross_minor,
        currency=currency,
    )
    return settlements.record_verified_order(
        provider=str(snapshot["provider"]),
        provider_order_reference=provider_order_reference,
        tenant_id=str(snapshot["tenant_id"]),
        publication_id=str(snapshot["publication_id"]),
        creator_user_id=snapshot.get("creator_user_id"),
        gross_minor=int(snapshot["gross_minor"]),
        provider_fee_minor=int(provider_fee_minor),
        currency=str(snapshot["currency"]),
        esp_owned=bool(snapshot["esp_owned"]),
        catalogue_owner=snapshot.get("catalogue_owner"),
    )


__all__ = ["MarketplaceOrderStore", "record_verified_marketplace_payment"]
