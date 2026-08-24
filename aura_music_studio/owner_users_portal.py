from __future__ import annotations

import os
import secrets
from html import escape
from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .owner_user_control import OwnerUserControl

router = APIRouter()
control = OwnerUserControl()
ADMIN_COOKIE = "lss_admin_session"

CSS = """
:root{--bg:#08050e;--panel:#17101f;--line:#ffffff1d;--gold:#e9bd65;--text:#fff;--muted:#c8bfd2;--green:#75da9e;--red:#ff8fa3;--purple:#a376ff}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 90% 0,#35134b,transparent 28%),#08050e;color:#fff;font-family:Inter,ui-sans-serif,system-ui,sans-serif}.wrap{width:min(1450px,calc(100% - 28px));margin:auto;padding:28px 0 60px}.top,.row{display:flex;justify-content:space-between;gap:10px;align-items:center;flex-wrap:wrap}.card{background:#15101dee;border:1px solid var(--line);border-radius:18px;padding:17px;margin:12px 0}.grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}.grid2{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.metric{border:1px solid var(--line);border-radius:13px;padding:11px;background:#ffffff05}.metric b{display:block;font-size:1.3rem}.muted{color:var(--muted)}.gold{color:var(--gold)}.good{color:var(--green)}.bad{color:var(--red)}.pill{display:inline-block;border:1px solid var(--line);padding:4px 8px;border-radius:999px;font-size:.75rem;margin:2px}.btn,button{border:0;border-radius:10px;padding:9px 12px;font-weight:850;cursor:pointer;text-decoration:none;display:inline-block;background:var(--gold);color:#180f20}.secondary{background:#ffffff0a;color:#fff;border:1px solid var(--line)}.danger{background:#57243a;color:#fff}.success{background:#245c3c;color:#fff}input,select{border:1px solid var(--line);background:#09070f;color:#fff;border-radius:9px;padding:9px}table{width:100%;border-collapse:collapse;min-width:1050px}th,td{padding:9px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}.scroll{overflow:auto}.actions{display:flex;gap:6px;flex-wrap:wrap}.small{font-size:.8rem}.request{border-left:4px solid var(--gold)}@media(max-width:850px){.grid,.grid2{grid-template-columns:1fr 1fr}}@media(max-width:560px){.grid,.grid2{grid-template-columns:1fr}}
"""


def _authorized(request: Request) -> bool:
    configured = os.getenv("LSS_ADMIN_KEY") or ""
    supplied = request.cookies.get(ADMIN_COOKIE) or ""
    return bool(configured and supplied and secrets.compare_digest(configured, supplied))


def _page(body: str) -> HTMLResponse:
    return HTMLResponse(f"<!doctype html><html><head><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='noindex,nofollow'><title>ESP Owner Users</title><style>{CSS}</style></head><body><main class='wrap'>{body}</main></body></html>")


def _esp_label(row: dict) -> str:
    status = row.get("esp_status") or "none"
    roles = row.get("esp_roles") or "regular"
    if status == "owner":
        return "Owner"
    if status == "active":
        return f"ESP {roles.title()}"
    if status == "pending":
        return "ESP Request Pending"
    return "Regular"


@router.get("/owner/users", response_class=HTMLResponse, include_in_schema=False)
def owner_users(request: Request, q: str = ""):
    if not _authorized(request):
        return RedirectResponse("/owner", status_code=303)
    rows = control.list_users()
    query = (q or "").strip().lower()
    if query:
        rows = [
            row for row in rows
            if query in (row.get("display_name") or "").lower()
            or query in (row.get("email") or "").lower()
            or query in (row.get("tiktok_handle") or "").lower()
            or query in (row.get("niche") or "").lower()
        ]

    pending = [row for row in rows if row.get("esp_status") == "pending"]
    creator_count = sum(1 for row in rows if row.get("esp_status") == "active" and row.get("esp_roles") in {"creator", "both"})
    agent_count = sum(1 for row in rows if row.get("esp_status") == "active" and row.get("esp_roles") in {"agent", "both"})
    body_rows = "".join(
        f"""<tr><td><a class='gold' href='/owner/users/{escape(row['id'], quote=True)}'><b>{escape(row['display_name'])}</b></a><br><span class='muted small'>{escape(row['email'])}</span></td><td><span class='pill'>{escape(str(row['plan_id']).upper())}</span><br><span class='muted small'>{escape(row.get('billing_status') or '')}</span></td><td><span class='pill'>{escape(_esp_label(row))}</span>{f"<br><span class='gold small'>@{escape(row.get('tiktok_handle') or '')}</span>" if row.get('tiktok_handle') else ''}</td><td>{escape((row.get('niche') or 'Not selected').replace('_',' ').title())}{f"<br><span class='muted small'>{escape(row.get('sub_niche') or '')}</span>" if row.get('sub_niche') else ''}</td><td>{int(row.get('usage_events') or 0)} events<br><span class='muted small'>{int(row.get('project_count') or 0)} projects</span></td><td>{int(row.get('progress_submissions') or 0)} uploads<br><span class='muted small'>{round(float(row.get('avg_training_percent') or 0))}% training</span></td><td><a class='btn secondary' href='/owner/users/{escape(row['id'], quote=True)}'>Open user</a></td></tr>"""
        for row in rows
    ) or "<tr><td colspan='7' class='muted'>No users match this filter.</td></tr>"

    body = f"""<div class='top'><div><div class='gold'><b>MARY & KEV · ESP OWNER CONTROL</b></div><h1>User Directory</h1><p class='muted'>Every account is visible here. Subscription level and ESP role are separate controls.</p></div><div><a class='btn secondary' href='/owner/dashboard'>Owner Dashboard</a></div></div>
<div class='grid'><div class='metric'><small class='muted'>Users shown</small><b>{len(rows)}</b></div><div class='metric'><small class='muted'>ESP requests</small><b>{len(pending)}</b></div><div class='metric'><small class='muted'>ESP creators</small><b>{creator_count}</b></div><div class='metric'><small class='muted'>ESP agents</small><b>{agent_count}</b></div></div>
<div class='card'><form method='get' action='/owner/users' class='row' style='justify-content:flex-start'><input name='q' value='{escape(q, quote=True)}' placeholder='Search name, email, handle or niche' style='min-width:300px'><button>Search</button><a class='btn secondary' href='/owner/users'>Clear</a></form></div>
<div class='card scroll'><table><thead><tr><th>User</th><th>Subscription</th><th>ESP status</th><th>Niche</th><th>Creation/activity</th><th>Training/progress</th><th></th></tr></thead><tbody>{body_rows}</tbody></table></div>"""
    return _page(body)


@router.get("/owner/users/{user_id}", response_class=HTMLResponse, include_in_schema=False)
def owner_user_detail(user_id: str, request: Request, message: str = ""):
    if not _authorized(request):
        return RedirectResponse("/owner", status_code=303)
    data = control.detail(user_id)
    if not data:
        return _page("<div class='card'><h1>User not found</h1><a class='btn' href='/owner/users'>Back</a></div>")
    user = data["user"]
    membership = data.get("esp") or {}
    profile = data.get("niche") or {}
    performance = data.get("performance") or {}
    role = membership.get("roles") if membership.get("status") in {"active", "owner"} else "regular"
    if role == "owner":
        role = "both"

    usage_html = "".join(
        f"<span class='pill'>{escape(row['event_type'].replace('_',' ').title())}: {int(row['n'])}</span>"
        for row in data.get("usage", [])
    ) or "<span class='muted'>No recorded creation/activity events yet.</span>"
    training_html = "".join(
        f"<div class='metric'><small class='muted'>{escape(key.replace('-',' ').title())}</small><b>{int(value)}%</b></div>"
        for key, value in data.get("training", {}).items()
    ) or "<div class='muted'>No training progress recorded yet.</div>"
    progress_rows = control.progress.list_for_user(user_id, 20)
    progress_html = "".join(
        f"<div class='card'><div class='row'><b>{escape(row['kind'].upper())} · {escape(row.get('period_label') or 'Progress')}</b><small class='muted'>{escape(row['created_at'][:16].replace('T',' '))}</small></div><div>{''.join(f"<span class='pill'>{escape(k.replace('_',' ').title())}: {escape(str(v))}</span>" for k,v in list(row.get('metrics',{}).items())[:10])}</div><ul>{''.join(f"<li>{escape(item)}</li>" for item in row.get('aura_guidance',[])[:5])}</ul>{f"<span class='good small'>Analytics file uploaded: {escape(row.get('upload_name') or '')}</span>" if row.get('upload_name') else ''}</div>"
        for row in progress_rows
    ) or "<p class='muted'>No LIVE/video analysis submitted yet.</p>"
    requests = data.get("esp_requests", [])
    requests_html = "".join(
        f"<div class='card request'><div class='row'><div><b>{escape(row['requested_role'].title())} request</b> · @{escape(row.get('tiktok_handle') or '')}<br><span class='muted'>{escape(row.get('region') or '')} · {escape(row['status'].title())}</span></div><small>{escape(row['created_at'][:16].replace('T',' '))}</small></div>{f"<p>{escape(row.get('note') or '')}</p>" if row.get('note') else ''}</div>"
        for row in requests
    ) or "<p class='muted'>No ESP access requests from this user.</p>"
    latest_request_pending = any(row.get("status") == "pending" for row in requests)
    message_html = f"<div class='card good'>{escape(message)}</div>" if message else ""

    body = f"""<div class='top'><div><div class='gold'><b>ESP USER CONTROL</b></div><h1>{escape(user['display_name'])}</h1><p class='muted'>{escape(user['email'])} · account {escape(user['status'])}</p></div><div><a class='btn secondary' href='/owner/users'>All users</a> <a class='btn secondary' href='/owner/dashboard'>Dashboard</a></div></div>{message_html}
<div class='grid'><div class='metric'><small class='muted'>Subscription</small><b>{escape(str(user['plan_id']).upper())}</b></div><div class='metric'><small class='muted'>ESP role</small><b>{escape(_esp_label({'esp_status': membership.get('status'),'esp_roles': membership.get('roles')}))}</b></div><div class='metric'><small class='muted'>Creation projects</small><b>{int(data.get('projects') or 0)}</b></div><div class='metric'><small class='muted'>Progress uploads</small><b>{int(performance.get('total') or 0)}</b></div></div>
<div class='grid2'><div class='card'><h2>Subscription level</h2><p class='muted'>Controls Creative Studio entitlement only. It does not grant ESP access.</p><form method='post' action='/owner/users/{escape(user_id, quote=True)}/plan'><select name='plan_id'><option value='free'{' selected' if user['plan_id']=='free' else ''}>Free</option><option value='base'{' selected' if user['plan_id']=='base' else ''}>Base</option><option value='pro'{' selected' if user['plan_id']=='pro' else ''}>Pro</option></select> <button>Set subscription</button></form></div>
<div class='card'><h2>ESP access role</h2><p class='muted'>Only Mary/Kev owner control can activate this. Regular users cannot self-upgrade into ESP.</p><form method='post' action='/owner/users/{escape(user_id, quote=True)}/esp-role'><select name='role'><option value='regular'{' selected' if role=='regular' else ''}>Regular / Non-ESP</option><option value='creator'{' selected' if role=='creator' else ''}>ESP Creator</option><option value='agent'{' selected' if role=='agent' else ''}>ESP Agent</option><option value='both'{' selected' if role=='both' else ''}>ESP Creator + Agent</option></select> <button class='success'>Apply ESP role</button></form>{f"<form method='post' action='/owner/users/{escape(user_id, quote=True)}/esp-decline' style='margin-top:8px'><button class='danger'>Decline pending ESP request</button></form>" if latest_request_pending else ''}</div></div>
<div class='card'><h2>Niche & profile</h2><div class='grid'><div class='metric'><small class='muted'>Primary niche</small><b>{escape((profile.get('niche') or 'Not selected').replace('_',' ').title())}</b></div><div class='metric'><small class='muted'>Sub-niche</small><b>{escape(profile.get('sub_niche') or '—')}</b></div><div class='metric'><small class='muted'>Network declaration</small><b>{escape((profile.get('network_status') or '—').replace('_',' ').title())}</b></div><div class='metric'><small class='muted'>TikTok</small><b>{escape('@'+membership.get('tiktok_handle') if membership.get('tiktok_handle') else '—')}</b></div></div>{f"<p><b>Audience:</b> {escape(profile.get('audience') or '')}</p>" if profile.get('audience') else ''}{f"<p><b>Goals:</b> {' · '.join(escape(g) for g in profile.get('goals',[]))}</p>" if profile.get('goals') else ''}</div>
<div class='card'><h2>Creation/activity categories</h2>{usage_html}</div>
<div class='card'><h2>ESP training progress</h2><div class='grid'>{training_html}</div></div>
<h2>ESP access request history</h2>{requests_html}
<h2>LIVE & video progress</h2>{progress_html}"""
    return _page(body)


@router.post("/owner/users/{user_id}/plan", include_in_schema=False)
def owner_set_plan(user_id: str, request: Request, plan_id: str = Form(...)):
    if not _authorized(request):
        return RedirectResponse("/owner", status_code=303)
    try:
        control.set_plan(user_id, plan_id, "Mary/Kev Owner Console")
        message = f"Subscription set to {plan_id.upper()}."
    except Exception as exc:
        message = f"Subscription change failed: {exc}"
    return RedirectResponse(f"/owner/users/{quote(user_id)}?message={quote(message)}", status_code=303)


@router.post("/owner/users/{user_id}/esp-role", include_in_schema=False)
def owner_set_esp_role(user_id: str, request: Request, role: str = Form(...)):
    if not _authorized(request):
        return RedirectResponse("/owner", status_code=303)
    try:
        control.set_esp_role(user_id, role, "Mary/Kev Owner Console")
        message = "ESP role updated."
    except Exception as exc:
        message = f"ESP role change failed: {exc}"
    return RedirectResponse(f"/owner/users/{quote(user_id)}?message={quote(message)}", status_code=303)


@router.post("/owner/users/{user_id}/esp-decline", include_in_schema=False)
def owner_decline_esp(user_id: str, request: Request):
    if not _authorized(request):
        return RedirectResponse("/owner", status_code=303)
    try:
        control.decline_esp_requests(user_id, "Mary/Kev Owner Console")
        message = "Pending ESP request declined."
    except Exception as exc:
        message = f"Could not decline request: {exc}"
    return RedirectResponse(f"/owner/users/{quote(user_id)}?message={quote(message)}", status_code=303)
