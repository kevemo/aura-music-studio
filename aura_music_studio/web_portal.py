from __future__ import annotations

import os
from html import escape
from urllib.parse import quote

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .accounts import AccountStore
from .billing import payment_instructions
from .billing_history import BillingHistoryService
from .branding import PRODUCT_FULL_NAME, PRODUCT_NAME, TAGLINE
from .mailer import notify_membership_request
from .membership import MembershipService
from .membership_billing_periods import MembershipBillingPreferenceStore
from .native_products import BillingPeriod
from .plans import PLANS, OWNERSHIP_NOTICE

router = APIRouter()
store = AccountStore()
memberships = MembershipService(store)
billing_preferences = MembershipBillingPreferenceStore(store)
COOKIE_NAME = "lss_session"

CSS = """
:root{--bg:#0b0712;--panel:#171020;--panel2:#21132d;--gold:#e8bd62;--text:#fff;--muted:#cbbfd5;--line:#3b294b;--good:#86e0a8;--bad:#ff9aa9}
*{box-sizing:border-box}body{margin:0;font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:radial-gradient(circle at top,#26113b 0,#0b0712 45%);color:var(--text);min-height:100vh}
a{color:inherit}.wrap{max-width:1180px;margin:auto;padding:22px}.nav{display:flex;align-items:center;justify-content:space-between;gap:20px;padding:16px 0}.brand{font-weight:900;letter-spacing:-.02em}.brand small{display:block;color:var(--gold);font-weight:700;font-size:.75rem;letter-spacing:.08em;text-transform:uppercase}.navlinks{display:flex;gap:10px;flex-wrap:wrap}.btn,button{display:inline-block;border:1px solid var(--line);background:#21152d;color:#fff;padding:11px 16px;border-radius:12px;text-decoration:none;font-weight:800;cursor:pointer}.btn.primary,button.primary{background:linear-gradient(135deg,#f1cf7a,#c99b3f);color:#160e1e;border:0}.btn.ghost{background:transparent}.hero{padding:80px 0 54px;display:grid;grid-template-columns:1.15fr .85fr;gap:35px;align-items:center}.hero h1{font-size:clamp(2.7rem,7vw,5.7rem);line-height:.94;margin:0 0 22px;letter-spacing:-.055em}.hero h1 span{color:var(--gold)}.hero p{font-size:1.2rem;line-height:1.65;color:var(--muted);max-width:760px}.hero-card,.card{background:linear-gradient(145deg,#1d1228,#110b18);border:1px solid var(--line);border-radius:24px;padding:26px;box-shadow:0 25px 80px #0006}.meter{height:9px;background:#30213d;border-radius:99px;overflow:hidden;margin:14px 0}.meter i{display:block;height:100%;width:88%;background:linear-gradient(90deg,#9d53d6,#e8bd62)}.eyebrow{color:var(--gold);text-transform:uppercase;letter-spacing:.16em;font-weight:900;font-size:.77rem}.section{padding:44px 0}.section h2{font-size:2.3rem;margin:0 0 12px}.section>p{color:var(--muted);max-width:780px}.grid3{display:grid;grid-template-columns:repeat(3,1fr);gap:18px;margin-top:28px}.price-card{position:relative}.price-card.pro{border-color:#bd8fe2;box-shadow:0 25px 75px #662a9b35}.badge{position:absolute;right:16px;top:16px;background:var(--gold);color:#1b1024;border-radius:99px;padding:6px 10px;font-size:.72rem;font-weight:900}.price{font-size:2.7rem;font-weight:950;margin:14px 0}.price small{font-size:.9rem;color:var(--muted)}ul.features{padding:0;list-style:none;line-height:1.55}.features li{padding:7px 0;border-bottom:1px solid #ffffff0e}.features li:before{content:'✓';color:var(--gold);font-weight:900;margin-right:8px}.form-card{max-width:590px;margin:52px auto}.form-card h1{margin-top:0}.field{margin:15px 0}.field label{display:block;font-weight:800;margin-bottom:7px}.field input,.field select,.field textarea{width:100%;background:#0e0914;border:1px solid var(--line);color:#fff;border-radius:12px;padding:13px;font:inherit}.help,.muted{color:var(--muted);font-size:.92rem}.alert{padding:13px 15px;border-radius:12px;margin:14px 0;background:#321c29;border:1px solid #6a3146}.alert.good{background:#143122;border-color:#2c704a}.dashboard{display:grid;grid-template-columns:280px 1fr;gap:20px;padding:32px 0}.sidebar h3{margin-top:0}.tier{font-size:1.45rem;color:var(--gold);font-weight:950}.tiles{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}.tile{background:#15101d;border:1px solid var(--line);padding:18px;border-radius:17px}.tile strong{display:block;font-size:1.1rem;margin-bottom:5px}.locked{opacity:.43}.esp-request{border-color:#e8bd6270;background:linear-gradient(145deg,#26182e,#120d18)}.footer{border-top:1px solid #ffffff10;color:var(--muted);padding:30px 0 50px;margin-top:35px;font-size:.88rem}
@media(max-width:820px){.hero,.grid3,.dashboard,.tiles{grid-template-columns:1fr}.hero{padding-top:45px}.nav{align-items:flex-start}.hero h1{font-size:3rem}}
"""


def _page(title: str, body: str, request: Request | None = None) -> HTMLResponse:
    member = None
    if request:
        member = store.resolve_session(request.cookies.get(COOKIE_NAME))
    auth_nav = (
        "<a class='btn ghost' href='/dashboard'>Dashboard</a>"
        if member else
        "<a class='btn ghost' href='/signin'>Sign in</a><a class='btn primary' href='/signup'>Join Studio</a>"
    )
    html = f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>{escape(title)} — {escape(PRODUCT_NAME)}</title><style>{CSS}</style></head><body>
<div class='wrap'><nav class='nav'><a class='brand' href='/' style='text-decoration:none'>{escape(PRODUCT_NAME)}<small>Elevate Souls Productions</small></a><div class='navlinks'><a class='btn ghost' href='/pricing'>Pricing</a>{auth_nav}</div></nav>{body}
<footer class='footer'>{escape(PRODUCT_FULL_NAME)} · {escape(TAGLINE)}<br><br>{escape(OWNERSHIP_NOTICE)}</footer></div></body></html>"""
    return HTMLResponse(html)


def _feature_names(plan_id: str) -> list[str]:
    if plan_id == "free":
        return ["Song ideas and basic creation", "Basic AI lyrics", "Aura Producer planning", "Basic real-audio previews"]
    if plan_id == "base":
        return ["1 confirmed full track every day", "Unlimited regeneration until that track is confirmed", "MP3 + WAV finished downloads", "Basic mastering", "Audio/score uploads", "Backing-track creation", "Harmony tools"]
    return ["Unlimited confirmed full tracks", "Unlimited regeneration", "All MP3/WAV/FLAC downloads", "Splitter + separated stems", "Full multitrack studio", "Advanced + reference mastering", "Cover/remix/repaint tools", "Sample Lab + Style DNA", "Harmony Architect", "Consent-approved voice duplication", "Automation + take lanes", "BandLab/stem exports", "Aura OS + Aura Sec included", "Every enabled studio feature"]


def _pricing_cards(selected: str | None = None) -> str:
    chunks = []
    for pid in ("free", "base", "pro"):
        plan = PLANS[pid]
        monthly = plan.display_price_for(BillingPeriod.MONTHLY)
        annual = ""
        actions = f"<a class='btn primary' href='/signup?plan={pid}&billing_period=monthly'>Choose this plan</a>"
        if plan.annual_price is not None and pid != "free":
            annual = f"<div class='muted'>{escape(plan.display_price_for(BillingPeriod.ANNUAL))} available</div>"
            actions = (
                f"<a class='btn primary' href='/signup?plan={pid}&billing_period=monthly'>Choose monthly</a> "
                f"<a class='btn' href='/signup?plan={pid}&billing_period=annual'>Choose yearly</a>"
            )
        cls = "card price-card pro" if pid == "pro" else "card price-card"
        badge = "<span class='badge'>FULL STUDIO</span>" if pid == "pro" else ""
        features = "".join(f"<li>{escape(x)}</li>" for x in _feature_names(pid))
        if selected == pid:
            badge = badge or "<span class='badge'>SELECTED</span>"
        chunks.append(
            f"<div class='{cls}'>{badge}<div class='eyebrow'>{escape(plan.name)}</div>"
            f"<div class='price'>{escape(monthly)}</div>{annual}<p class='muted'>{escape(plan.description)}</p>"
            f"<ul class='features'>{features}</ul>{actions}</div>"
        )
    return "<div class='grid3'>" + "".join(chunks) + "</div>"


def _period_value(value: str) -> BillingPeriod:
    try:
        return BillingPeriod(value)
    except ValueError as exc:
        raise ValueError(f"Unsupported billing period: {value}") from exc


def _pending_payment_html(user: dict, plan_id: str) -> str:
    plan = PLANS[plan_id]
    period = billing_preferences.approved_period_for_user(user["id"], plan_id)
    display = plan.display_price_for(period)
    try:
        pay = payment_instructions(plan_id, period)
    except ValueError:
        pay = None
    if pay and pay.get("url"):
        url = escape(str(pay.get("url") or ""), quote=True)
        return (
            f"<div class='alert'>Your membership was approved for <b>{escape(display)}</b>. "
            "Complete payment and wait for verified provider confirmation to activate your plan.</div>"
            f"<a class='btn primary' target='_blank' rel='noopener' href='{url}'>Open verified payment route</a>"
        )
    return (
        f"<div class='alert'>Your membership was approved for <b>{escape(display)}</b>. "
        "A verified payment-provider route for this billing period must be configured before activation. "
        "No browser return can activate paid access by itself.</div>"
    )


def _request_session_token(request: Request) -> str | None:
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.cookies.get(COOKIE_NAME)


def _billing_account(request: Request) -> dict:
    user = store.resolve_session(_request_session_token(request))
    if not user:
        raise HTTPException(401, "Authenticated account session required")
    return user


def _billing_history_html(history: dict) -> str:
    subscription = history.get("subscription") or {}
    scheduled = history.get("scheduled_transition") or {}
    account = history.get("account") or {}
    current = (
        f"<div class='card'><div class='eyebrow'>Current account billing</div>"
        f"<h2>{escape(str(account.get('plan_name') or 'Free'))}</h2>"
        f"<p>Status: <b>{escape(str(account.get('billing_status') or 'not required').replace('_',' '))}</b></p>"
        + (
            f"<p class='muted'>{escape(str(subscription.get('billing_period') or ''))} term · "
            f"{escape(str(subscription.get('period_start') or ''))} → {escape(str(subscription.get('period_end') or ''))}</p>"
            if subscription else
            "<p class='muted'>No current paid subscription term is recorded.</p>"
        )
        + (
            f"<p class='muted'>Scheduled: {escape(str(scheduled.get('target_plan_name') or scheduled.get('target_plan_id') or ''))} "
            f"({escape(str(scheduled.get('target_billing_period') or ''))}) from {escape(str(scheduled.get('effective_at') or ''))}.</p>"
            if scheduled else ""
        )
        + "</div>"
    )

    payments = history.get("payments") or []
    if payments:
        payment_cards = "".join(
            f"<div class='tile'><strong>{escape(str(item.get('display_amount') or ''))} · {escape(str(item.get('plan_name') or ''))}</strong>"
            f"<span class='muted'>{escape(str(item.get('billing_period') or ''))} billing<br>"
            f"Verified record: {escape(str(item.get('verified_at') or ''))}<br>"
            f"Receipt: {escape(str(item.get('receipt_reference') or ''))}<br>"
            f"Payment reference: {escape(str(item.get('payment_reference') or ''))}</span></div>"
            for item in payments
        )
    else:
        payment_cards = "<div class='tile'><strong>No paid records yet</strong><span class='muted'>Verified subscription payments will appear here.</span></div>"

    refunds = history.get("refunds") or []
    if refunds:
        refund_cards = "".join(
            f"<div class='tile'><strong>Refund recorded</strong><span class='muted'>"
            f"{escape(str(item.get('outcome_label') or 'Verified refund recorded'))}<br>"
            f"Verified record: {escape(str(item.get('verified_at') or ''))}<br>"
            f"Receipt: {escape(str(item.get('receipt_reference') or ''))}<br>"
            f"Refund reference: {escape(str(item.get('refund_reference') or ''))}</span></div>"
            for item in refunds
        )
    else:
        refund_cards = "<div class='tile'><strong>No refund records</strong><span class='muted'>Verified refunds will appear here without inventing an amount the ledger does not store.</span></div>"

    return (
        current
        + f"<section class='section'><div class='eyebrow'>Payment records</div><h2>Receipts & payment history</h2><div class='tiles'>{payment_cards}</div></section>"
        + f"<section class='section'><div class='eyebrow'>Refund records</div><h2>Refund history</h2><div class='tiles'>{refund_cards}</div></section>"
        + "<div class='alert'>This page is a read-only view of server-recorded verified billing events. It does not fabricate provider invoice URLs, and viewing this page does not independently re-verify bank or provider settlement.</div>"
    )


@router.get("/", response_class=HTMLResponse)
def home(request: Request):
    body = f"""
<section class='hero'><div><div class='eyebrow'>AI music creation · real audio · professional workflow</div><h1>Make music with <span>Aura.</span></h1><p>Create original songs, professional backing tracks, harmonies, stems, remixes and mastered releases inside one intelligent studio. Symbolic notation can guide the music, but the finished sound stays real-audio-first.</p><div class='navlinks'><a class='btn primary' href='/signup'>Start creating</a><a class='btn' href='/pricing'>Compare plans</a></div></div>
<div class='hero-card'><div class='eyebrow'>Aura Producer</div><h2>One studio. One creative brain.</h2><p class='muted'>Lyrics → arrangement → instruments → vocals → harmonies → mix → master → export.</p><div class='meter'><i></i></div><p><b>Real-audio final master</b><br><span class='muted'>No MIDI/SoundFont substitution as finished music.</span></p></div></section>
<section class='section'><div class='eyebrow'>Memberships</div><h2>Start basic. Unlock the whole studio.</h2><p>All memberships require approval by Elevate Souls Productions before access activates. Paid plans move to verified payment only after approval.</p>{_pricing_cards()}</section>"""
    return _page("Home", body, request)


@router.get("/pricing", response_class=HTMLResponse)
def pricing(request: Request):
    body = f"<section class='section'><div class='eyebrow'>Plans</div><h2>Choose your studio level</h2><p>Basic is £4.99/month. Unlimited Pro is £9.99/month or £99/year, includes Aura OS and Aura Sec entitlement, and unlocks the highest enabled creative access.</p>{_pricing_cards()}</section>"
    return _page("Pricing", body, request)


@router.get("/signup", response_class=HTMLResponse)
def signup_page(
    request: Request,
    plan: str = "free",
    billing_period: str = BillingPeriod.MONTHLY.value,
    error: str | None = None,
):
    plan = plan if plan in PLANS else "free"
    try:
        selected_period = _period_value(billing_period)
    except ValueError:
        selected_period = BillingPeriod.MONTHLY
    error_html = f"<div class='alert'>{escape(error)}</div>" if error else ""
    options = "".join(
        f"<option value='{pid}' {'selected' if pid == plan else ''}>{escape(PLANS[pid].name)} — {escape(PLANS[pid].display_price_for(BillingPeriod.MONTHLY))}</option>"
        for pid in ("free", "base", "pro")
    )
    period_options = (
        f"<option value='monthly' {'selected' if selected_period is BillingPeriod.MONTHLY else ''}>Monthly</option>"
        f"<option value='annual' {'selected' if selected_period is BillingPeriod.ANNUAL else ''}>Yearly — Unlimited Pro only (£99/year)</option>"
    )
    body = f"""<div class='card form-card'><div class='eyebrow'>Membership request</div><h1>Create your account</h1><p class='muted'>Your plan and billing period are part of the request Kev or Mary approves. Basic is monthly-only; Unlimited Pro can be monthly or yearly. Paid access activates only after verified payment evidence.</p>{error_html}
<form method='post' action='/signup'><div class='field'><label>Name</label><input name='display_name' required minlength='2' autocomplete='name'></div><div class='field'><label>Email</label><input type='email' name='email' required autocomplete='email'></div><div class='field'><label>Password</label><input type='password' name='password' required minlength='10' autocomplete='new-password'><div class='help'>Minimum 10 characters.</div></div><div class='field'><label>Membership</label><select name='plan_id'>{options}</select></div><div class='field'><label>Billing period</label><select name='billing_period'>{period_options}</select><div class='help'>Yearly billing is available for Unlimited Pro only.</div></div><button class='primary' type='submit'>Send membership request</button></form><p class='help'>Already have an account? <a href='/signin'>Sign in</a>.</p></div>"""
    return _page("Sign up", body, request)


@router.post("/signup", response_class=HTMLResponse)
def signup_submit(
    request: Request,
    display_name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    plan_id: str = Form("free"),
    billing_period: str = Form(BillingPeriod.MONTHLY.value),
):
    try:
        canonical_plan, period = billing_preferences.validate(plan_id, billing_period)
        result = store.signup(email, display_name, password, canonical_plan)
        billing_preferences.record_request(
            user_id=result.user_id,
            membership_request_id=result.membership_request_id,
            plan_id=result.requested_plan,
            billing_period=period,
        )
        notify_membership_request(
            approval_token=result.approval_token,
            applicant_email=result.email,
            display_name=result.display_name,
            plan_id=result.requested_plan,
        )
    except Exception as exc:
        return signup_page(request, plan_id, billing_period, str(exc))
    requested_name = PLANS[result.requested_plan].name if result.requested_plan in PLANS else result.requested_plan
    period_label = "yearly" if period is BillingPeriod.ANNUAL else "monthly"
    body = f"""<div class='card form-card'><div class='eyebrow'>Request received</div><h1>Membership pending approval</h1><div class='alert good'>Your request for the <b>{escape(requested_name)}</b> tier on <b>{escape(period_label)}</b> billing has been sent to Elevate Souls Productions.</div><p>You will be able to sign in while pending, but studio access stays locked until the request is approved. Paid plans then require verified payment before activation.</p><a class='btn primary' href='/signin'>Continue to sign in</a></div>"""
    return _page("Membership pending", body, request)


@router.get("/signin", response_class=HTMLResponse)
def signin_page(request: Request, error: str | None = None):
    error_html = f"<div class='alert'>{escape(error)}</div>" if error else ""
    body = f"""<div class='card form-card'><div class='eyebrow'>Member access</div><h1>Sign in</h1>{error_html}<form method='post' action='/signin'><div class='field'><label>Email</label><input type='email' name='email' required autocomplete='email'></div><div class='field'><label>Password</label><input type='password' name='password' required autocomplete='current-password'></div><button class='primary' type='submit'>Sign in</button></form><p class='help'>New here? <a href='/signup'>Request membership</a>.</p></div>"""
    return _page("Sign in", body, request)


@router.post("/signin")
def signin_submit(request: Request, email: str = Form(...), password: str = Form(...)):
    user = store.authenticate(email, password)
    if not user:
        return signin_page(request, "Incorrect email or password")
    token = store.create_session(user["id"])
    response = RedirectResponse("/dashboard", status_code=303)
    response.set_cookie(COOKIE_NAME, token, max_age=30*24*60*60, httponly=True, secure=(os.getenv("LSS_COOKIE_SECURE", "true").lower()=="true"), samesite="lax")
    return response


@router.post("/signout")
def signout(request: Request):
    token = request.cookies.get(COOKIE_NAME)
    store.revoke_session(token)
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie(COOKIE_NAME)
    return response


@router.get("/auth/me/billing-history")
def billing_history_json(request: Request):
    user = _billing_account(request)
    return BillingHistoryService(store).for_user(user["id"])


@router.get("/auth/billing-history", response_class=HTMLResponse)
def billing_history_page(request: Request):
    user = store.resolve_session(_request_session_token(request))
    if not user:
        return RedirectResponse("/signin", status_code=303)
    history = BillingHistoryService(store).for_user(user["id"])
    body = (
        "<section class='section'><div class='eyebrow'>Your account</div><h1>Billing history</h1>"
        "<p>View the subscription payments and refunds recorded for this signed-in account. This page is read-only.</p>"
        "<div class='navlinks'><a class='btn' href='/dashboard'>Back to dashboard</a></div></section>"
        + _billing_history_html(history)
    )
    return _page("Billing history", body, request)


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    token = request.cookies.get(COOKIE_NAME)
    user = store.resolve_session(token)
    if not user:
        return RedirectResponse("/signin", status_code=303)
    status = user["status"]
    requested = user.get("requested_plan_id") or user.get("plan_id") or "free"
    active_plan = user.get("plan_id") or "free"
    if status == "pending_approval":
        preference = billing_preferences.for_user(user["id"])
        requested_period = (preference or {}).get("billing_period") or BillingPeriod.MONTHLY.value
        state = f"<div class='alert'>Your {escape(requested_period)} membership request is waiting for ESP approval. Studio generation is locked until approval.</div>"
    elif status == "approved_pending_payment":
        try:
            state = _pending_payment_html(user, requested)
        except ValueError as exc:
            state = f"<div class='alert'>Billing approval needs owner review: {escape(str(exc))}</div>"
    elif status == "active":
        subscription = memberships.subscriptions.get(user["id"]) if active_plan != "free" else None
        period = (subscription or {}).get("billing_period")
        period_copy = f" · {escape(str(period))} billing" if period else ""
        state = f"<div class='alert good'>Your {escape(PLANS[active_plan].name)} membership is active{period_copy}.</div>"
    elif status == "rejected":
        state = "<div class='alert'>This membership request was not approved.</div>"
    else:
        state = f"<div class='alert'>Account status: {escape(status)}</div>"
    plan_for_tiles = PLANS[active_plan if status == "active" else "free"]
    tile_defs = [
        ("Create Music","Song creation and Aura production","basic_create"),
        ("Full Track","Finished real-audio generation","full_track"),
        ("Backing Tracks","Create professional backing arrangements","backing_track"),
        ("Mastering","Master finished mixes","basic_mastering"),
        ("Splitter / Stems","Separate vocals and instruments","stem_splitter"),
        ("Multitrack Studio","Tracks, takes, automation and editing","multitrack_daw"),
        ("Sample Lab","Generate and reshape samples","sample_lab"),
        ("Voice Studio","Consent-approved voice duplication","approved_voice_duplication"),
        ("Style DNA","Blend authorized style references","style_dna"),
    ]
    tiles = "".join(f"<div class='tile {'locked' if feature not in plan_for_tiles.features else ''}'><strong>{escape(name)}</strong><span class='muted'>{escape(desc)}</span><br><small>{'Available' if feature in plan_for_tiles.features and status=='active' else 'Locked / upgrade required'}</small></div>" for name,desc,feature in tile_defs)
    esp_request = """
    <section class='card esp-request'><div class='eyebrow'>Elevate Souls Productions members only</div><h2>Already an ESP Creator or Agent?</h2>
    <p class='muted'>Studio membership does not grant ESP Creator Network access. If you are already part of Elevate Souls Productions, submit your ESP status for owner verification. No ESP tools or training unlock until Mary or Kev approves the account.</p>
    <a class='btn primary' href='/command-center'>I am an Elevate Souls Productions Creator or Agent</a></section>
    """
    tier_plan_id = active_plan if status == "active" else requested
    tier_name = PLANS[tier_plan_id].name if tier_plan_id in PLANS else tier_plan_id
    body = f"""<div class='dashboard'><aside class='card sidebar'><div class='eyebrow'>Member</div><h3>{escape(user['display_name'])}</h3><p class='muted'>{escape(user['email'])}</p><div class='tier'>{escape(tier_name)}</div><p>Status: <b>{escape(status.replace('_',' '))}</b></p><p><a class='btn ghost' href='/auth/billing-history'>Billing history</a></p><form method='post' action='/signout'><button type='submit'>Sign out</button></form></aside><main><div class='eyebrow'>Studio dashboard</div><h1>Welcome to {escape(PRODUCT_NAME)}</h1>{state}<section class='section'><h2>Your creative tools</h2><div class='tiles'>{tiles}</div></section>{esp_request}</main></div>"""
    return _page("Dashboard", body, request)
