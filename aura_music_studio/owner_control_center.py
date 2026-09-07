from __future__ import annotations

from html import escape
from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .branding import ENDORSEMENT, PRODUCT_FULL_NAME, TAGLINE
from .owner_identity import (
    OWNER_THEMES,
    OwnerPersona,
    owner_session_authorized,
    owner_theme,
    request_owner_persona,
    set_persona_cookie,
)
from .owner_user_control import OwnerUserControl

router = APIRouter()
control = OwnerUserControl()


def _authorized(request: Request) -> bool:
    return owner_session_authorized(request)


def _page(request: Request, body: str, title: str = "ESP Owner Command Center") -> HTMLResponse:
    persona = request_owner_persona(request)
    theme = owner_theme(persona)
    who = theme.display_name if persona else "Mary / Kev"
    switch = "".join(
        f"<form method='post' action='/owner/persona/{key}' style='display:inline'>"
        f"<button class='persona {'active' if persona == key else ''}' type='submit'>{escape(value.display_name)}</button></form>"
        for key, value in OWNER_THEMES.items()
    )
    html = f"""<!doctype html><html lang='en'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1,viewport-fit=cover'>
<meta name='robots' content='noindex,nofollow'><title>{escape(title)} — {escape(PRODUCT_FULL_NAME)}</title>
<style>
:root{{--accent:{theme.accent};--secondary:{theme.secondary};--glow:{theme.glow};--bg:#07050c;--panel:#15101d;--panel2:#0e0b14;--line:#ffffff1e;--text:#fff;--muted:#c8bfd2;--good:#74dda0;--warn:#ffd27c;--bad:#ff91a5}}
*{{box-sizing:border-box}}body{{margin:0;color:var(--text);background:radial-gradient(circle at 8% 0,var(--glow),transparent 28%),radial-gradient(circle at 90% 0,var(--secondary),transparent 31%),#07050c;font-family:Inter,ui-sans-serif,system-ui,sans-serif;min-height:100vh}}a{{color:inherit;text-decoration:none}}button,.btn,input,select,textarea{{font:inherit}}.wrap{{width:min(1480px,calc(100% - 28px));margin:auto;padding:24px 0 60px}}.top,.row{{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}}.brand{{display:flex;gap:12px;align-items:center}}.orb{{width:50px;height:50px;border-radius:17px;background:radial-gradient(circle at 32% 28%,#fff,var(--accent) 20%,var(--secondary) 68%,#100716);box-shadow:0 0 38px var(--glow)}}.eyebrow{{font-size:.72rem;text-transform:uppercase;letter-spacing:.15em;color:var(--accent);font-weight:950}}h1{{font-size:clamp(2.2rem,5vw,4.3rem);letter-spacing:-.05em;margin:.12em 0}}h2{{margin:.2em 0 .45em}}.muted{{color:var(--muted);line-height:1.55}}.card{{background:linear-gradient(145deg,#17101fee,#0d0a13ed);border:1px solid var(--line);border-radius:20px;padding:18px;margin:12px 0;box-shadow:0 18px 55px #0005}}.grid{{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:10px}}.grid3{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}}.metric{{border:1px solid var(--line);border-radius:15px;padding:13px;background:#ffffff05;min-width:0}}.metric b{{display:block;font-size:1.7rem;margin-top:3px;overflow:hidden;text-overflow:ellipsis}}.metric small{{color:var(--muted)}}.btn,button{{display:inline-block;border:1px solid var(--line);border-radius:11px;padding:9px 12px;background:#ffffff09;color:#fff;font-weight:850;cursor:pointer}}.btn.primary,button.primary{{border:0;background:linear-gradient(110deg,var(--accent),var(--secondary));color:#120a18}}.persona{{padding:8px 13px}}.persona.active{{border-color:var(--accent);box-shadow:0 0 0 2px var(--glow) inset;background:#ffffff12}}.pill{{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:4px 8px;font-size:.72rem;margin:2px}}.pill.good{{color:var(--good);border-color:#74dda044}}.pill.warn{{color:var(--warn);border-color:#ffd27c44}}.scroll{{overflow:auto}}table{{width:100%;border-collapse:collapse;min-width:1000px}}th,td{{padding:9px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}th{{color:var(--muted);font-size:.75rem;text-transform:uppercase;letter-spacing:.08em}}input,select,textarea{{border:1px solid var(--line);border-radius:11px;padding:10px;background:#09070e;color:#fff;width:100%}}textarea{{min-height:150px;resize:vertical}}.actions{{display:flex;gap:7px;flex-wrap:wrap}}.audit{{border-left:3px solid var(--accent);padding-left:12px;margin:10px 0}}.good{{color:var(--good)}}.warn{{color:var(--warn)}}@media(max-width:1100px){{.grid{{grid-template-columns:repeat(3,1fr)}}.grid3{{grid-template-columns:1fr 1fr}}}}@media(max-width:700px){{.grid,.grid3{{grid-template-columns:1fr 1fr}}}}@media(max-width:500px){{.grid,.grid3{{grid-template-columns:1fr}}}}
</style></head><body><main class='wrap'><header class='top'><div class='brand'><div class='orb'></div><div><div class='eyebrow'>Elevate Souls Productions · Owner Control</div><b>{escape(PRODUCT_FULL_NAME)}</b><div class='muted' style='font-size:.78rem'>{escape(ENDORSEMENT)}</div></div></div><div class='actions'><span class='muted'>Using as <b style='color:var(--accent)'>{escape(who)}</b></span>{switch}<a class='btn' href='/owner/users'>Users</a><a class='btn' href='/owner/updates'>Updates & Features</a><a class='btn' href='/owner/backups'>Backups</a><a class='btn' href='/owner/compute-nodes'>Compute</a><form method='post' action='/owner/logout' style='display:inline'><button type='submit'>Sign out</button></form></div></header>{body}<footer class='muted' style='padding-top:28px'>{escape(TAGLINE)}</footer></main></body></html>"""
    return HTMLResponse(html)


def _role_label(row: dict) -> str:
    if row.get("esp_status") == "owner":
        return "Owner"
    if row.get("esp_status") == "active":
        role = (row.get("esp_roles") or "member").replace("both", "creator + agent")
        return f"ESP {role.title()}"
    if row.get("esp_status") == "pending":
        return "ESP request pending"
    return "Regular"


@router.post("/owner/persona/{persona}", include_in_schema=False)
def switch_owner_persona(persona: str, request: Request):
    if not _authorized(request):
        return RedirectResponse("/owner", status_code=303)
    if persona not in OWNER_THEMES:
        return RedirectResponse("/owner/dashboard", status_code=303)
    response = RedirectResponse("/owner/dashboard", status_code=303)
    set_persona_cookie(response, persona)  # type: ignore[arg-type]
    return response


@router.get("/owner/dashboard", response_class=HTMLResponse, include_in_schema=False)
def owner_dashboard(request: Request):
    if not _authorized(request):
        return RedirectResponse("/owner", status_code=303)
    persona = request_owner_persona(request)
    summary = control.dashboard_summary()
    rows = control.list_users()
    pending = [row for row in rows if row.get("esp_status") == "pending"][:12]
    recent_progress = sorted(
        [row for row in rows if row.get("last_progress_at")],
        key=lambda row: row.get("last_progress_at") or "",
        reverse=True,
    )[:10]
    audits = control.audit_log(limit=12)

    if persona is None:
        identity_prompt = """<section class='card' style='border-color:var(--accent)'><div class='eyebrow'>Owner identity</div><h2>Who is using the command centre?</h2><p class='muted'>Choose Mary or Kev above. Aura and the owner audit trail will then use that verified owner context for this session.</p></section>"""
    else:
        theme = owner_theme(persona)
        identity_prompt = f"""<section class='card' style='border-color:{theme.accent}66'><div class='eyebrow'>Aura owner context</div><h2>Welcome, {escape(theme.display_name)}.</h2><p class='muted'>{escape(theme.aura_context)}</p></section>"""

    metric_html = f"""<section class='grid'><div class='metric'><small>All users</small><b>{summary['users']}</b></div><div class='metric'><small>Regular / non-ESP</small><b>{summary['regular']}</b></div><div class='metric'><small>ESP requests</small><b>{summary['esp_pending']}</b></div><div class='metric'><small>ESP creators</small><b>{summary['esp_creators']}</b></div><div class='metric'><small>ESP agents</small><b>{summary['esp_agents']}</b></div><div class='metric'><small>Progress uploads</small><b>{summary['progress_submissions']}</b></div></section>"""

    pending_html = "".join(
        f"""<tr><td><a href='/owner/users/{escape(row['id'], quote=True)}'><b>{escape(row['display_name'])}</b></a><br><small class='muted'>{escape(row['email'])}</small></td><td>{escape('@'+row['tiktok_handle'] if row.get('tiktok_handle') else '—')}</td><td>{escape(row.get('region') or '—')}</td><td>{escape((row.get('niche') or 'Not selected').replace('_',' ').title())}</td><td><a class='btn primary' href='/owner/users/{escape(row['id'], quote=True)}'>Review & decide</a></td></tr>"""
        for row in pending
    ) or "<tr><td colspan='5' class='muted'>No ESP access requests are waiting for review.</td></tr>"

    progress_html = "".join(
        f"""<tr><td><a href='/owner/users/{escape(row['id'], quote=True)}'><b>{escape(row['display_name'])}</b></a></td><td>{escape((row.get('niche') or 'Other').replace('_',' ').title())}</td><td>{int(row.get('progress_submissions') or 0)}</td><td>{round(float(row.get('avg_training_percent') or 0))}%</td><td>{escape((row.get('last_progress_at') or '')[:16].replace('T',' '))}</td></tr>"""
        for row in recent_progress
    ) or "<tr><td colspan='5' class='muted'>No creator progress submissions yet.</td></tr>"

    audit_html = "".join(
        f"""<div class='audit'><div class='row'><b>{escape(item['action'].replace('_',' ').title())}</b><small class='muted'>{escape(item['created_at'][:16].replace('T',' '))}</small></div><div class='muted'>{escape(item['actor'])}{' · user '+escape(item['target_user_id'][:10]) if item.get('target_user_id') else ''}</div></div>"""
        for item in audits
    ) or "<p class='muted'>No owner changes have been recorded yet.</p>"

    body = f"""{identity_prompt}{metric_html}
<section class='grid3'><div class='card'><div class='eyebrow'>Subscriptions</div><h2>Creative Studio members</h2><div class='grid3'><div class='metric'><small>Free</small><b>{summary['plans']['free']}</b></div><div class='metric'><small>Base</small><b>{summary['plans']['base']}</b></div><div class='metric'><small>Pro</small><b>{summary['plans']['pro']}</b></div></div><p class='muted'>Subscription level controls normal creative tools only. ESP status is a separate owner-approved permission.</p><a class='btn primary' href='/owner/users'>Open complete user directory</a></div>
<div class='card'><div class='eyebrow'>ESP private side</div><h2>Creator & Agent approvals</h2><p class='muted'>No ESP role is self-granted. Review pending requests, assign Creator/Agent/Both, and retain the no-poaching boundary for creators represented elsewhere.</p><a class='btn primary' href='/owner/users'>Review ESP users</a></div>
<div class='card'><div class='eyebrow'>Creator intelligence</div><h2>Training & performance</h2><p class='muted'>Review niche, training completion, LIVE/video analytics uploads, Aura recommendations, creation activity, mentor assignment and internal sub-levels per creator.</p><a class='btn primary' href='/owner/users'>Open creator progress</a></div></section>
<section class='card'><div class='row'><div><div class='eyebrow'>Waiting for Mary / Kev</div><h2>Pending ESP access</h2></div><a class='btn' href='/owner/users'>All users</a></div><div class='scroll'><table><thead><tr><th>User</th><th>TikTok</th><th>Region</th><th>Niche</th><th></th></tr></thead><tbody>{pending_html}</tbody></table></div></section>
<section class='card'><div class='row'><div><div class='eyebrow'>Latest performance</div><h2>Creator progress activity</h2></div></div><div class='scroll'><table><thead><tr><th>Creator</th><th>Niche</th><th>Submissions</th><th>Training</th><th>Latest</th></tr></thead><tbody>{progress_html}</tbody></table></div></section>
<section class='card'><div class='row'><div><div class='eyebrow'>Accountability</div><h2>Recent owner actions</h2></div><a class='btn' href='/owner/audit'>Full audit</a></div>{audit_html}</section>"""
    return _page(request, body)


@router.get("/owner/audit", response_class=HTMLResponse, include_in_schema=False)
def owner_audit(request: Request, user_id: str = ""):
    if not _authorized(request):
        return RedirectResponse("/owner", status_code=303)
    rows = control.audit_log(user_id or None, limit=250)
    audit_html = "".join(
        f"""<div class='card audit'><div class='row'><div><div class='eyebrow'>{escape(item['action'].replace('_',' ').title())}</div><b>{escape(item['actor'])}</b></div><small class='muted'>{escape(item['created_at'])}</small></div><p class='muted'>Target user: {escape(item.get('target_user_id') or 'System')}</p><details><summary>Change data</summary><pre style='white-space:pre-wrap;color:#d9cfdf'>{escape(str({'before':item.get('before'),'after':item.get('after'),'metadata':item.get('metadata')}))}</pre></details></div>"""
        for item in rows
    ) or "<div class='card'><p class='muted'>No matching owner actions.</p></div>"
    body = f"""<div class='top'><div><div class='eyebrow'>Mary / Kev accountability</div><h1>Owner Audit Trail</h1><p class='muted'>Subscription, ESP-role and owner-profile changes are recorded with the active owner identity.</p></div><a class='btn' href='/owner/dashboard'>Back to dashboard</a></div>{audit_html}"""
    return _page(request, body, "Owner Audit Trail")


@router.get("/owner/users/{user_id}/owner-profile", response_class=HTMLResponse, include_in_schema=False)
def owner_user_profile(user_id: str, request: Request, message: str = ""):
    if not _authorized(request):
        return RedirectResponse("/owner", status_code=303)
    data = control.detail(user_id)
    if not data:
        return _page(request, "<div class='card'><h1>User not found</h1></div>")
    user = data["user"]
    profile = data.get("owner_profile") or {}
    categories = ", ".join(profile.get("categories") or [])
    flash = f"<div class='card good'>{escape(message)}</div>" if message else ""
    body = f"""<div class='top'><div><div class='eyebrow'>Internal ESP management</div><h1>{escape(user['display_name'])}</h1><p class='muted'>{escape(user['email'])}</p></div><div class='actions'><a class='btn' href='/owner/users/{escape(user_id, quote=True)}'>Full user view</a><a class='btn' href='/owner/users'>Users</a></div></div>{flash}
<section class='card'><h2>Owner-managed creator/agent profile</h2><p class='muted'>These fields are private to Mary/Kev owner administration. They do not change the public account profile.</p><form method='post' action='/owner/users/{escape(user_id, quote=True)}/owner-profile'><div class='grid3'><label>ESP sub-level<input name='sub_level' value='{escape(profile.get('sub_level') or '', quote=True)}' placeholder='Example: Apprentice, Certified, Senior, GOATS'></label><label>Mentor / owner contact<input name='mentor' value='{escape(profile.get('mentor') or '', quote=True)}' placeholder='Mentor or responsible agent'></label><label>Categories<input name='categories' value='{escape(categories, quote=True)}' placeholder='music, live growth, battle, training'></label></div><label>Mary/Kev private notes<textarea name='owner_notes' placeholder='Private progress notes, agreed actions, follow-up points...'>{escape(profile.get('owner_notes') or '')}</textarea></label><button class='primary' type='submit'>Save owner profile</button></form></section>"""
    return _page(request, body, f"Owner Profile — {user['display_name']}")


@router.post("/owner/users/{user_id}/owner-profile", include_in_schema=False)
def save_owner_user_profile(
    user_id: str,
    request: Request,
    sub_level: str = Form(""),
    mentor: str = Form(""),
    categories: str = Form(""),
    owner_notes: str = Form(""),
):
    if not _authorized(request):
        return RedirectResponse("/owner", status_code=303)
    try:
        category_rows = [value.strip() for value in categories.split(",") if value.strip()]
        control.update_owner_profile(
            user_id,
            sub_level=sub_level,
            mentor=mentor,
            categories=category_rows,
            owner_notes=owner_notes,
        )
        message = "Owner profile saved."
    except Exception as exc:
        message = f"Could not save owner profile: {exc}"
    return RedirectResponse(
        f"/owner/users/{quote(user_id)}/owner-profile?message={quote(message)}",
        status_code=303,
    )