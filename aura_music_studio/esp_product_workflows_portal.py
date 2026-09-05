from __future__ import annotations

from html import escape
from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .esp_product_workflows import (
    CreatorProfileUpdate,
    LeadCreate,
    StaleVersionError,
    _agent_access,
    _creator_access,
    workflows,
)

router = APIRouter(tags=["Chat 9 Product Workflow Portal"])

CSS = """
:root{color-scheme:dark;--bg:#09050f;--panel:#171020;--panel2:#21142e;--gold:#e7bd63;--text:#fff;--muted:#c8bdd2;--line:#473452;--ok:#78d99c;--bad:#ff93a6}
*{box-sizing:border-box}body{font-family:Inter,ui-sans-serif,system-ui,sans-serif;background:radial-gradient(circle at top right,#2b123f 0,#09050f 42%);color:var(--text);margin:0;min-height:100vh}.wrap{max-width:1120px;margin:auto;padding:24px}.top{display:flex;gap:12px;justify-content:space-between;align-items:flex-start;flex-wrap:wrap}.card{background:linear-gradient(145deg,var(--panel),#120b19);border:1px solid var(--line);border-radius:18px;padding:18px;margin:14px 0}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.btn,button{display:inline-block;border:0;border-radius:11px;padding:11px 15px;font-weight:800;cursor:pointer;text-decoration:none;background:var(--gold);color:#1c1024}.secondary{background:#352443;color:#fff}.muted{color:var(--muted)}label{display:block;font-weight:750;margin-top:10px}input,select,textarea{width:100%;padding:12px;border-radius:10px;border:1px solid var(--line);background:#0e0814;color:#fff;margin-top:6px}textarea{min-height:100px;resize:vertical}.check{display:flex;gap:10px;align-items:flex-start}.check input{width:auto;margin-top:5px}.notice{border-left:4px solid var(--gold)}.error{border-left-color:var(--bad)}table{width:100%;border-collapse:collapse}th,td{padding:10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}a{color:#f3d58d}:focus-visible{outline:3px solid #fff;outline-offset:3px}@media(max-width:760px){.grid{grid-template-columns:1fr}.wrap{padding:14px}table{display:block;overflow-x:auto}}
@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important;transition:none!important;animation:none!important}}
"""


def _page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{escape(title)}</title><style>{CSS}</style></head><body><main class='wrap'>{body}</main></body></html>",
        headers={"Cache-Control": "no-store"},
    )


def _lines(value: str) -> list[str]:
    return [line.strip() for line in (value or "").splitlines() if line.strip()]


def _csv(value: str) -> list[str]:
    return [part.strip() for part in (value or "").split(",") if part.strip()]


def _joined(value: list[str] | None) -> str:
    return "\n".join(value or [])


@router.get("/command-center/onboarding", response_class=HTMLResponse, include_in_schema=False)
def creator_onboarding(request: Request, saved: int = 0, error: str = ""):
    member, _membership = _creator_access(request)
    profile = workflows.profile(member.user_id) or {}
    version = int(profile.get("version") or 0)
    public_links = profile.get("public_social_links") or {}
    status = ""
    if saved:
        status = "<div class='card notice' role='status'><b>Saved.</b> Your Creator profile and onboarding record are durable.</div>"
    elif error:
        status = f"<div class='card notice error' role='alert'><b>Could not save.</b> {escape(error)}</div>"
    checked = " checked" if profile.get("discoverable") else ""
    standards_checked = " checked" if (profile.get("acknowledgements") or {}).get("professional_standards") else ""
    complete = profile.get("onboarding_status") == "complete"
    body = f"""
    <div class='top'><div><div class='muted'>Elevate Souls Productions</div><h1>Creator onboarding &amp; public identity</h1>
    <p class='muted'>Private development information is stored separately from the Shared Sky public creator profile.</p></div>
    <div><a class='btn secondary' href='/command-center/level-up'>Back to Level Up</a></div></div>
    {status}
    <form method='post' action='/command-center/onboarding'>
      <input type='hidden' name='expected_version' value='{version}'>
      <section class='card' aria-labelledby='public-heading'><h2 id='public-heading'>Public / discovery-safe profile</h2>
        <div class='grid'>
          <label>Public display name<input name='public_display_name' maxlength='120' value='{escape(str(profile.get("public_display_name") or ""))}' autocomplete='nickname'></label>
          <label>Public region (optional)<input name='public_region' maxlength='120' value='{escape(str(profile.get("public_region") or ""))}'></label>
          <label>Primary niche<input name='primary_niche' maxlength='120' value='{escape(str(profile.get("primary_niche") or ""))}'></label>
          <label>Secondary niche<input name='secondary_niche' maxlength='120' value='{escape(str(profile.get("secondary_niche") or ""))}'></label>
          <label>Languages, comma-separated<input name='languages' value='{escape(", ".join(profile.get("languages") or []))}'></label>
          <label>TikTok/public social URL<input name='public_social_url' inputmode='url' value='{escape(str(public_links.get("primary") or ""))}'></label>
        </div>
        <label>Public bio<textarea name='bio' maxlength='1200'>{escape(str(profile.get("bio") or ""))}</textarea></label>
        <label class='check'><input type='checkbox' name='discoverable' value='true'{checked}><span>Make this profile discoverable to Shared Sky. Turning this off removes the Chat 9 public profile read model immediately.</span></label>
      </section>
      <section class='card' aria-labelledby='private-heading'><h2 id='private-heading'>Private Creator development record</h2>
        <p class='muted'>These fields are not returned by the public Shared Sky creator endpoint.</p>
        <div class='grid'>
          <label>Time zone<input name='timezone' maxlength='80' value='{escape(str(profile.get("timezone") or ""))}' placeholder='Europe/London'></label>
          <label>Specialisms, comma-separated<input name='specialisms' value='{escape(", ".join(profile.get("specialisms") or []))}'></label>
        </div>
        <label>LIVE experience<textarea name='live_experience' maxlength='2000'>{escape(str(profile.get("live_experience") or ""))}</textarea></label>
        <label>Goals — one per line<textarea name='goals'>{escape(_joined(profile.get("goals")))}</textarea></label>
        <label>Equipment — one item per line<textarea name='equipment'>{escape(_joined(profile.get("equipment")))}</textarea></label>
        <label class='check'><input type='checkbox' name='professional_standards' value='true'{standards_checked}><span>I acknowledge the current ESP professional standards and policy requirements shown in my assigned training.</span></label>
        <label>Onboarding status<select name='onboarding_status'>
          <option value='in_progress'{' selected' if not complete else ''}>In progress</option>
          <option value='complete'{' selected' if complete else ''}>Complete</option>
        </select></label>
      </section>
      <button type='submit'>Save onboarding</button>
    </form>"""
    return _page("Creator onboarding", body)


@router.post("/command-center/onboarding", include_in_schema=False)
def save_creator_onboarding(
    request: Request,
    expected_version: int = Form(...),
    public_display_name: str = Form(""),
    public_region: str = Form(""),
    primary_niche: str = Form(""),
    secondary_niche: str = Form(""),
    languages: str = Form(""),
    public_social_url: str = Form(""),
    bio: str = Form(""),
    discoverable: bool = Form(False),
    timezone: str = Form(""),
    specialisms: str = Form(""),
    live_experience: str = Form(""),
    goals: str = Form(""),
    equipment: str = Form(""),
    professional_standards: bool = Form(False),
    onboarding_status: str = Form("in_progress"),
):
    member, _membership = _creator_access(request)
    current = workflows.profile(member.user_id) or {}
    try:
        workflows.save_profile(
            member.user_id,
            CreatorProfileUpdate(
                expected_version=expected_version,
                public_display_name=public_display_name,
                avatar_ref=current.get("avatar_ref"),
                banner_ref=current.get("banner_ref"),
                bio=bio,
                public_region=public_region,
                languages=_csv(languages),
                primary_niche=primary_niche,
                secondary_niche=secondary_niche,
                public_social_links={"primary": public_social_url.strip()} if public_social_url.strip() else {},
                discoverable=discoverable,
                timezone=timezone,
                live_experience=live_experience,
                goals=_lines(goals),
                schedule=current.get("schedule") or {},
                equipment=_lines(equipment),
                specialisms=_csv(specialisms),
                acknowledgements={"professional_standards": professional_standards},
                onboarding_status="complete" if onboarding_status == "complete" else "in_progress",
            ),
            actor=member.user_id,
        )
    except StaleVersionError:
        return RedirectResponse("/command-center/onboarding?error=" + quote("This record changed in another session. Reload and review the latest version before saving."), status_code=303)
    return RedirectResponse("/command-center/onboarding?saved=1", status_code=303)


@router.get("/command-center/agent/leads", response_class=HTMLResponse, include_in_schema=False)
def agent_lead_center(request: Request, created: int = 0, error: str = ""):
    member, _membership = _agent_access(request)
    leads = workflows.leads_for_agent(member.user_id)
    notice = ""
    if created:
        notice = "<div class='card notice' role='status'><b>Lead saved.</b> Global platform/handle deduplication is active.</div>"
    elif error:
        notice = f"<div class='card notice error' role='alert'><b>Lead not saved.</b> {escape(error)}</div>"
    rows = "".join(
        f"<tr><td>@{escape(str(row['handle']))}</td><td>{escape(str(row['platform']))}</td>"
        f"<td>{escape(str(row.get('region') or ''))}</td><td>{escape(str(row.get('niche') or ''))}</td>"
        f"<td>{escape(str(row['status']))}</td><td>{'Yes' if row.get('do_not_contact') else 'No'}</td>"
        f"<td>{escape(str(row.get('follow_up_at') or ''))}</td></tr>"
        for row in leads
    ) or "<tr><td colspan='7' class='muted'>No leads are assigned to you.</td></tr>"
    body = f"""
    <div class='top'><div><div class='muted'>Agent / Mentor Command Center</div><h1>Recruitment lead CRM</h1>
    <p class='muted'>Manual, lawful or authorised discovery inputs only. This surface does not scrape private data or automate outreach.</p></div>
    <a class='btn secondary' href='/command-center/level-up'>Back to Level Up</a></div>
    {notice}
    <section class='card'><h2>Add public lead</h2><form method='post' action='/command-center/agent/leads'>
      <div class='grid'><label>Platform<input name='platform' required maxlength='80' placeholder='TikTok'></label>
      <label>Public handle<input name='handle' required maxlength='120' placeholder='creator.handle'></label>
      <label>Public profile URL<input name='public_profile_url' inputmode='url' maxlength='1000'></label>
      <label>Region<input name='region' maxlength='120'></label>
      <label>Niche<input name='niche' maxlength='120'></label>
      <label>Follow-up date/time<input name='follow_up_at' maxlength='80' placeholder='2026-09-10T14:00:00+01:00'></label></div>
      <label>Notes<textarea name='notes' maxlength='2000'></textarea></label>
      <button type='submit'>Add lead</button>
    </form></section>
    <section class='card'><h2>My assigned leads</h2><div role='region' aria-label='Assigned leads' tabindex='0'>
      <table><thead><tr><th>Handle</th><th>Platform</th><th>Region</th><th>Niche</th><th>Status</th><th>Do not contact</th><th>Follow-up</th></tr></thead><tbody>{rows}</tbody></table>
    </div></section>"""
    return _page("Recruitment lead CRM", body)


@router.post("/command-center/agent/leads", include_in_schema=False)
def add_agent_lead(
    request: Request,
    platform: str = Form(...),
    handle: str = Form(...),
    public_profile_url: str = Form(""),
    region: str = Form(""),
    niche: str = Form(""),
    follow_up_at: str = Form(""),
    notes: str = Form(""),
):
    member, _membership = _agent_access(request)
    try:
        workflows.create_lead(
            LeadCreate(
                platform=platform,
                handle=handle,
                public_profile_url=public_profile_url,
                region=region,
                niche=niche,
                source="manual_public_discovery",
                follow_up_at=follow_up_at.strip() or None,
                notes=notes,
            ),
            actor_user_id=member.user_id,
            assigned_agent_user_id=member.user_id,
        )
    except (FileExistsError, ValueError) as exc:
        return RedirectResponse("/command-center/agent/leads?error=" + quote(str(exc)), status_code=303)
    return RedirectResponse("/command-center/agent/leads?created=1", status_code=303)


__all__ = ["router"]
