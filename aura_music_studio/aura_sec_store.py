from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from .accounts import AccountStore


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).isoformat()


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


@dataclass(frozen=True)
class SecurityLicence:
    user_id: str
    status: str
    sku_id: str | None
    device_limit: int
    period_start: str | None
    period_end: str | None


class AuraSecStore:
    """Persistent control-plane state for the separate Aura Sec product.

    This store intentionally does not infer a security entitlement from a Creative
    Free/Basic/Pro plan. Purchase verification, native device attestation and signed
    heartbeat verification happen in trusted services before their verified result is
    written here.
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
                CREATE TABLE IF NOT EXISTS aura_sec_licences (
                    user_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    sku_id TEXT,
                    device_limit INTEGER NOT NULL DEFAULT 0,
                    period_start TEXT,
                    period_end TEXT,
                    last_payment_reference TEXT,
                    verified_by TEXT,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS aura_sec_payments (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    sku_id TEXT NOT NULL,
                    payment_reference TEXT NOT NULL UNIQUE,
                    verified_by TEXT NOT NULL,
                    verified_at TEXT NOT NULL,
                    period_start TEXT NOT NULL,
                    period_end TEXT NOT NULL,
                    device_limit INTEGER NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS aura_sec_devices (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    architecture TEXT NOT NULL,
                    public_key_fingerprint TEXT NOT NULL,
                    agent_version TEXT,
                    status TEXT NOT NULL DEFAULT 'enrolled',
                    protection_state TEXT NOT NULL DEFAULT 'awaiting_heartbeat',
                    enrolled_at TEXT NOT NULL,
                    last_seen_at TEXT,
                    last_policy_version TEXT,
                    last_report_digest TEXT,
                    revoked_at TEXT,
                    UNIQUE(user_id, public_key_fingerprint),
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS aura_sec_incidents (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'open',
                    title TEXT NOT NULL,
                    detection_id TEXT,
                    confidence REAL,
                    summary_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY(device_id) REFERENCES aura_sec_devices(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS aura_sec_actions (
                    id TEXT PRIMARY KEY,
                    incident_id TEXT,
                    user_id TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    risk_class TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'proposed',
                    details_json TEXT,
                    requested_at TEXT NOT NULL,
                    approved_at TEXT,
                    executed_at TEXT,
                    verified_at TEXT,
                    FOREIGN KEY(incident_id) REFERENCES aura_sec_incidents(id) ON DELETE SET NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY(device_id) REFERENCES aura_sec_devices(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_aura_sec_devices_user_status
                ON aura_sec_devices(user_id, status);

                CREATE INDEX IF NOT EXISTS idx_aura_sec_incidents_user_status
                ON aura_sec_incidents(user_id, status, created_at);

                CREATE INDEX IF NOT EXISTS idx_aura_sec_actions_device_status
                ON aura_sec_actions(device_id, status, requested_at);
                """
            )

    def licence(self, user_id: str) -> dict:
        with self._connect() as con:
            row = con.execute("SELECT * FROM aura_sec_licences WHERE user_id=?", (user_id,)).fetchone()
        if not row:
            return {
                "user_id": user_id,
                "status": "not_purchased",
                "sku_id": None,
                "device_limit": 0,
                "period_start": None,
                "period_end": None,
            }
        item = dict(row)
        end = _parse(item.get("period_end"))
        if item.get("status") == "active" and end and end <= _now():
            with self._connect() as con:
                con.execute(
                    "UPDATE aura_sec_licences SET status='expired',updated_at=? WHERE user_id=?",
                    (_iso(), user_id),
                )
            item["status"] = "expired"
        item.pop("last_payment_reference", None)
        item.pop("verified_by", None)
        item.pop("updated_at", None)
        return item

    def has_active_licence(self, user_id: str) -> bool:
        return self.licence(user_id).get("status") == "active"

    def activate_verified_purchase(
        self,
        user_id: str,
        *,
        sku_id: str,
        payment_reference: str,
        device_limit: int,
        period_days: int,
        verified_by: str,
    ) -> dict:
        """Record a purchase only after a trusted billing/owner service verified it.

        This function is intentionally not exposed as a member web route. The caller must
        validate the configured SKU, amount/currency and payment-provider evidence before
        calling it. Aura Sec has no commercial SKU configured in the foundation release.
        """
        if not self.accounts.get_user(user_id):
            raise ValueError("User not found")
        sku = (sku_id or "").strip()
        reference = (payment_reference or "").strip()
        verifier = (verified_by or "").strip()
        if not sku or len(sku) > 100:
            raise ValueError("A configured Aura Sec SKU id is required")
        if len(reference) < 3 or len(reference) > 250:
            raise ValueError("A verified payment reference is required")
        if not verifier or len(verifier) > 160:
            raise ValueError("A trusted verifier identity is required")
        if not 1 <= int(device_limit) <= 100:
            raise ValueError("Device limit must be between 1 and 100")
        if not 1 <= int(period_days) <= 3660:
            raise ValueError("Licence period must be between 1 and 3660 days")

        now = _now()
        existing = self.licence(user_id)
        current_end = _parse(existing.get("period_end"))
        start = current_end if existing.get("status") == "active" and current_end and current_end > now else now
        end = start + timedelta(days=int(period_days))

        with self._connect() as con:
            duplicate = con.execute(
                "SELECT id FROM aura_sec_payments WHERE payment_reference=?", (reference,)
            ).fetchone()
            if duplicate:
                raise ValueError("This Aura Sec payment reference has already been used")
            con.execute(
                """INSERT INTO aura_sec_payments
                   (id,user_id,sku_id,payment_reference,verified_by,verified_at,period_start,period_end,device_limit)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (uuid4().hex, user_id, sku, reference, verifier, _iso(now), _iso(start), _iso(end), int(device_limit)),
            )
            con.execute(
                """INSERT INTO aura_sec_licences
                   (user_id,status,sku_id,device_limit,period_start,period_end,last_payment_reference,verified_by,updated_at)
                   VALUES (?, 'active', ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(user_id) DO UPDATE SET
                     status='active',sku_id=excluded.sku_id,device_limit=excluded.device_limit,
                     period_start=excluded.period_start,period_end=excluded.period_end,
                     last_payment_reference=excluded.last_payment_reference,
                     verified_by=excluded.verified_by,updated_at=excluded.updated_at""",
                (user_id, sku, int(device_limit), _iso(start), _iso(end), reference, verifier, _iso(now)),
            )
        return self.licence(user_id)

    def list_devices(self, user_id: str) -> list[dict]:
        with self._connect() as con:
            rows = con.execute(
                """SELECT id,display_name,platform,architecture,agent_version,status,protection_state,
                          enrolled_at,last_seen_at,last_policy_version,revoked_at
                   FROM aura_sec_devices WHERE user_id=? ORDER BY enrolled_at DESC""",
                (user_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def enroll_attested_device(
        self,
        user_id: str,
        *,
        display_name: str,
        platform: str,
        architecture: str,
        public_key_fingerprint: str,
    ) -> dict:
        """Persist an enrolment after the enrolment service verifies device key/attestation."""
        licence = self.licence(user_id)
        if licence.get("status") != "active":
            raise PermissionError("Active Aura Sec licence required")
        devices = [item for item in self.list_devices(user_id) if item.get("status") != "revoked"]
        if len(devices) >= int(licence.get("device_limit") or 0):
            raise PermissionError("Aura Sec device limit reached")

        name = (display_name or "").strip()[:120]
        platform_value = (platform or "").strip().lower()[:40]
        architecture_value = (architecture or "").strip().lower()[:40]
        fingerprint = (public_key_fingerprint or "").strip().lower()
        if len(name) < 1 or len(platform_value) < 2 or len(architecture_value) < 2:
            raise ValueError("Device name, platform and architecture are required")
        if len(fingerprint) < 32 or len(fingerprint) > 256:
            raise ValueError("A verified device public-key fingerprint is required")

        device_id = uuid4().hex
        with self._connect() as con:
            try:
                con.execute(
                    """INSERT INTO aura_sec_devices
                       (id,user_id,display_name,platform,architecture,public_key_fingerprint,enrolled_at)
                       VALUES (?,?,?,?,?,?,?)""",
                    (device_id, user_id, name, platform_value, architecture_value, fingerprint, _iso()),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("This device key is already enrolled") from exc
        return self.get_device(user_id, device_id)

    def get_device(self, user_id: str, device_id: str) -> dict:
        with self._connect() as con:
            row = con.execute(
                """SELECT id,display_name,platform,architecture,agent_version,status,protection_state,
                          enrolled_at,last_seen_at,last_policy_version,revoked_at
                   FROM aura_sec_devices WHERE user_id=? AND id=?""",
                (user_id, device_id),
            ).fetchone()
        if not row:
            raise ValueError("Aura Sec device not found")
        return dict(row)

    def record_verified_heartbeat(
        self,
        user_id: str,
        device_id: str,
        *,
        signature_verified: bool,
        agent_version: str,
        policy_version: str,
        report_digest: str,
        protection_state: str,
    ) -> dict:
        """Accept heartbeat state only after device-signature verification by the protocol layer."""
        if not signature_verified:
            raise PermissionError("Unverified Aura Sec heartbeat rejected")
        allowed_states = {"healthy", "degraded", "attention_required", "isolated", "updating"}
        if protection_state not in allowed_states:
            raise ValueError("Invalid protection state")
        if len((report_digest or "").strip()) < 32:
            raise ValueError("Signed report digest is required")
        self.get_device(user_id, device_id)
        with self._connect() as con:
            con.execute(
                """UPDATE aura_sec_devices SET agent_version=?,last_policy_version=?,last_report_digest=?,
                          protection_state=?,last_seen_at=?
                   WHERE user_id=? AND id=? AND revoked_at IS NULL""",
                (
                    (agent_version or "").strip()[:80],
                    (policy_version or "").strip()[:80],
                    report_digest.strip().lower()[:256],
                    protection_state,
                    _iso(),
                    user_id,
                    device_id,
                ),
            )
        return self.get_device(user_id, device_id)

    def revoke_device(self, user_id: str, device_id: str) -> dict:
        self.get_device(user_id, device_id)
        with self._connect() as con:
            con.execute(
                """UPDATE aura_sec_devices SET status='revoked',protection_state='not_managed',revoked_at=?
                   WHERE user_id=? AND id=?""",
                (_iso(), user_id, device_id),
            )
        return self.get_device(user_id, device_id)

    def create_incident(
        self,
        user_id: str,
        device_id: str,
        *,
        severity: str,
        title: str,
        detection_id: str | None = None,
        confidence: float | None = None,
        summary: dict | None = None,
    ) -> dict:
        self.get_device(user_id, device_id)
        severity_value = (severity or "").strip().lower()
        if severity_value not in {"info", "low", "medium", "high", "critical"}:
            raise ValueError("Invalid incident severity")
        if confidence is not None and not 0.0 <= float(confidence) <= 1.0:
            raise ValueError("Confidence must be between 0 and 1")
        title_value = (title or "").strip()[:240]
        if not title_value:
            raise ValueError("Incident title is required")
        incident_id = uuid4().hex
        now = _iso()
        with self._connect() as con:
            con.execute(
                """INSERT INTO aura_sec_incidents
                   (id,user_id,device_id,severity,status,title,detection_id,confidence,summary_json,created_at,updated_at)
                   VALUES (?,?,?,?,'open',?,?,?,?,?,?)""",
                (
                    incident_id,
                    user_id,
                    device_id,
                    severity_value,
                    title_value,
                    (detection_id or "").strip()[:160] or None,
                    float(confidence) if confidence is not None else None,
                    json.dumps(summary or {}, separators=(",", ":"), ensure_ascii=False),
                    now,
                    now,
                ),
            )
        return self.get_incident(user_id, incident_id)

    def get_incident(self, user_id: str, incident_id: str) -> dict:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM aura_sec_incidents WHERE user_id=? AND id=?",
                (user_id, incident_id),
            ).fetchone()
        if not row:
            raise ValueError("Aura Sec incident not found")
        item = dict(row)
        try:
            item["summary"] = json.loads(item.pop("summary_json") or "{}")
        except json.JSONDecodeError:
            item["summary"] = {}
        return item

    def propose_action(
        self,
        user_id: str,
        device_id: str,
        *,
        action_type: str,
        risk_class: str,
        incident_id: str | None = None,
        details: dict | None = None,
    ) -> dict:
        self.get_device(user_id, device_id)
        if incident_id:
            self.get_incident(user_id, incident_id)
        risk = (risk_class or "").strip().lower()
        if risk not in {"read_only", "low_risk", "confirmation_required", "strong_reauth_required"}:
            raise ValueError("Invalid Aura Sec action risk class")
        action = (action_type or "").strip()[:120]
        if not action:
            raise ValueError("Action type is required")
        action_id = uuid4().hex
        with self._connect() as con:
            con.execute(
                """INSERT INTO aura_sec_actions
                   (id,incident_id,user_id,device_id,action_type,risk_class,status,details_json,requested_at)
                   VALUES (?,?,?,?,?,?,'proposed',?,?)""",
                (
                    action_id,
                    incident_id,
                    user_id,
                    device_id,
                    action,
                    risk,
                    json.dumps(details or {}, separators=(",", ":"), ensure_ascii=False),
                    _iso(),
                ),
            )
        return self.get_action(user_id, action_id)

    def approve_action(self, user_id: str, action_id: str, *, strong_reauth_verified: bool = False) -> dict:
        action = self.get_action(user_id, action_id)
        if action["status"] != "proposed":
            raise ValueError("Only proposed actions can be approved")
        if action["risk_class"] == "strong_reauth_required" and not strong_reauth_verified:
            raise PermissionError("Strong re-authentication is required for this action")
        with self._connect() as con:
            con.execute(
                "UPDATE aura_sec_actions SET status='approved',approved_at=? WHERE user_id=? AND id=?",
                (_iso(), user_id, action_id),
            )
        return self.get_action(user_id, action_id)

    def get_action(self, user_id: str, action_id: str) -> dict:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM aura_sec_actions WHERE user_id=? AND id=?",
                (user_id, action_id),
            ).fetchone()
        if not row:
            raise ValueError("Aura Sec action not found")
        item = dict(row)
        try:
            item["details"] = json.loads(item.pop("details_json") or "{}")
        except json.JSONDecodeError:
            item["details"] = {}
        return item


__all__ = ["AuraSecStore", "SecurityLicence"]
