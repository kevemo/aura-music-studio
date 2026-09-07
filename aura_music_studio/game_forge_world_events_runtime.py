from __future__ import annotations

import json

from .game_forge_integrity import game_integrity_hash
from .game_forge_models import GameBuild, GameDNA
from .game_forge_store import game_dir, save_game
from .game_forge_world import ensure_world
from .game_forge_world_events import world_event_runtime_payload
from .game_forge_world_logic_runtime import (
    PLAYTEST_CSP,
    build_world_logic_playtest,
    private_play_html,
    render_world_logic_playtest,
)


def _json_for_script(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def _inject_before_body(html: str, fragment: str) -> str:
    marker = "</body></html>"
    if marker not in html:
        raise ValueError("Aura World Logic runtime document boundary changed; World Events injection requires review")
    return html.replace(marker, fragment + marker, 1)


def _event_document(payload: dict, code: str) -> str:
    return (
        "<script id='aura-world-events-dna' type='application/json'>"
        + _json_for_script(payload)
        + "</script><script>\n'use strict';\n"
        + code
        + "\n</script>"
    )


_AURA2D_WORLD_EVENTS = r"""
const auraEvents=JSON.parse(document.getElementById('aura-world-events-dna').textContent||'{"entities":[],"spawn_positions":{}}');
const eventRows=(auraEvents.entities||[]).filter(e=>e.active!==false),eventState=new Map(),eventParticles=[];
function eventBehavior(row,op){return(row.behaviors||[]).find(b=>b.op===op)||null}
function eventFinite(v,d=0){const n=Number(v);return Number.isFinite(n)?n:d}
function eventPlayerWorld(){return{x:(p.x-W/2)/auraUnits,y:-(p.y-H/2)/auraUnits,z:0}}
function eventDistance(a,b){return Math.hypot(eventFinite(a?.x)-eventFinite(b?.x),eventFinite(a?.y)-eventFinite(b?.y),eventFinite(a?.z)-eventFinite(b?.z))}
function eventScreen(pos){return{x:W/2+eventFinite(pos?.x)*auraUnits,y:H/2-eventFinite(pos?.y)*auraUnits}}
for(const row of eventRows)eventState.set(row.id,{lastSpawnAt:-1e9,audio:null,audioVolume:0,audioRetryAt:0,particleCarry:0});
const eventCanvas=document.createElement('canvas');eventCanvas.id='aura-world-events-overlay';eventCanvas.setAttribute('aria-hidden','true');eventCanvas.style.cssText='position:fixed;inset:0;z-index:4;pointer-events:none;width:100%;height:100%';document.body.insertBefore(eventCanvas,document.getElementById('hud'));const eventCtx=eventCanvas.getContext('2d',{alpha:true});
function eventResize(){const d=Math.min(devicePixelRatio||1,2);if(eventCanvas.width!==Math.floor(W*d)||eventCanvas.height!==Math.floor(H*d)){eventCanvas.width=Math.floor(W*d);eventCanvas.height=Math.floor(H*d);eventCtx.setTransform(d,0,0,d,0,0)}}
let eventAudioUnlocked=false;const hasEventAudio=eventRows.some(row=>eventBehavior(row,'audio_zone'));
if(hasEventAudio){const b=document.createElement('button');b.id='world-audio-toggle';b.textContent='Enable World Audio';b.style.cssText='position:fixed;right:12px;bottom:50px;z-index:9;background:#0c1324e8;color:#fff;border:1px solid #ffffff33;border-radius:9px;padding:8px 10px;font:800 12px system-ui;cursor:pointer';b.addEventListener('click',async()=>{eventAudioUnlocked=!eventAudioUnlocked;b.textContent=eventAudioUnlocked?'Disable World Audio':'Enable World Audio';if(!eventAudioUnlocked)for(const state of eventState.values())if(state.audio){state.audio.pause();state.audio.currentTime=0}});document.body.appendChild(b)}
function eventAudio(row,state,dt,now){const zone=eventBehavior(row,'audio_zone');if(!zone)return;const params=zone.params||{},url=String(params.media_url||'');if(!url||!eventAudioUnlocked){if(state.audio){state.audio.pause();state.audioVolume=0}return}if(!state.audio){state.audio=new Audio(url);state.audio.preload='metadata';state.audio.loop=params.loop!==false;state.audio.volume=0}const radius=Math.max(.25,eventFinite(params.radius,5)),dist=eventDistance(eventPlayerWorld(),row.position||{}),inside=dist<=radius,target=inside?Math.max(0,Math.min(1,eventFinite(params.volume,.65)))*(1-dist/radius):0,fade=Math.max(.05,eventFinite(params.fade_seconds,.6)),step=dt/fade;state.audioVolume+=Math.max(-step,Math.min(step,target-state.audioVolume));state.audio.volume=Math.max(0,Math.min(1,state.audioVolume));if(inside&&state.audio.paused&&now>=state.audioRetryAt){state.audioRetryAt=now+1;const promise=state.audio.play();if(promise?.catch)promise.catch(()=>{})}else if(!inside&&state.audioVolume<=.001&&!state.audio.paused)state.audio.pause()}
function eventSpawnPortal(row,state,now){const spawn=eventBehavior(row,'spawn');if(!spawn)return;const params=spawn.params||{},target=auraEvents.spawn_positions?.[params.target_spawn_id||'spawn'];if(!target)return;const radius=Math.max(.5,Math.max(Math.abs(eventFinite(row.scale?.x,1)),Math.abs(eventFinite(row.scale?.y,1)))),near=eventDistance(eventPlayerWorld(),row.position||{})<=radius,cool=Math.max(.1,eventFinite(params.cooldown_seconds,1));if(near&&now-state.lastSpawnAt>=cool){state.lastSpawnAt=now;const q=eventScreen(target);p.x=q.x;p.y=q.y;try{auraSay(`Entered ${row.name}`,2)}catch(_e){}}}
function eventEmit(row,state,dt){const emitter=eventBehavior(row,'particle_emitter');if(!emitter)return;const q=eventScreen(row.position||{}),pms=emitter.params||{},rate=Math.max(0,eventFinite(pms.rate,18)),max=Math.max(1,Math.min(512,Math.round(eventFinite(pms.max_particles,160)))),current=eventParticles.reduce((n,p)=>n+(p.owner===row.id?1:0),0);state.particleCarry+=rate*dt;let count=Math.min(max-current,Math.floor(state.particleCarry));state.particleCarry-=count;count=Math.min(count,32);for(let i=0;i<count&&eventParticles.length<1024;i++){const spread=Math.max(0,eventFinite(pms.spread,1)),speed=Math.max(0,eventFinite(pms.speed,1.5))*auraUnits*.35,angle=Math.random()*Math.PI*2,mag=(.3+Math.random()*.7)*speed;eventParticles.push({owner:row.id,x:q.x+(Math.random()-.5)*spread*auraUnits,y:q.y+(Math.random()-.5)*spread*auraUnits,vx:Math.cos(angle)*mag,vy:Math.sin(angle)*mag,age:0,life:Math.max(.05,eventFinite(pms.lifetime_seconds,1.8)),size:Math.max(.5,eventFinite(pms.size,5)),color:String(pms.color||'#5be1ff')})}}
function eventUpdate(dt,now){for(const row of eventRows){const state=eventState.get(row.id);if(!state)continue;eventSpawnPortal(row,state,now);eventAudio(row,state,dt,now);eventEmit(row,state,dt)}for(let i=eventParticles.length-1;i>=0;i--){const q=eventParticles[i];q.age+=dt;if(q.age>=q.life){eventParticles.splice(i,1);continue}q.x+=q.vx*dt;q.y+=q.vy*dt;q.vy-=8*dt}}
function eventDraw(){eventResize();eventCtx.clearRect(0,0,W,H);for(const row of eventRows){const spawn=eventBehavior(row,'spawn');if(!spawn||row.visible===false)continue;const q=eventScreen(row.position||{}),sx=Math.max(12,Math.abs(eventFinite(row.scale?.x,1))*auraUnits*.5),sy=Math.max(12,Math.abs(eventFinite(row.scale?.y,1))*auraUnits*.5);eventCtx.fillStyle='#986cff55';eventCtx.strokeStyle='#d8c9ff';eventCtx.lineWidth=2;eventCtx.fillRect(q.x-sx,q.y-sy,sx*2,sy*2);eventCtx.strokeRect(q.x-sx,q.y-sy,sx*2,sy*2)}for(const q of eventParticles){eventCtx.globalAlpha=Math.max(0,1-q.age/q.life);eventCtx.fillStyle=q.color;eventCtx.beginPath();eventCtx.arc(q.x,q.y,q.size,0,Math.PI*2);eventCtx.fill()}eventCtx.globalAlpha=1}
let eventLast=performance.now();function eventFrame(ms){const dt=Math.min(.033,Math.max(0,(ms-eventLast)/1000));eventLast=ms;eventUpdate(dt,ms/1000);eventDraw();requestAnimationFrame(eventFrame)}requestAnimationFrame(eventFrame);
"""


_AURA3D_WORLD_EVENTS = r"""
const auraEvents=JSON.parse(document.getElementById('aura-world-events-dna').textContent||'{"entities":[],"spawn_positions":{}}');
const eventRows=(auraEvents.entities||[]).filter(e=>e.active!==false),eventState=new Map(),eventParticles=[];
function eventBehavior(row,op){return(row.behaviors||[]).find(b=>b.op===op)||null}
function eventFinite(v,d=0){const n=Number(v);return Number.isFinite(n)?n:d}
function eventDistance(a,b){return Math.hypot(eventFinite(a?.x)-eventFinite(b?.x),eventFinite(a?.y)-eventFinite(b?.y),eventFinite(a?.z)-eventFinite(b?.z))}
function eventScreen3D(pos){const a=player.position||{},scale=28;return{x:innerWidth/2+(eventFinite(pos?.x)-eventFinite(a.x))*scale,y:innerHeight/2-(eventFinite(pos?.y)-eventFinite(a.y))*scale}}
for(const row of eventRows)eventState.set(row.id,{lastSpawnAt:-1e9,audio:null,audioVolume:0,audioRetryAt:0,particleCarry:0});
const eventCanvas=document.createElement('canvas');eventCanvas.id='aura-world-events-overlay';eventCanvas.setAttribute('aria-hidden','true');eventCanvas.style.cssText='position:fixed;inset:0;z-index:4;pointer-events:none;width:100%;height:100%';document.body.appendChild(eventCanvas);const eventCtx=eventCanvas.getContext('2d',{alpha:true});
function eventResize(){const d=Math.min(devicePixelRatio||1,2),w=innerWidth,h=innerHeight;if(eventCanvas.width!==Math.floor(w*d)||eventCanvas.height!==Math.floor(h*d)){eventCanvas.width=Math.floor(w*d);eventCanvas.height=Math.floor(h*d);eventCtx.setTransform(d,0,0,d,0,0)}}
let eventAudioUnlocked=false;const hasEventAudio=eventRows.some(row=>eventBehavior(row,'audio_zone'));
if(hasEventAudio){const b=document.createElement('button');b.id='world-audio-toggle';b.textContent='Enable World Audio';b.style.cssText='position:fixed;right:12px;bottom:50px;z-index:9;background:#0c1324e8;color:#fff;border:1px solid #ffffff33;border-radius:9px;padding:8px 10px;font:800 12px system-ui;cursor:pointer';b.addEventListener('click',()=>{eventAudioUnlocked=!eventAudioUnlocked;b.textContent=eventAudioUnlocked?'Disable World Audio':'Enable World Audio';if(!eventAudioUnlocked)for(const state of eventState.values())if(state.audio){state.audio.pause();state.audio.currentTime=0}});document.body.appendChild(b)}
function eventAudio(row,state,dt,now){const zone=eventBehavior(row,'audio_zone');if(!zone)return;const pms=zone.params||{},url=String(pms.media_url||'');if(!url||!eventAudioUnlocked){if(state.audio){state.audio.pause();state.audioVolume=0}return}if(!state.audio){state.audio=new Audio(url);state.audio.preload='metadata';state.audio.loop=pms.loop!==false;state.audio.volume=0}const radius=Math.max(.25,eventFinite(pms.radius,5)),dist=eventDistance(player.position||{},row.position||{}),inside=dist<=radius,target=inside?Math.max(0,Math.min(1,eventFinite(pms.volume,.65)))*(1-dist/radius):0,fade=Math.max(.05,eventFinite(pms.fade_seconds,.6)),step=dt/fade;state.audioVolume+=Math.max(-step,Math.min(step,target-state.audioVolume));state.audio.volume=Math.max(0,Math.min(1,state.audioVolume));if(inside&&state.audio.paused&&now>=state.audioRetryAt){state.audioRetryAt=now+1;const promise=state.audio.play();if(promise?.catch)promise.catch(()=>{})}else if(!inside&&state.audioVolume<=.001&&!state.audio.paused)state.audio.pause()}
function eventSpawnPortal(row,state,now){const spawn=eventBehavior(row,'spawn');if(!spawn)return;const pms=spawn.params||{},target=auraEvents.spawn_positions?.[pms.target_spawn_id||'spawn'];if(!target)return;const radius=Math.max(.5,Math.max(Math.abs(eventFinite(row.scale?.x,1)),Math.abs(eventFinite(row.scale?.y,1)),Math.abs(eventFinite(row.scale?.z,1)))),near=eventDistance(player.position||{},row.position||{})<=radius,cool=Math.max(.1,eventFinite(pms.cooldown_seconds,1));if(near&&now-state.lastSpawnAt>=cool){state.lastSpawnAt=now;player.position.x=eventFinite(target.x);player.position.y=eventFinite(target.y,1);player.position.z=eventFinite(target.z);try{auraSay(`Entered ${row.name}`,2)}catch(_e){}}}
function eventEmit(row,state,dt){const emitter=eventBehavior(row,'particle_emitter');if(!emitter)return;const q=eventScreen3D(row.position||{}),pms=emitter.params||{},rate=Math.max(0,eventFinite(pms.rate,18)),max=Math.max(1,Math.min(512,Math.round(eventFinite(pms.max_particles,160)))),current=eventParticles.reduce((n,p)=>n+(p.owner===row.id?1:0),0);state.particleCarry+=rate*dt;let count=Math.min(max-current,Math.floor(state.particleCarry));state.particleCarry-=count;count=Math.min(count,32);for(let i=0;i<count&&eventParticles.length<1024;i++){const spread=Math.max(0,eventFinite(pms.spread,1))*28,speed=Math.max(0,eventFinite(pms.speed,1.5))*18,angle=Math.random()*Math.PI*2,mag=(.3+Math.random()*.7)*speed;eventParticles.push({owner:row.id,x:q.x+(Math.random()-.5)*spread,y:q.y+(Math.random()-.5)*spread,vx:Math.cos(angle)*mag,vy:Math.sin(angle)*mag,age:0,life:Math.max(.05,eventFinite(pms.lifetime_seconds,1.8)),size:Math.max(.5,eventFinite(pms.size,5)),color:String(pms.color||'#5be1ff')})}}
function eventUpdate(dt,now){for(const row of eventRows){const state=eventState.get(row.id);if(!state)continue;eventSpawnPortal(row,state,now);eventAudio(row,state,dt,now);eventEmit(row,state,dt)}for(let i=eventParticles.length-1;i>=0;i--){const q=eventParticles[i];q.age+=dt;if(q.age>=q.life){eventParticles.splice(i,1);continue}q.x+=q.vx*dt;q.y+=q.vy*dt;q.vy-=8*dt}}
function eventDraw(){eventResize();eventCtx.clearRect(0,0,innerWidth,innerHeight);for(const row of eventRows){const spawn=eventBehavior(row,'spawn');if(!spawn||row.visible===false)continue;const q=eventScreen3D(row.position||{}),s=18;eventCtx.fillStyle='#986cff44';eventCtx.strokeStyle='#d8c9ff';eventCtx.lineWidth=2;eventCtx.fillRect(q.x-s,q.y-s,s*2,s*2);eventCtx.strokeRect(q.x-s,q.y-s,s*2,s*2)}for(const q of eventParticles){eventCtx.globalAlpha=Math.max(0,1-q.age/q.life);eventCtx.fillStyle=q.color;eventCtx.beginPath();eventCtx.arc(q.x,q.y,q.size,0,Math.PI*2);eventCtx.fill()}eventCtx.globalAlpha=1}
let eventLast=performance.now();function eventFrame(ms){const dt=Math.min(.033,Math.max(0,(ms-eventLast)/1000));eventLast=ms;eventUpdate(dt,ms/1000);eventDraw();requestAnimationFrame(eventFrame)}requestAnimationFrame(eventFrame);
"""


def render_world_events_playtest(game: GameDNA) -> str:
    world = ensure_world(game)
    payload = world_event_runtime_payload(game.id, world=world)
    html = render_world_logic_playtest(game)
    if not payload["entities"]:
        return html
    code = _AURA3D_WORLD_EVENTS if game.dimension == "3d" and game.engine_target == "aura3d" else _AURA2D_WORLD_EVENTS
    return _inject_before_body(html, _event_document(payload, code))


def build_world_events_playtest(game: GameDNA) -> tuple[GameDNA, str]:
    world = ensure_world(game)
    payload = world_event_runtime_payload(game.id, world=world)
    if not payload["entities"]:
        return build_world_logic_playtest(game)
    content_hash = game_integrity_hash(game)
    html = render_world_events_playtest(game)
    runtime_name = (
        "aura_game_runtime_3d_webgl2_v8_world_events"
        if game.dimension == "3d" and game.engine_target == "aura3d"
        else "aura_game_runtime_2d_canvas_v5_world_events"
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
    "render_world_events_playtest",
    "build_world_events_playtest",
    "private_play_html",
]
