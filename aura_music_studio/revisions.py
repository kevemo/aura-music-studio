from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

TRACKED_ROOT_FILES = ("project.yaml", "project.yml", "project.json", "aura_session.json")
TRACKED_WORK_FILES = (
    "build_around_last.json",
    "style_dna.json",
    "arrangement.json",
    "analysis.json",
    "production_plan.json",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def revision_root(project: Path) -> Path:
    root = project / "work" / "revisions"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _tracked_files(project: Path) -> list[tuple[str, Path]]:
    rows: list[tuple[str, Path]] = []
    for name in TRACKED_ROOT_FILES:
        path = project / name
        if path.is_file():
            rows.append((name, path))
    for name in TRACKED_WORK_FILES:
        path = project / "work" / name
        if path.is_file():
            rows.append((f"work/{name}", path))
    return rows


def create_revision(project: Path, *, label: str, reason: str = "manual", actor: str = "Aura", keep: int = 100) -> dict:
    """Snapshot project/session metadata without duplicating audio.

    Audio remains immutable in input/work/output locations and is referenced by the session. Revisions
    copy only small manifests/session/plan files, which keeps undo history inexpensive even for large songs.
    """
    project = project.resolve()
    rid = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:8]
    root = revision_root(project)
    dest = root / rid
    dest.mkdir(parents=True, exist_ok=False)

    files = []
    for rel, source in _tracked_files(project):
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        files.append({"path": rel, "sha256": _sha256(source), "bytes": source.stat().st_size})

    manifest = {
        "id": rid,
        "created_at": _now(),
        "label": (label or "Revision")[:160],
        "reason": (reason or "manual")[:80],
        "actor": (actor or "Aura")[:120],
        "files": files,
        "audio_copied": False,
        "project": project.name,
    }
    (dest / "revision.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    prune_revisions(project, keep=max(1, keep))
    return manifest


def list_revisions(project: Path) -> list[dict]:
    rows: list[dict] = []
    for folder in sorted(revision_root(project).iterdir(), reverse=True):
        if not folder.is_dir():
            continue
        meta = folder / "revision.json"
        if not meta.is_file():
            continue
        try:
            item = json.loads(meta.read_text(encoding="utf-8"))
            if isinstance(item, dict):
                rows.append(item)
        except Exception:
            continue
    return rows


def get_revision(project: Path, revision_id: str) -> tuple[Path, dict]:
    if not revision_id or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for ch in revision_id):
        raise ValueError("Invalid revision id")
    root = revision_root(project).resolve()
    folder = (root / revision_id).resolve()
    if root not in folder.parents or not folder.is_dir():
        raise FileNotFoundError(revision_id)
    meta = folder / "revision.json"
    if not meta.is_file():
        raise FileNotFoundError(revision_id)
    value = json.loads(meta.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Invalid revision manifest")
    return folder, value


def restore_revision(project: Path, revision_id: str, *, create_backup: bool = True, keep: int = 100) -> dict:
    project = project.resolve()
    folder, manifest = get_revision(project, revision_id)
    if create_backup:
        create_revision(project, label=f"Before restore {revision_id}", reason="pre_restore", keep=keep)

    restored = []
    for item in manifest.get("files", []):
        rel = item.get("path")
        if not isinstance(rel, str) or not rel:
            continue
        source = (folder / rel).resolve()
        if folder not in source.parents or not source.is_file():
            continue
        target = (project / rel).resolve()
        if project not in target.parents:
            raise ValueError("Revision attempted to restore outside project")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        restored.append(rel)
    return {"restored_revision": revision_id, "restored_files": restored, "audio_restored": False}


def prune_revisions(project: Path, *, keep: int) -> int:
    root = revision_root(project)
    folders = sorted([p for p in root.iterdir() if p.is_dir()], reverse=True)
    removed = 0
    for folder in folders[max(1, keep):]:
        shutil.rmtree(folder, ignore_errors=True)
        removed += 1
    return removed
