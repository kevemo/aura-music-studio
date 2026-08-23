from __future__ import annotations

import mimetypes
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from .plans import FLAC_DOWNLOAD, MP3_DOWNLOAD, STEM_DOWNLOAD, WAV_DOWNLOAD
from .tenant_storage import project_path

router = APIRouter(tags=["Studio Outputs"])

AUDIO_EXTS = {".mp3", ".wav", ".flac", ".ogg", ".m4a"}


def _project(name: str) -> Path:
    try:
        return project_path(name, must_exist=True)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, "Project not found") from exc


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if not member:
        raise HTTPException(401, "Sign in required")
    return member


def _required_download_feature(relative_path: str) -> str:
    lower = relative_path.lower()
    suffix = Path(lower).suffix
    stem_like = any(token in lower for token in ("stem", "stems/", "bandlab", "separated", "harmonies/", "voices/"))
    if stem_like:
        return STEM_DOWNLOAD
    if suffix == ".mp3":
        return MP3_DOWNLOAD
    if suffix == ".wav":
        return WAV_DOWNLOAD
    if suffix == ".flac":
        return FLAC_DOWNLOAD
    return STEM_DOWNLOAD


def _resolve_output(project: Path, relative_path: str) -> Path:
    output_root = (project / "output").resolve()
    target = (output_root / relative_path).resolve()
    if output_root not in target.parents or not target.is_file():
        raise HTTPException(404, "Output file not found")
    return target


def _allowed_output(project: Path, relative_path: str, request: Request) -> tuple[Path, str]:
    member = _member(request)
    needed = _required_download_feature(relative_path)
    if not member.plan.has(needed):
        raise HTTPException(403, "This output requires a higher Live Sound Studio membership tier")
    return _resolve_output(project, relative_path), needed


@router.get("/projects/{project_name}/outputs")
def list_outputs(project_name: str, request: Request):
    project = _project(project_name)
    member = _member(request)
    output = project / "output"
    if not output.exists():
        return []
    records = []
    for path in sorted(p for p in output.rglob("*") if p.is_file()):
        rel = path.relative_to(output).as_posix()
        needed = _required_download_feature(rel)
        allowed = member.plan.has(needed)
        audio = path.suffix.lower() in AUDIO_EXTS
        encoded_project = quote(project_name, safe="")
        encoded_rel = quote(rel, safe="/")
        records.append(
            {
                "name": path.name,
                "path": rel,
                "bytes": path.stat().st_size,
                "content_type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                "audio": audio,
                "download_allowed": allowed,
                "download_url": (
                    f"/projects/{encoded_project}/outputs/file/{encoded_rel}" if allowed else None
                ),
                "stream_url": (
                    f"/projects/{encoded_project}/outputs/stream/{encoded_rel}" if allowed and audio else None
                ),
            }
        )
    return records


@router.get("/projects/{project_name}/outputs/file/{relative_path:path}")
def download_output(project_name: str, relative_path: str, request: Request):
    project = _project(project_name)
    target, _ = _allowed_output(project, relative_path, request)
    return FileResponse(
        target,
        media_type=mimetypes.guess_type(target.name)[0] or "application/octet-stream",
        filename=target.name,
    )


@router.get("/projects/{project_name}/outputs/stream/{relative_path:path}")
def stream_output(project_name: str, relative_path: str, request: Request):
    project = _project(project_name)
    target, _ = _allowed_output(project, relative_path, request)
    if target.suffix.lower() not in AUDIO_EXTS:
        raise HTTPException(400, "Only audio outputs can be streamed inline")
    return FileResponse(
        target,
        media_type=mimetypes.guess_type(target.name)[0] or "audio/wav",
        headers={"Content-Disposition": "inline"},
    )
