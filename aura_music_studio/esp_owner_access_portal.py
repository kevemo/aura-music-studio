from __future__ import annotations

from html import escape
from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .esp_level_up import EspAgentAssignmentStore
from .esp_social_access_control import EspSocialAccessControlStore
from .owner_auth import owner_authorized
from .owner_dashboard_preferences_portal import router as owner_dashboard_preferences_router
from .owner_identity import owner_actor, owner_theme, request_owner_persona
from .owner_user_control import OwnerUserControl

router = APIRouter()
# This composite router is mounted before the legacy owner-control router in app.py,
# so the personalized dashboard can safely overlay /owner/dashboard without changing
# authentication/session code or the high-conflict application router.
router.include_router(owner_dashboard_preferences_router)
control = OwnerUserControl()
assignments = EspAgentAssignmentStore(control.esp)
social_controls = EspSocialAccessControlStore(control.esp)

_ASSIGNMENT_AUDIT_ACTIONS = {
    "esp_agent_creator_assigned",
    "esp_agent_creator_revoked",
}
_ASSIGNMENT_AUDIT_FIELDS = (
    "id",
    "agent_user_id",
    "creator_user_id",
    "status",
    "assigned_by",
    "assigned_at",
    "revoked_by",
    "revoked_at",
)


def _go(message: str = "") -> RedirectResponse:
    suffix = f"?message={quote(message)}" if message else ""
    return RedirectResponse(f"/owner/esp-access{suffix}", status_code=303)


def _authorized(request: Request) -> bool:
    return owner_authorized(request)


def _role(row: dict) -> str:
    if row.get("esp_status") == "owner":
        return "owner"
    if row.get("esp_status") == "active":
        return row.get("esp_roles") or "regular"
    return "regular"


def _assignment_snapshot(agent_user_id: str, creator_user_id: str) -> dict:
    """Return a minimal relationship snapshot suitable for owner audit evidence.

    The free-text assignment note is deliberately excluded from centralized audit storage;
    the audit metadata records only whether a note existed.
    """
    with assignments._connect() as con:
        row = con.execute(
            """SELECT id,agent_user_id,creator_user_id,status,assigned_by,assigned_at,revoked_by,revoked_at,note
               FROM esp_agent_creator_assignments WHERE agent_user_id=? AND creator_user_id=?""",
            (agent_user_id, creator_user_id),
        ).fetchone()
    if not row:
        return {}
    item = dict(row)
    snapshot = {field: item.get(field) for field in _ASSIGNMENT_AUDIT_FIELDS}
    snapshot["note_present"] = bool(str(item.get("note") or "").strip())
    return snapshot


def _record_assignment_audit(
    *,
    action: str,
    actor: str,
    agent_user_id: str,
    creator_user_id: str,
    before: dict,
    after: dict,
) -> None:
    if action not in _ASSIGNMENT_AUDIT_ACTIONS:
        raise ValueError("Unsupported ESP assignment audit action")
    assignment_id = (after or before).get("id")
    note_present = bool((after or before).get("note_present"))
    clean_before = {key: value for key, value in before.items() if key != "note_present"}
    clean_after = {key: value for key, value in after.items() if key != "note_present"}
    with control._connect() as con:
        control._audit(
            con,
            action=action,
            target_user_id=creator_user_id,
            before=clean_before,
            after=clean_after,
            metadata={
                "relationship": "agent_creator",
                "agent_user_id": agent_user_id,
                "assignment_id": assignment_id,
                "note_present": note_present,
                "subscription_changed": False,
            },
            actor=actor,
        )


@router.get("/owner/esp-access", response_class=HTMLResponse, include_in_schema=False)
def owner_esp_access(request: Request, message: str = ""):
    if not _authorized(request):
        return RedirectResponse("/owner", status_code=303)
    persona = request_owner_persona(request)
    theme = owner_theme(persona)
    rows = control.list_users()
    pending = [row for row in rows if row.get("esp_status") == "pending"]
    active_agents = [row for row in rows if row.get("esp_status") == "active" and row.get("esp_roles") in {"agent", "both"}]
    active_creators = [row for row in rows if row.get("esp_status") == "active" and row.get("esp_roles") in {"creator", "both"}]

    user_rows = []
    for row in rows:
        social = social_controls.get(row["id"])
        social_state = social.get("state") or "default"
        role = _role(row)
        if role == "owner":
            role_form = "<span class='pill good'>Owner</span>"
            social_form = "<span class='pill good'>Owner account</span>"
        else:
            role_form = f"""<form method='post' action='/owner/esp-access/{escape(row['id'], quote=True)}/role' class='inline'><select name='role'><option value='regular' {'selected' if role=='regular' else ''}>Regular</option><option value='creator' {'selected' if role=='creator' else ''}>ESP Creator</option><option value='agent' {'selected' if role=='agent' else ''}>ESP Agent</option><option value='both' {'selected' if role=='both' else ''}>Creator + Agent</option></select><button>Apply</button></form>"""
            if social_state == "suspended":
                social_form = f"""<form method='post' action='/owner/esp-access/{escape(row['id'], quote=True)}/social/restore' class='inline'><span class='pill bad'>Suspended</span><button class='goodbtn'>Restore Social Centre</button></form>"""
            else:
                social_form = f"""<form method='post' action='/owner/esp-access/{escape(row['id'], quote=True)}/social/suspend' class='inline'><input name='reason' placeholder='Reason (optional)'><button class='danger'>Suspend Social Centre</button></form>"""
        user_rows.append(
            f"""<tr><td><a href='/owner/users/{escape(row['id'], quote=True)}'><b>{escape(row.get('display_name') or row.get('email') or '')}</b></a><br><small>{escape(row.get('email') or '')}</small></td><td>{escape((row.get('plan_id') or 'free').title())}</td><td>{escape((row.get('esp_status') or 'none').title())}<br><small>{escape((row.get('esp_roles') or 'regular').replace('both','creator + agent').title())}</small></td><td>{escape((row.get('niche') or '—').replace('_',' ').title())}<br><small>{escape(row.get('network_status') or '—')}</small></td><td>{role_form}</td><td>{social_form}</td></tr>"""
        )

    pending_html = "".join(
        f"""<div class='card request'><div><b>{escape(row.get('display_name') or row.get('email') or '')}</b><br><small>{escape(row.get('email') or '')}</small><p>Requested: <b>{escape((row.get('esp_roles') or 'creator').replace('both','Creator + Agent').title())}</b><br>TikTok: {escape('@'+row['tiktok_handle'] if row.get('tiktok_handle') else '—')} · {escape(row.get('region') or '—')}</p></div><a class='btn primary' href='/owner/users/{escape(row['id'], quote=True)}'>Review user</a></div>"""
        for row in pending
    ) or "<div class='card muted'>No pending ESP requests.</div>"

    agent_options = "".join(
        f"<option value='{escape(row['id'], quote=True)}'>{escape(row.get('display_name') or row.get('email') or '')}</option>"
        for row in active_agents
    )
    creator_options = "".join(
        f"<option value='{escape(row['id'], quote=True)}'>{escape(row.get('display_name') or row.get('email') or '')}</option>"
        for row in active_creators
    )
    assignment_rows = []
    for agent in active_agents:
        for item in assignments.for_agent(agent["id"]):
            assignment_rows.append(
                f"""<tr><td>{escape(agent.get('display_name') or agent.get('email') or '')}</td><td>{escape(item.get('display_name') or item.get('email') or '')}<br><small>{escape('@'+item['tiktok_handle'] if item.get('tiktok_handle') else '')}</small></td><td>{escape((item.get('niche') or '—').replace('_',' ').title())}</td><td>{escape(item.get('assigned_at','')[:16].replace('T',' '))}</td><td><form method='post' action='/owner/esp-access/assignments/revoke'><input type='hidden' name='agent_user_id' value='{escape(agent['id'], quote=True)}'><input type='hidden' name='creator_user_id' value='{escape(item['creator_user_id'], quote=True)}'><button class='danger'>Revoke assignment</button></form></td></tr>"""
            )
    assignments_html = "".join(assignment_rows) or "<tr><td colspan='5' class='muted'>No active Agent→Creator assignments yet.</td></tr>"

    flash = f"<div class='flash'>{escape(message)}</div>" if message else ""
    html = f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='noindex,nofollow'><title>ESP Access Control</title><style>
    :root{{--a:{theme.accent};--b:{theme.secondary};--bg:#06050b;--panel:#12101a;--line:#ffffff1e;--text:#fff;--muted:#c1bacb;--good:#7ce0a8;--bad:#ff94a6;--gold:#f4c873}}*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 8% 0,var(--a),transparent 25%),radial-gradient(circle at 92% 0,var(--b),transparent 30%),#06050b;color:var(--text);font-family:Inter,system-ui,sans-serif}}a{{color:inherit;text-decoration:none}}.wrap{{width:min(1500px,calc(100% - 28px));margin:auto;padding:28px 0 70px}}.top{{display:flex;justify-content:space-between;gap:12px;align-items:center;flex-wrap:wrap}}.eyebrow{{color:var(--gold);font-size:.72rem;text-transform:uppercase;letter-spacing:.15em;font-weight:950}}h1{{font-size:clamp(2.6rem,6vw,5.2rem);letter-spacing:-.055em;margin:.12em 0}}p,.muted,small{{color:var(--muted)}}.card{{border:1px solid var(--line);border-radius:18px;padding:16px;background:#11101ae8;margin:12px 0}}.request{{display:flex;justify-content:space-between;gap:15px;align-items:center}}.btn,button{{border:1px solid var(--line);border-radius:10px;padding:8px 11px;background:#ffffff0a;color:#fff;font-weight:800;cursor:pointer}}.primary{{border:0;background:linear-gradient(110deg,var(--a),var(--b));color:#130a18}}.danger{{background:#5a2132}}.goodbtn{{background:#174c34}}.pill{{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:4px 7px;font-size:.7rem}}.pill.good{{color:var(--good)}}.pill.bad{{color:var(--bad)}}.flash{{border-left:4px solid var(--gold);padding:12px;background:#ffffff09;border-radius:10px;margin:12px 0}}.scroll{{overflow:auto}}table{{width:100%;border-collapse:collapse;min-width:1220px}}th,td{{padding:10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}th{{font-size:.72rem;color:var(--muted);text-transform:uppercase}}select,input{{border:1px solid var(--line);border-radius:9px;padding:8px;background:#08070d;color:#fff}}.inline{{display:grid;gap:6px}}.grid{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}@media(max-width:850px){{.grid{{grid-template-columns:1fr}}}}
    </style></head><body><main class='wrap'><header class='top'><div><div class='eyebrow'>Mary / Kev owner administration</div><h1>ESP Access & Social Control</h1><p>Creative subscription and ESP access are independent. This page controls only the private ESP permission dimension and assigned-creator relationships.</p></div><div><a class='btn' href='/owner/dashboard'>Owner Dashboard</a> <a class='btn' href='/owner/users'>All Users</a></div></header>{flash}
    <section><div class='eyebrow'>Pending verification</div><h2>ESP Creator / Agent requests</h2>{pending_html}</section>
    <section class='card'><div class='eyebrow'>Manual control</div><h2>Every user</h2><p class='muted'>Set Regular / ESP Creator / ESP Agent / Both, or suspend only the Social Media Centre. Revoking ESP does not alter Free/Basic/Pro.</p><div class='scroll'><table><thead><tr><th>User</th><th>Creative plan</th><th>ESP state</th><th>Niche / network</th><th>ESP role</th><th>Social Media Centre</th></tr></thead><tbody>{''.join(user_rows)}</tbody></table></div></section>
    <section class='grid'><div class='card'><div class='eyebrow'>No-poaching assignment boundary</div><h2>Assign ESP creator to ESP agent</h2><p class='muted'>Agents only see creators explicitly assigned here. Both accounts must already have active ESP roles.</p><form method='post' action='/owner/esp-access/assignments'><label>Agent</label><select name='agent_user_id' required>{agent_options}</select><label>Creator</label><select name='creator_user_id' required>{creator_options}</select><label>Internal note</label><input name='note' placeholder='Optional assignment note'><button class='primary'>Assign creator</button></form></div><div class='card'><div class='eyebrow'>Operational rule</div><h2>Immediate revocation</h2><p>Changing a member to <b>Regular</b> blocks Level Up Hub and all ESP social/agent routes immediately. Suspending Social Media Centre leaves approved ESP training intact while blocking social APIs. Agent→Creator assignments can be revoked separately.</p></div></section>
    <section class='card'><div class='eyebrow'>Current relationships</div><h2>Agent → Creator assignments</h2><div class='scroll'><table><thead><tr><th>Agent</th><th>Creator</th><th>Niche</th><th>Assigned</th><th></th></tr></thead><tbody>{assignments_html}</tbody></table></div></section></main></body></html>"""
    return HTMLResponse(html)


@router.post("/owner/esp-access/{user_id}/role", include_in_schema=False)
def set_role(user_id: str, request: Request, role: str = Form(...)):
    if not _authorized(request):
        return RedirectResponse("/owner", status_code=303)
    try:
        control.set_esp_role(user_id, role, owner_actor("ESP Owner"))
        return _go(f"ESP role updated to {role}. Creative subscription was not changed.")
    except ValueError as exc:
        return _go(str(exc))


@router.post("/owner/esp-access/{user_id}/social/suspend", include_in_schema=False)
def suspend_social(user_id: str, request: Request, reason: str = Form("")):
    if not _authorized(request):
        return RedirectResponse("/owner", status_code=303)
    try:
        social_controls.suspend(user_id, actor=owner_actor("ESP Owner"), reason=reason)
        return _go("ESP Social Media Centre access suspended.")
    except ValueError as exc:
        return _go(str(exc))


@router.post("/owner/esp-access/{user_id}/social/restore", include_in_schema=False)
def restore_social(user_id: str, request: Request):
    if not _authorized(request):
        return RedirectResponse("/owner", status_code=303)
    try:
        social_controls.restore(user_id, actor=owner_actor("ESP Owner"))
        return _go("ESP Social Media Centre access restored subject to normal ESP/no-other-network checks.")
    except ValueError as exc:
        return _go(str(exc))


@router.post("/owner/esp-access/assignments", include_in_schema=False)
def assign_creator(
    request: Request,
    agent_user_id: str = Form(...),
    creator_user_id: str = Form(...),
    note: str = Form(""),
):
    if not _authorized(request):
        return RedirectResponse("/owner", status_code=303)
    try:
        actor = owner_actor("ESP Owner")
        before = _assignment_snapshot(agent_user_id, creator_user_id)
        assignments.assign(agent_user_id, creator_user_id, actor=actor, note=note)
        after = _assignment_snapshot(agent_user_id, creator_user_id)
        _record_assignment_audit(
            action="esp_agent_creator_assigned",
            actor=actor,
            agent_user_id=agent_user_id,
            creator_user_id=creator_user_id,
            before=before,
            after=after,
        )
        return _go("Creator assigned to ESP Agent.")
    except ValueError as exc:
        return _go(str(exc))


@router.post("/owner/esp-access/assignments/revoke", include_in_schema=False)
def revoke_assignment(
    request: Request,
    agent_user_id: str = Form(...),
    creator_user_id: str = Form(...),
):
    if not _authorized(request):
        return RedirectResponse("/owner", status_code=303)
    try:
        actor = owner_actor("ESP Owner")
        before = _assignment_snapshot(agent_user_id, creator_user_id)
        assignments.revoke(agent_user_id, creator_user_id, actor=actor)
        after = _assignment_snapshot(agent_user_id, creator_user_id)
        _record_assignment_audit(
            action="esp_agent_creator_revoked",
            actor=actor,
            agent_user_id=agent_user_id,
            creator_user_id=creator_user_id,
            before=before,
            after=after,
        )
        return _go("Agent→Creator assignment revoked.")
    except ValueError as exc:
        return _go(str(exc))
