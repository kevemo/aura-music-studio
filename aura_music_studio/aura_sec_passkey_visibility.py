from __future__ import annotations

from html import escape
from urllib.parse import parse_qs

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from .accounts import AccountStore
from .aura_sec_browser_guard import BrowserGuardDecision, evaluate_url
from .aura_sec_reputation_status import browser_guard_cloud_status


_PASSKEY_PATH = "/aura-sec/passkeys"
_BROWSER_GUARD_PATH = "/aura-sec/browser-guard"
_BROWSER_GUARD_STATUS_PATH = "/api/aura-sec/member/browser-guard/status"
_SETTINGS_PATH = "/aura-sec/settings"
_SETTINGS_LINK = "href='/aura-sec/settings'>Security Settings</a>"
_PASSKEY_LABEL = "Passkeys &amp; High-Risk Auth"
_BROWSER_GUARD_LABEL = "Browser Guard"
_MEMBER_COOKIE = "lss_session"
_MAX_INSPECTION_FORM_BYTES = 12_000

accounts = AccountStore()


def _member(request: Request) -> dict | None:
    token = request.cookies.get(_MEMBER_COOKIE)
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
    return accounts.resolve_session(token)


def _inject_navigation(html: str) -> str:
    marker_index = html.find(_SETTINGS_LINK)
    if marker_index < 0:
        return html

    anchor_start = html.rfind("<a", 0, marker_index)
    if anchor_start < 0:
        return html

    links = ""
    if f"href='{_BROWSER_GUARD_PATH}'" not in html:
        links += f"<a class='navitem' href='{_BROWSER_GUARD_PATH}'>{_BROWSER_GUARD_LABEL}</a>"
    if f"href='{_PASSKEY_PATH}'" not in html:
        links += f"<a class='navitem' href='{_PASSKEY_PATH}'>{_PASSKEY_LABEL}</a>"
    if not links:
        return html
    return html[:anchor_start] + links + html[anchor_start:]


def _inject_settings_policy(html: str) -> str:
    if "id='aura-sec-passkey-policy'" in html:
        return html

    footer = "<div class='footer'>"
    footer_index = html.find(footer)
    if footer_index < 0:
        return html

    policy = """
<section id='aura-sec-passkey-policy' class='section panel'>
  <div class='eyebrow'>Passkeys &amp; High-Risk Auth</div>
  <h2>WebAuthn step-up is enforced for destructive actions</h2>
  <p>Passkeys use the browser and operating-system authenticator with server-verified WebAuthn public-key challenge-response. Aura Sec never receives the authenticator private key or biometric data.</p>
  <div class='checklist'>
    <div class='check'><b>✓</b><span>Passkey enrolment requires an authenticated member session plus current-password re-authentication.</span></div>
    <div class='check'><b>✓</b><span>High-risk verification is bound to the exact member, login session, Aura Sec action, relying-party origin and short-lived ceremony.</span></div>
    <div class='check'><b>✓</b><span>Once a passkey exists, high-risk approval cannot downgrade to password-only authorization.</span></div>
    <div class='check'><b>✓</b><span>Successful WebAuthn verification creates one-time action-bound evidence; browser approval still cannot issue or execute a native endpoint command.</span></div>
  </div>
  <div class='actions'><a class='action primary' href='/aura-sec/passkeys'>Manage passkeys</a><a class='action' href='/aura-sec/approvals'>Review pending approvals</a></div>
</section>
<section id='aura-sec-browser-guard-policy' class='section panel'>
  <div class='eyebrow'>Browser Guard</div>
  <h2>Local-first link inspection is available now</h2>
  <p>Aura Sec can safely parse and assess a pasted web, email or QR-decoded link without making a server-side request to that destination. Cloud provider readiness is reported separately and does not make the manual inspector perform a network lookup. Automatic navigation protection remains unavailable until the signed Browser Guard extension is released.</p>
  <div class='actions'><a class='action primary' href='/aura-sec/browser-guard'>Inspect a link</a><a class='action' href='/aura-sec/downloads'>Extension release status</a></div>
</section>
"""
    return html[:footer_index] + policy + html[footer_index:]


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


def _browser_guard_status(user_id: str) -> dict:
    cloud = browser_guard_cloud_status()
    return {
        "user_id": user_id,
        **cloud,
        # Compatibility fields retained for existing clients. "Configured" here means at least
        # one provider is runtime-ready; it never means the manual inspector performs a lookup.
        "cloud_reputation_configured": cloud["cloud_reputation_active"],
        "manual_inspection_uses_cloud_reputation": False,
        "extension_state": "not_released",
        "truth": (
            f"{cloud['truth']} The manual Browser Guard inspector remains local-only and does not query those providers. "
            "Automatic browser protection requires a separately signed extension release and is not active yet."
        ),
    }


def _provider_readiness_panel(cloud: dict) -> str:
    providers = cloud.get("providers") or []
    rows = "".join(
        "<li><b>"
        + escape(str(item.get("display_name") or item.get("provider_id") or "Provider"))
        + "</b> — "
        + escape(str(item.get("state") or "unknown").replace("_", " ").title())
        + "</li>"
        for item in providers
    )
    if not rows:
        rows = "<li>No reputation providers are registered.</li>"
    state_label = str(cloud.get("cloud_reputation_state") or "inactive").replace("_", " ").title()
    return f"""
<section class='panel'>
  <div class='eyebrow'>Cloud reputation readiness</div>
  <h2>{escape(state_label)}</h2>
  <p>{escape(str(cloud.get('truth') or 'Cloud reputation readiness is unavailable.'))}</p>
  <p><b>Ready providers:</b> {int(cloud.get('provider_count_ready') or 0)} / {int(cloud.get('provider_count_known') or 0)} · <b>Independent corroboration:</b> {'Ready' if cloud.get('independent_corroboration_ready') else 'Not ready'}</p>
  <ul>{rows}</ul>
  <p class='truth'>Provider status is read-only. This page cannot enable a feed, supply credentials, change commercial approval, or execute a provider lookup. Credential names, values and raw provider responses are not exposed.</p>
</section>
"""


def _decision_panel(decision: BrowserGuardDecision | None, error: str | None) -> str:
    if error:
        return (
            "<section class='result danger'><div class='eyebrow'>Inspection rejected</div>"
            f"<h2>{escape(error)}</h2><p>The destination was not contacted.</p></section>"
        )
    if decision is None:
        return ""

    tone = {"allow": "good", "warn": "warn", "block": "danger"}.get(decision.verdict, "warn")
    signals = "".join(
        f"<li><b>{escape(signal.code.replace('_', ' ').title())}</b> — {escape(signal.detail)}</li>"
        for signal in decision.signals
    ) or "<li>No local structural warning signals were detected.</li>"
    removed = ", ".join(escape(item) for item in decision.tracking_parameters_removed) or "None"
    normalized = escape(decision.normalized_url or "Not applicable")
    clean = escape(decision.privacy_clean_url or decision.normalized_url or "Not applicable")
    hard_block = escape(decision.hard_block_reason or "None")
    return f"""
<section class='result {tone}'>
  <div class='eyebrow'>Local inspection result</div>
  <h2>{escape(decision.verdict.upper())} · risk score {int(decision.risk_score)}/100</h2>
  <p><b>Normalized destination:</b> <code>{normalized}</code></p>
  <p><b>Privacy-clean destination:</b> <code>{clean}</code></p>
  <p><b>Hard-block basis:</b> {hard_block}</p>
  <p><b>Tracking parameters removed:</b> {removed}</p>
  <ul>{signals}</ul>
  <p class='truth'>This result uses local URL structure only. No cloud reputation claim is being made and the server did not visit the submitted destination.</p>
</section>
"""


def _browser_guard_page(*, decision: BrowserGuardDecision | None = None, error: str | None = None) -> HTMLResponse:
    result = _decision_panel(decision, error)
    cloud = browser_guard_cloud_status()
    cloud_state = str(cloud.get("cloud_reputation_state") or "inactive").replace("_", " ").title()
    readiness = _provider_readiness_panel(cloud)
    html = f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='noindex,nofollow'><title>Aura Sec Browser Guard</title><style>
:root{{--bg:#05080d;--panel:#0d151e;--line:#ffffff20;--text:#f7fbff;--muted:#aab7c4;--cyan:#70efff;--gold:#f6d58c;--good:#78e6a6;--warn:#ffd479;--danger:#ff8f9b}}*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 12% 0,#0d4264,transparent 34%),radial-gradient(circle at 92% 5%,#39205f,transparent 30%),var(--bg);color:var(--text);font-family:Inter,system-ui,sans-serif}}a{{color:inherit}}.wrap{{width:min(980px,calc(100% - 28px));margin:auto;padding:28px 0 70px}}.top{{display:flex;justify-content:space-between;gap:12px;align-items:center;flex-wrap:wrap}}.brand{{font-weight:950}}.brand small{{display:block;color:var(--gold);text-transform:uppercase;letter-spacing:.09em;font-size:.65rem}}.back{{text-decoration:none;border:1px solid var(--line);border-radius:12px;padding:9px 12px}}.hero{{padding:55px 0 20px}}.eyebrow{{color:var(--cyan);font-size:.72rem;text-transform:uppercase;letter-spacing:.14em;font-weight:900}}h1{{font-size:clamp(2.8rem,8vw,5.5rem);line-height:.92;letter-spacing:-.055em;margin:.12em 0 .22em}}h2{{margin:.25em 0 .45em}}p,li{{color:var(--muted);line-height:1.6}}.panel,.result{{border:1px solid var(--line);border-radius:20px;background:#ffffff08;padding:22px;margin-top:18px}}form{{display:grid;gap:12px}}label{{font-weight:850}}input,select{{width:100%;border:1px solid var(--line);background:#050a10;color:#fff;border-radius:12px;padding:13px 14px;font:inherit}}button{{border:0;border-radius:12px;padding:13px 16px;font-weight:950;background:linear-gradient(100deg,var(--cyan),#b7a8ff,var(--gold));color:#061015;cursor:pointer}}.truth{{border:1px solid #ffd47955;background:#ffd4790d;padding:13px;border-radius:12px;color:#f9ddb0}}.good{{border-color:#78e6a655}}.warn{{border-color:#ffd47966}}.danger{{border-color:#ff8f9b66}}code{{word-break:break-all;color:#dffaff}}ul{{padding-left:21px}}.status{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-top:18px}}.status div{{border:1px solid var(--line);border-radius:14px;padding:14px;background:#ffffff06}}.status b{{display:block}}.status span{{color:var(--muted);font-size:.8rem}}@media(max-width:700px){{.status{{grid-template-columns:1fr}}}}
</style></head><body><main class='wrap'><div class='top'><div class='brand'>Elevate Souls Productions Content Creation Command Center<small>Powered by Aura AI · Aura Sec</small></div><a class='back' href='/aura-sec'>← Security Center</a></div><section class='hero'><div class='eyebrow'>Browser Guard · Local-first inspector</div><h1>Inspect the link, <span>not the destination.</span></h1><p>Paste a suspicious web, email or QR-decoded link. Aura Sec parses the text locally on the server and does not browse, fetch, resolve or contact the submitted destination.</p></section><div class='status'><div><b>Manual inspection</b><span>Available · local-only</span></div><div><b>Cloud provider readiness</b><span>{escape(cloud_state)}</span></div><div><b>Automatic extension protection</b><span>Not released</span></div></div><section class='panel'><form method='post' action='/aura-sec/browser-guard' autocomplete='off'><label for='url'>Link to inspect</label><input id='url' name='url' type='text' maxlength='8192' placeholder='https://example.com/path' required autocomplete='off' spellcheck='false'><label for='source'>Where did you receive it?</label><select id='source' name='source'><option value='link'>Web link</option><option value='email'>Email</option><option value='qr'>QR code</option><option value='navigation'>Browser navigation</option><option value='download_redirect'>Download redirect</option></select><button type='submit'>Inspect safely</button><p class='truth'>Do not paste passwords, authentication tokens or other secrets. Embedded URL credentials are redacted from displayed results. This manual inspector does not query cloud providers.</p></form></section>{result}{readiness}<section class='panel'><div class='eyebrow'>Release truth</div><h2>The extension is not released yet</h2><p>The Browser Guard analysis engine exists, but Chrome, Edge and Firefox-family automatic protection will remain locked until signed extension packages pass the Aura Sec release manifest, provenance and security gates.</p><a href='/aura-sec/downloads'>View protection download status</a></section></main></body></html>"""
    return _secure_html(html)


class AuraSecPasskeyVisibilityMiddleware(BaseHTTPMiddleware):
    """Expose validated Aura Sec passkey and Browser Guard member experiences.

    The middleware can render the local-only Browser Guard inspector and sanitized provider
    readiness, but it never makes a network request to the inspected destination and does not
    activate provider, extension or native protection. Passkey ceremonies and approval trust
    remain in their dedicated services.
    """

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        method = request.method.upper()

        if path == _BROWSER_GUARD_STATUS_PATH and method == "GET":
            user = _member(request)
            if not user:
                return JSONResponse({"detail": "Sign in required"}, status_code=401)
            response = JSONResponse(_browser_guard_status(user["id"]))
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response

        if path == _BROWSER_GUARD_PATH and method in {"GET", "POST"}:
            user = _member(request)
            if not user:
                return RedirectResponse("/signin?next=/aura-sec/browser-guard", status_code=303)
            if method == "GET":
                return _browser_guard_page()

            body = await request.body()
            if len(body) > _MAX_INSPECTION_FORM_BYTES:
                return _browser_guard_page(error="Inspection request is too large")
            try:
                form = parse_qs(body.decode("utf-8"), keep_blank_values=True)
            except UnicodeDecodeError:
                return _browser_guard_page(error="Inspection request encoding is invalid")
            raw_url = (form.get("url") or [""])[0]
            source = (form.get("source") or ["link"])[0]
            try:
                decision = evaluate_url(raw_url, source=source)
            except (ValueError, PermissionError) as exc:
                return _browser_guard_page(error=str(exc))
            return _browser_guard_page(decision=decision)

        response = await call_next(request)

        if method != "GET" or not path.startswith("/aura-sec"):
            return response
        if path.endswith(".js"):
            return response
        if "text/html" not in (response.headers.get("content-type") or "").lower():
            return response

        body = b"".join([chunk async for chunk in response.body_iterator])
        try:
            html = body.decode("utf-8")
        except UnicodeDecodeError:
            return Response(
                content=body,
                status_code=response.status_code,
                headers={k: v for k, v in response.headers.items() if k.lower() != "content-length"},
                media_type=response.media_type,
                background=response.background,
            )

        html = _inject_navigation(html)
        if path == _SETTINGS_PATH:
            html = _inject_settings_policy(html)

        headers = {k: v for k, v in response.headers.items() if k.lower() != "content-length"}
        return Response(
            content=html.encode("utf-8"),
            status_code=response.status_code,
            headers=headers,
            media_type="text/html",
            background=response.background,
        )


__all__ = ["AuraSecPasskeyVisibilityMiddleware"]
