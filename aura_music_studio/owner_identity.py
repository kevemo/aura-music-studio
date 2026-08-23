from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from html import escape
from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware

from .audit import AuditLedger
from .branding import PRODUCT_FULL_NAME

router = APIRouter()
ADMIN_COOKIE = "lss_admin_session"
OWNER_ACTOR_COOKIE = "lss_owner_actor"
DEFAULT_TTL_HOURS = 12

OWNER_ACTORS = {
    "kev": {
        "id": "kev",
        "display_name": "Kev",
        "audit_name": "Kev — ESP Co-Owner",
        "description": "ESP co-owner workspace",
    },
    "mary": {
        "id": "mary",
        "display_name": "Mary",
        "audit_name": "Mary — ESP Co-Owner",
        "description": "ESP co-owner workspace",
    },
}

AUDIT = AuditLedger()


def _admin_key() -> str:
    return (os.getenv("LSS_ADMIN_KEY") or "").strip()


def admin_authorized(request: Request) -> bool:
    configured = _admin_key()
    supplied = request.cookies.get(ADMIN_COOKIE) or ""
    return bool(configured and supplied and secrets.compare_digest(configured, supplied))


def _ttl_seconds() -> int:
    try:
        hours = int(os.getenv("LSS_OWNER_ACTOR_TTL_HOURS", str(DEFAULT_TTL_HOURS)))
    except ValueError:
        hours = DEFAULT_TTL_HOURS
    return max(1, min(hours, 24 * 7)) * 60 * 60


def _signing_secret() -> bytes:
    explicit = (os.getenv("LSS_OWNER_ACTOR_SECRET") or "").strip()
    source = explicit or _admin_key()
    if not source:
        return b""
    # Domain-separate actor signing even when the owner admin key is the deployment secret.
    return hashlib.sha256(("lss-owner-actor-v1:" + source).encode("utf-8")).digest()


def issue_actor_token(actor_id: str, *, issued_at: int | None = None) -> str:
    actor = OWNER_ACTORS.get((actor_id or "").strip().lower())
    if not actor:
        raise ValueError("Choose a valid ESP owner profile")
    key = _signing_secret()
    if not key:
        raise RuntimeError("Owner identity signing is unavailable until LSS_ADMIN_KEY is configured")
    issued = int(time.time() if issued_at is None else issued_at)
    payload = f"{actor['id']}|{issued}"
    signature = hmac.new(key, payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}|{signature}"


def verify_actor_token(token: str | None, *, now: int | None = None) -> dict | None:
    value = (token or "").strip()
    if not value:
        return None
    key = _signing_secret()
    if not key:
        return None
    parts = value.split("|")
    if len(parts) != 3:
        return None
    actor_id, issued_raw, supplied_signature = parts
    actor = OWNER_ACTORS.get(actor_id)
    if not actor:
        return None
    try:
        issued = int(issued_raw)
    except ValueError:
        return None
    current = int(time.time() if now is None else now)
    if issued > current + 300 or current - issued > _ttl_seconds():
        return None
    payload = f"{actor_id}|{issued}"
    expected = hmac.new(key, payload.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, supplied_signature):
        return None
    return {**actor, "issued_at": issued}


def actor_from_request(request: Request) -> dict | None:
    if not admin_authorized(request):
        return None
    return verify_actor_token(request.cookies.get(OWNER_ACTOR_COOKIE))


def set_actor_cookie(response: Response, actor_id: str) -> None:
    response.set_cookie(
        OWNER_ACTOR_COOKIE,
        issue_actor_token(actor_id),
        max_age=_ttl_seconds(),
        httponly=True,
        secure=(os.getenv("LSS_COOKIE_SECURE", "true").lower() == "true"),
        samesite="strict",
    )


def clear_actor_cookie(response: Response) -> None:
    response.delete_cookie(OWNER_ACTOR_COOKIE)


def _safe_next(value: str | None) -> str:
    target = (value or "/owner/dashboard").strip()
    if not target.startswith("/owner") or target.startswith("//"):
        return "/owner/dashboard"
    if target in {"/owner", "/owner/login", "/owner/profile"}:
        return "/owner/dashboard"
    return target


def _profile_page(current: dict | None, next_path: str) -> HTMLResponse:
    cards = []
    for actor in OWNER_ACTORS.values():
        active = bool(current and current["id"] == actor["id"])
        active_badge = "<span class='active'>ACTIVE</span>" if active else ""
        cards.append(
            f"""<form method='post' action='/owner/profile' class='profile {'selected' if active else ''}'>
            <input type='hidden' name='actor_id' value='{escape(actor['id'], quote=True)}'>
            <input type='hidden' name='next_path' value='{escape(next_path, quote=True)}'>
            <div class='avatar'>{escape(actor['display_name'][0])}</div>
            <div><h2>{escape(actor['display_name'])}</h2><p>{escape(actor['description'])}</p>{active_badge}</div>
            <button type='submit'>{'Continue as' if not active else 'Continue'} {escape(actor['display_name'])}</button></form>"""
        )
    return HTMLResponse(
        f"""<!doctype html><html><head><meta name='viewport' content='width=device-width,initial-scale=1'>
        <title>Select ESP Owner — {escape(PRODUCT_FULL_NAME)}</title><style>
        *{{box-sizing:border-box}}body{{margin:0;min-height:100vh;background:radial-gradient(circle at top,#261033,#08040c 58%);color:#fff;font-family:Inter,system-ui,sans-serif;display:grid;place-items:center;padding:24px}}
        .wrap{{width:min(920px,100%);text-align:center}}.brand{{color:#e8bd62;font-weight:950;letter-spacing:.08em}}h1{{font-size:clamp(2rem,5vw,3.5rem);margin:.3em 0}}.lead{{color:#cdbfd4;max-width:680px;margin:0 auto 28px;line-height:1.55}}
        .grid{{display:grid;grid-template-columns:1fr 1fr;gap:18px}}.profile{{text-align:left;background:linear-gradient(145deg,#1b1024,#100816);border:1px solid #52305f;border-radius:24px;padding:24px;color:#fff;box-shadow:0 20px 60px #0008}}
        .profile.selected{{border-color:#e8bd62;box-shadow:0 0 0 2px #e8bd6233,0 20px 60px #0008}}.avatar{{width:74px;height:74px;border-radius:50%;display:grid;place-items:center;background:linear-gradient(135deg,#e8bd62,#bc6ee1);color:#160b18;font-size:2rem;font-weight:950;float:left;margin:0 18px 16px 0}}
        .profile h2{{font-size:1.8rem;margin:6px 0}}.profile p{{color:#cdbfd4}}button{{width:100%;margin-top:18px;border:0;border-radius:13px;padding:13px 16px;background:linear-gradient(135deg,#f3d98f,#bd74e2);color:#160b18;font-weight:950;cursor:pointer}}.active{{font-size:.75rem;border:1px solid #e8bd62;color:#e8bd62;border-radius:999px;padding:5px 9px;font-weight:900}}
        .note{{margin-top:24px;color:#9f90a8;font-size:.88rem}}@media(max-width:680px){{.grid{{grid-template-columns:1fr}}}}
        </style></head><body><main class='wrap'><div class='brand'>ELEVATE SOULS PRODUCTIONS</div><h1>Who is using ESP?</h1><p class='lead'>Select the active co-owner. This does not change permissions; it records who is operating the shared owner workspace so sensitive actions are attributable in the ESP audit trail.</p><div class='grid'>{''.join(cards)}</div><p class='note'>Owner authorisation remains controlled by the separate secure owner session. A profile selection alone can never grant access.</p></main></body></html>"""
    )


@router.get("/owner/profile", response_class=HTMLResponse)
def owner_profile(request: Request, next: str | None = None):
    if not admin_authorized(request):
        return RedirectResponse("/owner", status_code=303)
    return _profile_page(actor_from_request(request), _safe_next(next))


@router.post("/owner/profile")
def owner_profile_select(
    request: Request,
    actor_id: str = Form(...),
    next_path: str = Form("/owner/dashboard"),
):
    if not admin_authorized(request):
        return RedirectResponse("/owner", status_code=303)
    actor_key = (actor_id or "").strip().lower()
    actor = OWNER_ACTORS.get(actor_key)
    if not actor:
        return HTMLResponse("Invalid ESP owner profile", status_code=400)
    previous = actor_from_request(request)
    response = RedirectResponse(_safe_next(next_path), status_code=303)
    set_actor_cookie(response, actor_key)
    AUDIT.append(
        actor=actor["audit_name"],
        action="owner_profile_selected",
        details={"actor_id": actor_key, "previous_actor_id": previous.get("id") if previous else None},
    )
    return response


def _audit_action(path: str) -> str:
    if path == "/owner/activate-payment":
        return "owner_payment_activation"
    if path == "/owner/public-address/refresh":
        return "owner_public_address_refresh"
    if path == "/owner/backups/schedule":
        return "owner_backup_schedule_update"
    if path == "/owner/backups/create":
        return "owner_backup_created"
    if path == "/owner/compute-nodes/enrollments":
        return "owner_compute_enrollment_created"
    if path.endswith("/revoke") and "/owner/compute-nodes/" in path:
        return "owner_compute_node_revoked"
    return "owner_write"


async def _inject_actor_badge(response: Response, actor: dict, path: str) -> Response:
    if "text/html" not in (response.headers.get("content-type") or "").lower() or not hasattr(response, "body_iterator"):
        return response
    chunks: list[bytes] = []
    async for chunk in response.body_iterator:
        chunks.append(chunk if isinstance(chunk, bytes) else str(chunk).encode("utf-8"))
    raw = b"".join(chunks)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return Response(raw, status_code=response.status_code, headers=dict(response.headers), media_type=response.headers.get("content-type"))
    if "esp-owner-actor-badge" not in text:
        next_value = quote(path, safe="/")
        badge = (
            "<a id='esp-owner-actor-badge' href='/owner/profile?next=" + next_value + "' title='Switch active ESP owner' "
            "style=\"position:fixed;right:16px;bottom:16px;z-index:2147482000;text-decoration:none;"
            "background:#120918eF;border:1px solid #e8bd62;color:#fff;padding:9px 13px;border-radius:999px;"
            "font:800 13px system-ui,sans-serif;box-shadow:0 10px 32px #0009;backdrop-filter:blur(10px)\">"
            "ESP Owner · " + escape(actor["display_name"]) + " ↔</a>"
        )
        text = text.replace("</body>", badge + "</body>", 1) if "</body>" in text else text + badge
    headers = {k: v for k, v in response.headers.items() if k.lower() not in {"content-length", "content-type"}}
    return HTMLResponse(text, status_code=response.status_code, headers=headers)


class OwnerIdentityMiddleware(BaseHTTPMiddleware):
    """Require and audit a signed Kev/Mary actor identity inside the shared owner session.

    The actor token is intentionally not an authentication credential. All owner routes still rely
    on the independent LSS_ADMIN_KEY session; this layer adds attribution and UI context only.
    """

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if not path.startswith("/owner"):
            return await call_next(request)

        excluded = path in {"/owner", "/owner/login", "/owner/profile", "/owner/logout"}
        actor = actor_from_request(request)
        if not excluded and admin_authorized(request) and not actor:
            target = quote(path + (("?" + request.url.query) if request.url.query else ""), safe="/=?&%")
            return RedirectResponse(f"/owner/profile?next={target}", status_code=303)

        if actor:
            request.state.owner_actor = actor

        if path == "/owner/logout" and actor and request.method == "POST":
            AUDIT.append(actor=actor["audit_name"], action="owner_logout", details={})

        response = await call_next(request)

        if path == "/owner/logout":
            clear_actor_cookie(response)
            return response

        if actor and request.method in {"POST", "PUT", "PATCH", "DELETE"} and path != "/owner/profile" and response.status_code < 400:
            AUDIT.append(
                actor=actor["audit_name"],
                action=_audit_action(path),
                details={"method": request.method, "path": path, "status_code": response.status_code},
            )
        if actor and path != "/owner/profile":
            response = await _inject_actor_badge(response, actor, path)
        return response
