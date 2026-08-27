from __future__ import annotations

import json

from .game_forge_adventure_runtime import PLAYTEST_CSP, build_adventure_playtest, private_play_html, render_adventure_playtest
from .game_forge_integrity import game_integrity_hash
from .game_forge_models import GameBuild, GameDNA
from .game_forge_store import game_dir, save_game
from .game_forge_world import ensure_world
from .game_forge_world_logic import world_logic_runtime_payload


def _json_for_script(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def _inject_before_body(html: str, fragment: str) -> str:
    marker = "</body></html>"
    if marker not in html:
        raise ValueError("Aura Adventure runtime document boundary changed; World Logic injection requires review")
    return html.replace(marker, fragment + marker, 1)


def _logic_document(payload: dict, code: str) -> str:
    serialized = _json_for_script(payload)
    return (
        "<script id='aura-world-logic-dna' type='application/json'>"
        + serialized
        + "</script><script>\n'use strict';\n"
        + code
        + "\n</script>"
    )


_AURA2D_WORLD_LOGIC = r"""
const auraLogic=JSON.parse(document.getElementById('aura-world-logic-dna').textContent||'{"entities":[]}');
const logicRows=(auraLogic.entities||[]).filter(e=>e.active!==false),logicState=new Map();
function logicBehavior(row,op){return(row.behaviors||[]).find(b=>b.op===op)||null}
function logicFinite(v,d=0){const n=Number(v);return Number.isFinite(n)?n:d}
for(const row of logicRows)logicState.set(row.id,{base:{...(row.position||{x:0,y:0,z:0})},position:{...(row.position||{x:0,y:0,z:0})},timerElapsed:0,timerFired:false,doorOpenSince:0});
const logicOverlay=document.createElement('canvas');logicOverlay.id='aura-world-logic-overlay';logicOverlay.setAttribute('aria-hidden','true');logicOverlay.style.cssText='position:fixed;inset:0;z-index:4;pointer-events:none;width:100%;height:100%';document.body.insertBefore(logicOverlay,document.getElementById('hud'));
const logicCtx=logicOverlay.getContext('2d',{alpha:true});
function logicResize(){const d=Math.min(devicePixelRatio||1,2),w=Math.floor(W*d),h=Math.floor(H*d);if(logicOverlay.width!==w||logicOverlay.height!==h){logicOverlay.width=w;logicOverlay.height=h;logicCtx.setTransform(d,0,0,d,0,0)}}
function logicPlayerWorld(){return{x:(p.x-W/2)/auraUnits,y:-(p.y-H/2)/auraUnits,z:0}}
function logicPosition(id){if(id==='player')return logicPlayerWorld();const l=logicState.get(id);if(l)return l.position;try{const g=auraState.get(id);if(g)return g.position}catch(_e){}return null}
function logicMoveToward(pos,target,speed,stop,dt){const dx=logicFinite(target.x)-logicFinite(pos.x),dy=logicFinite(target.y)-logicFinite(pos.y),dz=logicFinite(target.z)-logicFinite(pos.z),dist=Math.hypot(dx,dy,dz);if(dist<=stop||dist<=1e-6)return;const step=Math.min(Math.max(0,speed)*dt,Math.max(0,dist-stop));pos.x+=dx/dist*step;pos.y+=dy/dist*step;pos.z+=dz/dist*step}
function logicDoor(row,state,params,dt,now){const player=logicPlayerWorld(),dx=player.x-state.base.x,dy=player.y-state.base.y,dz=player.z-state.base.z,near=Math.hypot(dx,dy,dz)<=logicFinite(params.trigger_distance,2.5),axis=['x','y','z'].includes(params.axis)?params.axis:'y',closed=logicFinite(state.base[axis]),open=closed+logicFinite(params.distance,3);if(near)state.doorOpenSince=now;const held=near||params.auto_close!==true||(state.doorOpenSince>0&&now-state.doorOpenSince<logicFinite(params.close_delay,1.5)),target=held?open:closed,current=logicFinite(state.position[axis]),maxStep=Math.max(.001,logicFinite(params.speed,2))*dt;state.position[axis]=current<target?Math.min(target,current+maxStep):Math.max(target,current-maxStep)}
function logicUpdate(dt,now){for(const row of logicRows){const state=logicState.get(row.id);if(!state)continue;const follow=logicBehavior(row,'follow_target');if(follow){const target=logicPosition(follow.params?.target||'player');if(target)logicMoveToward(state.position,target,logicFinite(follow.params?.speed,2),logicFinite(follow.params?.stop_distance,1.5),dt)}const timer=logicBehavior(row,'timer');if(timer){state.timerElapsed+=dt;const seconds=Math.max(.05,logicFinite(timer.params?.seconds,5));if(state.timerElapsed>=seconds&&(!state.timerFired||timer.params?.repeat===true)){state.timerFired=true;state.timerElapsed=timer.params?.repeat===true?state.timerElapsed%seconds:seconds;try{auraSay(timer.params?.event||row.name,3)}catch(_e){}}}const door=logicBehavior(row,'door');if(door)logicDoor(row,state,door.params||{},dt,now)}}
function logicDraw(){logicResize();logicCtx.clearRect(0,0,W,H);for(const row of logicRows){if(row.visible===false||logicBehavior(row,'timer'))continue;const s=logicState.get(row.id);if(!s)continue;const q={x:W/2+logicFinite(s.position.x)*auraUnits,y:H/2-logicFinite(s.position.y)*auraUnits},scale=row.scale||{},rx=Math.max(9,Math.min(70,Math.abs(logicFinite(scale.x,1))*auraUnits*.38)),ry=Math.max(9,Math.min(70,Math.abs(logicFinite(scale.y,1))*auraUnits*.38));logicCtx.save();logicCtx.translate(q.x,q.y);logicCtx.lineWidth=2;logicCtx.strokeStyle='#ffffffd0';logicCtx.fillStyle=logicBehavior(row,'door')?'#efca6dcc':'#5be1ffcc';logicCtx.fillRect(-rx,-ry,rx*2,ry*2);logicCtx.strokeRect(-rx,-ry,rx*2,ry*2);logicCtx.restore()}}
let logicLast=performance.now();function logicFrame(ms){const dt=Math.min(.033,Math.max(0,(ms-logicLast)/1000));logicLast=ms;logicUpdate(dt,ms/1000);logicDraw();requestAnimationFrame(logicFrame)}requestAnimationFrame(logicFrame);
"""


_AURA3D_WORLD_LOGIC = r"""
const auraLogic=JSON.parse(document.getElementById('aura-world-logic-dna').textContent||'{"entities":[]}');
const logicRows=(auraLogic.entities||[]).filter(e=>e.active!==false),logicState=new Map();
function logicBehavior(row,op){return(row.behaviors||[]).find(b=>b.op===op)||null}
function logicFinite(v,d=0){const n=Number(v);return Number.isFinite(n)?n:d}
for(const row of logicRows){const e=auraEntity(row.id);logicState.set(row.id,{base:{...(e?.position||row.position||{x:0,y:0,z:0})},timerElapsed:0,timerFired:false,doorOpenSince:0})}
function logicTarget(id){if(id==='player')return player;return auraEntity(id)}
function logicMoveToward(pos,target,speed,stop,dt){const dx=logicFinite(target.x)-logicFinite(pos.x),dy=logicFinite(target.y)-logicFinite(pos.y),dz=logicFinite(target.z)-logicFinite(pos.z),dist=Math.hypot(dx,dy,dz);if(dist<=stop||dist<=1e-6)return;const step=Math.min(Math.max(0,speed)*dt,Math.max(0,dist-stop));pos.x+=dx/dist*step;pos.y+=dy/dist*step;pos.z+=dz/dist*step}
function logicDoor(e,state,params,dt,now){const dx=logicFinite(player.position.x)-state.base.x,dy=logicFinite(player.position.y)-state.base.y,dz=logicFinite(player.position.z)-state.base.z,near=Math.hypot(dx,dy,dz)<=logicFinite(params.trigger_distance,2.5),axis=['x','y','z'].includes(params.axis)?params.axis:'y',closed=logicFinite(state.base[axis]),open=closed+logicFinite(params.distance,3);if(near)state.doorOpenSince=now;const held=near||params.auto_close!==true||(state.doorOpenSince>0&&now-state.doorOpenSince<logicFinite(params.close_delay,1.5)),target=held?open:closed,current=logicFinite(e.position[axis]),maxStep=Math.max(.001,logicFinite(params.speed,2))*dt;e.position[axis]=current<target?Math.min(target,current+maxStep):Math.max(target,current-maxStep)}
function logicUpdate(dt,now){for(const row of logicRows){const e=auraEntity(row.id),state=logicState.get(row.id);if(!state)continue;const follow=logicBehavior(row,'follow_target');if(follow&&e){const target=logicTarget(follow.params?.target||'player');if(target?.position)logicMoveToward(e.position,target.position,logicFinite(follow.params?.speed,2),logicFinite(follow.params?.stop_distance,1.5),dt)}const timer=logicBehavior(row,'timer');if(timer){state.timerElapsed+=dt;const seconds=Math.max(.05,logicFinite(timer.params?.seconds,5));if(state.timerElapsed>=seconds&&(!state.timerFired||timer.params?.repeat===true)){state.timerFired=true;state.timerElapsed=timer.params?.repeat===true?state.timerElapsed%seconds:seconds;try{auraSay(timer.params?.event||row.name,3)}catch(_e){}}}const door=logicBehavior(row,'door');if(door&&e)logicDoor(e,state,door.params||{},dt,now)}}
let logicLast=performance.now();function logicFrame(ms){const dt=Math.min(.033,Math.max(0,(ms-logicLast)/1000));logicLast=ms;logicUpdate(dt,ms/1000);requestAnimationFrame(logicFrame)}requestAnimationFrame(logicFrame);
"""


def render_world_logic_playtest(game: GameDNA) -> str:
    world = ensure_world(game)
    payload = world_logic_runtime_payload(world)
    html = render_adventure_playtest(game)
    if not payload["entities"]:
        return html
    code = _AURA3D_WORLD_LOGIC if game.dimension == "3d" and game.engine_target == "aura3d" else _AURA2D_WORLD_LOGIC
    return _inject_before_body(html, _logic_document(payload, code))


def build_world_logic_playtest(game: GameDNA) -> tuple[GameDNA, str]:
    world = ensure_world(game)
    payload = world_logic_runtime_payload(world)
    if not payload["entities"]:
        return build_adventure_playtest(game)
    content_hash = game_integrity_hash(game)
    html = render_world_logic_playtest(game)
    runtime_name = (
        "aura_game_runtime_3d_webgl2_v7_world_logic"
        if game.dimension == "3d" and game.engine_target == "aura3d"
        else "aura_game_runtime_2d_canvas_v4_world_logic"
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
    "render_world_logic_playtest",
    "build_world_logic_playtest",
    "private_play_html",
]
