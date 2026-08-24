from __future__ import annotations

from html import escape

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .esp_command_center import esp
from .esp_level_up import HUB_NAME
from .esp_niche import EspNicheStore

router = APIRouter()
niches = EspNicheStore()


def _member(request: Request):
    return getattr(request.state, "member", None)


def _shell(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1,viewport-fit=cover'>
        <meta name='robots' content='noindex,nofollow'><title>{escape(title)}</title><style>
        :root{{--bg:#03040a;--panel:#0e1020;--line:#ffffff1d;--gold:#f4c873;--violet:#9f73ff;--text:#fff;--muted:#bcbfd1;--good:#78e0a7}}
        *{{box-sizing:border-box}}body{{margin:0;min-height:100vh;color:var(--text);font-family:Inter,system-ui,sans-serif;background:radial-gradient(circle at 10% 0,#481b68,transparent 31%),radial-gradient(circle at 92% 0,#163b62,transparent 25%),linear-gradient(#03040a,#080812 65%,#020309)}}a{{color:inherit;text-decoration:none}}.wrap{{width:min(1050px,calc(100% - 28px));margin:auto;padding:42px 0 70px}}.eyebrow{{color:var(--gold);font-size:.72rem;text-transform:uppercase;letter-spacing:.16em;font-weight:950}}h1{{font-size:clamp(2.7rem,7vw,5.6rem);letter-spacing:-.06em;line-height:.92;margin:.15em 0 .25em}}.lead,p{{color:var(--muted);line-height:1.62}}.card{{border:1px solid var(--line);border-radius:22px;padding:20px;background:linear-gradient(145deg,#14172ae8,#080a14f2);margin:14px 0}}.grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:11px}}.btn,button{{display:inline-block;border:1px solid var(--line);border-radius:12px;padding:10px 14px;background:#ffffff08;color:#fff;font-weight:850;cursor:pointer}}.primary{{border:0;background:linear-gradient(115deg,var(--gold),var(--violet));color:#140a1a}}label{{display:block;font-weight:800;margin:10px 0 6px}}input,select,textarea{{width:100%;padding:11px;border-radius:11px;border:1px solid var(--line);background:#070913;color:#fff}}textarea{{min-height:100px}}.status{{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:5px 8px;font-size:.75rem;color:var(--good)}}.warn{{border-left:4px solid var(--gold)}}@media(max-width:700px){{.grid{{grid-template-columns:1fr}}}}
        </style></head><body><main class='wrap'>{body}</main></body></html>"""
    )


@router.get("/auth/esp", response_class=HTMLResponse, include_in_schema=False)
def esp_access_intro():
    return _shell(
        "ESP Level Up Hub Access",
        f"""<div class='eyebrow'>Elevate Souls Productions · Private Creator & Agent System</div><h1>{escape(HUB_NAME)}</h1>
        <p class='lead'>Every person starts with a normal Pulsar-Frequency House account. Free, Basic and Pro control the public creative tools only. ESP Creator/Agent access is a separate permission that can only be activated by Mary or Kev after review.</p>
        <section class='grid'><div class='card'><h2>1 · Sign up</h2><p>Create a normal account. Free is enough to request ESP verification.</p></div><div class='card'><h2>2 · Request</h2><p>Select Creator, Agent, or Creator + Agent and submit your TikTok identity/region for owner assessment.</p></div><div class='card'><h2>3 · Owner decision</h2><p>Mary/Kev approve, decline, change or revoke the ESP role. Requesting access never self-unlocks private resources.</p></div></section>
        <section class='card warn'><h2>No-poaching boundary</h2><p>ESP social-management tools are only for approved ESP members and are blocked for accounts represented by another Creator Network. Agent oversight is limited to explicitly assigned ESP creators.</p></section>
        <a class='btn primary' href='/signup'>Create Free Account</a> <a class='btn' href='/signin?next=/command-center'>Sign in & request ESP access</a>""",
    )


@router.get("/command-center", response_class=HTMLResponse, include_in_schema=False)
def esp_gateway(request: Request):
    """Canonical ESP entrypoint.

    This route is intentionally registered before the older Command Center route. It keeps
    request/approval separate from subscription tier and sends active members into the
    niche-personalised Level Up Hub.
    """
    member = _member(request)
    if member is None:
        return RedirectResponse("/signin?next=/command-center", status_code=303)

    membership = esp.membership(member.user_id)
    if membership and membership.get("status") in {"active", "owner"}:
        profile = niches.get(member.user_id)
        if profile is None:
            return RedirectResponse("/command-center/niche", status_code=303)
        return RedirectResponse("/command-center/level-up", status_code=303)

    pending = esp.pending_for_user(member.user_id)
    status = (membership or {}).get("status") or "none"
    if pending:
        return _shell(
            "ESP Access Pending",
            f"""<div class='eyebrow'>Elevate Souls Productions · Owner Review</div><h1>Your ESP request is pending.</h1><p class='lead'>You remain a normal <b>{escape(str(member.user.get('plan_id') or 'free').title())}</b> Pulsar-Frequency House member while Mary/Kev review the request. No ESP Creator, Agent, Level Up Hub, Social Media Centre, confidential training or creator-management permissions are granted until ownership approves them.</p><section class='card'><span class='status'>Pending owner decision</span><h2>Requested: {escape(str(pending.get('requested_role') or '').replace('both','Creator + Agent').title())}</h2><p>TikTok: @{escape(str(pending.get('tiktok_handle') or ''))}<br>Region: {escape(str(pending.get('region') or 'Not supplied'))}</p></section><a class='btn' href='/dashboard'>Back to Creative Studio</a>""",
        )

    status_note = ""
    if status == "rejected":
        status_note = "Your previous ESP request was declined. You can submit a new request if ownership has asked you to do so."
    elif status == "revoked":
        status_note = "Your previous ESP access has been revoked. Private ESP systems remain locked unless ownership activates your role again."

    return _shell(
        "Request ESP Access",
        f"""<div class='eyebrow'>Elevate Souls Productions · Verification Required</div><h1>I am an ESP Creator or Agent.</h1><p class='lead'>Submitting this form does not grant access. It creates an owner-review request for Mary/Kev. Your current <b>{escape(str(member.user.get('plan_id') or 'free').title())}</b> creative subscription stays exactly as it is.</p>
        {f"<section class='card warn'><b>{escape(status_note)}</b></section>" if status_note else ''}
        <form class='card' method='post' action='/auth/esp/request'><label>ESP role requested</label><select name='requested_role'><option value='creator'>ESP Creator</option><option value='agent'>ESP Agent</option><option value='both'>ESP Creator + Agent</option></select><label>TikTok handle</label><input name='tiktok_handle' placeholder='@yourhandle' required><label>Region</label><input name='region' placeholder='UK+, USA/Canada, LATAM, AU/NZ'><label>Anything Mary/Kev should know?</label><textarea name='note' placeholder='Optional verification note'></textarea><button class='primary' type='submit'>Send request for Mary/Kev review</button></form>
        <section class='card warn'><b>Private means private.</b><p>Until ownership approves the account, the Level Up Hub and Social Media Centre stay inaccessible. If approved, you will next select your niche and confirm Creator Network affiliation. ESP does not use these tools to poach creators from other networks.</p></section><a class='btn' href='/dashboard'>Back to Creative Studio</a>""",
    )
