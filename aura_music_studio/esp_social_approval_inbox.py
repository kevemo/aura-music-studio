from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .branding import ENDORSEMENT
from .esp_niche import require_esp_social_member
from .esp_social_public_review import router as public_review_router
from .esp_social_review_links_ui import router as review_links_ui_router
from .social_management import ActivityEvent, SocialHouseStore, utc_now

router = APIRouter(tags=["ESP Social Approvals"])
router.include_router(public_review_router)
router.include_router(review_links_ui_router)
ReviewDecision = Literal["approve", "request_changes", "reject"]


class ReviewRequest(BaseModel):
    decision: ReviewDecision
    note: str = Field(default="", max_length=2000)


def _member(request: Request):
    member, _membership, _profile = require_esp_social_member(request)
    return member


def _review_line(actor: str, decision: ReviewDecision, note: str) -> str:
    stamp = datetime.now(timezone.utc).isoformat()
    clean = " ".join((note or "").split())[:2000]
    label = {
        "approve": "Approved",
        "request_changes": "Changes requested",
        "reject": "Rejected / archived",
    }[decision]
    return f"[{stamp}] {label} by {actor}" + (f": {clean}" if clean else "")


def approval_queue(store: SocialHouseStore | None = None) -> list[dict]:
    db = store or SocialHouseStore()
    rows: list[dict] = []
    for index in db.list_spaces():
        try:
            house = db.load(str(index.get("id") or ""))
        except Exception:
            continue
        for content in house.content:
            if not content.approval_required or content.status != "pending_approval":
                continue
            rows.append({
                "space_id": house.id,
                "space_name": house.name,
                "content": content.model_dump(mode="json"),
            })
    rows.sort(key=lambda row: str((row["content"] or {}).get("updated_at") or ""), reverse=True)
    return rows


def review_content(
    actor: str,
    space_id: str,
    content_id: str,
    decision: ReviewDecision,
    note: str = "",
    *,
    store: SocialHouseStore | None = None,
):
    db = store or SocialHouseStore()
    house = db.load(space_id)
    content = next((item for item in house.content if item.id == content_id), None)
    if content is None:
        raise KeyError(content_id)
    if not content.approval_required:
        raise ValueError("This content item does not require approval")
    if content.status not in {"pending_approval", "in_production", "draft"}:
        raise ValueError(f"Content in {content.status} state cannot be reviewed from the Approval Inbox")

    line = _review_line(actor, decision, note)
    existing_notes = str(content.notes or "").strip()
    content.notes = (existing_notes + "\n" + line).strip()[-12000:]
    content.updated_at = utc_now()

    if decision == "approve":
        if actor not in content.approved_by:
            content.approved_by.append(actor)
        content.approval_at = utc_now()
        content.status = "approved"
        action = "content_approved"
    elif decision == "request_changes":
        content.approved_by = []
        content.approval_at = None
        content.status = "in_production"
        action = "content_changes_requested"
    else:
        content.approved_by = []
        content.approval_at = None
        content.status = "archived"
        action = "content_rejected_archived"

    house.activity.append(
        ActivityEvent(
            actor=actor,
            action=action,
            entity_type="content",
            entity_id=content.id,
            detail=(" ".join((note or "").split()))[:500],
        )
    )
    return db.save(house)


@router.get("/command-center/api/social/approvals")
def list_approvals(request: Request):
    _member(request)
    rows = approval_queue()
    return {"approvals": rows, "count": len(rows), "external_publish_triggered": False}


@router.post("/command-center/api/social/spaces/{space_id}/content/{content_id}/review")
def review_social_content(space_id: str, content_id: str, body: ReviewRequest, request: Request):
    member = _member(request)
    try:
        house = review_content(member.user_id, space_id, content_id, body.decision, body.note)
    except FileNotFoundError as exc:
        raise HTTPException(404, "ESP Social House not found") from exc
    except KeyError as exc:
        raise HTTPException(404, "Content item not found") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    content = next(item for item in house.content if item.id == content_id)
    return {
        "content": content.model_dump(mode="json"),
        "decision": body.decision,
        "reviewer": member.user_id,
        "external_publish_triggered": False,
        "detail": "Approval state updated. External publishing remains a separate authorised action.",
    }


CSS = r"""
:root{--bg:#03040a;--panel:#0c1020;--line:#ffffff1d;--text:#fff;--muted:#b8bfd2;--gold:#edca72;--violet:#9d70ff;--green:#74dfa8;--red:#ff90a4}*{box-sizing:border-box}body{margin:0;min-height:100vh;background:radial-gradient(circle at 7% 0,#3d1769,transparent 29%),radial-gradient(circle at 94% 0,#123d70,transparent 25%),#03040a;color:var(--text);font-family:Inter,ui-sans-serif,system-ui,sans-serif}a{color:inherit;text-decoration:none}button,textarea{font:inherit}.wrap{width:min(1240px,calc(100% - 28px));margin:auto}.nav{position:sticky;top:0;z-index:10;border-bottom:1px solid var(--line);background:#05070dec;backdrop-filter:blur(18px)}.navin{min-height:70px;display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap}.brand{font-weight:950}.brand small{display:block;color:var(--gold);font-size:.64rem;text-transform:uppercase;letter-spacing:.08em}.btn{border:1px solid var(--line);border-radius:11px;padding:9px 12px;background:#ffffff08;color:#fff;font-weight:850;cursor:pointer}.hero{padding:46px 0 22px}.eyebrow{font-size:.7rem;letter-spacing:.17em;text-transform:uppercase;color:var(--gold);font-weight:950}h1{font-size:clamp(2.7rem,7vw,5.4rem);line-height:.92;letter-spacing:-.06em;margin:.15em 0 .2em}.lead,.muted{color:var(--muted);line-height:1.6}.queue{display:grid;gap:10px;padding-bottom:55px}.card{border:1px solid var(--line);border-radius:18px;background:linear-gradient(145deg,#11162a,#080b15);padding:15px}.variant{border:1px solid var(--line);border-radius:12px;padding:10px;margin:7px 0;background:#ffffff04}.pill{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:4px 7px;font-size:.67rem;margin:2px}.row{display:grid;grid-template-columns:1fr auto;gap:10px}.actions{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}.approve{border-color:#74dfa855;color:var(--green)}.reject{border-color:#ff90a455;color:var(--red)}textarea{width:100%;min-height:70px;resize:vertical;border:1px solid var(--line);border-radius:11px;background:#060912;color:#fff;padding:10px;outline:none}.notice{display:none;border:1px solid var(--line);border-radius:11px;padding:10px;margin:10px 0}.notice.show{display:block}.empty{text-align:center;padding:28px;color:var(--muted);border:1px dashed #ffffff2a;border-radius:14px}.footer{border-top:1px solid var(--line);padding:28px 0 44px;color:var(--muted);font-size:.82rem}@media(max-width:720px){.row{grid-template-columns:1fr}}
"""

SCRIPT = r"""
const API='/command-center/api/social';let rows=[];const $=id=>document.getElementById(id);function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}function note(m,b=false){const n=$('notice');n.textContent=m;n.className='notice show';n.style.borderColor=b?'#ff90a455':'';clearTimeout(window._n);window._n=setTimeout(()=>n.className='notice',5000)}async function req(u,o={}){const r=await fetch(u,{credentials:'same-origin',headers:{'Content-Type':'application/json'},...o});let b={};try{b=await r.json()}catch(_){}if(!r.ok)throw new Error(b.detail||`Request failed (${r.status})`);return b}function variants(content){return (content.variants||[]).map(v=>`<div class="variant"><b>${esc(v.platform)} · ${esc(v.content_type)}</b><div class="muted" style="font-size:.72rem">${v.scheduled_at?'Scheduled '+esc(v.scheduled_at):'Unscheduled'} · auto-publish ${v.auto_publish?'requested':'off'}</div><div style="white-space:pre-wrap;margin-top:5px">${esc(v.caption||'')}</div>${(v.hashtags||[]).length?`<div class="muted">${v.hashtags.map(x=>'#'+esc(x)).join(' ')}</div>`:''}</div>`).join('')}function render(){ $('count').textContent=rows.length; $('queue').innerHTML=rows.length?rows.map(row=>{const c=row.content||{};return `<article class="card"><div class="row"><div><div class="eyebrow">${esc(row.space_name)}</div><h2 style="margin:4px 0">${esc(c.title)}</h2><div><span class="pill">${esc(c.status)}</span><span class="pill">${(c.variants||[]).length} variant${(c.variants||[]).length===1?'':'s'}</span>${c.source_creative_project?`<span class="pill">Creative DNA: ${esc(c.source_creative_project)}</span>`:''}</div></div><div class="muted" style="font-size:.7rem">${esc(c.updated_at||'')}</div></div>${variants(c)}<textarea id="note_${esc(c.id)}" placeholder="Optional review note / changes requested"></textarea><div class="actions"><button class="btn approve" onclick="review('${esc(row.space_id)}','${esc(c.id)}','approve')">✓ Approve</button><button class="btn" onclick="review('${esc(row.space_id)}','${esc(c.id)}','request_changes')">↺ Request changes</button><button class="btn reject" onclick="review('${esc(row.space_id)}','${esc(c.id)}','reject')">Archive / reject</button></div><div class="muted" style="font-size:.66rem;margin-top:7px">A review decision changes internal approval state only. It does not publish externally.</div></article>`}).join(''):'<div class="empty">No content is waiting for approval.</div>'}async function load(){try{const d=await req(API+'/approvals');rows=d.approvals||[];render()}catch(e){note(e.message,true)}}async function review(space,id,decision){if(decision==='reject'&&!confirm('Archive/reject this content item?'))return;const noteValue=$(`note_${id}`)?.value||'';try{const d=await req(`${API}/spaces/${encodeURIComponent(space)}/content/${encodeURIComponent(id)}/review`,{method:'POST',body:JSON.stringify({decision,note:noteValue})});note(d.detail||'Review saved.');await load()}catch(e){note(e.message,true)}}load();
"""


@router.get("/command-center/social/approvals", response_class=HTMLResponse, include_in_schema=False)
def approval_inbox(request: Request):
    _member(request)
    html = f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='noindex,nofollow'><title>ESP Approval Inbox</title><style>{CSS}</style></head><body><nav class='nav'><div class='wrap navin'><a class='brand' href='/command-center/social'>Elevate Souls Productions<small>Social Approval Inbox</small></a><div><a class='btn' href='/command-center/social/review-links'>Review Links</a> <a class='btn' href='/command-center/social/creative-launch'>Creative → Social</a> <a class='btn' href='/command-center/social'>Social House</a></div></div></nav><main class='wrap'><section class='hero'><div class='eyebrow'>Internal review gate</div><h1>Approve before anything can <span style='color:var(--gold)'>move forward.</span></h1><p class='lead'>Review platform-specific variants together, request changes or archive a rejected concept. The reviewer identity is taken from the signed-in ESP account, not typed into the browser. Approval does not trigger external publishing.</p><div id='notice' class='notice'></div><p class='muted'><b id='count'>0</b> item(s) waiting.</p></section><section id='queue' class='queue'><div class='empty'>Loading approvals…</div></section></main><footer class='footer'><div class='wrap'>{ENDORSEMENT}</div></footer><script>{SCRIPT}</script></body></html>"""
    return HTMLResponse(html)


__all__ = ["router", "approval_queue", "review_content", "SCRIPT"]
