from __future__ import annotations

import json
from typing import Any

from .game_forge_adventure import adventure_runtime_payload
from .game_forge_gameplay import gameplay_runtime_payload
from .game_forge_models import GameDNA
from .game_forge_state_machine import state_machine_runtime_payload
from .game_forge_world import GameWorldDNA, WorldEntityDNA

_BRIDGE_MARKER = "id='aura2d-world-bridge-dna'"
_RUNTIME_STATE_MARKER = "id='aura-runtime-state-dna'"
_BODY_CLOSE = "</body>"
_PIXELS_PER_WORLD_UNIT = 48


def _basic_entity_row(entity: WorldEntityDNA) -> dict[str, Any]:
    return {
        "id": entity.id,
        "name": entity.name,
        "kind": entity.kind,
        "position": entity.transform.position.model_dump(mode="json"),
        "scale": entity.transform.scale.model_dump(mode="json"),
        "active": bool(entity.active),
        "visible": bool(entity.visible),
        "physics": entity.physics.model_dump(mode="json") if entity.physics else None,
        "behaviors": [],
    }


def aura2d_world_bridge_payload(game: GameDNA, world: GameWorldDNA) -> dict[str, Any]:
    """Build the closed subset of World DNA needed by the Aura2D runtime bridge."""
    adventure = adventure_runtime_payload(game)
    gameplay = gameplay_runtime_payload(world)
    machines = state_machine_runtime_payload(game.id, world=world)

    referenced: set[str] = set()
    for row in adventure.get("objectives", []):
        target = row.get("target_entity_id")
        if isinstance(target, str) and target:
            referenced.add(target)
    for row in adventure.get("dialogues", []):
        target = row.get("trigger_entity_id")
        if isinstance(target, str) and target:
            referenced.add(target)
    for row in adventure.get("gates", []):
        for key in ("trigger_entity_id", "door_entity_id"):
            target = row.get(key)
            if isinstance(target, str) and target:
                referenced.add(target)
    for row in machines.get("entities", []):
        target = row.get("id")
        if isinstance(target, str) and target:
            referenced.add(target)

    rows = {row["id"]: dict(row) for row in gameplay.get("entities", []) if isinstance(row.get("id"), str)}
    for entity in world.entities:
        if entity.id in referenced and entity.id not in rows:
            rows[entity.id] = _basic_entity_row(entity)

    return {
        "version": 1,
        "projection": "world_xz_to_canvas_v1",
        "pixels_per_world_unit": _PIXELS_PER_WORLD_UNIT,
        "entities": list(rows.values()),
        "runtime_contract": {
            "world_dna_collision_bridge": True,
            "collectible": True,
            "damage": True,
            "checkpoint": True,
            "quest_trigger": True,
            "patrol": True,
            "dialogue_and_gate_interaction": True,
            "state_machine_player_near": True,
            "save_state_integration": True,
            "world_dna_spawn_binding": True,
            "creator_javascript": False,
            "external_network_access": False,
        },
    }


def _bridge_script() -> str:
    return r"""
'use strict';
var aura2dBridgeDNA=JSON.parse(document.getElementById('aura2d-world-bridge-dna').textContent||'{}');
var entities=(aura2dBridgeDNA.entities||[]).map(row=>({
  id:row.id,name:row.name,kind:row.kind,position:{x:Number(row.position?.x||0),y:Number(row.position?.y||0),z:Number(row.position?.z||0)},
  scale:{x:Number(row.scale?.x||1),y:Number(row.scale?.y||1),z:Number(row.scale?.z||1)},active:row.active!==false,visible:row.visible!==false,
  physics:row.physics||null,behaviors:Array.isArray(row.behaviors)?row.behaviors:[]
}));
(()=>{
const bridge=window.AuraRuntimeState;
if(!bridge)throw new Error('Aura2D World bridge requires AuraRuntimeState');
const pixels=Math.max(8,Math.min(256,Number(aura2dBridgeDNA.pixels_per_world_unit||48))),topInset=60;
const basePositions=new Map(entities.map(row=>[row.id,{x:row.position.x,y:row.position.y,z:row.position.z}]));
const cooldowns=new Map(),respawns=new Map(),insideTriggers=new Set();
const authoredGameplay=entities.some(row=>(row.behaviors||[]).length>0);
if(authoredGameplay){if(typeof stars!=='undefined'&&Array.isArray(stars))stars.length=0;if(typeof haz!=='undefined'&&Array.isArray(haz))haz.length=0}
function playCenterY(){return topInset+Math.max(1,H-topInset)/2}
function toScreen(pos){return {x:W/2+Number(pos?.x||0)*pixels,y:playCenterY()+Number(pos?.z||0)*pixels}}
function toWorld(x,y){return {x:(Number(x||0)-W/2)/pixels,y:0,z:(Number(y||0)-playCenterY())/pixels}}
const spawnEntity=entities.find(row=>row.kind==='spawn'),playerEntity=entities.find(row=>row.kind==='player'),authoredStart=spawnEntity?.position||playerEntity?.position||null;
if(authoredStart){const start=toScreen(authoredStart);p.x=Math.max(p.r,Math.min(W-p.r,start.x));p.y=Math.max(topInset+p.r,Math.min(H-p.r,start.y))}
function playerWorld(){return toWorld(p.x,p.y)}
function entityEnabled(row){return row&&row.active!==false&&!row._auraGateHidden&&!row._auraStateHidden&&!row._auraCollectedHidden}
function collisionRadius(row){const sx=Math.abs(Number(row.scale?.x||1)),sz=Math.abs(Number(row.scale?.z||1));return Math.max(.35,Math.max(sx,sz)*.55+p.r/pixels)}
function hitEntity(row,pp){
  if(!entityEnabled(row))return false;
  const shape=String(row.physics?.shape||'sphere'),sx=Math.max(.2,Math.abs(Number(row.scale?.x||1))*.5)+p.r/pixels,sz=Math.max(.2,Math.abs(Number(row.scale?.z||1))*.5)+p.r/pixels;
  if(shape==='box'||shape==='mesh'||shape==='heightfield')return Math.abs(pp.x-row.position.x)<=sx&&Math.abs(pp.z-row.position.z)<=sz;
  return Math.hypot(pp.x-row.position.x,pp.z-row.position.z)<=collisionRadius(row);
}
function markDirty(){try{window.AuraMarkSaveDirty?.()}catch(_error){}}
function snapshot(){return bridge.snapshot()}
function importSnapshot(state){const ok=bridge.importState(state);if(ok){syncFromState();markDirty()}return ok}
function syncBaseScore(state){if(typeof score!=='undefined')score=Math.max(0,Math.trunc(Number(state.score||0)));if(typeof lives!=='undefined')lives=Math.max(0,Math.min(99,Math.trunc(Number(state.lives||0))))}
function syncFromState(){
  const state=snapshot(),collected=new Set(Array.isArray(state.collected)?state.collected:[]);
  for(const row of entities)if(!respawns.has(row.id))row._auraCollectedHidden=collected.has(row.id);
  syncBaseScore(state);
}
const baseImport=bridge.importState.bind(bridge);
bridge.importState=raw=>{const ok=baseImport(raw);if(ok)syncFromState();return ok};
if(bridge.contract)bridge.contract.aura2d_world_behavior_bridge=true;
window.Aura2DWorldBridge={version:1,projection:'world_xz_to_canvas_v1',contract:Object.freeze({...aura2dBridgeDNA.runtime_contract}),worldToCanvas:pos=>toScreen(pos),canvasToWorld:(x,y)=>toWorld(x,y)};
if(typeof auraPlayerPosition==='function')auraPlayerPosition=playerWorld;
const initialPlayer=playerWorld();
if(typeof auraState!=='undefined'&&!auraState.checkpoint)auraState.checkpoint={x:initialPlayer.x,y:0,z:initialPlayer.z};
function respawnPlayer(){const state=snapshot(),cp=state.checkpoint||initialPlayer,screen=toScreen(cp);p.x=Math.max(p.r,Math.min(W-p.r,screen.x));p.y=Math.max(topInset+p.r,Math.min(H-p.r,screen.y))}
function updatePersisted(mutator){const state=snapshot();mutator(state);return importSnapshot(state)}
function collect(row,params,now){
  const state=snapshot(),saved=new Set(state.collected||[]),canRespawn=params.respawn===true;
  if(!canRespawn&&saved.has(row.id))return;
  if(canRespawn){const until=respawns.get(row.id)||0;if(until>now)return;const delay=Math.max(.25,Number(params.respawn_seconds||3))*1000;respawns.set(row.id,now+delay);row._auraCollectedHidden=true;setTimeout(()=>{respawns.delete(row.id);row._auraCollectedHidden=false},delay)}
  bridge.dispatch('collect',row.id);
  updatePersisted(next=>{next.score=Math.min(1000000000,Math.max(0,Number(next.score||0))+Math.max(1,Number(params.points||1)));if(!canRespawn&&!next.collected.includes(row.id))next.collected.push(row.id)});
  auraStatus(`Collected ${row.name} · Score ${snapshot().score}`);
}
function checkpoint(row,params,pp){const state=snapshot(),cp=state.checkpoint;if(cp&&Math.hypot(cp.x-pp.x,cp.z-pp.z)<=.25)return;updatePersisted(next=>{next.checkpoint={x:pp.x,y:0,z:pp.z}});auraStatus(params.label||row.name||'Checkpoint')}
function trigger(row,params){
  const state=snapshot(),saved=new Set(state.triggered||[]);if(params.once===true&&saved.has(row.id))return;
  bridge.dispatch('reach',row.id);if(params.event)bridge.setFlag(params.event,true);
  if(params.once===true)updatePersisted(next=>{if(!next.triggered.includes(row.id))next.triggered.push(row.id)});else markDirty();
}
function damage(row,params,now){
  const until=cooldowns.get(row.id)||0;if(until>now)return;cooldowns.set(row.id,now+Math.max(.05,Number(params.cooldown_seconds||.75))*1000);
  updatePersisted(next=>{next.lives=Math.max(0,Math.trunc(Number(next.lives||3))-Math.max(1,Number(params.amount||1)));if(next.lives<=0)next.lives=3});
  if(params.reset_to_checkpoint!==false)respawnPlayer();auraStatus(`Lives ${snapshot().lives}`)
}
function patrol(row,params,now){const base=basePositions.get(row.id)||row.position,raw=String(params.axis||'x').toLowerCase(),axis=raw==='x'?'x':'z',distance=Math.max(0,Number(params.distance||0)),speed=Math.max(0,Number(params.speed||0));row.position[axis]=Number(base[axis]||0)+Math.sin(now/1000*speed)*distance}
function gameplayTick(now){
  const pp=playerWorld();
  if(typeof auraState!=='undefined'){auraState.score=Math.max(0,Math.trunc(Number(score||0)));auraState.lives=Math.max(0,Math.min(99,Math.trunc(Number(lives||0))))}
  for(const row of entities){if(!row.active)continue;let touching=false;for(const behavior of row.behaviors||[]){const params=behavior.params||{};if(behavior.op==='patrol'){patrol(row,params,now);continue}const hit=hitEntity(row,pp);touching=touching||hit;if(!hit)continue;if(behavior.op==='collectible')collect(row,params,now);else if(behavior.op==='checkpoint')checkpoint(row,params,pp);else if(behavior.op==='damage')damage(row,params,now);else if(behavior.op==='quest_trigger'&&!insideTriggers.has(row.id))trigger(row,params)}if(touching)insideTriggers.add(row.id);else insideTriggers.delete(row.id)}
}
function nearestInteraction(){const pp=playerWorld();let best=null,bestDistance=1.8;const ids=new Set();for(const row of auraDialogues.values())ids.add(row.trigger_entity_id);for(const row of auraGates.values())ids.add(row.trigger_entity_id);for(const row of auraObjectives.values())if(row.kind==='reach'&&row.target_entity_id)ids.add(row.target_entity_id);for(const id of ids){const row=entities.find(entity=>entity.id===id);if(!row||!entityEnabled(row))continue;const d=Math.hypot(pp.x-row.position.x,pp.z-row.position.z);if(d<=bestDistance){best=row;bestDistance=d}}return best}
addEventListener('keydown',event=>{if(event.key.toLowerCase()!=='e'||event.repeat)return;const row=nearestInteraction();if(!row)return;if(auraTalk(row.id))return;auraDispatch('reach',row.id)});
function drawEntity(row){
  if(row.kind==='player'||row.kind==='spawn'||!entityEnabled(row)||row.visible===false)return;const s=toScreen(row.position),sx=Math.max(10,Math.min(90,Math.abs(Number(row.scale?.x||1))*pixels*.55)),sz=Math.max(10,Math.min(90,Math.abs(Number(row.scale?.z||1))*pixels*.55));ctx.save();ctx.lineWidth=2;ctx.font='600 11px system-ui,sans-serif';ctx.textAlign='center';
  if(row.kind==='collectible'){ctx.fillStyle='#f6d36c';ctx.beginPath();ctx.arc(s.x,s.y,Math.max(7,Math.min(24,sx*.35)),0,Math.PI*2);ctx.fill()}else if(row.kind==='hazard'){ctx.fillStyle='#ff627f';ctx.fillRect(s.x-sx/2,s.y-sz/2,sx,sz)}else if(row.kind==='trigger'){ctx.strokeStyle='#7ce8ff';ctx.setLineDash([5,4]);ctx.strokeRect(s.x-sx/2,s.y-sz/2,sx,sz)}else if(row.kind==='npc'){ctx.fillStyle='#c4a7ff';ctx.beginPath();ctx.arc(s.x,s.y,Math.max(9,Math.min(28,sx*.4)),0,Math.PI*2);ctx.fill()}else{ctx.fillStyle='#93a4c7';ctx.fillRect(s.x-sx/2,s.y-sz/2,sx,sz)}
  ctx.setLineDash([]);ctx.fillStyle='#ffffff';ctx.fillText(String(row.name||'').slice(0,42),s.x,s.y-Math.max(12,sz/2+7));ctx.restore()
}
function drawWorld(){for(const row of entities)drawEntity(row)}
if(typeof drawPlayer==='function'){const baseDrawPlayer=drawPlayer;drawPlayer=function(){drawWorld();baseDrawPlayer()}}
const help=document.getElementById('help');if(help)help.textContent='WASD / arrows · E interact · authored World DNA active';
if(typeof auraApplyAll==='function')auraApplyAll();
syncFromState();
let last=performance.now();function frame(now){last=now;gameplayTick(now);requestAnimationFrame(frame)}requestAnimationFrame(frame);
})();
"""


def inject_aura2d_world_bridge(html_text: str, *, game: GameDNA, world: GameWorldDNA) -> str:
    """Add closed World-DNA collision/render/interaction execution to an Aura2D playtest."""
    if _BRIDGE_MARKER in html_text:
        return html_text
    if _RUNTIME_STATE_MARKER not in html_text:
        raise ValueError("Aura2D World bridge requires the runtime-state kernel")
    if "const p=" not in html_text or "function drawPlayer()" not in html_text:
        raise ValueError("Aura2D World bridge requires the native Canvas player runtime")
    body_end = html_text.rfind(_BODY_CLOSE)
    if body_end < 0:
        raise ValueError("Game Forge playtest has no closing body element")
    payload = json.dumps(aura2d_world_bridge_payload(game, world), ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    script = f"<script id='aura2d-world-bridge-dna' type='application/json'>{payload}</script><script>{_bridge_script()}</script>"
    return html_text[:body_end] + script + html_text[body_end:]


__all__ = ["aura2d_world_bridge_payload", "inject_aura2d_world_bridge"]
