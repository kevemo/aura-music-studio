from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .plans import BASIC_TIMELINE
from .professional_editor import EditorItem, ProfessionalEditorStore
from .professional_editor_renderer import EditorRenderError, ProfessionalEditorRenderer
from .tenant_storage import project_path

router = APIRouter(prefix="/creative", tags=["Professional Video Proxy"])

ProxyProfileName = Literal["edit_540p", "edit_720p"]
_SAFE_ITEM_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")
_VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}

# All codec/filter arguments are server-owned constants. Clients choose only a named profile;
# they can never submit FFmpeg arguments, filter graphs, codecs, output paths or shell text.
PROXY_PROFILES: dict[str, dict[str, str | int]] = {
    "edit_540p": {
        "height": 540,
        "video_codec": "libx264",
        "crf": 30,
        "preset": "veryfast",
        "audio_codec": "aac",
        "audio_bitrate": "96k",
    },
    "edit_720p": {
        "height": 720,
        "video_codec": "libx264",
        "crf": 28,
        "preset": "veryfast",
        "audio_codec": "aac",
        "audio_bitrate": "128k",
    },
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProxyRenderRequest(BaseModel):
    profile: ProxyProfileName = "edit_720p"


class VideoProxyError(RuntimeError):
    pass


class ProfessionalVideoProxyService:
    """Generate disposable editing proxies without changing source-quality render inputs.

    Proxies live below ``work/editor_proxies`` in the member's existing project. The original
    ``EditorItem.source_ref`` is never replaced; final Professional Editor exports therefore keep
    resolving the source-quality media. Only the proxy metadata is attached to the item and is
    undoable through the editor's existing operation history.
    """

    def __init__(self, project_dir: Path):
        self.project_dir = Path(project_dir).resolve()
        self.store = ProfessionalEditorStore(self.project_dir)
        if not self.store.exists():
            raise VideoProxyError("Professional editor is not initialized for this project")
        self.proxy_root = (self.project_dir / "work" / "editor_proxies").resolve()
        self.timeout_seconds = max(
            30.0,
            min(7200.0, float(os.getenv("AURA_EDITOR_PROXY_TIMEOUT_SECONDS", "1800"))),
        )

    def _item(self, item_id: str) -> EditorItem:
        if not _SAFE_ITEM_ID.fullmatch(str(item_id or "")):
            raise VideoProxyError("Invalid editor item id")
        try:
            item = self.store.load_item(item_id)
        except KeyError as exc:
            raise VideoProxyError("Editor item not found") from exc
        if item.kind != "video_clip":
            raise VideoProxyError("Only video clips can have editing proxies")
        return item

    def _source(self, item: EditorItem) -> Path:
        try:
            source = ProfessionalEditorRenderer(self.project_dir)._source(item.source_ref)
        except EditorRenderError as exc:
            raise VideoProxyError(str(exc)) from exc
        if source.suffix.lower() not in _VIDEO_SUFFIXES:
            raise VideoProxyError("Video proxy source must be a supported project video file")
        return source

    def _proxy_directory(self, item_id: str) -> Path:
        target = (self.proxy_root / item_id).resolve()
        if self.proxy_root not in target.parents:
            raise VideoProxyError("Proxy directory resolves outside the project")
        target.mkdir(parents=True, exist_ok=True)
        resolved = target.resolve()
        if self.proxy_root not in resolved.parents:
            raise VideoProxyError("Proxy directory resolves outside the project")
        return resolved

    def _proxy_key(self, source: Path, profile: ProxyProfileName) -> str:
        stat = source.stat()
        payload = "|".join(
            (
                str(source.relative_to(self.project_dir)),
                str(stat.st_size),
                str(stat.st_mtime_ns),
                profile,
            )
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]

    def _public_record(self, record: dict | None) -> dict:
        value = dict(record or {})
        # Records intentionally contain project-relative references only. Preserve that invariant
        # even if an older/corrupt sidecar somehow contains an unexpected private field.
        for key in list(value):
            if key.endswith("_path") or key in {"command", "ffmpeg", "stderr", "stdout"}:
                value.pop(key, None)
        return value

    def state(self, item_id: str) -> dict:
        item = self._item(item_id)
        record = item.metadata.get("proxy") if isinstance(item.metadata, dict) else None
        available = False
        if isinstance(record, dict):
            try:
                self.resolve_media(item_id)
                available = True
            except (FileNotFoundError, VideoProxyError):
                available = False
        return {
            "item_id": item.id,
            "source_ref": item.source_ref,
            "source_quality_preserved": True,
            "proxy": self._public_record(record if isinstance(record, dict) else None),
            "available": available,
        }

    def render(self, item_id: str, profile: ProxyProfileName, *, actor: str) -> dict:
        item = self._item(item_id)
        source = self._source(item)
        spec = PROXY_PROFILES[profile]
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise VideoProxyError("FFmpeg is unavailable on this deployment; proxy creation is disabled")

        directory = self._proxy_directory(item.id)
        key = self._proxy_key(source, profile)
        filename = f"{profile}_{key}.mp4"
        output = (directory / filename).resolve()
        if directory not in output.parents:
            raise VideoProxyError("Proxy output resolves outside the project")

        reused = output.is_file() and output.stat().st_size > 0
        if not reused:
            temporary = (directory / f".{filename}.part").resolve()
            if directory not in temporary.parents:
                raise VideoProxyError("Proxy temporary output resolves outside the project")
            temporary.unlink(missing_ok=True)
            height = int(spec["height"])
            # ``scale=-2:<height>`` is built exclusively from a server-owned integer profile.
            # No caller-controlled text enters the filter graph or any other FFmpeg option.
            argv = [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-y",
                "-i",
                str(source),
                "-map",
                "0:v:0",
                "-map",
                "0:a?",
                "-vf",
                f"scale=-2:{height}",
                "-c:v",
                str(spec["video_codec"]),
                "-preset",
                str(spec["preset"]),
                "-crf",
                str(spec["crf"]),
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                str(spec["audio_codec"]),
                "-b:a",
                str(spec["audio_bitrate"]),
                "-movflags",
                "+faststart",
                "-f",
                "mp4",
                str(temporary),
            ]
            try:
                completed = subprocess.run(
                    argv,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_seconds,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                temporary.unlink(missing_ok=True)
                raise VideoProxyError("Video proxy render exceeded the configured time limit") from exc
            if completed.returncode != 0 or not temporary.is_file() or temporary.stat().st_size <= 0:
                temporary.unlink(missing_ok=True)
                # Do not echo FFmpeg stderr because it can contain host/container path details.
                raise VideoProxyError("Video proxy render failed")
            temporary.replace(output)

        proxy_ref = output.relative_to(self.project_dir).as_posix()
        record = {
            "schema_version": 1,
            "profile": profile,
            "proxy_ref": proxy_ref,
            "source_ref": item.source_ref,
            "bytes": output.stat().st_size,
            "source_key": key,
            "created_at": _now(),
            "reused_existing": reused,
            "preview_only": True,
            "final_render_uses_source": True,
        }
        updated = self.store.patch_item(
            item.id,
            {"metadata": {"proxy": record}},
            actor=actor,
        )
        return {
            "item_id": item.id,
            "source_ref": updated.source_ref,
            "source_quality_preserved": updated.source_ref == item.source_ref,
            "proxy": self._public_record(record),
            "available": True,
        }

    def resolve_media(self, item_id: str) -> Path:
        item = self._item(item_id)
        record = item.metadata.get("proxy") if isinstance(item.metadata, dict) else None
        if not isinstance(record, dict):
            raise FileNotFoundError(item_id)
        proxy_ref = str(record.get("proxy_ref") or "").strip()
        if not proxy_ref or Path(proxy_ref).is_absolute():
            raise VideoProxyError("Proxy media reference is invalid")
        target = (self.project_dir / proxy_ref).resolve()
        if self.proxy_root not in target.parents:
            raise VideoProxyError("Proxy media resolves outside the project proxy directory")
        if target.suffix.lower() != ".mp4":
            raise VideoProxyError("Proxy media must be MP4")
        if not target.is_file():
            raise FileNotFoundError(item_id)
        return target

    def purge(self, item_id: str, *, actor: str) -> dict:
        item = self._item(item_id)
        record = item.metadata.get("proxy") if isinstance(item.metadata, dict) else None
        deleted = False
        if isinstance(record, dict):
            try:
                target = self.resolve_media(item_id)
            except FileNotFoundError:
                target = None
            if target is not None:
                target.unlink(missing_ok=True)
                deleted = True
        updated = self.store.patch_item(item.id, {"metadata": {"proxy": None}}, actor=actor)
        return {
            "item_id": item.id,
            "source_ref": updated.source_ref,
            "source_quality_preserved": updated.source_ref == item.source_ref,
            "proxy": None,
            "available": False,
            "proxy_file_deleted": deleted,
        }


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Membership context unavailable")
    if not member.plan.has(BASIC_TIMELINE):
        raise HTTPException(403, "Professional video proxies unlock on the Basic membership tier")
    return member


def _actor(member) -> str:
    user = getattr(member, "user", {}) or {}
    return str(user.get("display_name") or user.get("email") or "Studio Member")[:160]


def _project(project_name: str) -> Path:
    try:
        return project_path(project_name, must_exist=True)
    except ValueError as exc:
        raise HTTPException(400, "Invalid project path") from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, "Project not found") from exc


def _service(project_name: str) -> ProfessionalVideoProxyService:
    try:
        return ProfessionalVideoProxyService(_project(project_name))
    except VideoProxyError as exc:
        raise HTTPException(400, str(exc)) from exc


def _execute(callable_):
    try:
        return callable_()
    except FileNotFoundError as exc:
        raise HTTPException(404, "Video proxy not found") from exc
    except (OSError, ValueError, VideoProxyError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/projects/{project_name}/editor/items/{item_id}/proxy")
def video_proxy_state(project_name: str, item_id: str, request: Request):
    _member(request)
    return _execute(lambda: _service(project_name).state(item_id))


@router.post("/projects/{project_name}/editor/items/{item_id}/proxy/render")
def render_video_proxy(project_name: str, item_id: str, body: ProxyRenderRequest, request: Request):
    member = _member(request)
    return _execute(lambda: _service(project_name).render(item_id, body.profile, actor=_actor(member)))


@router.get("/projects/{project_name}/editor/items/{item_id}/proxy/media")
def video_proxy_media(project_name: str, item_id: str, request: Request):
    _member(request)
    path = _execute(lambda: _service(project_name).resolve_media(item_id))
    return FileResponse(
        path,
        media_type="video/mp4",
        filename=path.name,
        content_disposition_type="inline",
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "X-ESP-Editor-Proxy": "preview-only",
            "X-ESP-Final-Render-Uses": "source-media",
        },
    )


@router.delete("/projects/{project_name}/editor/items/{item_id}/proxy")
def purge_video_proxy(project_name: str, item_id: str, request: Request):
    member = _member(request)
    return _execute(lambda: _service(project_name).purge(item_id, actor=_actor(member)))


__all__ = [
    "PROXY_PROFILES",
    "ProfessionalVideoProxyService",
    "ProxyRenderRequest",
    "VideoProxyError",
    "router",
]
