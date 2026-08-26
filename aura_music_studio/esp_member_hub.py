from __future__ import annotations

from html import escape
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from .esp_niche import EspNicheStore, require_esp_hub_member

router = APIRouter(tags=["ESP Member Hub"])
niches = EspNicheStore()


MODULES: tuple[dict[str, Any], ...] = (
    {
        "id": "home",
        "title": "ESP Home & Next Actions",
        "audience": {"creator", "agent", "owner"},
        "status": "built",
        "path": "/command-center/level-up",
        "summary": "Role-aware Level Up Hub, niche context, alerts and current ESP navigation.",
        "group": "Shared ESP",
    },
    {
        "id": "my-plan",
        "title": "My Plan",
        "audience": {"creator", "owner"},
        "status": "built",
        "path": "/command-center/my-plan",
        "summary": "Creator monthly objective, actions and progress pathway.",
        "group": "Creator",
    },
    {
        "id": "academy",
        "title": "Creator Academy & Niche Training",
        "audience": {"creator", "owner"},
        "status": "partial",
        "path": "/command-center/level-up",
        "summary": "Welcome, LIVE growth, creator academy, niche/show ideas, video strategy and approved learning tracks.",
        "group": "Creator",
    },
    {
        "id": "creator-progress",
        "title": "LIVE / Video Progress & Creator Health",
        "audience": {"creator", "owner"},
        "status": "built",
        "path": "/command-center/progress",
        "summary": "Creator progress submissions, consistency signals and explainable support prompts.",
        "group": "Creator",
    },
    {
        "id": "incentives",
        "title": "Incentives, Rewards & Eligibility",
        "audience": {"creator", "owner"},
        "status": "designed",
        "path": None,
        "summary": "Creator incentives, maintenance/rank pathways, recognition, ambassador rewards, equipment and battle incentives.",
        "group": "Creator",
    },
    {
        "id": "collaborate",
        "title": "Collaborate, Battles & Events",
        "audience": {"creator", "agent", "owner"},
        "status": "designed",
        "path": None,
        "summary": "Battle booking, co-host/collaboration opportunities, creator exchange and event workflows.",
        "group": "Shared ESP",
    },
    {
        "id": "support",
        "title": "ESP Support Desk",
        "audience": {"creator", "agent", "owner"},
        "status": "designed",
        "path": None,
        "summary": "Mentoring, technical/LIVE Studio, traffic-health, compliance, safety, Shop and escalation queues with ownership and SLAs.",
        "group": "Shared ESP",
    },
    {
        "id": "commerce",
        "title": "Commerce / Shop Creator Centre",
        "audience": {"creator", "agent", "owner"},
        "status": "designed",
        "path": None,
        "summary": "Shop readiness, approved training, opportunities, samples, campaign deadlines and seller/creator matching.",
        "group": "Shared ESP",
    },
    {
        "id": "brands",
        "title": "Brands & Commercial Opportunities",
        "audience": {"creator", "agent", "owner"},
        "status": "designed",
        "path": None,
        "summary": "Opt-in creator commercial profile, media-kit workflow, brand opportunities, applications, fulfilment and reporting.",
        "group": "Shared ESP",
    },
    {
        "id": "social",
        "title": "ESP Social Management",
        "audience": {"creator", "agent", "owner"},
        "status": "built",
        "path": "/command-center/social",
        "summary": "Private Social Houses, campaigns, calendar, approvals, publishing queue, connections and provider analytics.",
        "group": "Shared ESP",
    },
    {
        "id": "resources",
        "title": "ESP Resource Library",
        "audience": {"creator", "agent", "owner"},
        "status": "designed",
        "path": None,
        "summary": "Approved policies, guides, training videos, templates and announcements sourced from the ESP knowledge system.",
        "group": "Shared ESP",
    },
    {
        "id": "agent-academy",
        "title": "Agent Academy",
        "audience": {"agent", "owner"},
        "status": "partial",
        "path": "/command-center/level-up",
        "summary": "Recruitment foundations, gifting/ACU, LIVE roadmap, KPIs, video strategy, CapCut, business and advanced agent training.",
        "group": "Agent",
    },
    {
        "id": "assigned-roster",
        "title": "Assigned Creator Roster",
        "audience": {"agent", "owner"},
        "status": "built",
        "path": "/command-center/agent/roster",
        "summary": "Owner-assigned ESP creators only, with training, plan, progress and follow-up context.",
        "group": "Agent",
    },
    {
        "id": "health-queue",
        "title": "Creator Health Queue",
        "audience": {"agent", "owner"},
        "status": "built",
        "path": "/command-center/agent/health",
        "summary": "Explainable creator-support priority signals for assigned creators.",
        "group": "Agent",
    },
    {
        "id": "success-ops",
        "title": "Creator Success Operations",
        "audience": {"agent", "owner"},
        "status": "built",
        "path": "/command-center/agent/operations",
        "summary": "Check-ins, activation/build/optimise/reactivate/technical pathways and outcomes for assigned creators.",
        "group": "Agent",
    },
    {
        "id": "creator-discovery",
        "title": "Creator Discovery & Recruitment CRM",
        "audience": {"agent", "owner"},
        "status": "building",
        "path": "/command-center/agent/discovery",
        "summary": "Validate public prospects, prevent duplicate contact, track apply messages, follow-ups, applications and joins per agent.",
        "group": "Agent",
    },
    {
        "id": "recruitment-funnel",
        "title": "Recruitment Funnel",
        "audience": {"agent", "owner"},
        "status": "building",
        "path": "/command-center/agent/discovery",
        "summary": "Validated leads → contacted → follow-up → applied → joined → activated, with per-agent conversion metrics.",
        "group": "Agent",
    },
    {
        "id": "owner-focus",
        "title": "Owner Focus, Momentum & System Health",
        "audience": {"owner"},
        "status": "designed",
        "path": None,
        "summary": "Network snapshot, priority panel, rising/dropping/plateau creators, agent performance, compliance and system-health score.",
        "group": "Owner",
    },
)


def _role(membership: dict) -> str:
    if membership.get("status") == "owner":
        return "owner"
    value = str(membership.get("roles") or "").lower()
    if value == "both":
        return "both"
    return value if value in {"creator", "agent"} else "creator"


def _visible(module: dict, role: str) -> bool:
    audiences = set(module.get("audience") or set())
    if role == "owner":
        return "owner" in audiences or bool(audiences & {"creator", "agent"})
    if role == "both":
        return bool(audiences & {"creator", "agent"})
    return role in audiences


def catalog_for(membership: dict, profile: dict | None = None) -> dict:
    role = _role(membership)
    rows = []
    for module in MODULES:
        if not _visible(module, role):
            continue
        item = {key: (sorted(value) if isinstance(value, set) else value) for key, value in module.items()}
        rows.append(item)
    groups: dict[str, list[dict]] = {}
    for row in rows:
        groups.setdefault(str(row["group"]), []).append(row)
    return {
        "role": role,
        "niche": (profile or {}).get("niche"),
        "sub_niche": (profile or {}).get("sub_niche"),
        "modules": rows,
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
    role = data["role"]
    cards: list[str] = []
    for group, modules in data["groups"].items():
        body = []
        for module in modules:
            path = module.get("path")
            action = (
                f"<a class='btn' href='{escape(str(path))}'>Open</a>"
                if path
                else "<span class='soon'>Native module queued in this build</span>"
            )
            body.append(
                "<article class='card'>"
                f"<div class='top'><span class='status {escape(str(module['status']))}'>{escape(str(module['status']).upper())}</span>{action}</div>"
                f"<h3>{escape(str(module['title']))}</h3><p>{escape(str(module['summary']))}</p>"
                "</article>"
            )
        cards.append(f"<section><div class='eyebrow'>{escape(group)}</div><div class='grid'>{''.join(body)}</div></section>")
    niche = escape(str(data.get("niche") or "not selected").replace("_", " ").title())
    html = f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='noindex,nofollow'><title>ESP Member Hub</title><style>
    :root{{--line:#ffffff1d;--gold:#f2c86f;--violet:#9f70ff;--muted:#c1bfd0;--good:#75dda6;--warn:#ffca7d}}*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 8% 0,#43175e,transparent 31%),radial-gradient(circle at 92% 0,#123d5a,transparent 28%),#05050b;color:#fff;font-family:Inter,system-ui,sans-serif}}a{{color:inherit;text-decoration:none}}.wrap{{width:min(1320px,calc(100% - 28px));margin:auto;padding:38px 0 70px}}.top{{display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap}}.eyebrow{{color:var(--gold);font-size:.7rem;letter-spacing:.15em;text-transform:uppercase;font-weight:950;margin-top:22px}}h1{{font-size:clamp(2.8rem,7vw,5.8rem);letter-spacing:-.06em;line-height:.92;margin:.12em 0 .2em}}p{{color:var(--muted);line-height:1.55}}.grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-top:9px}}.card{{border:1px solid var(--line);border-radius:17px;padding:14px;background:linear-gradient(145deg,#15101feb,#090912ef)}}.card h3{{margin:10px 0 5px}}.btn,.soon{{border:1px solid var(--line);border-radius:9px;padding:7px 9px;background:#ffffff08;font-size:.75rem;font-weight:850}}.btn:hover{{border-color:var(--gold)}}.soon{{color:var(--muted)}}.status{{font-size:.64rem;border:1px solid var(--line);border-radius:999px;padding:4px 7px;font-weight:900}}.built{{color:var(--good)}}.building,.partial{{color:var(--warn)}}.guard{{border-left:4px solid var(--violet)}}@media(max-width:900px){{.grid{{grid-template-columns:1fr 1fr}}}}@media(max-width:600px){{.grid{{grid-template-columns:1fr}}}}
    </style></head><body><main class='wrap'><div class='top'><div><div class='eyebrow'>Elevate Souls Productions · Private Member Operating System</div><h1>ESP Member Hub</h1><p>Role: <b>{escape(role.replace('both','Creator + Agent').title())}</b> · Niche: <b>{niche}</b>. You see only the modules permitted for this ESP role.</p></div><a class='btn' href='/command-center/level-up'>Level Up Hub</a></div><article class='card guard'><b>Access boundary</b><p>ESP Creator/Agent access is separate from Free, Basic and Pro public creative subscriptions. Agent discovery is for validated legitimate prospects and blocks outreach to creators represented by another Creator Network.</p></article>{''.join(cards)}</main></body></html>"""
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


__all__ = ["router", "catalog_for", "MODULES"]
