from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

TRACKED_ROOT_FILES = (
    "project.yaml",
    "project.yml",
    "project.json",
    "aura_session.json",
    "song_dna.json",
    "creative_manifest.json",
)
TRACKED_WORK_FILES = (
    "build_around_last.json",
    "style_dna.json",
    "arrangement.json",
    "analysis.json",
    "production_plan.json",
    # Shared non-destructive image/video edit graph. Including this small metadata file makes
    # project checkpoints coherent across DAW + Video + Image without copying media binaries.
    "pro_editor.json",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
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


def _domain_for_path(path: str) -> str:
    if path == "aura_session.json":
        return "music_daw"
    if path == "song_dna.json":
        return "music_song_dna"
    if path == "creative_manifest.json":
        return "creative_manifest"
    if path == "work/pro_editor.json":
        return "professional_image_video_editor"
    if path in {"project.yaml", "project.yml", "project.json"}:
        return "project_core"
    return "production_metadata"


def _domain_summary(files: list[dict]) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for item in files:
        domain = _domain_for_path(str(item.get("path") or ""))
        entry = result.setdefault(domain, {"files": 0, "bytes": 0, "paths": []})
        entry["files"] += 1
        entry["bytes"] += int(item.get("bytes") or 0)
        entry["paths"].append(item.get("path"))
    return result


def create_revision(project: Path, *, label: str, reason: str = "manual", actor: str = "Aura", keep: int = 100) -> dict:
    """Snapshot DAW + Song DNA + creative-editor metadata without duplicating media.

    Audio, video and image binaries remain immutable in input/work/output locations and are
    referenced by project metadata. A revision captures the DAW session, Song DNA, Creative
    Manifest, professional image/video edit graph and production-plan metadata together, keeping
    deep cross-editor undo/version history inexpensive even for large projects.
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

    domains = _domain_summary(files)
    manifest = {
        "id": rid,
        "created_at": _now(),
        "label": (label or "Revision")[:160],
        "reason": (reason or "manual")[:80],
        "actor": (actor or "Aura")[:120],
        "files": files,
        "domains": domains,
        "cross_editor_checkpoint": True,
        "daw_included": "music_daw" in domains,
        "song_dna_included": "music_song_dna" in domains,
        "creative_manifest_included": "creative_manifest" in domains,
        "professional_editor_included": "professional_image_video_editor" in domains,
        "audio_copied": False,
        "media_copied": False,
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
                # Compatibility: older manifests had no domain summary.
                if "domains" not in item:
                    item["domains"] = _domain_summary(list(item.get("files") or []))
                item.setdefault("cross_editor_checkpoint", "work/pro_editor.json" in {x.get("path") for x in item.get("files", []) if isinstance(x, dict)})
                item.setdefault("song_dna_included", "song_dna.json" in {x.get("path") for x in item.get("files", []) if isinstance(x, dict)})
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
    if "domains" not in value:
        value["domains"] = _domain_summary(list(value.get("files") or []))
    return folder, value


def compare_revisions(project: Path, left_revision_id: str, right_revision_id: str) -> dict:
    """Compare checkpoint metadata without reading or duplicating source media."""
    _left_folder, left = get_revision(project, left_revision_id)
    _right_folder, right = get_revision(project, right_revision_id)
    left_files = {str(item.get("path")): item for item in left.get("files", []) if isinstance(item, dict) and item.get("path")}
    right_files = {str(item.get("path")): item for item in right.get("files", []) if isinstance(item, dict) and item.get("path")}

    added = sorted(set(right_files) - set(left_files))
    removed = sorted(set(left_files) - set(right_files))
    changed = sorted(
        path for path in set(left_files) & set(right_files)
        if left_files[path].get("sha256") != right_files[path].get("sha256")
    )
    unchanged = sorted(set(left_files) & set(right_files) - set(changed))
    touched = set(added) | set(removed) | set(changed)
    domains_changed = sorted({_domain_for_path(path) for path in touched})
    return {
        "left": {"id": left.get("id"), "label": left.get("label"), "created_at": left.get("created_at")},
        "right": {"id": right.get("id"), "label": right.get("label"), "created_at": right.get("created_at")},
        "files": {"added": added, "removed": removed, "changed": changed, "unchanged": unchanged},
        "domains_changed": domains_changed,
        "daw_changed": "music_daw" in domains_changed,
        "song_dna_changed": "music_song_dna" in domains_changed,
        "professional_editor_changed": "professional_image_video_editor" in domains_changed,
        "creative_manifest_changed": "creative_manifest" in domains_changed,
        "media_files_compared": False,
        "media_files_duplicated": False,
        "restorable_as_project_checkpoint": True,
    }


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
    restored_domains = sorted({_domain_for_path(path) for path in restored})
    return {
        "restored_revision": revision_id,
        "restored_files": restored,
        "restored_domains": restored_domains,
        "cross_editor_restore": bool({"professional_image_video_editor", "music_daw", "music_song_dna"} & set(restored_domains)),
        "song_dna_restored": "music_song_dna" in restored_domains,
        "audio_restored": False,
        "media_restored": False,
        "source_media_mutated": False,
    }


def prune_revisions(project: Path, *, keep: int) -> int:
    root = revision_root(project)
    folders = sorted([path for path in root.iterdir() if path.is_dir()], reverse=True)
    removed = 0
    for folder in folders[max(1, keep):]:
        shutil.rmtree(folder, ignore_errors=True)
        removed += 1
    return removed


__all__ = [
    "TRACKED_ROOT_FILES",
    "TRACKED_WORK_FILES",
    "compare_revisions",
    "create_revision",
    "get_revision",
    "list_revisions",
    "prune_revisions",
    "restore_revision",
    "revision_root",
]
