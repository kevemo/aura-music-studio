from __future__ import annotations

from html import escape

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .accounts import AccountStore
from .branding import ENDORSEMENT, PRODUCT_FULL_NAME, PRODUCT_NAME, TAGLINE
from .esp_command_center import EspStore
from .plans import PLANS

router = APIRouter()
accounts = AccountStore()
esp = EspStore(accounts)
MEMBER_COOKIE = "lss_session"


@router.get("/video-studio", include_in_schema=False)
def video_studio_entry():
    return RedirectResponse("/creative-house?kind=video", status_code=303)


@router.get("/image-designer", include_in_schema=False)
def image_designer_entry():
    return RedirectResponse("/creative-house?kind=image", status_code=303)


@router.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
def member_dashboard(request: Request):
    user = accounts.resolve_session(request.cookies.get(MEMBER_COOKIE))
    if not user:
        return RedirectResponse("/signin", status_code=303)

    status = user.get("status") or "unknown"
    active_plan = user.get("plan_id") or "free"
    requested_plan = user.get("requested_plan_id") or active_plan
    plan = PLANS.get(active_plan, PLANS["free"])
    esp_membership = esp.membership(user["id"])
    esp_status = (esp_membership or {}).get("status") or "none"
    esp_role = (esp_membership or {}).get("roles") or ""

    if status == "active":
        account_state = f"<span class='good'>{escape(plan.name)} membership active</span>"
    elif status == "approved_pending_payment":
        account_state = f"<span class='warn'>{escape(requested_plan.upper())} approved — payment verification required</span>"
    elif status == "pending_approval":
        account_state = "<span class='warn'>Membership awaiting approval</span>"
    elif status == "rejected":
        account_state = "<span class='bad'>Membership request not approved</span>"
    else:
        account_state = f"<span class='warn'>{escape(status.replace('_',' ').title())}</span>"

    regular_cards = [
        (
            "🎵",
            "Music Studio",
            "Generate release-grade songs, record performances, build around uploads, edit stems, mix/master and keep the finished song fully editable through Song DNA.",
            "/studio",
            "Open Music Studio",
            "/song-editor",
            "Editable Song DNA",
        ),
        (
            "🎬",
            "Video Studio",
            "Build video and music-video projects with scene-level Creative DNA, references, Aura direction and connected render workflows.",
            "/video-studio",
            "Open Video Studio",
            None,
            None,
        ),
        (
            "🎨",
            "Image Designer",
            "Create covers, posters, campaign artwork and image projects with editable elements, references and Aura-guided revisions.",
            "/image-designer",
            "Open Image Designer",
            None,
            None,
        ),
        (
            "✨",
            "Aura Creative Director",
            "Talk or type to Aura for project guidance, music production decisions and natural-language creative direction.",
            "/aura",
            "Talk to Aura",
            None,
            None,
        ),
        (
            "🧠",
            "Aura Intelligence",
            "A general conversational AI workspace for research, thinking, writing, planning and project-aware assistance outside the ESP agency hub.",
            "/aura-intelligence",
            "Open Aura Intelligence",
            None,
            None,
        ),
    ]
    cards = "".join(
        f"""<article class='tool'><div class='icon'>{icon}</div><h3>{escape(title)}</h3><p>{escape(copy)}</p><div class='toolactions'><a class='btn primary' href='{escape(url, quote=True)}'>{escape(cta)}</a>{f"<a class='btn secondary' href='{escape(secondary_url, quote=True)}'>{escape(secondary_cta)}</a>" if secondary_url and secondary_cta else ''}</div></article>"""
        for icon, title, copy, url, cta, secondary_url, secondary_cta in regular_cards
    )

    if esp_status in {"active", "owner"}:
        label = "Owner" if esp_status == "owner" else (esp_role.replace("both", "Creator + Agent").title() or "ESP Member")
        esp_panel = f"""<section class='esp'><div><div class='eyebrow'>Private Elevate Souls Productions Hub</div><h2>ESP {escape(label)}</h2><p>Your ESP Creator/Agent tools are a separate protected area: niche training, progress tracking, private social-management tools and agency resources are not part of the normal Creative Studio membership.</p></div><a class='btn espbtn' href='/command-center'>Enter ESP Hub</a></section>"""
    elif esp_status == "pending":
        esp_panel = """<section class='esp pending'><div><div class='eyebrow'>ESP access request</div><h2>Waiting for Mary / Kev approval</h2><p>Your request has not activated any ESP tools. The normal Creative Studio remains separate while the ESP role is reviewed.</p></div><a class='btn' href='/command-center'>View request status</a></section>"""
    else:
        esp_panel = """<section class='esp request'><div><div class='eyebrow'>Already an ESP Creator or Agent?</div><h2>Request verification</h2><p>If you are genuinely part of Elevate Souls Productions, use the verification request. ESP access is owner-approved only and this does not recruit creators from other networks.</p></div><a class='btn espbtn' href='/command-center'>I am an Elevate Souls Productions Creator or Agent</a></section>"""

    html = f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1,viewport-fit=cover'><meta name='robots' content='noindex,nofollow'><title>Dashboard — {escape(PRODUCT_NAME)}</title><style>
:root{{--bg:#04050b;--panel:#101320;--line:#ffffff1d;--gold:#f4c873;--violet:#a66bff;--cyan:#5de7ff;--pink:#ff74d0;--good:#76dda1;--warn:#ffd17b;--bad:#ff8fa3;--text:#fff;--muted:#bcc0d2}}*{{box-sizing:border-box}}body{{margin:0;color:var(--text);font-family:Inter,ui-sans-serif,system-ui,sans-serif;background:radial-gradient(circle at 10% 0,#32134c,transparent 30%),radial-gradient(circle at 90% 0,#10374f,transparent 28%),#04050b;min-height:100vh}}a{{color:inherit;text-decoration:none}}.wrap{{width:min(1320px,calc(100% - 28px));margin:auto;padding:24px 0 55px}}.top{{display:flex;justify-content:space-between;align-items:center;gap:14px;flex-wrap:wrap}}.brand{{font-weight:950}}.brand small{{display:block;color:var(--gold);font-size:.67rem;letter-spacing:.08em;text-transform:uppercase;margin-top:3px}}.btn,button{{display:inline-block;border:1px solid var(--line);border-radius:12px;padding:10px 14px;background:#ffffff08;color:#fff;font-weight:850;cursor:pointer}}.primary{{border:0;background:linear-gradient(110deg,#fff0b0,var(--gold),#c8a0ff);color:#160c1d}}.secondary{{background:#ffffff06}}.hero{{padding:55px 0 30px}}.eyebrow{{color:var(--gold);font-size:.72rem;letter-spacing:.16em;text-transform:uppercase;font-weight:900}}h1{{font-size:clamp(2.6rem,7vw,5.8rem);line-height:.92;letter-spacing:-.06em;margin:.14em 0 .18em}}h1 span{{background:linear-gradient(100deg,#fff,var(--gold),var(--pink),var(--cyan));background-clip:text;color:transparent}}.hero p,.muted{{color:var(--muted);line-height:1.6}}.account{{display:flex;gap:9px;align-items:center;flex-wrap:wrap;margin-top:18px}}.pill{{border:1px solid var(--line);border-radius:999px;padding:6px 9px;font-size:.78rem}}.good{{color:var(--good)}}.warn{{color:var(--warn)}}.bad{{color:var(--bad)}}.tools{{display:grid;grid-template-columns:repeat(5,1fr);gap:12px}}.tool{{border:1px solid var(--line);border-radius:21px;padding:19px;background:linear-gradient(145deg,#121528e8,#080a13e8);display:flex;flex-direction:column;min-height:315px}}.tool .icon{{font-size:1.8rem}}.tool h3{{font-size:1.15rem;margin:13px 0 7px}}.tool p{{color:var(--muted);line-height:1.55;flex:1}}.toolactions{{display:grid;gap:7px}}.esp{{display:flex;justify-content:space-between;gap:24px;align-items:center;margin-top:22px;padding:22px;border:1px solid #f4c87345;border-radius:22px;background:linear-gradient(120deg,#f4c8730c,#a66bff12,#080a13)}}.esp p{{color:var(--muted);max-width:850px;line-height:1.55}}.espbtn{{background:linear-gradient(110deg,var(--gold),#bf94ff);color:#140b1d;border:0;max-width:360px;text-align:center}}.pending{{border-color:#ffd17b55}}.request{{border-color:#76dda144}}.footer{{border-top:1px solid var(--line);margin-top:35px;padding-top:25px;color:var(--muted);font-size:.82rem}}@media(max-width:1120px){{.tools{{grid-template-columns:repeat(3,1fr)}}}}@media(max-width:760px){{.tools{{grid-template-columns:1fr 1fr}}.esp{{align-items:flex-start;flex-direction:column}}}}@media(max-width:500px){{.tools{{grid-template-columns:1fr}}}}
</style></head><body><main class='wrap'><header class='top'><a class='brand' href='/'>{escape(PRODUCT_FULL_NAME)}<small>{escape(ENDORSEMENT)}</small></a><div><a class='btn' href='/pricing'>Plans</a> <form method='post' action='/signout' style='display:inline'><button type='submit'>Sign out</button></form></div></header><section class='hero'><div class='eyebrow'>Member Creative Studio</div><h1>Welcome, <span>{escape(user['display_name'])}.</span></h1><p>The public member side is deliberately focused on creation and Aura. ESP agency management, social-management systems and creator/agent training live only inside the separately approved ESP Hub.</p><div class='account'><span class='pill'>{escape(active_plan.upper())}</span>{account_state}</div></section><section><div class='eyebrow'>Your five creative areas</div><h2>Build, edit and direct with Aura.</h2><div class='tools'>{cards}</div></section>{esp_panel}<footer class='footer'>{escape(TAGLINE)} · {escape(ENDORSEMENT)}</footer></main></body></html>"""
    return HTMLResponse(html)
