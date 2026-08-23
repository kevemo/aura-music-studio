from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .aura_companion import AuraCompanionError
from .aura_persona import AURA_PERSONA_NAME, persona_context
from .aura_system_companion import AuraSystemCompanionService
from .localization import LocalePreferenceStore

router = APIRouter(prefix="/api/aura", tags=["Aura Workpage"])
service = AuraSystemCompanionService()
locale_store = LocalePreferenceStore(service.store.db_path)


class AuraChatBody(BaseModel):
    message: str = Field(min_length=1, max_length=64000)
    thread_id: str | None = None
    project_id: str | None = None
    attachment_ids: list[str] = Field(default_factory=list, max_length=12)
    execute_tools: bool = True
    workspace_mode: str = Field(default="auto", max_length=64)


class CreateThreadBody(BaseModel):
    title: str = Field(default="New Aura chat", min_length=1, max_length=200)
    project_id: str | None = None
    scope: str = Field(default="creative", max_length=80)


class MemoryBody(BaseModel):
    scope: str = Field(default="personal", max_length=80)
    key: str = Field(min_length=1, max_length=120)
    value: object


class AuraAttachmentStore:
    """Tenant-bound attachments for Aura's full chat/work page."""

    TEXT_EXTS = {".txt", ".md", ".csv", ".json", ".yaml", ".yml", ".xml", ".html", ".htm", ".py", ".js", ".ts", ".tsx", ".jsx", ".css", ".sql", ".log"}

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        self.root = Path(os.getenv("AURA_CHAT_UPLOAD_DIR", "data/aura_chat_uploads"))
        self.root.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self):
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _init_schema(self) -> None:
        with self._connect() as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS aura_chat_attachments (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    thread_id TEXT,
                    original_name TEXT NOT NULL,
                    mime_type TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    sha256 TEXT NOT NULL,
                    stored_path TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_aura_attachments_user_created ON aura_chat_attachments(user_id, created_at DESC)"
            )

    @staticmethod
    def _user_dir(user_id: str) -> str:
        return hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:24]

    @staticmethod
    def _safe_name(name: str) -> str:
        original = Path(name or "attachment").name
        stem = re.sub(r"[^A-Za-z0-9._ -]+", "_", original).strip(" ._") or "attachment"
        return stem[:180]

    async def save(self, user_id: str, upload: UploadFile, thread_id: str | None = None) -> dict:
        max_bytes = int(os.getenv("AURA_CHAT_MAX_UPLOAD_BYTES", str(64 * 1024 * 1024)))
        attachment_id = uuid4().hex
        original = self._safe_name(upload.filename or "attachment")
        suffix = Path(original).suffix[:16]
        target_dir = self.root / self._user_dir(user_id)
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{attachment_id}{suffix}"
        digest = hashlib.sha256()
        total = 0
        try:
            with target.open("wb") as handle:
                while chunk := await upload.read(1024 * 1024):
                    total += len(chunk)
                    if total > max_bytes:
                        raise ValueError(f"Attachment exceeds the {max_bytes // (1024*1024)} MB Aura upload limit")
                    digest.update(chunk)
                    handle.write(chunk)
        except Exception:
            target.unlink(missing_ok=True)
            raise
        mime = upload.content_type or mimetypes.guess_type(original)[0] or "application/octet-stream"
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as con:
            con.execute(
                """INSERT INTO aura_chat_attachments
                   (id,user_id,thread_id,original_name,mime_type,size_bytes,sha256,stored_path,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (attachment_id, user_id, thread_id, original, mime, total, digest.hexdigest(), str(target), now),
            )
        return self.get(user_id, attachment_id)

    def get(self, user_id: str, attachment_id: str) -> dict:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM aura_chat_attachments WHERE user_id=? AND id=?", (user_id, attachment_id)
            ).fetchone()
        if not row:
            raise KeyError(attachment_id)
        return dict(row)

    def list_for_user(self, user_id: str, limit: int = 50) -> list[dict]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT * FROM aura_chat_attachments WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
                (user_id, max(1, min(limit, 200))),
            ).fetchall()
        return [dict(row) for row in rows]

    def bind(self, user_id: str, ids: list[str], thread_id: str) -> list[dict]:
        result = []
        with self._connect() as con:
            for attachment_id in ids:
                row = con.execute(
                    "SELECT * FROM aura_chat_attachments WHERE user_id=? AND id=?", (user_id, attachment_id)
                ).fetchone()
                if not row:
                    raise KeyError(attachment_id)
                con.execute(
                    "UPDATE aura_chat_attachments SET thread_id=? WHERE user_id=? AND id=?",
                    (thread_id, user_id, attachment_id),
                )
                result.append(dict(row))
        return result

    def context(self, user_id: str, ids: list[str]) -> list[dict]:
        context: list[dict] = []
        max_text = int(os.getenv("AURA_CHAT_TEXT_ATTACHMENT_CHARS", "50000"))
        for attachment_id in ids:
            item = self.get(user_id, attachment_id)
            path = Path(item["stored_path"])
            if not path.is_file():
                raise FileNotFoundError(item["original_name"])
            entry = {
                "attachment_id": item["id"],
                "name": item["original_name"],
                "mime_type": item["mime_type"],
                "size_bytes": item["size_bytes"],
                "sha256": item["sha256"],
                "local_path": str(path),
            }
            if path.suffix.lower() in self.TEXT_EXTS or item["mime_type"].startswith("text/"):
                try:
                    entry["text_excerpt"] = path.read_text(encoding="utf-8", errors="replace")[:max_text]
                except Exception:
                    pass
            context.append(entry)
        return context


attachments = AuraAttachmentStore(service.store.db_path)


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if not member:
        raise HTTPException(401, "Sign in required")
    return member


def _public_attachment(item: dict) -> dict:
    return {
        "id": item["id"],
        "thread_id": item.get("thread_id"),
        "name": item["original_name"],
        "mime_type": item["mime_type"],
        "size_bytes": item["size_bytes"],
        "sha256": item["sha256"],
        "created_at": item["created_at"],
    }


@router.get("/capabilities")
def aura_capabilities(request: Request):
    member = _member(request)
    locale = locale_store.get_user_locale(member.user_id) or "en"
    return {
        **service.capabilities(member),
        "persona": AURA_PERSONA_NAME,
        "workspace": {
            "full_chat": True,
            "persistent_history": True,
            "attachments": True,
            "text_file_context": True,
            "image_audio_video_attachment_storage": True,
            "voice_input": True,
            "live_translation": True,
            "generation_result_cards": True,
            "role_aware_tool_drawer": True,
            "project_context": True,
            "memory": True,
            "system_workflow_tools": True,
        },
        "locale": locale,
    }


@router.get("/threads")
def list_threads(request: Request, limit: int = 80):
    member = _member(request)
    return {"threads": service.store.list_threads(member.user_id, limit=limit)}


@router.post("/threads")
def create_thread(body: CreateThreadBody, request: Request):
    member = _member(request)
    access = service.access_profile(member)
    allowed_scopes = set(access["scopes"])
    scope = body.scope if body.scope in allowed_scopes else "creative"
    return service.store.create_thread(member.user_id, title=body.title, project_id=body.project_id, scope=scope)


@router.get("/threads/{thread_id}/messages")
def thread_messages(thread_id: str, request: Request, limit: int = 120):
    member = _member(request)
    try:
        thread = service.store.get_thread(member.user_id, thread_id)
        messages = service.store.messages(member.user_id, thread_id, limit=limit)
    except AuraCompanionError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"thread": thread, "messages": messages}


@router.get("/memory")
def aura_memory(request: Request, limit: int = 100):
    member = _member(request)
    return {"memories": service.store.memories(member.user_id, limit=limit)}


@router.post("/memory")
def save_memory(body: MemoryBody, request: Request):
    member = _member(request)
    try:
        return service.store.set_memory(member.user_id, body.scope, body.key, body.value)
    except AuraCompanionError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/attachments")
async def upload_attachment(
    request: Request,
    file: UploadFile = File(...),
    thread_id: str = Form(""),
):
    member = _member(request)
    if thread_id:
        try:
            service.store.get_thread(member.user_id, thread_id)
        except AuraCompanionError as exc:
            raise HTTPException(404, str(exc)) from exc
    try:
        item = await attachments.save(member.user_id, file, thread_id or None)
    except ValueError as exc:
        raise HTTPException(413, str(exc)) from exc
    return _public_attachment(item)


@router.get("/attachments")
def list_attachments(request: Request, limit: int = 50):
    member = _member(request)
    return {"attachments": [_public_attachment(x) for x in attachments.list_for_user(member.user_id, limit)]}


@router.get("/attachments/{attachment_id}/download")
def download_attachment(attachment_id: str, request: Request):
    member = _member(request)
    try:
        item = attachments.get(member.user_id, attachment_id)
    except KeyError as exc:
        raise HTTPException(404, "Aura attachment not found") from exc
    root = attachments.root.resolve()
    path = Path(item["stored_path"]).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise HTTPException(404, "Aura attachment file is unavailable")
    return FileResponse(path, media_type=item["mime_type"], filename=item["original_name"])


@router.post("/chat")
def aura_chat(body: AuraChatBody, request: Request):
    member = _member(request)
    attachment_context = []
    if body.attachment_ids:
        try:
            attachment_context = attachments.context(member.user_id, body.attachment_ids)
        except KeyError as exc:
            raise HTTPException(404, f"Aura attachment not found: {exc}") from exc
        except FileNotFoundError as exc:
            raise HTTPException(410, f"Aura attachment has expired: {exc}") from exc

    locale = locale_store.get_user_locale(member.user_id) or "en"
    context = {
        "workspace": "Aura full chat workpage",
        "aura_persona": persona_context(locale, body.workspace_mode),
        "workspace_mode": body.workspace_mode,
        "response_locale": locale,
        "attachments": attachment_context,
        "attachment_note": (
            "Text attachment excerpts are available directly. Image/audio/video attachments are securely stored and identified here; "
            "only claim visual/audio/video analysis when a configured multimodal analysis path actually processed the asset."
        ),
    }
    try:
        result = service.chat(
            member,
            message=body.message,
            thread_id=body.thread_id,
            project_id=body.project_id,
            project_context=context,
            execute_tools=body.execute_tools,
        )
    except AuraCompanionError as exc:
        raise HTTPException(422, str(exc)) from exc

    thread_id = result["thread"]["id"]
    if body.attachment_ids:
        try:
            attachments.bind(member.user_id, body.attachment_ids, thread_id)
        except KeyError:
            pass
    result["attachments"] = [_public_attachment(attachments.get(member.user_id, x)) for x in body.attachment_ids]
    result["locale"] = locale
    result["persona"] = AURA_PERSONA_NAME
    return result
