from __future__ import annotations

from html import escape

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .branding import ENDORSEMENT, PRODUCT_FULL_NAME, TAGLINE
from .owner_auth import end_owner_session, owner_authorized, owner_key_matches, start_owner_session
from .owner_identity import clear_persona_cookie

router = APIRouter()


def _page(body: str) -> HTMLResponse:
    return HTMLResponse(f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1,viewport-fit=cover'><meta name='robots' content='noindex,nofollow'><title>ESP Owner Access — {escape(PRODUCT_FULL_NAME)}</title><style>
:root{{--bg:#07050c;--panel:#15101d;--line:#ffffff1d;--gold:#f4c873;--violet:#9c73ff;--muted:#c8bfd2;--bad:#ff94a6}}*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 50% 0,#301247,transparent 32%),#07050c;color:#fff;font-family:Inter,ui-sans-serif,system-ui,sans-serif;min-height:100vh;display:grid;place-items:center}}.card{{width:min(560px,calc(100% - 28px));border:1px solid var(--line);border-radius:24px;padding:27px;background:#15101def;box-shadow:0 30px 100px #0008}}.eyebrow{{color:var(--gold);font-size:.72rem;letter-spacing:.16em;text-transform:uppercase;font-weight:950}}h1{{font-size:clamp(2.2rem,7vw,3.8rem);letter-spacing:-.05em;line-height:.95;margin:.18em 0}}p{{color:var(--muted);line-height:1.55}}input{{width:100%;border:1px solid var(--line);border-radius:12px;padding:13px;background:#09070f;color:#fff;margin:9px 0 12px}}button{{width:100%;border:0;border-radius:12px;padding:12px;background:linear-gradient(110deg,var(--gold),var(--violet));color:#160b1d;font-weight:900;cursor:pointer}}.bad{{color:var(--bad);border:1px solid #ff94a644;border-radius:11px;padding:10px;background:#ff94a60b}}small{{color:var(--muted)}}
</style></head><body><main class='card'>{body}</main></body></html>""")


@router.get("/owner", response_class=HTMLResponse, include_in_schema=False)
def owner_login(request: Request, error: str = ""):
    if owner_authorized(request):
        return RedirectResponse("/owner/dashboard", status_code=303)
    error_html = f"<div class='bad'>{escape(error)}</div>" if error else ""
    return _page(
        f"""<div class='eyebrow'>Elevate Souls Productions · Owner Only</div><h1>Mary & Kev Command Centre</h1><p>Sign in to the protected owner administration for {escape(PRODUCT_FULL_NAME)}. After authentication, choose Mary or Kev so Aura and the audit trail use the correct owner context.</p>{error_html}<form method='post' action='/owner/login'><label>Owner administration key</label><input type='password' name='admin_key' required autocomplete='current-password'><button type='submit'>Enter Owner Command Centre</button></form><p><small>The administration key is used only to authenticate this login. A random opaque session token is stored in the browser afterward; the deployment key itself is not written into the new session cookie.</small></p><p><small>{escape(ENDORSEMENT)} · {escape(TAGLINE)}</small></p>"""
    )


@router.post("/owner/login", include_in_schema=False)
def owner_login_submit(admin_key: str = Form(...)):
    if not owner_key_matches(admin_key):
        return RedirectResponse("/owner?error=Incorrect+owner+key", status_code=303)
    response = RedirectResponse("/owner/dashboard", status_code=303)
    start_owner_session(response)
    clear_persona_cookie(response)
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
    return response
