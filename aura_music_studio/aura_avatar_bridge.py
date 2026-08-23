from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/aura/avatar", tags=["Aura Embodied Bridge"])


class AuraAvatarBridgeError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AuraAvatarBridge:
    """User-bound command bridge between Aura's AI tools and the browser avatar runtime.

    The LLM never receives permission to run JavaScript or arbitrary selectors. The browser
    advertises a bounded set of visible controls with opaque IDs; Aura can only target one of
    those IDs, which the server resolves back to the registered selector.
    """

    ALLOWED_ACTIONS = {"guide_to", "present", "celebrate", "minimize", "restore", "listen", "think"}

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = str(db_path or os.getenv("LSS_DB_PATH") or "data/live_sound_studio.sqlite3")
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self):
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _init_schema(self) -> None:
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS aura_avatar_page_context (
                    user_id TEXT PRIMARY KEY,
                    path TEXT NOT NULL,
                    title TEXT NOT NULL,
                    controls_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS aura_avatar_commands (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued',
                    created_at TEXT NOT NULL,
                    consumed_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_aura_avatar_commands_user_status
                    ON aura_avatar_commands(user_id,status,created_at);
                """
            )

    def register_page(self, user_id: str, *, path: str, title: str, controls: list[dict[str, str]]) -> dict:
        clean: list[dict[str, str]] = []
        seen: set[str] = set()
        for item in controls[:80]:
            control_id = str(item.get("id") or "").strip()[:80]
            label = str(item.get("label") or "").strip()[:180]
            selector = str(item.get("selector") or "").strip()[:240]
            kind = str(item.get("kind") or "control").strip()[:40]
            if not control_id or not label or not selector or control_id in seen:
                continue
            # Runtime-generated selectors must use our opaque data attribute only.
            expected_prefix = '[data-aura-control-id="'
            if not selector.startswith(expected_prefix) or not selector.endswith('"]'):
                continue
            seen.add(control_id)
            clean.append({"id": control_id, "label": label, "selector": selector, "kind": kind})
        now = _now()
        with self._connect() as con:
            con.execute(
                """INSERT INTO aura_avatar_page_context(user_id,path,title,controls_json,updated_at)
                   VALUES (?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET
                   path=excluded.path,title=excluded.title,controls_json=excluded.controls_json,updated_at=excluded.updated_at""",
                (user_id, (path or "/")[:300], (title or "Live Sound Studio")[:240], json.dumps(clean), now),
            )
        return {"path": path, "title": title, "controls": clean, "updated_at": now}

    def page_context(self, user_id: str) -> dict:
        with self._connect() as con:
            row = con.execute("SELECT * FROM aura_avatar_page_context WHERE user_id=?", (user_id,)).fetchone()
        if not row:
            return {"path": None, "title": None, "controls": [], "updated_at": None}
        item = dict(row)
        try:
            item["controls"] = json.loads(item.pop("controls_json") or "[]")
        except Exception:
            item["controls"] = []
            item.pop("controls_json", None)
        return item

    def enqueue(
        self,
        user_id: str,
        *,
        action: str,
        control_id: str | None = None,
        message: str = "",
        speak: bool = True,
    ) -> dict:
        action = (action or "").strip().lower()
        if action not in self.ALLOWED_ACTIONS:
            raise AuraAvatarBridgeError(f"Unsupported Aura avatar action: {action}")

        context = self.page_context(user_id)
        selector = None
        label = None
        if action == "guide_to":
            if not control_id:
                raise AuraAvatarBridgeError("guide_to requires a control_id from get_current_interface")
            match = next((item for item in context.get("controls", []) if item.get("id") == control_id), None)
            if not match:
                raise AuraAvatarBridgeError("That interface control is no longer available on the user's current screen")
            selector = match["selector"]
            label = match["label"]

        command_id = uuid4().hex
        payload = {
            "control_id": control_id,
            "selector": selector,
            "label": label,
            "message": (message or "")[:4000],
            "speak": bool(speak),
            "page_path": context.get("path"),
        }
        with self._connect() as con:
            con.execute(
                "INSERT INTO aura_avatar_commands(id,user_id,action,payload_json,status,created_at) VALUES (?,?,?,?,?,?)",
                (command_id, user_id, action, json.dumps(payload), "queued", _now()),
            )
        return {"command_id": command_id, "action": action, "queued": True, "target_label": label}

    def consume_next(self, user_id: str) -> dict | None:
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                "SELECT * FROM aura_avatar_commands WHERE user_id=? AND status='queued' ORDER BY created_at ASC LIMIT 1",
                (user_id,),
            ).fetchone()
            if not row:
                con.commit()
                return None
            consumed = _now()
            con.execute(
                "UPDATE aura_avatar_commands SET status='consumed',consumed_at=? WHERE id=? AND user_id=? AND status='queued'",
                (consumed, row["id"], user_id),
            )
            con.commit()
        item = dict(row)
        try:
            item["payload"] = json.loads(item.pop("payload_json") or "{}")
        except Exception:
            item["payload"] = {}
            item.pop("payload_json", None)
        item["status"] = "consumed"
        item["consumed_at"] = consumed
        return item


bridge = AuraAvatarBridge()


class PageControl(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=180)
    selector: str = Field(min_length=1, max_length=240)
    kind: str = Field(default="control", max_length=40)


class PageContextBody(BaseModel):
    path: str = Field(default="/", max_length=300)
    title: str = Field(default="Live Sound Studio", max_length=240)
    controls: list[PageControl] = Field(default_factory=list, max_length=80)


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if not member:
        raise HTTPException(401, "Sign in required")
    return member


@router.post("/page-context")
def register_page_context(body: PageContextBody, request: Request):
    member = _member(request)
    return bridge.register_page(
        member.user_id,
        path=body.path,
        title=body.title,
        controls=[item.model_dump() for item in body.controls],
    )


@router.get("/page-context")
def get_page_context(request: Request):
    member = _member(request)
    return bridge.page_context(member.user_id)


@router.get("/commands/next")
def next_avatar_command(request: Request):
    member = _member(request)
    return {"command": bridge.consume_next(member.user_id)}
