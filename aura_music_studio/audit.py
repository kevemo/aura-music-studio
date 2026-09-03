from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from uuid import uuid4

from .accounts import AccountStore
from .aura_sec_dlp import redact_text, sanitize_audit_details


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AuditLedger:
    """Append-only ESP administrative audit trail with hash chaining and DLP persistence.

    The local chain is not a substitute for external immutable logging, but it makes silent row
    modification detectable and gives ESP a provider-independent history from day one. Every
    append passes through AuraSec DLP at the persistence boundary so callers cannot accidentally
    place credentials, direct personal data, binary payloads or non-standard JSON values into the
    long-lived security ledger.
    """

    def __init__(self, store: AccountStore | None = None):
        self.store = store or AccountStore()
        self.db_path = self.store.db_path
        self._init_schema()

    def _connect(self):
        con = sqlite3.connect(self.db_path, timeout=30)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _init_schema(self) -> None:
        with self._connect() as con:
            con.execute(
                """CREATE TABLE IF NOT EXISTS admin_audit_log (
                    id TEXT PRIMARY KEY,
                    occurred_at TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    action TEXT NOT NULL,
                    subject_user_id TEXT,
                    details_json TEXT NOT NULL,
                    previous_hash TEXT,
                    entry_hash TEXT NOT NULL UNIQUE
                )"""
            )
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_admin_audit_time ON admin_audit_log(occurred_at DESC)"
            )

    @staticmethod
    def _hash(payload: dict) -> str:
        canonical = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def append(
        self,
        *,
        actor: str,
        action: str,
        subject_user_id: str | None = None,
        details: dict | None = None,
    ) -> dict:
        occurred_at = _now()
        safe_actor = redact_text(actor or "ESP administrator", max_length=160)
        safe_action = redact_text(action or "unspecified", max_length=160)
        safe_subject = (
            redact_text(subject_user_id, max_length=160) if subject_user_id is not None else None
        )
        safe_details = sanitize_audit_details(details or {})
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            previous = con.execute(
                "SELECT entry_hash FROM admin_audit_log ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
            previous_hash = previous["entry_hash"] if previous else None
            payload = {
                "id": uuid4().hex,
                "occurred_at": occurred_at,
                "actor": safe_actor,
                "action": safe_action,
                "subject_user_id": safe_subject,
                "details": safe_details,
                "previous_hash": previous_hash,
            }
            entry_hash = self._hash(payload)
            con.execute(
                """INSERT INTO admin_audit_log
                   (id,occurred_at,actor,action,subject_user_id,details_json,previous_hash,entry_hash)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    payload["id"],
                    payload["occurred_at"],
                    payload["actor"],
                    payload["action"],
                    payload["subject_user_id"],
                    json.dumps(payload["details"], ensure_ascii=False, allow_nan=False),
                    previous_hash,
                    entry_hash,
                ),
            )
            con.commit()
        return {**payload, "entry_hash": entry_hash}

    def recent(self, limit: int = 100) -> list[dict]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT * FROM admin_audit_log ORDER BY occurred_at DESC LIMIT ?",
                (max(1, min(int(limit), 1000)),),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            try:
                item["details"] = json.loads(item.pop("details_json"))
            except Exception:
                item["details"] = {}
                item.pop("details_json", None)
            result.append(item)
        return result

    def verify_chain(self) -> dict:
        with self._connect() as con:
            rows = con.execute("SELECT * FROM admin_audit_log ORDER BY rowid ASC").fetchall()
        previous_hash = None
        checked = 0
        for row in rows:
            try:
                details = json.loads(row["details_json"] or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                return {"valid": False, "checked": checked, "broken_at": row["id"]}
            payload = {
                "id": row["id"],
                "occurred_at": row["occurred_at"],
                "actor": row["actor"],
                "action": row["action"],
                "subject_user_id": row["subject_user_id"],
                "details": details,
                "previous_hash": row["previous_hash"],
            }
            try:
                calculated_hash = self._hash(payload)
            except (TypeError, ValueError):
                return {"valid": False, "checked": checked, "broken_at": row["id"]}
            if row["previous_hash"] != previous_hash or calculated_hash != row["entry_hash"]:
                return {"valid": False, "checked": checked, "broken_at": row["id"]}
            previous_hash = row["entry_hash"]
            checked += 1
        return {"valid": True, "checked": checked, "head_hash": previous_hash}
