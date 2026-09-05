from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from html import escape
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .audit import AuditLedger
from .esp_niche import require_esp_hub_member
from .esp_support_center import _is_owner, support

router = APIRouter(tags=["ESP Support Conversations"])

MessageVisibility = Literal["user_visible", "internal"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SupportMessageCreate(BaseModel):
    body: str = Field(min_length=1, max_length=6000)
    visibility: MessageVisibility = "user_visible"


class SupportConversationStore:
    """Conversation extension for the existing durable ESP SupportCaseStore.

    It deliberately stores internal notes separately from user-visible replies. The member
    read path filters internal notes in SQL; private data is not fetched and filtered in the browser.
    """

    def __init__(self):
        self.db_path = support.db_path
        self.audit = AuditLedger(support.esp.accounts)
        self._init_schema()

    def _connect(self):
        con = sqlite3.connect(self.db_path, timeout=30)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _init_schema(self) -> None:
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS esp_support_messages (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL,
                    author_user_id TEXT NOT NULL,
                    author_role TEXT NOT NULL,
                    visibility TEXT NOT NULL CHECK(visibility IN ('user_visible','internal')),
                    body TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(case_id) REFERENCES esp_support_cases(id) ON DELETE CASCADE,
                    FOREIGN KEY(author_user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_chat9_support_messages_case
                    ON esp_support_messages(case_id,visibility,created_at);
                """
            )

    def list_messages(self, case_id: str, *, user_id: str, owner: bool) -> list[dict]:
        # Authorise against the existing case boundary before reading message rows.
        support.get(case_id, user_id=user_id, owner=owner)
        with self._connect() as con:
            if owner:
                rows = con.execute(
                    "SELECT * FROM esp_support_messages WHERE case_id=? ORDER BY created_at,id",
                    (case_id,),
                ).fetchall()
            else:
                rows = con.execute(
                    """SELECT * FROM esp_support_messages
                       WHERE case_id=? AND visibility='user_visible' ORDER BY created_at,id""",
                    (case_id,),
                ).fetchall()
        return [dict(row) for row in rows]

    def add_message(
        self,
        case_id: str,
        *,
        author_user_id: str,
        owner: bool,
        visibility: str,
        body: str,
    ) -> dict:
        # Existing store enforces member/Owner case privacy.
        support.get(case_id, user_id=author_user_id, owner=owner)
        if visibility not in {"user_visible", "internal"}:
            raise ValueError("Unsupported support message visibility")
        if not owner and visibility != "user_visible":
            raise PermissionError("Members cannot create internal support notes")
        clean = (body or "").strip()[:6000]
        if not clean:
            raise ValueError("Support reply cannot be empty")
        message_id = uuid4().hex
        role = "owner" if owner else "member"
        now = _now()
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            con.execute(
                """INSERT INTO esp_support_messages
                   (id,case_id,author_user_id,author_role,visibility,body,created_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (message_id, case_id, author_user_id, role, visibility, clean, now),
            )
            con.execute("UPDATE esp_support_cases SET updated_at=? WHERE id=?", (now, case_id))
            con.execute(
                """INSERT INTO esp_support_activity(id,case_id,actor,action,metadata_json,created_at)
                   VALUES (?,?,?,?,?,?)""",
                (
                    uuid4().hex,
                    case_id,
                    author_user_id[:120],
                    "support_message_added" if visibility == "user_visible" else "internal_note_added",
                    '{"content_in_activity":false,"visibility":"' + visibility + '"}',
                    now,
                ),
            )
        self.audit.append(
            actor=author_user_id,
            action="chat9.support_message_added" if visibility == "user_visible" else "chat9.support_internal_note_added",
            subject_user_id=None,
            details={"case_id": case_id, "message_id": message_id, "visibility": visibility, "content_logged": False},
        )
        return {
            "id": message_id,
            "case_id": case_id,
            "author_user_id": author_user_id,
            "author_role": role,
            "visibility": visibility,
            "body": clean,
            "created_at": now,
        }


conversations = SupportConversationStore()


def _actor(request: Request):
    member, membership = require_esp_hub_member(request)
    return member, membership, _is_owner(membership)


@router.get("/command-center/api/support/cases/{case_id}/messages")
def list_case_messages(case_id: str, request: Request):
    member, _membership, owner = _actor(request)
    try:
        return {
            "messages": conversations.list_messages(case_id, user_id=member.user_id, owner=owner),
            "internal_notes_included": owner,
        }
    except KeyError as exc:
        raise HTTPException(404, "Support case not found") from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


@router.post("/command-center/api/support/cases/{case_id}/messages")
def add_case_message(case_id: str, body: SupportMessageCreate, request: Request):
    member, _membership, owner = _actor(request)
    try:
        message = conversations.add_message(
            case_id,
            author_user_id=member.user_id,
            owner=owner,
            visibility=body.visibility,
            body=body.body,
        )
    except KeyError as exc:
        raise HTTPException(404, "Support case not found") from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"message": message}


@router.get("/command-center/support/cases/{case_id}", response_class=HTMLResponse, include_in_schema=False)
def support_conversation_page(case_id: str, request: Request):
    member, _membership, owner = _actor(request)
    try:
        case = support.get(case_id, user_id=member.user_id, owner=owner)
        messages = conversations.list_messages(case_id, user_id=member.user_id, owner=owner)
    except KeyError as exc:
        raise HTTPException(404, "Support case not found") from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc

    messages_html = "".join(
        f"<article class='msg {'internal' if row['visibility']=='internal' else ''}'>"
        f"<div><b>{'Internal note' if row['visibility']=='internal' else escape(row['author_role'].title())}</b> "
        f"<span class='muted'>{escape(row['created_at'][:16])}</span></div>"
        f"<p>{escape(row['body']).replace(chr(10), '<br>')}</p></article>"
        for row in messages
    ) or "<p class='muted'>No replies yet.</p>"
    internal_option = "<option value='internal'>Internal Owner note — hidden from member</option>" if owner else ""
    return HTMLResponse(
        f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
        <title>Support case {escape(case_id[:8])}</title><style>
        :root{{color-scheme:dark;--line:#493657;--gold:#efc86f;--muted:#c7bed0}}*{{box-sizing:border-box}}body{{margin:0;background:#09060e;color:#fff;font-family:Inter,system-ui,sans-serif}}main{{max-width:900px;margin:auto;padding:24px}}.card,.msg{{border:1px solid var(--line);border-radius:14px;padding:16px;margin:12px 0;background:#17101e}}.internal{{border-style:dashed;background:#241624}}.muted{{color:var(--muted)}}textarea,select{{width:100%;padding:11px;border:1px solid var(--line);border-radius:10px;background:#0c0810;color:#fff;margin:6px 0}}button,a.btn{{display:inline-block;border:0;border-radius:10px;padding:11px 14px;background:var(--gold);color:#170d1d;font-weight:800;text-decoration:none}}:focus-visible{{outline:3px solid #fff;outline-offset:3px}}
        </style></head><body><main><a class='btn' href='/command-center/support'>Back to Support</a><section class='card'><div class='muted'>{escape(case['category'])} · {escape(case['severity'])} · {escape(case['status'])}</div><h1>{escape(case['subject'])}</h1><p>{escape(case['description'])}</p></section><section aria-labelledby='conversation'><h2 id='conversation'>Conversation</h2>{messages_html}</section><section class='card'><h2>Add reply</h2><form id='reply'><label>Visibility<select id='visibility'><option value='user_visible'>User-visible reply</option>{internal_option}</select></label><label>Message<textarea id='body' maxlength='6000' required></textarea></label><button type='submit'>Add reply</button></form><p id='status' class='muted' role='status'></p></section></main><script>
        const form=document.getElementById('reply');form.addEventListener('submit',async e=>{{e.preventDefault();const status=document.getElementById('status');status.textContent='Saving…';const r=await fetch('/command-center/api/support/cases/{escape(case_id)}/messages',{{method:'POST',credentials:'same-origin',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{visibility:document.getElementById('visibility').value,body:document.getElementById('body').value}})}});if(r.ok)location.reload();else{{let d={{}};try{{d=await r.json()}}catch(_){{}}status.textContent=d.detail||'Reply could not be saved.';}}}});
        </script></body></html>""",
        headers={"Cache-Control": "no-store"},
    )


__all__ = ["router", "SupportConversationStore", "conversations"]
