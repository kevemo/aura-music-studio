from __future__ import annotations

import json

from .game_forge_gameplay import gameplay_runtime_payload
from .game_forge_integrity import game_integrity_hash
from .game_forge_models import GameBuild, GameDNA
from .game_forge_runtime import PLAYTEST_CSP, private_play_html
from .game_forge_runtime import render_foundation_playtest as _render_foundation_playtest
from .game_forge_store import game_dir, save_game
from .game_forge_world import ensure_world


def _json_for_script(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def _inject_before_body(html: str, fragment: str) -> str:
    marker = "</body></html>"
    if marker not in html:
        raise ValueError("Aura runtime document boundary changed; gameplay injection requires review")
    return html.replace(marker, fragment + marker, 1)


def _gameplay_document(payload: dict, code: str) -> str:
    serialized = _json_for_script(payload)
    return (
        "<script id='aura-gameplay-dna' type='application/json'>"
        + serialized
        + "</script><script>\n'use strict';\n"
        + code
        + "\n</script>"
    )


_AURA2D_GAMEPLAY = r"""
const auraGameplay=JSON.parse(document.getElementById('aura-gameplay-dna').textContent||'{"entities":[]}');
const auraRows=(auraGameplay.entities||[]).filter(e=>e.active!==false);
stars.length=0;haz.length=0;
const auraOverlay=document.createElement('canvas');auraOverlay.id='aura-gameplay-overlay';auraOverlay.setAttribute('aria-hidden','true');auraOverlay.style.cssText='position:fixed;inset:0;z-index:3;pointer-events:none;width:100%;height:100%';document.body.insertBefore(auraOverlay,document.getElementById('hud'));
const auraCtx=auraOverlay.getContext('2d',{alpha:true});
const auraMessage=document.createElement('div');auraMessage.id='aura-gameplay-message';auraMessage.setAttribute('role','status');auraMessage.setAttribute('aria-live','polite');auraMessage.style.cssText='position:fixed;left:50%;top:70px;transform:translateX(-50%);z-index:5;background:#050713dd;border:1px solid #ffffff28;border-radius:10px;padding:7px 10px;color:#fff;font:700 13px system-ui,sans-serif;display:none';document.body.appendChild(auraMessage);
const auraUnits=48,auraState=new Map(),auraNow=()=>performance.now()/1000;
function auraBehavior(row,op){return (row.behaviors||[]).find(b=>b.op===op)||null}
function auraResize(){const d=Math.min(devicePixelRatio||1,2);if(auraOverlay.width!==Math.floor(W*d)||auraOverlay.height!==Math.floor(H*d)){auraOverlay.width=Math.floor(W*d);auraOverlay.height=Math.floor(H*d);auraCtx.setTransform(d,0,0,d,0,0)}}
function auraScreen(pos){return{x:W/2+Number(pos?.x||0)*auraUnits,y:H/2-Number(pos?.y||0)*auraUnits}}
function auraRadius(row){const s=row.scale||{};return Math.max(10,Math.min(70,Math.max(Math.abs(Number(s.x||1)),Math.abs(Number(s.y||1)))*auraUnits*.38))}
for(const row of auraRows){auraState.set(row.id,{base:{...(row.position||{x:0,y:0,z:0})},position:{...(row.position||{x:0,y:0,z:0})},velocity:{x:0,y:0,z:0},taken:false,respawnAt:0,lastDamageAt:-1e9,triggered:false,checkpoint:false})}
const auraPlayerRow=auraRows.find(e=>e.kind==='player'||e.id==='player');const auraSpawnRow=auraRows.find(e=>e.kind==='spawn'||e.id==='spawn');
const auraStart=auraScreen(auraSpawnRow?.position||auraPlayerRow?.position||{x:0,y:0});p.x=auraStart.x;p.y=auraStart.y;let auraCheckpoint={...auraStart};
let auraMessageUntil=0;
function auraSay(text,seconds=2){auraMessage.textContent=String(text||'').slice(0,160);auraMessage.style.display='block';auraMessageUntil=auraNow()+seconds}
function auraHit(row,state){const q=auraScreen(state.position),r=auraRadius(row);return Math.hypot(p.x-q.x,p.y-q.y)<p.r+r}
function auraResetPlayer(){p.x=auraCheckpoint.x;p.y=auraCheckpoint.y}
function auraPatrol(row,state,now){const b=auraBehavior(row,'patrol');if(!b)return false;const params=b.params||{},axis=['x','y','z'].includes(params.axis)?params.axis:'x',distance=Number(params.distance||0),speed=Number(params.speed||0);const phase=Math.sin(now*speed);state.position[axis]=Number(state.base[axis]||0)+phase*distance;return true}
function auraPhysics(row,state,dt){if(!row.physics||row.physics.mode!=='dynamic'||row.kind==='player'||auraBehavior(row,'patrol'))return;const g=auraGameplay.gravity||{x:0,y:-9.81,z:0};state.velocity.x+=Number(g.x||0)*Number(row.physics.gravity_scale??1)*dt;state.velocity.y+=Number(g.y||0)*Number(row.physics.gravity_scale??1)*dt;state.velocity.z+=Number(g.z||0)*Number(row.physics.gravity_scale??1)*dt;state.position.x+=state.velocity.x*dt;state.position.y+=state.velocity.y*dt;state.position.z+=state.velocity.z*dt;if(state.position.y<0){state.position.y=0;state.velocity.y=Math.abs(state.velocity.y)*Math.max(0,Math.min(1,Number(row.physics.restitution||0)))}}
function auraGameplayUpdate(dt,now){for(const row of auraRows){if(row.id===auraPlayerRow?.id||row.id===auraSpawnRow?.id)continue;const state=auraState.get(row.id);if(!state)continue;auraPatrol(row,state,now);auraPhysics(row,state,dt);if(state.taken&&state.respawnAt&&now>=state.respawnAt){state.taken=false;state.respawnAt=0}if(state.taken)continue;const collect=auraBehavior(row,'collectible');if(collect&&auraHit(row,state)){score+=Math.max(1,Number(collect.params?.points||1));state.taken=true;if(collect.params?.respawn===true)state.respawnAt=now+Math.max(.25,Number(collect.params?.respawn_seconds||3));auraSay(`Collected ${row.name} · +${Math.max(1,Number(collect.params?.points||1))}`);continue}const damage=auraBehavior(row,'damage');if(damage&&auraHit(row,state)){const cool=Math.max(.05,Number(damage.params?.cooldown_seconds||.75));if(now-state.lastDamageAt>=cool){state.lastDamageAt=now;lives-=Math.max(1,Number(damage.params?.amount||1));if(damage.params?.reset_to_checkpoint!==false)auraResetPlayer();if(lives<=0){lives=3;score=0;auraResetPlayer();auraSay('Restarted from checkpoint')}else auraSay(`${row.name} · ${lives} lives remaining`)}}const checkpoint=auraBehavior(row,'checkpoint');if(checkpoint&&auraHit(row,state)&&!state.checkpoint){state.checkpoint=true;auraCheckpoint=auraScreen(state.position);auraSay(checkpoint.params?.label||row.name)}const trigger=auraBehavior(row,'quest_trigger');if(trigger&&auraHit(row,state)&&(!state.triggered||trigger.params?.once!==true)){state.triggered=true;auraSay(trigger.params?.event||row.name,3)}}document.getElementById('score').textContent=`Score ${score} · Lives ${lives}`;if(auraMessageUntil&&now>auraMessageUntil){auraMessage.style.display='none';auraMessageUntil=0}}
function auraGameplayDraw(){auraResize();auraCtx.clearRect(0,0,W,H);for(const row of auraRows){if(row.visible===false||row.kind==='player'||row.kind==='spawn')continue;const state=auraState.get(row.id);if(!state||state.taken)continue;const q=auraScreen(state.position),r=auraRadius(row),collect=auraBehavior(row,'collectible'),damage=auraBehavior(row,'damage'),checkpoint=auraBehavior(row,'checkpoint'),trigger=auraBehavior(row,'quest_trigger'),patrol=auraBehavior(row,'patrol');auraCtx.save();auraCtx.translate(q.x,q.y);auraCtx.lineWidth=2;auraCtx.strokeStyle='#ffffffcc';auraCtx.fillStyle=collect?'#f6d36c':damage?'#ff627f':checkpoint?'#79dda5':trigger?'#986cff':patrol?'#5be1ff':'#8ea6ff';if(row.physics?.shape==='sphere'||collect){auraCtx.beginPath();auraCtx.arc(0,0,r,0,Math.PI*2);auraCtx.fill();auraCtx.stroke()}else{auraCtx.fillRect(-r,-r*.55,r*2,r*1.1);auraCtx.strokeRect(-r,-r*.55,r*2,r*1.1)}auraCtx.restore()}}
let auraGameplayLast=performance.now();function auraGameplayFrame(nowMs){const dt=Math.min(.033,Math.max(0,(nowMs-auraGameplayLast)/1000));auraGameplayLast=nowMs;auraGameplayUpdate(dt,nowMs/1000);auraGameplayDraw();requestAnimationFrame(auraGameplayFrame)}requestAnimationFrame(auraGameplayFrame);
"""


_AURA3D_GAMEPLAY = r"""
const auraGameplay=JSON.parse(document.getElementById('aura-gameplay-dna').textContent||'{"entities":[]}');
const auraRows=(auraGameplay.entities||[]).filter(e=>e.active!==false),auraState=new Map();
const auraHud=document.createElement('div');auraHud.id='aura-gameplay-hud';auraHud.setAttribute('role','status');auraHud.setAttribute('aria-live','polite');auraHud.style.cssText='position:fixed;left:12px;top:104px;z-index:5;background:#060914d9;border:1px solid #ffffff26;border-radius:10px;padding:8px 10px;color:#fff;font:700 12px/1.4 system-ui,sans-serif;min-width:150px';document.body.appendChild(auraHud);
let auraScore=0,auraLives=3,auraEvent='',auraEventUntil=0;const auraNow=()=>performance.now()/1000;
function auraBehavior(row,op){return (row.behaviors||[]).find(b=>b.op===op)||null}
function auraEntity(id){return entities.find(e=>e.id===id)||null}
function auraRadius(row){const s=row.scale||{};return Math.max(.25,Math.min(50,Math.max(Math.abs(Number(s.x||1)),Math.abs(Number(s.y||1)),Math.abs(Number(s.z||1)))*.7))}
for(const row of auraRows){const runtime=auraEntity(row.id);auraState.set(row.id,{base:{...(row.position||runtime?.position||{x:0,y:0,z:0})},baseScale:{...(runtime?.scale||row.scale||{x:1,y:1,z:1})},velocity:{x:0,y:0,z:0},taken:false,respawnAt:0,lastDamageAt:-1e9,triggered:false,checkpoint:false})}
const auraSpawn=auraRows.find(e=>e.kind==='spawn'||e.id==='spawn');let auraCheckpoint={...(auraSpawn?.position||player.position||{x:0,y:1,z:0})};
function auraSay(text,seconds=2){auraEvent=String(text||'').slice(0,160);auraEventUntil=auraNow()+seconds}
function auraHit(row,e){const a=player.position||{},b=e.position||{},limit=.7+auraRadius(row);return Math.hypot(Number(a.x||0)-Number(b.x||0),Number(a.y||0)-Number(b.y||0),Number(a.z||0)-Number(b.z||0))<limit}
function auraResetPlayer(){player.position.x=Number(auraCheckpoint.x||0);player.position.y=Number(auraCheckpoint.y||1);player.position.z=Number(auraCheckpoint.z||0)}
function auraPatrol(row,e,state,now){const b=auraBehavior(row,'patrol');if(!b)return false;const p=b.params||{},axis=['x','y','z'].includes(p.axis)?p.axis:'x',distance=Number(p.distance||0),speed=Number(p.speed||0);e.position[axis]=Number(state.base[axis]||0)+Math.sin(now*speed)*distance;return true}
function auraPhysics(row,e,state,dt){if(!row.physics||row.physics.mode!=='dynamic'||row.kind==='player'||auraBehavior(row,'patrol'))return;const g=auraGameplay.gravity||{x:0,y:-9.81,z:0},scale=Number(row.physics.gravity_scale??1);state.velocity.x+=Number(g.x||0)*scale*dt;state.velocity.y+=Number(g.y||0)*scale*dt;state.velocity.z+=Number(g.z||0)*scale*dt;e.position.x+=state.velocity.x*dt;e.position.y+=state.velocity.y*dt;e.position.z+=state.velocity.z*dt;const floor=Math.max(.05,Math.abs(Number(row.scale?.y||1))*.5);if(e.position.y<floor){e.position.y=floor;state.velocity.y=Math.abs(state.velocity.y)*Math.max(0,Math.min(1,Number(row.physics.restitution||0)))}}
function auraHide(e,state){if(!e||state.taken)return;state.taken=true;e.scale={x:.000001,y:.000001,z:.000001}}
function auraShow(e,state){state.taken=false;e.scale={...state.baseScale}}
function auraGameplayUpdate(dt,now){for(const row of auraRows){if(row.kind==='player'||row.kind==='spawn')continue;const e=auraEntity(row.id),state=auraState.get(row.id);if(!e||!state)continue;auraPatrol(row,e,state,now);auraPhysics(row,e,state,dt);if(state.taken&&state.respawnAt&&now>=state.respawnAt){state.respawnAt=0;auraShow(e,state)}if(state.taken)continue;const collect=auraBehavior(row,'collectible');if(collect&&auraHit(row,e)){auraScore+=Math.max(1,Number(collect.params?.points||1));auraHide(e,state);if(collect.params?.respawn===true)state.respawnAt=now+Math.max(.25,Number(collect.params?.respawn_seconds||3));auraSay(`Collected ${row.name}`);continue}const damage=auraBehavior(row,'damage');if(damage&&auraHit(row,e)){const cool=Math.max(.05,Number(damage.params?.cooldown_seconds||.75));if(now-state.lastDamageAt>=cool){state.lastDamageAt=now;auraLives-=Math.max(1,Number(damage.params?.amount||1));if(damage.params?.reset_to_checkpoint!==false)auraResetPlayer();if(auraLives<=0){auraLives=3;auraScore=0;auraResetPlayer();auraSay('Restarted from checkpoint')}else auraSay(`${row.name} · ${auraLives} lives remaining`)}}const checkpoint=auraBehavior(row,'checkpoint');if(checkpoint&&auraHit(row,e)&&!state.checkpoint){state.checkpoint=true;auraCheckpoint={...(e.position||auraCheckpoint)};auraSay(checkpoint.params?.label||row.name)}const trigger=auraBehavior(row,'quest_trigger');if(trigger&&auraHit(row,e)&&(!state.triggered||trigger.params?.once!==true)){state.triggered=true;auraSay(trigger.params?.event||row.name,3)}}if(auraEventUntil&&now>auraEventUntil){auraEvent='';auraEventUntil=0}auraHud.textContent=`Score ${auraScore} · Lives ${auraLives}${auraEvent?' · '+auraEvent:''}`}
let auraGameplayLast=performance.now();function auraGameplayFrame(nowMs){const dt=Math.min(.033,Math.max(0,(nowMs-auraGameplayLast)/1000));auraGameplayLast=nowMs;auraGameplayUpdate(dt,nowMs/1000);requestAnimationFrame(auraGameplayFrame)}requestAnimationFrame(auraGameplayFrame);
"""


def render_gameplay_playtest(game: GameDNA) -> str:
    """Layer bounded declarative gameplay over the reviewed Aura native runtimes.

    The underlying renderer remains responsible for graphics/media. This layer only consumes the
    server-sanitized gameplay payload and never evaluates creator source, performs network I/O or
    loads executable game code.
    """
    world = ensure_world(game)
    html = _render_foundation_playtest(game)
    payload = gameplay_runtime_payload(world)
    payload["gravity"] = world.environment.gravity.model_dump(mode="json")
    code = _AURA3D_GAMEPLAY if game.dimension == "3d" and game.engine_target == "aura3d" else _AURA2D_GAMEPLAY
    return _inject_before_body(html, _gameplay_document(payload, code))


def build_gameplay_playtest(game: GameDNA) -> tuple[GameDNA, str]:
    ensure_world(game)
    content_hash = game_integrity_hash(game)
    html = render_gameplay_playtest(game)
    runtime_name = (
        "aura_game_runtime_3d_webgl2_v5_gameplay"
        if game.dimension == "3d" and game.engine_target == "aura3d"
        else "aura_game_runtime_2d_canvas_v2_gameplay"
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
    "render_gameplay_playtest",
    "build_gameplay_playtest",
    "private_play_html",
]
