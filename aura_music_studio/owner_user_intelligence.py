from __future__ import annotations

import json
import os
import re
from html import escape
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from .esp_owner_network_intelligence import router as esp_owner_network_intelligence_router
from .owner_identity import owner_session_authorized, owner_theme, request_owner_persona
from .owner_user_control import OwnerUserControl
from .owner_user_directory import user_detail as base_user_detail

router = APIRouter(tags=["Owner User Intelligence"])
router.include_router(esp_owner_network_intelligence_router)
control = OwnerUserControl()
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")


def _social_summary(user_id: str) -> dict:
    """Read one member's isolated Social House data without changing request identity."""
    if not _SAFE_ID.fullmatch(user_id or ""):
        return {"spaces": 0, "content": 0, "tasks": 0, "campaigns": 0, "pending_approval": 0, "published": 0, "activity": 0}
    root = Path(os.getenv("AURA_SOCIAL_ROOT", "data/social")).resolve() / user_id
    index_path = root / "index.json"
    result = {"spaces": 0, "content": 0, "tasks": 0, "campaigns": 0, "pending_approval": 0, "published": 0, "activity": 0, "latest_activity": None}
    if not index_path.is_file():
        return result
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except Exception:
        return result
    spaces = list(index.get("spaces") or [])
    result["spaces"] = len(spaces)
    latest = ""
    for row in spaces:
        space_id = str(row.get("id") or "")
        if not _SAFE_ID.fullmatch(space_id):
            continue
        path = (root / f"{space_id}.json").resolve()
        if root not in path.parents or not path.is_file():
            continue
        try:
            house = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        content = list(house.get("content") or [])
        result["content"] += len(content)
        result["tasks"] += len(house.get("tasks") or [])
        result["campaigns"] += len(house.get("projects") or [])
        result["activity"] += len(house.get("activity") or [])
        result["pending_approval"] += sum(1 for item in content if item.get("status") == "pending_approval")
        result["published"] += sum(1 for item in content if item.get("status") == "published")
        candidate = str(house.get("updated_at") or "")
        if candidate > latest:
            latest = candidate
    result["latest_activity"] = latest or None
    return result


def _page(request: Request, title: str, body: str) -> HTMLResponse:
    persona = request_owner_persona(request)
    theme = owner_theme(persona)
    return HTMLResponse(
        f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='noindex,nofollow'><title>{escape(title)}</title><style>
:root{{--accent:{theme.accent};--secondary:{theme.secondary};--line:#ffffff1d;--muted:#c7bfd2;--good:#74dda0;--warn:#ffd17b}}*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 90% 0,var(--secondary),transparent 30%),#07050c;color:#fff;font-family:Inter,ui-sans-serif,system-ui,sans-serif}}a{{color:inherit;text-decoration:none}}.wrap{{width:min(1280px,calc(100% - 28px));margin:auto;padding:34px 0 60px}}.top{{display:flex;justify-content:space-between;gap:10px;align-items:flex-start;flex-wrap:wrap}}.eyebrow{{color:var(--accent);text-transform:uppercase;letter-spacing:.14em;font-size:.7rem;font-weight:950}}h1{{font-size:clamp(2.6rem,6vw,5rem);letter-spacing:-.055em;line-height:.95;margin:.15em 0}}.muted{{color:var(--muted);line-height:1.55}}.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:9px;margin:14px 0}}.card,.metric{{border:1px solid var(--line);border-radius:17px;background:#15101deb;padding:15px}}.card{{margin:12px 0}}.metric b{{display:block;font-size:1.5rem;margin-top:4px}}.pill{{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:4px 7px;font-size:.7rem;margin:2px}}.btn{{display:inline-block;border:1px solid var(--line);border-radius:10px;padding:9px 11px;background:#ffffff08;font-weight:850}}.primary{{border:0;background:linear-gradient(110deg,var(--accent),var(--secondary));color:#150b18}}.two{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}ul{{line-height:1.6}}@media(max-width:850px){{.grid{{grid-template-columns:1fr 1fr}}.two{{grid-template-columns:1fr}}}}@media(max-width:520px){{.grid{{grid-template-columns:1fr}}}}
</style></head><body><main class='wrap'>{body}</main></body></html>"""
    )


@router.get("/owner/users/{user_id}/intelligence", response_class=HTMLResponse, include_in_schema=False)
def owner_user_intelligence(user_id: str, request: Request):
    if not owner_session_authorized(request):
        return RedirectResponse("/owner", status_code=303)
    data = control.detail(user_id)
    if not data:
        return _page(request, "User not found", "<div class='card'><h1>User not found</h1><a class='btn' href='/owner/users'>Back</a></div>")
    user = data["user"]
    membership = data.get("esp") or {}
    niche = data.get("niche") or {}
    owner_profile = data.get("owner_profile") or {}
    performance = data.get("performance") or {}
    training = data.get("training") or {}
    social = _social_summary(user_id)
    usage_total = sum(int(row.get("n") or 0) for row in data.get("usage", []))
    avg_training = round(sum(int(v or 0) for v in training.values()) / len(training)) if training else 0
    role = "Regular"
    if membership.get("status") == "owner":
        role = "Owner"
    elif membership.get("status") == "active":
        role = (membership.get("roles") or "ESP member").replace("both", "Creator + Agent").title()
    elif membership.get("status"):
        role = str(membership.get("status")).title()
    usage_html = "".join(f"<span class='pill'>{escape(str(row.get('event_type') or '').replace('_',' ').title())}: {int(row.get('n') or 0)}</span>" for row in data.get("usage", [])) or "<span class='muted'>No creative activity recorded.</span>"
    requests = data.get("esp_requests") or []
    body = f"""<div class='top'><div><div class='eyebrow'>Mary / Kev · User Intelligence</div><h1>{escape(user.get('display_name') or user.get('email') or 'Member')}</h1><p class='muted'>{escape(user.get('email') or '')}</p></div><div><a class='btn primary' href='/owner/esp-intelligence'>ESP Network Intelligence</a> <a class='btn' href='/owner/users/{quote(user_id, safe='')}'>Controls</a> <a class='btn' href='/owner/audit?user_id={quote(user_id, safe='')}'>Audit</a> <a class='btn' href='/owner/users'>Directory</a></div></div>
<section class='grid'><div class='metric'><span class='muted'>Creative plan</span><b>{escape(str(user.get('plan_id') or 'free').upper())}</b><small>{escape(str(user.get('billing_status') or ''))}</small></div><div class='metric'><span class='muted'>ESP authorization</span><b>{escape(role)}</b><small>{escape(str(membership.get('region') or ''))}</small></div><div class='metric'><span class='muted'>Creative activity</span><b>{usage_total}</b><small>{int(data.get('projects') or 0)} projects</small></div><div class='metric'><span class='muted'>Creator progress</span><b>{int(performance.get('total') or 0)}</b><small>{avg_training}% avg training</small></div></section>
<section class='two'><div class='card'><div class='eyebrow'>Creator identity</div><h2>{escape(str((niche.get('catalog') or {}).get('title') or niche.get('niche') or 'Niche not selected'))}</h2><p class='muted'>Sub-niche: {escape(str(niche.get('sub_niche') or '—'))}<br>Mentor: {escape(str(owner_profile.get('mentor') or '—'))}<br>Sub-level: {escape(str(owner_profile.get('sub_level') or '—'))}<br>Network state: {escape(str(niche.get('network_status') or '—'))}</p><div>{''.join(f"<span class='pill'>{escape(str(item))}</span>" for item in (owner_profile.get('categories') or []))}</div></div><div class='card'><div class='eyebrow'>ESP access requests</div><h2>{len(requests)} recorded</h2><p class='muted'>Current membership state: {escape(str(membership.get('status') or 'none'))}. Subscription and ESP authorization are displayed as separate dimensions.</p></div></section>
<section class='card'><div class='eyebrow'>Creative usage</div><h2>Creation & tool activity</h2>{usage_html}</section>
<section class='card'><div class='eyebrow'>Social Media Centre</div><div class='grid'><div class='metric'><span class='muted'>Social Houses</span><b>{social['spaces']}</b></div><div class='metric'><span class='muted'>Content items</span><b>{social['content']}</b></div><div class='metric'><span class='muted'>Campaigns / tasks</span><b>{social['campaigns']} / {social['tasks']}</b></div><div class='metric'><span class='muted'>Approval / published</span><b>{social['pending_approval']} / {social['published']}</b></div></div><p class='muted'>Activity events: {social['activity']} · Latest Social House update: {escape(str(social.get('latest_activity') or '—'))}. This owner view reads operational metadata only and does not expose OAuth credentials or external account tokens.</p></section>"""
    return _page(request, f"User Intelligence — {user.get('display_name') or user.get('email')}", body)


@router.get("/owner/users/{user_id}", response_class=HTMLResponse, include_in_schema=False)
def user_detail_with_intelligence(user_id: str, request: Request, message: str = ""):
    response = base_user_detail(user_id, request, message)
    if not isinstance(response, Response) or not getattr(response, "body", None):
        return response
    try:
        html = response.body.decode("utf-8")
    except Exception:
        return response
    marker = "<a class='btn' href='/owner/users'>All Users</a>"
    link = f"<a class='btn primary' href='/owner/users/{quote(user_id, safe='')}/intelligence'>Intelligence</a>"
    network_link = "<a class='btn' href='/owner/esp-intelligence'>ESP Network Intelligence</a>"
    if marker in html and "/intelligence'" not in html:
        html = html.replace(marker, marker + link + network_link, 1)
    return HTMLResponse(html, status_code=response.status_code, headers={"Cache-Control": "no-store"})


__all__ = ["router", "_social_summary"]
