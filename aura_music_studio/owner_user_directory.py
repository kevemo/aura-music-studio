from __future__ import annotations

from html import escape

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .owner_identity import owner_session_authorized, owner_theme, request_owner_persona
from .owner_user_control import OwnerUserControl

router = APIRouter()
control = OwnerUserControl()


def _page(request: Request, body: str, title: str) -> HTMLResponse:
    persona = request_owner_persona(request)
    theme = owner_theme(persona)
    return HTMLResponse(f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1,viewport-fit=cover'><meta name='robots' content='noindex,nofollow'><title>{escape(title)}</title><style>
:root{{--accent:{theme.accent};--secondary:{theme.secondary};--bg:#07050c;--panel:#15101d;--line:#ffffff1e;--muted:#c8bfd2;--good:#74dda0;--warn:#ffd17b;--bad:#ff8fa3}}*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 90% 0,var(--secondary),transparent 30%),#07050c;color:#fff;font-family:Inter,ui-sans-serif,system-ui,sans-serif}}a{{color:inherit;text-decoration:none}}.wrap{{width:min(1480px,calc(100% - 28px));margin:auto;padding:26px 0 60px}}.top,.row{{display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap}}.eyebrow{{color:var(--accent);text-transform:uppercase;letter-spacing:.14em;font-size:.72rem;font-weight:950}}h1{{font-size:clamp(2.3rem,5vw,4.2rem);letter-spacing:-.05em;margin:.15em 0}}.muted{{color:var(--muted);line-height:1.55}}.card{{border:1px solid var(--line);border-radius:19px;background:#15101dee;padding:17px;margin:12px 0}}.grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}}.grid2{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}}.metric{{border:1px solid var(--line);border-radius:14px;padding:11px;background:#ffffff05}}.metric b{{display:block;font-size:1.3rem;margin-top:4px}}.btn,button{{display:inline-block;border:1px solid var(--line);border-radius:10px;padding:9px 11px;background:#ffffff09;color:#fff;font-weight:850;cursor:pointer}}.primary{{border:0;background:linear-gradient(110deg,var(--accent),var(--secondary));color:#130b18}}.success{{background:#245d3c;border:0}}.danger{{background:#5d263a;border:0}}input,select{{border:1px solid var(--line);background:#09070f;color:#fff;border-radius:9px;padding:9px}}.pill{{display:inline-block;border:1px solid var(--line);padding:4px 8px;border-radius:999px;font-size:.72rem;margin:2px}}.good{{color:var(--good)}}.warn{{color:var(--warn)}}.bad{{color:var(--bad)}}.scroll{{overflow:auto}}table{{width:100%;border-collapse:collapse;min-width:1100px}}th,td{{padding:9px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}th{{font-size:.72rem;color:var(--muted);text-transform:uppercase;letter-spacing:.08em}}.actions{{display:flex;gap:6px;flex-wrap:wrap}}.progress{{height:9px;border-radius:999px;background:#ffffff0d;overflow:hidden}}.progress i{{display:block;height:100%;background:linear-gradient(90deg,var(--accent),var(--secondary))}}.audit{{border-left:3px solid var(--accent);padding-left:10px;margin:8px 0}}@media(max-width:850px){{.grid,.grid2{{grid-template-columns:1fr 1fr}}}}@media(max-width:520px){{.grid,.grid2{{grid-template-columns:1fr}}}}
</style></head><body><main class='wrap'>{body}</main></body></html>""")


def _role(row: dict) -> str:
    status = row.get("esp_status") or "none"
    roles = row.get("esp_roles") or ""
    if status == "owner":
        return "Owner"
    if status == "active":
        return "ESP " + roles.replace("both", "Creator + Agent").title()
    if status == "pending":
        return "ESP Pending"
    if status in {"rejected", "revoked"}:
        return status.title()
    return "Regular"


@router.get("/owner/users", response_class=HTMLResponse, include_in_schema=False)
def user_directory(request: Request, q: str = "", role: str = "all", plan: str = "all"):
    if not owner_session_authorized(request):
        return RedirectResponse("/owner", status_code=303)
    rows = control.list_users()
    query = (q or "").strip().lower()
    if query:
        rows = [row for row in rows if any(query in str(row.get(field) or "").lower() for field in ("display_name", "email", "tiktok_handle", "niche", "sub_niche", "sub_level", "mentor"))]
    if role != "all":
        if role == "regular":
            rows = [row for row in rows if row.get("esp_status") not in {"active", "owner", "pending"}]
        elif role == "pending":
            rows = [row for row in rows if row.get("esp_status") == "pending"]
        else:
            rows = [row for row in rows if row.get("esp_status") in {"active", "owner"} and (row.get("esp_roles") == role or row.get("esp_roles") == "both")]
    if plan != "all":
        rows = [row for row in rows if row.get("plan_id") == plan]

    body_rows = "".join(
        f"""<tr><td><a style='color:var(--accent)' href='/owner/users/{escape(row['id'], quote=True)}'><b>{escape(row['display_name'])}</b></a><br><small class='muted'>{escape(row['email'])}</small>{f"<br><span class='pill'>@{escape(row['tiktok_handle'])}</span>" if row.get('tiktok_handle') else ''}</td><td><span class='pill'>{escape(str(row.get('plan_id') or 'free').upper())}</span><br><small class='muted'>{escape(row.get('billing_status') or '')}</small></td><td><b>{escape(_role(row))}</b><br><small class='muted'>{escape(row.get('region') or '')}</small></td><td>{escape((row.get('niche') or 'Not selected').replace('_',' ').title())}<br><small class='muted'>{escape(row.get('sub_niche') or '')}</small></td><td>{escape(row.get('sub_level') or '—')}<br><small class='muted'>{escape(row.get('mentor') or '')}</small></td><td><b>{int(row.get('usage_events') or 0)}</b> events<br><small class='muted'>{int(row.get('project_count') or 0)} projects</small></td><td><b>{int(row.get('progress_submissions') or 0)}</b> uploads<div class='progress'><i style='width:{max(0,min(100,round(float(row.get('avg_training_percent') or 0))))}%'></i></div><small class='muted'>{round(float(row.get('avg_training_percent') or 0))}% training</small></td><td><a class='btn primary' href='/owner/users/{escape(row['id'], quote=True)}'>Open</a></td></tr>"""
        for row in rows
    ) or "<tr><td colspan='8' class='muted'>No matching users.</td></tr>"

    summary = control.dashboard_summary()
    body = f"""<div class='top'><div><div class='eyebrow'>Mary / Kev Owner Control</div><h1>User Directory</h1><p class='muted'>Every registered member is visible here. Creative subscription and ESP role remain separate controls.</p></div><div class='actions'><a class='btn' href='/owner/dashboard'>Command Center</a><a class='btn' href='/owner/audit'>Audit</a></div></div><section class='grid'><div class='metric'><small>All accounts</small><b>{summary['users']}</b></div><div class='metric'><small>ESP pending</small><b>{summary['esp_pending']}</b></div><div class='metric'><small>ESP creators</small><b>{summary['esp_creators']}</b></div><div class='metric'><small>ESP agents</small><b>{summary['esp_agents']}</b></div></section><section class='card'><form method='get' action='/owner/users' class='row' style='justify-content:flex-start'><input name='q' value='{escape(q, quote=True)}' placeholder='Search user, email, TikTok, niche, level, mentor' style='min-width:300px'><select name='role'><option value='all'>All roles</option><option value='regular'{' selected' if role=='regular' else ''}>Regular</option><option value='pending'{' selected' if role=='pending' else ''}>ESP Pending</option><option value='creator'{' selected' if role=='creator' else ''}>ESP Creator</option><option value='agent'{' selected' if role=='agent' else ''}>ESP Agent</option></select><select name='plan'><option value='all'>All plans</option><option value='free'{' selected' if plan=='free' else ''}>Free</option><option value='base'{' selected' if plan=='base' else ''}>Base</option><option value='pro'{' selected' if plan=='pro' else ''}>Pro</option></select><button class='primary'>Filter</button><a class='btn' href='/owner/users'>Clear</a></form></section><section class='card scroll'><table><thead><tr><th>User</th><th>Plan</th><th>ESP role</th><th>Niche</th><th>ESP level / mentor</th><th>Creations</th><th>Progress</th><th></th></tr></thead><tbody>{body_rows}</tbody></table></section>"""
    return _page(request, body, "Owner User Directory")


@router.get("/owner/users/{user_id}", response_class=HTMLResponse, include_in_schema=False)
def user_detail(user_id: str, request: Request, message: str = ""):
    if not owner_session_authorized(request):
        return RedirectResponse("/owner", status_code=303)
    data = control.detail(user_id)
    if not data:
        return _page(request, "<div class='card'><h1>User not found</h1><a class='btn' href='/owner/users'>Back</a></div>", "User not found")
    user = data["user"]
    membership = data.get("esp") or {}
    niche = data.get("niche") or {}
    owner_profile = data.get("owner_profile") or {}
    performance = data.get("performance") or {}
    training = data.get("training") or {}
    requests = data.get("esp_requests") or []
    audit = data.get("owner_audit") or []

    role = membership.get("roles") if membership.get("status") in {"active", "owner"} else "regular"
    if role == "owner":
        role = "both"
    pending = any(row.get("status") == "pending" for row in requests)
    categories = owner_profile.get("categories") or []
    creation_html = "".join(
        f"<span class='pill'>{escape(row['event_type'].replace('_',' ').title())}: {int(row['n'])}</span>"
        for row in data.get("usage", [])
    ) or "<span class='muted'>No creation/activity events recorded yet.</span>"
    training_html = "".join(
        f"<div class='metric'><small>{escape(key.replace('-',' ').title())}</small><b>{int(value)}%</b></div>"
        for key, value in training.items()
    ) or "<div class='metric'><small>Training</small><b>0%</b></div>"
    request_html = "".join(
        f"<div class='card'><div class='row'><b>{escape(row['requested_role'].replace('both','Creator + Agent').title())} request</b><span class='pill'>{escape(row['status'].title())}</span></div><p class='muted'>@{escape(row.get('tiktok_handle') or '')} · {escape(row.get('region') or '')} · {escape((row.get('created_at') or '')[:16].replace('T',' '))}</p>{f"<p>{escape(row.get('note') or '')}</p>" if row.get('note') else ''}</div>"
        for row in requests[:10]
    ) or "<p class='muted'>No ESP access requests.</p>"
    audit_html = "".join(
        f"<div class='audit'><b>{escape(row['action'].replace('_',' ').title())}</b><br><small class='muted'>{escape(row['actor'])} · {escape(row['created_at'][:16].replace('T',' '))}</small></div>"
        for row in audit[:12]
    ) or "<p class='muted'>No owner actions recorded for this user.</p>"
    flash = f"<div class='card good'>{escape(message)}</div>" if message else ""

    body = f"""<div class='top'><div><div class='eyebrow'>Owner User View</div><h1>{escape(user['display_name'])}</h1><p class='muted'>{escape(user['email'])}</p></div><div class='actions'><a class='btn' href='/owner/users'>All Users</a><a class='btn' href='/owner/users/{escape(user_id, quote=True)}/owner-profile'>Sub-level, mentor & notes</a><a class='btn' href='/owner/audit?user_id={escape(user_id, quote=True)}'>Audit</a></div></div>{flash}<section class='grid'><div class='metric'><small>Subscription</small><b>{escape(str(user.get('plan_id') or 'free').upper())}</b><span class='muted'>{escape(user.get('billing_status') or '')}</span></div><div class='metric'><small>ESP status</small><b>{escape((membership.get('status') or 'regular').title())}</b><span class='muted'>{escape((membership.get('roles') or '').replace('both','Creator + Agent').title())}</span></div><div class='metric'><small>Creation projects</small><b>{int(data.get('projects') or 0)}</b><span class='muted'>{sum(int(r.get('n') or 0) for r in data.get('usage',[]))} events</span></div><div class='metric'><small>LIVE/video progress</small><b>{int(performance.get('total') or 0)}</b><span class='muted'>submissions</span></div></section>
<section class='grid2'><div class='card'><div class='eyebrow'>Creative subscription</div><h2>Free / Base / Pro</h2><p class='muted'>This changes normal Pulsar-Frequency House creative entitlement only.</p><form method='post' action='/owner/users/{escape(user_id, quote=True)}/plan' class='actions'><select name='plan_id'><option value='free'{' selected' if user.get('plan_id')=='free' else ''}>Free</option><option value='base'{' selected' if user.get('plan_id')=='base' else ''}>Base</option><option value='pro'{' selected' if user.get('plan_id')=='pro' else ''}>Pro</option></select><button class='primary'>Apply plan</button></form></div><div class='card'><div class='eyebrow'>Private ESP permission</div><h2>Regular / Creator / Agent</h2><p class='muted'>ESP access is owner-approved and never created by buying a subscription.</p><form method='post' action='/owner/users/{escape(user_id, quote=True)}/esp-role' class='actions'><select name='role'><option value='regular'{' selected' if role=='regular' else ''}>Regular / Non-ESP</option><option value='creator'{' selected' if role=='creator' else ''}>ESP Creator</option><option value='agent'{' selected' if role=='agent' else ''}>ESP Agent</option><option value='both'{' selected' if role=='both' else ''}>ESP Creator + Agent</option></select><button class='success'>Apply ESP role</button></form>{f"<form method='post' action='/owner/users/{escape(user_id, quote=True)}/esp-decline' style='margin-top:8px'><button class='danger'>Decline pending ESP request</button></form>" if pending else ''}</div></section>
<section class='card'><div class='eyebrow'>ESP profile</div><h2>Niche, level & responsibility</h2><div class='grid'><div class='metric'><small>Niche</small><b>{escape((niche.get('niche') or 'Not selected').replace('_',' ').title())}</b><span class='muted'>{escape(niche.get('sub_niche') or '')}</span></div><div class='metric'><small>Network declaration</small><b>{escape((niche.get('network_status') or '—').replace('_',' ').title())}</b></div><div class='metric'><small>Internal sub-level</small><b>{escape(owner_profile.get('sub_level') or '—')}</b></div><div class='metric'><small>Mentor / owner contact</small><b>{escape(owner_profile.get('mentor') or '—')}</b></div></div><p>{''.join(f"<span class='pill'>{escape(value)}</span>" for value in categories) or '<span class="muted">No internal categories assigned.</span>'}</p>{f"<p class='muted'><b>Private owner notes:</b> {escape(owner_profile.get('owner_notes') or '')}</p>" if owner_profile.get('owner_notes') else ''}<a class='btn primary' href='/owner/users/{escape(user_id, quote=True)}/owner-profile'>Edit internal ESP profile</a></section>
<section class='card'><div class='eyebrow'>Creation categories</div><h2>What this user is using</h2>{creation_html}</section><section class='card'><div class='eyebrow'>Training</div><h2>ESP training progress</h2><div class='grid'>{training_html}</div></section><section><div class='eyebrow'>ESP verification</div><h2>Request history</h2>{request_html}</section><section class='card'><div class='eyebrow'>Owner accountability</div><h2>Recent Mary/Kev actions</h2>{audit_html}</section>"""
    return _page(request, body, f"Owner User — {user['display_name']}")
