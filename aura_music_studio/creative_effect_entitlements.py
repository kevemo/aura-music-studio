from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .creative_catalogue import get_catalogue_item
from .studio_catalogue_menus import EFFECT_BANDS

PUBLIC_COIN_UNIT = "COSMIC_CREATION_COIN"
LEGACY_LEDGER_UNIT = "PULSAR_CREDIT"
_BAND_PRICES = {band.id: int(band.coin_price) for band in EFFECT_BANDS}
if _BAND_PRICES != {"core": 0, "silver": 200, "gold": 500}:  # pragma: no cover
    raise RuntimeError("Creative effect Coin prices drifted from the canonical band contract")


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class CreativeEffectEntitlementStore:
    """Permanent first-party effect unlocks on the existing server-authoritative credit DB.

    The historical wallet stores the same integer platform units under the internal name
    ``PULSAR_CREDIT``. This compatibility layer exposes the owner-approved public product name
    Cosmic Creation Coins without rewriting existing ledger history. Effect purchase, wallet
    debit, immutable credit transaction and entitlement grant happen inside one SQLite
    ``BEGIN IMMEDIATE`` transaction so a member cannot be charged without receiving access.
    """

    def __init__(self, db_path: str | Path | None = None):
        configured = db_path or os.getenv("LSS_DB_PATH") or "data/live_sound_studio.sqlite3"
        self.db_path = Path(configured)
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
                CREATE TABLE IF NOT EXISTS creative_effect_entitlements (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    effect_id TEXT NOT NULL,
                    entitlement_band TEXT NOT NULL,
                    coin_price INTEGER NOT NULL CHECK(coin_price >= 0),
                    source TEXT NOT NULL,
                    purchase_transaction_id TEXT,
                    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
                    granted_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    revoked_at TEXT,
                    revoked_reason TEXT,
                    UNIQUE(user_id,effect_id),
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY(purchase_transaction_id) REFERENCES credit_transactions(id)
                );
                CREATE INDEX IF NOT EXISTS idx_effect_entitlements_user_active
                    ON creative_effect_entitlements(user_id,active,effect_id);
                """
            )

    @staticmethod
    def _require_user(con: sqlite3.Connection, user_id: str) -> str:
        clean = str(user_id or "").strip()
        if not clean:
            raise ValueError("A member user id is required")
        row = con.execute("SELECT id FROM users WHERE id=?", (clean,)).fetchone()
        if not row:
            raise ValueError("Member account not found")
        return clean

    @staticmethod
    def _ensure_wallet(con: sqlite3.Connection, user_id: str) -> None:
        now = _iso()
        con.execute(
            """INSERT INTO credit_wallets (user_id,balance,created_at,updated_at)
               VALUES (?,0,?,?) ON CONFLICT(user_id) DO NOTHING""",
            (user_id, now, now),
        )

    @staticmethod
    def _catalogue_contract(effect_id: str):
        try:
            item = get_catalogue_item(effect_id)
        except KeyError as exc:
            raise ValueError("Creative catalogue item not found") from exc
        expected = _BAND_PRICES.get(item.entitlement)
        if expected is None or int(item.ccc_price) != expected:
            raise RuntimeError(f"Creative effect price drift for {item.id}")
        return item, expected

    def has_entitlement(self, user_id: str, effect_id: str) -> dict:
        item, price = self._catalogue_contract(effect_id)
        if item.entitlement == "core":
            return {
                "effect_id": item.id,
                "owned": True,
                "included": True,
                "entitlement_band": "core",
                "coin_price": 0,
                "source": "core_catalogue",
            }
        with self._connect() as con:
            user_id = self._require_user(con, user_id)
            row = con.execute(
                """SELECT effect_id,entitlement_band,coin_price,source,granted_at,active
                   FROM creative_effect_entitlements WHERE user_id=? AND effect_id=?""",
                (user_id, item.id),
            ).fetchone()
        return {
            "effect_id": item.id,
            "owned": bool(row and int(row["active"]) == 1),
            "included": False,
            "entitlement_band": item.entitlement,
            "coin_price": price,
            "source": str(row["source"]) if row and int(row["active"]) == 1 else "",
            "granted_at": str(row["granted_at"]) if row and int(row["active"]) == 1 else None,
        }

    def list_owned(self, user_id: str) -> list[dict]:
        with self._connect() as con:
            user_id = self._require_user(con, user_id)
            rows = con.execute(
                """SELECT effect_id,entitlement_band,coin_price,source,purchase_transaction_id,granted_at
                   FROM creative_effect_entitlements
                   WHERE user_id=? AND active=1 ORDER BY granted_at DESC,effect_id ASC""",
                (user_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def purchase(self, user_id: str, effect_id: str, *, idempotency_key: str, actor: str = "member") -> dict:
        item, price = self._catalogue_contract(effect_id)
        if item.entitlement == "core":
            return {
                "purchased": False,
                "already_owned": True,
                "included": True,
                "effect_id": item.id,
                "entitlement_band": "core",
                "coin_price": 0,
                "balance_after": None,
                "transaction_id": None,
            }
        key = str(idempotency_key or "").strip()
        if not key or len(key) > 120:
            raise ValueError("A bounded idempotency key is required")
        actor = str(actor or "member").strip()[:120] or "member"
        reference = f"effect_unlock:v1:{user_id}:{item.id}:{key}"
        if len(reference) > 180:
            raise ValueError("Effect purchase idempotency reference is too long")

        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            user_id = self._require_user(con, user_id)
            self._ensure_wallet(con, user_id)

            existing_entitlement = con.execute(
                "SELECT * FROM creative_effect_entitlements WHERE user_id=? AND effect_id=? AND active=1",
                (user_id, item.id),
            ).fetchone()
            if existing_entitlement:
                wallet = con.execute("SELECT balance FROM credit_wallets WHERE user_id=?", (user_id,)).fetchone()
                return {
                    "purchased": False,
                    "already_owned": True,
                    "included": False,
                    "effect_id": item.id,
                    "entitlement_band": item.entitlement,
                    "coin_price": price,
                    "balance_after": int(wallet["balance"] if wallet else 0),
                    "transaction_id": existing_entitlement["purchase_transaction_id"],
                }

            existing_tx = con.execute("SELECT * FROM credit_transactions WHERE reference=?", (reference,)).fetchone()
            if existing_tx:
                raise RuntimeError("Effect purchase ledger/entitlement integrity mismatch")

            wallet = con.execute("SELECT balance FROM credit_wallets WHERE user_id=?", (user_id,)).fetchone()
            current = int(wallet["balance"] if wallet else 0)
            if current < price:
                raise ValueError("Insufficient Cosmic Creation Coins")

            now = _iso()
            new_balance = current - price
            tx_id = uuid4().hex
            con.execute("UPDATE credit_wallets SET balance=?,updated_at=? WHERE user_id=?", (new_balance, now, user_id))
            con.execute(
                """INSERT INTO credit_transactions
                   (id,user_id,amount,balance_after,kind,reason,reference,actor,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (tx_id, user_id, -price, new_balance, "spend", f"Permanent {item.entitlement} creative effect unlock: {item.id}", reference, actor, now),
            )
            entitlement_id = uuid4().hex
            previous = con.execute("SELECT id FROM creative_effect_entitlements WHERE user_id=? AND effect_id=?", (user_id, item.id)).fetchone()
            if previous:
                con.execute(
                    """UPDATE creative_effect_entitlements
                       SET entitlement_band=?,coin_price=?,source='coin_purchase',purchase_transaction_id=?,
                           active=1,granted_at=?,updated_at=?,revoked_at=NULL,revoked_reason=NULL
                       WHERE id=?""",
                    (item.entitlement, price, tx_id, now, now, previous["id"]),
                )
                entitlement_id = str(previous["id"])
            else:
                con.execute(
                    """INSERT INTO creative_effect_entitlements
                       (id,user_id,effect_id,entitlement_band,coin_price,source,purchase_transaction_id,
                        active,granted_at,updated_at,revoked_at,revoked_reason)
                       VALUES (?,?,?,?,?,'coin_purchase',?,1,?,?,NULL,NULL)""",
                    (entitlement_id, user_id, item.id, item.entitlement, price, tx_id, now, now),
                )

            return {"purchased": True, "already_owned": False, "included": False, "entitlement_id": entitlement_id, "effect_id": item.id, "entitlement_band": item.entitlement, "coin_price": price, "balance_after": new_balance, "transaction_id": tx_id}

    def refund_and_revoke(self, user_id: str, effect_id: str, *, reference: str, reason: str, actor: str) -> dict:
        item, price = self._catalogue_contract(effect_id)
        if item.entitlement == "core":
            raise ValueError("Core catalogue access cannot be refunded")
        refund_reference = str(reference or "").strip()
        if not refund_reference or len(refund_reference) > 180:
            raise ValueError("A bounded refund reference is required")
        reason = str(reason or "Effect purchase refund").strip()[:240]
        actor = str(actor or "admin").strip()[:120] or "admin"

        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            user_id = self._require_user(con, user_id)
            self._ensure_wallet(con, user_id)
            entitlement = con.execute("SELECT * FROM creative_effect_entitlements WHERE user_id=? AND effect_id=? AND active=1", (user_id, item.id)).fetchone()
            if not entitlement:
                raise ValueError("Active purchased effect entitlement not found")
            if entitlement["source"] != "coin_purchase":
                raise ValueError("Only Coin-purchased effect entitlements can be refunded here")

            existing = con.execute("SELECT * FROM credit_transactions WHERE reference=?", (refund_reference,)).fetchone()
            if existing:
                if existing["user_id"] != user_id or existing["kind"] != "refund" or int(existing["amount"]) != price:
                    raise ValueError("Refund reference has already been used for a different transaction")
                return {"refunded": True, "already_processed": True, "effect_id": item.id, "coin_amount": price, "balance_after": int(existing["balance_after"]), "transaction_id": existing["id"]}

            wallet = con.execute("SELECT balance FROM credit_wallets WHERE user_id=?", (user_id,)).fetchone()
            current = int(wallet["balance"] if wallet else 0)
            new_balance = current + price
            now = _iso()
            tx_id = uuid4().hex
            con.execute("UPDATE credit_wallets SET balance=?,updated_at=? WHERE user_id=?", (new_balance, now, user_id))
            con.execute(
                """INSERT INTO credit_transactions
                   (id,user_id,amount,balance_after,kind,reason,reference,actor,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (tx_id, user_id, price, new_balance, "refund", reason, refund_reference, actor, now),
            )
            con.execute("UPDATE creative_effect_entitlements SET active=0,updated_at=?,revoked_at=?,revoked_reason=? WHERE id=?", (now, now, reason, entitlement["id"]))
            return {"refunded": True, "already_processed": False, "effect_id": item.id, "coin_amount": price, "balance_after": new_balance, "transaction_id": tx_id}


store = CreativeEffectEntitlementStore()

__all__ = ["CreativeEffectEntitlementStore", "LEGACY_LEDGER_UNIT", "PUBLIC_COIN_UNIT", "store"]
