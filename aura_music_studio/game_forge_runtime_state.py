from __future__ import annotations

import json
from typing import Literal

from .game_forge_adventure import adventure_runtime_payload
from .game_forge_gameplay import gameplay_runtime_payload
from .game_forge_models import GameDNA
from .game_forge_state_machine import state_machine_runtime_payload
from .game_forge_world import GameWorldDNA

RuntimeKind = Literal["aura2d", "aura3d"]
_RUNTIME_MARKER = "id='aura-runtime-state-dna'"
_BODY_CLOSE = "</body>"


def runtime_state_payload(game: GameDNA, world: GameWorldDNA) -> dict:
    """Return only validated declarative DNA consumed by the browser runtime-state kernel."""
    return {
        "version": 1,
        "adventure": adventure_runtime_payload(game),
        "gameplay": gameplay_runtime_payload(world),
        "state_machines": state_machine_runtime_payload(game.id, world=world),
        "runtime_contract": {
            "browser_local_mutable_state": True,
            "save_export_import": True,
            "aura3d_world_behavior_bridge": True,
            "aura2d_state_kernel": True,
            "aura2d_world_behavior_bridge": False,
            "creator_javascript": False,
            "external_network_access": False,
        },
    }


def _runtime_script(*, runtime: RuntimeKind) -> str:
    runtime_json = json.dumps(runtime, ensure_ascii=True)
    template = r"""
'use strict';
const auraRuntimeKind=__RUNTIME__;
const auraRuntimeDNA=JSON.parse(document.getElementById('aura-runtime-state-dna').textContent||'{}');
const auraAdventure=auraRuntimeDNA.adventure||{},auraGameplay=auraRuntimeDNA.gameplay||{},auraMachines=auraRuntimeDNA.state_machines||{};
const auraItems=new Map((auraAdventure.items||[]).map(row=>[row.id,row]));
const auraObjectives=new Map((auraAdventure.objectives||[]).map(row=>[row.id,row]));
const auraDialogues=new Map((auraAdventure.dialogues||[]).map(row=>[row.id,row]));
const auraGates=new Map((auraAdventure.gates||[]).map(row=>[row.id,row]));
const auraMachineDNA=new Map((auraMachines.entities||[]).map(row=>[row.id,row]));
const auraGameplayDNA=new Map((auraGameplay.entities||[]).map(row=>[row.id,row]));
const auraKnownFlags=new Set(Object.keys(auraAdventure.initial_flags||{}));
for(const row of auraAdventure.objectives||[]){if(row.flag)auraKnownFlags.add(row.flag);if(row.completion_flag)auraKnownFlags.add(row.completion_flag)}
for(const row of auraAdventure.dialogues||[]){if(row.completion_flag)auraKnownFlags.add(row.completion_flag);for(const choice of row.choices||[])if(choice.set_flag)auraKnownFlags.add(choice.set_flag)}
for(const row of auraAdventure.gates||[]){if(row.requires_flag)auraKnownFlags.add(row.requires_flag);if(row.open_flag)auraKnownFlags.add(row.open_flag)}
const auraFinite=value=>typeof value==='number'&&Number.isFinite(value),auraClamp=(value,low,high)=>Math.max(low,Math.min(high,Number(value)||0));
const auraDeepCopy=value=>JSON.parse(JSON.stringify(value));
const auraState={
  v:1,score:0,lives:3,flags:{},inventory:{},objectives:{},dialogues:{},gates:{},machines:{},checkpoint:null,collected:new Set(),triggered:new Set()
};
for(const flag of auraKnownFlags)auraState.flags[flag]=Boolean(auraAdventure.initial_flags?.[flag]);
for(const item of auraItems.values())auraState.inventory[item.id]=0;
for(const objective of auraObjectives.values())auraState.objectives[objective.id]={progress:0,completed:false};
for(const dialogue of auraDialogues.values())auraState.dialogues[dialogue.id]=false;
for(const gate of auraGates.values())auraState.gates[gate.id]=false;
for(const row of auraMachineDNA.values())auraState.machines[row.id]={state:row.machine.initial_state,elapsed:0};
const auraInitialState=auraDeepCopy({flags:auraState.flags,inventory:auraState.inventory,objectives:auraState.objectives,dialogues:auraState.dialogues,gates:auraState.gates,machines:auraState.machines});
const auraLiveEntities=()=>typeof entities!=='undefined'&&Array.isArray(entities)?entities:[];
const auraLiveEntity=id=>auraLiveEntities().find(row=>row.id===id)||null;
function auraPlayerPosition(){
  if(auraRuntimeKind==='aura3d'&&typeof player!=='undefined'&&player?.position)return player.position;
  return null;
}
function auraEntityPosition(id){const live=auraLiveEntity(id);if(live?.position)return live.position;const machine=auraMachineDNA.get(id);return machine?.position||null}
function auraDistance(a,b){if(!a||!b)return Infinity;return Math.hypot(Number(a.x||0)-Number(b.x||0),Number(a.z||0)-Number(b.z||0))}
function auraMarkDirty(){try{if(typeof window.AuraMarkSaveDirty==='function')window.AuraMarkSaveDirty()}catch(_error){}}
const auraHud=document.getElementById('aura-runtime-state-hud'),auraDialogueUI=document.getElementById('aura-dialogue-ui'),auraDialogueText=document.getElementById('aura-dialogue-text'),auraDialogueChoices=document.getElementById('aura-dialogue-choices');
function auraStatus(message){if(auraHud)auraHud.textContent=String(message||'').slice(0,240)}
function auraInventoryAdd(itemId,amount){if(!auraItems.has(itemId))return false;const item=auraItems.get(itemId),next=Math.max(0,Math.min(Number(item.max_stack||9999),Math.trunc((auraState.inventory[itemId]||0)+Number(amount||0))));auraState.inventory[itemId]=next;auraMarkDirty();return true}
function auraSetFlag(flag,value=true){if(!auraKnownFlags.has(flag))return false;const next=Boolean(value),changed=auraState.flags[flag]!==next;auraState.flags[flag]=next;if(changed){auraUpdateFlagObjectives(flag);auraMarkDirty()}return true}
function auraCompleteObjective(objective){const state=auraState.objectives[objective.id];if(!state||state.completed)return false;state.progress=Math.max(state.progress,Number(objective.target_count||1));state.completed=true;if(objective.reward_item_id)auraInventoryAdd(objective.reward_item_id,Number(objective.reward_quantity||1));if(objective.completion_flag)auraSetFlag(objective.completion_flag,true);auraStatus(`Objective complete · ${objective.title}`);auraMarkDirty();return true}
function auraAdvanceObjective(kind,targetId,amount=1){for(const objective of auraObjectives.values()){if(objective.kind!==kind)continue;if((kind==='collect'||kind==='reach'||kind==='talk')&&objective.target_entity_id!==targetId)continue;const state=auraState.objectives[objective.id];if(!state||state.completed)continue;state.progress=Math.max(0,state.progress+Math.max(1,Number(amount||1)));if(state.progress>=Number(objective.target_count||1))auraCompleteObjective(objective);else auraMarkDirty()}}
function auraUpdateFlagObjectives(flag){for(const objective of auraObjectives.values()){if(objective.kind==='flag'&&objective.flag===flag&&auraState.flags[flag])auraCompleteObjective(objective)}}
function auraGateTry(triggerId){for(const gate of auraGates.values()){if(gate.trigger_entity_id!==triggerId||auraState.gates[gate.id])continue;if(gate.requires_flag&&!auraState.flags[gate.requires_flag]){auraStatus(`${gate.label} · requirement not met`);continue}if(gate.requires_item_id&&(auraState.inventory[gate.requires_item_id]||0)<1){auraStatus(`${gate.label} · item required`);continue}if(gate.requires_item_id&&gate.consume_item)auraInventoryAdd(gate.requires_item_id,-1);auraState.gates[gate.id]=true;if(gate.open_flag)auraSetFlag(gate.open_flag,true);const door=gate.door_entity_id?auraLiveEntity(gate.door_entity_id):null;if(door)door._auraGateHidden=true;auraStatus(`${gate.label} opened`);auraMarkDirty()}}
function auraCloseDialogue(){if(auraDialogueUI)auraDialogueUI.hidden=true;if(auraDialogueChoices)auraDialogueChoices.replaceChildren()}
function auraCompleteDialogue(dialogue){if(dialogue.once)auraState.dialogues[dialogue.id]=true;if(dialogue.completion_flag)auraSetFlag(dialogue.completion_flag,true);auraAdvanceObjective('talk',dialogue.trigger_entity_id,1);auraMarkDirty();auraCloseDialogue()}
function auraChooseDialogue(dialogue,choice){if(choice.set_flag)auraSetFlag(choice.set_flag,true);if(choice.grant_item_id)auraInventoryAdd(choice.grant_item_id,Number(choice.grant_quantity||1));if(choice.complete_dialogue!==false)auraCompleteDialogue(dialogue)}
function auraShowDialogue(dialogue){if(!dialogue||!auraDialogueUI||!auraDialogueText||!auraDialogueChoices)return false;if(dialogue.once&&auraState.dialogues[dialogue.id])return false;auraDialogueText.textContent=`${dialogue.speaker}: ${(dialogue.lines||[]).join(' ')}`;auraDialogueChoices.replaceChildren();const choices=Array.isArray(dialogue.choices)?dialogue.choices:[];if(choices.length){for(const choice of choices){const button=document.createElement('button');button.type='button';button.textContent=choice.label;button.addEventListener('click',()=>auraChooseDialogue(dialogue,choice));auraDialogueChoices.append(button)}}else{const button=document.createElement('button');button.type='button';button.textContent='Continue';button.addEventListener('click',()=>auraCompleteDialogue(dialogue));auraDialogueChoices.append(button)}auraDialogueUI.hidden=false;return true}
function auraTalk(triggerId){const dialogue=[...auraDialogues.values()].find(row=>row.trigger_entity_id===triggerId&&(!row.once||!auraState.dialogues[row.id]));return auraShowDialogue(dialogue)}
function auraDispatch(kind,targetId,value=true){if(kind==='collect'){auraAdvanceObjective('collect',targetId,1);return true}if(kind==='reach'){auraAdvanceObjective('reach',targetId,1);auraGateTry(targetId);return true}if(kind==='talk')return auraTalk(targetId);if(kind==='flag')return auraSetFlag(targetId,value);return false}
function auraMachineApply(entityId){const row=auraMachineDNA.get(entityId),state=auraState.machines[entityId];if(!row||!state)return;const definition=(row.machine.states||[]).find(item=>item.state===state.state);if(!definition)return;const live=auraLiveEntity(entityId);if(live){const base=row.position||{x:0,y:0,z:0},offset=definition.offset||{x:0,y:0,z:0};live.position={x:Number(base.x||0)+Number(offset.x||0),y:Number(base.y||0)+Number(offset.y||0),z:Number(base.z||0)+Number(offset.z||0)};live._auraStateHidden=definition.visible===false}if(definition.message)auraStatus(definition.message)}
function auraMachineTransition(row,state,transition){state.state=transition.to_state;state.elapsed=0;auraMachineApply(row.id);auraMarkDirty()}
function auraMachineTick(dt){const pp=auraPlayerPosition();for(const row of auraMachineDNA.values()){const state=auraState.machines[row.id];if(!state)continue;state.elapsed=Math.min(3600,state.elapsed+dt);const transitions=(row.machine.transitions||[]).filter(item=>item.from_state===state.state);for(const transition of transitions){if(state.elapsed<Number(transition.min_state_seconds||0))continue;let pass=false;if(transition.trigger==='timer')pass=state.elapsed>=Number(transition.seconds||Infinity);else if(transition.trigger==='adventure_flag')pass=Boolean(auraState.flags[transition.flag])===Boolean(transition.flag_value);else if(transition.trigger==='player_near')pass=auraDistance(pp,auraEntityPosition(row.id))<=Number(transition.radius||0);if(pass){auraMachineTransition(row,state,transition);break}}}}
const auraBehaviorBase=new Map();for(const row of auraGameplayDNA.values())auraBehaviorBase.set(row.id,auraDeepCopy(row.position||{x:0,y:0,z:0}));
const auraDamageCooldown=new Map(),auraRespawnAt=new Map();
function auraBehaviorRadius(row){const scale=row.scale||{x:1,y:1,z:1};return Math.max(.7,Math.max(Math.abs(Number(scale.x||1)),Math.abs(Number(scale.z||1)))*.7+.55)}
function auraRespawnPlayer(){if(auraRuntimeKind!=='aura3d'||typeof player==='undefined')return;const cp=auraState.checkpoint||{x:0,y:1,z:0};player.position.x=Number(cp.x||0);player.position.y=Number(cp.y||1);player.position.z=Number(cp.z||0)}
function auraGameplayTick(now){if(auraRuntimeKind!=='aura3d')return;const pp=auraPlayerPosition();if(!pp)return;for(const row of auraGameplayDNA.values()){const live=auraLiveEntity(row.id);if(!live)continue;for(const behavior of row.behaviors||[]){const params=behavior.params||{};if(behavior.op==='patrol'){const base=auraBehaviorBase.get(row.id)||{x:0,y:0,z:0},axis=['x','y','z'].includes(params.axis)?params.axis:'x',distance=Math.max(0,Number(params.distance||0)),speed=Math.max(0,Number(params.speed||0)),phase=Math.sin(now/1000*speed);live.position[axis]=Number(base[axis]||0)+phase*distance;continue}const near=auraDistance(pp,live.position)<=auraBehaviorRadius(row);if(!near)continue;if(behavior.op==='collectible'){if(params.respawn===true){const until=auraRespawnAt.get(row.id)||0;if(until>now)continue;auraRespawnAt.set(row.id,now+Math.max(.25,Number(params.respawn_seconds||3))*1000);live._auraCollectedHidden=true;setTimeout(()=>{live._auraCollectedHidden=false},Math.max(.25,Number(params.respawn_seconds||3))*1000)}else{if(auraState.collected.has(row.id))continue;auraState.collected.add(row.id);live._auraCollectedHidden=true}auraState.score=Math.min(1000000000,auraState.score+Math.max(1,Number(params.points||1)));auraDispatch('collect',row.id);auraStatus(`Collected ${row.name} · Score ${auraState.score}`);auraMarkDirty()}else if(behavior.op==='checkpoint'){const previous=auraState.checkpoint;if(!previous||auraDistance(previous,pp)>.25){auraState.checkpoint={x:Number(pp.x||0),y:Number(pp.y||0),z:Number(pp.z||0)};auraStatus(params.label||row.name||'Checkpoint');auraMarkDirty()}}else if(behavior.op==='quest_trigger'){if(params.once===true&&auraState.triggered.has(row.id))continue;if(params.once===true)auraState.triggered.add(row.id);auraDispatch('reach',row.id);if(params.event&&auraKnownFlags.has(params.event))auraSetFlag(params.event,true);auraMarkDirty()}else if(behavior.op==='damage'){const until=auraDamageCooldown.get(row.id)||0;if(until>now)continue;auraDamageCooldown.set(row.id,now+Math.max(.05,Number(params.cooldown_seconds||.75))*1000);auraState.lives=Math.max(0,auraState.lives-Math.max(1,Number(params.amount||1)));if(params.reset_to_checkpoint!==false)auraRespawnPlayer();if(auraState.lives<=0){auraState.lives=3;auraRespawnPlayer()}auraStatus(`Lives ${auraState.lives}`);auraMarkDirty()}}}}
function auraNearestInteractable(){if(auraRuntimeKind!=='aura3d')return null;const pp=auraPlayerPosition();let best=null,bestDistance=2.75;const ids=new Set();for(const row of auraDialogues.values())ids.add(row.trigger_entity_id);for(const row of auraGates.values())ids.add(row.trigger_entity_id);for(const row of auraObjectives.values())if(row.kind==='reach'&&row.target_entity_id)ids.add(row.target_entity_id);for(const id of ids){const distance=auraDistance(pp,auraEntityPosition(id));if(distance<=bestDistance){best={id,distance};bestDistance=distance}}return best}
addEventListener('keydown',event=>{if(event.key.toLowerCase()!=='e'||event.repeat)return;const target=auraNearestInteractable();if(!target)return;if(auraTalk(target.id))return;auraDispatch('reach',target.id)});
function auraTimerObjectives(dt){for(const objective of auraObjectives.values()){if(objective.kind!=='timer')continue;const state=auraState.objectives[objective.id];if(!state||state.completed)continue;state.progress=Math.min(Number(objective.seconds||86400),state.progress+dt);if(state.progress>=Number(objective.seconds||Infinity))auraCompleteObjective(objective)}}
function auraExportState(){const machines={};for(const [id,state] of Object.entries(auraState.machines))machines[id]={state:state.state,elapsed:auraClamp(state.elapsed,0,3600)};return {v:1,score:auraClamp(auraState.score,0,1000000000),lives:Math.trunc(auraClamp(auraState.lives,0,99)),flags:auraDeepCopy(auraState.flags),inventory:auraDeepCopy(auraState.inventory),objectives:auraDeepCopy(auraState.objectives),dialogues:auraDeepCopy(auraState.dialogues),gates:auraDeepCopy(auraState.gates),machines,checkpoint:auraState.checkpoint?{x:Number(auraState.checkpoint.x||0),y:Number(auraState.checkpoint.y||0),z:Number(auraState.checkpoint.z||0)}:null,collected:[...auraState.collected],triggered:[...auraState.triggered]}}
function auraValidateState(raw){if(!raw||raw.v!==1||!auraFinite(raw.score)||!auraFinite(raw.lives)||typeof raw.flags!=='object'||typeof raw.inventory!=='object'||typeof raw.objectives!=='object'||typeof raw.dialogues!=='object'||typeof raw.gates!=='object'||typeof raw.machines!=='object'||!Array.isArray(raw.collected)||!Array.isArray(raw.triggered))return false;for(const [id,row] of auraMachineDNA){const saved=raw.machines[id];if(saved&&(!(row.machine.states||[]).some(state=>state.state===saved.state)||!auraFinite(saved.elapsed)))return false}if(raw.checkpoint&&( !auraFinite(raw.checkpoint.x)||!auraFinite(raw.checkpoint.y)||!auraFinite(raw.checkpoint.z)))return false;return true}
function auraImportState(raw){if(!auraValidateState(raw))return false;auraState.score=auraClamp(raw.score,0,1000000000);auraState.lives=Math.trunc(auraClamp(raw.lives,0,99));for(const flag of auraKnownFlags)auraState.flags[flag]=Boolean(raw.flags?.[flag]);for(const item of auraItems.values())auraState.inventory[item.id]=Math.trunc(auraClamp(raw.inventory?.[item.id]||0,0,Number(item.max_stack||9999)));for(const objective of auraObjectives.values()){const saved=raw.objectives?.[objective.id]||{};auraState.objectives[objective.id]={progress:auraClamp(saved.progress||0,0,Math.max(Number(objective.target_count||1),Number(objective.seconds||0))),completed:Boolean(saved.completed)}}for(const dialogue of auraDialogues.values())auraState.dialogues[dialogue.id]=Boolean(raw.dialogues?.[dialogue.id]);for(const gate of auraGates.values())auraState.gates[gate.id]=Boolean(raw.gates?.[gate.id]);for(const [id,row] of auraMachineDNA){const saved=raw.machines?.[id];auraState.machines[id]=saved?{state:saved.state,elapsed:auraClamp(saved.elapsed,0,3600)}:{state:row.machine.initial_state,elapsed:0}}auraState.checkpoint=raw.checkpoint?{x:Number(raw.checkpoint.x),y:Number(raw.checkpoint.y),z:Number(raw.checkpoint.z)}:null;auraState.collected=new Set(raw.collected.filter(id=>auraGameplayDNA.has(id)).slice(0,10000));auraState.triggered=new Set(raw.triggered.filter(id=>auraGameplayDNA.has(id)).slice(0,10000));auraApplyAll();return true}
function auraApplyAll(){for(const row of auraMachineDNA.values())auraMachineApply(row.id);for(const gate of auraGates.values()){const door=gate.door_entity_id?auraLiveEntity(gate.door_entity_id):null;if(door)door._auraGateHidden=Boolean(auraState.gates[gate.id])}for(const row of auraGameplayDNA.values()){const live=auraLiveEntity(row.id);if(live)live._auraCollectedHidden=auraState.collected.has(row.id)}}
if(auraRuntimeKind==='aura3d'&&typeof cellVisible==='function'){const auraBaseCellVisible=cellVisible;cellVisible=e=>!e._auraGateHidden&&!e._auraStateHidden&&!e._auraCollectedHidden&&auraBaseCellVisible(e)}
if(auraRuntimeKind==='aura3d'){const pp=auraPlayerPosition();if(pp)auraState.checkpoint={x:Number(pp.x||0),y:Number(pp.y||0),z:Number(pp.z||0)}}
for(const objective of auraObjectives.values())if(objective.kind==='flag'&&objective.flag&&auraState.flags[objective.flag])auraCompleteObjective(objective);
auraApplyAll();
window.AuraRuntimeState={version:1,dispatch:auraDispatch,setFlag:auraSetFlag,addItem:auraInventoryAdd,talk:auraTalk,exportState:auraExportState,validateState:auraValidateState,importState:auraImportState,snapshot:()=>auraDeepCopy(auraExportState()),contract:auraDeepCopy(auraRuntimeDNA.runtime_contract||{})};
let auraRuntimeLast=performance.now();function auraRuntimeFrame(now){const dt=Math.min(.1,Math.max(0,(now-auraRuntimeLast)/1000));auraRuntimeLast=now;auraTimerObjectives(dt);auraMachineTick(dt);auraGameplayTick(now);requestAnimationFrame(auraRuntimeFrame)}requestAnimationFrame(auraRuntimeFrame);
"""
    return template.replace("__RUNTIME__", runtime_json)


def inject_runtime_state(
    html_text: str,
    *,
    game: GameDNA,
    world: GameWorldDNA,
    runtime: RuntimeKind,
) -> str:
    """Inject the closed Adventure/Gameplay/State-Machine runtime without adding network access."""
    if runtime not in {"aura2d", "aura3d"}:
        raise ValueError(f"Unsupported Game Forge runtime: {runtime}")
    if _RUNTIME_MARKER in html_text:
        return html_text
    body_end = html_text.rfind(_BODY_CLOSE)
    if body_end < 0:
        raise ValueError("Game Forge playtest has no closing body element")
    payload = json.dumps(runtime_state_payload(game, world), ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    ui = (
        "<div id='aura-runtime-state-hud' role='status' aria-live='polite' style='position:fixed;right:12px;top:72px;z-index:7;max-width:min(420px,82vw);padding:7px 10px;border-radius:9px;background:#050713d9;border:1px solid #ffffff26;color:#dce4f4;font:600 12px/1.35 system-ui,sans-serif'>Game state ready</div>"
        "<div id='aura-dialogue-ui' hidden style='position:fixed;left:50%;bottom:12%;transform:translateX(-50%);z-index:20;width:min(680px,88vw);padding:16px;border-radius:14px;background:#050713f2;border:1px solid #ffffff33;color:white;font-family:system-ui,sans-serif'>"
        "<div id='aura-dialogue-text' style='line-height:1.5;margin-bottom:12px'></div><div id='aura-dialogue-choices' style='display:flex;gap:8px;flex-wrap:wrap'></div></div>"
    )
    script = (
        f"<script id='aura-runtime-state-dna' type='application/json'>{payload}</script>"
        "<script>" + _runtime_script(runtime=runtime) + "</script>"
    )
    return html_text[:body_end] + ui + script + html_text[body_end:]


__all__ = ["RuntimeKind", "runtime_state_payload", "inject_runtime_state"]
