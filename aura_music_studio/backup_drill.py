from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

from .backup import StudioBackupManager


def _sqlite_integrity(path: Path) -> str:
    con = sqlite3.connect(path)
    try:
        row = con.execute("PRAGMA integrity_check").fetchone()
    finally:
        con.close()
    return str(row[0]).lower() if row else "missing"


def run_backup_restore_drill(
    *,
    database: Path,
    projects: Path,
    working_dir: Path | None = None,
    include_outputs: bool = False,
    include_work: bool = True,
) -> dict:
    """Create, verify and restore a backup into an isolated drill destination.

    The configured live database/projects are read only. Restore always targets a fresh directory
    underneath ``working_dir`` (or a temporary directory) and therefore never replaces live state.
    Deployment secrets are not part of StudioBackupManager archives.
    """
    database = database.resolve()
    projects = projects.resolve()
    if not database.is_file():
        raise FileNotFoundError(database)
    if not projects.is_dir():
        raise FileNotFoundError(projects)

    temp = None
    if working_dir is None:
        temp = tempfile.TemporaryDirectory(prefix="aura-backup-drill-")
        root = Path(temp.name).resolve()
    else:
        root = working_dir.resolve()
        root.mkdir(parents=True, exist_ok=True)

    source_root = database.parent.resolve()
    if root == source_root or root in source_root.parents:
        if temp:
            temp.cleanup()
        raise ValueError("Backup drill working directory must not contain or replace the live database directory")
    if root == projects or root in projects.parents:
        if temp:
            temp.cleanup()
        raise ValueError("Backup drill working directory must not contain or replace the live project directory")

    backups = root / "archives"
    archive = backups / "restore-drill.zip"
    restored_db = root / "restored" / "data" / "live_sound_studio.sqlite3"
    restored_projects = root / "restored" / "projects"

    source = StudioBackupManager(database=database, projects=projects, backup_dir=backups)
    created = source.create(
        output=archive,
        include_outputs=include_outputs,
        include_work=include_work,
    )
    inspection = StudioBackupManager.inspect(archive, verify_hashes=True)

    destination = StudioBackupManager(
        database=restored_db,
        projects=restored_projects,
        backup_dir=root / "restored-backups",
    )
    restored = destination.restore(
        archive,
        confirm_offline=True,
        preserve_existing=False,
    )
    integrity = _sqlite_integrity(restored_db)
    if integrity != "ok":
        raise RuntimeError(f"Backup drill restored SQLite integrity check failed: {integrity}")

    report = {
        "ok": True,
        "archive_sha256": created["sha256"],
        "archive_bytes": created["bytes"],
        "verified_files": inspection["verified_files"],
        "restored": bool(restored["restored"]),
        "restored_database_integrity": integrity,
        "project_file_count": inspection["manifest"]["project_file_count"],
        "deployment_secrets_changed": restored["deployment_secrets_changed"],
        "source_state_replaced": False,
        "working_directory": str(root),
        "restored_database": str(restored_db),
        "restored_projects": str(restored_projects),
    }
    # Callers using an implicit temporary directory only need the validation result; do not return
    # paths that disappear immediately after this function exits.
    if temp:
        report["working_directory"] = None
        report["restored_database"] = None
        report["restored_projects"] = None
        temp.cleanup()
    return report


def main() -> int:
    import os

    database = Path(os.getenv("LSS_DB_PATH", "data/live_sound_studio.sqlite3"))
    projects = Path(os.getenv("AURA_PROJECTS_ROOT", "projects"))
    working = Path(os.getenv("AURA_BACKUP_DRILL_DIR", "data/backup_restore_drill"))
    report = run_backup_restore_drill(database=database, projects=projects, working_dir=working)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["run_backup_restore_drill"]
