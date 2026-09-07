from __future__ import annotations

import sqlite3
from html import escape
from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from .branding import ENDORSEMENT, PRODUCT_FULL_NAME, TAGLINE
from .owner_auth import owner_authorized
from .owner_dashboard_preferences import (
    DASHBOARD_WIDGETS,
    DEFAULT_ROUTES,
    DENSITIES,
    LAYOUT_MODES,
    OwnerDashboardPreferenceStore,
)
from .owner_identity import OWNER_THEMES, owner_theme, request_owner_persona
from .owner_user_control import OwnerUserControl

router = APIRouter()
control = OwnerUserControl()
preferences = OwnerDashboardPreferenceStore(control.db_path)

_WIDGET_LABELS = {
    "executive_summary": "Executive Summary",
    "esp_network": "ESP Network",
    "creator_development": "Creator Development",
    "agent_performance": "Agent Performance",
    "finance": "Finance Overview",
    "subscriptions_credits": "Subscriptions & Credits",
    "ai_usage_costs": "AI Usage & Cost Readiness",
    "shop_commerce": "Shop & Commerce",
    "training": "Training",
    "support": "Support",
    "system_health": "System Health",
    "recent_owner_actions": "Recent Owner Actions",
}

_STYLE = """
*{box-sizing:border-box}body{margin:0;background:#07050c;color:#fff;font-family:Inter,ui-sans-serif,system-ui,sans-serif}a{color:inherit;text-decoration:none}button,.btn,input,select{font:inherit}.wrap{width:min(1500px,calc(100% - 28px));margin:auto;padding:22px 0 60px}.top,.row{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap}.brand{display:flex;align-items:center;gap:11px}.orb{width:46px;height:46px;border-radius:15px;background:radial-gradient(circle at 30% 25%,#fff,var(--accent) 22%,var(--secondary) 70%,#0c0611);box-shadow:0 0 30px var(--glow)}.eyebrow{font-size:.71rem;letter-spacing:.15em;text-transform:uppercase;color:var(--accent);font-weight:950}h1{font-size:clamp(2.1rem,5vw,4.2rem);letter-spacing:-.05em;margin:.12em 0}h2{margin:.15em 0 .45em}.muted{color:#cabfd3;line-height:1.55}.actions{display:flex;gap:7px;flex-wrap:wrap;align-items:center}.btn,button{display:inline-block;border:1px solid #ffffff20;border-radius:11px;padding:9px 12px;background:#ffffff09;color:#fff;font-weight:850;cursor:pointer}.persona.active{border-color:var(--accent);box-shadow:0 0 0 2px var(--glow) inset}.primary{background:linear-gradient(110deg,var(--accent),var(--secondary))!important;color:#120916!important;border:0!important}.grid{display:grid;grid-template-columns:repeat(12,minmax(0,1fr));gap:11px}.widget{grid-column:span 6;border:1px solid #ffffff20;border-radius:20px;padding:18px;background:linear-gradient(145deg,#17101fee,#0d0a13ed);box-shadow:0 18px 55px #0005}body.compact .widget{padding:12px;border-radius:15px}body.layout-executive .widget:first-child,body.layout-finance .widget[data-widget='finance'],body.layout-network .widget[data-widget='esp_network'],body.layout-operations .widget[data-widget='agent_performance'],body.layout-creative .widget[data-widget='creator_development']{grid-column:span 12}.metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px}.metric{border:1px solid #ffffff20;border-radius:13px;padding:11px;background:#ffffff05}.metric b{display:block;font-size:1.55rem;margin-top:3px}.metric small{color:#cabfd3}.list{display:grid;gap:8px}.item{border-left:3px solid var(--accent);padding-left:10px}.settings{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}label{display:block;color:#cabfd3;font-size:.8rem;font-weight:800}select,input{width:100%;margin-top:5px;border:1px solid #ffffff20;border-radius:10px;padding:10px;background:#08060c;color:#fff}.widget-toggle{display:flex;align-items:center;gap:7px;border:1px solid #ffffff20;border-radius:10px;padding:8px}.widget-toggle input{width:auto;margin:0}@media(max-width:1000px){.widget{grid-column:span 12}.settings{grid-template-columns:1fr}}@media(max-width:700px){.metrics{grid-template-columns:1fr 1fr}}@media(max-width:460px){.metrics{grid-template-columns:1fr}}
"""


def _authorized(request: Request) -> bool:
    return owner_authorized(request)


def _credit_summary() -> dict:
    result = {
        "wallets": 0,
        "credits_in_circulation": 0,
        "purchase_events": 0,
        "spend_events": 0,
    }
    try:
        with sqlite3.connect(control.db_path) as con:
            exists = con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='credit_wallets'"
            ).fetchone()
            if not exists:
                return result
            row = con.execute(
                "SELECT COUNT(*),COALESCE(SUM(balance),0) FROM credit_wallets"
            ).fetchone()
            result["wallets"] = int(row[0] or 0)
            result["credits_in_circulation"] = int(row[1] or 0)
            tx_exists = con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='credit_transactions'"
            ).fetchone()
            if tx_exists:
                result["purchase_events"] = int(
                    con.execute(
                        "SELECT COUNT(*) FROM credit_transactions WHERE kind='purchase'"
                    ).fetchone()[0]
                    or 0
                )
                result["spend_events"] = int(
                    con.execute(
                        "SELECT COUNT(*) FROM credit_transactions WHERE kind='spend'"
                    ).fetchone()[0]
                    or 0
                )
    except sqlite3.Error:
        pass
    return result


def _persona_switch(persona: str | None) -> str:
    chunks: list[str] = []
    for key, value in OWNER_THEMES.items():
        active = " active" if persona == key else ""
        chunks.append(
            "<form method='post' action='/owner/persona/"
            + escape(key, quote=True)
            + "' style='display:inline'><button class='persona"
            + active
            + "' type='submit'>"
            + escape(value.display_name)
            + "</button></form>"
        )
    return "".join(chunks)


def _page(request: Request, body: str, *, title: str) -> HTMLResponse:
    persona = request_owner_persona(request)
    theme = owner_theme(persona)
    who = theme.display_name if persona else "Mary / Kev"
    pref = preferences.get(persona) if persona else None
    layout = pref.layout_mode if pref else "executive"
    density = pref.density if pref else "comfortable"
    css_vars = (
        "--accent:"
        + escape(theme.accent, quote=True)
        + ";--secondary:"
        + escape(theme.secondary, quote=True)
        + ";--glow:"
        + escape(theme.glow, quote=True)
    )
    head = (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1,viewport-fit=cover'>"
        "<meta name='robots' content='noindex,nofollow'><title>"
        + escape(title)
        + " — "
        + escape(PRODUCT_FULL_NAME)
        + "</title><style>"
        + _STYLE
        + "</style></head>"
    )
    header = (
        "<header class='top'><div class='brand'><div class='orb'></div><div>"
        "<div class='eyebrow'>Elevate Souls Productions · Owner Admin</div><b>"
        + escape(PRODUCT_FULL_NAME)
        + "</b><div class='muted' style='font-size:.78rem'>"
        + escape(ENDORSEMENT)
        + "</div></div></div><div class='actions'><span class='muted'>Using as <b style='color:var(--accent)'>"
        + escape(who)
        + "</b></span>"
        + _persona_switch(persona)
        + "<a class='btn' href='/owner/preferences'>Customize</a>"
        + "<a class='btn' href='/owner/users'>Users</a>"
        + "<a class='btn' href='/owner/backups'>Backups</a>"
        + "<a class='btn' href='/owner/compute-nodes'>Compute</a>"
        + "<form method='post' action='/owner/logout' style='display:inline'><button type='submit'>Sign out</button></form>"
        + "</div></header>"
    )
    html = (
        head
        + "<body class='layout-"
        + escape(layout, quote=True)
        + " "
        + escape(density, quote=True)
        + "' style='"
        + css_vars
        + "'><main class='wrap'>"
        + header
        + body
        + "<footer class='muted' style='padding-top:28px'>"
        + escape(TAGLINE)
        + "</footer></main></body></html>"
    )
    return HTMLResponse(html)


def _metric(label: str, value) -> str:
    return (
        "<div class='metric'><small>"
        + escape(label)
        + "</small><b>"
        + escape(str(value))
        + "</b></div>"
    )


def _widgets(summary: dict, rows: list[dict], audits: list[dict], persona: str) -> dict[str, str]:
    active_creators = [
        row
        for row in rows
        if row.get("esp_status") == "active" and row.get("esp_roles") in {"creator", "both"}
    ]
    active_agents = [
        row
        for row in rows
        if row.get("esp_status") == "active" and row.get("esp_roles") in {"agent", "both"}
    ]
    recent_progress = sorted(
        [row for row in rows if row.get("last_progress_at")],
        key=lambda row: row.get("last_progress_at") or "",
        reverse=True,
    )[:5]
    training_rows = [
        float(row.get("avg_training_percent") or 0)
        for row in rows
        if row.get("esp_status") == "active"
    ]
    avg_training = round(sum(training_rows) / len(training_rows)) if training_rows else 0
    usage_events = sum(int(row.get("usage_events") or 0) for row in rows)
    credits = _credit_summary()
    plans = summary.get("plans") or {}

    audit_html = "".join(
        "<div class='item'><b>"
        + escape(str(item.get("action") or "").replace("_", " ").title())
        + "</b><div class='muted'>"
        + escape(str(item.get("actor") or ""))
        + " · "
        + escape(str(item.get("created_at") or "")[:16].replace("T", " "))
        + "</div></div>"
        for item in audits[:5]
    ) or "<p class='muted'>No recent owner actions.</p>"
    progress_html = "".join(
        "<div class='item'><b>"
        + escape(str(row.get("display_name") or "Creator"))
        + "</b><div class='muted'>"
        + str(int(row.get("progress_submissions") or 0))
        + " progress submissions · "
        + str(round(float(row.get("avg_training_percent") or 0)))
        + "% training</div></div>"
        for row in recent_progress
    ) or "<p class='muted'>No creator progress submissions yet.</p>"

    owner_name = escape(owner_theme(persona).display_name)
    return {
        "executive_summary": (
            "<div class='eyebrow'>Executive</div><h2>"
            + owner_name
            + "'s Command View</h2><div class='metrics'>"
            + _metric("All users", summary.get("users", 0))
            + _metric("ESP creators", summary.get("esp_creators", 0))
            + _metric("ESP agents", summary.get("esp_agents", 0))
            + _metric("ESP requests", summary.get("esp_pending", 0))
            + "</div><p class='muted'>Aura can use this owner-selected presentation context, but permissions and critical controls remain server-enforced.</p>"
        ),
        "esp_network": (
            "<div class='eyebrow'>ESP Network</div><h2>Creator & Agent Network</h2><div class='metrics'>"
            + _metric("Creators", len(active_creators))
            + _metric("Agents", len(active_agents))
            + _metric("Pending", summary.get("esp_pending", 0))
            + _metric("Progress uploads", summary.get("progress_submissions", 0))
            + "</div><p><a class='btn primary' href='/owner/users'>Manage roles & members</a></p>"
        ),
        "creator_development": (
            "<div class='eyebrow'>Creator Development</div><h2>Latest Progress</h2><div class='list'>"
            + progress_html
            + "</div><p><a class='btn' href='/owner/users'>Open creator intelligence</a></p>"
        ),
        "agent_performance": (
            "<div class='eyebrow'>Agent Operations</div><h2>Mentor & Recruitment Oversight</h2><div class='metrics'>"
            + _metric("Active agents", len(active_agents))
            + _metric("Creators assigned/active", len(active_creators))
            + "</div><p class='muted'>Detailed evidence-driven mentoring, recruitment academy and compensation review remain in the protected ESP operations surfaces.</p>"
        ),
        "finance": (
            "<div class='eyebrow'>Finance</div><h2>Verified Commercial Inputs</h2><div class='metrics'>"
            + _metric("Free", plans.get("free", 0))
            + _metric("Basic", plans.get("base", 0))
            + _metric("Pro", plans.get("pro", 0))
            + _metric("Credit purchase events", credits["purchase_events"])
            + "</div><p class='muted'>Plan and credit-ledger activity is shown here. Fiat revenue, fees, tax and net margin must come from verified billing/provider records; this dashboard does not invent currency totals.</p>"
        ),
        "subscriptions_credits": (
            "<div class='eyebrow'>Credits</div><h2>Platform Credit Economy</h2><div class='metrics'>"
            + _metric("Wallets", credits["wallets"])
            + _metric("Credits in circulation", credits["credits_in_circulation"])
            + _metric("Purchase events", credits["purchase_events"])
            + _metric("Spend events", credits["spend_events"])
            + "</div><p class='muted'>Credits are platform units only and never grant an ESP Creator or Agent role.</p>"
        ),
        "ai_usage_costs": (
            "<div class='eyebrow'>AI Operations</div><h2>Usage & Cost Readiness</h2><div class='metrics'>"
            + _metric("Tracked usage events", usage_events)
            + _metric("Tracked projects", sum(int(row.get("project_count") or 0) for row in rows))
            + "</div><p class='muted'>Provider/model cost totals are shown only when verified cost telemetry exists. Usage counts are never relabelled as money.</p>"
        ),
        "shop_commerce": "<div class='eyebrow'>Commerce</div><h2>Shop Creator Operations</h2><p class='muted'>Shopify/TikTok Shop workflows remain approval-gated and provider-receipt driven. Pending external work is never represented as completed.</p><p><a class='btn' href='/owner/users'>Review authorised ESP members</a></p>",
        "training": (
            "<div class='eyebrow'>Training</div><h2>ESP Learning Oversight</h2><div class='metrics'>"
            + _metric("Active ESP members", len([row for row in rows if row.get("esp_status") == "active"]))
            + _metric("Average recorded completion", f"{avg_training}%")
            + "</div><p class='muted'>Creator and Agent training remains role-gated and owner-activated.</p>"
        ),
        "support": "<div class='eyebrow'>Support</div><h2>Owner Escalation</h2><p class='muted'>Use the protected user directory and ESP support surfaces to review member issues. Private creative content is not surfaced here merely for convenience.</p>",
        "system_health": "<div class='eyebrow'>Platform Health</div><h2>Infrastructure & Recovery</h2><p><a class='btn' href='/owner/compute-nodes'>Compute nodes</a> <a class='btn' href='/owner/backups'>Backups</a></p><p class='muted'>Operational health remains separate from marketing/commercial metrics so failures cannot be hidden by dashboard customization.</p>",
        "recent_owner_actions": (
            "<div class='eyebrow'>Audit</div><h2>Recent Owner Actions</h2><div class='list'>"
            + audit_html
            + "</div><p><a class='btn' href='/owner/audit'>Full audit trail</a></p>"
        ),
    }


@router.get("/owner/dashboard", response_class=HTMLResponse, include_in_schema=False)
def personalized_owner_dashboard(request: Request):
    if not _authorized(request):
        return RedirectResponse("/owner", status_code=303)
    persona = request_owner_persona(request)
    if persona is None:
        choose = "".join(
            "<form method='post' action='/owner/persona/"
            + escape(key, quote=True)
            + "' style='display:inline-block;margin:5px'><button class='primary' type='submit'>Use "
            + escape(theme.display_name)
            + " Dashboard</button></form>"
            for key, theme in OWNER_THEMES.items()
        )
        return _page(
            request,
            "<section class='widget' style='grid-column:span 12'><div class='eyebrow'>Owner identity</div><h1>Who is using Admin?</h1><p class='muted'>Choose Mary or Kev. This selects presentation, Aura owner context and audit identity only; the signed owner session remains the authorization boundary.</p>"
            + choose
            + "</section>",
            title="Owner Dashboard",
        )

    pref = preferences.get(persona)
    summary = control.dashboard_summary()
    rows = control.list_users()
    audits = control.audit_log(limit=8)
    widget_map = _widgets(summary, rows, audits, persona)
    visible = "".join(
        "<section class='widget' data-widget='"
        + escape(key, quote=True)
        + "'>"
        + widget_map[key]
        + "</section>"
        for key in pref.visible_widgets
        if key in widget_map
    )
    if not visible:
        visible = "<section class='widget' style='grid-column:span 12'><h2>No optional widgets selected</h2><p class='muted'>Critical owner navigation remains available above. Open Customize to restore or select dashboard widgets.</p></section>"
    theme = owner_theme(persona)
    body = (
        "<section class='row' style='margin:18px 0'><div><div class='eyebrow'>"
        + escape(pref.layout_mode.title())
        + " layout · "
        + escape(pref.density)
        + " density</div><h1>"
        + escape(theme.display_name)
        + "'s Dashboard</h1><p class='muted'>"
        + escape(theme.aura_context)
        + "</p></div><a class='btn primary' href='/owner/preferences'>Customize dashboard</a></section>"
        + "<section class='grid'>"
        + visible
        + "</section>"
    )
    return _page(request, body, title=f"{theme.display_name} Owner Dashboard")


@router.get("/owner/preferences", response_class=HTMLResponse, include_in_schema=False)
def owner_preferences_page(request: Request):
    if not _authorized(request):
        return RedirectResponse("/owner", status_code=303)
    persona = request_owner_persona(request)
    if persona is None:
        return RedirectResponse("/owner/dashboard", status_code=303)
    pref = preferences.get(persona)
    hidden = set(pref.hidden_widgets)
    order_text = ", ".join(pref.widget_order)
    toggles = "".join(
        "<label class='widget-toggle'><input type='checkbox' name='hidden_widgets' value='"
        + escape(widget, quote=True)
        + "'"
        + (" checked" if widget in hidden else "")
        + "> Hide "
        + escape(_WIDGET_LABELS[widget])
        + "</label>"
        for widget in DASHBOARD_WIDGETS
    )
    layout_options = "".join(
        "<option value='"
        + escape(value, quote=True)
        + "'"
        + (" selected" if value == pref.layout_mode else "")
        + ">"
        + escape(value.title())
        + "</option>"
        for value in sorted(LAYOUT_MODES)
    )
    density_options = "".join(
        "<option value='"
        + escape(value, quote=True)
        + "'"
        + (" selected" if value == pref.density else "")
        + ">"
        + escape(value.title())
        + "</option>"
        for value in sorted(DENSITIES)
    )
    route_options = "".join(
        "<option value='"
        + escape(value, quote=True)
        + "'"
        + (" selected" if value == pref.default_route else "")
        + ">"
        + escape(value)
        + "</option>"
        for value in sorted(DEFAULT_ROUTES)
    )
    theme = owner_theme(persona)
    body = (
        "<section style='margin:18px 0'><div class='eyebrow'>Personal Admin Workspace</div><h1>Customize "
        + escape(theme.display_name)
        + "'s Dashboard</h1><p class='muted'>These settings belong only to "
        + escape(theme.display_name)
        + ". They change presentation, not permissions, ESP roles, financial authority or security controls.</p></section>"
        + "<section class='widget' style='grid-column:span 12'><form method='post' action='/owner/preferences'>"
        + "<div class='settings'><label>Layout mode<select name='layout_mode'>"
        + layout_options
        + "</select></label><label>Density<select name='density'>"
        + density_options
        + "</select></label><label>Default owner view<select name='default_route'>"
        + route_options
        + "</select></label></div><label style='margin-top:14px'>Widget order (comma separated)<input name='widget_order' value='"
        + escape(order_text, quote=True)
        + "'></label><div class='settings' style='margin-top:14px'>"
        + toggles
        + "</div><div class='actions' style='margin-top:16px'><button class='primary' type='submit'>Save "
        + escape(theme.display_name)
        + " Preferences</button></form><form method='post' action='/owner/preferences/reset'><button type='submit'>Reset to "
        + escape(theme.display_name)
        + " Default</button></form><a class='btn' href='/owner/dashboard'>Back to dashboard</a></div></section>"
    )
    return _page(request, body, title="Owner Dashboard Preferences")


@router.post("/owner/preferences", include_in_schema=False)
def save_owner_preferences(
    request: Request,
    layout_mode: str = Form(...),
    density: str = Form(...),
    default_route: str = Form(...),
    widget_order: str = Form(""),
    hidden_widgets: list[str] = Form(default=[]),
):
    if not _authorized(request):
        return RedirectResponse("/owner", status_code=303)
    persona = request_owner_persona(request)
    if persona is None:
        return RedirectResponse("/owner/dashboard", status_code=303)
    try:
        preferences.save(
            persona,
            layout_mode=layout_mode,
            density=density,
            default_route=default_route,
            widget_order=[part.strip() for part in widget_order.split(",")],
            hidden_widgets=hidden_widgets,
        )
    except ValueError as exc:
        return RedirectResponse(
            "/owner/preferences?error=" + quote(str(exc), safe=""),
            status_code=303,
        )
    return RedirectResponse("/owner/dashboard", status_code=303)


@router.post("/owner/preferences/reset", include_in_schema=False)
def reset_owner_preferences(request: Request):
    if not _authorized(request):
        return RedirectResponse("/owner", status_code=303)
    persona = request_owner_persona(request)
    if persona is None:
        return RedirectResponse("/owner/dashboard", status_code=303)
    preferences.reset(persona)
    return RedirectResponse("/owner/dashboard", status_code=303)


@router.get("/owner/preferences/context", include_in_schema=False)
def owner_preferences_context(request: Request):
    if not _authorized(request):
        return JSONResponse({"detail": "Owner authentication required"}, status_code=401)
    persona = request_owner_persona(request)
    if persona is None:
        return JSONResponse({"detail": "Choose Mary or Kev first"}, status_code=409)
    pref = preferences.get(persona)
    return {
        "owner": owner_theme(persona).display_name,
        "preferences": pref.as_dict(),
        "aura_context": pref.aura_context(),
        "authority_note": "Presentation preferences do not grant or change permissions.",
    }


__all__ = ["router", "preferences"]
