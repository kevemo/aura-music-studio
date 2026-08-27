from __future__ import annotations

import os
import re
from pathlib import Path

from .request_context import current_user_id

ROOT = Path(os.getenv("AURA_PROJECTS_ROOT", "projects")).resolve()
ROOT.mkdir(parents=True, exist_ok=True)

_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")
_SYSTEM_PROJECT_DIRS = {"members", "_games", "_public_games"}


def safe_project_name(name: str) -> str:
    value = (name or "").strip()
    if not _SAFE_NAME.fullmatch(value) or value in {".", ".."}:
        raise ValueError("Invalid project name")
    return value


def projects_root() -> Path:
    """Return the private project root for the current authenticated member.

    CLI/background jobs without a web request keep using the repository-level projects root.
    Public product API requests receive a ContextVar from MembershipAccessMiddleware and are
    transparently isolated under `projects/members/<user_id>/`.
    """
    user_id = current_user_id()
    if not user_id:
        ROOT.mkdir(parents=True, exist_ok=True)
        return ROOT
    target = ROOT / "members" / user_id
    target.mkdir(parents=True, exist_ok=True)
    return target


def project_path(name: str, *, must_exist: bool = True) -> Path:
    root = projects_root().resolve()
    target = (root / safe_project_name(name)).resolve()
    if root not in target.parents:
        raise ValueError("Invalid project path")
    if must_exist and (not target.exists() or not target.is_dir()):
        raise FileNotFoundError(name)
    return target


def list_project_dirs() -> list[Path]:
    root = projects_root()
    return sorted(
        [p for p in root.iterdir() if p.is_dir() and p.name not in _SYSTEM_PROJECT_DIRS],
        key=lambda p: p.name.lower(),
    )
