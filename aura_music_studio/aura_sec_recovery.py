from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from .accounts import AccountStore
from .aura_sec_store import AuraSecStore


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).isoformat()


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Recovery timestamps must be timezone-aware")
    return parsed.astimezone(timezone.utc)


_ALLOWED_TARGET_TYPES = {"local_versioned", "external_drive", "cloud_encrypted", "immutable_cloud", "offline_export"}


class AuraSecRecoveryStore:
    """Evidence-backed ransomware/data-loss recovery state.

    A configured backup destination is never equivalent to proven recovery. Readiness
    requires cryptographically/integrity verified recovery points and recent restore tests.
    Provider credentials and encryption root keys are deliberately outside this database.
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
                CREATE TABLE IF NOT EXISTS aura_sec_backup_targets (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    target_type TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    encrypted INTEGER NOT NULL,
                    isolated_or_immutable INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    verified_at TEXT NOT NULL,
                    last_successful_backup_at TEXT,
                    last_integrity_check_at TEXT,
                    revoked_at TEXT,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY(device_id) REFERENCES aura_sec_devices(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS aura_sec_recovery_points (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    content_digest TEXT NOT NULL,
                    manifest_digest TEXT NOT NULL,
                    integrity_verified INTEGER NOT NULL DEFAULT 0,
                    malware_scan_state TEXT NOT NULL DEFAULT 'not_scanned',
                    verified_at TEXT,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY(device_id) REFERENCES aura_sec_devices(id) ON DELETE CASCADE,
                    FOREIGN KEY(target_id) REFERENCES aura_sec_backup_targets(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS aura_sec_restore_drills (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    recovery_point_id TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    status TEXT NOT NULL DEFAULT 'running',
                    integrity_reverified INTEGER NOT NULL DEFAULT 0,
                    restored_content_digest TEXT,
                    notes TEXT,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY(device_id) REFERENCES aura_sec_devices(id) ON DELETE CASCADE,
                    FOREIGN KEY(recovery_point_id) REFERENCES aura_sec_recovery_points(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_aura_sec_recovery_points_device_created
                ON aura_sec_recovery_points(device_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_aura_sec_restore_drills_device_completed
                ON aura_sec_restore_drills(device_id, completed_at);
                """
            )

    def register_verified_target(
        self,
        user_id: str,
        device_id: str,
        *,
        target_type: str,
        display_name: str,
        encrypted: bool,
        isolated_or_immutable: bool,
        provider_connection_verified: bool,
    ) -> dict:
        if not provider_connection_verified:
            raise PermissionError("Backup target connection must be verified before registration")
        if self.security.licence(user_id).get("status") != "active":
            raise PermissionError("Active Aura Sec licence required")
        device = self.security.get_device(user_id, device_id)
        if device.get("status") == "revoked":
            raise PermissionError("Cannot configure recovery for a revoked device")
        kind = (target_type or "").strip().lower()
        if kind not in _ALLOWED_TARGET_TYPES:
            raise ValueError("Unsupported Aura Sec backup target type")
        name = (display_name or "").strip()[:160]
        if not name:
            raise ValueError("Backup target display name is required")
        target_id = uuid4().hex
        with self._connect() as con:
            con.execute(
                """INSERT INTO aura_sec_backup_targets
                   (id,user_id,device_id,target_type,display_name,encrypted,isolated_or_immutable,verified_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    target_id,
                    user_id,
                    device_id,
                    kind,
                    name,
                    int(bool(encrypted)),
                    int(bool(isolated_or_immutable)),
                    _iso(),
                ),
            )
        return self.get_target(user_id, target_id)

    def get_target(self, user_id: str, target_id: str) -> dict:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM aura_sec_backup_targets WHERE user_id=? AND id=?",
                (user_id, target_id),
            ).fetchone()
        if not row:
            raise ValueError("Aura Sec backup target not found")
        item = dict(row)
        item["encrypted"] = bool(item["encrypted"])
        item["isolated_or_immutable"] = bool(item["isolated_or_immutable"])
        return item

    def record_verified_recovery_point(
        self,
        user_id: str,
        device_id: str,
        target_id: str,
        *,
        content_digest: str,
        manifest_digest: str,
        integrity_verified: bool,
        malware_scan_state: str,
        created_at: datetime | None = None,
    ) -> dict:
        target = self.get_target(user_id, target_id)
        if target["device_id"] != device_id or target["status"] != "active":
            raise PermissionError("Backup target is not active for this device")
        if not integrity_verified:
            raise PermissionError("Recovery point integrity must be verified before it can count toward readiness")
        scan_state = (malware_scan_state or "").strip().lower()
        if scan_state not in {"clean", "suspicious", "infected", "not_scanned"}:
            raise ValueError("Invalid recovery point malware scan state")
        for label, digest in (("content", content_digest), ("manifest", manifest_digest)):
            if len((digest or "").strip()) != 64 or any(ch not in "0123456789abcdefABCDEF" for ch in digest):
                raise ValueError(f"{label} digest must be a SHA-256 hexadecimal value")
        point_id = uuid4().hex
        created = (created_at or _now()).astimezone(timezone.utc)
        verified = _now()
        with self._connect() as con:
            con.execute(
                """INSERT INTO aura_sec_recovery_points
                   (id,user_id,device_id,target_id,created_at,content_digest,manifest_digest,
                    integrity_verified,malware_scan_state,verified_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    point_id,
                    user_id,
                    device_id,
                    target_id,
                    _iso(created),
                    content_digest.lower(),
                    manifest_digest.lower(),
                    1,
                    scan_state,
                    _iso(verified),
                ),
            )
            con.execute(
                """UPDATE aura_sec_backup_targets
                   SET last_successful_backup_at=?,last_integrity_check_at=?
                   WHERE user_id=? AND id=?""",
                (_iso(created), _iso(verified), user_id, target_id),
            )
        return self.get_recovery_point(user_id, point_id)

    def get_recovery_point(self, user_id: str, point_id: str) -> dict:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM aura_sec_recovery_points WHERE user_id=? AND id=?",
                (user_id, point_id),
            ).fetchone()
        if not row:
            raise ValueError("Aura Sec recovery point not found")
        item = dict(row)
        item["integrity_verified"] = bool(item["integrity_verified"])
        return item

    def record_restore_drill(
        self,
        user_id: str,
        device_id: str,
        recovery_point_id: str,
        *,
        status: str,
        integrity_reverified: bool,
        restored_content_digest: str | None,
        notes: str = "",
        completed_at: datetime | None = None,
    ) -> dict:
        point = self.get_recovery_point(user_id, recovery_point_id)
        if point["device_id"] != device_id:
            raise PermissionError("Recovery point does not belong to this device")
        state = (status or "").strip().lower()
        if state not in {"success", "failed"}:
            raise ValueError("Restore drill status must be success or failed")
        if state == "success" and not integrity_reverified:
            raise ValueError("Successful restore drill requires post-restore integrity verification")
        if state == "success":
            if not restored_content_digest or len(restored_content_digest.strip()) != 64:
                raise ValueError("Successful restore drill requires restored content SHA-256")
            if restored_content_digest.lower() != point["content_digest"].lower():
                raise ValueError("Restored content digest does not match recovery point")
        drill_id = uuid4().hex
        finished = (completed_at or _now()).astimezone(timezone.utc)
        with self._connect() as con:
            con.execute(
                """INSERT INTO aura_sec_restore_drills
                   (id,user_id,device_id,recovery_point_id,started_at,completed_at,status,
                    integrity_reverified,restored_content_digest,notes)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    drill_id,
                    user_id,
                    device_id,
                    recovery_point_id,
                    _iso(finished),
                    _iso(finished),
                    state,
                    int(bool(integrity_reverified)),
                    restored_content_digest.lower() if restored_content_digest else None,
                    (notes or "").strip()[:1000],
                ),
            )
        return self.get_restore_drill(user_id, drill_id)

    def get_restore_drill(self, user_id: str, drill_id: str) -> dict:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM aura_sec_restore_drills WHERE user_id=? AND id=?",
                (user_id, drill_id),
            ).fetchone()
        if not row:
            raise ValueError("Aura Sec restore drill not found")
        item = dict(row)
        item["integrity_reverified"] = bool(item["integrity_reverified"])
        return item

    def readiness(
        self,
        user_id: str,
        device_id: str,
        *,
        now: datetime | None = None,
        max_recovery_point_age: timedelta = timedelta(days=1),
        max_restore_test_age: timedelta = timedelta(days=30),
    ) -> dict:
        current = (now or _now()).astimezone(timezone.utc)
        with self._connect() as con:
            target_rows = con.execute(
                """SELECT * FROM aura_sec_backup_targets
                   WHERE user_id=? AND device_id=? AND status='active' AND revoked_at IS NULL""",
                (user_id, device_id),
            ).fetchall()
            point_row = con.execute(
                """SELECT * FROM aura_sec_recovery_points
                   WHERE user_id=? AND device_id=? AND integrity_verified=1
                   ORDER BY created_at DESC LIMIT 1""",
                (user_id, device_id),
            ).fetchone()
            drill_row = con.execute(
                """SELECT * FROM aura_sec_restore_drills
                   WHERE user_id=? AND device_id=? AND status='success' AND integrity_reverified=1
                   ORDER BY completed_at DESC LIMIT 1""",
                (user_id, device_id),
            ).fetchone()

        targets = [dict(row) for row in target_rows]
        latest_point = dict(point_row) if point_row else None
        latest_drill = dict(drill_row) if drill_row else None
        encrypted_target = any(bool(row["encrypted"]) for row in targets)
        isolated_target = any(bool(row["isolated_or_immutable"]) for row in targets)
        point_fresh = bool(
            latest_point and current - _parse(latest_point["created_at"]) <= max_recovery_point_age
        )
        point_clean = bool(latest_point and latest_point["malware_scan_state"] == "clean")
        drill_fresh = bool(
            latest_drill and current - _parse(latest_drill["completed_at"]) <= max_restore_test_age
        )

        if not targets:
            state = "not_configured"
        elif not latest_point:
            state = "no_verified_recovery_point"
        elif not point_fresh:
            state = "recovery_point_stale"
        elif not latest_drill or not drill_fresh:
            state = "restore_test_required"
        elif not point_clean:
            state = "recovery_point_needs_security_review"
        elif encrypted_target and isolated_target:
            state = "ransomware_ready"
        else:
            state = "recovery_verified"

        return {
            "state": state,
            "active_targets": len(targets),
            "encrypted_target_present": encrypted_target,
            "isolated_or_immutable_target_present": isolated_target,
            "latest_recovery_point": None
            if not latest_point
            else {
                "id": latest_point["id"],
                "created_at": latest_point["created_at"],
                "malware_scan_state": latest_point["malware_scan_state"],
                "fresh": point_fresh,
            },
            "latest_restore_drill": None
            if not latest_drill
            else {
                "id": latest_drill["id"],
                "completed_at": latest_drill["completed_at"],
                "fresh": drill_fresh,
            },
            "restore_proven": bool(latest_drill and drill_fresh),
            "truth": "Backup configuration alone never counts as proven recovery; readiness requires verified data integrity and a recent successful restore drill.",
        }


__all__ = ["AuraSecRecoveryStore"]
