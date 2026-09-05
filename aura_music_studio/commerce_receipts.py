from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class CommerceReceiptStore:
    """Append-only evidence for verified non-subscription purchases.

    Subscription periods already have their authoritative ledger in ``subscription_payments``.
    This store captures other paid purchases (currently Stripe credit top-ups) using only the
    finance fields required for owner reporting. Raw provider payloads, card data and settlement
    bank details are deliberately not persisted here.
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
                CREATE TABLE IF NOT EXISTS commerce_receipts (
                    id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    reference TEXT NOT NULL UNIQUE,
                    user_id TEXT NOT NULL,
                    plan_id TEXT,
                    pack_id TEXT,
                    amount_minor INTEGER NOT NULL CHECK(amount_minor >= 0),
                    currency TEXT NOT NULL,
                    units INTEGER,
                    status TEXT NOT NULL,
                    verified_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_commerce_receipts_verified
                    ON commerce_receipts(verified_at DESC, id DESC);
                CREATE INDEX IF NOT EXISTS idx_commerce_receipts_kind
                    ON commerce_receipts(kind, status, currency);
                """
            )

    def record(
        self,
        *,
        provider: str,
        kind: str,
        reference: str,
        user_id: str,
        amount_minor: int,
        currency: str,
        status: str = "paid",
        plan_id: str | None = None,
        pack_id: str | None = None,
        units: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        provider = (provider or "").strip().lower()
        kind = (kind or "").strip().lower()
        reference = (reference or "").strip()
        user_id = (user_id or "").strip()
        currency = (currency or "").strip().upper()
        status = (status or "").strip().lower()
        amount_minor = int(amount_minor)
        if not provider or not kind or not reference or not user_id:
            raise ValueError("Commerce receipt requires provider, kind, reference and user id")
        if amount_minor < 0:
            raise ValueError("Commerce receipt amount cannot be negative")
        if len(currency) != 3 or not currency.isalpha():
            raise ValueError("Commerce receipt requires a three-letter currency")
        if status not in {"paid", "refunded", "partially_refunded"}:
            raise ValueError("Unsupported commerce receipt status")
        if units is not None and int(units) < 0:
            raise ValueError("Commerce receipt units cannot be negative")
        safe_metadata = metadata or {}
        encoded_metadata = json.dumps(safe_metadata, sort_keys=True, separators=(",", ":"))
        if len(encoded_metadata) > 4000:
            raise ValueError("Commerce receipt metadata is too large")

        with self._connect() as con:
            user = con.execute("SELECT id FROM users WHERE id=?", (user_id,)).fetchone()
            if not user:
                raise ValueError("Commerce receipt references an unknown user")
            existing = con.execute("SELECT * FROM commerce_receipts WHERE reference=?", (reference,)).fetchone()
            if existing:
                row = dict(existing)
                expected = {
                    "provider": provider,
                    "kind": kind,
                    "user_id": user_id,
                    "amount_minor": amount_minor,
                    "currency": currency,
                    "status": status,
                    "plan_id": plan_id,
                    "pack_id": pack_id,
                    "units": int(units) if units is not None else None,
                }
                if any(row.get(key) != value for key, value in expected.items()):
                    raise ValueError("Commerce receipt reference was reused with different financial data")
                return row

            receipt_id = uuid4().hex
            con.execute(
                """INSERT INTO commerce_receipts
                   (id,provider,kind,reference,user_id,plan_id,pack_id,amount_minor,currency,units,status,verified_at,metadata_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    receipt_id,
                    provider,
                    kind,
                    reference,
                    user_id,
                    plan_id,
                    pack_id,
                    amount_minor,
                    currency,
                    int(units) if units is not None else None,
                    status,
                    _iso(),
                    encoded_metadata,
                ),
            )
            row = con.execute("SELECT * FROM commerce_receipts WHERE id=?", (receipt_id,)).fetchone()
        return dict(row)

    def recent(self, *, kind: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        with self._connect() as con:
            if kind:
                rows = con.execute(
                    """SELECT id,provider,kind,reference,user_id,plan_id,pack_id,amount_minor,currency,units,status,verified_at
                       FROM commerce_receipts WHERE kind=? ORDER BY verified_at DESC,id DESC LIMIT ?""",
                    (kind, limit),
                ).fetchall()
            else:
                rows = con.execute(
                    """SELECT id,provider,kind,reference,user_id,plan_id,pack_id,amount_minor,currency,units,status,verified_at
                       FROM commerce_receipts ORDER BY verified_at DESC,id DESC LIMIT ?""",
                    (limit,),
                ).fetchall()
        return [dict(row) for row in rows]


__all__ = ["CommerceReceiptStore"]
