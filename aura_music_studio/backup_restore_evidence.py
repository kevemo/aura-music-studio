from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REQUIRED_CHECKS = (
    "archive_hash_verified",
    "manifest_verified",
    "database_integrity_ok",
    "project_files_restored",
    "application_startup_ok",
    "read_path_smoke_test_ok",
)
_IDENTIFIER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{2,159}")
_CHECK_RE = re.compile(r"[a-z][a-z0-9_]{2,63}")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_ENVIRONMENT_RE = re.compile(r"[a-z0-9][a-z0-9._-]{1,63}")
_SECRET_MARKERS = (
    "password=",
    "passwd=",
    "secret=",
    "token=",
    "api_key=",
    "apikey=",
    "authorization:",
    "bearer ",
    "-----begin ",
    "sk_live_",
    "sk_test_",
    "sk-proj-",
    "rk_live_",
    "whsec_",
    "postgres://",
    "postgresql://",
    "mysql://",
    "mongodb://",
    "redis://",
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _timestamp(value: Any, field: str) -> datetime:
    raw = str(value or "").strip()
    if not raw or len(raw) > 64:
        raise ValueError(f"Backup/restore evidence has an invalid {field}")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"Backup/restore evidence has an invalid {field}") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"Backup/restore evidence {field} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _reject_secret_like(value: str, field: str) -> str:
    lowered = value.lower()
    if any(marker in lowered for marker in _SECRET_MARKERS):
        raise ValueError(f"Backup/restore evidence {field} must not contain secrets or credentials")
    return value


def _identifier(value: Any, field: str) -> str:
    result = str(value or "").strip()
    if not _IDENTIFIER_RE.fullmatch(result):
        raise ValueError(f"Backup/restore evidence has an invalid {field}")
    return _reject_secret_like(result, field)


def _environment(value: Any) -> str:
    result = str(value or "").strip().lower()
    if not _ENVIRONMENT_RE.fullmatch(result):
        raise ValueError("Backup/restore evidence has an invalid environment")
    return result


def _sha256(value: Any) -> str:
    result = str(value or "").strip().lower()
    if not _SHA256_RE.fullmatch(result):
        raise ValueError("Backup/restore evidence has an invalid backup SHA-256")
    return result


def _canonical_checks(checks: Mapping[str, Any]) -> dict[str, bool]:
    if not isinstance(checks, Mapping):
        raise ValueError("Backup/restore checks must be a mapping")
    canonical: dict[str, bool] = {}
    for raw_name, raw_value in checks.items():
        name = str(raw_name or "").strip().lower()
        if not _CHECK_RE.fullmatch(name):
            raise ValueError("Backup/restore evidence has an invalid check name")
        if type(raw_value) is not bool:
            raise ValueError(f"Backup/restore check {name} must be boolean")
        canonical[name] = raw_value
    missing = [name for name in _REQUIRED_CHECKS if name not in canonical]
    if missing:
        raise ValueError(f"Backup/restore evidence is missing required checks: {', '.join(missing)}")
    return dict(sorted(canonical.items()))


def _canonical_payload(
    *,
    drill_id: Any,
    environment: Any,
    backup_artifact_ref: Any,
    backup_sha256: Any,
    schema_revision: Any,
    restore_target_type: Any,
    backup_created_at: Any,
    restore_started_at: Any,
    restore_completed_at: Any,
    verification_completed_at: Any,
    checks: Mapping[str, Any],
) -> dict[str, Any]:
    backup_created = _timestamp(backup_created_at, "backup creation timestamp")
    restore_started = _timestamp(restore_started_at, "restore start timestamp")
    restore_completed = _timestamp(restore_completed_at, "restore completion timestamp")
    verification_completed = _timestamp(
        verification_completed_at, "verification completion timestamp"
    )
    if backup_created > restore_started:
        raise ValueError("Backup creation timestamp cannot be after restore start")
    if restore_started > restore_completed:
        raise ValueError("Restore completion timestamp cannot be before restore start")
    if restore_completed > verification_completed:
        raise ValueError("Verification completion timestamp cannot be before restore completion")

    canonical_checks = _canonical_checks(checks)
    measured_rpo_seconds = int((restore_started - backup_created).total_seconds())
    measured_rto_seconds = int((verification_completed - restore_started).total_seconds())
    if measured_rpo_seconds < 0 or measured_rto_seconds < 0:
        raise ValueError("Backup/restore recovery measurements cannot be negative")

    payload: dict[str, Any] = {
        "drill_id": _identifier(drill_id, "drill id"),
        "environment": _environment(environment),
        "backup_artifact_ref": _identifier(backup_artifact_ref, "backup artifact reference"),
        "backup_sha256": _sha256(backup_sha256),
        "schema_revision": _identifier(schema_revision, "schema revision"),
        "restore_target_type": _identifier(restore_target_type, "restore target type").lower(),
        "backup_created_at": _iso(backup_created),
        "restore_started_at": _iso(restore_started),
        "restore_completed_at": _iso(restore_completed),
        "verification_completed_at": _iso(verification_completed),
        "checks": canonical_checks,
        # For a restore drill, RPO is represented as backup age at restore start.
        "measured_rpo_seconds": measured_rpo_seconds,
        # RTO runs through post-restore verification, not only file/database replacement.
        "measured_rto_seconds": measured_rto_seconds,
        "passed": all(canonical_checks[name] for name in _REQUIRED_CHECKS),
    }
    payload["evidence_sha256"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return payload


class BackupRestoreEvidenceStore:
    """Append-only evidence for completed backup/restore drills.

    The store contains operational facts only: opaque artifact/revision identifiers, hashes,
    timestamps and boolean verification results. It deliberately does not store backup content,
    database URLs, credentials, provider tokens, environment variables or free-form operator notes.

    A record proves only that the caller supplied a completed drill result. It does not perform the
    restore and it does not convert repository tests into production evidence.
    """

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS backup_restore_drill_evidence (
                    drill_id TEXT PRIMARY KEY,
                    environment TEXT NOT NULL,
                    backup_artifact_ref TEXT NOT NULL,
                    backup_sha256 TEXT NOT NULL,
                    schema_revision TEXT NOT NULL,
                    restore_target_type TEXT NOT NULL,
                    backup_created_at TEXT NOT NULL,
                    restore_started_at TEXT NOT NULL,
                    restore_completed_at TEXT NOT NULL,
                    verification_completed_at TEXT NOT NULL,
                    checks_json TEXT NOT NULL,
                    measured_rpo_seconds INTEGER NOT NULL CHECK(measured_rpo_seconds >= 0),
                    measured_rto_seconds INTEGER NOT NULL CHECK(measured_rto_seconds >= 0),
                    passed INTEGER NOT NULL CHECK(passed IN (0,1)),
                    evidence_sha256 TEXT NOT NULL,
                    recorded_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_backup_restore_evidence_environment_time
                    ON backup_restore_drill_evidence(environment, verification_completed_at DESC);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        return con

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        result = dict(row)
        result["checks"] = json.loads(result.pop("checks_json"))
        result["passed"] = bool(result["passed"])
        return result

    def get(self, drill_id: str) -> dict[str, Any] | None:
        drill_id = _identifier(drill_id, "drill id")
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM backup_restore_drill_evidence WHERE drill_id=?",
                (drill_id,),
            ).fetchone()
        return self._row(row)

    def latest(self, environment: str) -> dict[str, Any] | None:
        environment = _environment(environment)
        with self._connect() as con:
            row = con.execute(
                """
                SELECT * FROM backup_restore_drill_evidence
                WHERE environment=?
                ORDER BY verification_completed_at DESC, recorded_at DESC
                LIMIT 1
                """,
                (environment,),
            ).fetchone()
        return self._row(row)

    def record(
        self,
        *,
        drill_id: str,
        environment: str,
        backup_artifact_ref: str,
        backup_sha256: str,
        schema_revision: str,
        restore_target_type: str,
        backup_created_at: str,
        restore_started_at: str,
        restore_completed_at: str,
        verification_completed_at: str,
        checks: Mapping[str, Any],
    ) -> dict[str, Any]:
        payload = _canonical_payload(
            drill_id=drill_id,
            environment=environment,
            backup_artifact_ref=backup_artifact_ref,
            backup_sha256=backup_sha256,
            schema_revision=schema_revision,
            restore_target_type=restore_target_type,
            backup_created_at=backup_created_at,
            restore_started_at=restore_started_at,
            restore_completed_at=restore_completed_at,
            verification_completed_at=verification_completed_at,
            checks=checks,
        )
        checks_json = json.dumps(payload["checks"], sort_keys=True, separators=(",", ":"))
        expected = {
            key: value
            for key, value in payload.items()
            if key != "checks"
        }
        expected["checks_json"] = checks_json

        with self._connect() as con:
            existing = con.execute(
                "SELECT * FROM backup_restore_drill_evidence WHERE drill_id=?",
                (payload["drill_id"],),
            ).fetchone()
            if existing:
                result = dict(existing)
                for key, value in expected.items():
                    expected_value = int(value) if key == "passed" else value
                    if result.get(key) != expected_value:
                        raise ValueError("Backup/restore drill evidence changed after it was recorded")
                return self._row(existing) or {}

            con.execute(
                """
                INSERT INTO backup_restore_drill_evidence (
                    drill_id, environment, backup_artifact_ref, backup_sha256, schema_revision,
                    restore_target_type, backup_created_at, restore_started_at,
                    restore_completed_at, verification_completed_at, checks_json,
                    measured_rpo_seconds, measured_rto_seconds, passed, evidence_sha256, recorded_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    payload["drill_id"],
                    payload["environment"],
                    payload["backup_artifact_ref"],
                    payload["backup_sha256"],
                    payload["schema_revision"],
                    payload["restore_target_type"],
                    payload["backup_created_at"],
                    payload["restore_started_at"],
                    payload["restore_completed_at"],
                    payload["verification_completed_at"],
                    checks_json,
                    payload["measured_rpo_seconds"],
                    payload["measured_rto_seconds"],
                    int(payload["passed"]),
                    payload["evidence_sha256"],
                    _iso(_now()),
                ),
            )
            row = con.execute(
                "SELECT * FROM backup_restore_drill_evidence WHERE drill_id=?",
                (payload["drill_id"],),
            ).fetchone()
        return self._row(row) or {}


def assess_backup_restore_release_gate(
    *,
    store: BackupRestoreEvidenceStore,
    environment: str,
    max_evidence_age_seconds: int,
    max_rpo_seconds: int,
    max_rto_seconds: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Assess caller-owned release thresholds against the latest restore-drill evidence.

    No production thresholds are embedded here. Release owners must supply the approved evidence
    freshness, RPO and RTO limits for the environment being assessed.
    """

    thresholds = {
        "max_evidence_age_seconds": max_evidence_age_seconds,
        "max_rpo_seconds": max_rpo_seconds,
        "max_rto_seconds": max_rto_seconds,
    }
    for name, value in thresholds.items():
        if type(value) is not int or value < 0:
            raise ValueError(f"{name} must be a non-negative integer")

    current = now or _now()
    if current.tzinfo is None:
        raise ValueError("Release gate assessment time must be timezone-aware")
    current = current.astimezone(timezone.utc)
    environment = _environment(environment)
    evidence = store.latest(environment)
    reasons: list[str] = []
    if evidence is None:
        reasons.append("no_restore_drill_evidence")
        return {
            "ready": False,
            "environment": environment,
            "drill_id": None,
            "reasons": reasons,
            **thresholds,
        }

    verified_at = _timestamp(
        evidence["verification_completed_at"], "verification completion timestamp"
    )
    age_seconds = int((current - verified_at).total_seconds())
    if age_seconds < 0:
        reasons.append("evidence_timestamp_is_in_future")
    elif age_seconds > max_evidence_age_seconds:
        reasons.append("restore_drill_evidence_is_stale")
    if not evidence["passed"]:
        reasons.append("latest_restore_drill_failed_required_checks")
    if int(evidence["measured_rpo_seconds"]) > max_rpo_seconds:
        reasons.append("measured_rpo_exceeds_release_threshold")
    if int(evidence["measured_rto_seconds"]) > max_rto_seconds:
        reasons.append("measured_rto_exceeds_release_threshold")

    return {
        "ready": not reasons,
        "environment": environment,
        "drill_id": evidence["drill_id"],
        "evidence_sha256": evidence["evidence_sha256"],
        "evidence_age_seconds": age_seconds,
        "measured_rpo_seconds": int(evidence["measured_rpo_seconds"]),
        "measured_rto_seconds": int(evidence["measured_rto_seconds"]),
        "reasons": reasons,
        **thresholds,
    }


def _default_store() -> BackupRestoreEvidenceStore:
    database = Path(os.getenv("LSS_DB_PATH", "data/live_sound_studio.sqlite3")).resolve()
    return BackupRestoreEvidenceStore(database)


backup_restore_evidence = _default_store()
REQUIRED_RESTORE_CHECKS = _REQUIRED_CHECKS

__all__ = [
    "BackupRestoreEvidenceStore",
    "REQUIRED_RESTORE_CHECKS",
    "assess_backup_restore_release_gate",
    "backup_restore_evidence",
]
