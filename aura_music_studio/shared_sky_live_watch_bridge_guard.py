from __future__ import annotations

import re

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from .shared_sky_live_watch_ui_v2 import watch_page_v2


router = APIRouter(tags=["Shared Sky Watch Browser Bridge Guard"])

_BOOTSTRAP_MODE = '"browser_authorization_mode":"cookie_bootstrap_redirect"'
_NATIVE_HLS_HOOK = "if(path.endsWith('.m3u8')&&!nativeHlsSupported(v))"
_NATIVE_HLS_REPLACEMENT = (
    "const bootstrapHls=descriptor.browser_authorization_mode==='cookie_bootstrap_redirect';"
    "if((bootstrapHls||path.endsWith('.m3u8'))&&!nativeHlsSupported(v))"
)
_QUALITY_ADD = "if(u)q.add(new Option(r.name||'Rendition',u))"
_QUALITY_ADD_REPLACEMENT = (
    "if(u&&(d.browser_authorization_mode!=='cookie_bootstrap_redirect'||"
    "new URL(u,location.origin).origin===location.origin))"
    "q.add(new Option(r.name||'Rendition',u))"
)


def _harden_cookie_bootstrap_html(html: str) -> str:
    """Keep Chat 2's cookie bootstrap fail-closed on browsers without native HLS.

    Chat 2 owns token minting, cookie creation, redirects and media serving. This guard only fixes
    the viewer-layer capability decision: a token-free ``/bootstrap`` URL is still an HLS source
    after redirect, so it must not be eagerly assigned to native video before the browser proves
    native HLS support. No bearer, manifest construction or transport mutation occurs here.
    """

    if _BOOTSTRAP_MODE not in html:
        return html

    hardened, source_edits = re.subn(
        r"(<video id='video' controls playsinline preload='metadata') src='[^']*'",
        r"\1 hidden",
        html,
        count=1,
    )
    if source_edits != 1:
        # Contract drift: fail closed by stripping any first video source rather than allowing an
        # unreviewed bootstrap to auto-load.
        hardened = re.sub(
            r"(<video id='video'[^>]*?)\s+src='[^']*'",
            r"\1 hidden",
            hardened,
            count=1,
        )

    # A cookie-scoped media session must not cause eager cross-origin caption fetches.
    hardened = re.sub(r"<track\s+kind='captions'[^>]*>", "", hardened)

    if _NATIVE_HLS_HOOK in hardened:
        hardened = hardened.replace(_NATIVE_HLS_HOOK, _NATIVE_HLS_REPLACEMENT, 1)
    else:
        # If the expected player hook drifts, suppress initial descriptor application entirely.
        # The page remains usable for chat/community and a future reviewed player can restore media.
        hardened = hardened.replace(
            "applyDescriptor(descriptor);history();join();events();refreshInteractives();",
            "setStatus('Shared Sky playback update required','The browser playback contract changed; media is being held fail-closed.');history();join();events();refreshInteractives();",
            1,
        )

    if _QUALITY_ADD in hardened:
        hardened = hardened.replace(_QUALITY_ADD, _QUALITY_ADD_REPLACEMENT, 1)

    return hardened


@router.get("/watch/{broadcast_id}", response_class=HTMLResponse, include_in_schema=False)
def watch_page_bridge_guard(broadcast_id: str, request: Request):
    response = watch_page_v2(broadcast_id, request)
    if response.status_code != 200:
        return response
    try:
        html = response.body.decode("utf-8")
    except Exception:
        return response
    hardened = _harden_cookie_bootstrap_html(html)
    if hardened == html:
        return response
    headers = dict(response.headers)
    headers.pop("content-length", None)
    headers["Cache-Control"] = "no-store"
    return HTMLResponse(
        hardened,
        status_code=response.status_code,
        headers=headers,
    )


__all__ = ["router", "watch_page_bridge_guard", "_harden_cookie_bootstrap_html"]
