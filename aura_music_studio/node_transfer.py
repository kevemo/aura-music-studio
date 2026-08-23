from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from .request_context import reset_current_user_id, set_current_user_id
from .tenant_storage import project_path


PROJECT_SKIP_PREFIXES = {
    "output/",
    "work/revisions/",
}
RESULT_ALLOWED_PREFIXES = (
    "output/",
    "work/",
)
RESULT_ALLOWED_ROOT_FILES = {
    "aura_session.json",
    "aura_status.json",
    "project.yaml",
    "project.yml",
    "project.json",
    "assets.json",
}


def _max_bytes() -> int:
    try:
        return max(64 * 1024 * 1024, int(os.getenv("LSS_NODE_MAX_BUNDLE_BYTES", str(2 * 1024**3))))
    except Exception:
        return 2 * 1024**3


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_member(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError("Unsafe archive path")
    return path


def _project_files(project: Path):
    for path in sorted(project.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        rel = path.relative_to(project).as_posix()
        if any(rel == prefix.rstrip("/") or rel.startswith(prefix) for prefix in PROJECT_SKIP_PREFIXES):
            continue
        # Runtime revision/rights ledgers remain on the coordinator; only files required to execute
        # a production/engineering job travel to the trusted node.
        if rel.startswith(".aura_rights/"):
            continue
        yield rel, path


def build_project_bundle(job: dict, destination: Path) -> dict:
    """Create a checksummed, tenant-scoped job bundle for a trusted ESP compute node."""
    context = set_current_user_id(job["user_id"])
    try:
        project = project_path(job["project_name"], must_exist=True)
        destination.parent.mkdir(parents=True, exist_ok=True)
        files = []
        total = 0
        with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
            for rel, source in _project_files(project):
                size = source.stat().st_size
                total += size
                if total > _max_bytes():
                    raise ValueError("Project bundle exceeds the configured ESP node transfer limit")
                digest = _sha256(source)
                archive.write(source, f"project/{rel}")
                files.append({"path": rel, "sha256": digest, "bytes": size})
            manifest = {
                "format": "esp-node-job-v1",
                "job": {
                    "id": job["id"],
                    "job_type": job["job_type"],
                    "project_name": job["project_name"],
                    "payload": json.loads(job.get("payload_json") or "{}"),
                },
                "files": files,
                "uncompressed_bytes": total,
            }
            archive.writestr("node_job.json", json.dumps(manifest, indent=2))
        return {"path": str(destination), "sha256": _sha256(destination), "bytes": destination.stat().st_size, "manifest": manifest}
    finally:
        reset_current_user_id(context)


def extract_project_bundle(bundle: Path, destination: Path) -> dict:
    """Safely extract and verify a coordinator-created project bundle on an ESP node."""
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(bundle, "r") as archive:
        names = archive.namelist()
        if "node_job.json" not in names:
            raise ValueError("ESP node bundle is missing its manifest")
        manifest = json.loads(archive.read("node_job.json"))
        if manifest.get("format") != "esp-node-job-v1":
            raise ValueError("Unsupported ESP node bundle format")
        expected = {item["path"]: item for item in manifest.get("files", []) if isinstance(item, dict) and item.get("path")}
        total = 0
        for name in names:
            if name == "node_job.json" or name.endswith("/"):
                continue
            member = _safe_member(name)
            if not str(member).startswith("project/"):
                raise ValueError("Unexpected file in ESP node bundle")
            rel = PurePosixPath(*member.parts[1:])
            if not rel.parts:
                raise ValueError("Invalid project file path")
            info = archive.getinfo(name)
            total += info.file_size
            if total > _max_bytes():
                raise ValueError("ESP node bundle exceeds configured extraction limit")
            target = destination.joinpath(*rel.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info, "r") as source, target.open("wb") as out:
                shutil.copyfileobj(source, out, length=1024 * 1024)
            item = expected.get(rel.as_posix())
            if not item or _sha256(target) != item.get("sha256"):
                raise ValueError(f"Project bundle checksum failed for {rel.as_posix()}")
        return manifest


def build_result_bundle(project: Path, job_id: str, destination: Path) -> dict:
    """Package only coordinator-approved mutable outputs from a completed node job."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    files = []
    total = 0
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
        candidates: list[tuple[str, Path]] = []
        for root_file in sorted(RESULT_ALLOWED_ROOT_FILES):
            source = project / root_file
            if source.is_file() and not source.is_symlink():
                candidates.append((root_file, source))
        for prefix in RESULT_ALLOWED_PREFIXES:
            root = project / prefix.rstrip("/")
            if not root.is_dir():
                continue
            for source in sorted(root.rglob("*")):
                if source.is_file() and not source.is_symlink():
                    rel = source.relative_to(project).as_posix()
                    # Never return revision snapshots from a worker.
                    if rel.startswith("work/revisions/"):
                        continue
                    candidates.append((rel, source))
        seen = set()
        for rel, source in candidates:
            if rel in seen:
                continue
            seen.add(rel)
            size = source.stat().st_size
            total += size
            if total > _max_bytes():
                raise ValueError("Node result bundle exceeds configured transfer limit")
            digest = _sha256(source)
            archive.write(source, f"result/{rel}")
            files.append({"path": rel, "sha256": digest, "bytes": size})
        manifest = {
            "format": "esp-node-result-v1",
            "job_id": job_id,
            "files": files,
            "uncompressed_bytes": total,
        }
        archive.writestr("node_result.json", json.dumps(manifest, indent=2))
    return {"path": str(destination), "sha256": _sha256(destination), "bytes": destination.stat().st_size, "manifest": manifest}


def apply_result_bundle(job: dict, bundle: Path) -> dict:
    """Verify and merge a trusted node result into the correct tenant project."""
    context = set_current_user_id(job["user_id"])
    staging_dir: Path | None = None
    try:
        project = project_path(job["project_name"], must_exist=True)
        staging_dir = Path(tempfile.mkdtemp(prefix="lss-node-result-"))
        with zipfile.ZipFile(bundle, "r") as archive:
            names = archive.namelist()
            if "node_result.json" not in names:
                raise ValueError("Node result is missing its manifest")
            manifest = json.loads(archive.read("node_result.json"))
            if manifest.get("format") != "esp-node-result-v1" or manifest.get("job_id") != job["id"]:
                raise ValueError("Node result does not match the leased job")
            expected = {item["path"]: item for item in manifest.get("files", []) if isinstance(item, dict) and item.get("path")}
            total = 0
            extracted: list[tuple[str, Path]] = []
            for name in names:
                if name == "node_result.json" or name.endswith("/"):
                    continue
                member = _safe_member(name)
                if not str(member).startswith("result/"):
                    raise ValueError("Unexpected file in node result")
                rel = PurePosixPath(*member.parts[1:])
                rel_text = rel.as_posix()
                allowed = rel_text in RESULT_ALLOWED_ROOT_FILES or any(rel_text.startswith(prefix) for prefix in RESULT_ALLOWED_PREFIXES)
                if not allowed or rel_text.startswith("work/revisions/"):
                    raise ValueError(f"Node result attempted to modify a protected path: {rel_text}")
                info = archive.getinfo(name)
                total += info.file_size
                if total > _max_bytes():
                    raise ValueError("Node result exceeds configured extraction limit")
                target = staging_dir.joinpath(*rel.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info, "r") as source, target.open("wb") as out:
                    shutil.copyfileobj(source, out, length=1024 * 1024)
                item = expected.get(rel_text)
                if not item or _sha256(target) != item.get("sha256"):
                    raise ValueError(f"Node result checksum failed for {rel_text}")
                extracted.append((rel_text, target))

        merged = []
        for rel, source in extracted:
            target = project.joinpath(*PurePosixPath(rel).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            merged.append(rel)
        return {"job_id": job["id"], "merged_files": merged, "merged_count": len(merged)}
    finally:
        if staging_dir:
            shutil.rmtree(staging_dir, ignore_errors=True)
        reset_current_user_id(context)
