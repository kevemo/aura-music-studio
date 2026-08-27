from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from .membership_api import require_admin

router = APIRouter(tags=["Pulsar Credits"])

_CREDIT_UNIT = "PULSAR_CREDIT"
_ALLOWED_KINDS = {"grant", "spend", "refund", "purchase", "adjustment"}


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class CreditWalletStore:
    """Append-only integer credit ledger isolated from subscriptions and ESP roles.

    Pulsar credits are platform units, not fiat money and not a membership tier. Mutating
    the wallet therefore never activates Free/Basic/Pro access and never grants an ESP
    Creator, Agent, Creator+Agent or Owner role.
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
                CREATE TABLE IF NOT EXISTS credit_wallets (
                    user_id TEXT PRIMARY KEY,
                    balance INTEGER NOT NULL DEFAULT 0 CHECK(balance >= 0),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS credit_transactions (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    amount INTEGER NOT NULL,
                    balance_after INTEGER NOT NULL CHECK(balance_after >= 0),
                    kind TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    reference TEXT UNIQUE,
                    actor TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_credit_transactions_user_created
                    ON credit_transactions(user_id, created_at DESC, id DESC);
                """
            )

    @staticmethod
    def _require_user(con: sqlite3.Connection, user_id: str) -> str:
        clean = (user_id or "").strip()
        if not clean:
            raise ValueError("A member user id is required")
        row = con.execute("SELECT id FROM users WHERE id=?", (clean,)).fetchone()
        if not row:
            raise ValueError("Member account not found")
        return clean

    def _ensure_wallet(self, con: sqlite3.Connection, user_id: str) -> str:
        user_id = self._require_user(con, user_id)
        now = _iso()
        con.execute(
            """INSERT INTO credit_wallets (user_id,balance,created_at,updated_at)
               VALUES (?,0,?,?) ON CONFLICT(user_id) DO NOTHING""",
            (user_id, now, now),
        )
        return user_id

    def balance(self, user_id: str) -> int:
        with self._connect() as con:
            user_id = self._ensure_wallet(con, user_id)
            row = con.execute("SELECT balance FROM credit_wallets WHERE user_id=?", (user_id,)).fetchone()
            return int(row["balance"] if row else 0)

    def transactions(self, user_id: str, limit: int = 100) -> list[dict]:
        limit = max(1, min(int(limit), 500))
        with self._connect() as con:
            user_id = self._ensure_wallet(con, user_id)
            rows = con.execute(
                """SELECT id,amount,balance_after,kind,reason,reference,actor,created_at
                   FROM credit_transactions WHERE user_id=?
                   ORDER BY created_at DESC, id DESC LIMIT ?""",
                (user_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def adjust(
        self,
        user_id: str,
        amount: int,
        *,
        kind: str,
        reason: str,
        actor: str | None = None,
        reference: str | None = None,
    ) -> dict:
        amount = int(amount)
        if amount == 0:
            raise ValueError("Credit adjustment cannot be zero")
        kind = (kind or "adjustment").strip().lower()
        if kind not in _ALLOWED_KINDS:
            raise ValueError("Unsupported credit transaction kind")
        reason = (reason or "Credit adjustment").strip()[:240]
        if not reason:
            raise ValueError("A credit transaction reason is required")
        actor = (actor or "system").strip()[:120] or "system"
        reference = (reference or "").strip()[:180] or None

        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            user_id = self._ensure_wallet(con, user_id)

            if reference:
                existing = con.execute(
                    "SELECT * FROM credit_transactions WHERE reference=?", (reference,)
                ).fetchone()
                if existing:
                    if (
                        existing["user_id"] != user_id
                        or int(existing["amount"]) != amount
                        or existing["kind"] != kind
                    ):
                        raise ValueError("Credit reference has already been used for a different transaction")
                    return dict(existing)

            row = con.execute("SELECT balance FROM credit_wallets WHERE user_id=?", (user_id,)).fetchone()
            current = int(row["balance"] if row else 0)
            new_balance = current + amount
            if new_balance < 0:
                raise ValueError("Insufficient credits")

            now = _iso()
            tx_id = uuid4().hex
            con.execute(
                "UPDATE credit_wallets SET balance=?,updated_at=? WHERE user_id=?",
                (new_balance, now, user_id),
            )
            con.execute(
                """INSERT INTO credit_transactions
                   (id,user_id,amount,balance_after,kind,reason,reference,actor,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (tx_id, user_id, amount, new_balance, kind, reason, reference, actor, now),
            )
            tx = con.execute("SELECT * FROM credit_transactions WHERE id=?", (tx_id,)).fetchone()
            return dict(tx)

    def grant(
        self,
        user_id: str,
        credits: int,
        *,
        reason: str,
        actor: str,
        reference: str | None = None,
    ) -> dict:
        credits = int(credits)
        if credits <= 0:
            raise ValueError("Credits granted must be greater than zero")
        return self.adjust(
            user_id,
            credits,
            kind="grant",
            reason=reason,
            actor=actor,
            reference=reference,
        )

    def spend(
        self,
        user_id: str,
        credits: int,
        *,
        reason: str,
        reference: str | None = None,
        actor: str = "member",
    ) -> dict:
        credits = int(credits)
        if credits <= 0:
            raise ValueError("Credits spent must be greater than zero")
        return self.adjust(
            user_id,
            -credits,
            kind="spend",
            reason=reason,
            actor=actor,
            reference=reference,
        )

    def verify_integrity(self, user_id: str) -> dict:
        """Verify the stored balance against the immutable transaction chain."""
        with self._connect() as con:
            user_id = self._ensure_wallet(con, user_id)
            wallet = con.execute(
                "SELECT balance FROM credit_wallets WHERE user_id=?", (user_id,)
            ).fetchone()
            rows = con.execute(
                """SELECT id,amount,balance_after FROM credit_transactions
                   WHERE user_id=? ORDER BY created_at ASC,id ASC""",
                (user_id,),
            ).fetchall()

        running = 0
        valid = True
        invalid_transaction_id = None
        for row in rows:
            running += int(row["amount"])
            if running != int(row["balance_after"]):
                valid = False
                invalid_transaction_id = row["id"]
                break
        stored = int(wallet["balance"] if wallet else 0)
        if running != stored:
            valid = False
        return {
            "valid": valid,
            "calculated_balance": running,
            "stored_balance": stored,
            "transactions_checked": len(rows),
            "invalid_transaction_id": invalid_transaction_id,
            "unit": _CREDIT_UNIT,
        }


store = CreditWalletStore()


class AdminCreditGrantRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=128)
    credits: int = Field(gt=0, le=1_000_000)
    reason: str = Field(min_length=2, max_length=240)
    reference: str | None = Field(default=None, max_length=180)


def _member_user_id(request: Request) -> str:
    member = getattr(request.state, "member", None)
    user_id = getattr(member, "user_id", None)
    if not user_id:
        raise HTTPException(401, "Active membership required")
    return str(user_id)


@router.get("/credits/balance")
def credit_balance(request: Request):
    user_id = _member_user_id(request)
    return {
        "user_id": user_id,
        "balance": store.balance(user_id),
        "unit": _CREDIT_UNIT,
        "monetary_currency": None,
        "subscription_effect": "none",
        "esp_role_effect": "none",
    }


@router.get("/credits/history")
def credit_history(request: Request, limit: int = 100):
    user_id = _member_user_id(request)
    return {
        "user_id": user_id,
        "balance": store.balance(user_id),
        "unit": _CREDIT_UNIT,
        "transactions": store.transactions(user_id, limit=limit),
        "subscription_effect": "none",
        "esp_role_effect": "none",
    }


@router.post("/admin/credits/grant")
def admin_credit_grant(
    payload: AdminCreditGrantRequest,
    x_lss_admin_key: str | None = Header(default=None),
):
    require_admin(x_lss_admin_key)
    try:
        transaction = store.grant(
            payload.user_id,
            payload.credits,
            reason=payload.reason,
            actor="admin_key",
            reference=payload.reference,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "granted": True,
        "user_id": payload.user_id,
        "balance": transaction["balance_after"],
        "transaction": transaction,
        "unit": _CREDIT_UNIT,
        "subscription_effect": "none",
        "esp_role_effect": "none",
    }


__all__ = ["CreditWalletStore", "router", "store"]
