from __future__ import annotations

from html import escape
from typing import Any

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from .accounts import AccountStore
from .aura_sec_approval import AuraSecApprovalGateway
from .aura_sec_passkeys import AuraSecPasskeyService
from .aura_sec_protocol import ActionRisk
from .aura_sec_store import AuraSecStore


router = APIRouter(tags=["Aura Sec Passkeys"])
accounts = AccountStore()
security = AuraSecStore(accounts)
passkeys = AuraSecPasskeyService(accounts, security)
approvals = AuraSecApprovalGateway(accounts, security, passkeys)
MEMBER_COOKIE = "lss_session"
MASTER_PRODUCT_NAME = "Elevate Souls Productions Content Creation Command Center"
MASTER_ENDORSEMENT = "Powered by Aura AI"


class PasskeyRegistrationStart(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    password: str = Field(min_length=1, max_length=1024)
    label: str = Field(default="Passkey", min_length=1, max_length=80)


class PasskeyRegistrationComplete(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    ceremony_id: str = Field(min_length=16, max_length=128)
    label: str = Field(default="Passkey", min_length=1, max_length=80)
    credential_response: dict[str, Any]


class PasskeyActionComplete(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    ceremony_id: str = Field(min_length=16, max_length=128)
    credential_response: dict[str, Any]


def _session_context(request: Request) -> tuple[dict, str]:
    token = request.cookies.get(MEMBER_COOKIE)
    user = accounts.resolve_session(token)
    if not user or not token:
        raise HTTPException(401, "Sign in required")
    return user, token


def _secure_html(content: str, *, status_code: int = 200) -> HTMLResponse:
    response = HTMLResponse(content, status_code=status_code)
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'; "
        "img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'"
    )
    return response


def _page(title: str, body: str, *, script: bool = False) -> HTMLResponse:
    script_tag = "<script src='/aura-sec/passkey-client.js' defer></script>" if script else ""
    return _secure_html(
        f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='noindex,nofollow'><title>{escape(title)} · Aura Sec</title><style>
:root{{--bg:#02060a;--panel:#0b161f;--line:#ffffff1d;--text:#f7fbff;--muted:#a4b4c2;--cyan:#69efff;--gold:#f5cc78;--danger:#ff8799}}*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 10% 0,#123b54,transparent 31%),radial-gradient(circle at 92% 0,#381a53,transparent 28%),var(--bg);color:var(--text);font-family:Inter,ui-sans-serif,system-ui,sans-serif}}a{{color:inherit}}.wrap{{width:min(900px,calc(100% - 28px));margin:auto;padding:28px 0 64px}}.brand{{font-weight:950;text-decoration:none}}.brand small{{display:block;color:var(--gold);font-size:.68rem;letter-spacing:.1em;text-transform:uppercase;margin-top:5px}}.panel,.truth{{border:1px solid var(--line);border-radius:20px;padding:22px;background:linear-gradient(145deg,#101d27e8,#071017ec);margin-top:22px}}.eyebrow{{font-size:.7rem;letter-spacing:.13em;text-transform:uppercase;color:var(--cyan);font-weight:950}}h1{{font-size:clamp(2.1rem,6vw,4.1rem);line-height:.98;letter-spacing:-.05em;margin:.2em 0}}h2{{margin:5px 0 14px}}p,.meta{{color:var(--muted);line-height:1.6}}.meta{{font-size:.82rem}}label{{display:block;font-size:.8rem;font-weight:850;margin:15px 0 7px}}input{{width:100%;border:1px solid var(--line);border-radius:12px;background:#050b10;color:#fff;padding:12px;font:inherit}}button,.button{{display:inline-flex;border:0;border-radius:12px;padding:11px 15px;margin-top:17px;background:linear-gradient(105deg,var(--cyan),#b9a7ff,var(--gold));color:#071016;font-weight:950;cursor:pointer;text-decoration:none}}.secondary{{background:#ffffff08;color:#fff;border:1px solid var(--line);margin-left:8px}}.truth{{border-color:#ffd47740;background:#ffd47708}}.truth b{{color:var(--gold)}}.credential{{border-top:1px solid var(--line);padding:14px 0}}.credential:first-child{{border-top:0}}.status{{min-height:1.6em;margin-top:12px;color:var(--gold)}}
</style>{script_tag}</head><body><main class='wrap'><a class='brand' href='/aura-sec'>{escape(MASTER_PRODUCT_NAME)}<small>{escape(MASTER_ENDORSEMENT)}</small></a>{body}</main></body></html>"""
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


@router.get("/aura-sec/passkey-client.js", include_in_schema=False)
def passkey_client_js():
    javascript = r"""
const AuraPasskey = (() => {
  const b64uToBytes = (value) => {
    const pad = '='.repeat((4 - value.length % 4) % 4);
    const raw = atob((value + pad).replace(/-/g, '+').replace(/_/g, '/'));
    return Uint8Array.from(raw, c => c.charCodeAt(0));
  };
  const bytesToB64u = (value) => {
    if (value === null || value === undefined) return null;
    const bytes = new Uint8Array(value);
    let raw = '';
    bytes.forEach(b => raw += String.fromCharCode(b));
    return btoa(raw).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/g, '');
  };
  const creationOptions = (options) => {
    options.challenge = b64uToBytes(options.challenge);
    options.user.id = b64uToBytes(options.user.id);
    (options.excludeCredentials || []).forEach(item => item.id = b64uToBytes(item.id));
    return options;
  };
  const requestOptions = (options) => {
    options.challenge = b64uToBytes(options.challenge);
    (options.allowCredentials || []).forEach(item => item.id = b64uToBytes(item.id));
    return options;
  };
  const serialize = (credential) => {
    const response = credential.response;
    const output = {
      id: credential.id,
      rawId: bytesToB64u(credential.rawId),
      type: credential.type,
      authenticatorAttachment: credential.authenticatorAttachment || null,
      response: { clientDataJSON: bytesToB64u(response.clientDataJSON) }
    };
    if (response.attestationObject) output.response.attestationObject = bytesToB64u(response.attestationObject);
    if (response.authenticatorData) output.response.authenticatorData = bytesToB64u(response.authenticatorData);
    if (response.signature) output.response.signature = bytesToB64u(response.signature);
    if (response.userHandle) output.response.userHandle = bytesToB64u(response.userHandle);
    if (typeof response.getTransports === 'function') output.response.transports = response.getTransports();
    return output;
  };
  const post = async (url, payload) => {
    const response = await fetch(url, {
      method: 'POST',
      credentials: 'same-origin',
      headers: {'Content-Type': 'application/json', 'Accept': 'application/json'},
      body: JSON.stringify(payload)
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || 'Aura Sec passkey request failed');
    return data;
  };
  const message = (id, text) => { const el = document.getElementById(id); if (el) el.textContent = text; };

  const enrol = async (form) => {
    const password = form.querySelector('[name=password]').value;
    const label = form.querySelector('[name=label]').value;
    message('passkey-status', 'Preparing secure passkey enrolment…');
    const begin = await post('/aura-sec/passkeys/register/options', {password, label});
    const credential = await navigator.credentials.create({publicKey: creationOptions(begin.public_key)});
    if (!credential) throw new Error('Passkey enrolment was cancelled');
    await post('/aura-sec/passkeys/register/complete', {
      ceremony_id: begin.ceremony_id,
      label,
      credential_response: serialize(credential)
    });
    message('passkey-status', 'Passkey enrolled. Reloading credential list…');
    location.reload();
  };

  const verifyAction = async (button) => {
    const actionId = button.dataset.actionId;
    const approvalToken = button.dataset.approvalToken;
    message('passkey-status', 'Requesting action-bound passkey verification…');
    const begin = await post(`/aura-sec/approval/${encodeURIComponent(actionId)}/passkey/options`, {});
    const credential = await navigator.credentials.get({publicKey: requestOptions(begin.public_key)});
    if (!credential) throw new Error('Passkey verification was cancelled');
    const verified = await post(`/aura-sec/approval/${encodeURIComponent(actionId)}/passkey/complete`, {
      ceremony_id: begin.ceremony_id,
      credential_response: serialize(credential)
    });
    const form = document.getElementById('passkey-approval-submit');
    form.querySelector('[name=approval_token]').value = approvalToken;
    form.querySelector('[name=strong_reauth_evidence_id]').value = verified.evidence_id;
    form.submit();
  };

  document.addEventListener('DOMContentLoaded', () => {
    const enrolForm = document.getElementById('passkey-enrol-form');
    if (enrolForm) enrolForm.addEventListener('submit', event => {
      event.preventDefault();
      enrol(enrolForm).catch(error => message('passkey-status', error.message));
    });
    const actionButton = document.getElementById('passkey-action-button');
    if (actionButton) actionButton.addEventListener('click', () => {
      actionButton.disabled = true;
      verifyAction(actionButton)
        .catch(error => { message('passkey-status', error.message); actionButton.disabled = false; });
    });
  });
  return {};
})();
"""
    response = Response(javascript, media_type="application/javascript")
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@router.get("/aura-sec/passkeys", response_class=HTMLResponse, include_in_schema=False)
def passkeys_page(request: Request):
    try:
        user, _session_token = _session_context(request)
    except HTTPException:
        return RedirectResponse("/signin?next=/aura-sec/passkeys", status_code=303)
    credentials = passkeys.list_credentials(user["id"])
    rows = "".join(
        f"<div class='credential'><b>{escape(item['label'])}</b><div class='meta'>Type {escape(str(item.get('device_type') or 'unknown'))} · Backed up {'Yes' if item.get('backed_up') else 'No'} · Enrolled {escape(str(item['created_at']))}</div></div>"
        for item in credentials
    ) or "<p>No Aura Sec passkey is enrolled yet.</p>"
    body = f"""<div class='eyebrow'>High-risk authentication</div><h1>Passkeys</h1><section class='panel'><h2>Enrolled authenticators</h2>{rows}</section><section class='panel'><h2>Enrol a passkey</h2><p>Enrolment requires your current account password first. Your private key and biometric data stay with your authenticator and are never sent to Aura Sec.</p><form id='passkey-enrol-form'><label for='label'>Passkey label</label><input id='label' name='label' maxlength='80' value='My passkey' autocomplete='off'><label for='password'>Current account password</label><input id='password' name='password' type='password' autocomplete='current-password' required><button type='submit'>Enrol passkey</button></form><div id='passkey-status' class='status' aria-live='polite'></div></section><section class='truth'><b>Downgrade protection.</b> Once a passkey is enrolled, Aura Sec high-risk actions require passkey verification and cannot fall back to password-only approval. Passkey verification authorizes only the approval phase; it never directly executes an endpoint command.</section>"""
    return _page("Passkeys", body, script=True)


@router.post("/aura-sec/passkeys/register/options")
def passkey_registration_options(payload: PasskeyRegistrationStart, request: Request):
    user, session_token = _session_context(request)
    try:
        return passkeys.begin_registration(
            user["id"],
            session_token=session_token,
            password=payload.password,
            label=payload.label,
        )
    except (PermissionError, RuntimeError) as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/aura-sec/passkeys/register/complete")
def passkey_registration_complete(payload: PasskeyRegistrationComplete, request: Request):
    user, session_token = _session_context(request)
    try:
        return passkeys.complete_registration(
            user["id"],
            session_token=session_token,
            ceremony_id=payload.ceremony_id,
            credential_response=payload.credential_response,
            label=payload.label,
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/aura-sec/approval/{action_id}/passkey/options")
def passkey_action_options(action_id: str, request: Request):
    user, session_token = _session_context(request)
    try:
        return passkeys.begin_action_verification(
            user["id"],
            action_id,
            session_token=session_token,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except (PermissionError, RuntimeError) as exc:
        raise HTTPException(403, str(exc)) from exc


@router.post("/aura-sec/approval/{action_id}/passkey/complete")
def passkey_action_complete(action_id: str, payload: PasskeyActionComplete, request: Request):
    user, session_token = _session_context(request)
    try:
        return passkeys.complete_action_verification(
            user["id"],
            action_id,
            session_token=session_token,
            ceremony_id=payload.ceremony_id,
            credential_response=payload.credential_response,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


# These two POST routes intentionally precede the legacy approval portal routes when the
# router is mounted. GET review remains on the existing portal, while start/confirm gain
# passkey-aware high-risk handling without creating any native execution endpoint.
@router.post("/aura-sec/approval/{action_id}/start", response_class=HTMLResponse, include_in_schema=False)
def approval_start_passkey_aware(action_id: str, request: Request):
    try:
        user, session_token = _session_context(request)
    except HTTPException:
        return RedirectResponse(f"/signin?next=/aura-sec/approval/{action_id}", status_code=303)
    _action_for_review(user["id"], action_id)
    try:
        challenge = approvals.create_challenge(user["id"], action_id, session_token=session_token)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc

    if challenge.get("passkey_required"):
        body = f"""<div class='eyebrow'>Passkey step-up</div><h1>Verify High-Risk Approval</h1><section class='panel'><h2>{escape(challenge['risk_class'].replace('_',' ').title())}</h2><p>A passkey is enrolled on this account, so password downgrade is disabled. Your authenticator must verify you for this exact action.</p><button id='passkey-action-button' type='button' data-action-id='{escape(action_id, quote=True)}' data-approval-token='{escape(challenge['approval_token'], quote=True)}'>Verify with passkey</button><div id='passkey-status' class='status' aria-live='polite'></div><form id='passkey-approval-submit' method='post' action='/aura-sec/approval/{escape(action_id)}/confirm'><input type='hidden' name='approval_token'><input type='hidden' name='strong_reauth_evidence_id'></form></section><section class='truth'><b>Passkey proof is action-bound.</b> A successful assertion creates one short-lived evidence record for this login session and this exact action. The final approval still does not issue a native command.</section>"""
        return _page("Passkey Security Approval", body, script=True)

    password = (
        "<label for='password'>Re-enter your account password</label><input id='password' name='password' type='password' autocomplete='current-password' required>"
        if challenge["strong_reauthentication_required"]
        else ""
    )
    body = f"""<div class='eyebrow'>One-time confirmation</div><h1>Confirm Approval</h1><section class='panel'><h2>{escape(challenge['risk_class'].replace('_',' ').title())}</h2><p>This challenge expires at {escape(challenge['expires_at'])} and is bound to this login session and this one action.</p><form method='post' action='/aura-sec/approval/{escape(action_id)}/confirm'><input type='hidden' name='approval_token' value='{escape(challenge['approval_token'], quote=True)}'>{password}<button type='submit'>Approve this bounded action</button><a class='button secondary' href='/aura-sec/approvals'>Cancel</a></form></section><section class='truth'><b>Approval is not execution.</b> Confirmation can only move this exact action to approved. It cannot send a shell command, and no endpoint command is issued by this browser form.</section>"""
    return _page("Confirm Security Approval", body)


@router.post("/aura-sec/approval/{action_id}/confirm", response_class=HTMLResponse, include_in_schema=False)
def approval_confirm_passkey_aware(
    action_id: str,
    request: Request,
    approval_token: str = Form(...),
    password: str = Form(""),
    strong_reauth_evidence_id: str = Form(""),
):
    try:
        user, session_token = _session_context(request)
    except HTTPException:
        return RedirectResponse(f"/signin?next=/aura-sec/approval/{action_id}", status_code=303)
    try:
        result = approvals.approve(
            user["id"],
            action_id,
            session_token=session_token,
            approval_token=approval_token,
            password=password or None,
            strong_reauth_evidence_id=strong_reauth_evidence_id or None,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    method = escape(str(result.get("strong_reauthentication_method") or "explicit confirmation"))
    body = f"""<div class='eyebrow'>Approval recorded</div><h1>Action Approved</h1><section class='panel'><h2>{escape(result['action']['action_type'].replace('_',' ').title())}</h2><p>The member approval phase is complete.</p><div class='meta'>Action status: {escape(result['action']['status'])} · Re-authentication: {method}</div><a class='button' href='/aura-sec/approvals'>Return to approvals</a></section><section class='truth'><b>No command was issued by this form.</b> {escape(result['truth'])}</section>"""
    return _page("Security Action Approved", body)


__all__ = ["router"]
