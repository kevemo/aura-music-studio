from __future__ import annotations

import json

from .game_forge_integrity import game_integrity_hash
from .game_forge_live_3d import inject_live_3d_tuning
from .game_forge_live_copilot import inject_live_copilot
from .game_forge_models import GameBuild, GameDNA
from .game_forge_state_machine import state_machine_runtime_payload
from .game_forge_store import game_dir, save_game
from .game_forge_world import ensure_world
from .game_forge_world_events_runtime import (
    PLAYTEST_CSP,
    build_world_events_playtest,
    private_play_html,
    render_world_events_playtest,
)


def _json_for_script(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def _inject_before_body(html: str, fragment: str) -> str:
    marker = "</body></html>"
    if marker not in html:
        raise ValueError("Aura World Events runtime document boundary changed; State Machine injection requires review")
    return html.replace(marker, fragment + marker, 1)


def _machine_document(payload: dict, code: str) -> str:
    return (
        "<script id='aura-state-machine-dna' type='application/json'>"
        + _json_for_script(payload)
        + "</script><script>\n'use strict';\n"
        + code
        + "\n</script>"
    )


_AURA2D_STATE_MACHINE = r"""
const auraMachines=JSON.parse(document.getElementById('aura-state-machine-dna').textContent||'{"entities":[]}');
const machineRows=(auraMachines.entities||[]),machineState=new Map();
function machineFinite(v,d=0){const n=Number(v);return Number.isFinite(n)?n:d}
function machinePlayerWorld(){return{x:(p.x-W/2)/auraUnits,y:-(p.y-H/2)/auraUnits,z:0}}
function machineDistance(a,b){return Math.hypot(machineFinite(a?.x)-machineFinite(b?.x),machineFinite(a?.y)-machineFinite(b?.y),machineFinite(a?.z)-machineFinite(b?.z))}
function machineStateDef(row,id){return(row.machine?.states||[]).find(s=>s.state===id)||null}
function machinePosition(row,stateDef){const base=row.position||{},off=stateDef?.offset||{};return{x:machineFinite(base.x)+machineFinite(off.x),y:machineFinite(base.y)+machineFinite(off.y),z:machineFinite(base.z)+machineFinite(off.z)}}
function machineScreen(pos){return{x:W/2+machineFinite(pos?.x)*auraUnits,y:H/2-machineFinite(pos?.y)*auraUnits}}
function machineFlag(name){try{return typeof adv!=='undefined'&&adv?.flags?.[name]===true}catch(_e){return false}}
function machineEnter(row,st,next,now){const def=machineStateDef(row,next);if(!def)return;st.current=next;st.enteredAt=now;st.position=machinePosition(row,def);st.visible=def.visible!==false;if(def.message){try{auraSay(def.message,3)}catch(_e){}}}
for(const row of machineRows){const initial=row.machine?.initial_state||row.machine?.states?.[0]?.state;const def=machineStateDef(row,initial);machineState.set(row.id,{current:initial,enteredAt:performance.now()/1000,position:machinePosition(row,def),visible:def?.visible!==false})}
function machineTriggered(row,st,t,now){const dwell=Math.max(.05,machineFinite(t.min_state_seconds,.1));if(now-st.enteredAt<dwell)return false;if(t.trigger==='timer')return now-st.enteredAt>=Math.max(.05,machineFinite(t.seconds,1));if(t.trigger==='player_near')return machineDistance(machinePlayerWorld(),st.position)<=Math.max(.1,machineFinite(t.radius,2));if(t.trigger==='adventure_flag'){const value=machineFlag(t.flag);return value===(t.flag_value!==false)}return false}
function machineUpdate(now){for(const row of machineRows){const st=machineState.get(row.id);if(!st)continue;const transitions=row.machine?.transitions||[];for(const t of transitions){if(t.from_state!==st.current)continue;if(machineTriggered(row,st,t,now)){machineEnter(row,st,t.to_state,now);break}}}}
const machineCanvas=document.createElement('canvas');machineCanvas.id='aura-state-machine-overlay';machineCanvas.setAttribute('aria-hidden','true');machineCanvas.style.cssText='position:fixed;inset:0;z-index:4;pointer-events:none;width:100%;height:100%';document.body.appendChild(machineCanvas);const machineCtx=machineCanvas.getContext('2d',{alpha:true});
function machineResize(){const d=Math.min(devicePixelRatio||1,2);if(machineCanvas.width!==Math.floor(W*d)||machineCanvas.height!==Math.floor(H*d)){machineCanvas.width=Math.floor(W*d);machineCanvas.height=Math.floor(H*d);machineCtx.setTransform(d,0,0,d,0,0)}}
function machineDraw(){machineResize();machineCtx.clearRect(0,0,W,H);for(const row of machineRows){const st=machineState.get(row.id);if(!st||!st.visible)continue;const q=machineScreen(st.position),s=row.scale||{},rx=Math.max(10,Math.min(80,Math.abs(machineFinite(s.x,1))*auraUnits*.4)),ry=Math.max(10,Math.min(80,Math.abs(machineFinite(s.y,1))*auraUnits*.4));machineCtx.fillStyle='#5be1ff99';machineCtx.strokeStyle='#ffffffdd';machineCtx.lineWidth=2;machineCtx.fillRect(q.x-rx,q.y-ry,rx*2,ry*2);machineCtx.strokeRect(q.x-rx,q.y-ry,rx*2,ry*2)}}
function machineFrame(ms){const now=ms/1000;machineUpdate(now);machineDraw();requestAnimationFrame(machineFrame)}requestAnimationFrame(machineFrame);
"""


_AURA3D_STATE_MACHINE = r"""
const auraMachines=JSON.parse(document.getElementById('aura-state-machine-dna').textContent||'{"entities":[]}');
const machineRows=(auraMachines.entities||[]),machineState=new Map();
function machineFinite(v,d=0){const n=Number(v);return Number.isFinite(n)?n:d}
function machineDistance(a,b){return Math.hypot(machineFinite(a?.x)-machineFinite(b?.x),machineFinite(a?.y)-machineFinite(b?.y),machineFinite(a?.z)-machineFinite(b?.z))}
function machineStateDef(row,id){return(row.machine?.states||[]).find(s=>s.state===id)||null}
function machineFlag(name){try{return typeof adv!=='undefined'&&adv?.flags?.[name]===true}catch(_e){return false}}
function machineApply(row,st,def){const e=auraEntity(row.id);if(!e||!def)return;const base=st.base,off=def.offset||{};e.position.x=machineFinite(base.x)+machineFinite(off.x);e.position.y=machineFinite(base.y)+machineFinite(off.y);e.position.z=machineFinite(base.z)+machineFinite(off.z);e.scale=def.visible===false?{x:.000001,y:.000001,z:.000001}:{...st.baseScale}}
function machineEnter(row,st,next,now){const def=machineStateDef(row,next);if(!def)return;st.current=next;st.enteredAt=now;machineApply(row,st,def);if(def.message){try{auraSay(def.message,3)}catch(_e){}}}
for(const row of machineRows){const e=auraEntity(row.id),initial=row.machine?.initial_state||row.machine?.states?.[0]?.state,def=machineStateDef(row,initial),base={...(e?.position||row.position||{x:0,y:0,z:0})},baseScale={...(e?.scale||row.scale||{x:1,y:1,z:1})};const st={current:initial,enteredAt:performance.now()/1000,base,baseScale};machineState.set(row.id,st);machineApply(row,st,def)}
function machineCurrentPosition(row,st){const e=auraEntity(row.id);return e?.position||st.base}
function machineTriggered(row,st,t,now){const dwell=Math.max(.05,machineFinite(t.min_state_seconds,.1));if(now-st.enteredAt<dwell)return false;if(t.trigger==='timer')return now-st.enteredAt>=Math.max(.05,machineFinite(t.seconds,1));if(t.trigger==='player_near')return machineDistance(player.position||{},machineCurrentPosition(row,st))<=Math.max(.1,machineFinite(t.radius,2));if(t.trigger==='adventure_flag'){const value=machineFlag(t.flag);return value===(t.flag_value!==false)}return false}
function machineUpdate(now){for(const row of machineRows){const st=machineState.get(row.id);if(!st)continue;for(const t of row.machine?.transitions||[]){if(t.from_state!==st.current)continue;if(machineTriggered(row,st,t,now)){machineEnter(row,st,t.to_state,now);break}}}}
function machineFrame(ms){machineUpdate(ms/1000);requestAnimationFrame(machineFrame)}requestAnimationFrame(machineFrame);
"""


def _inject_live_layers(html: str, *, game: GameDNA) -> str:
    html = inject_live_copilot(html, game=game)
    return inject_live_3d_tuning(html, game=game)


def render_state_machine_playtest(game: GameDNA) -> str:
    world = ensure_world(game)
    payload = state_machine_runtime_payload(game.id, world=world)
    html = render_world_events_playtest(game)
    if payload["entities"]:
        code = _AURA3D_STATE_MACHINE if game.dimension == "3d" and game.engine_target == "aura3d" else _AURA2D_STATE_MACHINE
        html = _inject_before_body(html, _machine_document(payload, code))
    return _inject_live_layers(html, game=game)


def _decorate_delegated_build(game: GameDNA, html: str) -> tuple[GameDNA, str]:
    """Add private live-creation layers to a lower-layer build without changing Game DNA."""
    if not game.latest_build:
        raise ValueError("Delegated Game Forge build did not produce build metadata")
    html = _inject_live_layers(html, game=game)
    path = game_dir(game.id) / "builds" / game.latest_build.build_id / "play.html"
    if not path.is_file():
        raise FileNotFoundError(path)
    path.write_text(html, encoding="utf-8")
    return game, html


def build_state_machine_playtest(game: GameDNA) -> tuple[GameDNA, str]:
    world = ensure_world(game)
    payload = state_machine_runtime_payload(game.id, world=world)
    if not payload["entities"]:
        game, html = build_world_events_playtest(game)
        return _decorate_delegated_build(game, html)
    content_hash = game_integrity_hash(game)
    html = render_state_machine_playtest(game)
    runtime_name = (
        "aura_game_runtime_3d_webgl2_v9_state_machine"
        if game.dimension == "3d" and game.engine_target == "aura3d"
        else "aura_game_runtime_2d_canvas_v6_state_machine"
    )
    build = GameBuild(content_hash=content_hash, requested_engine=game.engine_target, runtime=runtime_name)
    folder = game_dir(game.id)
    build_dir = folder / "builds" / build.build_id
    build_dir.mkdir(parents=True, exist_ok=False)
    (build_dir / "play.html").write_text(html, encoding="utf-8")
    game.latest_build = build
    game.status = "review_ready"
    game.updated_at = build.created_at
    save_game(game)
    return game, html


__all__ = [
    "PLAYTEST_CSP",
    "render_state_machine_playtest",
    "build_state_machine_playtest",
    "private_play_html",
]
