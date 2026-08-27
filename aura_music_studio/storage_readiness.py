from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .backup import StudioBackupManager

_EPHEMERAL_ROOTS = (
    Path("/tmp"),
    Path("/var/tmp"),
    Path("/dev/shm"),
    Path("/run"),
)


def _value(env: Mapping[str, str], name: str, default: str) -> str:
    return str(env.get(name, default) or default).strip()


def _resolved(path: Path) -> Path:
    return path.expanduser().absolute()


def _inside(path: Path, root: Path) -> bool:
    path = _resolved(path)
    root = _resolved(root)
    return path == root or root in path.parents


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class StoragePaths:
    database: Path
    projects: Path
    backups: Path

    @classmethod
    def from_environ(cls, env: Mapping[str, str]) -> "StoragePaths":
        return cls(
            database=Path(_value(env, "LSS_DB_PATH", "data/live_sound_studio.sqlite3")),
            projects=Path(_value(env, "AURA_PROJECTS_ROOT", "projects")),
            backups=Path(_value(env, "LSS_BACKUP_DIR", "backups")),
        )

    def public_details(self) -> dict:
        return {
            "database_absolute": self.database.is_absolute(),
            "projects_absolute": self.projects.is_absolute(),
            "backups_absolute": self.backups.is_absolute(),
        }


def validate_storage_contract(
    environ: Mapping[str, str] | None = None,
    *,
    require_production: bool | None = None,
) -> dict:
    """Validate the storage *contract* without writing to deployment paths.

    Production paths must be absolute, non-ephemeral and non-overlapping. The report is
    deliberately secret-free and does not expose configured filesystem paths because owner
    health/readiness responses may be collected by monitoring systems.
    """

    env = environ or os.environ
    deployment = _value(env, "AURA_DEPLOYMENT_ENV", "development").lower()
    production = deployment == "production" if require_production is None else require_production
    paths = StoragePaths.from_environ(env)
    errors: list[str] = []

    if production:
        for label, path in (
            ("database", paths.database),
            ("projects", paths.projects),
            ("backups", paths.backups),
        ):
            if not path.is_absolute():
                errors.append(f"Production {label} storage must use an absolute path.")
            resolved = _resolved(path)
            if any(_inside(resolved, root) for root in _EPHEMERAL_ROOTS):
                errors.append(f"Production {label} storage cannot use an ephemeral runtime root.")

    db_parent = _resolved(paths.database.parent)
    projects = _resolved(paths.projects)
    backups = _resolved(paths.backups)

    if projects == backups or _inside(projects, backups) or _inside(backups, projects):
        errors.append("Project and backup storage must use separate non-nested roots.")
    if _inside(db_parent, projects) or _inside(projects, db_parent):
        errors.append("Database and project storage must not overlap.")
    if _inside(db_parent, backups) or _inside(backups, db_parent):
        errors.append("Database and backup storage must not overlap.")
    if paths.database.name in {"", ".", ".."}:
        errors.append("Database path must name a file.")

    return {
        "ok": not errors,
        "environment": deployment,
        "production_contract_required": bool(production),
        "errors": errors,
        "details": {
            **paths.public_details(),
            "ephemeral_roots_rejected": True,
            "storage_roots_non_overlapping": not any("overlap" in item or "nested" in item for item in errors),
            "configured_paths_exposed": False,
        },
    }


def _probe_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(prefix=".aura-storage-probe-", dir=path, delete=False)
    probe = Path(handle.name)
    try:
        handle.write(b"aura-storage-probe\n")
        handle.flush()
        os.fsync(handle.fileno())
        handle.close()
        with probe.open("rb") as reader:
            if reader.read() != b"aura-storage-probe\n":
                raise RuntimeError("Storage probe could not read back the bytes it wrote")
    finally:
        try:
            handle.close()
        except Exception:
            pass
        probe.unlink(missing_ok=True)


def probe_storage_runtime(
    environ: Mapping[str, str] | None = None,
    *,
    create_missing: bool = False,
) -> dict:
    """Probe actual configured storage without exposing its paths.

    The probe writes and fsyncs a tiny file inside each durable root, then checks SQLite
    integrity when the configured member database already exists. It never creates the actual
    application database as a side effect.
    """

    env = environ or os.environ
    paths = StoragePaths.from_environ(env)
    targets = {
        "database_parent": _resolved(paths.database.parent),
        "projects": _resolved(paths.projects),
        "backups": _resolved(paths.backups),
    }
    errors: list[str] = []
    writable: dict[str, bool] = {}

    for label, target in targets.items():
        try:
            if not target.exists() and not create_missing:
                raise FileNotFoundError("configured storage root does not exist")
            _probe_directory(target)
            writable[label] = True
        except Exception as exc:
            writable[label] = False
            errors.append(f"{label} writable/fsync probe failed: {type(exc).__name__}")

    sqlite_checked = False
    sqlite_integrity_ok = None
    db = _resolved(paths.database)
    if db.exists():
        sqlite_checked = True
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)
            try:
                integrity = con.execute("PRAGMA quick_check").fetchone()
                foreign_keys = con.execute("PRAGMA foreign_key_check").fetchone()
                sqlite_integrity_ok = bool(
                    integrity
                    and str(integrity[0]).lower() == "ok"
                    and foreign_keys is None
                )
            finally:
                con.close()
            if not sqlite_integrity_ok:
                errors.append("Configured SQLite database failed integrity/foreign-key checks.")
        except Exception as exc:
            sqlite_integrity_ok = False
            errors.append(f"Configured SQLite database probe failed: {type(exc).__name__}")

    return {
        "ok": not errors,
        "errors": errors,
        "details": {
            "writable_fsync": writable,
            "sqlite_checked": sqlite_checked,
            "sqlite_integrity_ok": sqlite_integrity_ok,
            "configured_paths_exposed": False,
        },
    }


def run_backup_restore_drill() -> dict:
    """Exercise the real backup/inspect/restore code against disposable state.

    This is safe for CI and operations because it never reads or mutates the configured live
    application paths. It proves that the current backup format can restore a SQLite row and a
    project binary byte-for-byte using the exact production BackupManager implementation.
    """

    with tempfile.TemporaryDirectory(prefix="aura-storage-drill-") as tmp:
        root = Path(tmp)
        source_db = root / "source-data" / "live_sound_studio.sqlite3"
        source_projects = root / "source-projects"
        archive_dir = root / "archives"
        source_db.parent.mkdir(parents=True, exist_ok=True)
        source_projects.mkdir(parents=True, exist_ok=True)

        con = sqlite3.connect(source_db)
        try:
            con.execute("CREATE TABLE restore_drill(id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
            con.execute("INSERT INTO restore_drill(id,payload) VALUES ('sentinel','durable-storage-ok')")
            con.commit()
        finally:
            con.close()

        project_file = source_projects / "member-a" / "project-a" / "sentinel.bin"
        project_file.parent.mkdir(parents=True, exist_ok=True)
        project_file.write_bytes(os.urandom(64 * 1024))
        expected_project_hash = _sha256(project_file)

        source = StudioBackupManager(
            database=source_db,
            projects=source_projects,
            backup_dir=archive_dir,
        )
        archive = root / "drill-backup.zip"
        created = source.create(output=archive, include_outputs=True, include_work=True)
        inspected = source.inspect(archive, verify_hashes=True)

        restored_db = root / "restored-data" / "live_sound_studio.sqlite3"
        restored_projects = root / "restored-projects"
        restored_backups = root / "restored-backups"
        target = StudioBackupManager(
            database=restored_db,
            projects=restored_projects,
            backup_dir=restored_backups,
        )
        restored = target.restore(
            archive,
            confirm_offline=True,
            preserve_existing=False,
        )

        con = sqlite3.connect(restored_db)
        try:
            row = con.execute(
                "SELECT payload FROM restore_drill WHERE id='sentinel'"
            ).fetchone()
            integrity = con.execute("PRAGMA integrity_check").fetchone()
        finally:
            con.close()
        restored_file = restored_projects / "member-a" / "project-a" / "sentinel.bin"
        project_ok = restored_file.is_file() and _sha256(restored_file) == expected_project_hash
        database_ok = bool(
            row
            and row[0] == "durable-storage-ok"
            and integrity
            and str(integrity[0]).lower() == "ok"
        )
        archive_ok = bool(created.get("sha256")) and inspected.get("hashes_verified") is True

        return {
            "ok": database_ok and project_ok and archive_ok and bool(restored.get("restored")),
            "database_restored": database_ok,
            "project_bytes_restored": project_ok,
            "archive_hashes_verified": archive_ok,
            "deployment_secrets_touched": False,
            "live_storage_touched": False,
        }


def build_storage_readiness_report(
    environ: Mapping[str, str] | None = None,
    *,
    live_probe: bool = False,
    create_missing: bool = False,
    restore_drill: bool = False,
) -> dict:
    env = environ or os.environ
    contract = validate_storage_contract(env)
    runtime = (
        probe_storage_runtime(env, create_missing=create_missing)
        if live_probe and contract["ok"]
        else None
    )
    drill = run_backup_restore_drill() if restore_drill else None
    ok = contract["ok"] and (runtime is None or runtime["ok"]) and (drill is None or drill["ok"])
    return {
        "ok": ok,
        "contract": contract,
        "runtime_probe": runtime,
        "restore_drill": drill,
        "secret_values_exposed": False,
        "configured_paths_exposed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify Command Center durable storage readiness")
    parser.add_argument("--live-probe", action="store_true", help="write/fsync probe configured storage roots")
    parser.add_argument("--create-missing", action="store_true", help="allow live probe to create missing roots")
    parser.add_argument("--restore-drill", action="store_true", help="run disposable backup/restore drill")
    args = parser.parse_args()
    report = build_storage_readiness_report(
        live_probe=args.live_probe,
        create_missing=args.create_missing,
        restore_drill=args.restore_drill,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
