from __future__ import annotations

import json
import os
import sqlite3
import time
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path

from .backup import StudioBackupManager

EVIDENCE_SCHEMA_VERSION = 1
_ALLOWED_RESTORE_ENVIRONMENTS = {"test", "ci", "integration", "staging", "recovery"}
ApplicationRestoreValidator = Callable[[sqlite3.Connection, Path], bool]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).isoformat()


def _parse_time(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def load_restore_evidence(path: str | Path | None, *, max_age_hours: int = 24 * 30) -> dict:
    """Load secret-free restore evidence without treating the document as authority by itself."""
    if not path:
        return {
            "configured": False,
            "verified": False,
            "state": "not_configured",
            "reason": "No restore evidence path is configured.",
        }

    evidence_path = Path(path).expanduser().resolve()
    if not evidence_path.is_file():
        return {
            "configured": True,
            "verified": False,
            "state": "missing",
            "reason": "Configured restore evidence file does not exist.",
        }

    try:
        payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {
            "configured": True,
            "verified": False,
            "state": "invalid",
            "reason": "Restore evidence is unreadable or invalid JSON.",
        }

    if not isinstance(payload, dict) or payload.get("schema_version") != EVIDENCE_SCHEMA_VERSION:
        return {
            "configured": True,
            "verified": False,
            "state": "invalid",
            "reason": "Restore evidence schema is unsupported.",
        }

    executed_at = _parse_time(payload.get("executed_at"))
    environment = str(payload.get("environment") or "").strip().lower()
    result = str(payload.get("result") or "").strip().lower()
    integrity = str(payload.get("database_integrity") or "").strip().lower()
    application_check = bool(payload.get("application_data_check"))
    hashes_verified = bool(payload.get("backup_hashes_verified"))
    now = _now()
    age_hours = ((now - executed_at).total_seconds() / 3600) if executed_at else None
    fresh = age_hours is not None and 0 <= age_hours <= max(1, int(max_age_hours))
    environment_ok = environment in _ALLOWED_RESTORE_ENVIRONMENTS
    verified = bool(
        result == "verified"
        and integrity == "ok"
        and application_check
        and hashes_verified
        and fresh
        and environment_ok
    )

    reason = None
    if not executed_at:
        reason = "Restore evidence has no valid execution timestamp."
    elif not fresh:
        reason = "Restore evidence is stale or timestamped in the future."
    elif not environment_ok:
        reason = "Restore evidence must come from an isolated test, CI, integration, staging or recovery environment."
    elif result != "verified" or integrity != "ok" or not application_check or not hashes_verified:
        reason = "Restore evidence does not prove a complete verified drill."

    return {
        "configured": True,
        "verified": verified,
        "state": "verified" if verified else "unverified",
        "environment": environment or None,
        "executed_at": executed_at.isoformat() if executed_at else None,
        "age_hours": round(age_hours, 3) if age_hours is not None else None,
        "duration_seconds": payload.get("duration_seconds"),
        "database_integrity": integrity or None,
        "application_data_check": application_check,
        "application_validation_source": payload.get("application_validation_source"),
        "backup_hashes_verified": hashes_verified,
        "production_backup_used": bool(payload.get("production_backup_used", False)),
        "reason": reason,
    }


def probe_runtime_storage(environ: Mapping[str, str] | None = None) -> dict:
    """Perform bounded non-destructive connectivity probes of local durable-state dependencies."""
    env = environ or os.environ
    db = Path(str(env.get("LSS_DB_PATH") or "data/live_sound_studio.sqlite3")).expanduser().resolve()
    projects = Path(str(env.get("AURA_PROJECTS_ROOT") or "projects")).expanduser().resolve()
    backups = Path(str(env.get("LSS_BACKUP_DIR") or "backups")).expanduser().resolve()

    db_state = "unavailable"
    db_connectivity = None
    db_error = None
    if db.is_file():
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
            try:
                row = con.execute("SELECT 1").fetchone()
                db_connectivity = "ok" if row and row[0] == 1 else "failed"
                db_state = "healthy" if db_connectivity == "ok" else "degraded"
            finally:
                con.close()
        except sqlite3.Error as exc:
            db_error = type(exc).__name__

    def directory_state(path: Path) -> str:
        if not path.is_dir():
            return "unavailable"
        return "healthy" if os.access(path, os.R_OK | os.W_OK | os.X_OK) else "degraded"

    project_state = directory_state(projects)
    backup_state = directory_state(backups)
    critical_ok = db_state == project_state == backup_state == "healthy"
    return {
        "verified": critical_ok,
        "database": {
            "state": db_state,
            "connectivity_check": db_connectivity,
            "full_integrity_check_performed": False,
            "error_code": db_error,
        },
        "project_storage": {"state": project_state},
        "backup_storage": {"state": backup_state},
        "external_provider_probes_performed": False,
        "destructive_writes_performed": False,
    }


def run_restore_drill(
    work_dir: Path,
    *,
    environment: str = "ci",
    production_backup: Path | None = None,
    application_validator: ApplicationRestoreValidator | None = None,
) -> dict:
    """Run an isolated backup/restore drill and return secret-free release evidence.

    Synthetic mode proves the repository's backup/restore mechanism with deterministic probe data.
    A supplied production backup is stricter: SQLite integrity alone is never treated as an
    application-level verification. Production-backup evidence can become verified only when the
    caller supplies an explicit validator that checks representative restored application state.
    """
    environment = str(environment or "").strip().lower()
    if environment not in _ALLOWED_RESTORE_ENVIRONMENTS:
        raise ValueError("Restore drills must run in test, ci, integration, staging or recovery environments")

    root = Path(work_dir).resolve()
    source_root = root / "source"
    target_root = root / "restored"
    backup_root = root / "backups"
    source_db = source_root / "studio.sqlite3"
    source_projects = source_root / "projects"
    source_projects.mkdir(parents=True, exist_ok=True)
    backup_root.mkdir(parents=True, exist_ok=True)

    started = time.monotonic()
    production_backup_used = production_backup is not None
    if production_backup is None:
        con = sqlite3.connect(source_db)
        try:
            con.execute("CREATE TABLE restore_probe (id TEXT PRIMARY KEY, value TEXT NOT NULL)")
            con.execute("INSERT INTO restore_probe VALUES ('probe-1', 'expected-value')")
            con.commit()
        finally:
            con.close()
        project = source_projects / "restore-probe"
        project.mkdir(parents=True, exist_ok=True)
        (project / "project.json").write_text('{"probe":"expected-value"}\n', encoding="utf-8")
        source = StudioBackupManager(database=source_db, projects=source_projects, backup_dir=backup_root)
        archive = Path(source.create()["backup"])
    else:
        archive = Path(production_backup).expanduser().resolve()
        if not archive.is_file():
            raise FileNotFoundError(archive)

    inspected = StudioBackupManager.inspect(archive, verify_hashes=True)
    target_db = target_root / "data" / "studio.sqlite3"
    target_projects = target_root / "projects"
    target = StudioBackupManager(database=target_db, projects=target_projects, backup_dir=root / "restored-backups")
    restored = target.restore(archive, confirm_offline=True, preserve_existing=True)

    con = sqlite3.connect(target_db)
    try:
        integrity_row = con.execute("PRAGMA integrity_check").fetchone()
        integrity = str(integrity_row[0]).lower() if integrity_row else "unknown"
        if production_backup_used:
            application_data_check = bool(application_validator and application_validator(con, target_projects))
            validation_source = "explicit_validator" if application_validator else "missing"
        else:
            row = con.execute("SELECT value FROM restore_probe WHERE id='probe-1'").fetchone()
            application_data_check = bool(row and row[0] == "expected-value")
            validation_source = "synthetic_probe"
    finally:
        con.close()

    if not production_backup_used:
        application_data_check = application_data_check and (
            target_projects / "restore-probe" / "project.json"
        ).is_file()

    duration = time.monotonic() - started
    verified = bool(
        restored.get("restored")
        and inspected.get("hashes_verified")
        and integrity == "ok"
        and application_data_check
    )
    return {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "kind": "restore_drill",
        "result": "verified" if verified else "failed",
        "environment": environment,
        "executed_at": _iso(),
        "duration_seconds": round(duration, 3),
        "backup_hashes_verified": bool(inspected.get("hashes_verified")),
        "verified_file_count": int(inspected.get("verified_files") or 0),
        "database_integrity": integrity,
        "application_data_check": application_data_check,
        "application_validation_source": validation_source,
        "production_backup_used": production_backup_used,
        "deployment_secrets_changed": bool(restored.get("deployment_secrets_changed", True)),
    }


def write_restore_evidence(path: Path, evidence: dict) -> Path:
    target = Path(path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target


__all__ = [
    "ApplicationRestoreValidator",
    "EVIDENCE_SCHEMA_VERSION",
    "load_restore_evidence",
    "probe_runtime_storage",
    "run_restore_drill",
    "write_restore_evidence",
]
