from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


POLICY_VERSION = "marketplace-v1"
CREATOR_SPLIT_BPS = 5_000
FULL_BPS = 10_000
OWNER_CATALOGUE_PRINCIPALS = frozenset({"mary", "kev"})


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalise_currency(value: str) -> str:
    currency = (value or "").strip().upper()
    if len(currency) != 3 or not currency.isalpha():
        raise ValueError("Marketplace settlement requires a three-letter currency")
    return currency


def _allocation_for(
    *,
    net_minor: int,
    esp_owned: bool,
    catalogue_owner: str | None,
) -> tuple[str, int, int, str | None]:
    """Return immutable marketplace allocation from server-owned provenance.

    ESP-owned catalogue settlement is deliberately fail-closed: 100% owner-pool allocation
    is available only when the caller has already established persistent ESP ownership and the
    provenance names Mary or Kev. Every other publication uses the creator 50/50 policy.
    """

    owner = (catalogue_owner or "").strip().lower() or None
    if esp_owned:
        if owner not in OWNER_CATALOGUE_PRINCIPALS:
            raise ValueError("ESP-owned catalogue settlement requires Mary/Kev provenance")
        return "esp_owner_catalogue_100", 0, net_minor, owner
    if owner is not None:
        raise ValueError("Catalogue owner provenance cannot be set for a creator publication")

    creator_minor = net_minor // 2
    admin_minor = net_minor - creator_minor
    return "creator_marketplace_50_50", creator_minor, admin_minor, None


class MarketplaceSettlementStore:
    """Append-only marketplace order and reversal accounting evidence.

    This ledger records immutable provider-verified commercial facts. It does not grant
    membership, Creator/Agent/Owner authority, initiate payouts, or claim provider/bank
    reconciliation. Those boundaries remain separate production controls.
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
                CREATE TABLE IF NOT EXISTS marketplace_settlements (
                    id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    provider_order_reference TEXT NOT NULL UNIQUE,
                    tenant_id TEXT NOT NULL,
                    publication_id TEXT NOT NULL,
                    creator_user_id TEXT,
                    gross_minor INTEGER NOT NULL CHECK(gross_minor >= 0),
                    provider_fee_minor INTEGER NOT NULL CHECK(provider_fee_minor >= 0),
                    net_minor INTEGER NOT NULL CHECK(net_minor >= 0),
                    currency TEXT NOT NULL,
                    allocation_policy TEXT NOT NULL,
                    policy_version TEXT NOT NULL,
                    creator_share_minor INTEGER NOT NULL CHECK(creator_share_minor >= 0),
                    admin_pool_share_minor INTEGER NOT NULL CHECK(admin_pool_share_minor >= 0),
                    esp_owned INTEGER NOT NULL CHECK(esp_owned IN (0,1)),
                    catalogue_owner TEXT,
                    verified_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_marketplace_settlements_tenant
                    ON marketplace_settlements(tenant_id, verified_at DESC);
                CREATE TABLE IF NOT EXISTS marketplace_reversals (
                    id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    provider_reversal_reference TEXT NOT NULL UNIQUE,
                    settlement_id TEXT NOT NULL,
                    amount_minor INTEGER NOT NULL CHECK(amount_minor > 0),
                    creator_share_minor INTEGER NOT NULL CHECK(creator_share_minor >= 0),
                    admin_pool_share_minor INTEGER NOT NULL CHECK(admin_pool_share_minor >= 0),
                    currency TEXT NOT NULL,
                    verified_at TEXT NOT NULL,
                    FOREIGN KEY(settlement_id) REFERENCES marketplace_settlements(id)
                );
                CREATE INDEX IF NOT EXISTS idx_marketplace_reversals_settlement
                    ON marketplace_reversals(settlement_id, verified_at DESC);
                """
            )

    def record_verified_order(
        self,
        *,
        provider: str,
        provider_order_reference: str,
        tenant_id: str,
        publication_id: str,
        gross_minor: int,
        provider_fee_minor: int,
        currency: str,
        creator_user_id: str | None,
        esp_owned: bool = False,
        catalogue_owner: str | None = None,
    ) -> dict[str, Any]:
        provider = (provider or "").strip().lower()
        reference = (provider_order_reference or "").strip()
        tenant_id = (tenant_id or "").strip()
        publication_id = (publication_id or "").strip()
        creator_user_id = (creator_user_id or "").strip() or None
        gross_minor = int(gross_minor)
        provider_fee_minor = int(provider_fee_minor)
        currency = _normalise_currency(currency)

        if not provider or not reference or not tenant_id or not publication_id:
            raise ValueError("Marketplace order requires provider, reference, tenant and publication")
        if gross_minor < 0 or provider_fee_minor < 0 or provider_fee_minor > gross_minor:
            raise ValueError("Marketplace gross/fee amounts are invalid")
        if not esp_owned and creator_user_id is None:
            raise ValueError("Creator marketplace settlement requires a creator user id")
        if esp_owned and creator_user_id is not None:
            raise ValueError("ESP-owned catalogue settlement cannot also name a creator payee")

        net_minor = gross_minor - provider_fee_minor
        policy, creator_share, admin_share, owner = _allocation_for(
            net_minor=net_minor,
            esp_owned=bool(esp_owned),
            catalogue_owner=catalogue_owner,
        )
        expected = {
            "provider": provider,
            "tenant_id": tenant_id,
            "publication_id": publication_id,
            "creator_user_id": creator_user_id,
            "gross_minor": gross_minor,
            "provider_fee_minor": provider_fee_minor,
            "net_minor": net_minor,
            "currency": currency,
            "allocation_policy": policy,
            "policy_version": POLICY_VERSION,
            "creator_share_minor": creator_share,
            "admin_pool_share_minor": admin_share,
            "esp_owned": int(bool(esp_owned)),
            "catalogue_owner": owner,
        }

        with self._connect() as con:
            existing = con.execute(
                "SELECT * FROM marketplace_settlements WHERE provider_order_reference=?",
                (reference,),
            ).fetchone()
            if existing:
                row = dict(existing)
                if any(row.get(key) != value for key, value in expected.items()):
                    raise ValueError("Marketplace order reference was reused with different financial data")
                return row

            settlement_id = uuid4().hex
            con.execute(
                """INSERT INTO marketplace_settlements
                   (id,provider,provider_order_reference,tenant_id,publication_id,creator_user_id,
                    gross_minor,provider_fee_minor,net_minor,currency,allocation_policy,policy_version,
                    creator_share_minor,admin_pool_share_minor,esp_owned,catalogue_owner,verified_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    settlement_id,
                    provider,
                    reference,
                    tenant_id,
                    publication_id,
                    creator_user_id,
                    gross_minor,
                    provider_fee_minor,
                    net_minor,
                    currency,
                    policy,
                    POLICY_VERSION,
                    creator_share,
                    admin_share,
                    int(bool(esp_owned)),
                    owner,
                    _iso(),
                ),
            )
            row = con.execute(
                "SELECT * FROM marketplace_settlements WHERE id=?", (settlement_id,)
            ).fetchone()
        return dict(row)

    def record_verified_reversal(
        self,
        *,
        provider: str,
        provider_reversal_reference: str,
        provider_order_reference: str,
        amount_minor: int,
        currency: str,
    ) -> dict[str, Any]:
        provider = (provider or "").strip().lower()
        reversal_reference = (provider_reversal_reference or "").strip()
        order_reference = (provider_order_reference or "").strip()
        amount_minor = int(amount_minor)
        currency = _normalise_currency(currency)
        if not provider or not reversal_reference or not order_reference or amount_minor <= 0:
            raise ValueError("Marketplace reversal requires provider, references and positive amount")

        with self._connect() as con:
            settlement = con.execute(
                "SELECT * FROM marketplace_settlements WHERE provider_order_reference=?",
                (order_reference,),
            ).fetchone()
            if not settlement:
                raise ValueError("Marketplace reversal references an unknown order")
            settlement = dict(settlement)
            if settlement["provider"] != provider or settlement["currency"] != currency:
                raise ValueError("Marketplace reversal provider/currency does not match order")

            existing = con.execute(
                "SELECT * FROM marketplace_reversals WHERE provider_reversal_reference=?",
                (reversal_reference,),
            ).fetchone()
            if existing:
                row = dict(existing)
                expected = {
                    "provider": provider,
                    "settlement_id": settlement["id"],
                    "amount_minor": amount_minor,
                    "currency": currency,
                }
                if any(row.get(key) != value for key, value in expected.items()):
                    raise ValueError("Marketplace reversal reference was reused with different data")
                return row

            reversed_so_far = int(
                con.execute(
                    "SELECT COALESCE(SUM(amount_minor),0) FROM marketplace_reversals WHERE settlement_id=?",
                    (settlement["id"],),
                ).fetchone()[0]
            )
            if reversed_so_far + amount_minor > int(settlement["net_minor"]):
                raise ValueError("Marketplace reversals cannot exceed verified net order amount")

            # Reverse using cumulative allocation to keep partial reversals deterministic and
            # ensure the final reversal exactly zeroes the original allocation, including odd pence.
            new_total = reversed_so_far + amount_minor
            if settlement["allocation_policy"] == "esp_owner_catalogue_100":
                creator_reversed_total = 0
            else:
                creator_reversed_total = new_total // 2
            admin_reversed_total = new_total - creator_reversed_total

            prior = con.execute(
                """SELECT COALESCE(SUM(creator_share_minor),0),
                          COALESCE(SUM(admin_pool_share_minor),0)
                   FROM marketplace_reversals WHERE settlement_id=?""",
                (settlement["id"],),
            ).fetchone()
            creator_share = creator_reversed_total - int(prior[0])
            admin_share = admin_reversed_total - int(prior[1])

            reversal_id = uuid4().hex
            con.execute(
                """INSERT INTO marketplace_reversals
                   (id,provider,provider_reversal_reference,settlement_id,amount_minor,
                    creator_share_minor,admin_pool_share_minor,currency,verified_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    reversal_id,
                    provider,
                    reversal_reference,
                    settlement["id"],
                    amount_minor,
                    creator_share,
                    admin_share,
                    currency,
                    _iso(),
                ),
            )
            row = con.execute(
                "SELECT * FROM marketplace_reversals WHERE id=?", (reversal_id,)
            ).fetchone()
        return dict(row)

    def balance_for_order(self, provider_order_reference: str) -> dict[str, Any]:
        reference = (provider_order_reference or "").strip()
        with self._connect() as con:
            settlement = con.execute(
                "SELECT * FROM marketplace_settlements WHERE provider_order_reference=?",
                (reference,),
            ).fetchone()
            if not settlement:
                raise ValueError("Unknown marketplace order")
            settlement = dict(settlement)
            reversed_row = con.execute(
                """SELECT COALESCE(SUM(amount_minor),0),
                          COALESCE(SUM(creator_share_minor),0),
                          COALESCE(SUM(admin_pool_share_minor),0)
                   FROM marketplace_reversals WHERE settlement_id=?""",
                (settlement["id"],),
            ).fetchone()

        reversed_minor = int(reversed_row[0])
        creator_reversed = int(reversed_row[1])
        admin_reversed = int(reversed_row[2])
        return {
            "provider_order_reference": reference,
            "currency": settlement["currency"],
            "verified_net_minor": int(settlement["net_minor"]),
            "reversed_minor": reversed_minor,
            "remaining_net_minor": int(settlement["net_minor"]) - reversed_minor,
            "creator_share_remaining_minor": int(settlement["creator_share_minor"])
            - creator_reversed,
            "admin_pool_share_remaining_minor": int(settlement["admin_pool_share_minor"])
            - admin_reversed,
            "allocation_policy": settlement["allocation_policy"],
            "policy_version": settlement["policy_version"],
            "payout_initiated": False,
            "provider_reconciled": False,
        }


__all__ = [
    "CREATOR_SPLIT_BPS",
    "FULL_BPS",
    "MarketplaceSettlementStore",
    "OWNER_CATALOGUE_PRINCIPALS",
    "POLICY_VERSION",
]
