from __future__ import annotations

import json
from html import escape

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .branding import PRODUCT_FULL_NAME
from .game_forge_export import router as game_export_api_router
from .plans import GAME_CREATE

router = APIRouter(tags=["Aura Game Export Studio"])
# Export API and UI stay in one composable router so Game Forge can mount the complete surface at
# a stable subsystem boundary without editing the central application composition file.
router.include_router(game_export_api_router)


@router.get("/game-creation/export/{game_id}", response_class=HTMLResponse, include_in_schema=False)
def game_export_portal(game_id: str, request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        return RedirectResponse(f"/signin?next=/game-creation/export/{escape(game_id, quote=True)}", status_code=303)
    if not member.plan.has(GAME_CREATE):
        return RedirectResponse("/game-creation", status_code=303)

    safe_brand = escape(PRODUCT_FULL_NAME)
    game_json = json.dumps(game_id)
    page = r"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='noindex,nofollow'><title>Game Export Studio — __BRAND__</title><style>
:root{--bg:#04050b;--panel:#0e1322;--line:#ffffff22;--gold:#efca6d;--violet:#986cff;--green:#79dda5;--muted:#bcc5d5;--red:#ff8fa5}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 8% 0,#42165f66,transparent 32%),radial-gradient(circle at 92% 8%,#0e4a6755,transparent 29%),var(--bg);color:#fff;font-family:Inter,system-ui,sans-serif}.wrap{width:min(1180px,calc(100% - 24px));margin:auto}.nav{border-bottom:1px solid var(--line);background:#05070eef;position:sticky;top:0;z-index:20}.navin{min-height:64px;display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap}.brand{font-weight:950;color:#fff;text-decoration:none}.brand small{display:block;color:var(--gold);font-size:.65rem;letter-spacing:.12em;text-transform:uppercase}.hero{padding:38px 0 20px}.eyebrow{color:var(--gold);font-size:.68rem;font-weight:950;letter-spacing:.14em;text-transform:uppercase}h1{font-size:clamp(2.5rem,6vw,5rem);line-height:.94;letter-spacing:-.05em;margin:.12em 0}.muted{color:var(--muted);line-height:1.55}.grid{display:grid;grid-template-columns:1.2fr .8fr;gap:14px;padding-bottom:70px}.card{border:1px solid var(--line);border-radius:18px;background:linear-gradient(145deg,#111729e9,#090d18ee);padding:16px;margin-bottom:12px}.target{border:1px solid var(--line);border-radius:12px;padding:12px;margin-top:8px;background:#ffffff05}.row{display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap}.pill{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:4px 8px;font-size:.7rem}.ready{color:#b9ffd7;border-color:#79dda566}.planned{color:#ffd7df;border-color:#ff8fa566}button,.btn{font:inherit;border:1px solid var(--line);background:#ffffff08;color:#fff;border-radius:10px;padding:10px 12px;font-weight:850;cursor:pointer;text-decoration:none}.primary{border:0;background:linear-gradient(120deg,var(--gold),var(--violet));color:#160c1d}.notice{display:none;padding:11px;border:1px solid var(--line);border-radius:10px;margin:10px 0}.notice.show{display:block}.download{display:none;margin-top:10px}.download.show{display:inline-block}@media(max-width:820px){.grid{grid-template-columns:1fr}}
</style></head><body><nav class='nav'><div class='wrap navin'><a class='brand' href='/game-creation'>__BRAND__<small>Aura Game Forge · Export Studio</small></a><div><a class='btn' id='playLink' href='#'>Playtest</a> <a class='btn' id='stateLink' href='#'>State Machines</a></div></div></nav><main class='wrap'><section class='hero'><div class='eyebrow'>Integrity-bound delivery</div><h1 id='title'>Game Export Studio</h1><p class='muted'>Package the exact current Aura build and checksum-verified media into a reproducible web bundle. Planned external-engine adapters remain visibly unavailable until they are genuinely production ready.</p><div id='notice' class='notice'></div></section><section class='grid'><div><div class='card'><div class='eyebrow'>Production-ready export</div><h2>Aura Web Package</h2><p class='muted'>Includes the reviewed play runtime, same-origin verified media and a provenance manifest. Requires a current build plus confirmed project and media rights.</p><button id='exportBtn' class='primary'>Create Aura Web Export</button><a id='download' class='btn download' href='#'>Download ZIP</a><div id='result' class='muted'></div></div><div class='card'><div class='eyebrow'>Adapter roadmap</div><div id='targets'>Loading export capabilities…</div></div></div><div><div class='card'><div class='eyebrow'>Safety contract</div><h3>No fake engine exports</h3><p class='muted'>Phaser, PlayCanvas, Babylon.js and Godot remain planned adapters. Export Studio will not present data stubs or LLM-generated code as production-ready engine projects.</p></div><div class='card'><div class='eyebrow'>Provenance</div><p class='muted'>Export identifiers derive from the current integrity hash. ZIP timestamps and manifest provenance are stable for a fixed build, so an unchanged build and unchanged verified media reproduce the same package bytes.</p></div></div></section></main><script>
'use strict';const GAME_ID=__GAME_JSON__;const $=id=>document.getElementById(id);function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}function notice(v,bad=false){const e=$('notice');e.textContent=v;e.className='notice show';e.style.borderColor=bad?'#ff8fa566':'#79dda566'}async function api(url,opt={}){const r=await fetch(url,{credentials:'same-origin',headers:{'Content-Type':'application/json',...(opt.headers||{})},...opt});let b={};try{b=await r.json()}catch{}if(!r.ok)throw new Error(typeof b.detail==='string'?b.detail:(b.detail?.message||`Request failed (${r.status})`));return b}
async function load(){try{const [game,caps]=await Promise.all([api(`/api/game-forge/games/${encodeURIComponent(GAME_ID)}`),api(`/api/game-forge/games/${encodeURIComponent(GAME_ID)}/exports/capabilities`)]);$('title').textContent=`${game.title} · Game Export Studio`;$('playLink').href=`/game-creation/play/${encodeURIComponent(GAME_ID)}`;$('stateLink').href=`/game-creation/state-machines/${encodeURIComponent(GAME_ID)}`;$('targets').innerHTML=Object.entries(caps.targets||{}).map(([id,row])=>`<div class='target'><div class='row'><b>${esc(row.label||id)}</b><span class='pill ${row.production_ready?'ready':'planned'}'>${row.production_ready?'Production ready':'Planned'}</span></div><div class='muted'>${esc(row.format||'')} ${row.reason?`· ${esc(row.reason)}`:''}</div></div>`).join('')}catch(e){notice(e.message,true)}}
$('exportBtn').addEventListener('click',async()=>{try{$('exportBtn').disabled=true;notice('Building integrity-bound Aura Web package…');const r=await api(`/api/game-forge/games/${encodeURIComponent(GAME_ID)}/exports`,{method:'POST',body:JSON.stringify({target:'aura_web'})});$('download').href=r.download_url;$('download').className='btn download show';$('result').textContent=`${r.filename} · ${r.byte_size} bytes · SHA-256 ${r.sha256}`;notice('Export ready. Same unchanged build will reproduce the same package.')}catch(e){notice(e.message,true)}finally{$('exportBtn').disabled=false}});load();
</script></body></html>"""
    return HTMLResponse(page.replace("__BRAND__", safe_brand).replace("__GAME_JSON__", game_json))


__all__ = ["router"]
