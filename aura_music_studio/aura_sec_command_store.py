from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from .accounts import AccountStore
from .aura_sec_protocol import ActionRisk, ActionType, CommandReceipt, SecurityCommand
from .aura_sec_store import AuraSecStore


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).isoformat()


def _hash_nonce(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


_ALLOWED_RECEIPT_TRANSITIONS: dict[str, set[str]] = {
    "issued": {"received", "rejected", "failed"},
    "received": {"executed", "failed"},
    "executed": {"verified", "failed"},
    "rejected": set(),
    "failed": set(),
    "verified": set(),
}


class AuraSecCommandStore:
    """Persist short-lived typed endpoint commands and verified receipts.

    The command payload is constructed from the bounded `SecurityCommand` schema. There is
    no storage or API field for arbitrary command lines, scripts, PowerShell or shell text.
    """

    def __init__(self, accounts: AccountStore | None = None, security: AuraSecStore | None = None):
        self.accounts = accounts or AccountStore()
        self.security = security or AuraSecStore(self.accounts)
        self._init_schema()

    def _connect(self):
        con = sqlite3.connect(self.accounts.db_path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _init_schema(self) -> None:
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS aura_sec_commands (
                    id TEXT PRIMARY KEY,
                    action_id TEXT NOT NULL UNIQUE,
                    user_id TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    risk_class TEXT NOT NULL,
                    policy_version TEXT NOT NULL,
                    nonce_hash TEXT NOT NULL,
                    parameters_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'issued',
                    issued_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    last_receipt_at TEXT,
                    result_code TEXT,
                    evidence_digest TEXT,
                    updated_at TEXT NOT NULL,
                    UNIQUE(device_id, nonce_hash),
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY(device_id) REFERENCES aura_sec_devices(id) ON DELETE CASCADE,
                    FOREIGN KEY(action_id) REFERENCES aura_sec_actions(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_aura_sec_commands_device_status
                ON aura_sec_commands(device_id, status, expires_at);
                """
            )

    def issue_approved_action(
        self,
        user_id: str,
        action_id: str,
        *,
        policy_version: str,
        nonce: str,
        parameters: dict | None = None,
        ttl_seconds: int = 300,
    ) -> SecurityCommand:
        action = self.security.get_action(user_id, action_id)
        if action.get("status") != "approved":
            raise PermissionError("Aura Sec action must be approved before command issuance")
        device = self.security.get_device(user_id, action["device_id"])
        if device.get("status") == "revoked" or device.get("revoked_at"):
            raise PermissionError("Cannot issue Aura Sec command to a revoked device")
        if not 30 <= int(ttl_seconds) <= 900:
            raise ValueError("Aura Sec command lifetime must be between 30 and 900 seconds")

        try:
            action_type = ActionType(action["action_type"])
            risk = ActionRisk(action["risk_class"])
        except ValueError as exc:
            raise ValueError("Stored Aura Sec action is not compatible with the bounded command protocol") from exc

        issued = _now()
        approval_id = action_id if risk in {
            ActionRisk.CONFIRMATION_REQUIRED,
            ActionRisk.STRONG_REAUTH_REQUIRED,
        } else None
        command = SecurityCommand(
            command_id=uuid4().hex,
            device_id=action["device_id"],
            action=action_type,
            risk=risk,
            issued_at=issued,
            expires_at=issued + timedelta(seconds=int(ttl_seconds)),
            policy_version=(policy_version or "").strip(),
            nonce=(nonce or "").strip(),
            approval_id=approval_id,
            parameters=parameters or {},
        )

        with self._connect() as con:
            try:
                con.execute(
                    """INSERT INTO aura_sec_commands
                       (id,action_id,user_id,device_id,action_type,risk_class,policy_version,nonce_hash,
                        parameters_json,status,issued_at,expires_at,updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,'issued',?,?,?)""",
                    (
                        command.command_id,
                        action_id,
                        user_id,
                        command.device_id,
                        command.action.value,
                        command.risk.value,
                        command.policy_version,
                        _hash_nonce(command.nonce),
                        json.dumps(command.parameters, separators=(",", ":"), ensure_ascii=False),
                        command.issued_at.isoformat(),
                        command.expires_at.isoformat(),
                        _iso(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("Aura Sec action was already issued or nonce was replayed") from exc
        return command

    def get(self, user_id: str, command_id: str) -> dict:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM aura_sec_commands WHERE user_id=? AND id=?",
                (user_id, command_id),
            ).fetchone()
        if not row:
            raise ValueError("Aura Sec command not found")
        item = dict(row)
        try:
            item["parameters"] = json.loads(item.pop("parameters_json") or "{}")
        except json.JSONDecodeError:
            item["parameters"] = {}
        item.pop("nonce_hash", None)
        return item

    def accept_verified_receipt(
        self,
        user_id: str,
        receipt: CommandReceipt,
        *,
        signature_verified: bool,
        now: datetime | None = None,
    ) -> dict:
        if not signature_verified:
            raise PermissionError("Unverified Aura Sec command receipt rejected")
        command = self.get(user_id, receipt.command_id)
        if command["device_id"] != receipt.device_id:
            raise PermissionError("Aura Sec receipt device does not match command target")

        current = (now or _now()).astimezone(timezone.utc)
        expiry = datetime.fromisoformat(command["expires_at"]).astimezone(timezone.utc)
        if current > expiry and receipt.status not in {"failed", "rejected"}:
            raise PermissionError("Expired Aura Sec command cannot report a new successful state")

        old_status = command["status"]
        if receipt.status not in _ALLOWED_RECEIPT_TRANSITIONS.get(old_status, set()):
            raise ValueError(f"Invalid Aura Sec command transition {old_status} -> {receipt.status}")

        with self._connect() as con:
            con.execute(
                """UPDATE aura_sec_commands
                   SET status=?,last_receipt_at=?,result_code=?,evidence_digest=?,updated_at=?
                   WHERE user_id=? AND id=?""",
                (
                    receipt.status,
                    receipt.occurred_at.isoformat(),
                    receipt.result_code,
                    receipt.evidence_digest.lower() if receipt.evidence_digest else None,
                    _iso(current),
                    user_id,
                    receipt.command_id,
                ),
            )
            if receipt.status == "executed":
                con.execute(
                    "UPDATE aura_sec_actions SET status='executed',executed_at=? WHERE user_id=? AND id=?",
                    (receipt.occurred_at.isoformat(), user_id, command["action_id"]),
                )
            elif receipt.status == "verified":
                con.execute(
                    """UPDATE aura_sec_actions
                       SET status='verified',verified_at=? WHERE user_id=? AND id=?""",
                    (receipt.occurred_at.isoformat(), user_id, command["action_id"]),
                )
            elif receipt.status in {"failed", "rejected"}:
                con.execute(
                    "UPDATE aura_sec_actions SET status=? WHERE user_id=? AND id=?",
                    (receipt.status, user_id, command["action_id"]),
                )
        return self.get(user_id, receipt.command_id)


__all__ = ["AuraSecCommandStore"]
