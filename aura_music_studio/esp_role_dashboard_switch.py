from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from html import escape
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel

from .esp_command_center import esp
from .esp_niche import require_esp_hub_member

router = APIRouter(tags=["ESP Role Dashboard Switch"])
DashboardMode = Literal["creator", "agent"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _role(membership: dict) -> str:
    if membership.get("status") == "owner":
        return "owner"
    return str(membership.get("roles") or "").strip().lower()


def allowed_modes(membership: dict) -> list[str]:
    role = _role(membership)
    if role == "owner":
        return ["creator", "agent"]
    if role == "both":
        return ["creator", "agent"]
    if role == "creator":
        return ["creator"]
    if role == "agent":
        return ["agent"]
    return []


class ViewUpdate(BaseModel):
    mode: DashboardMode


class DashboardPreferenceStore:
    def __init__(self, db_path=None):
        self.db_path = db_path or esp.db_path
        self._init_schema()

    def _connect(self):
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _init_schema(self):
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS esp_dashboard_preferences (
                    user_id TEXT PRIMARY KEY,
                    mode TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                """
            )

    def get(self, user_id: str, membership: dict) -> str:
        allowed = allowed_modes(membership)
        if not allowed:
            raise PermissionError("No ESP dashboard is available to this role")
        with self._connect() as con:
            row = con.execute("SELECT mode FROM esp_dashboard_preferences WHERE user_id=?", (user_id,)).fetchone()
        if row and row["mode"] in allowed:
            return row["mode"]
        return allowed[0]

    def set(self, user_id: str, membership: dict, mode: str) -> str:
        allowed = allowed_modes(membership)
        if mode not in allowed:
            raise PermissionError("That ESP dashboard is not available to this role")
        with self._connect() as con:
            con.execute(
                """INSERT INTO esp_dashboard_preferences(user_id,mode,updated_at) VALUES (?,?,?)
                   ON CONFLICT(user_id) DO UPDATE SET mode=excluded.mode,updated_at=excluded.updated_at""",
                (user_id, mode, _now()),
            )
        return mode


preferences = DashboardPreferenceStore()


def _context(request: Request):
    member, membership = require_esp_hub_member(request)
    modes = allowed_modes(membership)
    if not modes:
        raise HTTPException(403, "No ESP dashboard is available")
    return member, membership, modes


@router.get("/command-center/api/dashboard-view")
def dashboard_view_api(request: Request):
    member, membership, modes = _context(request)
    return {
        "mode": preferences.get(member.user_id, membership),
        "allowed_modes": modes,
        "switch_enabled": len(modes) > 1,
        "esp_role": _role(membership),
        "subscription_grants_views": False,
        "owner_activation_required": True,
    }


@router.put("/command-center/api/dashboard-view")
def set_dashboard_view_api(body: ViewUpdate, request: Request):
    member, membership, modes = _context(request)
    try:
        mode = preferences.set(member.user_id, membership, body.mode)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    return {
        "mode": mode,
        "allowed_modes": modes,
        "switch_enabled": len(modes) > 1,
        "redirect": f"/command-center/{mode}-dashboard",
    }


@router.get("/command-center/dashboard", include_in_schema=False)
def dashboard_router(request: Request):
    member, membership, _modes = _context(request)
    mode = preferences.get(member.user_id, membership)
    return RedirectResponse(f"/command-center/{mode}-dashboard", status_code=303)


CSS = """
:root{--line:#ffffff1f;--muted:#c8bfd2;--gold:#efc66b;--violet:#a26fff}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 88% 0,#42185d,transparent 30%),#07050d;color:#fff;font-family:Inter,system-ui,sans-serif}.wrap{width:min(1160px,calc(100% - 28px));margin:auto;padding:34px 0 60px}.top,.row{display:flex;justify-content:space-between;gap:12px;align-items:flex-start;flex-wrap:wrap}.eyebrow{color:var(--gold);font-size:.72rem;letter-spacing:.14em;text-transform:uppercase;font-weight:950}h1{font-size:clamp(2.5rem,7vw,5rem);letter-spacing:-.05em;margin:.15em 0}.muted{color:var(--muted);line-height:1.55}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}.card{border:1px solid var(--line);border-radius:18px;padding:16px;background:#14101deb;margin:12px 0}.btn{display:inline-block;border:1px solid var(--line);border-radius:10px;padding:9px 12px;background:#ffffff09;color:#fff;text-decoration:none;font-weight:850}.primary{border:0;background:linear-gradient(110deg,var(--gold),var(--violet));color:#160d1d}@media(max-width:760px){.grid{grid-template-columns:1fr}}
"""


def _page(title: str, eyebrow: str, lead: str, cards: list[tuple[str, str, str]], switch_html: str) -> HTMLResponse:
    body = "".join(
        f"<article class='card'><h2>{escape(name)}</h2><p class='muted'>{escape(description)}</p><a class='btn primary' href='{escape(url, quote=True)}'>Open</a></article>"
        for name, description, url in cards
    )
    return HTMLResponse(
        "<!doctype html><html><head><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='noindex,nofollow'>"
        f"<title>{escape(title)}</title><style>{CSS}</style></head><body><main class='wrap'><div class='top'><div>"
        f"<div class='eyebrow'>{escape(eyebrow)}</div><h1>{escape(title)}</h1><p class='muted'>{escape(lead)}</p></div><div>{switch_html}</div></div>"
        f"<section class='grid'>{body}</section><section class='card'><b>Access boundary</b><p class='muted'>These dashboards are unlocked only by ESP roles activated by ownership. Free/Basic/Pro subscription status cannot grant Creator or Agent permissions.</p></section>"
        "</main></body></html>",
        headers={"Cache-Control": "no-store"},
    )


def _switch(modes: list[str], current: str) -> str:
    links = ["<a class='btn' href='/command-center/level-up'>Level Up Hub</a>"]
    if len(modes) > 1:
        other = "agent" if current == "creator" else "creator"
        links.insert(0, f"<a class='btn primary' href='/command-center/switch-to/{other}'>{other.title()} Dashboard</a>")
    return " ".join(links)


@router.get("/command-center/switch-to/{mode}", include_in_schema=False)
def switch_dashboard(mode: str, request: Request):
    member, membership, _modes = _context(request)
    if mode not in {"creator", "agent"}:
        raise HTTPException(404, "ESP dashboard not found")
    try:
        preferences.set(member.user_id, membership, mode)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    return RedirectResponse(f"/command-center/{mode}-dashboard", status_code=303)


@router.get("/command-center/creator-dashboard", response_class=HTMLResponse, include_in_schema=False)
def creator_dashboard(request: Request):
    member, membership, modes = _context(request)
    if "creator" not in modes:
        raise HTTPException(403, "ESP Creator access is required")
    preferences.set(member.user_id, membership, "creator")
    cards = [
        ("My Creator Plan", "Personal goals, action pathway and development planning.", "/command-center/my-plan"),
        ("Creator Progress", "Upload LIVE/video evidence and review Aura progress guidance.", "/command-center/progress"),
        ("Academy & Resources", "Role-gated ESP creator education and niche training.", "/command-center/library"),
        ("Commerce & Shop", "Shop readiness, opportunities and commercial tracking.", "/command-center/commerce"),
        ("Shop Automation", "Authorised Shopify/TikTok Shop/shipping workflow controls where entitled.", "/command-center/shop-automation"),
        ("Social Media Centre", "Content planning, publishing workflows and social intelligence.", "/command-center/social"),
    ]
    return _page(
        "Creator Dashboard",
        "Elevate Souls Productions · Creator View",
        "Your creator-growth workspace. Agent-only recruitment and assigned-creator management tools are not surfaced in this view.",
        cards,
        _switch(modes, "creator"),
    )


@router.get("/command-center/agent-dashboard", response_class=HTMLResponse, include_in_schema=False)
def agent_dashboard(request: Request):
    member, membership, modes = _context(request)
    if "agent" not in modes:
        raise HTTPException(403, "ESP Agent access is required")
    preferences.set(member.user_id, membership, "agent")
    cards = [
        ("Assigned Creator Roster", "Only creators explicitly assigned to this Agent by ESP ownership.", "/command-center/agent/roster"),
        ("Creator Health Queue", "Prioritise creator-support work from authorised ESP data.", "/command-center/agent/health"),
        ("Success Operations", "Check-ins, follow-ups, creator-success pathways and escalation.", "/command-center/agent/operations"),
        ("Backstage Evidence", "Upload and analyse creator-supplied Backstage/Manage Creator evidence without claiming direct access.", "/command-center/agent/backstage-evidence"),
        ("Development Planner", "Human-led 7/30/60/90-day creator mentoring cycles.", "/command-center/agent/development"),
        ("Creator Discovery", "ESP Agent lead discovery and recruitment CRM with validation controls.", "/command-center/agent/discovery"),
    ]
    return _page(
        "Agent Dashboard",
        "Elevate Souls Productions · Mentor View",
        "Your Agent/Mentor workspace. Creator management remains limited to explicit ESP assignments and owner-controlled roles.",
        cards,
        _switch(modes, "agent"),
    )


__all__ = ["router", "DashboardPreferenceStore", "allowed_modes", "preferences"]
