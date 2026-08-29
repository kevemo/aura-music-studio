from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
from uuid import uuid4

from webauthn import (
    base64url_to_bytes,
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import bytes_to_base64url
from webauthn.helpers.exceptions import InvalidAuthenticationResponse, InvalidRegistrationResponse
from webauthn.helpers.structs import (
    AttestationConveyancePreference,
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from .accounts import AccountStore
from .aura_sec_protocol import ActionRisk
from .aura_sec_store import AuraSecStore


RP_NAME = "Elevate Souls Productions Content Creation Command Center"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).astimezone(timezone.utc).isoformat()


def _hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _enum_value(value) -> str:
    return str(getattr(value, "value", value))


class AuraSecPasskeyService:
    """WebAuthn credential and action-bound strong re-authentication service.

    Private keys and biometric material never reach this service. The server stores only
    WebAuthn verification material: credential ID, public key, sign counter and minimal
    authenticator metadata. A successful assertion creates short-lived evidence bound to
    the exact member session and Aura Sec action. Evidence is one-time and cannot issue a
    native endpoint command by itself.
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
                CREATE TABLE IF NOT EXISTS aura_sec_passkey_credentials (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    credential_id TEXT NOT NULL UNIQUE,
                    public_key TEXT NOT NULL,
                    sign_count INTEGER NOT NULL DEFAULT 0,
                    aaguid TEXT,
                    attestation_format TEXT,
                    device_type TEXT,
                    backed_up INTEGER NOT NULL DEFAULT 0,
                    transports_json TEXT NOT NULL DEFAULT '[]',
                    label TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    last_used_at TEXT,
                    revoked_at TEXT,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_aura_sec_passkeys_user
                ON aura_sec_passkey_credentials(user_id,revoked_at);

                CREATE TABLE IF NOT EXISTS aura_sec_passkey_ceremonies (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    session_hash TEXT NOT NULL,
                    purpose TEXT NOT NULL,
                    action_id TEXT,
                    challenge TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    consumed_at TEXT,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_aura_sec_passkey_ceremonies
                ON aura_sec_passkey_ceremonies(user_id,purpose,action_id,status,expires_at);

                CREATE TABLE IF NOT EXISTS aura_sec_strong_reauth_evidence (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    session_hash TEXT NOT NULL,
                    action_id TEXT NOT NULL,
                    credential_record_id TEXT NOT NULL,
                    method TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    verified_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    consumed_at TEXT,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY(credential_record_id) REFERENCES aura_sec_passkey_credentials(id)
                );
                CREATE INDEX IF NOT EXISTS idx_aura_sec_strong_reauth
                ON aura_sec_strong_reauth_evidence(user_id,action_id,status,expires_at);
                """
            )

    def _rp_config(self) -> tuple[str, str]:
        configured = (os.getenv("LSS_PUBLIC_BASE_URL") or "").strip().rstrip("/")
        if not configured:
            raise RuntimeError(
                "LSS_PUBLIC_BASE_URL must be configured before Aura Sec passkeys can be used"
            )
        try:
            parsed = urlsplit(configured)
            port = parsed.port
        except ValueError as exc:
            raise RuntimeError("Aura Sec passkey public origin is invalid") from exc
        hostname = (parsed.hostname or "").lower()
        scheme = (parsed.scheme or "").lower()
        if not hostname or scheme not in {"http", "https"}:
            raise RuntimeError("Aura Sec passkey public origin must be an http(s) origin")
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment or parsed.username:
            raise RuntimeError("Aura Sec passkey public base URL must contain only an origin")
        local = hostname in {"localhost", "127.0.0.1", "::1", "testserver"}
        if scheme != "https" and not local:
            raise RuntimeError("Aura Sec passkeys require HTTPS outside local development")
        if port is None:
            origin = f"{scheme}://{hostname}"
        else:
            origin = f"{scheme}://{hostname}:{port}"
        return hostname, origin

    def _require_session(self, user_id: str, session_token: str) -> dict:
        if not session_token or len(session_token) < 16:
            raise PermissionError("Valid Aura Sec member session required")
        user = self.accounts.resolve_session(session_token)
        if not user or user.get("id") != user_id:
            raise PermissionError("Aura Sec member session is invalid or belongs to another account")
        return user

    def _require_password(self, user_id: str, password: str) -> dict:
        if not password:
            raise PermissionError("Current account password is required before passkey enrolment")
        user = self.accounts.get_user(user_id)
        if not user:
            raise PermissionError("Aura Sec member account no longer exists")
        authenticated = self.accounts.authenticate(user["email"], password)
        if not authenticated or authenticated.get("id") != user_id:
            raise PermissionError("Aura Sec password re-authentication failed")
        return user

    def _active_credentials(self, user_id: str) -> list[dict]:
        with self._connect() as con:
            rows = con.execute(
                """SELECT * FROM aura_sec_passkey_credentials
                   WHERE user_id=? AND revoked_at IS NULL ORDER BY created_at ASC""",
                (user_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_credentials(self, user_id: str) -> list[dict]:
        output = []
        for item in self._active_credentials(user_id):
            output.append(
                {
                    "id": item["id"],
                    "label": item["label"],
                    "device_type": item.get("device_type"),
                    "backed_up": bool(item.get("backed_up")),
                    "created_at": item["created_at"],
                    "last_used_at": item.get("last_used_at"),
                }
            )
        return output

    def has_active_credential(self, user_id: str) -> bool:
        return bool(self._active_credentials(user_id))

    def _create_ceremony(
        self,
        user_id: str,
        session_token: str,
        *,
        purpose: str,
        challenge: bytes,
        action_id: str | None = None,
        ttl_minutes: int = 5,
    ) -> str:
        if purpose not in {"register", "action"}:
            raise ValueError("Unsupported Aura Sec passkey ceremony purpose")
        if not 1 <= int(ttl_minutes) <= 10:
            raise ValueError("Aura Sec passkey ceremony lifetime must be between 1 and 10 minutes")
        ceremony_id = uuid4().hex
        now = _now()
        expires = now + timedelta(minutes=int(ttl_minutes))
        session_hash = _hash_secret(session_token)
        with self._connect() as con:
            con.execute(
                """UPDATE aura_sec_passkey_ceremonies
                   SET status='superseded'
                   WHERE user_id=? AND session_hash=? AND purpose=?
                     AND COALESCE(action_id,'')=COALESCE(?, '') AND status='pending'""",
                (user_id, session_hash, purpose, action_id),
            )
            con.execute(
                """INSERT INTO aura_sec_passkey_ceremonies
                   (id,user_id,session_hash,purpose,action_id,challenge,status,created_at,expires_at)
                   VALUES (?,?,?,?,?,?,'pending',?,?)""",
                (
                    ceremony_id,
                    user_id,
                    session_hash,
                    purpose,
                    action_id,
                    bytes_to_base64url(challenge),
                    _iso(now),
                    _iso(expires),
                ),
            )
        return ceremony_id

    def _ceremony(
        self,
        user_id: str,
        ceremony_id: str,
        session_token: str,
        *,
        purpose: str,
        action_id: str | None = None,
        now: datetime | None = None,
    ) -> dict:
        self._require_session(user_id, session_token)
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM aura_sec_passkey_ceremonies WHERE id=? AND user_id=?",
                (ceremony_id, user_id),
            ).fetchone()
        if not row:
            raise PermissionError("Aura Sec passkey ceremony was not found")
        item = dict(row)
        if item["purpose"] != purpose or item.get("action_id") != action_id:
            raise PermissionError("Aura Sec passkey ceremony scope does not match this operation")
        if item["status"] != "pending" or item.get("consumed_at"):
            raise PermissionError("Aura Sec passkey ceremony has already been used or replaced")
        if not secrets.compare_digest(item["session_hash"], _hash_secret(session_token)):
            raise PermissionError("Aura Sec passkey ceremony belongs to a different login session")
        current = (now or _now()).astimezone(timezone.utc)
        expires = datetime.fromisoformat(item["expires_at"]).astimezone(timezone.utc)
        if current >= expires:
            with self._connect() as con:
                con.execute(
                    "UPDATE aura_sec_passkey_ceremonies SET status='expired' WHERE id=? AND status='pending'",
                    (ceremony_id,),
                )
            raise PermissionError("Aura Sec passkey ceremony has expired")
        return item

    def begin_registration(
        self,
        user_id: str,
        *,
        session_token: str,
        password: str,
        label: str = "Passkey",
    ) -> dict:
        user = self._require_session(user_id, session_token)
        self._require_password(user_id, password)
        rp_id, origin = self._rp_config()
        challenge = secrets.token_bytes(32)
        existing = self._active_credentials(user_id)
        options = generate_registration_options(
            rp_id=rp_id,
            rp_name=RP_NAME,
            user_id=hashlib.sha256(f"aura-sec-passkey:{user_id}".encode("utf-8")).digest(),
            user_name=str(user.get("email") or user_id),
            user_display_name=str(user.get("display_name") or "Aura Sec member"),
            challenge=challenge,
            timeout=60000,
            attestation=AttestationConveyancePreference.NONE,
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.PREFERRED,
                user_verification=UserVerificationRequirement.REQUIRED,
            ),
            exclude_credentials=[
                PublicKeyCredentialDescriptor(id=base64url_to_bytes(item["credential_id"]))
                for item in existing
            ],
        )
        ceremony_id = self._create_ceremony(
            user_id,
            session_token,
            purpose="register",
            challenge=challenge,
        )
        cleaned_label = (label or "Passkey").strip()[:80] or "Passkey"
        return {
            "ceremony_id": ceremony_id,
            "public_key": json.loads(options_to_json(options)),
            "label": cleaned_label,
            "expected_origin": origin,
            "private_key_received_by_server": False,
            "biometric_data_received_by_server": False,
        }

    def complete_registration(
        self,
        user_id: str,
        *,
        session_token: str,
        ceremony_id: str,
        credential_response: dict,
        label: str = "Passkey",
    ) -> dict:
        ceremony = self._ceremony(
            user_id,
            ceremony_id,
            session_token,
            purpose="register",
        )
        rp_id, origin = self._rp_config()
        challenge = base64url_to_bytes(ceremony["challenge"])
        try:
            verified = verify_registration_response(
                credential=credential_response,
                expected_challenge=challenge,
                expected_rp_id=rp_id,
                expected_origin=origin,
                require_user_presence=True,
                require_user_verification=True,
            )
        except InvalidRegistrationResponse as exc:
            with self._connect() as con:
                con.execute(
                    "UPDATE aura_sec_passkey_ceremonies SET status='failed',consumed_at=? WHERE id=? AND status='pending'",
                    (_iso(), ceremony_id),
                )
            raise PermissionError("Aura Sec passkey registration verification failed") from exc

        record_id = uuid4().hex
        credential_id = bytes_to_base64url(verified.credential_id)
        public_key = bytes_to_base64url(verified.credential_public_key)
        transports = credential_response.get("response", {}).get("transports", [])
        if not isinstance(transports, list):
            transports = []
        transports = [str(item)[:40] for item in transports[:12]]
        cleaned_label = (label or "Passkey").strip()[:80] or "Passkey"
        now = _now()
        try:
            with self._connect() as con:
                updated = con.execute(
                    """UPDATE aura_sec_passkey_ceremonies
                       SET status='consumed',consumed_at=?
                       WHERE id=? AND status='pending' AND consumed_at IS NULL""",
                    (_iso(now), ceremony_id),
                )
                if updated.rowcount != 1:
                    raise PermissionError("Aura Sec passkey ceremony was consumed concurrently")
                con.execute(
                    """INSERT INTO aura_sec_passkey_credentials
                       (id,user_id,credential_id,public_key,sign_count,aaguid,attestation_format,
                        device_type,backed_up,transports_json,label,created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        record_id,
                        user_id,
                        credential_id,
                        public_key,
                        int(verified.sign_count),
                        str(verified.aaguid),
                        _enum_value(verified.fmt),
                        _enum_value(verified.credential_device_type),
                        1 if verified.credential_backed_up else 0,
                        json.dumps(transports, separators=(",", ":")),
                        cleaned_label,
                        _iso(now),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("This passkey credential is already registered") from exc

        return {
            "registered": True,
            "credential": {
                "id": record_id,
                "label": cleaned_label,
                "device_type": _enum_value(verified.credential_device_type),
                "backed_up": bool(verified.credential_backed_up),
                "user_verified": bool(verified.user_verified),
                "created_at": _iso(now),
            },
            "private_key_received_by_server": False,
            "biometric_data_received_by_server": False,
        }

    def _require_high_risk_action(self, user_id: str, action_id: str) -> dict:
        if self.security.licence(user_id).get("status") != "active":
            raise PermissionError("Active Aura Sec licence required")
        action = self.security.get_action(user_id, action_id)
        if action.get("status") != "proposed":
            raise ValueError("Only proposed Aura Sec actions can request passkey verification")
        if action.get("risk_class") != ActionRisk.STRONG_REAUTH_REQUIRED.value:
            raise PermissionError("Passkey step-up is reserved for strong re-authentication actions")
        device = self.security.get_device(user_id, action["device_id"])
        if device.get("status") == "revoked" or device.get("revoked_at"):
            raise PermissionError("Revoked Aura Sec device actions cannot be authorized")
        return action

    def begin_action_verification(
        self,
        user_id: str,
        action_id: str,
        *,
        session_token: str,
    ) -> dict:
        self._require_session(user_id, session_token)
        self._require_high_risk_action(user_id, action_id)
        credentials = self._active_credentials(user_id)
        if not credentials:
            raise PermissionError("A registered passkey is required for this high-risk Aura Sec action")
        rp_id, _origin = self._rp_config()
        challenge = secrets.token_bytes(32)
        options = generate_authentication_options(
            rp_id=rp_id,
            challenge=challenge,
            timeout=60000,
            allow_credentials=[
                PublicKeyCredentialDescriptor(id=base64url_to_bytes(item["credential_id"]))
                for item in credentials
            ],
            user_verification=UserVerificationRequirement.REQUIRED,
        )
        ceremony_id = self._create_ceremony(
            user_id,
            session_token,
            purpose="action",
            action_id=action_id,
            challenge=challenge,
        )
        return {
            "ceremony_id": ceremony_id,
            "action_id": action_id,
            "public_key": json.loads(options_to_json(options)),
            "user_verification_required": True,
            "command_issued": False,
        }

    def complete_action_verification(
        self,
        user_id: str,
        action_id: str,
        *,
        session_token: str,
        ceremony_id: str,
        credential_response: dict,
        evidence_ttl_seconds: int = 120,
    ) -> dict:
        if not 30 <= int(evidence_ttl_seconds) <= 300:
            raise ValueError("Aura Sec passkey evidence lifetime must be between 30 and 300 seconds")
        self._require_high_risk_action(user_id, action_id)
        ceremony = self._ceremony(
            user_id,
            ceremony_id,
            session_token,
            purpose="action",
            action_id=action_id,
        )
        credential_id = str(credential_response.get("id") or "").strip()
        if not credential_id:
            raise PermissionError("Aura Sec passkey assertion did not include a credential ID")
        with self._connect() as con:
            row = con.execute(
                """SELECT * FROM aura_sec_passkey_credentials
                   WHERE user_id=? AND credential_id=? AND revoked_at IS NULL""",
                (user_id, credential_id),
            ).fetchone()
        if not row:
            raise PermissionError("Aura Sec passkey credential is not registered to this member")
        credential = dict(row)
        rp_id, origin = self._rp_config()
        try:
            verified = verify_authentication_response(
                credential=credential_response,
                expected_challenge=base64url_to_bytes(ceremony["challenge"]),
                expected_rp_id=rp_id,
                expected_origin=origin,
                credential_public_key=base64url_to_bytes(credential["public_key"]),
                credential_current_sign_count=int(credential["sign_count"]),
                require_user_verification=True,
            )
        except InvalidAuthenticationResponse as exc:
            with self._connect() as con:
                con.execute(
                    "UPDATE aura_sec_passkey_ceremonies SET status='failed',consumed_at=? WHERE id=? AND status='pending'",
                    (_iso(), ceremony_id),
                )
            raise PermissionError("Aura Sec passkey assertion verification failed") from exc
        if not secrets.compare_digest(
            bytes_to_base64url(verified.credential_id), credential["credential_id"]
        ):
            raise PermissionError("Aura Sec passkey assertion credential changed during verification")
        if not verified.user_verified:
            raise PermissionError("Aura Sec high-risk approval requires authenticator user verification")

        now = _now()
        evidence_id = uuid4().hex
        expires = now + timedelta(seconds=int(evidence_ttl_seconds))
        with self._connect() as con:
            updated = con.execute(
                """UPDATE aura_sec_passkey_ceremonies
                   SET status='consumed',consumed_at=?
                   WHERE id=? AND status='pending' AND consumed_at IS NULL""",
                (_iso(now), ceremony_id),
            )
            if updated.rowcount != 1:
                raise PermissionError("Aura Sec passkey ceremony was consumed concurrently")
            con.execute(
                """UPDATE aura_sec_passkey_credentials
                   SET sign_count=?,device_type=?,backed_up=?,last_used_at=? WHERE id=?""",
                (
                    int(verified.new_sign_count),
                    _enum_value(verified.credential_device_type),
                    1 if verified.credential_backed_up else 0,
                    _iso(now),
                    credential["id"],
                ),
            )
            con.execute(
                """INSERT INTO aura_sec_strong_reauth_evidence
                   (id,user_id,session_hash,action_id,credential_record_id,method,status,
                    verified_at,expires_at)
                   VALUES (?,?,?,?,?,'webauthn','pending',?,?)""",
                (
                    evidence_id,
                    user_id,
                    _hash_secret(session_token),
                    action_id,
                    credential["id"],
                    _iso(now),
                    _iso(expires),
                ),
            )
        return {
            "verified": True,
            "evidence_id": evidence_id,
            "action_id": action_id,
            "method": "webauthn",
            "user_verified": True,
            "expires_at": _iso(expires),
            "one_time": True,
            "command_issued": False,
        }

    def consume_action_evidence(
        self,
        user_id: str,
        action_id: str,
        *,
        session_token: str,
        evidence_id: str,
        now: datetime | None = None,
    ) -> dict:
        self._require_session(user_id, session_token)
        current = (now or _now()).astimezone(timezone.utc)
        with self._connect() as con:
            row = con.execute(
                """SELECT * FROM aura_sec_strong_reauth_evidence
                   WHERE id=? AND user_id=? AND action_id=?""",
                (evidence_id, user_id, action_id),
            ).fetchone()
        if not row:
            raise PermissionError("Aura Sec strong re-authentication evidence was not found")
        item = dict(row)
        if item["method"] != "webauthn" or item["status"] != "pending" or item.get("consumed_at"):
            raise PermissionError("Aura Sec strong re-authentication evidence has already been used")
        if not secrets.compare_digest(item["session_hash"], _hash_secret(session_token)):
            raise PermissionError("Aura Sec strong re-authentication evidence belongs to another session")
        expires = datetime.fromisoformat(item["expires_at"]).astimezone(timezone.utc)
        if current >= expires:
            with self._connect() as con:
                con.execute(
                    "UPDATE aura_sec_strong_reauth_evidence SET status='expired' WHERE id=? AND status='pending'",
                    (evidence_id,),
                )
            raise PermissionError("Aura Sec strong re-authentication evidence has expired")
        with self._connect() as con:
            updated = con.execute(
                """UPDATE aura_sec_strong_reauth_evidence
                   SET status='consumed',consumed_at=?
                   WHERE id=? AND status='pending' AND consumed_at IS NULL""",
                (_iso(current), evidence_id),
            )
            if updated.rowcount != 1:
                raise PermissionError("Aura Sec strong re-authentication evidence was consumed concurrently")
        return {
            "verified": True,
            "method": "webauthn",
            "action_id": action_id,
            "credential_record_id": item["credential_record_id"],
            "consumed": True,
        }


__all__ = ["AuraSecPasskeyService", "RP_NAME"]
