from __future__ import annotations

import secrets
from html import escape
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from . import shared_sky_live_community as live
from .esp_niche import require_esp_hub_member
from .owner_identity import owner_session_authorized
from .shared_sky_live_moderator_permissions import ModeratorPermissionService

router = APIRouter(tags=["Shared Skies Rhiannon LIVE Assist"])

_ACTIVE_LIVE_STATES = {"live", "degraded", "reconnecting"}


def guardian_projection(broadcast: dict[str, Any], *, effective_moderators: int = 0) -> dict[str, Any]:
    """Return a bounded Rhiannon LIVE Guardian readiness projection.

    Guardian is deliberately advisory/read-only at this boundary. It consumes Shared Skies LIVE
    moderation truth but cannot grant permissions, assign moderators, mutate chat/Q&A, stop
    transport, score Battles, send Gifts or perform provider writes. Chat 1 remains the Rhiannon
    intelligence authority; this module only provides a safe LIVE context contract.
    """

    state = str(broadcast.get("state") or "unknown").strip().lower()
    active = state in _ACTIVE_LIVE_STATES
    return {
        "product": "Rhiannon LIVE Guardian",
        "broadcast_id": str(broadcast.get("id") or ""),
        "broadcast_state": state,
        "live_active": active,
        "shared_skies_moderation_available": True,
        "effective_assigned_moderators": max(0, int(effective_moderators)),
        "assistant_authority": "read_only_advisory",
        "rhiannon_authority": "Chat 1 Rhiannon intelligence authority",
        "provider_moderation_write": {
            "ready": False,
            "reason_code": "provider_moderation_authorisation_not_evidenced",
            "message": (
                "No external provider moderation write is enabled by Guardian itself. "
                "A separately authorised provider adapter is required."
            ),
        },
        "permission_model": {
            "agent_role_alone_grants_moderation": False,
            "delegated_moderator_requires_owner_enabled_permission": True,
            "delegated_moderator_requires_live_assignment": True,
        },
        "advisory_capabilities": [
            "surface_moderation_queue",
            "summarise_bounded_report_context",
            "suggest_timeout_or_comment_removal",
            "suggest_report_escalation",
            "suggest_stream_review_flag",
        ],
        "prohibited_authority": [
            "grant_moderator_permission",
            "assign_live_moderator",
            "execute_provider_moderation_write",
            "start_or_stop_transport",
            "mutate_battle_score",
            "mutate_coin_or_gift_finance",
            "execute_arbitrary_command",
        ],
    }


def auto_cue_html(nonce: str) -> str:
    nonce_attr = escape(nonce, quote=True)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Shared Skies Auto Cue</title>
<style>
:root{{--bg:#04080f;--panel:#0b1622;--line:#ffffff22;--text:#f7fbff;--muted:#aab8c7;--accent:#7ce7d7}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font-family:Inter,system-ui,sans-serif}}
.wrap{{max-width:1200px;margin:auto;padding:18px}}.toolbar{{display:flex;gap:8px;flex-wrap:wrap;align-items:center;background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:12px;position:sticky;top:0;z-index:3}}
button,input,textarea{{font:inherit}}button{{background:#12283a;color:var(--text);border:1px solid var(--line);padding:8px 11px;border-radius:9px;font-weight:700}}label{{font-size:.85rem;color:var(--muted)}}input[type=range]{{vertical-align:middle}}textarea{{width:100%;min-height:180px;margin:12px 0;background:#07111c;color:var(--text);border:1px solid var(--line);border-radius:12px;padding:14px;resize:vertical}}
.stage{{height:62vh;overflow:hidden;border:1px solid var(--line);border-radius:16px;background:#000;position:relative}}.script{{padding:38vh 8vw 45vh;white-space:pre-wrap;line-height:1.45;font-size:48px;text-align:center;transform-origin:center top}}.stage.mirror .script{{transform:scaleX(-1)}}
.marker{{position:absolute;left:0;right:0;top:44%;border-top:1px dashed #7ce7d788;pointer-events:none}}.countdown{{position:absolute;inset:0;display:none;place-items:center;background:#000e;font-size:min(30vw,220px);font-weight:900;z-index:4}}.note{{color:var(--muted);font-size:.9rem}}
:focus-visible{{outline:3px solid var(--accent);outline-offset:2px}}@media(max-width:700px){{.script{{font-size:34px;padding-left:5vw;padding-right:5vw}}}}
</style></head><body><main class="wrap">
<h1>Private Auto Cue</h1><p class="note">Your script stays in this browser page. Shared Skies does not upload or persist the text.</p>
<div class="toolbar">
<button id="start">Start</button><button id="pause">Pause</button><button id="reset">Reset</button><button id="count">3s Countdown</button>
<label>Speed <input id="speed" type="range" min="10" max="220" value="65"> <span id="speedOut">65</span> px/s</label>
<label>Text <input id="size" type="range" min="24" max="96" value="48"> <span id="sizeOut">48</span> px</label>
<button id="mirror">Mirror</button><button id="full">Fullscreen</button><button id="second">Second screen</button><button id="clear">Clear</button>
</div>
<textarea id="editor" autocomplete="off" spellcheck="true" placeholder="Paste or type your private cue script here…"></textarea>
<section id="stage" class="stage" aria-label="Auto Cue prompter"><div class="marker"></div><div id="script" class="script"></div><div id="countdown" class="countdown"></div></section>
</main>
<script nonce="{nonce_attr}">
(()=>{{
'use strict';
const editor=document.getElementById('editor'),stage=document.getElementById('stage'),script=document.getElementById('script');
const speed=document.getElementById('speed'),size=document.getElementById('size'),speedOut=document.getElementById('speedOut'),sizeOut=document.getElementById('sizeOut');
const countdown=document.getElementById('countdown');let running=false,last=0,raf=0;
function sync(){{script.textContent=editor.value}}
function tick(ts){{if(!running)return;if(!last)last=ts;const dt=Math.min(100,ts-last);last=ts;stage.scrollTop+=Number(speed.value)*dt/1000;if(stage.scrollTop>=stage.scrollHeight-stage.clientHeight)running=false;if(running)raf=requestAnimationFrame(tick)}}
function start(){{sync();if(running)return;running=true;last=0;raf=requestAnimationFrame(tick)}}
function pause(){{running=false;last=0;if(raf)cancelAnimationFrame(raf)}}
function reset(){{pause();stage.scrollTop=0}}
async function countStart(){{pause();countdown.style.display='grid';for(let n=3;n>0;n--){{countdown.textContent=String(n);await new Promise(r=>setTimeout(r,1000))}}countdown.style.display='none';start()}}
function secondScreen(){{sync();const w=window.open('','sharedSkiesAutoCueSecond','popup,width=1000,height=700');if(!w)return;w.document.title='Shared Skies Auto Cue — Second Screen';w.document.body.replaceChildren();w.document.body.style.cssText='margin:0;background:#000;color:#fff;font-family:system-ui;overflow:auto';const pre=w.document.createElement('pre');pre.style.cssText='white-space:pre-wrap;font-size:48px;line-height:1.45;text-align:center;padding:35vh 7vw 45vh;margin:0';pre.textContent=editor.value;w.document.body.appendChild(pre)}}
editor.addEventListener('input',sync);speed.addEventListener('input',()=>speedOut.textContent=speed.value);size.addEventListener('input',()=>{{sizeOut.textContent=size.value;script.style.fontSize=size.value+'px'}});
document.getElementById('start').onclick=start;document.getElementById('pause').onclick=pause;document.getElementById('reset').onclick=reset;document.getElementById('count').onclick=countStart;
document.getElementById('mirror').onclick=()=>stage.classList.toggle('mirror');document.getElementById('full').onclick=()=>stage.requestFullscreen?.();document.getElementById('second').onclick=secondScreen;
document.getElementById('clear').onclick=()=>{{editor.value='';sync();reset()}};
document.addEventListener('keydown',e=>{{if(e.target===editor)return;if(e.code==='Space'){{e.preventDefault();running?pause():start()}}if(e.key==='Home')reset();if(e.key==='Escape')pause()}});sync();
}})();
</script></body></html>"""


@router.get("/shared-sky/live/auto-cue", response_class=HTMLResponse, include_in_schema=False)
def auto_cue_page(request: Request):
    require_esp_hub_member(request)
    nonce = secrets.token_urlsafe(18)
    return HTMLResponse(
        auto_cue_html(nonce),
        headers={
            "Cache-Control": "no-store, max-age=0",
            "Pragma": "no-cache",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": (
                "default-src 'none'; style-src 'unsafe-inline'; "
                f"script-src 'nonce-{nonce}'; img-src data:; "
                "base-uri 'none'; form-action 'none'; frame-ancestors 'self'"
            ),
        },
    )


@router.get("/shared-sky/live/api/watch/{broadcast_id}/rhiannon-guardian/readiness")
def rhiannon_guardian_readiness(broadcast_id: str, request: Request):
    owner = owner_session_authorized(request)
    if owner:
        member = getattr(request.state, "member", None)
        actor_user_id = str(getattr(member, "user_id", "owner") or "owner")
    else:
        member, _ = require_esp_hub_member(request)
        actor_user_id = str(member.user_id)
    try:
        broadcast = dict(live.community._broadcast(broadcast_id))
    except Exception as exc:
        raise HTTPException(404, "Shared Skies LIVE not found") from exc
    if not owner and str(broadcast.get("user_id") or "") != actor_user_id:
        raise HTTPException(403, "Only the LIVE creator or an Owner can view Guardian readiness")
    try:
        assignments = ModeratorPermissionService(live.community).list_live_assignments(
            broadcast_id,
            actor_user_id=actor_user_id,
            owner=owner,
        )
    except Exception as exc:
        raise HTTPException(403, "Guardian moderation readiness is unavailable") from exc
    effective_count = sum(1 for item in assignments if bool(item.get("effective")))
    return guardian_projection(broadcast, effective_moderators=effective_count)


def install_shared_skies_live_assist(app: Any) -> None:
    existing = {
        (
            str(getattr(route, "path", "")),
            tuple(sorted(str(method).upper() for method in (getattr(route, "methods", set()) or set()))),
        )
        for route in app.router.routes
    }
    for route in tuple(router.routes):
        signature = (
            str(getattr(route, "path", "")),
            tuple(sorted(str(method).upper() for method in (getattr(route, "methods", set()) or set()))),
        )
        if signature in existing:
            continue
        app.router.routes.append(route)
        existing.add(signature)


__all__ = [
    "auto_cue_html",
    "auto_cue_page",
    "guardian_projection",
    "install_shared_skies_live_assist",
    "rhiannon_guardian_readiness",
    "router",
]
