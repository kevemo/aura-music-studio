from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

from .creative_project import CreativeManifest

_URI_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:($|[\\/])")


def normalize_project_source_ref(project_dir: Path, source_ref: str | None) -> str | None:
    """Return a canonical project-relative editor media reference.

    Editor timelines are persisted and may later be consumed by render workers, so they must
    never carry caller-selected URLs or host filesystem paths. The referenced file is not
    required to exist yet because queued/generated media can be bound before rendering
    completes; confinement is enforced against the resolved project namespace instead.
    """
    if source_ref is None:
        return None

    value = source_ref.strip()
    if not value:
        return None
    if "\x00" in value:
        raise ValueError("Editor source_ref contains an invalid NUL byte")
    if _URI_SCHEME.match(value):
        raise ValueError("Editor source_ref must be project-relative, not a URL or URI")
    if _WINDOWS_DRIVE.match(value):
        raise ValueError("Editor source_ref must not contain a Windows drive path")
    if value.startswith(("/", "\\", "//")):
        raise ValueError("Editor source_ref must not be an absolute or UNC path")

    # Canonicalize separators so persisted editor state is portable across self-hosted OSes.
    normalized = value.replace("\\", "/")
    parts = PurePosixPath(normalized).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        if ".." in parts:
            raise ValueError("Editor source_ref must not traverse outside the project")
        normalized = PurePosixPath(normalized).as_posix()
        parts = PurePosixPath(normalized).parts
    if ".." in parts:
        raise ValueError("Editor source_ref must not traverse outside the project")

    root = Path(project_dir).resolve()
    candidate = (root / Path(*parts)).resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("Editor source_ref resolves outside the project") from exc

    return PurePosixPath(*parts).as_posix()


def normalized_manifest_for_editor(project_dir: Path, manifest: CreativeManifest) -> CreativeManifest:
    """Validate every element source before any editor timeline mutation occurs."""
    normalized = manifest.model_copy(deep=True)
    for element in normalized.elements:
        element.source_ref = normalize_project_source_ref(project_dir, element.source_ref)
    return normalized


__all__ = ["normalize_project_source_ref", "normalized_manifest_for_editor"]
