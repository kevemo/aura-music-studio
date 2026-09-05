from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from datetime import datetime, timezone

from pydantic import ValidationError

from .accounts import AccountStore
from .aura_sec_command_signing import SignedSecurityCommand
from .aura_sec_protocol import ActionRisk


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).astimezone(timezone.utc).isoformat()


def _hash_nonce(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class AuraSecCommandDeliveryStore:
    """Durable at-least-once delivery state for server-signed Aura Sec commands.

    A signed envelope is persisted before the bridge returns it to a native agent. This
    makes a post-sign transport failure recoverable without creating a different command,
    nonce or signature. Delivery retries are therefore exact command-id retransmissions,
    not re-execution requests. Native executors must remain idempotent on command_id.

    The store deliberately has no generic command creation or shell capability. It can only
    bind a previously-persisted bounded Aura Sec command to its exact signed envelope.
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
                CREATE TABLE IF NOT EXISTS aura_sec_command_deliveries (
                    command_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    signed_envelope_json TEXT NOT NULL,
                    signed_payload_digest TEXT NOT NULL,
                    signer_key_id TEXT NOT NULL,
                    public_key_fingerprint TEXT NOT NULL,
                    first_delivery_at TEXT NOT NULL,
                    last_delivery_at TEXT NOT NULL,
                    delivery_count INTEGER NOT NULL DEFAULT 1,
                    FOREIGN KEY(command_id) REFERENCES aura_sec_commands(id) ON DELETE CASCADE,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY(device_id) REFERENCES aura_sec_devices(id) ON DELETE CASCADE,
                    CHECK(delivery_count >= 1)
                );
                CREATE INDEX IF NOT EXISTS idx_aura_sec_command_delivery_device
                ON aura_sec_command_deliveries(user_id,device_id,last_delivery_at);
                """
            )

    @staticmethod
    def _canonical_parameters(value: dict) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    @staticmethod
    def _validated_signed_envelope(raw: str) -> SignedSecurityCommand:
        try:
            payload = json.loads(raw)
            signed = SignedSecurityCommand.model_validate(payload)
        except (json.JSONDecodeError, ValidationError, TypeError, ValueError) as exc:
            raise PermissionError("Persisted Aura Sec signed command envelope is invalid") from exc
        expected_digest = hashlib.sha256(signed.canonical_signed_payload()).hexdigest()
        if not secrets.compare_digest(expected_digest, signed.payload_digest):
            raise PermissionError("Persisted Aura Sec signed command envelope digest is invalid")
        return signed

    def _command_row(self, user_id: str, command_id: str) -> sqlite3.Row:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM aura_sec_commands WHERE user_id=? AND id=?",
                (user_id, command_id),
            ).fetchone()
        if not row:
            raise ValueError("Aura Sec command not found")
        return row

    def _assert_matches_command_row(self, row: sqlite3.Row, signed: SignedSecurityCommand) -> None:
        if not secrets.compare_digest(str(row["id"]), signed.command_id):
            raise PermissionError("Signed Aura Sec command id does not match persisted command")
        if not secrets.compare_digest(str(row["device_id"]), signed.device_id):
            raise PermissionError("Signed Aura Sec command device does not match persisted command")
        if str(row["action_type"]) != signed.action.value:
            raise PermissionError("Signed Aura Sec command action does not match persisted command")
        if str(row["risk_class"]) != signed.risk.value:
            raise PermissionError("Signed Aura Sec command risk does not match persisted command")
        if not secrets.compare_digest(str(row["policy_version"]), signed.policy_version):
            raise PermissionError("Signed Aura Sec command policy does not match persisted command")
        if not secrets.compare_digest(str(row["nonce_hash"]), _hash_nonce(signed.nonce)):
            raise PermissionError("Signed Aura Sec command nonce does not match persisted command")

        try:
            parameters = json.loads(str(row["parameters_json"] or "{}"))
        except json.JSONDecodeError as exc:
            raise PermissionError("Persisted Aura Sec command parameters are invalid") from exc
        if self._canonical_parameters(parameters) != self._canonical_parameters(signed.parameters):
            raise PermissionError("Signed Aura Sec command parameters do not match persisted command")

        issued_at = datetime.fromisoformat(str(row["issued_at"])).astimezone(timezone.utc)
        expires_at = datetime.fromisoformat(str(row["expires_at"])).astimezone(timezone.utc)
        if issued_at != signed.issued_at.astimezone(timezone.utc):
            raise PermissionError("Signed Aura Sec command issuance time does not match persisted command")
        if expires_at != signed.expires_at.astimezone(timezone.utc):
            raise PermissionError("Signed Aura Sec command expiry does not match persisted command")

        risk = ActionRisk(str(row["risk_class"]))
        expected_approval = str(row["action_id"]) if risk in {
            ActionRisk.CONFIRMATION_REQUIRED,
            ActionRisk.STRONG_REAUTH_REQUIRED,
        } else None
        if signed.approval_id != expected_approval:
            raise PermissionError("Signed Aura Sec command approval binding does not match persisted action")

        expected_digest = hashlib.sha256(signed.canonical_signed_payload()).hexdigest()
        if not secrets.compare_digest(expected_digest, signed.payload_digest):
            raise PermissionError("Signed Aura Sec command payload digest is invalid")

    def persist_first_delivery(
        self,
        user_id: str,
        signed_command: SignedSecurityCommand,
        *,
        delivered_at: datetime | None = None,
    ) -> SignedSecurityCommand:
        """Durably bind a signed envelope before any transport response is emitted."""
        if not isinstance(signed_command, SignedSecurityCommand):
            signed_command = SignedSecurityCommand.model_validate(signed_command)
        row = self._command_row(user_id, signed_command.command_id)
        if str(row["status"]) != "issued":
            raise PermissionError("Only an issued Aura Sec command can enter native delivery")
        self._assert_matches_command_row(row, signed_command)

        when = (delivered_at or _now()).astimezone(timezone.utc)
        if when >= signed_command.expires_at.astimezone(timezone.utc):
            raise PermissionError("Expired Aura Sec command cannot enter native delivery")
        envelope = json.dumps(
            signed_command.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        with self._connect() as con:
            try:
                con.execute(
                    """INSERT INTO aura_sec_command_deliveries
                       (command_id,user_id,device_id,signed_envelope_json,signed_payload_digest,
                        signer_key_id,public_key_fingerprint,first_delivery_at,last_delivery_at,delivery_count)
                       VALUES (?,?,?,?,?,?,?,?,?,1)""",
                    (
                        signed_command.command_id,
                        user_id,
                        signed_command.device_id,
                        envelope,
                        signed_command.payload_digest,
                        signed_command.signer_key_id,
                        signed_command.public_key_fingerprint,
                        _iso(when),
                        _iso(when),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise PermissionError("Aura Sec signed command delivery state already exists") from exc
        return signed_command

    def next_pending_for_device(
        self,
        user_id: str,
        device_id: str,
        *,
        now: datetime | None = None,
    ) -> SignedSecurityCommand | None:
        """Return and atomically account for the oldest still-valid signed redelivery."""
        current = (now or _now()).astimezone(timezone.utc)
        with self._connect() as con:
            row = con.execute(
                """SELECT c.*,d.signed_envelope_json,d.signed_payload_digest,
                          d.signer_key_id,d.public_key_fingerprint
                   FROM aura_sec_commands c
                   JOIN aura_sec_command_deliveries d ON d.command_id=c.id
                   WHERE c.user_id=? AND c.device_id=? AND c.status='issued' AND c.expires_at>?
                   ORDER BY c.issued_at ASC
                   LIMIT 1""",
                (user_id, device_id, _iso(current)),
            ).fetchone()
            if not row:
                return None

            signed = self._validated_signed_envelope(str(row["signed_envelope_json"]))
            self._assert_matches_command_row(row, signed)
            if not secrets.compare_digest(str(row["signed_payload_digest"]), signed.payload_digest):
                raise PermissionError("Aura Sec durable delivery digest does not match signed envelope")
            if not secrets.compare_digest(str(row["signer_key_id"]), signed.signer_key_id):
                raise PermissionError("Aura Sec durable delivery signer identity was altered")
            if not secrets.compare_digest(
                str(row["public_key_fingerprint"]), signed.public_key_fingerprint
            ):
                raise PermissionError("Aura Sec durable delivery signer fingerprint was altered")

            updated = con.execute(
                """UPDATE aura_sec_command_deliveries
                   SET last_delivery_at=?,delivery_count=delivery_count+1
                   WHERE command_id=? AND user_id=?""",
                (_iso(current), signed.command_id, user_id),
            )
            if updated.rowcount != 1:
                raise PermissionError("Aura Sec signed command redelivery state changed concurrently")
        return signed

    def abandon_never_delivered(self, user_id: str, command_id: str) -> bool:
        """Delete only an issued command that never obtained a durable signed envelope."""
        with self._connect() as con:
            deleted = con.execute(
                """DELETE FROM aura_sec_commands
                   WHERE user_id=? AND id=? AND status='issued'
                     AND NOT EXISTS (
                         SELECT 1 FROM aura_sec_command_deliveries d
                         WHERE d.command_id=aura_sec_commands.id
                     )""",
                (user_id, command_id),
            )
        return deleted.rowcount == 1

    def delivery_state(self, user_id: str, command_id: str) -> dict:
        with self._connect() as con:
            row = con.execute(
                """SELECT command_id,user_id,device_id,signed_payload_digest,signer_key_id,
                          public_key_fingerprint,first_delivery_at,last_delivery_at,delivery_count
                   FROM aura_sec_command_deliveries WHERE user_id=? AND command_id=?""",
                (user_id, command_id),
            ).fetchone()
        if not row:
            raise ValueError("Aura Sec command delivery state not found")
        return dict(row)


__all__ = ["AuraSecCommandDeliveryStore"]
