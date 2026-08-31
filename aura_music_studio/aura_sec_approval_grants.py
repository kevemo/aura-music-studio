from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

from .accounts import AccountStore
from .aura_sec_protocol import ActionRisk


CONFIRMATION_APPROVAL_TTL_SECONDS = 600
STRONG_REAUTH_APPROVAL_TTL_SECONDS = 300
_HIGH_RISK = {
    ActionRisk.CONFIRMATION_REQUIRED,
    ActionRisk.STRONG_REAUTH_REQUIRED,
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: str | datetime) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        raise PermissionError("Aura Sec approval timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def approval_lifetime_seconds(risk: ActionRisk | str) -> int | None:
    value = risk if isinstance(risk, ActionRisk) else ActionRisk(str(risk))
    if value is ActionRisk.CONFIRMATION_REQUIRED:
        return CONFIRMATION_APPROVAL_TTL_SECONDS
    if value is ActionRisk.STRONG_REAUTH_REQUIRED:
        return STRONG_REAUTH_APPROVAL_TTL_SECONDS
    return None


def action_approval_deadline(action: dict) -> datetime | None:
    try:
        risk = ActionRisk(str(action.get("risk_class") or ""))
    except ValueError as exc:
        raise PermissionError("Stored Aura Sec action risk class is invalid") from exc
    ttl = approval_lifetime_seconds(risk)
    if ttl is None:
        return None
    approved_at = action.get("approved_at")
    if not approved_at:
        raise PermissionError("High-risk Aura Sec action has no approval timestamp")
    return _aware(approved_at) + timedelta(seconds=ttl)


def is_action_authorization_current(action: dict, *, now: datetime | None = None) -> bool:
    deadline = action_approval_deadline(action)
    if deadline is None:
        return True
    current = (now or _now()).astimezone(timezone.utc)
    return current < deadline


class AuraSecApprovalGrantStore:
    """Immutable, short-lived authorization evidence for high-risk Aura Sec actions.

    Grant ids intentionally equal their one-shot action ids. This preserves compatibility
    with the existing signed-command protocol while changing the security meaning of
    `approval_id`: it now identifies a concrete immutable grant row, not merely a mutable
    action status. A fresh authorization must use a successor action and therefore receives
    a different id. Grant rows are created in the same transaction that changes an action
    from proposed to approved; missing legacy grants fail closed rather than being inferred
    later from mutable state.
    """

    def __init__(self, accounts: AccountStore | None = None):
        self.accounts = accounts or AccountStore()
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
                CREATE TABLE IF NOT EXISTS aura_sec_action_approval_grants (
                    id TEXT PRIMARY KEY,
                    action_id TEXT NOT NULL UNIQUE,
                    user_id TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    risk_class TEXT NOT NULL,
                    authorization_method TEXT NOT NULL,
                    approved_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    action_snapshot_digest TEXT NOT NULL,
                    FOREIGN KEY(action_id) REFERENCES aura_sec_actions(id) ON DELETE CASCADE,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY(device_id) REFERENCES aura_sec_devices(id) ON DELETE CASCADE,
                    CHECK(id = action_id),
                    CHECK(risk_class IN ('confirmation_required','strong_reauth_required')),
                    CHECK(length(action_snapshot_digest) = 64)
                );
                CREATE INDEX IF NOT EXISTS idx_aura_sec_approval_grants_user_device_expiry
                ON aura_sec_action_approval_grants(user_id,device_id,expires_at);
                CREATE TRIGGER IF NOT EXISTS trg_aura_sec_approval_grants_immutable
                BEFORE UPDATE ON aura_sec_action_approval_grants
                BEGIN
                    SELECT RAISE(ABORT, 'Aura Sec approval grants are immutable');
                END;
                """
            )

    @staticmethod
    def _snapshot_payload(action: dict) -> bytes:
        payload = {
            "action_id": str(action.get("id") or ""),
            "action_type": str(action.get("action_type") or ""),
            "approved_at": str(action.get("approved_at") or ""),
            "details": action.get("details") or {},
            "device_id": str(action.get("device_id") or ""),
            "incident_id": action.get("incident_id"),
            "requested_at": str(action.get("requested_at") or ""),
            "risk_class": str(action.get("risk_class") or ""),
            "user_id": str(action.get("user_id") or ""),
        }
        return (
            "AURA-SEC-ACTION-APPROVAL-SNAPSHOT-V1\n"
            + json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            + "\n"
        ).encode("utf-8")

    @classmethod
    def _snapshot_digest(cls, action: dict) -> str:
        return hashlib.sha256(cls._snapshot_payload(action)).hexdigest()

    @staticmethod
    def _method_for(risk: ActionRisk) -> str:
        if risk is ActionRisk.STRONG_REAUTH_REQUIRED:
            return "strong_reauth"
        if risk is ActionRisk.CONFIRMATION_REQUIRED:
            return "explicit_confirmation"
        raise PermissionError("Automatic Aura Sec actions do not use approval grants")

    def record_approved_action(
        self,
        con: sqlite3.Connection,
        user_id: str,
        action: dict,
        *,
        approved_at: datetime,
    ) -> dict | None:
        """Insert a high-risk grant inside the caller's action-approval transaction."""
        if str(action.get("user_id") or "") != user_id:
            raise PermissionError("Aura Sec approval grant user does not match action owner")
        try:
            risk = ActionRisk(str(action.get("risk_class") or ""))
        except ValueError as exc:
            raise PermissionError("Stored Aura Sec action risk class is invalid") from exc
        if risk not in _HIGH_RISK:
            return None

        approved = approved_at.astimezone(timezone.utc)
        snapshot = dict(action)
        snapshot["status"] = "approved"
        snapshot["approved_at"] = approved.isoformat()
        deadline = action_approval_deadline(snapshot)
        assert deadline is not None
        action_id = str(snapshot.get("id") or "")
        device_id = str(snapshot.get("device_id") or "")
        digest = self._snapshot_digest(snapshot)
        try:
            con.execute(
                """INSERT INTO aura_sec_action_approval_grants
                   (id,action_id,user_id,device_id,risk_class,authorization_method,
                    approved_at,expires_at,action_snapshot_digest)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    action_id,
                    action_id,
                    user_id,
                    device_id,
                    risk.value,
                    self._method_for(risk),
                    approved.isoformat(),
                    deadline.isoformat(),
                    digest,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise PermissionError("Aura Sec approval grant already exists or could not be recorded") from exc
        row = con.execute(
            "SELECT * FROM aura_sec_action_approval_grants WHERE user_id=? AND action_id=?",
            (user_id, action_id),
        ).fetchone()
        if row is None:
            raise PermissionError("Aura Sec approval grant could not be durably recorded")
        return dict(row)

    def _validate_row(self, row: sqlite3.Row, user_id: str, action: dict) -> dict:
        action_id = str(action.get("id") or "")
        device_id = str(action.get("device_id") or "")
        try:
            risk = ActionRisk(str(action.get("risk_class") or ""))
        except ValueError as exc:
            raise PermissionError("Stored Aura Sec action risk class is invalid") from exc
        expected_digest = self._snapshot_digest(action)
        expected_deadline = action_approval_deadline(action)
        if expected_deadline is None:
            raise PermissionError("Automatic Aura Sec actions do not use approval grants")

        checks = (
            (str(row["id"]), action_id, "grant id"),
            (str(row["action_id"]), action_id, "action id"),
            (str(row["user_id"]), user_id, "user"),
            (str(row["device_id"]), device_id, "device"),
            (str(row["risk_class"]), risk.value, "risk class"),
            (str(row["authorization_method"]), self._method_for(risk), "authorization method"),
            (str(row["action_snapshot_digest"]), expected_digest, "action snapshot"),
        )
        for actual, expected, label in checks:
            if not secrets.compare_digest(actual, expected):
                raise PermissionError(f"Aura Sec approval grant {label} does not match approved action")

        approved_at = _aware(str(row["approved_at"]))
        action_approved_at = _aware(str(action.get("approved_at") or ""))
        expires_at = _aware(str(row["expires_at"]))
        if approved_at != action_approved_at:
            raise PermissionError("Aura Sec approval grant timestamp does not match approved action")
        if expires_at != expected_deadline:
            raise PermissionError("Aura Sec approval grant expiry does not match server policy")
        return dict(row)

    def ensure_for_approved_action(
        self,
        user_id: str,
        action: dict,
        *,
        now: datetime | None = None,
    ) -> dict | None:
        """Validate a pre-existing immutable high-risk grant and enforce its deadline."""
        if str(action.get("user_id") or "") != user_id:
            raise PermissionError("Aura Sec approval grant user does not match action owner")
        if str(action.get("status") or "") != "approved":
            raise PermissionError("Aura Sec action must be approved before authorization is usable")
        try:
            risk = ActionRisk(str(action.get("risk_class") or ""))
        except ValueError as exc:
            raise PermissionError("Stored Aura Sec action risk class is invalid") from exc
        if risk not in _HIGH_RISK:
            return None

        action_id = str(action.get("id") or "")
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM aura_sec_action_approval_grants WHERE action_id=?",
                (action_id,),
            ).fetchone()
        if row is None:
            raise PermissionError(
                "Aura Sec high-risk approval grant is missing; fresh authorization is required"
            )

        grant = self._validate_row(row, user_id, action)
        deadline = _aware(str(grant["expires_at"]))
        current = (now or _now()).astimezone(timezone.utc)
        if current >= deadline:
            raise PermissionError("Aura Sec approval grant expired; fresh authorization is required")
        return grant

    def get(self, user_id: str, action_id: str) -> dict:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM aura_sec_action_approval_grants WHERE user_id=? AND action_id=?",
                (user_id, action_id),
            ).fetchone()
        if not row:
            raise ValueError("Aura Sec approval grant not found")
        return dict(row)


__all__ = [
    "AuraSecApprovalGrantStore",
    "CONFIRMATION_APPROVAL_TTL_SECONDS",
    "STRONG_REAUTH_APPROVAL_TTL_SECONDS",
    "action_approval_deadline",
    "approval_lifetime_seconds",
    "is_action_authorization_current",
]
