from __future__ import annotations

import os
from html import escape

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .branding import ENDORSEMENT, PRODUCT_FULL_NAME, TAGLINE
from .owner_auth import end_owner_session, owner_authorized, owner_key_matches, start_owner_session
from .owner_identity import (
    OWNER_THEMES,
    clear_persona_cookie,
    request_owner_persona,
    set_persona_cookie,
)
from .owner_mfa import (
    OWNER_MFA_CHALLENGE_COOKIE,
    OWNER_MFA_CHALLENGE_MINUTES,
    owner_mfa_configured,
    owner_mfa_required,
    service as owner_mfa_service,
)

router = APIRouter()


def _page(body: str) -> HTMLResponse:
    response = HTMLResponse(f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1,viewport-fit=cover'><meta name='robots' content='noindex,nofollow'><title>ESP Owner Access — {escape(PRODUCT_FULL_NAME)}</title><style>
:root{{--bg:#07050c;--panel:#15101d;--line:#ffffff1d;--gold:#f4c873;--violet:#9c73ff;--muted:#c8bfd2;--bad:#ff94a6;--good:#82dca4}}*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 50% 0,#301247,transparent 32%),#07050c;color:#fff;font-family:Inter,ui-sans-serif,system-ui,sans-serif;min-height:100vh;display:grid;place-items:center}}.card{{width:min(560px,calc(100% - 28px));border:1px solid var(--line);border-radius:24px;padding:27px;background:#15101def;box-shadow:0 30px 100px #0008}}.eyebrow{{color:var(--gold);font-size:.72rem;letter-spacing:.16em;text-transform:uppercase;font-weight:950}}h1{{font-size:clamp(2.2rem,7vw,3.8rem);letter-spacing:-.05em;line-height:.95;margin:.18em 0}}p{{color:var(--muted);line-height:1.55}}label{{display:block;font-weight:800;margin-top:9px}}input,select{{width:100%;border:1px solid var(--line);border-radius:12px;padding:13px;background:#09070f;color:#fff;margin:9px 0 12px}}button{{width:100%;border:0;border-radius:12px;padding:12px;background:linear-gradient(110deg,var(--gold),var(--violet));color:#160b1d;font-weight:900;cursor:pointer}}.bad{{color:var(--bad);border:1px solid #ff94a644;border-radius:11px;padding:10px;background:#ff94a60b}}.good{{color:var(--good)}}small{{color:var(--muted)}}
</style></head><body><main class='card'>{body}</main></body></html>""")
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


def _mfa_cookie(response: HTMLResponse | RedirectResponse, token: str) -> None:
    response.set_cookie(
        OWNER_MFA_CHALLENGE_COOKIE,
        token,
        max_age=OWNER_MFA_CHALLENGE_MINUTES * 60,
        httponly=True,
        secure=(os.getenv("LSS_COOKIE_SECURE", "true").lower() == "true"),
        samesite="strict",
        path="/owner",
    )


def _clear_mfa_cookie(response: HTMLResponse | RedirectResponse) -> None:
    response.delete_cookie(OWNER_MFA_CHALLENGE_COOKIE, path="/owner")


def _mfa_page(persona: str, *, purpose: str, error: str = "") -> HTMLResponse:
    display = OWNER_THEMES.get(persona)
    owner_name = display.display_name if display else "Owner"
    error_html = f"<div class='bad'>{escape(error)}</div>" if error else ""
    if purpose == "switch":
        action = f"/owner/persona/{escape(persona, quote=True)}"
        heading = f"Verify {owner_name}"
        copy = f"Enter {owner_name}'s current six-digit authenticator code before switching the verified owner context."
        button = f"Switch to {owner_name}"
    else:
        action = "/owner/login"
        heading = f"Verify {owner_name}"
        copy = f"The owner administration key was accepted. Enter {owner_name}'s current six-digit authenticator code to create the owner session."
        button = "Complete owner sign in"
    return _page(
        f"""<div class='eyebrow'>Second-factor verification</div><h1>{escape(heading)}</h1><p>{escape(copy)}</p>{error_html}<form method='post' action='{action}'><input type='hidden' name='persona' value='{escape(persona, quote=True)}'><label>Authenticator code</label><input inputmode='numeric' pattern='[0-9]{{6}}' maxlength='6' name='totp_code' required autocomplete='one-time-code' autofocus><button type='submit'>{escape(button)}</button></form><p><small>The challenge expires after {OWNER_MFA_CHALLENGE_MINUTES} minutes and cannot be reused. Authentication secrets are stored only in deployment configuration, never in this website database.</small></p>"""
    )


@router.get("/owner", response_class=HTMLResponse, include_in_schema=False)
def owner_login(request: Request, error: str = ""):
    if owner_authorized(request):
        return RedirectResponse("/owner/dashboard", status_code=303)
    error_html = f"<div class='bad'>{escape(error)}</div>" if error else ""
    if owner_mfa_required():
        options = "".join(
            f"<option value='{key}'>{escape(theme.display_name)}</option>"
            for key, theme in OWNER_THEMES.items()
        )
        mfa_note = (
            "<p><small>Two-factor owner verification is required. The administration key is the first factor; Mary and Kev each use their own authenticator secret as the second factor.</small></p>"
        )
        persona_field = f"<label>Who is signing in?</label><select name='persona' required>{options}</select>"
    else:
        mfa_note = "<p><small>Owner MFA is not required in this environment. Production should enable the separate Mary/Kev second-factor policy.</small></p>"
        persona_field = ""
    return _page(
        f"""<div class='eyebrow'>Elevate Souls Productions · Owner Only</div><h1>Mary & Kev Command Centre</h1><p>Sign in to the protected owner administration for {escape(PRODUCT_FULL_NAME)}. Owner identity controls Aura context and owner audit attribution.</p>{error_html}<form method='post' action='/owner/login'>{persona_field}<label>Owner administration key</label><input type='password' name='admin_key' required autocomplete='current-password'><button type='submit'>Continue securely</button></form>{mfa_note}<p><small>The administration key is used only to authenticate this login. A random opaque session token is stored in the browser afterward; the deployment key itself is not written into a new session cookie.</small></p><p><small>{escape(ENDORSEMENT)} · {escape(TAGLINE)}</small></p>"""
    )


@router.post("/owner/login", include_in_schema=False)
def owner_login_submit(
    request: Request,
    admin_key: str = Form(""),
    persona: str = Form(""),
    totp_code: str = Form(""),
):
    # MFA verification phase. The first factor is deliberately not resubmitted or stored.
    if totp_code:
        if not owner_mfa_required():
            return RedirectResponse("/owner", status_code=303)
        challenge_token = request.cookies.get(OWNER_MFA_CHALLENGE_COOKIE) or ""
        expected = (persona or "").strip().lower()
        try:
            verified_persona = owner_mfa_service().verify_challenge(
                challenge_token,
                totp_code,
                expected_purpose="login",
                expected_persona=expected if expected in OWNER_THEMES else None,
            )
        except (RuntimeError, ValueError) as exc:
            response = _mfa_page(expected if expected in OWNER_THEMES else "kev", purpose="login", error=str(exc))
            if not owner_mfa_service().challenge(challenge_token):
                _clear_mfa_cookie(response)
            return response

        response = RedirectResponse("/owner/dashboard", status_code=303)
        start_owner_session(response)
        set_persona_cookie(response, verified_persona)  # type: ignore[arg-type]
        _clear_mfa_cookie(response)
        return response

    # First-factor phase.
    if not owner_key_matches(admin_key):
        return RedirectResponse("/owner?error=Incorrect+owner+key", status_code=303)

    if owner_mfa_required():
        selected = (persona or "").strip().lower()
        if selected not in OWNER_THEMES:
            return RedirectResponse("/owner?error=Choose+Mary+or+Kev", status_code=303)
        if not owner_mfa_configured():
            return RedirectResponse("/owner?error=Owner+MFA+is+required+but+not+fully+configured", status_code=303)
        try:
            challenge = owner_mfa_service().create_challenge(selected, purpose="login")
        except (RuntimeError, ValueError) as exc:
            return _page(f"<div class='eyebrow'>Owner security</div><h1>Owner sign in blocked</h1><div class='bad'>{escape(str(exc))}</div><p><a href='/owner' style='color:var(--gold)'>Return to owner sign in</a></p>")
        response = _mfa_page(selected, purpose="login")
        _mfa_cookie(response, challenge)
        # Crucially, no owner session exists until the TOTP phase succeeds.
        clear_persona_cookie(response)
        return response

    response = RedirectResponse("/owner/dashboard", status_code=303)
    start_owner_session(response)
    clear_persona_cookie(response)
    return response


@router.post("/owner/persona/{persona}", include_in_schema=False)
def switch_owner_persona(
    persona: str,
    request: Request,
    totp_code: str = Form(""),
):
    if not owner_authorized(request):
        return RedirectResponse("/owner", status_code=303)
    target = (persona or "").strip().lower()
    if target not in OWNER_THEMES:
        return RedirectResponse("/owner/dashboard", status_code=303)

    if not owner_mfa_required():
        response = RedirectResponse("/owner/dashboard", status_code=303)
        set_persona_cookie(response, target)  # type: ignore[arg-type]
        return response

    current = request_owner_persona(request)
    if current == target:
        return RedirectResponse("/owner/dashboard", status_code=303)
    if not owner_mfa_configured():
        return _page("<div class='eyebrow'>Owner security</div><h1>Owner switch blocked</h1><div class='bad'>Owner MFA is required but not fully configured.</div><p><a href='/owner/dashboard' style='color:var(--gold)'>Return to dashboard</a></p>")

    if not totp_code:
        try:
            challenge = owner_mfa_service().create_challenge(target, purpose="switch")
        except (RuntimeError, ValueError) as exc:
            return _page(f"<div class='eyebrow'>Owner security</div><h1>Owner switch blocked</h1><div class='bad'>{escape(str(exc))}</div><p><a href='/owner/dashboard' style='color:var(--gold)'>Return to dashboard</a></p>")
        response = _mfa_page(target, purpose="switch")
        _mfa_cookie(response, challenge)
        return response

    challenge_token = request.cookies.get(OWNER_MFA_CHALLENGE_COOKIE) or ""
    try:
        owner_mfa_service().verify_challenge(
            challenge_token,
            totp_code,
            expected_purpose="switch",
            expected_persona=target,
        )
    except (RuntimeError, ValueError) as exc:
        response = _mfa_page(target, purpose="switch", error=str(exc))
        if not owner_mfa_service().challenge(challenge_token):
            _clear_mfa_cookie(response)
        return response

    response = RedirectResponse("/owner/dashboard", status_code=303)
    set_persona_cookie(response, target)  # type: ignore[arg-type]
    _clear_mfa_cookie(response)
    return response


@router.post("/owner/logout", include_in_schema=False)
def owner_logout(request: Request):
    response = RedirectResponse("/owner", status_code=303)
    # Capture/revoke the opaque token before the compatibility layer modifies the
    # in-process legacy cookie view. It is preserved on request.state when needed.
    opaque = getattr(request.state, "owner_opaque_session", None)
    if opaque:
        original = request.cookies.get("lss_admin_session")
        request.cookies["lss_admin_session"] = opaque
        try:
            end_owner_session(request, response)
        finally:
            if original is not None:
                request.cookies["lss_admin_session"] = original
    else:
        end_owner_session(request, response)
    clear_persona_cookie(response)
    _clear_mfa_cookie(response)
    return response
