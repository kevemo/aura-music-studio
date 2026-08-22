from __future__ import annotations

import os
import secrets
from html import escape

from fastapi import APIRouter, Form, Header, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from .accounts import AccountStore
from .billing import payment_instructions, public_payment_options
from .branding import PRODUCT_FULL_NAME, TAGLINE
from .mailer import notify_membership_decision, notify_membership_request
from .membership import MembershipService
from .plans import OWNERSHIP_NOTICE, public_plans

router = APIRouter()
store = AccountStore()
memberships = MembershipService(store)
COOKIE_NAME = "lss_session"


class SignupRequest(BaseModel):
    email: str
    display_name: str
    password: str
    plan_id: str = "free"


class LoginRequest(BaseModel):
    email: str
    password: str


class PaymentActivationRequest(BaseModel):
    user_id: str
    plan_id: str
    payment_reference: str


def session_token(request: Request) -> str | None:
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.cookies.get(COOKIE_NAME)


def _admin_authorized(x_lss_admin_key: str | None) -> bool:
    configured = os.getenv("LSS_ADMIN_KEY") or ""
    return bool(configured and x_lss_admin_key and secrets.compare_digest(configured, x_lss_admin_key))


def require_admin(x_lss_admin_key: str | None) -> None:
    if not _admin_authorized(x_lss_admin_key):
        raise HTTPException(403, "ESP administrator authorization required")


@router.get("/plans")
def plans():
    return {
        "plans": public_plans(),
        "payments": public_payment_options(),
        "ownership_notice": OWNERSHIP_NOTICE,
        "approval_required": True,
        "approval_email": "elevatesoulsproductions@gmail.com",
    }


@router.post("/auth/signup")
def signup(payload: SignupRequest):
    try:
        result = store.signup(payload.email, payload.display_name, payload.password, payload.plan_id)
        delivery = notify_membership_request(
            approval_token=result.approval_token,
            applicant_email=result.email,
            display_name=result.display_name,
            plan_id=result.requested_plan,
        )
        return {
            "created": True,
            "status": "pending_approval",
            "message": "Your membership request has been sent to Elevate Souls Productions for approval.",
            "requested_plan": result.requested_plan,
            "approval_notification": delivery,
        }
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/auth/login")
def login(payload: LoginRequest, response: Response):
    user = store.authenticate(payload.email, payload.password)
    if not user:
        raise HTTPException(401, "Incorrect email or password")
    token = store.create_session(user["id"])
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=30 * 24 * 60 * 60,
        httponly=True,
        secure=(os.getenv("LSS_COOKIE_SECURE", "true").lower() == "true"),
        samesite="lax",
    )
    profile = memberships.profile(token)
    if profile["status"] == "approved_pending_payment":
        profile["payment"] = payment_instructions(user["requested_plan_id"])
    return {"session_token": token, "member": profile}


@router.post("/auth/logout")
def logout(request: Request, response: Response):
    token = session_token(request)
    store.revoke_session(token)
    response.delete_cookie(COOKIE_NAME)
    return {"signed_out": True}


@router.get("/auth/me")
def me(request: Request):
    token = session_token(request)
    try:
        profile = memberships.profile(token or "")
    except PermissionError as exc:
        raise HTTPException(401, str(exc)) from exc
    if profile["status"] == "approved_pending_payment":
        user = store.resolve_session(token)
        if user:
            profile["payment"] = payment_instructions(user["requested_plan_id"])
    return profile


@router.get("/membership/review", response_class=HTMLResponse)
def review_membership(token: str):
    request_item = store.membership_request_from_token(token)
    if not request_item:
        return HTMLResponse("<h2>Membership request not found.</h2>", status_code=404)
    if request_item.get("expired"):
        return HTMLResponse("<h2>This membership approval link has expired.</h2>", status_code=410)
    if request_item.get("status") != "pending":
        return HTMLResponse(f"<h2>This request is already {escape(request_item['status'])}.</h2>")
    name = escape(request_item["display_name"])
    email = escape(request_item["email"])
    plan = escape(request_item["requested_plan_id"].upper())
    safe_token = escape(token, quote=True)
    return HTMLResponse(f"""
<!doctype html>
<html><head><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Membership Review — {escape(PRODUCT_FULL_NAME)}</title>
<style>
body{{font-family:system-ui,sans-serif;background:#0d0715;color:#fff;margin:0;padding:30px}}
.card{{max-width:650px;margin:60px auto;padding:30px;border-radius:24px;background:#1d1230;box-shadow:0 20px 60px #0008}}
h1{{margin-top:0}} .muted{{color:#c9bfd5}} button{{padding:14px 22px;border:0;border-radius:12px;font-weight:800;margin-right:12px;cursor:pointer}}
.approve{{background:#f2c35c;color:#1a1024}} .reject{{background:#53233b;color:#fff}} input{{width:100%;box-sizing:border-box;padding:12px;border-radius:10px;border:1px solid #5f4a72;background:#120b1e;color:white;margin:8px 0 20px}}
</style></head>
<body><div class='card'>
<h1>{escape(PRODUCT_FULL_NAME)}</h1><p class='muted'>{escape(TAGLINE)}</p>
<h2>Membership Request</h2>
<p><b>Name:</b> {name}<br><b>Email:</b> {email}<br><b>Requested tier:</b> {plan}</p>
<form method='post' action='/membership/decision'>
<input type='hidden' name='token' value='{safe_token}'>
<label>Approved/rejected by</label><input name='decided_by' placeholder='Kev or Mary' required>
<button class='approve' name='decision' value='approve'>Approve</button>
<button class='reject' name='decision' value='reject'>Reject</button>
</form></div></body></html>
""")


@router.post("/membership/decision", response_class=HTMLResponse)
def membership_decision(token: str = Form(...), decision: str = Form(...), decided_by: str = Form(...)):
    before = store.membership_request_from_token(token)
    if not before:
        raise HTTPException(404, "Membership request not found")
    try:
        user = store.decide_membership(token, decision, decided_by)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    approved = decision.lower() == "approve"
    plan_id = before["requested_plan_id"]
    payment = payment_instructions(plan_id) if approved else None
    notify_membership_decision(
        applicant_email=before["email"],
        display_name=before["display_name"],
        approved=approved,
        plan_id=plan_id,
        payment_url=(payment or {}).get("url"),
    )
    if approved and payment and payment.get("payment_required"):
        url = escape(payment.get("url") or "", quote=True)
        return HTMLResponse(
            f"<h2>Approved.</h2><p>{escape(before['display_name'])} has been sent the payment link.</p>"
            f"<p><a href='{url}'>Open {escape(plan_id.upper())} PayPal payment link</a></p>"
        )
    return HTMLResponse(f"<h2>Membership {escape('approved' if approved else 'rejected')}.</h2>")


@router.get("/membership/payment")
def current_payment(request: Request):
    token = session_token(request)
    try:
        member = memberships.from_session(token, require_active=False)
    except PermissionError as exc:
        raise HTTPException(401, str(exc)) from exc
    if member.user.get("status") != "approved_pending_payment":
        return {"payment_required": False, "status": member.user.get("status")}
    return payment_instructions(member.user["requested_plan_id"])


@router.get("/admin/membership/pending")
def pending_memberships(x_lss_admin_key: str | None = Header(default=None)):
    require_admin(x_lss_admin_key)
    return store.pending_requests()


@router.post("/admin/membership/activate-payment")
def activate_payment(payload: PaymentActivationRequest, x_lss_admin_key: str | None = Header(default=None)):
    require_admin(x_lss_admin_key)
    try:
        user = store.activate_paid_plan(payload.user_id, payload.plan_id, payload.payment_reference)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "activated": True,
        "user_id": user["id"],
        "plan_id": user["plan_id"],
        "billing_status": user["billing_status"],
        "payment_reference": payload.payment_reference,
    }
