from __future__ import annotations

import json
from html import escape

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .branding import PRODUCT_FULL_NAME
from .game_forge_godot_export import GODOT_SOURCE_ADAPTER_VERSION, create_godot_source_export
from .game_forge_store import load_game
from .plans import GAME_CREATE

router = APIRouter(tags=["Aura Game Godot Source Preview"])


def _creator(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Sign in required")
    if not member.plan.has(GAME_CREATE):
        raise HTTPException(403, "Godot source preview export unlocks on the Basic £4.99 tier")
    return member


def _game(game_id: str):
    try:
        return load_game(game_id)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, "Game not found") from exc


def _capability(game_id: str) -> dict:
    return {
        "game_id": game_id,
        "target": "godot",
        "label": "Godot 4 Source Project Preview",
        "adapter_version": GODOT_SOURCE_ADAPTER_VERSION,
        "source_project_ready": True,
        "production_ready": False,
        "runtime_parity_claimed": False,
        "format": "godot4_source_zip_v1",
        "fixed_reviewed_gdscript_template": True,
        "creator_generated_executable_code": False,
        "requires_headless_engine_validation_before_production": True,
        "reason": (
            "A deterministic Godot 4 source project can be downloaded now, but production-equivalent "
            "runtime parity is not claimed until generated projects pass a pinned Godot 4 headless CI gate."
        ),
    }


@router.get("/api/game-forge/games/{game_id}/exports/godot-source/capability")
def godot_source_capability(game_id: str, request: Request):
    _creator(request)
    _game(game_id)
    return _capability(game_id)


@router.post("/api/game-forge/games/{game_id}/exports/godot-source")
def create_godot_source_preview(game_id: str, request: Request):
    _creator(request)
    game = _game(game_id)
    try:
        result = create_godot_source_export(game)
    except (OSError, ValueError) as exc:
        raise HTTPException(409, str(exc)) from exc
    return result


@router.get("/game-creation/godot-export/{game_id}", response_class=HTMLResponse, include_in_schema=False)
def godot_source_preview_portal(game_id: str, request: Request):
    member = getattr(request.state, "member", None)
    safe_id = escape(game_id, quote=True)
    if member is None:
        return RedirectResponse(f"/signin?next=/game-creation/godot-export/{safe_id}", status_code=303)
    if not member.plan.has(GAME_CREATE):
        return RedirectResponse("/game-creation", status_code=303)
    safe_brand = escape(PRODUCT_FULL_NAME)
    game_json = json.dumps(game_id)
    page = r"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='noindex,nofollow'><title>Godot 4 Source Preview — __BRAND__</title><style>
:root{--bg:#04050b;--panel:#0e1322;--line:#ffffff22;--gold:#efca6d;--violet:#986cff;--cyan:#5be1ff;--green:#79dda5;--muted:#bec6d6}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 8% 0,#42165f66,transparent 32%),radial-gradient(circle at 92% 8%,#0e4a6755,transparent 29%),var(--bg);color:#fff;font-family:Inter,system-ui,sans-serif}.wrap{width:min(1040px,calc(100% - 24px));margin:auto}.nav{border-bottom:1px solid var(--line);background:#05070eef}.navin{min-height:66px;display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap}.brand{font-weight:950;color:#fff;text-decoration:none}.brand small{display:block;color:var(--gold);font-size:.66rem;letter-spacing:.1em;text-transform:uppercase}.hero{padding:46px 0 18px}.eyebrow{color:var(--gold);font-size:.7rem;font-weight:950;letter-spacing:.14em;text-transform:uppercase}h1{font-size:clamp(2.7rem,7vw,5.6rem);line-height:.92;letter-spacing:-.05em;margin:.15em 0}.muted{color:var(--muted);line-height:1.6}.card{border:1px solid var(--line);border-radius:18px;background:linear-gradient(145deg,#111729e9,#090d18ee);padding:18px;margin:12px 0}.safe{border-left:3px solid var(--green)}.warning{border-left:3px solid var(--gold)}button,.btn{font:inherit;border:1px solid var(--line);background:#ffffff08;color:#fff;border-radius:10px;padding:10px 13px;font-weight:850;cursor:pointer;text-decoration:none}.primary{border:0;background:linear-gradient(120deg,var(--gold),var(--violet));color:#160c1d}.pill{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:4px 8px;font-size:.72rem;margin:3px}.notice{display:none;padding:11px;border:1px solid var(--line);border-radius:10px;margin:10px 0}.notice.show{display:block}code{color:#bcefff}.row{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap}@media(max-width:620px){.row{align-items:stretch}.row>*{width:100%}}
</style></head><body><nav class='nav'><div class='wrap navin'><a class='brand' href='/game-creation'>__BRAND__<small>Aura Game Forge · Godot 4 Source Preview</small></a><a class='btn' href='/game-creation'>Game Forge</a></div></nav><main class='wrap'><section class='hero'><div class='eyebrow'>Deterministic external-engine source adapter</div><h1 id='title'>Godot 4 Source Preview</h1><p class='muted'>Download a real Godot 4 source project generated from the current integrity-bound Aura Game/World DNA. This is a developer preview, not a claim that Godot already reproduces every Aura-native gameplay, Adventure, cinematic, audio or State Machine feature.</p><div id='notice' class='notice'></div></section><section class='card warning'><div class='eyebrow'>Truth boundary</div><h2>Source-ready ≠ production parity</h2><p class='muted'>The project contains <code>project.godot</code>, <code>main.tscn</code>, a fixed reviewed <code>main.gd</code> adapter template, Game/World DNA JSON and checksum-verified media. Creator text is never interpolated into GDScript. Production-ready remains false until generated projects are exercised by a pinned Godot 4 headless CI gate.</p><span class='pill'>Godot 4.x</span><span class='pill'>deterministic ZIP</span><span class='pill'>fixed reviewed GDScript</span><span class='pill'>verified media</span></section><section class='card safe'><div class='row'><div><div class='eyebrow'>Create preview package</div><h2 id='gameTitle'>Loading game…</h2><p class='muted'>Rebuild the Aura game first if its integrity hash changed. Rights confirmation and verified asset checks remain mandatory.</p></div><button id='buildBtn' class='primary'>Create Godot Source ZIP</button></div><div id='result' class='muted'></div></section></main><script>
'use strict';const GAME_ID=__GAME_JSON__;const $=id=>document.getElementById(id);function notice(v,bad=false){const n=$('notice');n.textContent=v;n.className='notice show';n.style.borderColor=bad?'#ff8fa566':'#79dda566'}async function api(url,opt={}){const r=await fetch(url,{credentials:'same-origin',headers:{'Content-Type':'application/json',...(opt.headers||{})},...opt});let b={};try{b=await r.json()}catch{}if(!r.ok)throw new Error(typeof b.detail==='string'?b.detail:(b.detail?.message||`Request failed (${r.status})`));return b}async function load(){try{const g=await api(`/api/game-forge/games/${encodeURIComponent(GAME_ID)}`);$('gameTitle').textContent=g.title||'Godot Source Preview'}catch(e){notice(e.message,true)}}$('buildBtn').addEventListener('click',async()=>{try{$('buildBtn').disabled=true;const r=await api(`/api/game-forge/games/${encodeURIComponent(GAME_ID)}/exports/godot-source`,{method:'POST'});$('result').innerHTML=`Preview created: <b>${r.filename}</b> · ${Math.round(r.byte_size/1024)} KB · production parity: <b>not claimed</b><br><a class='btn' href='${r.download_url}'>Download Godot Source ZIP</a>`;notice('Godot 4 source preview created from the current integrity-bound Aura build.')}catch(e){notice(e.message,true)}finally{$('buildBtn').disabled=false}});load();
</script></body></html>"""
    page = page.replace("__BRAND__", safe_brand).replace("__GAME_JSON__", game_json)
    return HTMLResponse(page)


__all__ = ["router", "godot_source_preview_portal"]
