from __future__ import annotations

from html import escape

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .branding import PRODUCT_FULL_NAME
from .owner_auth import owner_authorized
from .owner_identity import set_persona_cookie
from .protected_data_auth import (
    ProtectedPersona,
    end_protected_session,
    protected_data_authorized,
    protected_key_configured,
    protected_key_matches,
    record_step_up_denial,
    start_protected_session,
)

router = APIRouter()


def _page(body: str, *, status_code: int = 200) -> HTMLResponse:
    response = HTMLResponse(
        f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Protected Data — {escape(PRODUCT_FULL_NAME)}</title><style>
*{{box-sizing:border-box}}body{{margin:0;background:#09050d;color:#fff;font-family:Inter,system-ui,sans-serif}}.wrap{{max-width:780px;margin:auto;padding:24px}}.card{{background:#150b1d;border:1px solid #50305d;border-radius:18px;padding:22px;margin:18px 0}}.muted{{color:#cdbfd4;line-height:1.55}}.warn{{color:#ffd07a}}.good{{color:#86e0a8}}.bad{{color:#ff9aa9}}label{{display:block;margin:14px 0}}input,select{{width:100%;padding:11px;border-radius:9px;border:1px solid #523263;background:#09050d;color:#fff}}button,.btn{{display:inline-block;border:1px solid #553363;border-radius:10px;background:#22122d;color:#fff;padding:10px 14px;text-decoration:none;font-weight:850;cursor:pointer}}.primary{{background:linear-gradient(135deg,#ffe7a6,#e8ba59,#b67a23);color:#160b18}}
</style></head><body><div class='wrap'>{body}</div></body></html>""",
        status_code=status_code,
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return response


@router.get("/owner/protected-data", response_class=HTMLResponse)
def protected_data_dashboard(request: Request):
    if not owner_authorized(request):
        return RedirectResponse("/owner", status_code=303)

    if protected_data_authorized(request):
        return _page(
            """<div class='card'><h1>Protected Data authority active</h1>
<p class='good'>The current Owner session has a short-lived, persona-bound Protected Data step-up.</p>
<p class='muted'>This authority is separate from Owner/Admin access and expires automatically. It is required for Recovery Vault backup settings, backup creation and backup downloads.</p>
<a class='btn primary' href='/owner/backups'>Open Recovery Vault</a>
<form method='post' action='/owner/protected-data/lock' style='margin-top:16px'><button type='submit'>Lock Protected Data</button></form></div>"""
        )

    mary_state = "configured" if protected_key_configured("mary") else "not configured"
    kev_state = "configured" if protected_key_configured("kev") else "not configured"
    return _page(
        f"""<div class='card'><h1>Protected Data step-up</h1>
<p class='muted'>Recovery Vault access requires a valid Owner session plus a separate Mary/Kev protected credential. Owner status alone never bypasses this boundary.</p>
<p class='muted'>Mary credential: <b>{escape(mary_state)}</b><br>Kev credential: <b>{escape(kev_state)}</b></p>
<form method='post' action='/owner/protected-data/unlock' autocomplete='off'>
<label>Protected persona<select name='persona' required><option value='kev'>Kev</option><option value='mary'>Mary</option></select></label>
<label>Protected credential<input type='password' name='protected_key' autocomplete='current-password' required></label>
<button class='primary' type='submit'>Unlock Protected Data</button></form>
<p class='warn'>No default protected credentials exist. Missing deployment configuration fails closed.</p>
<a class='btn' href='/owner/dashboard'>Back to Owner Dashboard</a></div>"""
    )


@router.post("/owner/protected-data/unlock")
def unlock_protected_data(
    request: Request,
    persona: str = Form(...),
    protected_key: str = Form(...),
):
    if not owner_authorized(request):
        return RedirectResponse("/owner", status_code=303)

    selected: ProtectedPersona | None = persona if persona in {"mary", "kev"} else None  # type: ignore[assignment]
    if selected is None or not protected_key_matches(selected, protected_key):
        record_step_up_denial(selected)
        return _page(
            """<div class='card'><h1>Protected Data locked</h1><p class='bad'>The protected credential was not accepted.</p><p class='muted'>No protected session was created. Repeated failures should be reviewed in the Protected Data audit ledger.</p><a class='btn' href='/owner/protected-data'>Try again</a></div>""",
            status_code=403,
        )

    response = RedirectResponse("/owner/backups", status_code=303)
    set_persona_cookie(response, selected)
    start_protected_session(response, selected)
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/owner/protected-data/lock")
def lock_protected_data(request: Request):
    response = RedirectResponse("/owner/dashboard", status_code=303)
    end_protected_session(request, response)
    response.headers["Cache-Control"] = "no-store"
    return response


__all__ = ["router"]
