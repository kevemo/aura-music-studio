from __future__ import annotations

import hashlib
import secrets
import sqlite3
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .accounts import AccountStore
from .aura_sec_action_parameters import validated_command_parameters
from .aura_sec_command_store import AuraSecCommandStore
from .aura_sec_store import AuraSecStore


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).isoformat()


def _hash_nonce(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class NativeCommandPoll(BaseModel):
    """Short-lived signed request from an enrolled Aura Sec native client."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1] = 1
    device_id: str = Field(min_length=16, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    sequence: int = Field(ge=1, le=9_223_372_036_854_775_807)
    issued_at: datetime
    expires_at: datetime
    agent_version: str = Field(min_length=1, max_length=80)
    policy_version: str = Field(min_length=1, max_length=80)
    session_nonce: str = Field(min_length=16, max_length=256, pattern=r"^[A-Za-z0-9._~-]+$")

    @field_validator("issued_at", "expires_at")
    @classmethod
    def timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("timezone-aware timestamp required")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def validity_window(self):
        if self.expires_at <= self.issued_at:
            raise ValueError("native poll expiry must be after issuance")
        if (self.expires_at - self.issued_at).total_seconds() > 120:
            raise ValueError("native poll validity window cannot exceed two minutes")
        return self


class AuraSecNativeBridge:
    """Trusted gateway between verified native sessions and the bounded command store.

    This class is not mounted as a member/browser API. A transport/authentication layer
    must verify the enrolled device signature before setting `signature_verified=True`.
    Signed request sequence numbers are persisted so a captured old poll cannot be replayed
    to obtain a second command. The bridge issues at most one previously-approved action per
    verified poll and runs its parameters through the native parameter firewall first.
    """

    def __init__(
        self,
        accounts: AccountStore | None = None,
        security: AuraSecStore | None = None,
        commands: AuraSecCommandStore | None = None,
    ):
        self.accounts = accounts or AccountStore()
        self.security = security or AuraSecStore(self.accounts)
        self.commands = commands or AuraSecCommandStore(self.accounts, self.security)
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
                CREATE TABLE IF NOT EXISTS aura_sec_native_poll_state (
                    device_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    last_sequence INTEGER NOT NULL,
                    last_nonce_hash TEXT NOT NULL,
                    last_verified_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY(device_id) REFERENCES aura_sec_devices(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_aura_sec_native_poll_user
                ON aura_sec_native_poll_state(user_id,last_verified_at);
                """
            )

    def _accept_verified_poll_sequence(
        self,
        user_id: str,
        poll: NativeCommandPoll,
        *,
        signature_verified: bool,
        now: datetime | None = None,
    ) -> None:
        if not signature_verified:
            raise PermissionError("Unverified Aura Sec native poll rejected")
        current = (now or _now()).astimezone(timezone.utc)
        if current < poll.issued_at or current >= poll.expires_at:
            raise PermissionError("Aura Sec native poll is outside its validity window")
        if self.security.licence(user_id).get("status") != "active":
            raise PermissionError("Active Aura Sec licence required")
        device = self.security.get_device(user_id, poll.device_id)
        if device.get("status") == "revoked" or device.get("revoked_at"):
            raise PermissionError("Revoked Aura Sec device cannot poll for commands")

        nonce_hash = _hash_nonce(poll.session_nonce)
        with self._connect() as con:
            row = con.execute(
                "SELECT last_sequence,last_nonce_hash FROM aura_sec_native_poll_state WHERE user_id=? AND device_id=?",
                (user_id, poll.device_id),
            ).fetchone()
            if row:
                if poll.sequence <= int(row["last_sequence"]):
                    raise PermissionError("Aura Sec native poll sequence was replayed or moved backwards")
                if secrets.compare_digest(nonce_hash, row["last_nonce_hash"]):
                    raise PermissionError("Aura Sec native poll nonce was replayed")
                updated = con.execute(
                    """UPDATE aura_sec_native_poll_state
                       SET last_sequence=?,last_nonce_hash=?,last_verified_at=?
                       WHERE user_id=? AND device_id=? AND last_sequence<?""",
                    (poll.sequence, nonce_hash, _iso(current), user_id, poll.device_id, poll.sequence),
                )
                if updated.rowcount != 1:
                    raise PermissionError("Aura Sec native poll lost a sequence race")
            else:
                try:
                    con.execute(
                        """INSERT INTO aura_sec_native_poll_state
                           (device_id,user_id,last_sequence,last_nonce_hash,last_verified_at)
                           VALUES (?,?,?,?,?)""",
                        (poll.device_id, user_id, poll.sequence, nonce_hash, _iso(current)),
                    )
                except sqlite3.IntegrityError as exc:
                    raise PermissionError("Aura Sec native poll state changed concurrently") from exc

    def _next_approved_action_id(self, user_id: str, device_id: str) -> str | None:
        with self._connect() as con:
            row = con.execute(
                """SELECT a.id
                   FROM aura_sec_actions a
                   LEFT JOIN aura_sec_commands c ON c.action_id=a.id
                   WHERE a.user_id=? AND a.device_id=? AND a.status='approved' AND c.id IS NULL
                   ORDER BY COALESCE(a.approved_at,a.requested_at) ASC
                   LIMIT 1""",
                (user_id, device_id),
            ).fetchone()
        return str(row["id"]) if row else None

    def poll_verified_command(
        self,
        user_id: str,
        poll: NativeCommandPoll,
        *,
        signature_verified: bool,
        now: datetime | None = None,
    ) -> dict:
        """Return at most one freshly-issued bounded command to a verified native client."""
        self._accept_verified_poll_sequence(
            user_id,
            poll,
            signature_verified=signature_verified,
            now=now,
        )
        action_id = self._next_approved_action_id(user_id, poll.device_id)
        if not action_id:
            return {
                "command": None,
                "poll_sequence": poll.sequence,
                "member_browser_route_exposed": False,
                "truth": "No previously-approved bounded action is waiting for this verified device session.",
            }

        action = self.security.get_action(user_id, action_id)
        parameters = validated_command_parameters(action["action_type"], action.get("details") or {})
        command = self.commands.issue_approved_action(
            user_id,
            action_id,
            policy_version=poll.policy_version,
            nonce=secrets.token_urlsafe(32),
            parameters=parameters,
            ttl_seconds=300,
        )
        return {
            "command": command.model_dump(mode="json"),
            "poll_sequence": poll.sequence,
            "member_browser_route_exposed": False,
            "truth": (
                "The command was issued only after verified device-session proof, replay protection, prior action approval "
                "and strict per-action parameter validation."
            ),
        }


__all__ = ["AuraSecNativeBridge", "NativeCommandPoll"]
