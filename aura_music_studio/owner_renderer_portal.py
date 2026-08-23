from __future__ import annotations

import os
import secrets
from html import escape

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .branding import PRODUCT_FULL_NAME
from .renderer_runtime import renderer_runtime_status

router = APIRouter()
ADMIN_COOKIE = "lss_admin_session"

CSS = """
body{font-family:system-ui,sans-serif;background:#08040c;color:#fff;margin:0}.wrap{max-width:1100px;margin:auto;padding:28px}.card{background:#170d20;border:1px solid #563364;border-radius:18px;padding:20px;margin:16px 0}.row{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}.muted{color:#cdbfd4}.gold{color:#f0ca72}.good{color:#72dfa0}.bad{color:#ff91a1}.warn{color:#ffd07a}.pill{border:1px solid #6f456f;border-radius:999px;padding:5px 9px}.btn{background:#e7b953;color:#160b18;border:0;border-radius:10px;padding:10px 14px;font-weight:800;text-decoration:none;display:inline-block}code,pre{word-break:break-word;color:#ffe29a;white-space:pre-wrap}pre{background:#0b0610;padding:13px;border-radius:11px;border:1px solid #45264e}@media(max-width:760px){.grid{grid-template-columns:1fr}}
"""


def _authorized(request: Request) -> bool:
    configured = os.getenv("LSS_ADMIN_KEY") or ""
    supplied = request.cookies.get(ADMIN_COOKIE) or ""
    return bool(configured and supplied and secrets.compare_digest(configured, supplied))


def _page(body: str) -> HTMLResponse:
    return HTMLResponse(
        f"<!doctype html><html><head><meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>Renderer Control — {escape(PRODUCT_FULL_NAME)}</title><style>{CSS}</style></head>"
        f"<body><div class='wrap'>{body}</div></body></html>"
    )


def _engine_card(engine: dict) -> str:
    reachable = bool(engine.get("reachable"))
    state = "LIVE" if reachable else "OFFLINE"
    cls = "good" if reachable else "bad"
    model = engine.get("full_song_model") or engine.get("stage1_model") or "configured by deployment"
    role = engine.get("role") or ("Primary full-song / upload-conditioned renderer" if engine.get("primary") else "Optional renderer")
    extra = ""
    if engine.get("id") == "yue":
        extra = f"<p class='muted'>Max segments: {escape(str(engine.get('max_segments') or 'deployment default'))} · Heavy lyrics-first path</p>"
    reported = engine.get("models_reported") or []
    if reported:
        extra += "<p class='muted'><b>Worker models:</b> " + escape(", ".join(map(str, reported[:8]))) + "</p>"
    return (
        f"<div class='card'><div class='row'><h2>{escape(str(engine.get('id') or 'renderer'))}</h2>"
        f"<span class='pill {cls}'>{state}</span></div><p>{escape(str(role))}</p>"
        f"<p><b>Configured model:</b> {escape(str(model))}</p>{extra}"
        f"<p class='muted'>Private service URL and credentials are intentionally not shown here.</p></div>"
    )


@router.get("/owner/renderers", response_class=HTMLResponse)
def owner_renderers(request: Request):
    if not _authorized(request):
        return RedirectResponse("/owner", status_code=303)
    status = renderer_runtime_status()
    ready = bool(status.get("final_master_renderer_ready"))
    self_hosted = bool(status.get("self_hosted_ready"))
    primary = status.get("active_primary") or "none"
    cards = "".join(_engine_card(item) for item in status.get("engines") or [])
    ace_command = "python scripts/start_renderers.py --mode ace"
    both_command = "python scripts/start_renderers.py --mode both"
    smoke_command = "docker compose -f docker-compose.yml -f docker-compose.gpu.yml exec live-sound-studio aura renderer-smoke"
    body = f"""
<div class='row'><div><div class='gold'><b>ESP LIVE RENDERER CONTROL</b></div><h1>Aura Neural Music Engines</h1><p class='muted'>Truthful readiness for the real waveform engines that may create Final Masters.</p></div><a class='btn' href='/owner/dashboard'>← Owner dashboard</a></div>
<div class='grid'>
  <div class='card'><b>Final Master renderer</b><h2 class='{'good' if ready else 'bad'}'>{'READY' if ready else 'NOT READY'}</h2></div>
  <div class='card'><b>Self-hosted renderer</b><h2 class='{'good' if self_hosted else 'warn'}'>{'LIVE' if self_hosted else 'OFFLINE'}</h2></div>
  <div class='card'><b>Active primary</b><h2>{escape(str(primary))}</h2></div>
</div>
{cards}
<div class='card'><h2>Start on the ESP GPU host</h2><p class='muted'>For security, the browser is never given the Docker socket. Run one of these commands on the ESP-controlled GPU computer.</p><p><b>ACE-Step primary:</b></p><pre><code>{escape(ace_command)}</code></pre><p><b>ACE-Step + YuE:</b></p><pre><code>{escape(both_command)}</code></pre></div>
<div class='card'><h2>Prove real generation after startup</h2><p class='muted'>This short smoke render calls the private ACE-Step REST API and then independently decodes the returned waveform. A health response alone is not accepted as proof.</p><pre><code>{escape(smoke_command)}</code></pre></div>
<div class='card'><h2>Security boundary</h2><p class='muted'>ACE-Step/YuE have no public host ports in the normal ESP topology. The customer app, queue worker and enrolled ESP compute nodes are the only systems intended to request generation. Symbolic control files cannot satisfy Final Master validation.</p></div>
"""
    return _page(body)
