from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .accounts import AccountStore
from .aura_sec_protocol import ActionRisk
from .aura_sec_store import AuraSecStore


APPROVAL_TTL_SECONDS: dict[ActionRisk, int] = {
    ActionRisk.READ_ONLY: 1800,
    ActionRisk.LOW_RISK: 1800,
    ActionRisk.CONFIRMATION_REQUIRED: 600,
    ActionRisk.STRONG_REAUTH_REQUIRED: 300,
}


def _utc(value: datetime | None = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("Aura Sec approval timestamps must be timezone-aware")
    return current.astimezone(timezone.utc)


def _parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class ApprovalWindow:
    action_id: str
    risk: ActionRisk
    approved_at: datetime
    expires_at: datetime
    remaining_seconds: float


class AuraSecApprovalLifetime:
    """Risk-bounded server-side lifetime and re-authorization policy for Aura Sec actions.

    Approval freshness is derived from the persisted approval timestamp. There is no client
    supplied expiry and no way to extend a previously granted approval. Once stale, the action
    is moved to ``expired`` and must be explicitly returned to ``proposed`` before the normal
    approval flow can run again. Strong-re-auth actions therefore require the strong re-auth
    check again on their next approval.
    """

    def __init__(
        self,
        accounts: AccountStore | None = None,
        security: AuraSecStore | None = None,
    ):
        self.accounts = accounts or AccountStore()
        self.security = security or AuraSecStore(self.accounts)

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.accounts.db_path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA journal_mode=WAL")
        return con

    @staticmethod
    def _risk(action: dict) -> ActionRisk:
        try:
            return ActionRisk(str(action.get("risk_class") or ""))
        except ValueError as exc:
            raise PermissionError("Aura Sec action has an invalid stored risk class") from exc

    def window(self, action: dict, *, now: datetime | None = None) -> ApprovalWindow:
        if action.get("status") != "approved":
            raise PermissionError("Aura Sec action must be approved before command issuance")
        approved_at = _parse_utc(action.get("approved_at"))
        if approved_at is None:
            raise PermissionError("Aura Sec approval has no trustworthy approval timestamp")
        risk = self._risk(action)
        expires_at = approved_at + timedelta(seconds=APPROVAL_TTL_SECONDS[risk])
        current = _utc(now)
        return ApprovalWindow(
            action_id=str(action["id"]),
            risk=risk,
            approved_at=approved_at,
            expires_at=expires_at,
            remaining_seconds=(expires_at - current).total_seconds(),
        )

    def _expire_if_still_approved(self, user_id: str, action_id: str) -> None:
        with self._connect() as con:
            con.execute(
                """UPDATE aura_sec_actions
                   SET status='expired'
                   WHERE user_id=? AND id=? AND status='approved'""",
                (user_id, action_id),
            )

    def require_fresh(
        self,
        user_id: str,
        action_id: str,
        *,
        now: datetime | None = None,
        minimum_remaining_seconds: int = 0,
    ) -> dict:
        minimum = int(minimum_remaining_seconds)
        if not 0 <= minimum <= 900:
            raise ValueError("Aura Sec minimum approval lifetime is invalid")

        action = self.security.get_action(user_id, action_id)
        if action.get("status") != "approved":
            raise PermissionError("Aura Sec action must be approved before command issuance")

        try:
            window = self.window(action, now=now)
        except PermissionError:
            self._expire_if_still_approved(user_id, action_id)
            raise

        if window.remaining_seconds < minimum or window.remaining_seconds <= 0:
            self._expire_if_still_approved(user_id, action_id)
            raise PermissionError(
                "Aura Sec approval expired or is too close to expiry; explicit re-authorization is required"
            )

        fresh = dict(action)
        fresh["approval_expires_at"] = window.expires_at.isoformat()
        fresh["approval_remaining_seconds"] = window.remaining_seconds
        return fresh

    def expire_stale_approvals(
        self,
        user_id: str,
        *,
        device_id: str | None = None,
        now: datetime | None = None,
    ) -> int:
        current = _utc(now)
        params: list[str] = [user_id]
        where = "user_id=? AND status='approved'"
        if device_id is not None:
            where += " AND device_id=?"
            params.append(device_id)
        with self._connect() as con:
            rows = con.execute(
                f"SELECT id,risk_class,approved_at FROM aura_sec_actions WHERE {where}",
                tuple(params),
            ).fetchall()

        expired = 0
        for row in rows:
            action = {
                "id": row["id"],
                "risk_class": row["risk_class"],
                "approved_at": row["approved_at"],
                "status": "approved",
            }
            try:
                window = self.window(action, now=current)
            except PermissionError:
                self._expire_if_still_approved(user_id, str(row["id"]))
                expired += 1
                continue
            if window.remaining_seconds <= 0:
                self._expire_if_still_approved(user_id, str(row["id"]))
                expired += 1
        return expired

    def reauthorize_action(self, user_id: str, action_id: str) -> dict:
        """Return only an expired, never-executed action to the normal approval flow."""
        action = self.security.get_action(user_id, action_id)
        if action.get("status") != "expired":
            raise ValueError("Only expired Aura Sec actions can be re-authorized")
        if action.get("executed_at") or action.get("verified_at"):
            raise PermissionError("Executed Aura Sec actions cannot be re-authorized")
        device = self.security.get_device(user_id, action["device_id"])
        if device.get("status") == "revoked" or device.get("revoked_at"):
            raise PermissionError("Revoked Aura Sec device cannot receive re-authorized actions")

        with self._connect() as con:
            updated = con.execute(
                """UPDATE aura_sec_actions
                   SET status='proposed',approved_at=NULL
                   WHERE user_id=? AND id=? AND status='expired'
                     AND executed_at IS NULL AND verified_at IS NULL""",
                (user_id, action_id),
            )
            if updated.rowcount != 1:
                raise PermissionError("Aura Sec action state changed before re-authorization")
        return self.security.get_action(user_id, action_id)


__all__ = [
    "APPROVAL_TTL_SECONDS",
    "ApprovalWindow",
    "AuraSecApprovalLifetime",
]
