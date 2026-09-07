from __future__ import annotations

from html import escape
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from .esp_niche import EspNicheStore, require_esp_hub_member

router = APIRouter(tags=["ESP Member Hub"])
niches = EspNicheStore()


MODULES: tuple[dict[str, Any], ...] = (
    {"id":"home","title":"ESP Home & Next Actions","audience":{"creator","agent","owner"},"status":"built","path":"/command-center/level-up","summary":"Role-aware Level Up Hub, niche context, alerts and current ESP navigation.","group":"Shared ESP"},
    {"id":"academy","title":"Academy & Resource Library","audience":{"creator","agent","owner"},"status":"built","path":"/command-center/library","summary":"Role-restricted Creator, Agent and Owner learning tracks with action evidence and completion progress.","group":"Shared ESP"},
    {"id":"support","title":"Support, Safety & Evidence","audience":{"creator","agent","owner"},"status":"built","path":"/command-center/support","summary":"Private support cases, evidence organisation, technical/policy/traffic/safety/IP/commerce categories and owner resolution workflow.","group":"Shared ESP"},
    {"id":"broadcast","title":"Broadcast & Tech Desk","audience":{"creator","agent","owner"},"status":"partial","path":"/command-center/broadcast-tech","summary":"LIVE Studio/broadcast configuration, technical readiness and escalation workflows; provider/platform capabilities remain truthfully gated.","group":"Shared ESP"},
    {"id":"social","title":"ESP Social Management","audience":{"creator","agent","owner"},"status":"partial","path":"/command-center/social","summary":"Private Social Houses, campaigns, media, calendar, approvals, publishing queue, secure connections and provider analytics.","group":"Shared ESP"},
    {"id":"collaborate","title":"Collaborate, Battles & Events","audience":{"creator","agent","owner"},"status":"built","path":"/command-center/collaborations","summary":"Opt-in battle scheduling, co-host/collaboration matching, reliability, event operations and pre/post content plans.","group":"Shared ESP"},
    {"id":"commerce","title":"Commerce / Shop Creator Centre","audience":{"creator","agent","owner"},"status":"built","path":"/command-center/commerce","summary":"Shop readiness, creator opt-in, opportunities, samples, deadlines, disclosure checks and fulfilment tracking where regional programmes permit.","group":"Shared ESP"},
    {"id":"brands","title":"Brands & Commercial Opportunities","audience":{"creator","agent","owner"},"status":"built","path":"/command-center/brands","summary":"Opt-in commercial profiles, media kits, Agent-owned brand leads, creator opportunities, applications, deliverables, reporting and renewals.","group":"Shared ESP"},
    {"id":"my-plan","title":"My Plan","audience":{"creator","owner"},"status":"built","path":"/command-center/my-plan","summary":"Creator monthly objective, actions, activation/build/optimise pathway and review history.","group":"Creator"},
    {"id":"creator-progress","title":"LIVE / Video Progress & Creator Health","audience":{"creator","owner"},"status":"built","path":"/command-center/progress","summary":"Creator progress evidence, trend signals, training context and explainable Aura support prompts.","group":"Creator"},
    {"id":"incentives","title":"Incentives, Rewards & Eligibility","audience":{"creator","owner"},"status":"built","path":"/command-center/incentives","summary":"Current incentive programmes, recognition, maintenance/rank/referral pathways and transparent human-verified eligibility checks.","group":"Creator"},
    {"id":"agent-academy","title":"Agent Academy","audience":{"agent","owner"},"status":"built","path":"/command-center/library","summary":"Recruitment, ACU/engagement, LIVE roadmap, KPI, video strategy, CapCut, business and advanced agent training.","group":"Agent"},
    {"id":"assigned-roster","title":"Assigned Creator Roster","audience":{"agent","owner"},"status":"built","path":"/command-center/agent/roster","summary":"Owner-assigned ESP creators only, with training, plan, progress and follow-up context.","group":"Agent"},
    {"id":"health-queue","title":"Creator Health Queue","audience":{"agent","owner"},"status":"built","path":"/command-center/agent/health","summary":"Explainable support-priority signals for assigned creators; no automated disciplinary decisions.","group":"Agent"},
    {"id":"success-ops","title":"Creator Success Operations","audience":{"agent","owner"},"status":"built","path":"/command-center/agent/operations","summary":"Check-ins, activation/build/optimise/reactivate/technical pathways and outcomes for assigned creators.","group":"Agent"},
    {"id":"creator-discovery","title":"Creator Discovery & Recruitment CRM","audience":{"agent","owner"},"status":"built","path":"/command-center/agent/discovery","summary":"Global handle dedupe, source provenance, age/network validation, assigned-agent ownership, outreach/follow-up/application/join tracking and opt-out protection.","group":"Agent"},
    {"id":"recruitment-funnel","title":"Recruitment Funnel","audience":{"agent","owner"},"status":"built","path":"/command-center/agent/discovery","summary":"Validated → ready → contacted → follow-up → replied → applied → joined conversion metrics per agent.","group":"Agent"},
    {"id":"owner-focus","title":"Owner Focus, Momentum & System Health","audience":{"owner"},"status":"built","path":"/owner/focus","summary":"Network snapshot, rising/dropping/plateau signals, agent funnel, data coverage and explainable system-health priorities.","group":"Owner"},
    {"id":"owner-access","title":"ESP Access, Assignment & Social Control","audience":{"owner"},"status":"built","path":"/owner/esp-access","summary":"Mary/Kev role approval, immediate revocation, Agent→Creator assignment and Social Centre suspension controls.","group":"Owner"},
)


def _role(membership: dict) -> str:
    if membership.get("status") == "owner":
        return "owner"
    value = str(membership.get("roles") or "").lower()
    if value == "both":
        return "both"
    return value if value in {"creator", "agent"} else "creator"


def _visible(module: dict, role: str) -> bool:
    audience = set(module.get("audience") or set())
    if role == "owner":
        return True
    if role == "both":
        return bool(audience & {"creator", "agent"})
    return role in audience


def catalog_for(membership: dict, profile: dict | None = None) -> dict:
    role = _role(membership)
    modules = []
    for module in MODULES:
        if not _visible(module, role):
            continue
        modules.append({key: (sorted(value) if isinstance(value, set) else value) for key, value in module.items()})
    groups: dict[str, list[dict]] = {}
    for row in modules:
        groups.setdefault(str(row["group"]), []).append(row)
    return {
        "role": role,
        "niche": (profile or {}).get("niche"),
        "sub_niche": (profile or {}).get("sub_niche"),
        "modules": modules,
        "groups": groups,
        "private_esp_only": True,
        "creative_subscription_grants_esp": False,
    }


@router.get("/command-center/api/member-hub/catalog")
def member_hub_catalog(request: Request):
    member, membership = require_esp_hub_member(request)
    return catalog_for(membership, niches.get(member.user_id))


@router.get("/command-center/member-hub", response_class=HTMLResponse, include_in_schema=False)
def member_hub_page(request: Request):
    member, membership = require_esp_hub_member(request)
    data = catalog_for(membership, niches.get(member.user_id))
    cards: list[str] = []
    for group, modules in data["groups"].items():
        body: list[str] = []
        for module in modules:
            action = (
                f"<a class='btn' href='{escape(str(module['path']))}'>Open</a>"
                if module.get("path")
                else "<span class='soon'>Next build stage</span>"
            )
            body.append(
                "<article class='card'>"
                f"<div class='top'><span class='status {escape(str(module['status']))}'>{escape(str(module['status']).upper())}</span>{action}</div>"
                f"<h3>{escape(str(module['title']))}</h3><p>{escape(str(module['summary']))}</p></article>"
            )
        cards.append(f"<section><div class='eyebrow'>{escape(group)}</div><div class='grid'>{''.join(body)}</div></section>")
    role = str(data["role"]).replace("both", "Creator + Agent").title()
    niche = str(data.get("niche") or "not selected").replace("_", " ").title()
    html = f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='noindex,nofollow'><title>ESP Member Hub</title><style>
    :root{{--line:#ffffff1d;--gold:#f2c86f;--violet:#9f70ff;--muted:#c1bfc8;--good:#75dda6;--warn:#ffca7d}}*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 8% 0,#43175e,transparent 31%),radial-gradient(circle at 92% 3%,#123d5a,transparent 28%),#05050b;color:#fff;font-family:Inter,system-ui,sans-serif}}a{{color:inherit;text-decoration:none}}.wrap{{width:min(1320px,calc(100% - 28px));margin:auto;padding:38px 0 70px}}.top{{display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap}}.eyebrow{{color:var(--gold);font-size:.7rem;letter-spacing:.15em;text-transform:uppercase;font-weight:950;margin-top:22px}}h1{{font-size:clamp(2.8rem,7vw,5.8rem);letter-spacing:-.06em;line-height:.92;margin:.12em 0 .2em}}p{{color:var(--muted);line-height:1.55}}.grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-top:9px}}.card{{border:1px solid var(--line);border-radius:17px;padding:14px;background:linear-gradient(145deg,#15101feb,#090912ef)}}.card h3{{margin:10px 0 5px}}.btn,.soon{{border:1px solid var(--line);border-radius:9px;padding:7px 9px;background:#ffffff08;font-size:.75rem;font-weight:850}}.btn:hover{{border-color:var(--gold)}}.soon{{color:var(--muted)}}.status{{font-size:.64rem;border:1px solid var(--line);border-radius:999px;padding:4px 7px;font-weight:900}}.built{{color:var(--good)}}.partial,.designed{{color:var(--warn)}}.guard{{border-left:4px solid var(--violet)}}@media(max-width:900px){{.grid{{grid-template-columns:1fr 1fr}}}}@media(max-width:600px){{.grid{{grid-template-columns:1fr}}}}
    </style></head><body><main class='wrap'><div class='top'><div><div class='eyebrow'>Elevate Souls Productions · Private Member Operating System</div><h1>ESP Member Hub</h1><p>Role: <b>{escape(role)}</b> · Niche: <b>{escape(niche)}</b>. This dashboard shows only modules permitted for the current ESP role.</p></div><a class='btn' href='/command-center/level-up'>Level Up Hub</a></div><article class='card guard'><b>Private ESP boundary</b><p>ESP Creator/Agent access is independent from Free, Basic and Pro public creative subscriptions. Recruitment tools are Agent/Owner only and block outreach when validation shows another Creator Network, underage status or an opt-out.</p></article>{''.join(cards)}</main></body></html>"""
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


__all__ = ["router", "catalog_for", "MODULES"]
