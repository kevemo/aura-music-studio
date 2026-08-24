from __future__ import annotations

import re
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import aura_agent_core as core
from . import aura_agent_tools as tools
from .aura_agent_core import AuraModelClient
from .aura_chat_store import AuraChatStore

router = APIRouter(tags=["Aura Artifacts"])
store = AuraChatStore()
_INSTALLED = False
_MAX_ARTIFACTS = 200
_MAX_CONTENT = 200_000
_KINDS = {"document", "markdown", "code", "json", "yaml", "csv", "text", "prompt", "lyrics"}


class ArtifactCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    kind: str = Field(default="document", max_length=40)
    language: str = Field(default="", max_length=80)
    content: str = Field(default="", max_length=_MAX_CONTENT)


class ArtifactPatchRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=160)
    language: str | None = Field(default=None, max_length=80)
    content: str | None = Field(default=None, max_length=_MAX_CONTENT)
    note: str = Field(default="Manual edit", max_length=500)


class ArtifactRestoreRequest(BaseModel):
    version: int = Field(ge=1)


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Sign in required")
    return member


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _kind(value: str) -> str:
    clean = (value or "document").strip().lower()
    if clean not in _KINDS:
        raise ValueError("Artifact kind must be document, markdown, code, json, yaml, csv, text, prompt or lyrics")
    return clean


class AuraArtifactStore:
    def __init__(self, chat_store: AuraChatStore | None = None):
        self.chat_store = chat_store or store
        self.ensure_schema()

    def ensure_schema(self) -> None:
        with self.chat_store._connect() as con:
            con.execute(
                """CREATE TABLE IF NOT EXISTS aura_artifacts (
                       id TEXT PRIMARY KEY,
                       user_id TEXT NOT NULL,
                       thread_id TEXT NOT NULL,
                       title TEXT NOT NULL,
                       kind TEXT NOT NULL,
                       language TEXT NOT NULL DEFAULT '',
                       content TEXT NOT NULL DEFAULT '',
                       current_version INTEGER NOT NULL DEFAULT 1,
                       created_at TEXT NOT NULL,
                       updated_at TEXT NOT NULL
                   )"""
            )
            con.execute("CREATE INDEX IF NOT EXISTS idx_aura_artifacts_thread ON aura_artifacts(user_id,thread_id,updated_at DESC)")
            con.execute(
                """CREATE TABLE IF NOT EXISTS aura_artifact_versions (
                       artifact_id TEXT NOT NULL,
                       user_id TEXT NOT NULL,
                       version INTEGER NOT NULL,
                       content TEXT NOT NULL,
                       note TEXT NOT NULL DEFAULT '',
                       created_at TEXT NOT NULL,
                       PRIMARY KEY(artifact_id,version)
                   )"""
            )

    @staticmethod
    def _public(row, *, include_content: bool = True) -> dict:
        value = {
            "id": row["id"],
            "title": row["title"],
            "kind": row["kind"],
            "language": row["language"],
            "current_version": int(row["current_version"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "characters": len(row["content"] or ""),
            "code_execution_enabled": False,
        }
        if include_content:
            value["content"] = row["content"]
        return value

    def _owned_thread(self, user_id: str, thread_id: str) -> None:
        if not self.chat_store.thread(user_id, thread_id):
            raise KeyError(thread_id)

    def list(self, user_id: str, thread_id: str) -> list[dict]:
        self._owned_thread(user_id, thread_id)
        with self.chat_store._connect() as con:
            rows = con.execute(
                "SELECT * FROM aura_artifacts WHERE user_id=? AND thread_id=? ORDER BY updated_at DESC",
                (user_id, thread_id),
            ).fetchall()
        return [self._public(row, include_content=False) for row in rows]

    def get(self, user_id: str, thread_id: str, artifact_id: str) -> dict | None:
        self._owned_thread(user_id, thread_id)
        with self.chat_store._connect() as con:
            row = con.execute(
                "SELECT * FROM aura_artifacts WHERE id=? AND user_id=? AND thread_id=?",
                (artifact_id, user_id, thread_id),
            ).fetchone()
        return self._public(row) if row else None

    def create(self, user_id: str, thread_id: str, *, title: str, kind: str, language: str = "", content: str = "", note: str = "Initial version") -> dict:
        self._owned_thread(user_id, thread_id)
        if len(self.list(user_id, thread_id)) >= _MAX_ARTIFACTS:
            raise ValueError(f"Aura Artifacts are limited to {_MAX_ARTIFACTS} per conversation")
        clean_title = " ".join(title.split())[:160]
        clean_content = str(content or "")[:_MAX_CONTENT]
        if not clean_title:
            raise ValueError("Artifact title is required")
        item_id, now = uuid4().hex, _now()
        with self.chat_store._connect() as con:
            con.execute(
                """INSERT INTO aura_artifacts(id,user_id,thread_id,title,kind,language,content,current_version,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (item_id, user_id, thread_id, clean_title, _kind(kind), language.strip()[:80], clean_content, 1, now, now),
            )
            con.execute(
                """INSERT INTO aura_artifact_versions(artifact_id,user_id,version,content,note,created_at)
                   VALUES (?,?,?,?,?,?)""",
                (item_id, user_id, 1, clean_content, note[:500], now),
            )
        return self.get(user_id, thread_id, item_id) or {}

    def update(self, user_id: str, thread_id: str, artifact_id: str, *, content: str | None = None, title: str | None = None, language: str | None = None, note: str = "Aura revision") -> dict:
        current = self.get(user_id, thread_id, artifact_id)
        if not current:
            raise KeyError(artifact_id)
        next_content = current["content"] if content is None else str(content)[:_MAX_CONTENT]
        next_title = current["title"] if title is None else " ".join(title.split())[:160]
        next_language = current["language"] if language is None else str(language).strip()[:80]
        if not next_title:
            raise ValueError("Artifact title is required")
        version = int(current["current_version"]) + 1
        now = _now()
        with self.chat_store._connect() as con:
            con.execute(
                """UPDATE aura_artifacts SET title=?,language=?,content=?,current_version=?,updated_at=?
                   WHERE id=? AND user_id=? AND thread_id=?""",
                (next_title, next_language, next_content, version, now, artifact_id, user_id, thread_id),
            )
            con.execute(
                """INSERT INTO aura_artifact_versions(artifact_id,user_id,version,content,note,created_at)
                   VALUES (?,?,?,?,?,?)""",
                (artifact_id, user_id, version, next_content, note[:500], now),
            )
        return self.get(user_id, thread_id, artifact_id) or {}

    def versions(self, user_id: str, thread_id: str, artifact_id: str) -> list[dict]:
        if not self.get(user_id, thread_id, artifact_id):
            raise KeyError(artifact_id)
        with self.chat_store._connect() as con:
            rows = con.execute(
                """SELECT version,note,created_at,length(content) AS characters
                   FROM aura_artifact_versions WHERE artifact_id=? AND user_id=? ORDER BY version DESC""",
                (artifact_id, user_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def restore(self, user_id: str, thread_id: str, artifact_id: str, version: int) -> dict:
        current = self.get(user_id, thread_id, artifact_id)
        if not current:
            raise KeyError(artifact_id)
        with self.chat_store._connect() as con:
            row = con.execute(
                "SELECT content FROM aura_artifact_versions WHERE artifact_id=? AND user_id=? AND version=?",
                (artifact_id, user_id, int(version)),
            ).fetchone()
        if not row:
            raise KeyError(version)
        return self.update(
            user_id,
            thread_id,
            artifact_id,
            content=row["content"],
            note=f"Restored version {version}",
        )

    def delete(self, user_id: str, thread_id: str, artifact_id: str) -> bool:
        if not self.get(user_id, thread_id, artifact_id):
            return False
        with self.chat_store._connect() as con:
            con.execute("DELETE FROM aura_artifact_versions WHERE artifact_id=? AND user_id=?", (artifact_id, user_id))
            con.execute("DELETE FROM aura_artifacts WHERE id=? AND user_id=? AND thread_id=?", (artifact_id, user_id, thread_id))
        return True


artifact_store = AuraArtifactStore(store)


def _artifact_system(kind: str, language: str) -> str:
    return (
        "Draft one high-quality Aura Artifact from the member's instruction. Return only the artifact content, no wrapper commentary. "
        f"Artifact kind: {kind}. Language/format hint: {language or 'natural/appropriate'}. "
        "Do not claim tools/actions happened. For code, write source only; this artifact system never executes code."
    )


def _draft(instruction: str, *, kind: str, language: str = "", existing: str | None = None) -> str:
    prompt = instruction.strip()[:20000]
    if existing is not None:
        prompt = "Existing artifact:\n" + existing[:120000] + "\n\nRevision instruction:\n" + prompt
    reply = AuraModelClient().complete(
        [
            {"role": "system", "content": _artifact_system(kind, language)},
            {"role": "user", "content": prompt},
        ],
        temperature=0.45,
    )
    return reply.text[:_MAX_CONTENT]


ARTIFACT_SPECS = [
    tools.ToolSpec(
        name="list_artifacts",
        description="List private versioned Aura Artifacts in the current conversation.",
        arguments={},
    ),
    tools.ToolSpec(
        name="read_artifact",
        description="Read one private Aura Artifact by id or unambiguous title.",
        arguments={"artifact": "Artifact id or title."},
    ),
    tools.ToolSpec(
        name="create_artifact",
        description="Create a private versioned document/code/prompt/lyrics artifact by drafting content from an instruction. Code is stored but never executed on the web host.",
        arguments={"title": "Artifact title.", "kind": "document|markdown|code|json|yaml|csv|text|prompt|lyrics", "language": "Optional code/format language.", "instruction": "What Aura should draft."},
        write=True,
    ),
    tools.ToolSpec(
        name="revise_artifact",
        description="Create a new version of an existing Aura Artifact from a revision instruction while preserving prior versions.",
        arguments={"artifact": "Artifact id or title.", "instruction": "Requested revision."},
        write=True,
    ),
]


def _select_artifact(rows: list[dict], selector: str) -> dict:
    clean = (selector or "").strip().lower()
    if not clean:
        if len(rows) == 1:
            return rows[0]
        raise ValueError("Specify an artifact id or title")
    exact = [row for row in rows if clean in {str(row["id"]).lower(), str(row["title"]).lower()}]
    if len(exact) == 1:
        return exact[0]
    partial = [row for row in rows if clean in str(row["title"]).lower()]
    if len(partial) == 1:
        return partial[0]
    if not partial:
        raise KeyError(f"No Aura Artifact matches {selector!r}")
    raise ValueError("Artifact selector is ambiguous: " + ", ".join(row["title"] for row in partial[:12]))


def _explicit_artifact_write(name: str, text: str) -> bool:
    lower = (text or "").lower()
    if name == "create_artifact":
        action = any(word in lower for word in ("create", "make", "write", "draft", "save"))
        target = any(word in lower for word in ("artifact", "document", "doc", "file", "code", "script", "prompt", "lyrics", "plan", "report"))
        return action and target
    if name == "revise_artifact":
        return any(word in lower for word in ("revise", "update", "edit", "rewrite", "change")) and "artifact" in lower
    return True


def _artifact_related(text: str) -> bool:
    lower = (text or "").lower()
    return "artifact" in lower or any(phrase in lower for phrase in ("save this as a document", "create a document", "write a code file", "create a code file"))


def install_aura_artifacts() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    for spec in ARTIFACT_SPECS:
        if spec.name not in {item.name for item in tools.TOOL_SPECS}:
            tools.TOOL_SPECS.append(spec)
            tools._SPEC_BY_NAME[spec.name] = spec

    original_execute = tools.AuraToolRegistry.execute
    original_needs = core._needs_model_tool_router

    def execute(self, call: tools.ToolCall, *, latest_user_message: str):
        names = {spec.name for spec in ARTIFACT_SPECS}
        if call.name not in names:
            return original_execute(self, call, latest_user_message=latest_user_message)
        if not self.tools_enabled:
            raise PermissionError("Aura tools are disabled for this conversation")
        from .aura_runtime_context import current_turn
        turn = current_turn()
        if turn is None or turn.user_id != self.member.user_id:
            raise RuntimeError("Current Aura conversation context is unavailable")
        if call.name in {"create_artifact", "revise_artifact"} and not _explicit_artifact_write(call.name, latest_user_message):
            raise PermissionError("Aura Artifact writes require explicit wording in the member's latest message")
        rows = artifact_store.list(self.member.user_id, turn.thread_id)
        args = dict(call.arguments or {})
        if call.name == "list_artifacts":
            return {"artifacts": rows, "code_execution_enabled": False}
        if call.name == "read_artifact":
            selected = _select_artifact(rows, str(args.get("artifact") or ""))
            return artifact_store.get(self.member.user_id, turn.thread_id, selected["id"])
        if call.name == "create_artifact":
            title = str(args.get("title") or "Aura Artifact").strip()[:160]
            kind = _kind(str(args.get("kind") or "document"))
            language = str(args.get("language") or "").strip()[:80]
            instruction = str(args.get("instruction") or latest_user_message).strip()
            content = _draft(instruction, kind=kind, language=language)
            return artifact_store.create(
                self.member.user_id,
                turn.thread_id,
                title=title,
                kind=kind,
                language=language,
                content=content,
                note="Created by Aura",
            )
        selected = _select_artifact(rows, str(args.get("artifact") or ""))
        current = artifact_store.get(self.member.user_id, turn.thread_id, selected["id"])
        instruction = str(args.get("instruction") or latest_user_message).strip()
        content = _draft(instruction, kind=current["kind"], language=current["language"], existing=current["content"])
        return artifact_store.update(
            self.member.user_id,
            turn.thread_id,
            selected["id"],
            content=content,
            note="Revised by Aura: " + " ".join(instruction.split())[:300],
        )

    def needs_model_tool_router(text: str, pinned_project: str | None, tools_enabled: bool, web_enabled: bool) -> bool:
        if tools_enabled and _artifact_related(text):
            return True
        return original_needs(text, pinned_project, tools_enabled, web_enabled)

    tools.AuraToolRegistry.execute = execute
    core._needs_model_tool_router = needs_model_tool_router
    _INSTALLED = True


@router.get("/aura-intelligence/api/threads/{thread_id}/artifacts")
def list_artifacts(thread_id: str, request: Request):
    member = _member(request)
    try:
        return artifact_store.list(member.user_id, thread_id)
    except KeyError as exc:
        raise HTTPException(404, "Aura conversation not found") from exc


@router.post("/aura-intelligence/api/threads/{thread_id}/artifacts")
def create_artifact(thread_id: str, body: ArtifactCreateRequest, request: Request):
    member = _member(request)
    try:
        return artifact_store.create(member.user_id, thread_id, title=body.title, kind=body.kind, language=body.language, content=body.content)
    except KeyError as exc:
        raise HTTPException(404, "Aura conversation not found") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/aura-intelligence/api/threads/{thread_id}/artifacts/{artifact_id}")
def get_artifact(thread_id: str, artifact_id: str, request: Request):
    member = _member(request)
    try:
        item = artifact_store.get(member.user_id, thread_id, artifact_id)
    except KeyError as exc:
        raise HTTPException(404, "Aura conversation not found") from exc
    if not item:
        raise HTTPException(404, "Aura Artifact not found")
    return item


@router.patch("/aura-intelligence/api/threads/{thread_id}/artifacts/{artifact_id}")
def patch_artifact(thread_id: str, artifact_id: str, body: ArtifactPatchRequest, request: Request):
    member = _member(request)
    try:
        return artifact_store.update(
            member.user_id,
            thread_id,
            artifact_id,
            content=body.content,
            title=body.title,
            language=body.language,
            note=body.note,
        )
    except KeyError as exc:
        raise HTTPException(404, "Aura Artifact not found") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/aura-intelligence/api/threads/{thread_id}/artifacts/{artifact_id}/versions")
def artifact_versions(thread_id: str, artifact_id: str, request: Request):
    member = _member(request)
    try:
        return artifact_store.versions(member.user_id, thread_id, artifact_id)
    except KeyError as exc:
        raise HTTPException(404, "Aura Artifact not found") from exc


@router.post("/aura-intelligence/api/threads/{thread_id}/artifacts/{artifact_id}/restore")
def restore_artifact(thread_id: str, artifact_id: str, body: ArtifactRestoreRequest, request: Request):
    member = _member(request)
    try:
        return artifact_store.restore(member.user_id, thread_id, artifact_id, body.version)
    except KeyError as exc:
        raise HTTPException(404, "Aura Artifact/version not found") from exc


@router.delete("/aura-intelligence/api/threads/{thread_id}/artifacts/{artifact_id}")
def delete_artifact(thread_id: str, artifact_id: str, request: Request):
    member = _member(request)
    try:
        deleted = artifact_store.delete(member.user_id, thread_id, artifact_id)
    except KeyError as exc:
        raise HTTPException(404, "Aura conversation not found") from exc
    if not deleted:
        raise HTTPException(404, "Aura Artifact not found")
    return {"deleted": True, "artifact_id": artifact_id}


__all__ = ["router", "AuraArtifactStore", "artifact_store", "install_aura_artifacts"]
