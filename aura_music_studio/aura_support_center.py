from __future__ import annotations

import os
import re
import secrets
import sqlite3
from datetime import datetime, timezone
from html import escape
from uuid import uuid4

from fastapi import APIRouter, Form, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .accounts import AccountStore
from .branding import ENDORSEMENT, PRODUCT_FULL_NAME, TAGLINE
from .mailer import DEFAULT_ADMIN_EMAIL, send_email
from .owner_identity import owner_actor, owner_session_authorized

router = APIRouter()
accounts = AccountStore()
MEMBER_COOKIE = "lss_session"
SUPPORT_EMAIL = DEFAULT_ADMIN_EMAIL

FEATURE_GUIDE: tuple[dict[str, str], ...] = (
    {"key":"aura","title":"Aura AI","keywords":"aura ai voice text assistant help workflow research artifacts tasks notifications", "body":"Aura is the Command Center's intelligent guide. Members can use text and supported voice experiences for creative direction, project guidance, explanations, research and bounded tool workflows. Aura respects the member's permissions and never grants a role or entitlement by conversation."},
    {"key":"music","title":"Music Studio & Professional DAW","keywords":"music song lyrics daw stems mix master vocal karaoke recording midi engineering", "body":"Create songs, develop lyrics and arrangements, work with editable stems, record audio, use multitrack DAW tools, mix and master, separate stems, align lyrics, build karaoke assets and export supported project media."},
    {"key":"video","title":"Video Studio & Professional Editor","keywords":"video editor timeline image to video text captions scenes render transitions keyframes", "body":"Build text-to-video and image-to-video projects, organise scenes on a professional timeline, edit media sources, sync music and captions, render background jobs and export completed video projects."},
    {"key":"image","title":"Image & Poster Studio","keywords":"image poster artwork inpaint outpaint upscale cover social design", "body":"Create and edit project artwork, covers, posters and social assets with project-owned media, rights-aware generation, image editing and reusable project assets."},
    {"key":"game","title":"Game Forge","keywords":"game forge godot world quests characters physics adventure", "body":"Build bounded game worlds, characters, quests, events and gameplay state with Aura-assisted creation, validated project state and supported Godot-oriented export/preview workflows."},
    {"key":"esp","title":"ESP Creator Network","keywords":"esp creator agent tiktok live network mentor training recruitment creator hub agent hub", "body":"Eligible Elevate Souls Productions members can access separate Creator, Agent and Owner systems according to their server-authoritative ESP role. Subscription purchase never grants ESP Creator, Agent or Owner permission."},
    {"key":"social","title":"Social Management","keywords":"social media tiktok instagram youtube facebook x twitch handles publishing analytics", "body":"Members can maintain their social handles and use enabled social-management integrations. TikTok is the required profile handle in the member social profile; other supported networks are optional. Provider data is only shown when a legitimate integration supplies it."},
    {"key":"membership","title":"Membership, Billing & Discounts","keywords":"membership subscription billing payment renewal discount promo code creation coins", "body":"Membership controls creative entitlements. Owners can configure approved discount codes and billing communications. Renewal dates and amounts are shown only from verified billing facts; ESP organisational roles stay separate from commercial membership."},
    {"key":"privacy","title":"Privacy, Safety & Rights","keywords":"privacy consent safety report appeal copyright ip likeness voice rights deletion export", "body":"The Command Center includes privacy requests, consent controls, safety reporting and appeals, IP/copyright processes, likeness and voice-consent safeguards, provenance and jurisdiction-aware governance workflows."},
    {"key":"aurasec","title":"Aura Sec","keywords":"aura sec security devices threat vulnerability recovery optimizer passkey", "body":"Aura Sec is a separate security product/control plane. Its browser area can manage verified device state, approvals and security information, but browser activity is not presented as native endpoint protection and separate Aura Sec licensing is required."},
    {"key":"projects","title":"Projects, Assets & Exports","keywords":"project asset library files export download provenance versions jobs", "body":"Creative work is organised into private projects with reusable assets, versions, background jobs, provenance and controlled exports. Project and tenant boundaries prevent one member from accessing another member's private media."},
)

ESCALATION_TERMS = {
    "refund", "chargeback", "charged", "payment failed", "account locked", "disabled", "hacked",
    "harassment", "bullying", "threat", "safety", "privacy", "delete my data", "copyright",
    "legal", "appeal", "complaint", "fraud", "security incident", "cannot login", "can't login",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(value: str, limit: int) -> str:
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", (value or "").strip())[:limit]


class SupportStore:
    def __init__(self, account_store: AccountStore | None = None):
        self.accounts = account_store or accounts
        self.db_path = self.accounts.db_path
        self._init_schema()

    def _connect(self):
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _init_schema(self) -> None:
        with self._connect() as con:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS support_tickets (
                    id TEXT PRIMARY KEY,
                    user_id TEXT,
                    email TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    category TEXT NOT NULL DEFAULT 'general',
                    status TEXT NOT NULL DEFAULT 'open',
                    escalated INTEGER NOT NULL DEFAULT 0,
                    escalation_reason TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE SET NULL
                );
                CREATE TABLE IF NOT EXISTS support_messages (
                    id TEXT PRIMARY KEY,
                    ticket_id TEXT NOT NULL,
                    direction TEXT NOT NULL CHECK(direction IN ('inbound','outbound')),
                    author_type TEXT NOT NULL,
                    body TEXT NOT NULL,
                    external_message_ref TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(ticket_id) REFERENCES support_tickets(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS support_aura_actions (
                    id TEXT PRIMARY KEY,
                    ticket_id TEXT NOT NULL,
                    answer_text TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    matched_feature TEXT,
                    auto_sent INTEGER NOT NULL DEFAULT 0,
                    requires_owner INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(ticket_id) REFERENCES support_tickets(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_support_ticket_status ON support_tickets(status, escalated, updated_at DESC);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_support_external_message_ref
                    ON support_messages(external_message_ref) WHERE external_message_ref IS NOT NULL;
            """)

    def answer(self, question: str) -> dict:
        question = _clean(question, 4000)
        if not question:
            raise ValueError("Ask Aura a question first")
        words = {w for w in re.findall(r"[a-z0-9]+", question.lower()) if len(w) > 2}
        scored = []
        for item in FEATURE_GUIDE:
            hay = set(re.findall(r"[a-z0-9]+", (item["title"] + " " + item["keywords"] + " " + item["body"]).lower()))
            score = len(words & hay)
            scored.append((score, item))
        score, item = max(scored, key=lambda row: row[0])
        confidence = min(0.98, 0.45 + score * 0.08) if score else 0.25
        if score == 0:
            return {
                "answer": "I can explain every major area of the Command Center. Try asking about Music Studio, Video Studio, Image & Poster Studio, Game Forge, Aura AI, ESP Creator Network, social tools, membership, privacy or Aura Sec. If your question is account-specific, use Contact Support and Mary or Kev can assist when needed.",
                "confidence": confidence,
                "matched_feature": None,
                "requires_owner": False,
            }
        return {
            "answer": f"{item['title']}: {item['body']}",
            "confidence": confidence,
            "matched_feature": item["key"],
            "requires_owner": False,
        }

    def create_ticket(self, *, email: str, display_name: str, subject: str, message: str,
                      user_id: str | None = None, category: str = "general", external_message_ref: str | None = None) -> dict:
        email = _clean(email, 254).lower()
        if "@" not in email or email.startswith("@") or email.endswith("@"):
            raise ValueError("Enter a valid email address")
        display_name = _clean(display_name, 120) or "Member"
        subject = _clean(subject, 180)
        message = _clean(message, 12000)
        if len(subject) < 3 or len(message) < 8:
            raise ValueError("Please include a subject and enough detail for support")
        lower = f"{subject} {message}".lower()
        escalation_matches = sorted(term for term in ESCALATION_TERMS if term in lower)
        escalated = bool(escalation_matches)
        ticket_id = uuid4().hex
        now = _now()
        with self._connect() as con:
            con.execute("""INSERT INTO support_tickets
                (id,user_id,email,display_name,subject,category,status,escalated,escalation_reason,created_at,updated_at)
                VALUES (?,?,?,?,?,?, 'open',?,?,?,?,?)""",
                (ticket_id, user_id, email, display_name, subject, _clean(category, 60) or "general",
                 int(escalated), ", ".join(escalation_matches)[:500], now, now),
            )
            con.execute("""INSERT INTO support_messages
                (id,ticket_id,direction,author_type,body,external_message_ref,created_at)
                VALUES (?,?,'inbound','member',?,?,?)""",
                (uuid4().hex, ticket_id, message, _clean(external_message_ref or "", 240) or None, now),
            )
        return self.get_ticket(ticket_id) or {}

    def get_ticket(self, ticket_id: str) -> dict | None:
        with self._connect() as con:
            row = con.execute("SELECT * FROM support_tickets WHERE id=?", (ticket_id,)).fetchone()
            if not row:
                return None
            messages = con.execute("SELECT * FROM support_messages WHERE ticket_id=? ORDER BY created_at", (ticket_id,)).fetchall()
        out = dict(row)
        out["messages"] = [dict(m) for m in messages]
        return out

    def list_tickets(self, limit: int = 200) -> list[dict]:
        with self._connect() as con:
            rows = con.execute("SELECT * FROM support_tickets ORDER BY escalated DESC, updated_at DESC LIMIT ?", (max(1, min(limit, 500)),)).fetchall()
        return [dict(row) for row in rows]

    def process_aura_response(self, ticket_id: str, *, allow_auto_send: bool = True) -> dict:
        ticket = self.get_ticket(ticket_id)
        if not ticket:
            raise ValueError("Support ticket not found")
        inbound = next((m for m in reversed(ticket["messages"]) if m["direction"] == "inbound"), None)
        if not inbound:
            raise ValueError("No inbound support message found")
        result = self.answer(ticket["subject"] + "\n" + inbound["body"])
        requires_owner = bool(ticket["escalated"] or result["confidence"] < 0.55)
        if requires_owner:
            answer = (
                "Thank you for contacting Elevate Souls Productions support. Aura has logged your request and flagged it for Mary or Kev because it needs account-specific or human review. A member of the owner/support team will assist you as soon as practical."
            )
        else:
            answer = "Hello " + ticket["display_name"] + ",\n\nAura Support: " + result["answer"] + "\n\nIf this does not resolve your question, reply to this email and Mary or Kev can assist further.\n\n" + ENDORSEMENT
        auto_sent = False
        if allow_auto_send:
            delivery = send_email(ticket["email"], f"Re: {ticket['subject']} — Aura Support", answer)
            auto_sent = bool(delivery.get("sent") or delivery.get("delivery") == "development_outbox")
            with self._connect() as con:
                con.execute("""INSERT INTO support_messages
                    (id,ticket_id,direction,author_type,body,external_message_ref,created_at)
                    VALUES (?,?,'outbound','aura',?,NULL,?)""", (uuid4().hex, ticket_id, answer, _now()))
        with self._connect() as con:
            con.execute("""INSERT INTO support_aura_actions
                (id,ticket_id,answer_text,confidence,matched_feature,auto_sent,requires_owner,created_at)
                VALUES (?,?,?,?,?,?,?,?)""",
                (uuid4().hex, ticket_id, answer, float(result["confidence"]), result["matched_feature"], int(auto_sent), int(requires_owner), _now()),
            )
            if requires_owner:
                con.execute("UPDATE support_tickets SET escalated=1,status='owner_review',updated_at=? WHERE id=?", (_now(), ticket_id))
        if requires_owner:
            send_email(SUPPORT_EMAIL, f"Owner assistance required — support ticket {ticket_id[:8]}",
                       f"Aura has escalated a support request for Mary or Kev.\n\nFrom: {ticket['display_name']} <{ticket['email']}>\nSubject: {ticket['subject']}\nTicket: {ticket_id}\n\nReview in Owner Support: /owner/support")
        return {**result, "ticket_id": ticket_id, "requires_owner": requires_owner, "auto_sent": auto_sent, "answer": answer}

    def owner_reply(self, ticket_id: str, body: str, actor: str = "ESP Owner") -> dict:
        ticket = self.get_ticket(ticket_id)
        if not ticket:
            raise ValueError("Support ticket not found")
        body = _clean(body, 12000)
        if len(body) < 3:
            raise ValueError("Reply cannot be empty")
        who = owner_actor(actor)
        delivery = send_email(ticket["email"], f"Re: {ticket['subject']} — Elevate Souls Productions Support", body)
        with self._connect() as con:
            con.execute("INSERT INTO support_messages(id,ticket_id,direction,author_type,body,external_message_ref,created_at) VALUES (?,?,'outbound',?,?,NULL,?)",
                        (uuid4().hex, ticket_id, who, body, _now()))
            con.execute("UPDATE support_tickets SET status='answered',updated_at=? WHERE id=?", (_now(), ticket_id))
        return delivery


support = SupportStore()


def _page(body: str, title: str) -> HTMLResponse:
    css = """body{margin:0;background:#09060f;color:#fff;font-family:Inter,system-ui,sans-serif}.wrap{width:min(1200px,calc(100% - 28px));margin:auto;padding:28px 0 60px}.top,.row{display:flex;justify-content:space-between;gap:12px;align-items:center;flex-wrap:wrap}.eyebrow{color:#f3bd68;text-transform:uppercase;letter-spacing:.15em;font-weight:900;font-size:.74rem}h1{font-size:clamp(2.6rem,6vw,5rem);margin:.12em 0}.muted{color:#cfc5d8;line-height:1.6}.card{background:#17101fee;border:1px solid #ffffff20;border-radius:20px;padding:18px;margin:12px 0}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}.btn,button{border:1px solid #ffffff25;background:#ffffff0b;color:#fff;border-radius:10px;padding:10px 13px;font-weight:800;text-decoration:none;cursor:pointer}.primary{border:0;background:linear-gradient(110deg,#f3bd68,#9c75ff);color:#160b1d}input,textarea,select{width:100%;box-sizing:border-box;background:#0c0812;color:#fff;border:1px solid #ffffff24;border-radius:10px;padding:11px;margin:6px 0 12px}textarea{min-height:130px}.answer{white-space:pre-wrap;line-height:1.65}.pill{display:inline-block;border:1px solid #ffffff25;border-radius:999px;padding:4px 8px;font-size:.75rem}.bad{color:#ff9daf}.good{color:#8be5ae}@media(max-width:800px){.grid{grid-template-columns:1fr}}"""
    return HTMLResponse(f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{escape(title)} — {escape(PRODUCT_FULL_NAME)}</title><style>{css}</style></head><body><main class='wrap'>{body}</main></body></html>")


@router.get("/help-support", response_class=HTMLResponse, include_in_schema=False)
@router.get("/support", response_class=HTMLResponse, include_in_schema=False)
def support_center(request: Request, q: str = "", sent: str = ""):
    guide = "".join(f"<article class='card'><div class='eyebrow'>{escape(item['title'])}</div><p class='muted'>{escape(item['body'])}</p></article>" for item in FEATURE_GUIDE)
    answer = ""
    if q:
        try:
            result = support.answer(q)
            answer = f"<section class='card' id='aura-answer'><div class='eyebrow'>Aura Support</div><h2>Answer</h2><div class='answer' id='answer-text'>{escape(result['answer'])}</div><div class='row' style='margin-top:12px'><span class='pill'>Knowledge confidence: {round(float(result['confidence'])*100)}%</span><button type='button' onclick='speakAnswer()'>🔊 Aura read this aloud</button></div></section>"
        except ValueError as exc:
            answer = f"<div class='card bad'>{escape(str(exc))}</div>"
    flash = "<div class='card good'>Your support request has been sent. Aura will respond where the question is safely covered by the help knowledge base, and Mary or Kev will be alerted when human assistance is needed.</div>" if sent else ""
    member = accounts.resolve_session(request.cookies.get(MEMBER_COOKIE))
    default_email = escape((member or {}).get("email") or "", quote=True)
    default_name = escape((member or {}).get("display_name") or "", quote=True)
    body = f"""<div class='top'><div><div class='eyebrow'>Powered by Aura AI</div><h1>Aura Help & Support Centre</h1><p class='muted'>Learn the complete Command Center by text or voice, or contact Elevate Souls Productions support when you need personal assistance.</p></div><div class='row'><a class='btn' href='/'>Home</a><a class='btn' href='/esp-network'>ESP Creator Network</a></div></div>{flash}
<section class='card'><div class='eyebrow'>Ask Aura</div><h2>Text & voice help</h2><p class='muted'>Ask how any major feature works. Aura's support answers on this page are grounded in the site's maintained feature guide. Voice input depends on browser speech-recognition support; spoken answers use your device's speech synthesis.</p><form method='get' action='/help-support'><textarea id='question' name='q' placeholder='For example: How do I use the Professional DAW and stems?'>{escape(q)}</textarea><div class='row' style='justify-content:flex-start'><button class='primary'>Ask Aura</button><button type='button' onclick='startListening()'>🎙 Speak to Aura</button></div></form></section>{answer}
<section><div class='eyebrow'>Everything you can learn</div><h2>Command Center feature guide</h2><div class='grid'>{guide}</div></section>
<section class='card'><div class='eyebrow'>Contact Support</div><h2>Email Mary / Kev support</h2><p class='muted'>Support email: <b>{escape(SUPPORT_EMAIL)}</b>. Use this form to create a tracked support request. Aura can provide a bounded first response for general feature questions. Billing, account access, privacy, safety, legal, copyright and uncertain requests are escalated for Mary or Kev rather than being autonomously decided by AI.</p><form method='post' action='/support/contact'><input name='display_name' value='{default_name}' placeholder='Your name' required><input type='email' name='email' value='{default_email}' placeholder='Your email' required><select name='category'><option value='general'>General help</option><option value='creative'>Creative Studios</option><option value='esp'>ESP Creator Network</option><option value='billing'>Membership / billing</option><option value='account'>Account access</option><option value='privacy'>Privacy / rights</option><option value='safety'>Safety / safeguarding</option><option value='security'>Security / Aura Sec</option></select><input name='subject' placeholder='What do you need help with?' required maxlength='180'><textarea name='message' placeholder='Tell us what happened or what you would like to learn.' required></textarea><button class='primary'>Send to Elevate Souls Productions Support</button></form></section>
<script>
function speakAnswer(){{const el=document.getElementById('answer-text');if(!el||!('speechSynthesis' in window))return;window.speechSynthesis.cancel();const u=new SpeechSynthesisUtterance(el.innerText);u.rate=1;window.speechSynthesis.speak(u)}}
function startListening(){{const SR=window.SpeechRecognition||window.webkitSpeechRecognition;if(!SR){{alert('Voice input is not supported by this browser. You can still type to Aura.');return}}const r=new SR();r.lang=navigator.language||'en-GB';r.interimResults=false;r.maxAlternatives=1;r.onresult=e=>{{document.getElementById('question').value=e.results[0][0].transcript}};r.start()}}
</script><footer class='muted' style='margin-top:30px'>{escape(TAGLINE)} · {escape(ENDORSEMENT)}</footer>"""
    return _page(body, "Aura Help & Support Centre")


@router.post("/support/contact", include_in_schema=False)
def contact_support(request: Request, display_name: str = Form(...), email: str = Form(...), subject: str = Form(...),
                    message: str = Form(...), category: str = Form("general")):
    member = accounts.resolve_session(request.cookies.get(MEMBER_COOKIE))
    try:
        ticket = support.create_ticket(email=email, display_name=display_name, subject=subject, message=message,
                                       user_id=(member or {}).get("id"), category=category)
        send_email(SUPPORT_EMAIL, f"Support request — {ticket['subject']}",
                   f"New Command Center support request.\n\nFrom: {ticket['display_name']} <{ticket['email']}>\nCategory: {ticket['category']}\nTicket: {ticket['id']}\n\n{message}\n\nOwner queue: /owner/support")
        support.process_aura_response(ticket["id"], allow_auto_send=True)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse("/help-support?sent=1", status_code=303)


@router.post("/support/inbound-email")
def inbound_support_email(sender_email: str = Form(...), sender_name: str = Form("Member"), subject: str = Form(...),
                          body: str = Form(...), message_ref: str = Form(...), x_support_inbound_token: str | None = Header(default=None)):
    configured = (os.getenv("LSS_SUPPORT_INBOUND_TOKEN") or "").strip()
    if not configured or not x_support_inbound_token or not secrets.compare_digest(configured, x_support_inbound_token):
        raise HTTPException(403, "Verified support email ingestion is not configured")
    try:
        ticket = support.create_ticket(email=sender_email, display_name=sender_name, subject=subject, message=body,
                                       category="email", external_message_ref=message_ref)
        return support.process_aura_response(ticket["id"], allow_auto_send=True)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "This inbound email has already been processed") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/owner/support", response_class=HTMLResponse, include_in_schema=False)
def owner_support(request: Request):
    if not owner_session_authorized(request):
        return RedirectResponse("/owner", status_code=303)
    rows = support.list_tickets()
    tickets = "".join(f"<article class='card'><div class='row'><div><div class='eyebrow'>{escape(row['category'].title())}</div><h3>{escape(row['subject'])}</h3></div><span class='pill {'bad' if row['escalated'] else ''}'>{escape(row['status'].replace('_',' ').title())}</span></div><p class='muted'>{escape(row['display_name'])} · {escape(row['email'])} · {escape(row['created_at'][:16].replace('T',' '))}</p><a class='btn primary' href='/owner/support/{row['id']}'>Open ticket</a></article>" for row in rows) or "<div class='card muted'>No support tickets yet.</div>"
    return _page(f"<div class='top'><div><div class='eyebrow'>Mary / Kev Owner Support</div><h1>Support Queue</h1><p class='muted'>Aura can answer bounded feature questions. Escalated tickets require owner review.</p></div><a class='btn' href='/owner/dashboard'>Owner Command Center</a></div>{tickets}", "Owner Support Queue")


@router.get("/owner/support/{ticket_id}", response_class=HTMLResponse, include_in_schema=False)
def owner_support_ticket(ticket_id: str, request: Request):
    if not owner_session_authorized(request):
        return RedirectResponse("/owner", status_code=303)
    ticket = support.get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(404, "Support ticket not found")
    messages = "".join(f"<div class='card'><div class='eyebrow'>{escape(m['author_type'])} · {escape(m['direction'])}</div><div class='answer'>{escape(m['body'])}</div></div>" for m in ticket["messages"])
    body = f"<div class='top'><div><div class='eyebrow'>Support Ticket</div><h1>{escape(ticket['subject'])}</h1><p class='muted'>{escape(ticket['display_name'])} · {escape(ticket['email'])}</p></div><a class='btn' href='/owner/support'>Back to queue</a></div>{messages}<section class='card'><div class='eyebrow'>Owner response</div><form method='post' action='/owner/support/{escape(ticket_id, quote=True)}/reply'><textarea name='body' required placeholder='Write Mary or Kev support response'></textarea><button class='primary'>Email response</button></form></section>"
    return _page(body, "Owner Support Ticket")


@router.post("/owner/support/{ticket_id}/reply", include_in_schema=False)
def owner_support_reply(ticket_id: str, request: Request, body: str = Form(...)):
    if not owner_session_authorized(request):
        return RedirectResponse("/owner", status_code=303)
    try:
        support.owner_reply(ticket_id, body)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(f"/owner/support/{ticket_id}", status_code=303)
