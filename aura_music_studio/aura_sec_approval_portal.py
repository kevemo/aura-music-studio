from __future__ import annotations

from html import escape

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .accounts import AccountStore
from .aura_sec_approval import AuraSecApprovalGateway
from .aura_sec_protocol import ActionRisk
from .aura_sec_store import AuraSecStore

router = APIRouter(tags=["Aura Sec Approval Portal"])
accounts = AccountStore()
security = AuraSecStore(accounts)
approvals = AuraSecApprovalGateway(accounts, security)
MEMBER_COOKIE = "lss_session"

MASTER_PRODUCT_NAME = "Elevate Souls Productions Content Creation Command Center"
MASTER_ENDORSEMENT = "Powered by Aura AI"


def _session_context(request: Request):
    token = request.cookies.get(MEMBER_COOKIE)
    user = accounts.resolve_session(token)
    if not user or not token:
        return None, None
    return user, token


def _secure_html(html: str, *, status_code: int = 200) -> HTMLResponse:
    response = HTMLResponse(html, status_code=status_code)
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'; "
        "img-src 'self' data:; style-src 'self' 'unsafe-inline'"
    )
    return response


def _page(title: str, body: str) -> HTMLResponse:
    return _secure_html(
        f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='noindex,nofollow'><title>{escape(title)} · Aura Sec</title><style>
:root{{--bg:#02060a;--panel:#0b161f;--line:#ffffff1d;--text:#f7fbff;--muted:#a4b4c2;--cyan:#69efff;--gold:#f5cc78;--violet:#b18cff;--danger:#ff8799}}*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 10% 0,#123b54,transparent 31%),radial-gradient(circle at 92% 0,#381a53,transparent 28%),var(--bg);color:var(--text);font-family:Inter,ui-sans-serif,system-ui,sans-serif}}a{{color:inherit}}.wrap{{width:min(820px,calc(100% - 28px));margin:auto;padding:28px 0 64px}}.brand{{font-weight:950;text-decoration:none}}.brand small{{display:block;color:var(--gold);font-size:.68rem;letter-spacing:.1em;text-transform:uppercase;margin-top:5px}}.panel,.truth{{border:1px solid var(--line);border-radius:20px;padding:22px;background:linear-gradient(145deg,#101d27e8,#071017ec);margin-top:22px}}.eyebrow{{font-size:.7rem;letter-spacing:.13em;text-transform:uppercase;color:var(--cyan);font-weight:950}}h1{{font-size:clamp(2.2rem,6vw,4.4rem);line-height:.96;letter-spacing:-.05em;margin:.2em 0}}h2{{margin:5px 0 14px}}p,.meta{{color:var(--muted);line-height:1.6}}.meta{{font-size:.8rem}}.risk{{display:inline-flex;border:1px solid #ff87994b;color:var(--danger);border-radius:999px;padding:6px 10px;font-size:.7rem;font-weight:900;text-transform:uppercase}}label{{display:block;font-size:.8rem;font-weight:850;margin:15px 0 7px}}input{{width:100%;border:1px solid var(--line);border-radius:12px;background:#050b10;color:#fff;padding:12px;font:inherit}}button,.button{{display:inline-flex;border:0;border-radius:12px;padding:11px 15px;margin-top:17px;background:linear-gradient(105deg,var(--cyan),#b9a7ff,var(--gold));color:#071016;font-weight:950;cursor:pointer;text-decoration:none}}.secondary{{background:#ffffff08;color:#fff;border:1px solid var(--line);margin-left:8px}}.truth{{border-color:#ffd47740;background:#ffd47708}}.truth b{{color:var(--gold)}}
</style></head><body><main class='wrap'><a class='brand' href='/aura-sec/approvals'>{escape(MASTER_PRODUCT_NAME)}<small>{escape(MASTER_ENDORSEMENT)}</small></a>{body}</main></body></html>"""
    )


def _action_for_review(user_id: str, action_id: str) -> dict:
    try:
        action = security.get_action(user_id, action_id)
    except ValueError as exc:
        raise HTTPException(404, "Aura Sec action not found") from exc
    if action.get("status") != "proposed":
        raise HTTPException(409, "Only proposed Aura Sec actions can be reviewed for approval")
    if action.get("risk_class") not in {
        ActionRisk.CONFIRMATION_REQUIRED.value,
        ActionRisk.STRONG_REAUTH_REQUIRED.value,
    }:
        raise HTTPException(403, "This action does not use the member approval workflow")
    return action


@router.get("/aura-sec/approval/{action_id}", response_class=HTMLResponse, include_in_schema=False)
def approval_review_page(action_id: str, request: Request):
    user, _session_token = _session_context(request)
    if not user:
        return RedirectResponse(f"/signin?next=/aura-sec/approval/{action_id}", status_code=303)
    action = _action_for_review(user["id"], action_id)
    strong = action["risk_class"] == ActionRisk.STRONG_REAUTH_REQUIRED.value
    details = action.get("details") or {}
    summary = str(details.get("summary") or "No additional human-readable explanation was supplied.")[:1000]
    body = f"""<div class='eyebrow'>Security action review</div><h1>{escape(action['action_type'].replace('_',' ').title())}</h1><span class='risk'>{escape(action['risk_class'].replace('_',' ').title())}</span><section class='panel'><h2>Review before approving</h2><p>{escape(summary)}</p><div class='meta'>Action ID {escape(action['id'])} · Device {escape(action['device_id'])}</div><p>{'This high-risk action will require your account password again on the confirmation step.' if strong else 'This action requires explicit confirmation using a one-time session-bound approval challenge.'}</p><form method='post' action='/aura-sec/approval/{escape(action_id)}/start'><button type='submit'>Begin secure approval</button><a class='button secondary' href='/aura-sec/approvals'>Cancel</a></form></section><section class='truth'><b>Nothing executes from this page.</b> Starting review creates a short-lived approval challenge only. Even after approval, a separate signed native-device session must request and execute the bounded command.</section>"""
    return _page("Review Security Action", body)


@router.post("/aura-sec/approval/{action_id}/start", response_class=HTMLResponse, include_in_schema=False)
def approval_start(action_id: str, request: Request):
    user, session_token = _session_context(request)
    if not user or not session_token:
        return RedirectResponse(f"/signin?next=/aura-sec/approval/{action_id}", status_code=303)
    _action_for_review(user["id"], action_id)
    try:
        challenge = approvals.create_challenge(user["id"], action_id, session_token=session_token)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    password = (
        "<label for='password'>Re-enter your account password</label><input id='password' name='password' type='password' autocomplete='current-password' required>"
        if challenge["strong_reauthentication_required"]
        else ""
    )
    body = f"""<div class='eyebrow'>One-time confirmation</div><h1>Confirm Approval</h1><section class='panel'><h2>{escape(challenge['risk_class'].replace('_',' ').title())}</h2><p>This challenge expires at {escape(challenge['expires_at'])} and is bound to this login session and this one action.</p><form method='post' action='/aura-sec/approval/{escape(action_id)}/confirm'><input type='hidden' name='approval_token' value='{escape(challenge['approval_token'], quote=True)}'>{password}<button type='submit'>Approve this bounded action</button><a class='button secondary' href='/aura-sec/approvals'>Cancel</a></form></section><section class='truth'><b>Approval is not execution.</b> Confirmation can only move this exact action to approved. It cannot send a shell command, and no endpoint command is issued by this browser form.</section>"""
    return _page("Confirm Security Approval", body)


@router.post("/aura-sec/approval/{action_id}/confirm", response_class=HTMLResponse, include_in_schema=False)
def approval_confirm(
    action_id: str,
    request: Request,
    approval_token: str = Form(...),
    password: str = Form(""),
):
    user, session_token = _session_context(request)
    if not user or not session_token:
        return RedirectResponse(f"/signin?next=/aura-sec/approval/{action_id}", status_code=303)
    try:
        result = approvals.approve(
            user["id"],
            action_id,
            session_token=session_token,
            approval_token=approval_token,
            password=password or None,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    body = f"""<div class='eyebrow'>Approval recorded</div><h1>Action Approved</h1><section class='panel'><h2>{escape(result['action']['action_type'].replace('_',' ').title())}</h2><p>The member approval phase is complete.</p><div class='meta'>Action status: {escape(result['action']['status'])}</div><a class='button' href='/aura-sec/approvals'>Return to approvals</a></section><section class='truth'><b>No command was issued by this form.</b> {escape(result['truth'])}</section>"""
    return _page("Security Action Approved", body)


__all__ = ["router"]
