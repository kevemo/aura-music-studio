from __future__ import annotations

import json
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from html import escape
from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from .accounts import AccountStore, _hash_password
from .branding import PRODUCT_FULL_NAME, TAGLINE
from .mailer import DEFAULT_ADMIN_EMAIL, send_email
from .membership import MembershipService

router = APIRouter()
store = AccountStore()
memberships = MembershipService(store)
OWNER_COOKIE = "lss_admin_session"
MEMBER_COOKIE = "lss_session"
VALID_ROLES = {"creator", "agent", "both"}


RESOURCE_CATALOG: dict[str, dict] = {
    "creator-companion": {
        "title": "Creator Companion System",
        "category": "Creator Academy",
        "url": "https://drive.google.com/drive/folders/1qNnnWKbvX7kmb8-VJytOVYFrqRZp1vQ0",
        "roles": {"creator", "agent", "both", "owner"},
        "description": "Creator Academy, welcome material, niche training, campaigns, battle resources, training videos and support systems.",
    },
    "creator-incentives": {
        "title": "ESP Incentives & Recognition",
        "category": "Creator Growth",
        "url": "https://drive.google.com/drive/folders/1mriMXyk_k4AiFbE02GNVuBpsMz4eczPu",
        "roles": {"creator", "agent", "both", "owner"},
        "description": "Current creator incentive programmes, rank/maintenance systems, ambassadors, consistency, recognition and reward structures.",
    },
    "battle-program": {
        "title": "Battle Creator Programme",
        "category": "LIVE Growth",
        "url": "https://drive.google.com/drive/folders/1wZQkNCFadzCCnPoS6ybpgyrvVZCuaJ3q",
        "roles": {"creator", "agent", "both", "owner"},
        "description": "Battle-only creator programme and training pathway.",
    },
    "battle-operations": {
        "title": "Battle Operations & Collaboration",
        "category": "LIVE Operations",
        "url": "https://drive.google.com/drive/folders/1tGwJz9FEHnLNjyGB2y5azwZReB0-JZJx",
        "roles": {"creator", "agent", "both", "owner"},
        "description": "Battle scheduling, collaboration resources, leaderboards and moderation support.",
    },
    "effect-house": {
        "title": "TikTok Effect House Academy",
        "category": "Creation",
        "url": "https://drive.google.com/drive/folders/1PKYk4O5fSNDivCma8AZknnfRXD7UWSa-",
        "roles": {"creator", "agent", "both", "owner"},
        "description": "Effect House introduction, technical guidance, best practices and first-effect training.",
    },
    "agent-apprentice": {
        "title": "ESP Agent Apprentice Programme",
        "category": "Agent Academy",
        "url": "https://drive.google.com/drive/folders/1m6E26n5RX6x4GhwjO6YOMo-uosf5YPgk",
        "roles": {"agent", "both", "owner"},
        "description": "Recruitment foundations, gifting and engagement, LIVE structure, KPI systems, video strategy, CapCut and business-building modules.",
    },
    "agent-master": {
        "title": "ESP Agent & Creator Operations Master",
        "category": "Agent Operations",
        "url": "https://drive.google.com/drive/folders/1WBuYlPpJVbudz9bfaTOotxBYpfmDgJDv",
        "roles": {"agent", "both", "owner"},
        "description": "Agent programme, creator companion, incentives, battle consultants, team roles, Effect House and operational resources.",
    },
    "governance": {
        "title": "Governance, Accountability & Discord Operations",
        "category": "Leadership",
        "url": "https://drive.google.com/drive/folders/1LVGIMjLsQl9bv-yZdyPAxXCVDbuKhkiY",
        "roles": {"agent", "both", "owner"},
        "description": "Governance, onboarding, education, staff duties, culture protection, accountability, global scaling and sustainability systems.",
    },
    "growth-blueprint": {
        "title": "ESP Expansion & Competitive Blueprint",
        "category": "Leadership",
        "url": "https://drive.google.com/drive/folders/1-rB6jqWmUgcOlrZhDe4N9oWjw6S-AMD4",
        "roles": {"agent", "both", "owner"},
        "description": "ESP competitive-gap research, expansion blueprints and build-beyond-the-market strategy.",
    },
}


CSS = """
:root{--bg:#09050f;--panel:#171020;--panel2:#21142e;--gold:#e7bd63;--text:#fff;--muted:#c8bdd2;--line:#473452;--green:#78d99c;--red:#ff93a6;--purple:#b47cff}
*{box-sizing:border-box}body{font-family:Inter,ui-sans-serif,system-ui,sans-serif;background:radial-gradient(circle at top right,#2b123f 0,#09050f 42%);color:var(--text);margin:0;min-height:100vh}.wrap{max-width:1240px;margin:auto;padding:28px}.top{display:flex;align-items:flex-start;justify-content:space-between;gap:18px;flex-wrap:wrap}.brand{color:var(--gold);font-weight:900;letter-spacing:.08em}.muted{color:var(--muted)}.card{background:linear-gradient(145deg,var(--panel),#120b19);border:1px solid var(--line);border-radius:20px;padding:20px;margin:14px 0;box-shadow:0 12px 40px #0005}.grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}.grid2{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.stat{font-size:2rem;font-weight:900;color:var(--gold)}.pill{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:5px 10px;font-size:.8rem;margin:3px}.btn,button{display:inline-block;border:0;border-radius:11px;padding:11px 15px;font-weight:850;cursor:pointer;text-decoration:none;background:var(--gold);color:#1c1024}.secondary{background:#352443;color:#fff}.danger{background:#5b2438;color:#fff}.success{background:#245d3c;color:#fff}input,select,textarea{width:100%;padding:12px;border-radius:11px;border:1px solid var(--line);background:#0e0814;color:#fff;margin:6px 0 14px}label{font-weight:750}.resource{height:100%;display:flex;flex-direction:column}.resource p{flex:1}.bar{height:12px;background:#2a1d31;border-radius:99px;overflow:hidden}.fill{height:100%;background:linear-gradient(90deg,var(--gold),var(--purple))}.revrow{display:grid;grid-template-columns:120px 1fr 80px;gap:12px;align-items:center;margin:8px 0}.flash{border-left:4px solid var(--gold)}table{width:100%;border-collapse:collapse}th,td{padding:10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}small{color:var(--muted)}@media(max-width:850px){.grid,.grid2{grid-template-columns:1fr}.revrow{grid-template-columns:90px 1fr 60px}.wrap{padding:18px}}
"""


def _page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><html><head><meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{escape(title)}</title><style>{CSS}</style></head><body><div class='wrap'>{body}</div></body></html>"
    )


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None = None) -> str:
    return (dt or _now()).isoformat()


def _hash_token(token: str) -> str:
    import hashlib
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _session_token(request: Request) -> str | None:
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.cookies.get(MEMBER_COOKIE)


def _owner_authorized(request: Request) -> bool:
    configured = os.getenv("LSS_ADMIN_KEY") or ""
    supplied = request.cookies.get(OWNER_COOKIE) or ""
    return bool(configured and supplied and secrets.compare_digest(configured, supplied))


def _public_url() -> str:
    return (os.getenv("LSS_PUBLIC_BASE_URL") or os.getenv("LSS_PUBLIC_URL") or "http://127.0.0.1:8000").rstrip("/")


class EspStore:
    def __init__(self, account_store: AccountStore | None = None):
        self.accounts = account_store or store
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
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS esp_memberships (
                    user_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL DEFAULT 'none',
                    roles TEXT NOT NULL DEFAULT '',
                    tiktok_handle TEXT,
                    region TEXT,
                    requested_at TEXT,
                    approved_at TEXT,
                    approved_by TEXT,
                    revoked_at TEXT,
                    revoked_by TEXT,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS esp_access_requests (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    requested_role TEXT NOT NULL,
                    tiktok_handle TEXT,
                    region TEXT,
                    note TEXT,
                    token_hash TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    decided_at TEXT,
                    decided_by TEXT,
                    assigned_role TEXT,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS esp_resource_events (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    resource_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    metadata_json TEXT,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS esp_training_progress (
                    user_id TEXT NOT NULL,
                    resource_id TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'started',
                    percent INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(user_id, resource_id),
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                """
            )

    def membership(self, user_id: str) -> dict | None:
        with self._connect() as con:
            row = con.execute("SELECT * FROM esp_memberships WHERE user_id=?", (user_id,)).fetchone()
        return dict(row) if row else None

    def pending_for_user(self, user_id: str) -> dict | None:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM esp_access_requests WHERE user_id=? AND status='pending' ORDER BY created_at DESC LIMIT 1",
                (user_id,),
            ).fetchone()
        return dict(row) if row else None

    def request_access(self, user_id: str, requested_role: str, tiktok_handle: str, region: str, note: str) -> tuple[dict, str]:
        role = (requested_role or "").strip().lower()
        if role not in VALID_ROLES:
            raise ValueError("Choose Creator, Agent, or Both")
        handle = (tiktok_handle or "").strip().lstrip("@")[:80]
        if len(handle) < 2:
            raise ValueError("Enter your TikTok handle")
        region = (region or "").strip()[:120]
        note = (note or "").strip()[:1500]
        existing = self.membership(user_id)
        if existing and existing.get("status") in {"active", "owner"}:
            raise ValueError("This account already has ESP Command Center access")
        if self.pending_for_user(user_id):
            raise ValueError("An ESP access request is already awaiting review")

        token = secrets.token_urlsafe(32)
        now = _now()
        request_id = uuid4().hex
        with self._connect() as con:
            con.execute(
                """INSERT INTO esp_access_requests
                   (id,user_id,requested_role,tiktok_handle,region,note,token_hash,status,created_at,expires_at)
                   VALUES (?,?,?,?,?,?,?,'pending',?,?)""",
                (request_id, user_id, role, handle, region, note, _hash_token(token), _iso(now), _iso(now + timedelta(days=7))),
            )
            con.execute(
                """INSERT INTO esp_memberships(user_id,status,roles,tiktok_handle,region,requested_at,updated_at)
                   VALUES (?,'pending','',?,?,?,?)
                   ON CONFLICT(user_id) DO UPDATE SET status='pending',tiktok_handle=excluded.tiktok_handle,
                     region=excluded.region,requested_at=excluded.requested_at,updated_at=excluded.updated_at""",
                (user_id, handle, region, _iso(now), _iso(now)),
            )
        return self.request_from_token(token) or {}, token

    def request_from_token(self, token: str) -> dict | None:
        with self._connect() as con:
            row = con.execute(
                """SELECT r.*,u.email,u.display_name FROM esp_access_requests r
                   JOIN users u ON u.id=r.user_id WHERE r.token_hash=?""",
                (_hash_token(token),),
            ).fetchone()
        if not row:
            return None
        item = dict(row)
        item["expired"] = item["expires_at"] <= _iso()
        return item

    def decide(self, token: str, decision: str, assigned_role: str, decided_by: str) -> dict:
        action = (decision or "").strip().lower()
        if action not in {"approve", "reject"}:
            raise ValueError("Decision must be approve or reject")
        role = (assigned_role or "").strip().lower()
        if action == "approve" and role not in VALID_ROLES:
            raise ValueError("Assigned role must be Creator, Agent, or Both")
        now = _iso()
        with self._connect() as con:
            row = con.execute("SELECT * FROM esp_access_requests WHERE token_hash=?", (_hash_token(token),)).fetchone()
            if not row:
                raise ValueError("ESP access request not found")
            if row["status"] != "pending":
                raise ValueError("This ESP access request has already been decided")
            if row["expires_at"] <= now:
                con.execute("UPDATE esp_access_requests SET status='expired',decided_at=? WHERE id=?", (now, row["id"]))
                raise ValueError("This ESP approval link has expired")
            user = con.execute("SELECT * FROM users WHERE id=?", (row["user_id"],)).fetchone()
            if not user:
                raise ValueError("User no longer exists")

            if action == "approve":
                con.execute(
                    """UPDATE esp_access_requests SET status='approved',decided_at=?,decided_by=?,assigned_role=? WHERE id=?""",
                    (now, decided_by[:120], role, row["id"]),
                )
                con.execute(
                    """UPDATE esp_memberships SET status='active',roles=?,approved_at=?,approved_by=?,revoked_at=NULL,
                       revoked_by=NULL,updated_at=? WHERE user_id=?""",
                    (role, now, decided_by[:120], now, row["user_id"]),
                )
                # ESP membership includes the Base £/$4.99 entitlement at no charge. Existing active Pro remains Pro.
                if user["plan_id"] != "pro":
                    con.execute(
                        """UPDATE users SET status='active',plan_id='base',requested_plan_id='base',billing_status='esp_comped'
                           WHERE id=?""",
                        (row["user_id"],),
                    )
            else:
                con.execute(
                    "UPDATE esp_access_requests SET status='rejected',decided_at=?,decided_by=? WHERE id=?",
                    (now, decided_by[:120], row["id"]),
                )
                con.execute(
                    "UPDATE esp_memberships SET status='rejected',roles='',updated_at=? WHERE user_id=?",
                    (now, row["user_id"]),
                )
        return self.accounts.get_user(row["user_id"]) or {}

    def set_role(self, user_id: str, role: str, actor: str) -> None:
        role = role.strip().lower()
        if role not in VALID_ROLES:
            raise ValueError("Role must be creator, agent, or both")
        now = _iso()
        with self._connect() as con:
            current = con.execute("SELECT status FROM esp_memberships WHERE user_id=?", (user_id,)).fetchone()
            if not current or current["status"] not in {"active", "owner"}:
                raise ValueError("User does not have active ESP access")
            con.execute(
                "UPDATE esp_memberships SET roles=?,approved_by=?,updated_at=? WHERE user_id=?",
                (role, actor[:120], now, user_id),
            )

    def revoke(self, user_id: str, actor: str) -> None:
        now = _iso()
        with self._connect() as con:
            current = con.execute("SELECT status FROM esp_memberships WHERE user_id=?", (user_id,)).fetchone()
            if not current:
                raise ValueError("ESP membership not found")
            con.execute(
                "UPDATE esp_memberships SET status='revoked',roles='',revoked_at=?,revoked_by=?,updated_at=? WHERE user_id=?",
                (now, actor[:120], now, user_id),
            )
            user = con.execute("SELECT plan_id,billing_status FROM users WHERE id=?", (user_id,)).fetchone()
            if user and user["billing_status"] == "esp_comped":
                con.execute(
                    "UPDATE users SET plan_id='free',requested_plan_id='free',billing_status='not_required' WHERE id=?",
                    (user_id,),
                )

    def log_resource(self, user_id: str, resource_id: str, event_type: str, metadata: dict | None = None) -> None:
        with self._connect() as con:
            con.execute(
                "INSERT INTO esp_resource_events(id,user_id,resource_id,event_type,occurred_at,metadata_json) VALUES (?,?,?,?,?,?)",
                (uuid4().hex, user_id, resource_id, event_type, _iso(), json.dumps(metadata or {}, sort_keys=True)),
            )

    def set_progress(self, user_id: str, resource_id: str, percent: int) -> None:
        pct = max(0, min(100, int(percent)))
        status = "complete" if pct >= 100 else "in_progress" if pct > 0 else "not_started"
        with self._connect() as con:
            con.execute(
                """INSERT INTO esp_training_progress(user_id,resource_id,status,percent,updated_at) VALUES (?,?,?,?,?)
                   ON CONFLICT(user_id,resource_id) DO UPDATE SET status=excluded.status,percent=excluded.percent,updated_at=excluded.updated_at""",
                (user_id, resource_id, status, pct, _iso()),
            )

    def progress(self, user_id: str) -> dict[str, int]:
        with self._connect() as con:
            rows = con.execute("SELECT resource_id,percent FROM esp_training_progress WHERE user_id=?", (user_id,)).fetchall()
        return {r["resource_id"]: int(r["percent"]) for r in rows}

    def pending(self) -> list[dict]:
        with self._connect() as con:
            rows = con.execute(
                """SELECT r.*,u.email,u.display_name FROM esp_access_requests r
                   JOIN users u ON u.id=r.user_id WHERE r.status='pending' ORDER BY r.created_at ASC"""
            ).fetchall()
        return [dict(r) for r in rows]

    def members(self) -> list[dict]:
        with self._connect() as con:
            rows = con.execute(
                """SELECT e.*,u.email,u.display_name,u.plan_id,u.billing_status FROM esp_memberships e
                   JOIN users u ON u.id=e.user_id WHERE e.status IN ('active','owner') ORDER BY u.display_name COLLATE NOCASE"""
            ).fetchall()
        return [dict(r) for r in rows]

    def dashboard_stats(self) -> dict:
        with self._connect() as con:
            users = con.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]
            active = con.execute("SELECT COUNT(*) n FROM esp_memberships WHERE status IN ('active','owner')").fetchone()["n"]
            creators = con.execute("SELECT COUNT(*) n FROM esp_memberships WHERE status='active' AND roles='creator'").fetchone()["n"]
            agents = con.execute("SELECT COUNT(*) n FROM esp_memberships WHERE status='active' AND roles='agent'").fetchone()["n"]
            both = con.execute("SELECT COUNT(*) n FROM esp_memberships WHERE status='active' AND roles='both'").fetchone()["n"]
            pending = con.execute("SELECT COUNT(*) n FROM esp_access_requests WHERE status='pending'").fetchone()["n"]
            comped = con.execute("SELECT COUNT(*) n FROM users WHERE billing_status='esp_comped'").fetchone()["n"]
            pro = con.execute("SELECT COUNT(*) n FROM users WHERE status='active' AND plan_id='pro'").fetchone()["n"]
            since = _iso(_now() - timedelta(days=30))
            resource_events = con.execute("SELECT COUNT(*) n FROM esp_resource_events WHERE occurred_at>=?", (since,)).fetchone()["n"]
            studio_events = con.execute("SELECT COUNT(*) n FROM usage_events WHERE occurred_at>=?", (since,)).fetchone()["n"]
            revenue_rows = con.execute(
                """SELECT substr(verified_at,1,7) month,SUM(CAST(amount_usd AS REAL)) amount
                   FROM subscription_payments GROUP BY substr(verified_at,1,7) ORDER BY month DESC LIMIT 12"""
            ).fetchall()
        revenue = [{"month": r["month"], "amount": float(r["amount"] or 0)} for r in reversed(revenue_rows)]
        return {
            "users": users, "active": active, "creators": creators, "agents": agents, "both": both,
            "pending": pending, "comped": comped, "pro": pro, "resource_events": resource_events,
            "studio_events": studio_events, "revenue": revenue,
        }

    def ensure_owner_user(self) -> dict:
        email = (os.getenv("LSS_OWNER_EMAIL") or DEFAULT_ADMIN_EMAIL).strip().lower()
        user = self.accounts.get_user_by_email(email)
        now = _iso()
        with self._connect() as con:
            if not user:
                salt, digest = _hash_password(secrets.token_urlsafe(40))
                user_id = uuid4().hex
                con.execute(
                    """INSERT INTO users(id,email,display_name,password_salt,password_hash,status,plan_id,requested_plan_id,billing_status,created_at,approved_at,approved_by)
                       VALUES (?,?,?,?,?,'active','pro','pro','owner_comped',?,?,?)""",
                    (user_id, email, "ESP Owners", salt, digest, now, now, "ESP owner portal"),
                )
            else:
                user_id = user["id"]
                con.execute(
                    "UPDATE users SET status='active',plan_id='pro',requested_plan_id='pro',billing_status='owner_comped' WHERE id=?",
                    (user_id,),
                )
            con.execute(
                """INSERT INTO esp_memberships(user_id,status,roles,tiktok_handle,region,approved_at,approved_by,updated_at)
                   VALUES (?,'owner','owner','','Global',?,'ESP owner portal',?)
                   ON CONFLICT(user_id) DO UPDATE SET status='owner',roles='owner',approved_at=COALESCE(approved_at,excluded.approved_at),
                     approved_by='ESP owner portal',updated_at=excluded.updated_at""",
                (user_id, now, now),
            )
        return self.accounts.get_user(user_id) or {}


esp = EspStore(store)


def _member_or_401(request: Request, *, require_active: bool = True):
    try:
        return memberships.from_session(_session_token(request), require_active=require_active)
    except PermissionError as exc:
        raise HTTPException(401, str(exc)) from exc


def _roles(value: str | None) -> set[str]:
    if value == "both":
        return {"creator", "agent", "both"}
    return {value or ""}


def _resource_allowed(resource: dict, membership: dict) -> bool:
    role = membership.get("roles") or ""
    return bool(_roles(role) & set(resource["roles"])) or role == "owner"


def _resource_cards(member_id: str, membership: dict, wanted: str) -> str:
    progress = esp.progress(member_id)
    cards = []
    for rid, resource in RESOURCE_CATALOG.items():
        if not _resource_allowed(resource, membership):
            continue
        is_agent = bool({"agent", "both", "owner"} & set(resource["roles"])) and "creator" not in resource["roles"]
        if wanted == "creator" and is_agent:
            continue
        if wanted == "agent" and not is_agent:
            continue
        pct = progress.get(rid, 0)
        cards.append(
            f"""<div class='card resource'><div><span class='pill'>{escape(resource['category'])}</span></div>
            <h3>{escape(resource['title'])}</h3><p class='muted'>{escape(resource['description'])}</p>
            <div class='bar'><div class='fill' style='width:{pct}%'></div></div><small>{pct}% marked complete</small>
            <div style='margin-top:14px'><a class='btn' href='/command-center/open/{quote(rid)}'>Open training</a></div>
            <form method='post' action='/command-center/progress/{quote(rid)}' style='margin-top:10px'>
            <select name='percent'><option value='0'>Not started</option><option value='25'>25%</option><option value='50'>50%</option><option value='75'>75%</option><option value='100'>Complete</option></select>
            <button class='secondary'>Update progress</button></form></div>"""
        )
    return "".join(cards) or "<div class='card muted'>No resources are assigned to this role.</div>"


@router.get("/auth/esp", response_class=HTMLResponse)
def esp_landing():
    return _page(
        "ESP Command Center",
        f"""<div class='top'><div><div class='brand'>ELEVATE SOULS PRODUCTIONS</div><h1>ESP Command Center</h1>
        <p class='muted'>Approved Creator Network members receive the Base studio entitlement at no charge while their ESP access remains active. Pro remains the full paid studio upgrade.</p></div></div>
        <div class='grid'><div class='card'><h2>Creator</h2><p>Creator Academy, LIVE growth, incentives, battle systems, Effect House and music creation.</p></div>
        <div class='card'><h2>Agent</h2><p>Agent Academy, recruitment, KPI systems, governance, accountability, creator management and leadership resources.</p></div>
        <div class='card'><h2>Both</h2><p>One account with both permission sets. ESP owners can change roles at any time.</p></div></div>
        <div class='card flash'><h2>Access is approval-gated</h2><p class='muted'>Create/sign in to your Live Sound Studio account first. From the Command Center you can request Creator, Agent, or Both access. Kev or Mary reviews the request before ESP-only material becomes visible.</p>
        <a class='btn' href='/signin'>Sign in</a> <a class='btn secondary' href='/signup'>Create studio account</a></div>""",
    )


@router.get("/command-center", response_class=HTMLResponse)
def command_center(request: Request):
    member = _member_or_401(request)
    esp_member = esp.membership(member.user_id)
    if not esp_member or esp_member.get("status") not in {"active", "owner"}:
        pending = esp.pending_for_user(member.user_id)
        pending_html = "<p class='muted'>Your request is awaiting owner review.</p>" if pending else ""
        return _page(
            "Request ESP Access",
            f"""<div class='brand'>ESP COMMAND CENTER</div><h1>Request member access</h1>
            <div class='card'><p>You are signed in as <b>{escape(member.user.get('display_name') or member.user.get('email') or '')}</b>.</p>
            {pending_html}
            <form method='post' action='/auth/esp/request'>
            <label>ESP role requested</label><select name='requested_role' {'disabled' if pending else ''}><option value='creator'>Creator</option><option value='agent'>Agent</option><option value='both'>Creator + Agent</option></select>
            <label>TikTok handle</label><input name='tiktok_handle' placeholder='@yourhandle' required {'disabled' if pending else ''}>
            <label>Region</label><input name='region' placeholder='UK+, USA/Canada, LATAM, AU/NZ' {'disabled' if pending else ''}>
            <label>Optional note</label><textarea name='note' rows='4' placeholder='Anything Kev or Mary should know' {'disabled' if pending else ''}></textarea>
            <button {'disabled' if pending else ''}>Send request to ESP owners</button></form></div>""",
        )

    role = esp_member.get("roles") or ""
    plan = member.plan.id
    plan_copy = "Base included with ESP" if plan == "base" and member.user.get("billing_status") == "esp_comped" else f"{member.plan.name} studio"
    creator_html = _resource_cards(member.user_id, esp_member, "creator")
    agent_html = _resource_cards(member.user_id, esp_member, "agent") if role in {"agent", "both", "owner"} else ""
    return _page(
        "ESP Command Center",
        f"""<div class='top'><div><div class='brand'>ELEVATE SOULS PRODUCTIONS</div><h1>ESP Command Center</h1>
        <p class='muted'>Welcome, {escape(member.user.get('display_name') or '')}. Your current ESP role is <b>{escape(role.upper())}</b>.</p></div>
        <div><span class='pill'>{escape(plan_copy)}</span> <a class='btn' href='/studio'>Open Live Sound Studio</a></div></div>
        <div class='grid'><div class='card'><div class='stat'>{escape(role.upper())}</div><small>ESP permission set</small></div>
        <div class='card'><div class='stat'>{'£9.99 Pro' if plan == 'pro' else '£4.99 Base'}</div><small>{'Full feature studio unlocked' if plan == 'pro' else 'Included free while ESP membership is active'}</small></div>
        <div class='card'><div class='stat'>{len([r for r in RESOURCE_CATALOG.values() if _resource_allowed(r, esp_member)])}</div><small>Role-gated resource areas</small></div></div>
        <h2>Creator Command Center</h2><div class='grid'>{creator_html}</div>
        {f"<h2>Agent Command Center</h2><div class='grid'>{agent_html}</div>" if agent_html else ''}
        <div class='card'><h2>Creation ecosystem</h2><p class='muted'>Music creation stays inside the Live Sound Studio. ESP-only education and operating systems remain here behind role gates, so ordinary studio customers cannot access creator-network training or internal operations.</p>
        <a class='btn' href='/studio'>Music creation</a> <a class='btn secondary' href='/pricing'>Upgrade to Pro</a></div>""",
    )


@router.post("/auth/esp/request")
def request_esp_access(
    request: Request,
    requested_role: str = Form(...),
    tiktok_handle: str = Form(...),
    region: str = Form(""),
    note: str = Form(""),
):
    member = _member_or_401(request)
    try:
        item, token = esp.request_access(member.user_id, requested_role, tiktok_handle, region, note)
    except ValueError as exc:
        return _page("ESP Request", f"<div class='card'><h1>ESP access request</h1><p>{escape(str(exc))}</p><a class='btn' href='/command-center'>Back</a></div>")
    admin_email = (os.getenv("LSS_MEMBERSHIP_APPROVAL_EMAIL") or os.getenv("LSS_ADMIN_APPROVAL_EMAIL") or DEFAULT_ADMIN_EMAIL).strip()
    review_url = f"{_public_url()}/auth/esp/review?token={quote(token)}"
    body = f"""A new ESP Command Center access request needs review.

Name: {member.user.get('display_name')}
Email: {member.user.get('email')}
TikTok: @{item.get('tiktok_handle') or tiktok_handle.lstrip('@')}
Requested role: {requested_role.upper()}
Region: {region or 'Not supplied'}
Note: {note or 'None'}

Approve, reject, or change the assigned role:
{review_url}

This link is single-use and expires automatically.
"""
    send_email(admin_email, f"ESP Command Center access request — {member.user.get('display_name')}", body)
    return RedirectResponse("/command-center", status_code=303)


@router.get("/auth/esp/review", response_class=HTMLResponse)
def review_esp_access(token: str):
    item = esp.request_from_token(token)
    if not item:
        return _page("ESP Review", "<div class='card'><h1>Request not found</h1></div>")
    if item.get("expired"):
        return _page("ESP Review", "<div class='card'><h1>This approval link has expired.</h1></div>")
    if item.get("status") != "pending":
        return _page("ESP Review", f"<div class='card'><h1>This request is already {escape(item['status'])}.</h1></div>")
    safe_token = escape(token, quote=True)
    return _page(
        "ESP Access Review",
        f"""<div class='brand'>ESP OWNER REVIEW</div><h1>Command Center access request</h1>
        <div class='card'><p><b>{escape(item['display_name'])}</b><br>{escape(item['email'])}<br>@{escape(item.get('tiktok_handle') or '')}</p>
        <p>Requested: <b>{escape(item['requested_role'].upper())}</b><br>Region: {escape(item.get('region') or 'Not supplied')}</p>
        <p class='muted'>{escape(item.get('note') or '')}</p>
        <form method='post' action='/auth/esp/decision'><input type='hidden' name='token' value='{safe_token}'>
        <label>Assigned role</label><select name='assigned_role'><option value='creator'>Creator</option><option value='agent'>Agent</option><option value='both' {'selected' if item['requested_role']=='both' else ''}>Creator + Agent</option></select>
        <label>Reviewed by</label><input name='decided_by' placeholder='Kev or Mary' required>
        <button name='decision' value='approve'>Approve + grant Base free</button> <button class='danger' name='decision' value='reject'>Reject</button></form></div>""",
    )


@router.post("/auth/esp/decision", response_class=HTMLResponse)
def decide_esp_access(
    token: str = Form(...),
    decision: str = Form(...),
    assigned_role: str = Form("creator"),
    decided_by: str = Form(...),
):
    before = esp.request_from_token(token)
    if not before:
        raise HTTPException(404, "ESP access request not found")
    try:
        user = esp.decide(token, decision, assigned_role, decided_by)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    approved = decision.strip().lower() == "approve"
    if approved:
        subject = "Your ESP Command Center access is approved"
        body = f"Hello {before['display_name']},\n\nYour ESP Command Center access is approved as {assigned_role.upper()}. Your Live Sound Studio Base entitlement is included at no charge while your ESP membership remains active. Pro remains the paid full-feature upgrade.\n\nSign in and open the Command Center: {_public_url()}/command-center\n"
    else:
        subject = "Update on your ESP Command Center request"
        body = f"Hello {before['display_name']},\n\nYour ESP Command Center request was not approved at this time.\n"
    send_email(before["email"], subject, body)
    return _page(
        "ESP Decision",
        f"<div class='card'><h1>{'Approved' if approved else 'Rejected'}</h1><p>{escape(before['display_name'])} — {escape(user.get('email') or before['email'])}</p><a class='btn' href='/owner/esp'>Open owner ESP dashboard</a></div>",
    )


@router.get("/command-center/open/{resource_id}")
def open_resource(resource_id: str, request: Request):
    member = _member_or_401(request)
    esp_member = esp.membership(member.user_id)
    resource = RESOURCE_CATALOG.get(resource_id)
    if not resource or not esp_member or esp_member.get("status") not in {"active", "owner"} or not _resource_allowed(resource, esp_member):
        raise HTTPException(403, "This ESP resource is not assigned to your role")
    esp.log_resource(member.user_id, resource_id, "open")
    return RedirectResponse(resource["url"], status_code=303)


@router.post("/command-center/progress/{resource_id}")
def update_progress(resource_id: str, request: Request, percent: int = Form(...)):
    member = _member_or_401(request)
    esp_member = esp.membership(member.user_id)
    resource = RESOURCE_CATALOG.get(resource_id)
    if not resource or not esp_member or not _resource_allowed(resource, esp_member):
        raise HTTPException(403, "This ESP resource is not assigned to your role")
    esp.set_progress(member.user_id, resource_id, percent)
    esp.log_resource(member.user_id, resource_id, "progress", {"percent": percent})
    return RedirectResponse("/command-center", status_code=303)


@router.get("/owner/esp", response_class=HTMLResponse)
def owner_esp_dashboard(request: Request):
    if not _owner_authorized(request):
        return RedirectResponse("/owner", status_code=303)
    stats = esp.dashboard_stats()
    pending = esp.pending()
    active_members = esp.members()
    pending_html = "".join(
        f"""<div class='card'><b>{escape(x['display_name'])}</b> <span class='pill'>{escape(x['requested_role'].upper())}</span><br><small>{escape(x['email'])} · @{escape(x.get('tiktok_handle') or '')} · {escape(x.get('region') or '')}</small>
        <p class='muted'>Open the single-use review link from the approval email for the full approve/reject workflow.</p></div>""" for x in pending
    ) or "<div class='card muted'>No pending ESP access requests.</div>"
    member_rows = "".join(
        f"""<tr><td><b>{escape(x['display_name'])}</b><br><small>{escape(x['email'])}</small></td><td>{escape((x['roles'] or '').upper())}</td><td>{escape((x['plan_id'] or '').upper())}<br><small>{escape(x.get('billing_status') or '')}</small></td><td>
        <form method='post' action='/owner/esp/role'><input type='hidden' name='user_id' value='{escape(x['user_id'], quote=True)}'><select name='role'><option value='creator'>Creator</option><option value='agent'>Agent</option><option value='both'>Both</option></select><input name='actor' value='ESP Owner'><button class='secondary'>Change</button></form>
        <form method='post' action='/owner/esp/revoke'><input type='hidden' name='user_id' value='{escape(x['user_id'], quote=True)}'><input type='hidden' name='actor' value='ESP Owner'><button class='danger'>Remove ESP access</button></form></td></tr>""" for x in active_members if x.get("status") != "owner"
    ) or "<tr><td colspan='4' class='muted'>No active ESP members yet.</td></tr>"
    revenue = stats["revenue"]
    max_rev = max([x["amount"] for x in revenue], default=1.0) or 1.0
    revenue_html = "".join(
        f"<div class='revrow'><small>{escape(x['month'])}</small><div class='bar'><div class='fill' style='width:{min(100,(x['amount']/max_rev)*100):.1f}%'></div></div><b>${x['amount']:.2f}</b></div>" for x in revenue
    ) or "<p class='muted'>No verified paid subscription revenue recorded yet.</p>"
    return _page(
        "ESP Owner Command Center",
        f"""<div class='top'><div><div class='brand'>ESP OWNER CONTROL</div><h1>Command Center Administration</h1><p class='muted'>Members, roles, usage, training engagement and studio revenue in one owner view.</p></div>
        <div><a class='btn' href='/owner/esp/creation-centre'>Private Creation Centre</a> <a class='btn secondary' href='/owner/dashboard'>Studio membership admin</a></div></div>
        <div class='grid'><div class='card'><div class='stat'>{stats['active']}</div><small>Active ESP members</small></div><div class='card'><div class='stat'>{stats['pending']}</div><small>Pending ESP requests</small></div><div class='card'><div class='stat'>{stats['users']}</div><small>Total studio accounts</small></div>
        <div class='card'><div class='stat'>{stats['creators']}</div><small>Creator-only</small></div><div class='card'><div class='stat'>{stats['agents']}</div><small>Agent-only</small></div><div class='card'><div class='stat'>{stats['both']}</div><small>Creator + Agent</small></div>
        <div class='card'><div class='stat'>{stats['comped']}</div><small>ESP Base entitlements</small></div><div class='card'><div class='stat'>{stats['pro']}</div><small>Active Pro studio accounts</small></div><div class='card'><div class='stat'>{stats['resource_events'] + stats['studio_events']}</div><small>Tracked usage events (30 days)</small></div></div>
        <h2>Revenue history</h2><div class='card'>{revenue_html}</div>
        <h2>Pending ESP access</h2>{pending_html}
        <h2>Active ESP members</h2><div class='card' style='overflow:auto'><table><thead><tr><th>Member</th><th>Role</th><th>Studio</th><th>Owner controls</th></tr></thead><tbody>{member_rows}</tbody></table></div>""",
    )


@router.post("/owner/esp/role")
def owner_change_role(request: Request, user_id: str = Form(...), role: str = Form(...), actor: str = Form("ESP Owner")):
    if not _owner_authorized(request):
        return RedirectResponse("/owner", status_code=303)
    try:
        esp.set_role(user_id, role, actor)
    except ValueError as exc:
        return _page("ESP Role", f"<div class='card'><h1>Role update failed</h1><p>{escape(str(exc))}</p><a class='btn' href='/owner/esp'>Back</a></div>")
    return RedirectResponse("/owner/esp", status_code=303)


@router.post("/owner/esp/revoke")
def owner_revoke(request: Request, user_id: str = Form(...), actor: str = Form("ESP Owner")):
    if not _owner_authorized(request):
        return RedirectResponse("/owner", status_code=303)
    try:
        esp.revoke(user_id, actor)
    except ValueError as exc:
        return _page("ESP Revoke", f"<div class='card'><h1>Removal failed</h1><p>{escape(str(exc))}</p><a class='btn' href='/owner/esp'>Back</a></div>")
    return RedirectResponse("/owner/esp", status_code=303)


@router.get("/owner/esp/creation-centre", response_class=HTMLResponse)
def owner_creation_centre(request: Request):
    if not _owner_authorized(request):
        return RedirectResponse("/owner", status_code=303)
    return _page(
        "ESP Private Creation Centre",
        f"""<div class='brand'>OWNER ONLY</div><h1>ESP Private Creation Centre</h1>
        <div class='grid'><div class='card'><h2>Live Sound Studio — Owner Pro</h2><p class='muted'>Enter the production studio with an owner-comped Pro entitlement. This does not expose owner access to ordinary members.</p><form method='post' action='/owner/esp/enter-studio'><button>Enter Owner Pro Studio</button></form></div>
        <div class='card'><h2>Creator & Agent Operations</h2><p class='muted'>Return to the owner command center to manage approvals, roles, usage and access.</p><a class='btn secondary' href='/owner/esp'>Open owner dashboard</a></div>
        <div class='card'><h2>System identity</h2><p>{escape(PRODUCT_FULL_NAME)}</p><p class='muted'>{escape(TAGLINE)}</p></div></div>
        <div class='card'><h2>Owner security boundary</h2><p class='muted'>The creation centre is authorized by the owner admin session. It creates/uses a separate internal owner studio identity with Pro entitlements, so no Gmail password or owner password is stored in source code.</p></div>""",
    )


@router.post("/owner/esp/enter-studio")
def owner_enter_studio(request: Request):
    if not _owner_authorized(request):
        return RedirectResponse("/owner", status_code=303)
    user = esp.ensure_owner_user()
    token = store.create_session(user["id"])
    response = RedirectResponse("/studio", status_code=303)
    response.set_cookie(
        MEMBER_COOKIE,
        token,
        max_age=30 * 24 * 60 * 60,
        httponly=True,
        secure=(os.getenv("LSS_COOKIE_SECURE", "true").lower() == "true"),
        samesite="lax",
    )
    return response
