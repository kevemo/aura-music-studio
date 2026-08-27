from __future__ import annotations

import hashlib
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from .accounts import AccountStore
from .aura_sec_protocol import ActionRisk
from .aura_sec_store import AuraSecStore


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).isoformat()


def _hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class AuraSecApprovalGateway:
    """Session-bound one-time member approval challenges for disruptive actions.

    Approval is intentionally a separate phase from command issuance. Completing a
    challenge can move a bounded action from `proposed` to `approved`; only a later
    verified native-device poll can obtain the typed command.
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
                CREATE TABLE IF NOT EXISTS aura_sec_approval_challenges (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    action_id TEXT NOT NULL,
                    session_hash TEXT NOT NULL,
                    token_hash TEXT NOT NULL UNIQUE,
                    risk_class TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    consumed_at TEXT,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY(action_id) REFERENCES aura_sec_actions(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_aura_sec_approval_pending
                ON aura_sec_approval_challenges(user_id,action_id,status,expires_at);
                """
            )

    def _validated_action(self, user_id: str, action_id: str) -> dict:
        if self.security.licence(user_id).get("status") != "active":
            raise PermissionError("Active Aura Sec licence required to approve security actions")
        action = self.security.get_action(user_id, action_id)
        if action.get("status") != "proposed":
            raise ValueError("Only proposed Aura Sec actions can enter member approval")
        if action.get("risk_class") not in {
            ActionRisk.CONFIRMATION_REQUIRED.value,
            ActionRisk.STRONG_REAUTH_REQUIRED.value,
        }:
            raise PermissionError("This Aura Sec action does not use the human approval gateway")
        device = self.security.get_device(user_id, action["device_id"])
        if device.get("status") == "revoked" or device.get("revoked_at"):
            raise PermissionError("Revoked Aura Sec device actions cannot be approved")
        return action

    def create_challenge(
        self,
        user_id: str,
        action_id: str,
        *,
        session_token: str,
        ttl_minutes: int = 5,
    ) -> dict:
        if not session_token or len(session_token) < 16:
            raise PermissionError("A valid member session is required for Aura Sec approval")
        if not 1 <= int(ttl_minutes) <= 10:
            raise ValueError("Aura Sec approval challenge lifetime must be between 1 and 10 minutes")
        action = self._validated_action(user_id, action_id)
        challenge_id = uuid4().hex
        token = secrets.token_urlsafe(32)
        now = _now()
        expires = now + timedelta(minutes=int(ttl_minutes))
        session_hash = _hash_secret(session_token)
        with self._connect() as con:
            con.execute(
                """UPDATE aura_sec_approval_challenges
                   SET status='superseded'
                   WHERE user_id=? AND action_id=? AND session_hash=? AND status='pending'""",
                (user_id, action_id, session_hash),
            )
            con.execute(
                """INSERT INTO aura_sec_approval_challenges
                   (id,user_id,action_id,session_hash,token_hash,risk_class,status,created_at,expires_at)
                   VALUES (?,?,?,?,?,?,'pending',?,?)""",
                (
                    challenge_id,
                    user_id,
                    action_id,
                    session_hash,
                    _hash_secret(token),
                    action["risk_class"],
                    _iso(now),
                    _iso(expires),
                ),
            )
        return {
            "challenge_id": challenge_id,
            "approval_token": token,
            "action_id": action_id,
            "risk_class": action["risk_class"],
            "strong_reauthentication_required": action["risk_class"] == ActionRisk.STRONG_REAUTH_REQUIRED.value,
            "expires_at": _iso(expires),
            "one_time": True,
            "command_issued": False,
        }

    def approve(
        self,
        user_id: str,
        action_id: str,
        *,
        session_token: str,
        approval_token: str,
        password: str | None = None,
        now: datetime | None = None,
    ) -> dict:
        if not session_token or not approval_token:
            raise PermissionError("Aura Sec approval challenge and member session are required")
        action = self._validated_action(user_id, action_id)
        current = (now or _now()).astimezone(timezone.utc)
        session_hash = _hash_secret(session_token)
        token_hash = _hash_secret(approval_token.strip())

        with self._connect() as con:
            row = con.execute(
                """SELECT * FROM aura_sec_approval_challenges
                   WHERE user_id=? AND action_id=? AND token_hash=?""",
                (user_id, action_id, token_hash),
            ).fetchone()
        if not row:
            raise PermissionError("Aura Sec approval challenge was not found")
        challenge = dict(row)
        if challenge.get("status") != "pending" or challenge.get("consumed_at"):
            raise PermissionError("Aura Sec approval challenge has already been used or replaced")
        if not secrets.compare_digest(challenge["session_hash"], session_hash):
            raise PermissionError("Aura Sec approval challenge is bound to a different member session")
        expires = datetime.fromisoformat(challenge["expires_at"]).astimezone(timezone.utc)
        if current >= expires:
            with self._connect() as con:
                con.execute(
                    """UPDATE aura_sec_approval_challenges SET status='expired'
                       WHERE id=? AND status='pending'""",
                    (challenge["id"],),
                )
            raise PermissionError("Aura Sec approval challenge has expired")
        if challenge["risk_class"] != action["risk_class"]:
            raise PermissionError("Aura Sec approval risk classification changed; request a new challenge")

        strong_reauth = action["risk_class"] == ActionRisk.STRONG_REAUTH_REQUIRED.value
        if strong_reauth:
            if not password:
                raise PermissionError("Password re-authentication is required for this high-risk Aura Sec action")
            user = self.accounts.get_user(user_id)
            if not user:
                raise PermissionError("Aura Sec member account no longer exists")
            authenticated = self.accounts.authenticate(user["email"], password)
            if not authenticated or authenticated.get("id") != user_id:
                raise PermissionError("Aura Sec password re-authentication failed")

        # Consume first so one token cannot race two approval attempts. If the action was
        # concurrently changed after validation, the challenge is safely spent and a new
        # review is required rather than reusing stale authorisation.
        with self._connect() as con:
            updated = con.execute(
                """UPDATE aura_sec_approval_challenges
                   SET status='consumed',consumed_at=?
                   WHERE id=? AND status='pending' AND consumed_at IS NULL""",
                (_iso(current), challenge["id"]),
            )
            if updated.rowcount != 1:
                raise PermissionError("Aura Sec approval challenge was consumed concurrently")

        approved = self.security.approve_action(
            user_id,
            action_id,
            strong_reauth_verified=strong_reauth,
        )
        return {
            "approved": True,
            "action": approved,
            "strong_reauthentication_verified": strong_reauth,
            "command_issued": False,
            "truth": (
                "Member approval is recorded, but no endpoint command has been issued. "
                "A verified signed native-device session is still required to receive the bounded command."
            ),
        }


__all__ = ["AuraSecApprovalGateway"]
