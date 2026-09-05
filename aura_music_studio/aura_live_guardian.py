from __future__ import annotations

import json
import os
from html import escape
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .aura_live_guardian_readiness_ui import live_guardian_readiness_page
from .aura_live_guardian_review_ui import (
    acknowledge_guardian_escalation,
    confirm_guardian_review,
    dismiss_guardian_review,
    guardian_review_page,
)
from .aura_live_guardian_status_ui import live_guardian_status
from .aura_live_moderator import (
    AURA_LIVE_MODERATOR_HANDLE,
    AURA_LIVE_MODERATOR_PROFILE_URL,
    AuraModeratorAuthorization,
    ModerationMode,
)
from .aura_live_moderator_store import AuraLiveModeratorStore
from .branding import PRODUCT_FULL_NAME

router = APIRouter(tags=["Aura LIVE Guardian"])
# Keep the production Guardian router flat. Register bounded creator safety handlers directly
# rather than nesting APIRouters so route composition remains stable across FastAPI versions.
router.add_api_route("/live-guardian/review", guardian_review_page, methods=["GET"], response_class=HTMLResponse, include_in_schema=False)
router.add_api_route("/live-guardian/review/{review_id}/confirm", confirm_guardian_review, methods=["POST"], include_in_schema=False)
router.add_api_route("/live-guardian/review/{review_id}/dismiss", dismiss_guardian_review, methods=["POST"], include_in_schema=False)
router.add_api_route("/live-guardian/review/{review_id}/acknowledge", acknowledge_guardian_escalation, methods=["POST"], include_in_schema=False)
router.add_api_route("/live-guardian/readiness", live_guardian_readiness_page, methods=["GET"], response_class=HTMLResponse, include_in_schema=False)
router.add_api_route("/live-guardian/status", live_guardian_status, methods=["GET"], include_in_schema=False)


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Sign in required")
    return member


def _store() -> AuraLiveModeratorStore:
    path = Path(os.getenv("AURA_LIVE_MODERATOR_DB", "data/aura_live_moderator.sqlite3"))
    path.parent.mkdir(parents=True, exist_ok=True)
    return AuraLiveModeratorStore(path)


def _actor(member) -> str:
    return f"member:{member.user_id}"


def _checked(value: bool) -> str:
    return " checked" if value else ""


@router.get("/live-guardian", response_class=HTMLResponse, include_in_schema=False)
def live_guardian(request: Request):
    member = _member(request)
    store = _store()
    stored = store.get(member.user_id)
    authorization = stored.authorization if stored else None
    events = store.recent_events(member.user_id, limit=20)
    chain_ok = store.verify_audit_chain(member.user_id)

    handle = authorization.creator_handle if authorization else ""
    consent = authorization.creator_consent if authorization else False
    assignment = authorization.moderator_assignment_confirmed if authorization else False
    mode = authorization.mode.value if authorization else ModerationMode.ADVISORY.value
    provider_write = authorization.provider_write_enabled if authorization else False

    audit_rows = "".join(
        "<tr>" f"<td>{escape(event.created_at.isoformat())}</td>" f"<td>{escape(event.event_type.replace('_', ' ').title())}</td>" f"<td>{escape(event.actor)}</td>" f"<td><code>{escape(json.dumps(event.metadata, sort_keys=True, ensure_ascii=False))}</code></td>" "</tr>"
        for event in events
    ) or "<tr><td colspan='4' class='muted'>No Aura LIVE moderation events yet.</td></tr>"
    options = "".join(f"<option value='{item.value}'{' selected' if mode == item.value else ''}>{escape(item.value.replace('_', ' ').title())}</option>" for item in ModerationMode)
    display_name = escape(getattr(member, "display_name", "Creator") or "Creator")
    html = f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='referrer' content='no-referrer'><meta name='robots' content='noindex,nofollow'><title>Aura LIVE Guardian — {escape(PRODUCT_FULL_NAME)}</title><style>
body{{margin:0;background:#070812;color:#fff;font-family:Inter,system-ui,sans-serif}}.wrap{{width:min(1180px,calc(100% - 28px));margin:auto;padding:34px 0 60px}}h1{{font-size:clamp(2.5rem,6vw,5rem);margin:.1em 0}}.grid{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}.card{{background:#111526;border:1px solid #ffffff20;border-radius:18px;padding:18px;margin-bottom:14px}}.muted{{color:#bdc6d8;line-height:1.55}}label{{display:block;font-weight:800;margin:12px 0 5px}}input,select,button,.btn{{font:inherit;border:1px solid #ffffff25;border-radius:10px;background:#ffffff0d;color:#fff;padding:10px 12px}}input[type=text],select{{width:100%}}input[type=checkbox]{{width:auto;margin-right:8px}}button,.btn{{cursor:pointer;font-weight:850;text-decoration:none;display:inline-block}}.primary{{background:linear-gradient(120deg,#efc96b,#9b72ff);color:#160b22;border:0}}.safe{{color:#8ef0b0}}.warn{{color:#ffd483}}.row{{display:flex;gap:10px;align-items:center;flex-wrap:wrap}}table{{width:100%;border-collapse:collapse}}th,td{{padding:10px;border-bottom:1px solid #ffffff16;text-align:left;vertical-align:top}}code{{word-break:break-word;color:#efc96b}}@media(max-width:800px){{.grid{{grid-template-columns:1fr}}}}
</style></head><body><main class='wrap'><div style='color:#efc96b;font-weight:900'>Powered by Aura AI · Creator LIVE Safety</div><h1>Aura LIVE Guardian</h1><p class='muted'>Welcome {display_name}. Configure Aura's official TikTok LIVE moderation identity and review the safety evidence for your LIVE sessions. Adding Aura as a TikTok moderator does not by itself grant the Command Center provider-write authority.</p>
<div class='grid'><section><div class='card'><h2>Official Aura moderator</h2><p><b>@{AURA_LIVE_MODERATOR_HANDLE}</b></p><p class='muted'>Assign this official Aura profile as a moderator from TikTok's supported LIVE moderation controls, then confirm the assignment here.</p><a class='btn' href='{AURA_LIVE_MODERATOR_PROFILE_URL}' target='_blank' rel='noopener noreferrer'>Open Aura TikTok profile</a></div>
<div class='card'><h2>Your authorization</h2><form method='post' action='/live-guardian/settings'><label>Your TikTok handle</label><input name='creator_handle' type='text' maxlength='24' required value='{escape(handle, quote=True)}' placeholder='your.handle'><label><input name='creator_consent' type='checkbox' value='true'{_checked(consent)}> I explicitly authorize Aura to assist with moderation for my LIVE.</label><label><input name='moderator_assignment_confirmed' type='checkbox' value='true'{_checked(assignment)}> I confirm @{AURA_LIVE_MODERATOR_HANDLE} has been assigned as a TikTok LIVE moderator.</label><label>Moderation mode</label><select name='mode'>{options}</select><p class='muted'><b>Advisory</b> recommends only. <b>Assisted</b> keeps human confirmation for provider actions. <b>Auto Protect</b> can automate only bounded lower-severity actions when an approved connector explicitly grants that capability.</p><button class='primary' type='submit'>Save LIVE Guardian settings</button></form>{"<form method='post' action='/live-guardian/revoke' style='margin-top:12px'><button type='submit'>Revoke Aura LIVE authorization</button></form>" if stored else ""}</div></section>
<section><div class='card'><h2>Protection status</h2><p>Creator consent: <b class='{'safe' if consent else 'warn'}'>{'Active' if consent else 'Not granted'}</b></p><p>Moderator assignment: <b class='{'safe' if assignment else 'warn'}'>{'Confirmed' if assignment else 'Not confirmed'}</b></p><p>Mode: <b>{escape(mode.replace('_', ' ').title())}</b></p><p>Approved provider-write path: <b class='{'safe' if provider_write else 'warn'}'>{'Enabled after review' if provider_write else 'Unavailable / recommendation only'}</b></p><p class='muted'>Creators cannot self-enable provider writes from this page. That state requires a separately reviewed approval and an approved TikTok/partner LIVE connector. High-severity threat, doxxing and grooming signals remain human-escalated.</p></div>
<div class='card'><h2>LIVE creator tools</h2><div class='row'><a class='btn primary' href='/live-guardian/readiness'>Safety Readiness</a><a class='btn' href='/live-guardian/review'>Human Review Queue</a><a class='btn' href='/live-overlay-studio'>Overlay Studio</a><a class='btn' href='/live-overlay-studio/prompter'>Auto Cue Prompter</a><a class='btn' href='/live-guardian/policy'>Moderation Policy</a></div><p class='muted'>The private Guardian status endpoint provides bounded safety state for future creator LIVE surfaces. It never asserts provider execution authority. Assisted-mode recommendations and critical escalations remain human controlled.</p></div><div class='card'><h2>Audit integrity</h2><p>Moderation audit chain: <b class='{'safe' if chain_ok else 'warn'}'>{'Verified' if chain_ok else 'Integrity check failed'}</b></p><p class='muted'>Authorization and moderation evidence is append-only and hash chained. TikTok passwords, session cookies, access tokens, private keys and provider secrets are not stored here.</p></div></section></div>
<div class='card'><h2>Recent Aura LIVE moderation evidence</h2><div style='overflow:auto'><table><thead><tr><th>Time</th><th>Event</th><th>Actor</th><th>Evidence</th></tr></thead><tbody>{audit_rows}</tbody></table></div></div></main></body></html>"""
    return HTMLResponse(html, headers={"Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer", "X-Robots-Tag": "noindex, nofollow"})


@router.post("/live-guardian/settings", include_in_schema=False)
def save_live_guardian_settings(request: Request, creator_handle: str = Form(...), mode: ModerationMode = Form(ModerationMode.ADVISORY), creator_consent: bool = Form(False), moderator_assignment_confirmed: bool = Form(False)):
    member = _member(request)
    authorization = AuraModeratorAuthorization(creator_handle=creator_handle, creator_consent=creator_consent, moderator_assignment_confirmed=moderator_assignment_confirmed, mode=mode, provider_write_enabled=False)
    _store().save_creator_authorization(user_id=member.user_id, authorization=authorization, actor=_actor(member))
    return RedirectResponse("/live-guardian", status_code=303)


@router.post("/live-guardian/revoke", include_in_schema=False)
def revoke_live_guardian(request: Request):
    member = _member(request)
    try:
        _store().revoke(user_id=member.user_id, actor=_actor(member))
    except KeyError as exc:
        raise HTTPException(404, "Aura LIVE authorization not found") from exc
    return RedirectResponse("/live-guardian", status_code=303)


__all__ = ["router", "live_guardian", "save_live_guardian_settings", "revoke_live_guardian"]
