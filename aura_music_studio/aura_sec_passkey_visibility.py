from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


_PASSKEY_PATH = "/aura-sec/passkeys"
_SETTINGS_PATH = "/aura-sec/settings"
_SETTINGS_LINK = "href='/aura-sec/settings'>Security Settings</a>"
_PASSKEY_LABEL = "Passkeys &amp; High-Risk Auth"


def _inject_navigation(html: str) -> str:
    if f"href='{_PASSKEY_PATH}'" in html:
        return html

    marker_index = html.find(_SETTINGS_LINK)
    if marker_index < 0:
        return html

    anchor_start = html.rfind("<a", 0, marker_index)
    if anchor_start < 0:
        return html

    passkey_link = (
        f"<a class='navitem' href='{_PASSKEY_PATH}'>{_PASSKEY_LABEL}</a>"
    )
    return html[:anchor_start] + passkey_link + html[anchor_start:]


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
"""
    return html[:footer_index] + policy + html[footer_index:]


class AuraSecPasskeyVisibilityMiddleware(BaseHTTPMiddleware):
    """Expose the validated Aura Sec passkey workflow in existing member UI.

    The middleware is intentionally presentation-only. It does not create WebAuthn
    ceremonies, authenticate members, approve actions, or issue native commands. Those
    trust decisions remain inside the dedicated passkey and approval services.
    """

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)

        if request.method.upper() != "GET" or not request.url.path.startswith("/aura-sec"):
            return response
        if request.url.path.endswith(".js"):
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
        if request.url.path == _SETTINGS_PATH:
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
