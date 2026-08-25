from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .esp_niche import require_esp_social_member
from .request_context import reset_current_user_id, set_current_user_id
from .social_management import ActivityEvent, SocialHouseStore, utc_now

router = APIRouter(tags=["ESP Public Social Review"])
ReviewScope = Literal["view", "comment", "approve"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _db_path() -> Path:
    path = Path(os.getenv("AURA_SOCIAL_REVIEW_DB", "data/social_review.sqlite3")).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS social_review_links (
            id TEXT PRIMARY KEY,
            token_hash TEXT UNIQUE NOT NULL,
            owner_user_id TEXT NOT NULL,
            space_id TEXT NOT NULL,
            content_id TEXT NOT NULL,
            scopes_json TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            revoked_at TEXT,
            created_at TEXT NOT NULL,
            last_used_at TEXT
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_social_review_owner ON social_review_links(owner_user_id, created_at DESC)")
    conn.commit()
    return conn


class CreateReviewLink(BaseModel):
    space_id: str = Field(min_length=1, max_length=140)
    content_id: str = Field(min_length=1, max_length=140)
    scopes: list[ReviewScope] = Field(default_factory=lambda: ["view", "comment"])
    expires_hours: int = Field(default=168, ge=1, le=24 * 90)


class PublicFeedback(BaseModel):
    action: Literal["comment", "approve"]
    reviewer_name: str = Field(default="Guest reviewer", min_length=1, max_length=120)
    note: str = Field(default="", max_length=2000)


def _member(request: Request):
    member, _membership, _profile = require_esp_social_member(request)
    return member


def _normalise_scopes(scopes: list[str]) -> list[str]:
    allowed = {"view", "comment", "approve"}
    clean = []
    for scope in scopes:
        if scope in allowed and scope not in clean:
            clean.append(scope)
    if "view" not in clean:
        clean.insert(0, "view")
    return clean


def create_review_link(owner_user_id: str, body: CreateReviewLink) -> dict:
    scopes = _normalise_scopes(body.scopes)
    token = secrets.token_urlsafe(32)
    link_id = "share_" + secrets.token_hex(12)
    expires = _now() + timedelta(hours=body.expires_hours)
    token_ctx = set_current_user_id(owner_user_id)
    try:
        house = SocialHouseStore().load(body.space_id)
        content = next((item for item in house.content if item.id == body.content_id), None)
        if content is None:
            raise KeyError(body.content_id)
    finally:
        reset_current_user_id(token_ctx)
    with _db() as conn:
        conn.execute(
            "INSERT INTO social_review_links(id,token_hash,owner_user_id,space_id,content_id,scopes_json,expires_at,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (link_id, _token_hash(token), owner_user_id, body.space_id, body.content_id, json.dumps(scopes), expires.isoformat(), utc_now()),
        )
    return {
        "id": link_id,
        "token": token,
        "url": f"/review/social/{token}",
        "scopes": scopes,
        "expires_at": expires.isoformat(),
        "content_title": content.title,
    }


def list_review_links(owner_user_id: str) -> list[dict]:
    with _db() as conn:
        rows = conn.execute(
            "SELECT id,space_id,content_id,scopes_json,expires_at,revoked_at,created_at,last_used_at FROM social_review_links WHERE owner_user_id=? ORDER BY created_at DESC LIMIT 200",
            (owner_user_id,),
        ).fetchall()
    return [
        {
            "id": row["id"],
            "space_id": row["space_id"],
            "content_id": row["content_id"],
            "scopes": json.loads(row["scopes_json"]),
            "expires_at": row["expires_at"],
            "revoked_at": row["revoked_at"],
            "created_at": row["created_at"],
            "last_used_at": row["last_used_at"],
        }
        for row in rows
    ]


def revoke_review_link(owner_user_id: str, link_id: str) -> bool:
    with _db() as conn:
        cur = conn.execute(
            "UPDATE social_review_links SET revoked_at=? WHERE id=? AND owner_user_id=? AND revoked_at IS NULL",
            (utc_now(), link_id, owner_user_id),
        )
        return cur.rowcount > 0


def _resolve_token(token: str, required_scope: str = "view") -> dict:
    if not token or len(token) > 300:
        raise PermissionError("Invalid review link")
    with _db() as conn:
        row = conn.execute(
            "SELECT * FROM social_review_links WHERE token_hash=?",
            (_token_hash(token),),
        ).fetchone()
        if row is None:
            raise PermissionError("Review link not found")
        if row["revoked_at"]:
            raise PermissionError("Review link has been revoked")
        try:
            expires = datetime.fromisoformat(row["expires_at"])
        except ValueError as exc:
            raise PermissionError("Review link is invalid") from exc
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires <= _now():
            raise PermissionError("Review link has expired")
        scopes = json.loads(row["scopes_json"])
        if required_scope not in scopes:
            raise PermissionError(f"Review link does not allow {required_scope}")
        conn.execute("UPDATE social_review_links SET last_used_at=? WHERE id=?", (utc_now(), row["id"]))
    return {
        "id": row["id"],
        "owner_user_id": row["owner_user_id"],
        "space_id": row["space_id"],
        "content_id": row["content_id"],
        "scopes": scopes,
        "expires_at": row["expires_at"],
    }


@contextmanager
def _owner_context(owner_user_id: str):
    token = set_current_user_id(owner_user_id)
    try:
        yield
    finally:
        reset_current_user_id(token)


def public_review_snapshot(token: str) -> dict:
    link = _resolve_token(token, "view")
    with _owner_context(link["owner_user_id"]):
        house = SocialHouseStore().load(link["space_id"])
        content = next((item for item in house.content if item.id == link["content_id"]), None)
        if content is None:
            raise FileNotFoundError(link["content_id"])
        return {
            "link": {"id": link["id"], "scopes": link["scopes"], "expires_at": link["expires_at"]},
            "space": {"name": house.name},
            "content": content.model_dump(mode="json"),
            "external_publish_triggered": False,
        }


def submit_public_feedback(token: str, body: PublicFeedback) -> dict:
    scope = "approve" if body.action == "approve" else "comment"
    link = _resolve_token(token, scope)
    reviewer = " ".join(body.reviewer_name.split())[:120] or "Guest reviewer"
    note = " ".join(body.note.split())[:2000]
    with _owner_context(link["owner_user_id"]):
        store = SocialHouseStore()
        house = store.load(link["space_id"])
        content = next((item for item in house.content if item.id == link["content_id"]), None)
        if content is None:
            raise FileNotFoundError(link["content_id"])
        stamp = utc_now()
        if body.action == "approve":
            if not content.approval_required:
                raise ValueError("This content item does not require approval")
            if content.status not in {"pending_approval", "in_production", "draft"}:
                raise ValueError(f"Content in {content.status} state cannot be approved from this link")
            actor = f"Public reviewer: {reviewer}"
            if actor not in content.approved_by:
                content.approved_by.append(actor)
            content.approval_at = stamp
            content.status = "approved"
            line = f"[{stamp}] Approved via public review by {reviewer}" + (f": {note}" if note else "")
            action = "public_content_approved"
        else:
            line = f"[{stamp}] Public feedback by {reviewer}" + (f": {note}" if note else "")
            action = "public_content_commented"
        content.notes = (str(content.notes or "").strip() + "\n" + line).strip()[-12000:]
        content.updated_at = stamp
        house.activity.append(
            ActivityEvent(
                actor=reviewer,
                action=action,
                entity_type="content",
                entity_id=content.id,
                detail=note[:500],
                public=True,
            )
        )
        store.save(house)
    return {"action": body.action, "reviewer": reviewer, "status": content.status, "external_publish_triggered": False}


@router.post("/command-center/api/social/review-links")
def create_link(body: CreateReviewLink, request: Request):
    member = _member(request)
    try:
        return create_review_link(member.user_id, body)
    except FileNotFoundError as exc:
        raise HTTPException(404, "ESP Social House not found") from exc
    except KeyError as exc:
        raise HTTPException(404, "Content item not found") from exc


@router.get("/command-center/api/social/review-links")
def links(request: Request):
    member = _member(request)
    return {"links": list_review_links(member.user_id)}


@router.delete("/command-center/api/social/review-links/{link_id}")
def revoke_link(link_id: str, request: Request):
    member = _member(request)
    if not revoke_review_link(member.user_id, link_id):
        raise HTTPException(404, "Review link not found")
    return {"revoked": True}


@router.get("/api/public-review/social/{token}", include_in_schema=False)
def public_snapshot(token: str):
    try:
        return public_review_snapshot(token)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, "Content no longer exists") from exc


@router.post("/api/public-review/social/{token}", include_in_schema=False)
def public_feedback(token: str, body: PublicFeedback):
    try:
        return submit_public_feedback(token, body)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, "Content no longer exists") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


PAGE_CSS = """
:root{--bg:#04050a;--panel:#101421;--line:#ffffff20;--text:#fff;--muted:#b9c1d2;--gold:#efcc77;--good:#77dda8}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 10% 0,#3d176b,transparent 30%),#04050a;color:var(--text);font-family:Inter,ui-sans-serif,system-ui,sans-serif}.wrap{width:min(900px,calc(100% - 26px));margin:auto;padding:38px 0 60px}.card{border:1px solid var(--line);border-radius:18px;padding:16px;background:#101421e8;margin:10px 0}.variant{border:1px solid var(--line);border-radius:12px;padding:11px;margin:8px 0;background:#ffffff05}.muted{color:var(--muted);line-height:1.55}.pill{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:4px 8px;font-size:.7rem;margin:2px}.btn{border:1px solid var(--line);border-radius:11px;padding:10px 13px;background:#ffffff09;color:#fff;font-weight:850;cursor:pointer}.primary{border:0;background:linear-gradient(110deg,var(--gold),#9d70ff);color:#160d1e}input,textarea{width:100%;border:1px solid var(--line);border-radius:10px;padding:10px;background:#070912;color:#fff;font:inherit}textarea{min-height:90px}.actions{display:flex;gap:8px;flex-wrap:wrap}.notice{display:none;border:1px solid var(--line);border-radius:10px;padding:10px}.notice.show{display:block}
"""

PAGE_SCRIPT = r"""
const token=location.pathname.split('/').pop(),api='/api/public-review/social/'+encodeURIComponent(token),$=id=>document.getElementById(id);function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}function msg(v,b=false){const n=$('notice');n.textContent=v;n.className='notice show';n.style.borderColor=b?'#ff8fa355':''}async function req(opt={}){const r=await fetch(api,{headers:{'Content-Type':'application/json'},...opt});let b={};try{b=await r.json()}catch(_){}if(!r.ok)throw new Error(b.detail||`Request failed (${r.status})`);return b}function render(d){const c=d.content||{},scopes=d.link?.scopes||[];$('title').textContent=c.title||'Content review';$('meta').textContent=`${d.space?.name||'ESP'} · ${c.status||''} · expires ${d.link?.expires_at||''}`;$('variants').innerHTML=(c.variants||[]).map(v=>`<div class="variant"><b>${esc(v.platform)} · ${esc(v.content_type)}</b><div class="muted">${v.scheduled_at?'Scheduled '+esc(v.scheduled_at):'Unscheduled'}</div><div style="white-space:pre-wrap;margin-top:7px">${esc(v.caption||'')}</div>${(v.hashtags||[]).length?`<div class="muted">${v.hashtags.map(x=>'#'+esc(x)).join(' ')}</div>`:''}</div>`).join('');$('feedback').style.display=scopes.includes('comment')||scopes.includes('approve')?'block':'none';$('comment').style.display=scopes.includes('comment')?'inline-block':'none';$('approve').style.display=scopes.includes('approve')?'inline-block':'none'}async function submit(action){const reviewer_name=$('name').value.trim()||'Guest reviewer',note=$('note').value.trim();try{const d=await req({method:'POST',body:JSON.stringify({action,reviewer_name,note})});msg(action==='approve'?'Approved. Thank you.':'Feedback saved. Thank you.');if(action==='approve')$('approve').disabled=true;setTimeout(load,500)}catch(e){msg(e.message,true)}}async function load(){try{render(await req())}catch(e){msg(e.message,true);$('content').style.display='none'}}$('comment').onclick=()=>submit('comment');$('approve').onclick=()=>submit('approve');load();
"""


@router.get("/review/social/{token}", response_class=HTMLResponse, include_in_schema=False)
def public_review_page(token: str):
    # Validate before rendering so revoked/expired links do not get a live review shell.
    try:
        public_review_snapshot(token)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, "Content no longer exists") from exc
    html = f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='noindex,nofollow'><meta name='referrer' content='no-referrer'><title>ESP Content Review</title><style>{PAGE_CSS}</style></head><body><main class='wrap'><div class='muted' style='text-transform:uppercase;letter-spacing:.12em;font-size:.72rem'>Elevate Souls Productions · Private Content Review</div><h1 id='title'>Content review</h1><p id='meta' class='muted'></p><div id='notice' class='notice'></div><section id='content'><div id='variants'></div><div id='feedback' class='card' style='display:none'><h2>Feedback</h2><input id='name' maxlength='120' placeholder='Your name'><textarea id='note' maxlength='2000' placeholder='Optional feedback / changes'></textarea><div class='actions'><button id='comment' class='btn'>Send feedback</button><button id='approve' class='btn primary'>Approve content</button></div><p class='muted' style='font-size:.75rem'>This review link cannot publish content, access the ESP workspace, or view other projects.</p></div></section></main><script>{PAGE_SCRIPT}</script></body></html>"""
    return HTMLResponse(html, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


__all__ = [
    "router",
    "CreateReviewLink",
    "PublicFeedback",
    "create_review_link",
    "list_review_links",
    "revoke_review_link",
    "public_review_snapshot",
    "submit_public_feedback",
]
