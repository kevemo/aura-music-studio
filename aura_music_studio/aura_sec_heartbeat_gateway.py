from __future__ import annotations

import hashlib
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from .accounts import AccountStore
from .aura_sec_protocol import DeviceHeartbeat
from .aura_sec_store import AuraSecStore, HeartbeatSignatureVerifier


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).isoformat()


def _hash_challenge(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class AuraSecHeartbeatGateway:
    """Native-only freshness gateway for signed Aura Sec heartbeats.

    This class deliberately has no FastAPI router. A member/browser session cannot mint
    native heartbeat proof. Production native transport must authenticate the enrolled
    device first, then use this gateway to issue and consume a one-time challenge.

    The challenge is defence in depth on top of the signed heartbeat's monotonic device
    sequence. Only a SHA-256 challenge hash is persisted; the nonce is returned once.
    """

    def __init__(
        self,
        accounts: AccountStore | None = None,
        security: AuraSecStore | None = None,
    ) -> None:
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
                CREATE TABLE IF NOT EXISTS aura_sec_heartbeat_challenges (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    challenge_hash TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL DEFAULT 'pending',
                    attempt_id TEXT,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    consumed_at TEXT,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY(device_id) REFERENCES aura_sec_devices(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_aura_sec_heartbeat_challenge_device
                ON aura_sec_heartbeat_challenges(user_id, device_id, status, expires_at);
                """
            )

    def issue_challenge(
        self,
        user_id: str,
        device_id: str,
        *,
        ttl_seconds: int = 120,
    ) -> dict:
        """Issue a native-device heartbeat nonce after transport/device authentication.

        The caller is responsible for authenticating the native transport before calling
        this method. It must never be exposed as a normal member-browser endpoint.
        """
        if self.security.licence(user_id).get("status") != "active":
            raise PermissionError("Active Aura Sec licence required for heartbeat challenge")
        device = self.security.get_device(user_id, device_id)
        if device.get("status") == "revoked" or device.get("revoked_at"):
            raise PermissionError("Revoked Aura Sec device cannot receive heartbeat challenge")
        if not 30 <= int(ttl_seconds) <= 300:
            raise ValueError("Heartbeat challenge lifetime must be between 30 and 300 seconds")

        now = _now()
        expires = now + timedelta(seconds=int(ttl_seconds))
        challenge = secrets.token_urlsafe(32)
        challenge_hash = _hash_challenge(challenge)
        challenge_id = uuid4().hex

        with self._connect() as con:
            # Only the newest server challenge is valid for a device. A previous challenge
            # that was interrupted while verifying also fails closed and is superseded.
            con.execute(
                """UPDATE aura_sec_heartbeat_challenges
                   SET status='superseded',attempt_id=NULL
                   WHERE user_id=? AND device_id=? AND status IN ('pending','verifying')""",
                (user_id, device_id),
            )
            con.execute(
                """INSERT INTO aura_sec_heartbeat_challenges
                   (id,user_id,device_id,challenge_hash,status,created_at,expires_at)
                   VALUES (?,?,?,?,'pending',?,?)""",
                (
                    challenge_id,
                    user_id,
                    device_id,
                    challenge_hash,
                    _iso(now),
                    _iso(expires),
                ),
            )

        return {
            "challenge_id": challenge_id,
            "challenge_nonce": challenge,
            "expires_at": _iso(expires),
            "one_time": True,
            "member_browser_route_exposed": False,
        }

    def _reserve_challenge(
        self,
        user_id: str,
        heartbeat: DeviceHeartbeat,
        *,
        now: datetime,
    ) -> tuple[str, str]:
        challenge_hash = _hash_challenge(heartbeat.challenge_nonce)
        attempt_id = uuid4().hex
        expired = False
        with self._connect() as con:
            row = con.execute(
                """SELECT id,status,expires_at FROM aura_sec_heartbeat_challenges
                   WHERE user_id=? AND device_id=? AND challenge_hash=?""",
                (user_id, heartbeat.device_id, challenge_hash),
            ).fetchone()
            if not row:
                raise PermissionError("Aura Sec heartbeat challenge was not issued by this service")
            if row["status"] != "pending":
                raise PermissionError("Aura Sec heartbeat challenge is no longer pending")
            expires = datetime.fromisoformat(row["expires_at"]).astimezone(timezone.utc)
            if now >= expires:
                con.execute(
                    """UPDATE aura_sec_heartbeat_challenges SET status='expired',attempt_id=NULL
                       WHERE id=? AND status='pending'""",
                    (row["id"],),
                )
                expired = True
            else:
                cursor = con.execute(
                    """UPDATE aura_sec_heartbeat_challenges SET status='verifying',attempt_id=?
                       WHERE id=? AND status='pending'""",
                    (attempt_id, row["id"]),
                )
                if cursor.rowcount != 1:
                    raise PermissionError("Aura Sec heartbeat challenge was concurrently claimed")
                challenge_id = str(row["id"])
        if expired:
            raise PermissionError("Aura Sec heartbeat challenge has expired")
        return challenge_id, attempt_id

    def _release_failed_attempt(self, challenge_id: str, attempt_id: str) -> None:
        """Make a still-valid challenge reusable after a failed cryptographic attempt."""
        current = _now()
        with self._connect() as con:
            row = con.execute(
                """SELECT expires_at FROM aura_sec_heartbeat_challenges
                   WHERE id=? AND status='verifying' AND attempt_id=?""",
                (challenge_id, attempt_id),
            ).fetchone()
            if not row:
                return
            expires = datetime.fromisoformat(row["expires_at"]).astimezone(timezone.utc)
            if current >= expires:
                con.execute(
                    """UPDATE aura_sec_heartbeat_challenges
                       SET status='expired',attempt_id=NULL
                       WHERE id=? AND status='verifying' AND attempt_id=?""",
                    (challenge_id, attempt_id),
                )
            else:
                con.execute(
                    """UPDATE aura_sec_heartbeat_challenges
                       SET status='pending',attempt_id=NULL
                       WHERE id=? AND status='verifying' AND attempt_id=?""",
                    (challenge_id, attempt_id),
                )

    def verify_and_record(
        self,
        user_id: str,
        heartbeat: DeviceHeartbeat,
        *,
        signature_b64: str,
        signature_verifier: HeartbeatSignatureVerifier | None,
    ) -> dict:
        """Reserve a one-time challenge, verify the signed heartbeat and consume it."""
        challenge_id, attempt_id = self._reserve_challenge(
            user_id,
            heartbeat,
            now=_now(),
        )
        try:
            device = self.security.record_verified_heartbeat(
                user_id,
                heartbeat,
                signature_b64=signature_b64,
                signature_verifier=signature_verifier,
            )
        except Exception:
            # A bad signature, stale sequence or other failed verification must not burn a
            # legitimate one-time challenge. Concurrent callers cannot use it while the
            # state is 'verifying'.
            self._release_failed_attempt(challenge_id, attempt_id)
            raise

        with self._connect() as con:
            cursor = con.execute(
                """UPDATE aura_sec_heartbeat_challenges
                   SET status='consumed',attempt_id=NULL,consumed_at=?
                   WHERE id=? AND status='verifying' AND attempt_id=?""",
                (_iso(), challenge_id, attempt_id),
            )
            if cursor.rowcount != 1:
                # The signed device state has already been recorded, so do not reopen the
                # challenge on bookkeeping failure. Leaving it non-pending fails closed.
                raise RuntimeError("Aura Sec heartbeat challenge consumption failed closed")

        return {
            "device": device,
            "challenge_consumed": True,
            "member_browser_route_exposed": False,
        }


__all__ = ["AuraSecHeartbeatGateway"]
