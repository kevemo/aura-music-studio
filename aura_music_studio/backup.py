from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from . import __version__

BACKUP_FORMAT = 1
BUFFER_SIZE = 1024 * 1024


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(BUFFER_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_archive_name(name: str) -> str:
    normalized = name.replace("\\", "/")
    pure = PurePosixPath(normalized)
    if not normalized or pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise ValueError(f"Unsafe backup archive path: {name!r}")
    return str(pure)


def _zipinfo_is_symlink(info: zipfile.ZipInfo) -> bool:
    # On Unix-created ZIPs the top file-type bits of external_attr contain the POSIX mode.
    mode = (info.external_attr >> 16) & 0o170000
    return mode == 0o120000


def _project_root() -> Path:
    return Path(os.getenv("AURA_PROJECTS_ROOT", "projects")).resolve()


def _db_path() -> Path:
    return Path(os.getenv("LSS_DB_PATH", "data/live_sound_studio.sqlite3")).resolve()


def _backup_root() -> Path:
    return Path(os.getenv("LSS_BACKUP_DIR", "backups")).resolve()


def _stream_into_zip(zf: zipfile.ZipFile, source: Path, archive_name: str) -> dict:
    archive_name = _safe_archive_name(archive_name)
    digest = hashlib.sha256()
    size = 0
    info = zipfile.ZipInfo(archive_name, date_time=datetime.now().timetuple()[:6])
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100600 << 16
    with source.open("rb") as src, zf.open(info, "w", force_zip64=True) as dst:
        while True:
            chunk = src.read(BUFFER_SIZE)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
            dst.write(chunk)
    return {"path": archive_name, "sha256": digest.hexdigest(), "bytes": size}


def _iter_project_files(root: Path, *, include_outputs: bool, include_work: bool):
    if not root.exists():
        return
    for current, dirnames, filenames in os.walk(root, followlinks=False):
        current_path = Path(current)
        # Never follow or archive symlinked directories. Cache/temp folders may be reconstructed.
        keep_dirs: list[str] = []
        for dirname in dirnames:
            path = current_path / dirname
            rel = path.relative_to(root)
            if path.is_symlink():
                continue
            if not include_outputs and "output" in rel.parts:
                continue
            if not include_work and "work" in rel.parts:
                continue
            if dirname in {"__pycache__", ".pytest_cache"}:
                continue
            keep_dirs.append(dirname)
        dirnames[:] = keep_dirs
        for filename in filenames:
            path = current_path / filename
            if path.is_symlink() or not path.is_file():
                continue
            rel = path.relative_to(root)
            if not include_outputs and "output" in rel.parts:
                continue
            if not include_work and "work" in rel.parts:
                continue
            yield path, rel


def _sqlite_snapshot(source: Path, destination: Path) -> None:
    if not source.is_file():
        raise FileNotFoundError(f"Studio database not found: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=30)
    dst = sqlite3.connect(destination)
    try:
        src.backup(dst)
        result = dst.execute("PRAGMA integrity_check").fetchone()
        if not result or str(result[0]).lower() != "ok":
            raise RuntimeError(f"SQLite backup integrity check failed: {result}")
    finally:
        dst.close()
        src.close()


@dataclass
class BackupManifest:
    format_version: int = BACKUP_FORMAT
    product: str = "ESP Live Sound Studio"
    studio_version: str = __version__
    created_at: str = field(default_factory=_now)
    database_path: str = "database/live_sound_studio.sqlite3"
    files: list[dict] = field(default_factory=list)
    project_file_count: int = 0
    include_outputs: bool = True
    include_work: bool = True
    secrets_included: bool = False
    environment_included: bool = False
    encrypted: bool = False
    notes: list[str] = field(default_factory=list)


class StudioBackupManager:
    def __init__(
        self,
        *,
        database: Path | None = None,
        projects: Path | None = None,
        backup_dir: Path | None = None,
    ):
        self.database = (database or _db_path()).resolve()
        self.projects = (projects or _project_root()).resolve()
        self.backup_dir = (backup_dir or _backup_root()).resolve()
        self.backup_dir.mkdir(parents=True, exist_ok=True)

    def create(
        self,
        *,
        output: Path | None = None,
        include_outputs: bool = True,
        include_work: bool = True,
        age_recipient: str | None = None,
        keep_plain_when_encrypted: bool = False,
    ) -> dict:
        """Create a portable Studio-state archive without exporting deployment secrets.

        SQLite is copied through sqlite3.backup(), so the database image is transactionally
        consistent. Project files are streamed once into the archive; for a fully quiescent cross-file
        snapshot, ESP should pause new renders/uploads while the backup is being made.
        """
        output = (output or self.backup_dir / f"ESP_Live_Sound_Studio_{_stamp()}.zip").resolve()
        if output.suffix.lower() != ".zip":
            raise ValueError("Plain backup output must use .zip")
        output.parent.mkdir(parents=True, exist_ok=True)
        if output.exists():
            raise FileExistsError(output)

        manifest = BackupManifest(include_outputs=include_outputs, include_work=include_work)
        manifest.notes.append("Deployment .env and provider/API/payment/email secrets are deliberately excluded.")
        manifest.notes.append("SQLite database copied with sqlite3 backup API.")

        with tempfile.TemporaryDirectory(prefix="lss-backup-") as tmp:
            db_snapshot = Path(tmp) / "live_sound_studio.sqlite3"
            _sqlite_snapshot(self.database, db_snapshot)
            with zipfile.ZipFile(output, "w", allowZip64=True) as zf:
                manifest.files.append(_stream_into_zip(zf, db_snapshot, manifest.database_path))

                for source, rel in _iter_project_files(
                    self.projects,
                    include_outputs=include_outputs,
                    include_work=include_work,
                ) or []:
                    archive_name = f"projects/{rel.as_posix()}"
                    manifest.files.append(_stream_into_zip(zf, source, archive_name))
                    manifest.project_file_count += 1

                manifest_bytes = json.dumps(asdict(manifest), indent=2).encode("utf-8")
                info = zipfile.ZipInfo("backup-manifest.json", date_time=datetime.now().timetuple()[:6])
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100600 << 16
                zf.writestr(info, manifest_bytes)

        result = {
            "backup": str(output),
            "sha256": _sha256_file(output),
            "bytes": output.stat().st_size,
            "manifest": asdict(manifest),
            "encrypted_backup": None,
        }

        recipient = (age_recipient or "").strip()
        if recipient:
            age = shutil.which("age")
            if not age:
                raise RuntimeError("age encryption was requested but the `age` command is not installed")
            encrypted = output.with_suffix(output.suffix + ".age")
            subprocess.run([age, "-r", recipient, "-o", str(encrypted), str(output)], check=True, timeout=3600)
            result["encrypted_backup"] = str(encrypted)
            result["encrypted_sha256"] = _sha256_file(encrypted)
            manifest.encrypted = True
            if not keep_plain_when_encrypted:
                output.unlink(missing_ok=True)
                result["backup"] = None
        return result

    @staticmethod
    def _open_archive(path: Path, *, age_identity: Path | None = None):
        if path.suffix.lower() != ".age":
            return None, path
        if not age_identity:
            raise ValueError("Encrypted .age backup requires an age identity file")
        age = shutil.which("age")
        if not age:
            raise RuntimeError("`age` is required to decrypt this backup")
        tmp = tempfile.TemporaryDirectory(prefix="lss-restore-age-")
        decrypted = Path(tmp.name) / "backup.zip"
        subprocess.run(
            [age, "-d", "-i", str(age_identity), "-o", str(decrypted), str(path)],
            check=True,
            timeout=3600,
        )
        return tmp, decrypted

    @classmethod
    def inspect(cls, archive: Path, *, age_identity: Path | None = None, verify_hashes: bool = True) -> dict:
        archive = archive.resolve()
        if not archive.is_file():
            raise FileNotFoundError(archive)
        tmp, readable = cls._open_archive(archive, age_identity=age_identity)
        try:
            with zipfile.ZipFile(readable, "r") as zf:
                infos = {info.filename: info for info in zf.infolist()}
                if "backup-manifest.json" not in infos:
                    raise ValueError("Backup manifest is missing")
                for info in infos.values():
                    _safe_archive_name(info.filename)
                    if _zipinfo_is_symlink(info):
                        raise ValueError(f"Backup contains forbidden symlink: {info.filename}")
                manifest = json.loads(zf.read("backup-manifest.json"))
                if manifest.get("format_version") != BACKUP_FORMAT:
                    raise ValueError(f"Unsupported backup format: {manifest.get('format_version')}")
                if manifest.get("secrets_included") or manifest.get("environment_included"):
                    raise ValueError("Backup unexpectedly claims to contain deployment secrets/environment")

                verified = 0
                if verify_hashes:
                    for item in manifest.get("files", []):
                        name = _safe_archive_name(str(item.get("path") or ""))
                        if name not in infos:
                            raise ValueError(f"Backup file listed in manifest is missing: {name}")
                        digest = hashlib.sha256()
                        size = 0
                        with zf.open(name, "r") as handle:
                            while True:
                                chunk = handle.read(BUFFER_SIZE)
                                if not chunk:
                                    break
                                digest.update(chunk)
                                size += len(chunk)
                        if digest.hexdigest() != item.get("sha256") or size != int(item.get("bytes", -1)):
                            raise ValueError(f"Backup checksum/size verification failed for {name}")
                        verified += 1
                return {
                    "archive": str(archive),
                    "manifest": manifest,
                    "verified_files": verified,
                    "hashes_verified": verify_hashes,
                }
        finally:
            if tmp:
                tmp.cleanup()

    def restore(
        self,
        archive: Path,
        *,
        confirm_offline: bool,
        age_identity: Path | None = None,
        preserve_existing: bool = True,
    ) -> dict:
        """Restore a verified backup while the Studio is offline.

        This method intentionally refuses to restore without an explicit offline confirmation because
        replacing a database/projects tree under live web/worker processes can corrupt active state.
        Deployment secrets remain untouched because they are not in the archive.
        """
        if not confirm_offline:
            raise PermissionError("Restore refused: stop the Studio/web/worker services and explicitly confirm offline restore")
        archive = archive.resolve()
        inspection = self.inspect(archive, age_identity=age_identity, verify_hashes=True)
        tmp_age, readable = self._open_archive(archive, age_identity=age_identity)
        restore_tmp = tempfile.TemporaryDirectory(prefix="lss-restore-")
        try:
            staging = Path(restore_tmp.name)
            with zipfile.ZipFile(readable, "r") as zf:
                for info in zf.infolist():
                    if info.filename == "backup-manifest.json":
                        continue
                    name = _safe_archive_name(info.filename)
                    if _zipinfo_is_symlink(info):
                        raise ValueError(f"Backup contains forbidden symlink: {name}")
                    target = (staging / Path(*PurePosixPath(name).parts)).resolve()
                    if staging.resolve() not in target.parents:
                        raise ValueError(f"Backup attempted path traversal: {name}")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(info, "r") as src, target.open("wb") as dst:
                        shutil.copyfileobj(src, dst, length=BUFFER_SIZE)

            staged_db = staging / "database" / "live_sound_studio.sqlite3"
            if not staged_db.is_file():
                raise ValueError("Backup database is missing")
            con = sqlite3.connect(staged_db)
            try:
                result = con.execute("PRAGMA integrity_check").fetchone()
                if not result or str(result[0]).lower() != "ok":
                    raise ValueError(f"Restored SQLite image failed integrity check: {result}")
            finally:
                con.close()

            staged_projects = staging / "projects"
            stamp = _stamp()
            preserved: list[str] = []

            self.database.parent.mkdir(parents=True, exist_ok=True)
            if self.database.exists():
                if preserve_existing:
                    old_db = self.database.with_name(self.database.name + f".pre_restore_{stamp}")
                    self.database.replace(old_db)
                    preserved.append(str(old_db))
                else:
                    self.database.unlink()
            shutil.copy2(staged_db, self.database)

            self.projects.parent.mkdir(parents=True, exist_ok=True)
            if self.projects.exists():
                if preserve_existing:
                    old_projects = self.projects.with_name(self.projects.name + f".pre_restore_{stamp}")
                    self.projects.replace(old_projects)
                    preserved.append(str(old_projects))
                else:
                    shutil.rmtree(self.projects)
            if staged_projects.exists():
                shutil.copytree(staged_projects, self.projects, symlinks=False)
            else:
                self.projects.mkdir(parents=True, exist_ok=True)

            return {
                "restored": True,
                "archive": str(archive),
                "database": str(self.database),
                "projects": str(self.projects),
                "preserved_previous_state": preserved,
                "deployment_secrets_changed": False,
                "verified_files": inspection["verified_files"],
            }
        finally:
            restore_tmp.cleanup()
            if tmp_age:
                tmp_age.cleanup()
