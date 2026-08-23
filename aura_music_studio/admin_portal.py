from __future__ import annotations

import os
import secrets
import sqlite3
from html import escape

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .accounts import AccountStore
from .branding import PRODUCT_FULL_NAME, TAGLINE
from .public_address import PublicAddressManager
from .subscriptions import SubscriptionLedger

router = APIRouter()
store = AccountStore()
subscriptions = SubscriptionLedger(store)
public_address = PublicAddressManager()
ADMIN_COOKIE = "lss_admin_session"

CSS = """
:root{--bg:#0c0713;--panel:#1a1124;--gold:#e8bd62;--text:#fff;--muted:#c8bbd3;--line:#423052;--green:#75d89d;--red:#ff8fa3}*{box-sizing:border-box}body{font-family:system-ui,sans-serif;background:#0c0713;color:#fff;margin:0}.wrap{max-width:1100px;margin:auto;padding:28px}.card{background:#1a1124;border:1px solid var(--line);border-radius:18px;padding:22px;margin:16px 0}.row{display:flex;gap:12px;align-items:center;justify-content:space-between;flex-wrap:wrap}.muted{color:var(--muted)}.gold{color:var(--gold)}input,select{background:#0d0813;border:1px solid var(--line);color:#fff;border-radius:10px;padding:11px}button,.btn{border:0;border-radius:10px;padding:11px 15px;font-weight:800;cursor:pointer;text-decoration:none;display:inline-block}.approve{background:var(--gold);color:#180f20}.reject{background:#552339;color:#fff}.activate{background:#245b3b;color:#fff}.top{display:flex;justify-content:space-between;align-items:center;gap:15px}.pill{border:1px solid var(--line);padding:5px 9px;border-radius:99px;font-size:.8rem}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}.kv{display:grid;grid-template-columns:190px 1fr;gap:8px 14px;margin:10px 0}.warn{color:#ffcf75}.good{color:var(--green)}.bad{color:var(--red)}code{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;word-break:break-all}@media(max-width:760px){.grid{grid-template-columns:1fr}.kv{grid-template-columns:1fr}}
"""


def _admin_key() -> str:
    return os.getenv("LSS_ADMIN_KEY") or ""


def _authorized(request: Request) -> bool:
    configured = _admin_key()
    supplied = request.cookies.get(ADMIN_COOKIE) or ""
    return bool(configured and supplied and secrets.compare_digest(configured, supplied))


def _page(body: str) -> HTMLResponse:
    return HTMLResponse(f"<!doctype html><html><head><meta name='viewport' content='width=device-width,initial-scale=1'><title>ESP Admin — {escape(PRODUCT_FULL_NAME)}</title><style>{CSS}</style></head><body><div class='wrap'>{body}</div></body></html>")


def _payment_queue() -> list[dict]:
    con = sqlite3.connect(store.db_path)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            """SELECT id, email, display_name, requested_plan_id, billing_status, approved_at
               FROM users WHERE status='approved_pending_payment'
               ORDER BY approved_at ASC"""
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        con.close()


def _address_panel() -> str:
    status = public_address.read_status()
    if not status.get("checked_at"):
        return """<div class='card'><h2>Public address</h2><p class='muted'>Aura has not completed a public-address check yet.</p><form method='post' action='/owner/public-address/refresh'><button class='approve'>Run address check</button></form></div>"""

    hostname = status.get("hostname") or "Not configured"
    public_ip = status.get("public_ipv4") or "Not detected"
    router_ip = status.get("router_external_ipv4") or "Not available"
    lan_ip = status.get("lan_ipv4") or "Not detected"
    recommended = status.get("recommended_url") or "Not currently reachable"
    provider = (status.get("provider") or "none").upper()
    dns = ", ".join(status.get("dns_addresses") or []) or "No DNS result"
    cgnat = bool(status.get("likely_cgnat"))
    https_ready = bool(status.get("caddy_https_ready"))
    warnings = status.get("warnings") or []
    warning_html = "".join(f"<li>{escape(str(item))}</li>" for item in warnings)
    state_class = "bad" if cgnat else "good"
    https_class = "good" if https_ready else "warn"
    return f"""<div class='card'><div class='row'><div><div class='gold'><b>AURA PUBLIC ADDRESS MANAGER</b></div><h2>Self-hosted Studio address</h2></div><span class='pill'>{escape(provider)}</span></div>
<div class='kv'><b>Recommended URL</b><code>{escape(str(recommended))}</code><b>Hostname</b><code>{escape(str(hostname))}</code><b>Public IPv4</b><code>{escape(str(public_ip))}</code><b>Router-facing IPv4</b><code>{escape(str(router_ip))}</code><b>LAN IPv4</b><code>{escape(str(lan_ip))}</code><b>DNS resolves to</b><code>{escape(dns)}</code><b>Likely CGNAT</b><span class='{state_class}'>{'YES — inbound IPv4 may be blocked upstream' if cgnat else 'No CGNAT signal detected'}</span><b>Hostname HTTPS readiness</b><span class='{https_class}'>{'DNS points at this host' if https_ready else 'Not yet verified'}</span></div>
<p class='muted'>Aura owns the address-management logic. FreeDNS/DuckDNS, when selected, only provide the optional hostname. Memberships, music, projects, database, workers and AI remain on the ESP-controlled host.</p>{f"<ul class='warn'>{warning_html}</ul>" if warning_html else ''}<div class='row' style='justify-content:flex-start'><form method='post' action='/owner/public-address/refresh'><button class='approve'>Refresh + update DDNS</button></form><a class='btn activate' href='{escape(str(recommended), quote=True)}' target='_blank' rel='noopener' {'style="pointer-events:none;opacity:.45"' if recommended == 'Not currently reachable' else ''}>Open public Studio</a></div></div>"""


@router.get("/owner", response_class=HTMLResponse)
def owner_login(request: Request, error: str | None = None):
    if _authorized(request):
        return RedirectResponse("/owner/dashboard", status_code=303)
    message = f"<p style='color:var(--red)'>{escape(error)}</p>" if error else ""
    return _page(f"<div class='card' style='max-width:520px;margin:80px auto'><div class='gold'><b>Elevate Souls Productions</b></div><h1>The Live Sound Studio Owner Access</h1><p class='muted'>For authorised ESP owners/administrators only.</p>{message}<form method='post' action='/owner/login'><input type='password' name='admin_key' placeholder='Owner admin key' required style='width:100%;margin:12px 0'><button class='approve' type='submit'>Enter owner dashboard</button></form></div>")


@router.post("/owner/login")
def owner_login_submit(admin_key: str = Form(...)):
    configured = _admin_key()
    if not configured or not secrets.compare_digest(configured, admin_key):
        return RedirectResponse("/owner?error=Incorrect+owner+key", status_code=303)
    response = RedirectResponse("/owner/dashboard", status_code=303)
    response.set_cookie(
        ADMIN_COOKIE,
        admin_key,
        max_age=12 * 60 * 60,
        httponly=True,
        secure=(os.getenv("LSS_COOKIE_SECURE", "true").lower() == "true"),
        samesite="strict",
    )
    return response


@router.post("/owner/logout")
def owner_logout():
    response = RedirectResponse("/owner", status_code=303)
    response.delete_cookie(ADMIN_COOKIE)
    return response


@router.get("/owner/dashboard", response_class=HTMLResponse)
def owner_dashboard(request: Request):
    if not _authorized(request):
        return RedirectResponse("/owner", status_code=303)

    pending = store.pending_requests()
    if pending:
        pending_html = "".join(
            f"""<div class='card'><div class='row'><div><b>{escape(item['display_name'])}</b><br><span class='muted'>{escape(item['email'])}</span></div><span class='pill'>{escape(item['requested_plan_id'].upper())}</span></div><p class='muted'>Requested: {escape(item['created_at'])}</p><p>Use the secure approval link sent to the ESP membership inbox to approve or reject this request.</p></div>"""
            for item in pending
        )
    else:
        pending_html = "<div class='card'><p class='muted'>No pending membership requests.</p></div>"

    payment_rows = _payment_queue()
    if payment_rows:
        payment_html = "".join(
            f"""<div class='card'><div class='row'><div><b>{escape(item['display_name'])}</b><br><span class='muted'>{escape(item['email'])}</span><br><small>User ID: {escape(item['id'])}</small></div><span class='pill'>{escape(item['requested_plan_id'].upper())}</span></div><p class='muted'>Approved: {escape(item.get('approved_at') or '')}</p><form method='post' action='/owner/activate-payment'><input type='hidden' name='user_id' value='{escape(item['id'], quote=True)}'><input type='hidden' name='plan_id' value='{escape(item['requested_plan_id'], quote=True)}'><div class='row' style='justify-content:flex-start'><input name='payment_reference' placeholder='PayPal transaction/invoice reference' required><button class='activate'>Verify payment + activate 31 days</button></div></form></div>"""
            for item in payment_rows
        )
    else:
        payment_html = "<div class='card'><p class='muted'>No approved members are waiting for payment verification.</p></div>"

    body = f"""<div class='top'><div><div class='gold'><b>ESP OWNER CONTROL</b></div><h1>{escape(PRODUCT_FULL_NAME)}</h1><p class='muted'>{escape(TAGLINE)}</p></div><form method='post' action='/owner/logout'><button class='reject'>Sign out</button></form></div>
<div class='grid'><div class='card'><b>Free</b><h2>$0</h2><span class='muted'>Basic studio</span></div><div class='card'><b>Base</b><h2>$4.99</h2><span class='muted'>1 confirmed track/day</span></div><div class='card'><b>Pro</b><h2>$9.99</h2><span class='muted'>Unlimited full studio</span></div></div>
{_address_panel()}
<h2>Pending membership requests</h2>{pending_html}
<h2>Approved — waiting for PayPal verification</h2>{payment_html}
<div class='card'><h2>Monthly access rule</h2><p class='muted'>Each verified Base/Pro payment grants a 31-day billing period. Another verified payment extends the existing paid-through date. When that period expires, the account automatically returns to payment-pending until renewal is verified. Duplicate payment references are rejected.</p></div>"""
    return _page(body)


@router.post("/owner/public-address/refresh", response_class=HTMLResponse)
def owner_public_address_refresh(request: Request):
    if not _authorized(request):
        return RedirectResponse("/owner", status_code=303)
    try:
        public_address.check(update_ddns=True)
        return RedirectResponse("/owner/dashboard", status_code=303)
    except Exception as exc:
        return _page(
            f"<div class='card'><h1>Public address refresh</h1><p class='bad'>Aura could not complete the address check: {escape(type(exc).__name__ + ': ' + str(exc))}</p><a class='btn approve' href='/owner/dashboard'>Back to owner dashboard</a></div>"
        )


@router.post("/owner/activate-payment", response_class=HTMLResponse)
def owner_activate_payment(request: Request, user_id: str = Form(...), plan_id: str = Form(...), payment_reference: str = Form(...)):
    if not _authorized(request):
        return RedirectResponse("/owner", status_code=303)
    try:
        status = subscriptions.verify_payment(user_id, plan_id, payment_reference)
        user = status["user"] or {}
        state = status["subscription"] or {}
        message = (
            f"Activated {escape(user.get('display_name','member'))} on {escape(plan_id.upper())}. "
            f"Paid through {escape(state.get('period_end',''))}. Payment reference: {escape(payment_reference)}"
        )
        colour = "var(--green)"
    except Exception as exc:
        message = f"Activation failed: {escape(str(exc))}"
        colour = "var(--red)"
    return _page(f"<div class='card'><h1>Payment verification</h1><p style='color:{colour}'>{message}</p><a class='btn approve' href='/owner/dashboard'>Back to owner dashboard</a></div>")
