from __future__ import annotations

from html import escape

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .accounts import AccountStore
from .branding import ENDORSEMENT, PRODUCT_FULL_NAME, PRODUCT_NAME, TAGLINE
from .esp_command_center import EspStore
from .native_access import EffectiveNativeAccess, NativeAccessResolver
from .native_products import AURA_SEC_ENTITLEMENT
from .plans import PLANS

router = APIRouter()
accounts = AccountStore()
esp = EspStore(accounts)
native_access = NativeAccessResolver(accounts=accounts)
MEMBER_COOKIE = "lss_session"


@router.get("/video-studio", include_in_schema=False)
def video_studio_entry():
    return RedirectResponse("/creative-house?kind=video", status_code=303)


@router.get("/image-designer", include_in_schema=False)
def image_designer_entry():
    return RedirectResponse("/creative-house?kind=image", status_code=303)


def _esp_quick_actions(status: str, role: str) -> list[tuple[str, str, str]]:
    """Return only navigation the authenticated ESP role is allowed to discover.

    These links are a usability layer, not an authorization boundary. Every destination keeps
    its own server-side ESP/owner checks. Ordinary members and pending requests receive no
    private shortcuts here.
    """
    if status not in {"active", "owner"}:
        return []

    normalized = (role or "").strip().lower()
    is_owner = status == "owner"
    is_creator = is_owner or normalized in {"creator", "both"}
    is_agent = is_owner or normalized in {"agent", "both"}

    actions: list[tuple[str, str, str]] = [
        ("Enter ESP Hub", "/command-center", "Private Creator & Agent home"),
        ("Social Media Centre", "/command-center/social", "ESP social planning and publishing"),
        ("LIVE Creator Studio", "/live-overlay-studio", "Overlays, show control and post-show tools"),
    ]
    if is_creator:
        actions.extend(
            [
                ("Creator Growth Plan", "/command-center/level-up", "Niche-aware plan, reviews and level-up path"),
                ("Creator Progress", "/command-center/progress", "Private progress and mentoring view"),
            ]
        )
    if is_agent:
        actions.extend(
            [
                ("Agent Operations", "/command-center/agent/operations", "Recruitment and assigned-creator operations"),
                ("Agent Health", "/command-center/agent/health", "Agent activity and operational health"),
            ]
        )
    if is_owner:
        actions.append(("Owner Command Center", "/owner/dashboard", "Mary & Kev protected owner operations"))
    return actions


def _esp_action_html(status: str, role: str) -> str:
    return "".join(
        f"<a class='espquick' href='{escape(url, quote=True)}'><b>{escape(label)}</b><small>{escape(copy)}</small></a>"
        for label, url, copy in _esp_quick_actions(status, role)
    )


def _aura_sec_security_panel(access) -> str:
    """Render commercial Aura Sec access truth without widening native-device authority."""

    has_access = bool(access.has(AURA_SEC_ENTITLEMENT))
    sources = tuple(access.sources_for(AURA_SEC_ENTITLEMENT)) if has_access else ()

    source_labels: list[str] = []
    if any(source.startswith("membership:") for source in sources):
        source_labels.append("Unlimited Pro membership")
    if "native_purchase" in sources:
        source_labels.append("verified native purchase")

    if has_access:
        source_label = " and ".join(source_labels) or "verified commercial entitlement"
        eyebrow = "Aura Sec commercial access active · same account"
        commercial_copy = (
            f"Aura Sec commercial access is active via {source_label}. Open the Security Center "
            "to review the member-safe security control plane, enrolled devices, incidents and bounded action status."
        )
    else:
        eyebrow = "Aura Sec available · same account"
        commercial_copy = (
            "Aura Sec is included with Unlimited Pro and can also be purchased separately where offered. "
            "Open Native Products to review the account's current Aura OS and Aura Sec commercial access."
        )

    return (
        "<section class='security'><div>"
        f"<div class='eyebrow'>{escape(eyebrow)}</div>"
        "<h2>🛡️ Aura Sec Security Center</h2>"
        f"<p>{escape(commercial_copy)}</p>"
        "<p class='securitytruth'>Commercial access never grants native device trust by itself. "
        "The browser is a member-safe control plane only: it cannot execute endpoint commands, poll native command "
        "channels, submit native heartbeat proof, access command-signing keys or bypass strong re-authentication.</p>"
        "</div><div class='securityactions'>"
        "<a class='btn securitybtn' href='/aura-sec'>Open Aura Sec</a>"
        "<a class='btn secondary' href='/account/native-products'>Manage Aura OS &amp; Aura Sec</a>"
        "</div></section>"
    )


def _dashboard_native_access(user: dict) -> EffectiveNativeAccess:
    """Resolve commercial native access from authoritative storage or fail closed.

    A valid production session normally resolves to a persisted account. Tests and defensive
    callers can still present an inconsistent session snapshot; that must never allow a plan
    value carried by the session itself to manufacture Aura OS/Aura Sec entitlement.
    """

    user_id = str(user.get("id") or "").strip()
    try:
        return native_access.resolve(user_id)
    except LookupError:
        return EffectiveNativeAccess(
            user_id=user_id,
            membership_plan_id="free",
            membership_entitlements=frozenset(),
            purchased_entitlements=frozenset(),
        )


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
            "Generate release-grade songs, record performances, build around uploads, edit stems, mix/master and keep the finished song editable through Song DNA and project revisions.",
            "/studio",
            "Open Music Studio",
            "/song-editor",
            "Editable Song DNA",
        ),
        (
            "🎬",
            "Video Studio",
            "Build video and music-video projects with scene-level Creative DNA, references, Aura direction and the connected renderer bridge. External generation backends report their real deployment status.",
            "/video-studio",
            "Open Video Studio",
            None,
            None,
        ),
        (
            "🎨",
            "Image Designer",
            "Create covers, posters, campaign artwork and image projects with editable Creative DNA elements, references, lineage and Aura-guided revisions.",
            "/image-designer",
            "Open Image Designer",
            None,
            None,
        ),
        (
            "🎮",
            "Game Forge",
            "Create editable Aura Game DNA for 2D or 3D games, import verified Music, Video and Image House assets, bind them into World DNA and keep runtime/export targets explicit.",
            "/game-creation",
            "Open Game Forge",
            None,
            None,
        ),
        (
            "▣",
            "Creative Library",
            "Browse created music, audio, videos and images across your private projects. Launch songs and videos into the persistent Pulsar Player, revisit historical versions and download where your membership allows.",
            "/creative/library",
            "Open Creative Library",
            None,
            None,
        ),
        (
            "✨",
            "Aura Creative Director",
            "Talk or type to Aura for creative project guidance, music-production decisions and natural-language direction across your studio work.",
            "/aura",
            "Talk to Aura",
            None,
            None,
        ),
        (
            "🧠",
            "Aura Intelligence",
            "Your private realtime AI workspace: Voice Conversation, Aura Today, Artifacts, Tasks, Notifications, research, project-aware workflows and read-only Google tools when you explicitly connect them.",
            "/aura-intelligence",
            "Open Aura Intelligence",
            None,
            None,
        ),
        (
            "🛍️",
            "Marketplace Account",
            "View your account-scoped purchase history plus verified creator marketplace earnings, refund reversals and net proceeds. This surface is read-only and marketplace participation remains opt-in.",
            "/marketplace/account",
            "Open Marketplace Account",
            None,
            None,
        ),
    ]
    cards = "".join(
        f"""<article class='tool'><div class='icon'>{icon}</div><h3>{escape(title)}</h3><p>{escape(copy)}</p><div class='toolactions'><a class='btn primary' href='{escape(url, quote=True)}'>{escape(cta)}</a>{f"<a class='btn secondary' href='{escape(secondary_url, quote=True)}'>{escape(secondary_cta)}</a>" if secondary_url and secondary_cta else ''}</div></article>"""
        for icon, title, copy, url, cta, secondary_url, secondary_cta in regular_cards
    )

    aura_features = (
        ("☀", "Aura Today", "Calendar/Gmail metadata, active Tasks, notifications and pinned-project context in one private overview."),
        ("🎙", "Voice Conversation", "Optional hands-free listen → think/tools → speak loop using the same auditable conversation history."),
        ("▤", "Artifacts", "Versioned documents, lyrics, prompts, data and code with history and restore; code never executes on the web host."),
        ("⏰", "Tasks & Briefings", "Durable reminders, read-only research and scheduled workspace briefings that survive browser sessions when the worker is online."),
        ("🔗", "Connected Workspace", "Encrypted per-member Google Drive, Calendar and Gmail read-only access only after the member explicitly authorizes it."),
        ("🧩", "Verified Workflows", "Multi-step Aura tools pass actual verified results between steps instead of inventing project, file, email or event identifiers."),
    )
    aura_core = "".join(
        f"<div class='coreitem'><span>{icon}</span><div><b>{escape(title)}</b><small>{escape(copy)}</small></div></div>"
        for icon, title, copy in aura_features
    )

    security_panel = _aura_sec_security_panel(_dashboard_native_access(user))

    if esp_status in {"active", "owner"}:
        label = "Owner" if esp_status == "owner" else (esp_role.replace("both", "Creator + Agent").title() or "ESP Member")
        quick_actions = _esp_action_html(esp_status, esp_role)
        esp_panel = f"""<section class='esp'><div class='espintro'><div class='eyebrow'>Private Elevate Souls Productions Area</div><h2>ESP {escape(label)}</h2><p>Your approved ESP Creator/Agent permissions unlock additional areas inside this same Pulsar-Frequency House account: niche training, progress, Social Management, commercial growth, LIVE operations and agency tools. These areas remain hidden from ordinary public members.</p><div class='espactions'>{quick_actions}</div></div></section>"""
    elif esp_status == "pending":
        esp_panel = """<section class='esp pending'><div><div class='eyebrow'>ESP access request</div><h2>Waiting for Mary / Kev approval</h2><p>Your request has not activated ESP-only areas yet. You can continue using your normal Pulsar creative tools while the additional ESP role is reviewed.</p></div><a class='btn' href='/command-center'>View request status</a></section>"""
    else:
        esp_panel = """<section class='esp request'><div><div class='eyebrow'>Already an ESP Creator or Agent?</div><h2>Request verification</h2><p>If you are genuinely part of Elevate Souls Productions, request verification here. ESP areas use the same account and site, but access is owner-approved and cannot be obtained merely by purchasing a creative subscription.</p></div><a class='btn espbtn' href='/command-center'>I am an Elevate Souls Productions Creator or Agent</a></section>"""

    html = f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1,viewport-fit=cover'><meta name='robots' content='noindex,nofollow'><title>Dashboard — {escape(PRODUCT_NAME)}</title><style>
:root{{--bg:#04050b;--panel:#101320;--line:#ffffff1d;--gold:#f4c873;--violet:#a66bff;--cyan:#5de7ff;--pink:#ff74d0;--good:#76dda1;--warn:#ffd17b;--bad:#ff8fa3;--text:#fff;--muted:#bcc0d2}}*{{box-sizing:border-box}}body{{margin:0;color:var(--text);font-family:Inter,ui-sans-serif,system-ui,sans-serif;background:radial-gradient(circle at 10% 0,#32134c,transparent 30%),radial-gradient(circle at 90% 0,#10374f,transparent 28%),#04050b;min-height:100vh}}a{{color:inherit;text-decoration:none}}.wrap{{width:min(1320px,calc(100% - 28px));margin:auto;padding:24px 0 55px}}.top{{display:flex;justify-content:space-between;align-items:center;gap:14px;flex-wrap:wrap}}.brand{{font-weight:950}}.brand small{{display:block;color:var(--gold);font-size:.67rem;letter-spacing:.08em;text-transform:uppercase;margin-top:3px}}.btn,button{{display:inline-block;border:1px solid var(--line);border-radius:12px;padding:10px 14px;background:#ffffff08;color:#fff;font-weight:850;cursor:pointer}}.primary{{border:0;background:linear-gradient(110deg,#fff0b0,var(--gold),#c8a0ff);color:#160c1d}}.secondary{{background:#ffffff06}}.hero{{padding:55px 0 30px}}.eyebrow{{color:var(--gold);font-size:.72rem;letter-spacing:.16em;text-transform:uppercase;font-weight:900}}h1{{font-size:clamp(2.6rem,7vw,5.8rem);line-height:.92;letter-spacing:-.06em;margin:.14em 0 .18em}}h1 span{{background:linear-gradient(100deg,#fff,var(--gold),var(--pink),var(--cyan));background-clip:text;color:transparent}}.hero p,.muted{{color:var(--muted);line-height:1.6}}.account{{display:flex;gap:9px;align-items:center;flex-wrap:wrap;margin-top:18px}}.pill{{border:1px solid var(--line);border-radius:999px;padding:6px 9px;font-size:.78rem}}.good{{color:var(--good)}}.warn{{color:var(--warn)}}.bad{{color:var(--bad)}}.core{{margin:4px 0 28px;border:1px solid #a66bff52;border-radius:24px;padding:22px;background:radial-gradient(circle at 10% 0,#a66bff1f,transparent 34%),radial-gradient(circle at 95% 10%,#5de7ff13,transparent 30%),#0b0e1ae8}}.corehead{{display:flex;justify-content:space-between;align-items:flex-start;gap:18px;flex-wrap:wrap}}.corehead h2{{margin:5px 0 7px}}.corehead p{{margin:0;color:var(--muted);max-width:830px;line-height:1.55}}.coregrid{{display:grid;grid-template-columns:repeat(3,1fr);gap:9px;margin-top:16px}}.coreitem{{display:flex;gap:9px;align-items:flex-start;border:1px solid var(--line);border-radius:14px;padding:11px;background:#ffffff05}}.coreitem>span{{font-size:1.25rem}}.coreitem b{{display:block;font-size:.82rem}}.coreitem small{{display:block;color:var(--muted);font-size:.7rem;line-height:1.42;margin-top:3px}}.tools{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}}.tool{{border:1px solid var(--line);border-radius:21px;padding:19px;background:linear-gradient(145deg,#121528e8,#080a13e8);display:flex;flex-direction:column;min-height:315px}}.tool .icon{{font-size:1.8rem}}.tool h3{{font-size:1.15rem;margin:13px 0 7px}}.tool p{{color:var(--muted);line-height:1.55;flex:1}}.toolactions{{display:grid;gap:7px}}.security,.esp{{display:flex;justify-content:space-between;gap:24px;align-items:center;margin-top:22px;padding:22px;border-radius:22px}}.security{{border:1px solid #55e7ff52;background:radial-gradient(circle at 8% 0,#55e7ff12,transparent 34%),linear-gradient(120deg,#071924,#101320)}}.security p,.esp p{{color:var(--muted);max-width:900px;line-height:1.55}}.securitytruth{{font-size:.84rem}}.securityactions{{display:grid;gap:8px;min-width:205px}}.securityactions .btn{{text-align:center}}.securitybtn{{background:linear-gradient(110deg,#8df3ff,#55e7ff,#a9bfff);color:#06121a;border:0;min-width:165px;text-align:center}}.esp{{border:1px solid #f4c87345;background:linear-gradient(120deg,#f4c8730c,#a66bff12,#080a13)}}.espintro{{width:100%}}.espactions{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:9px;margin-top:16px}}.espquick{{border:1px solid var(--line);border-radius:14px;padding:12px;background:#ffffff07;min-height:84px}}.espquick:hover{{border-color:#f4c87370;background:#f4c8730c}}.espquick b{{display:block;font-size:.83rem}}.espquick small{{display:block;color:var(--muted);font-size:.7rem;line-height:1.4;margin-top:5px}}.espbtn{{background:linear-gradient(110deg,var(--gold),#bf94ff);color:#140b1d;border:0;max-width:360px;text-align:center}}.pending{{border-color:#ffd17b55}}.request{{border-color:#76dda144}}.footer{{border-top:1px solid var(--line);margin-top:35px;padding-top:25px;color:var(--muted);font-size:.82rem}}@media(max-width:1120px){{.tools{{grid-template-columns:repeat(3,1fr)}}.coregrid{{grid-template-columns:repeat(2,1fr)}}.espactions{{grid-template-columns:repeat(2,minmax(0,1fr))}}}}@media(max-width:760px){{.tools{{grid-template-columns:1fr 1fr}}.coregrid{{grid-template-columns:1fr}}.security,.esp{{align-items:flex-start;flex-direction:column}}.securityactions{{width:100%}}}}@media(max-width:500px){{.tools,.espactions{{grid-template-columns:1fr}}}}
</style></head><body><main class='wrap'><header class='top'><a class='brand' href='/'>{escape(PRODUCT_FULL_NAME)}<small>{escape(ENDORSEMENT)}</small></a><div><a class='btn' href='/pricing'>Plans</a> <form method='post' action='/signout' style='display:inline'><button type='submit'>Sign out</button></form></div></header><section class='hero'><div class='eyebrow'>Member Creative Studio</div><h1>Welcome, <span>{escape(user['display_name'])}.</span></h1><p>Pulsar-Frequency House is one integrated creation platform. Music, video, image, games, Aura and your Creative Library are available from this account; Aura Sec uses the same signed-in account and its commercial access can come from Unlimited Pro or a verified native purchase, while native-device trust remains a separate security boundary. Approved ESP members additionally unlock private Creator, Agent or Owner areas within the same site.</p><div class='account'><span class='pill'>{escape(active_plan.upper())}</span>{account_state}</div></section><section class='core'><div class='corehead'><div><div class='eyebrow'>Aura Core 0.20</div><h2>Your intelligent creative command layer.</h2><p>Aura carries private conversation and project context across research, files and creative workflows. External models/services still report their real configured state rather than being presented as active when they are not.</p></div><a class='btn primary' href='/aura-intelligence'>Open Aura Intelligence</a></div><div class='coregrid'>{aura_core}</div></section><section><div class='eyebrow'>Your creative studios</div><h2>Build, edit, play and direct with Aura.</h2><div class='tools'>{cards}</div></section>{security_panel}{esp_panel}<footer class='footer'>{escape(TAGLINE)} · {escape(ENDORSEMENT)}</footer></main></body></html>"""
    response = HTMLResponse(html)
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response